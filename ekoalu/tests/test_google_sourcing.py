"""Tests du sourcing Google Custom Search (API mockee)."""
from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import call_command

from ekoalu.google_sourcing import client, queries


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
    with patch.object(client, "search_raw", side_effect=[page1, []]):
        urls = client.search_linkedin_profiles("q", max_results=10)
    pids = [u for u in urls]
    assert len(urls) == 2
    assert any("jean-dupont" in u for u in urls)
    assert any("marie-martin" in u for u in urls)
    assert not any("/company/" in u for u in urls)


def test_search_linkedin_profiles_respecte_max():
    page = [{"link": f"https://www.linkedin.com/in/p{i}/"} for i in range(10)]
    with patch.object(client, "search_raw", side_effect=[page, []]):
        urls = client.search_linkedin_profiles("q", max_results=3)
    assert len(urls) == 3


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

    monkeypatch.setenv("GOOGLE_CSE_API_KEY", "k")
    monkeypatch.setenv("GOOGLE_CSE_CX", "cx")

    urls = ["https://www.linkedin.com/in/alice-x/", "https://www.linkedin.com/in/bob-y/"]
    with patch("ekoalu.google_sourcing.client.search_linkedin_profiles", return_value=urls):
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

    urls = ["https://www.linkedin.com/in/zoe/"]
    with patch("ekoalu.google_sourcing.client.search_linkedin_profiles", return_value=urls):
        out = StringIO()
        call_command("source_via_google", "--campaign", "Test Boite", "--dry-run", stdout=out)
    assert not Lead.objects.filter(public_identifier="zoe").exists()
    assert "[dry]" in out.getvalue()


@pytest.mark.django_db
def test_command_idempotent(abm_campaign, monkeypatch):
    from crm.models import Lead
    from ekoalu.lead_routing.models import LeadDiscovery

    monkeypatch.setenv("GOOGLE_CSE_API_KEY", "k")
    monkeypatch.setenv("GOOGLE_CSE_CX", "cx")
    urls = ["https://www.linkedin.com/in/alice-x/"]
    with patch("ekoalu.google_sourcing.client.search_linkedin_profiles", return_value=urls):
        call_command("source_via_google", "--campaign", "Test Boite", stdout=StringIO())
        call_command("source_via_google", "--campaign", "Test Boite", stdout=StringIO())

    assert Lead.objects.filter(public_identifier="alice-x").count() == 1
    assert LeadDiscovery.objects.filter(campaign=abm_campaign, lead__public_identifier="alice-x").count() == 1


@pytest.mark.django_db
def test_command_exige_config_hors_dry_run(abm_campaign, monkeypatch):
    from django.core.management.base import CommandError

    monkeypatch.delenv("GOOGLE_CSE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CSE_CX", raising=False)
    with pytest.raises(CommandError):
        call_command("source_via_google", "--campaign", "Test Boite", stdout=StringIO())
