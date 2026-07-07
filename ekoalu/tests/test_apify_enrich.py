"""Tests du squelette d'enrichissement Apify cookieless (LOT F).

Client mocke (aucun appel reseau), mapper defensif, commande --dry-run,
token absent -> erreur claire, et surtout : JAMAIS de cookie dans le payload.
"""
from __future__ import annotations

import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from ekoalu.apify_enrich import client
from ekoalu.apify_enrich.mapper import map_actor_item, snapshot_completeness

URL = "https://www.linkedin.com/in/jean-test/"

ACTOR_ITEM = {
    "linkedinUrl": URL,
    "publicIdentifier": "jean-test",
    "fullName": "Jean Test",
    "firstName": "Jean",
    "lastName": "Test",
    "headline": "Conducteur de travaux TCE",
    "about": "20 ans de chantiers tertiaires.",
    "addressWithCountry": "Lyon, Auvergne-Rhone-Alpes, France",
    "experiences": [
        {"title": "Conducteur de travaux", "companyName": "Leon Grosse",
         "location": "Lyon", "description": "Bureaux et ERP."},
        {"title": "Chef de chantier", "subtitle": "Eiffage"},
    ],
    "educations": [{"schoolName": "INSA Lyon", "degreeName": "Ingenieur"}],
}


@pytest.fixture(autouse=True)
def _apify_env(monkeypatch):
    monkeypatch.delenv("EKOALU_APIFY_TOKEN", raising=False)
    monkeypatch.delenv("EKOALU_APIFY_ACTOR", raising=False)


def _resp(payload, ok=True, status=200):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status
    resp.text = json.dumps(payload)
    resp.json.return_value = payload
    return resp


# --------------------------------------------------------------------------
# client
# --------------------------------------------------------------------------

class TestClient:
    def test_token_absent_erreur_claire(self):
        with pytest.raises(RuntimeError, match="EKOALU_APIFY_TOKEN manquant"):
            client.run_profile_scraper([URL])

    def test_run_ok_renvoie_les_items(self, monkeypatch):
        monkeypatch.setenv("EKOALU_APIFY_TOKEN", "tok-123")
        with patch("ekoalu.apify_enrich.client.requests.post",
                   return_value=_resp([ACTOR_ITEM])) as post:
            items = client.run_profile_scraper([URL])
        assert items == [ACTOR_ITEM]
        _, kwargs = post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer tok-123"
        assert client.DEFAULT_ACTOR in post.call_args[0][0]

    def test_payload_sans_cookie_ni_session(self, monkeypatch):
        """REGLE ABSOLUE : jamais li_at/cookie/session dans ce qu'on envoie."""
        monkeypatch.setenv("EKOALU_APIFY_TOKEN", "tok-123")
        with patch("ekoalu.apify_enrich.client.requests.post",
                   return_value=_resp([])) as post:
            client.run_profile_scraper([URL])
        sent = json.dumps(post.call_args.kwargs["json"]).lower()
        for banned in ("cookie", "li_at", "session"):
            assert banned not in sent
        assert post.call_args.kwargs["json"] == {"profileUrls": [URL]}

    def test_http_erreur_message_actionnable(self, monkeypatch):
        monkeypatch.setenv("EKOALU_APIFY_TOKEN", "tok-123")
        with patch("ekoalu.apify_enrich.client.requests.post",
                   return_value=_resp({"error": "invalid-token"}, ok=False, status=401)):
            with pytest.raises(RuntimeError, match="Apify HTTP 401"):
                client.run_profile_scraper([URL])

    def test_actor_id_env_et_normalisation(self, monkeypatch):
        assert client.actor_id() == client.DEFAULT_ACTOR
        monkeypatch.setenv("EKOALU_APIFY_ACTOR", "autre/acteur-scraper")
        assert client.actor_id() == "autre~acteur-scraper"

    def test_build_input_refuse_url_non_profil(self):
        with pytest.raises(ValueError, match="URL non-profil"):
            client.build_input(["https://www.google.com/"])
        with pytest.raises(ValueError, match="Aucune URL"):
            client.build_input([])


# --------------------------------------------------------------------------
# mapper
# --------------------------------------------------------------------------

class TestMapper:
    def test_item_complet_mappe_vers_snapshot(self):
        snap = map_actor_item(ACTOR_ITEM)
        assert snap["full_name"] == "Jean Test"
        assert snap["headline"] == "Conducteur de travaux TCE"
        assert snap["summary"] == "20 ans de chantiers tertiaires."
        assert snap["public_identifier"] == "jean-test"
        assert snap["location_name"].startswith("Lyon")
        assert snap["positions"][0]["title"] == "Conducteur de travaux"
        assert snap["positions"][0]["company_name"] == "Leon Grosse"
        assert snap["positions"][1]["company_name"] == "Eiffage"  # cle subtitle
        assert snap["educations"][0]["school_name"] == "INSA Lyon"
        assert snap["source"] == "apify"

    def test_item_vide_champs_a_none(self):
        snap = map_actor_item({})
        for field in ("url", "urn", "full_name", "headline", "summary",
                      "location_name", "public_identifier", "connection_degree"):
            assert snap[field] is None
        assert snap["positions"] == []
        assert snap["educations"] == []
        assert snap["source"] == "apify"

    def test_public_identifier_derive_de_l_url(self):
        snap = map_actor_item({"url": URL})
        assert snap["public_identifier"] == "jean-test"

    def test_full_name_reconstruit_depuis_prenom_nom(self):
        snap = map_actor_item({"firstName": "Ana", "lastName": "Bo"})
        assert snap["full_name"] == "Ana Bo"

    def test_completude(self):
        filled, total, missing = snapshot_completeness(map_actor_item(ACTOR_ITEM))
        assert (filled, total, missing) == (6, 6, [])
        filled, total, missing = snapshot_completeness(map_actor_item({}))
        assert filled == 0 and "headline" in missing


# --------------------------------------------------------------------------
# commande test_apify_enrich
# --------------------------------------------------------------------------

@pytest.mark.django_db
class TestCommand:
    def test_dry_run_n_appelle_pas_l_api(self):
        out = StringIO()
        with patch.object(client, "run_profile_scraper") as run:
            call_command("test_apify_enrich", "--urls", URL, "--dry-run", stdout=out)
        run.assert_not_called()
        assert "[dry]" in out.getvalue()
        assert URL in out.getvalue()

    def test_reel_sans_token_erreur_claire(self):
        with pytest.raises(CommandError, match="EKOALU_APIFY_TOKEN manquant"):
            call_command("test_apify_enrich", "--urls", URL, stdout=StringIO())

    def test_reel_affiche_snapshot_sans_ecrire_en_db(self, monkeypatch):
        from crm.models import Lead

        monkeypatch.setenv("EKOALU_APIFY_TOKEN", "tok-123")
        out = StringIO()
        with patch.object(client, "run_profile_scraper", return_value=[ACTOR_ITEM]):
            call_command("test_apify_enrich", "--urls", URL, stdout=out)
        text = out.getvalue()
        assert "Jean Test" in text
        assert "Leon Grosse" in text
        assert "6/6" in text
        assert Lead.objects.count() == 0  # AUCUNE ecriture DB

    def test_from_serper_prend_les_leads_url_only(self):
        from crm.models import Lead

        Lead.objects.create(  # URL-only sans snapshot -> cible
            linkedin_url=URL, public_identifier="jean-test")
        Lead.objects.create(  # deja un snapshot -> exclu
            linkedin_url="https://www.linkedin.com/in/deja-lu/",
            public_identifier="deja-lu", profile_snapshot={"headline": "x"})
        Lead.objects.create(  # mail-only synthetique -> exclu
            linkedin_url="https://bdd-prospect.local/siren/123",
            public_identifier="bdd-prospect-123")

        out = StringIO()
        call_command("test_apify_enrich", "--from-serper", "10", "--dry-run", stdout=out)
        assert URL in out.getvalue()
        assert "deja-lu" not in out.getvalue()
        assert "bdd-prospect" not in out.getvalue()
        assert "1 profils" in out.getvalue()

    def test_arguments_exclusifs_obligatoires(self):
        with pytest.raises(CommandError, match="--urls OU --from-serper"):
            call_command("test_apify_enrich", stdout=StringIO())
        with pytest.raises(CommandError, match="--urls OU --from-serper"):
            call_command("test_apify_enrich", "--urls", URL, "--from-serper", "3",
                         stdout=StringIO())
