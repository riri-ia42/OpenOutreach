"""Tests du sourcing via recherche Google (backend Serper.dev, API mockee)."""
from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import call_command

from ekoalu.google_sourcing import client, prefilter, queries


# --------------------------------------------------------------------------
# queries
# --------------------------------------------------------------------------

def test_target_company_name_parse_du_nom():
    c = SimpleNamespace(name="EKOALU - ABM - Léon Grosse")
    # pas d'abm_link -> acces leve, capte par le try/except
    assert queries.target_company_name(c) == "Léon Grosse"


def test_target_company_name_via_abm_link():
    link = SimpleNamespace(target_company_id=3, target_company=SimpleNamespace(name="Bouygues"))
    c = SimpleNamespace(name="EKOALU - ABM - Autre", abm_link=link)
    assert queries.target_company_name(c) == "Bouygues"


def test_build_abm_queries_un_par_poste_et_cible():
    c = SimpleNamespace(name="EKOALU - ABM - Léon Grosse")
    qs = queries.build_abm_queries(c)
    assert len(qs) == len(queries.ABM_ROLE_TERMS)
    assert all(q.startswith("site:linkedin.com/in") for q in qs)
    assert all('"Léon Grosse"' in q for q in qs)
    assert any('"conducteur de travaux"' in q for q in qs)


def test_build_abm_queries_sans_cible_vide():
    c = SimpleNamespace(name="EKOALU - Persona sans ABM")
    assert queries.build_abm_queries(c) == []


# --- Campagnes SECTEUR (secteur d'activite + poste, pas 1 entreprise) ---

def test_sector_slug_parse_du_nom():
    c = SimpleNamespace(name="EKOALU - SECTEUR - Bailleurs sociaux RA")
    assert queries.sector_slug(c) == "bailleurs sociaux ra"
    assert queries.sector_slug(SimpleNamespace(name="EKOALU - ABM - Bateg")) is None


def test_build_sector_queries_ancre_x_poste_x_geo():
    c = SimpleNamespace(name="EKOALU - SECTEUR - Bailleurs sociaux RA")
    qs = queries.build_sector_queries(c)
    spec = queries.SECTOR_SPECS["bailleurs sociaux ra"]
    assert len(qs) == len(spec["roles"]) * len(spec["regions"])
    assert all(q.startswith("site:linkedin.com/in") for q in qs)
    assert all('"logement social"' in q for q in qs)       # ancre metier (pas "bailleur social")
    assert all("Lyon" in q for q in qs)                    # biais Rhone-Alpes
    assert any('"responsable travaux"' in q for q in qs)
    # budget : rester <= --per-campaign-queries (9) pour tout servir en 1 passage
    assert len(qs) <= 9


def test_build_sector_queries_slug_inconnu_vide():
    c = SimpleNamespace(name="EKOALU - SECTEUR - Secteur pas encore specifie")
    assert queries.build_sector_queries(c) == []


def test_build_queries_dispatch_abm_ou_secteur():
    abm = SimpleNamespace(name="EKOALU - ABM - Léon Grosse")
    sect = SimpleNamespace(name="EKOALU - SECTEUR - Bailleurs sociaux RA")
    assert queries.build_queries(abm) == queries.build_abm_queries(abm)
    assert queries.build_queries(sect) == queries.build_sector_queries(sect)


def test_sector_campaign_serper_only():
    from ekoalu.google_sourcing.routing import native_search_allowed, is_sector_campaign
    sect = SimpleNamespace(name="EKOALU - SECTEUR - Bailleurs sociaux RA")
    glob = SimpleNamespace(name="EKOALU - MACON")
    assert is_sector_campaign(sect) is True
    assert native_search_allowed(sect) is False   # Serper uniquement
    assert native_search_allowed(glob) is True     # persona global garde le natif


# --------------------------------------------------------------------------
# client.search_raw (mock requests.post — contrat Serper)
# --------------------------------------------------------------------------

def test_search_raw_appelle_serper_et_renvoie_organic(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "sk-test")
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"organic": [{"link": "https://www.linkedin.com/in/x/", "title": "X"}]}

    def fake_post(url, json=None, timeout=None, headers=None):
        captured.update(url=url, json=json, headers=headers)
        return FakeResp()

    with patch.object(client.requests, "post", side_effect=fake_post):
        items = client.search_raw("ma requete", num=10, page=2)

    assert items == [{"link": "https://www.linkedin.com/in/x/", "title": "X"}]
    assert captured["url"] == client.ENDPOINT
    assert captured["headers"]["X-API-KEY"] == "sk-test"
    assert captured["json"]["q"] == "ma requete"
    assert captured["json"]["page"] == 2
    assert captured["json"]["num"] == 10  # num<=10 : 1 credit


def test_search_raw_sans_cle_leve(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        client.search_raw("q")


# --------------------------------------------------------------------------
# client.search_linkedin_profiles (mock search_raw)
# --------------------------------------------------------------------------

def test_search_linkedin_profiles_extrait_dedup_et_filtre():
    page1 = [
        {"link": "https://www.linkedin.com/in/jean-dupont/"},
        {"link": "https://fr.linkedin.com/in/marie-martin/"},
        {"link": "https://www.linkedin.com/company/leon-grosse/"},  # exclu (pas /in/)
        {"link": "https://www.linkedin.com/in/jean-dupont/"},        # doublon
    ]
    with patch.object(client, "search_raw", return_value=page1):
        urls = client.search_linkedin_profiles("q", max_results=10)
    assert len(urls) == 2
    assert any("jean-dupont" in u for u in urls)
    assert any("marie-martin" in u for u in urls)
    assert not any("/company/" in u for u in urls)


def test_search_linkedin_profiles_respecte_max():
    page = [{"link": f"https://www.linkedin.com/in/p{i}/"} for i in range(10)]
    with patch.object(client, "search_raw", return_value=page):
        urls = client.search_linkedin_profiles("q", max_results=3)
    assert len(urls) == 3


def test_search_linkedin_results_une_page_un_credit():
    """search_linkedin_results ne pagine PAS lui-meme : 1 appel = 1 page = 1 credit
    (la pagination est pilotee par service.py qui connait les profils connus)."""
    page = [{"link": f"https://www.linkedin.com/in/p{i}/"} for i in range(10)]
    with patch.object(client, "search_raw", return_value=page) as mock_raw:
        results = client.search_linkedin_results("q", num=10, page=2)
    assert mock_raw.call_count == 1
    assert mock_raw.call_args.kwargs.get("page") == 2
    assert len(results) == 10


# --------------------------------------------------------------------------
# prefilter — filtre négatif hors-domaine AVANT lecture (preuve : rejets 15/06)
# --------------------------------------------------------------------------

def test_prefilter_garde_les_profils_batiment():
    # Cas réels du sourcing ABM
    assert prefilter.passes_prefilter(
        {"title": "Jean Dupont - Conducteur de travaux - Bateg", "snippet": ""})
    assert prefilter.passes_prefilter(
        {"title": "Marie - Chargée d'affaires métallerie", "snippet": "façades, serrurerie"})


def test_prefilter_ecarte_le_hors_domaine():
    # Tous tirés des 51 rejets wrong_fit du 15/06
    assert not prefilter.passes_prefilter(
        {"title": "Luigi Sibille - Postdoctoral Researcher", "snippet": "Princeton University, civil engineering"})
    assert not prefilter.passes_prefilter(
        {"title": "Nathalie - Consultante formatrice en anglais", "snippet": "coach linguistique"})
    assert not prefilter.passes_prefilter(
        {"title": "Nicolas - Psychothérapeute / producteur artistique", "snippet": ""})
    assert not prefilter.passes_prefilter(
        {"title": "Achille - Avocat en droit de la construction", "snippet": "Cinetic Avocats"})
    assert not prefilter.passes_prefilter(
        {"title": "Abrar - Data Engineer / Data Analyst", "snippet": "BI, ETL, Power BI"})


def test_prefilter_insensible_accents_casse():
    assert prefilter.is_offdomain("PHOTOGRAPHE indépendant", "")
    assert prefilter.is_offdomain("Géomètre-expert", "")


def test_prefilter_kill_switch(monkeypatch):
    monkeypatch.setenv("EKOALU_SERPER_PREFILTER", "0")
    # désactivé : même un avocat passe
    assert prefilter.passes_prefilter({"title": "Avocat", "snippet": ""})


@pytest.mark.django_db
def test_source_campaign_prefiltre_avant_lecture(abm_campaign, monkeypatch):
    """Le hors-domaine ne crée PAS de lead (donc pas de lecture à payer)."""
    from crm.models import Lead
    from ekoalu.google_sourcing.service import source_campaign

    monkeypatch.setenv("SERPER_API_KEY", "k")
    monkeypatch.setenv("EKOALU_SERPER_PREFILTER", "1")
    results = [
        {"link": "https://www.linkedin.com/in/bon-profil/", "title": "Conducteur de travaux", "snippet": ""},
        {"link": "https://www.linkedin.com/in/un-avocat/", "title": "Avocat", "snippet": "barreau de Lyon"},
    ]
    with patch.object(client, "search_linkedin_results", return_value=results):
        res = source_campaign(abm_campaign, max_profiles=10, query_budget=1)

    assert res.prefiltered == 1
    assert res.new_leads == 1
    assert Lead.objects.filter(public_identifier="bon-profil").exists()
    assert not Lead.objects.filter(public_identifier="un-avocat").exists()


# --------------------------------------------------------------------------
# Commande source_via_google
# --------------------------------------------------------------------------

@pytest.fixture
def abm_campaign(db):
    from linkedin.models import Campaign

    return Campaign.objects.create(name="EKOALU - ABM - Test Boite")


@pytest.mark.django_db
def test_command_cree_leads_url_only_et_discovery(abm_campaign, monkeypatch):
    from crm.models import Lead
    from ekoalu.lead_routing.models import LeadDiscovery

    monkeypatch.setenv("SERPER_API_KEY", "k")

    results = [
        {"link": "https://www.linkedin.com/in/alice-x/", "title": "Alice X - Directrice", "snippet": ""},
        {"link": "https://www.linkedin.com/in/bob-y/", "title": "Bob Y - Conducteur de travaux", "snippet": ""},
    ]
    with patch("ekoalu.google_sourcing.client.search_linkedin_results", return_value=results):
        call_command("source_via_google", "--campaign", "Test Boite", "--max", "10", stdout=StringIO())

    assert Lead.objects.filter(public_identifier="alice-x").exists()
    assert Lead.objects.filter(public_identifier="bob-y").exists()
    # rattaches a la campagne (routage)
    assert LeadDiscovery.objects.filter(campaign=abm_campaign, lead__public_identifier="alice-x").exists()
    assert LeadDiscovery.objects.filter(campaign=abm_campaign).count() == 2
    # URL-only : pas d'embedding (le daemon enrichira)
    assert Lead.objects.get(public_identifier="alice-x").embedding is None


@pytest.mark.django_db
def test_command_dry_run_ne_cree_rien(abm_campaign):
    from crm.models import Lead

    results = [{"link": "https://www.linkedin.com/in/zoe/", "title": "Zoe - Métreur", "snippet": ""}]
    with patch("ekoalu.google_sourcing.client.search_linkedin_results", return_value=results):
        out = StringIO()
        call_command("source_via_google", "--campaign", "Test Boite", "--dry-run", stdout=out)
    assert not Lead.objects.filter(public_identifier="zoe").exists()
    assert "[dry]" in out.getvalue()


@pytest.mark.django_db
def test_command_idempotent(abm_campaign, monkeypatch):
    from crm.models import Lead
    from ekoalu.lead_routing.models import LeadDiscovery

    monkeypatch.setenv("SERPER_API_KEY", "k")
    results = [{"link": "https://www.linkedin.com/in/alice-x/", "title": "Alice X - Directrice", "snippet": ""}]
    with patch("ekoalu.google_sourcing.client.search_linkedin_results", return_value=results):
        call_command("source_via_google", "--campaign", "Test Boite", stdout=StringIO())
        call_command("source_via_google", "--campaign", "Test Boite", stdout=StringIO())

    assert Lead.objects.filter(public_identifier="alice-x").count() == 1
    assert LeadDiscovery.objects.filter(campaign=abm_campaign, lead__public_identifier="alice-x").count() == 1


@pytest.mark.django_db
def test_command_exige_config_hors_dry_run(abm_campaign, monkeypatch):
    from django.core.management.base import CommandError

    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    with pytest.raises(CommandError):
        call_command("source_via_google", "--campaign", "Test Boite", stdout=StringIO())
