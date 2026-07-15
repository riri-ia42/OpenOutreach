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

# Forme apimaestro (acteur par defaut depuis le 15/07), copiee du test reel :
# basic_info + experience[] (is_current) + profileUrl.
APIMAESTRO_ITEM = {
    "profileUrl": URL,
    "basic_info": {
        "fullname": "jean test",
        "first_name": "jean",
        "last_name": "test",
        "headline": "Conducteur de travaux chez SOTEB",
        "public_identifier": "jean-test",
        "profile_url": "https://linkedin.com/in/jean-test",
        "about": "20 ans de chantiers tertiaires.",
        "location": {"country": "France", "city": "Greater Lyon Area",
                     "full": "Greater Lyon Area", "country_code": "FR"},
    },
    "experience": [
        {"title": "Chef de chantier", "company": "Eiffage", "is_current": False},
        {"title": "Conducteur de travaux", "company": "SOTEB", "is_current": True},
    ],
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
        # apimaestro (defaut 15/07) : usernames + includeEmail False (pas
        # d'email enrichi = point RGPD evite), rien d'autre
        assert post.call_args.kwargs["json"] == {
            "usernames": [URL],
            "includeEmail": False,
        }

    def test_build_input_harvestapi(self, monkeypatch):
        """Repli harvestapi (via env) : queries + mode de facturation."""
        monkeypatch.setenv("EKOALU_APIFY_ACTOR", "harvestapi/linkedin-profile-scraper")
        assert client.build_input([URL]) == {
            "profileScraperMode": client.HARVESTAPI_MODE,
            "queries": [URL],
        }
        assert client.batch_size() == 5
        assert client.estimated_cost_per_profile_usd() == 0.004

    def test_build_input_format_generique_hors_acteurs_connus(self, monkeypatch):
        monkeypatch.setenv("EKOALU_APIFY_ACTOR", "dev_fusion/linkedin-profile-scraper")
        assert client.build_input([URL]) == {"profileUrls": [URL]}

    def test_build_input_decode_urls_percent_encodees(self):
        encoded = "https://www.linkedin.com/in/fran%C3%A7ois-test/"
        payload = client.build_input([encoded])
        assert payload["usernames"] == ["https://www.linkedin.com/in/françois-test/"]

    def test_lots_de_batch_size_et_items_erreur_ecartes(self, monkeypatch):
        """15 URLs -> 2 appels run-sync (lots de 10, apimaestro) ; item
        {'error': ...} isole ecarte."""
        monkeypatch.setenv("EKOALU_APIFY_TOKEN", "tok-123")
        urls = [f"https://www.linkedin.com/in/lead-{i}/" for i in range(15)]
        with patch("ekoalu.apify_enrich.client.requests.post",
                   return_value=_resp([ACTOR_ITEM, {"error": "profil prive"}])) as post:
            items = client.run_profile_scraper(urls)
        assert post.call_count == 2
        assert items == [ACTOR_ITEM] * 2

    def test_not_found_apimaestro_traverse_vers_le_service(self, monkeypatch):
        """Un not-found CIBLE (avec URL) n'est PAS ecarte : le service doit
        disqualifier le lead (sinon re-facture quotidienne, smoke 15/07)."""
        monkeypatch.setenv("EKOALU_APIFY_TOKEN", "tok-123")
        ko = {"message": "No profile found or wrong input",
              "profileUrl": URL, "profile_input": URL}
        with patch("ekoalu.apify_enrich.client.requests.post",
                   return_value=_resp([APIMAESTRO_ITEM, ko])):
            items = client.run_profile_scraper([URL])
        assert items == [APIMAESTRO_ITEM, ko]

    def test_message_generique_sans_url_ecarte(self, monkeypatch):
        monkeypatch.setenv("EKOALU_APIFY_TOKEN", "tok-123")
        with patch("ekoalu.apify_enrich.client.requests.post",
                   return_value=_resp([APIMAESTRO_ITEM,
                                       {"message": "rate limited"}])):
            items = client.run_profile_scraper([URL])
        assert items == [APIMAESTRO_ITEM]

    def test_limite_free_tier_leve_erreur_dediee(self, monkeypatch):
        """Messages reels : apimaestro « Daily free-tier limit of 10 profiles
        reached » ; harvestapi « Free users are limited to 20 runs »."""
        monkeypatch.setenv("EKOALU_APIFY_TOKEN", "tok-123")
        ko = {"message": "Daily free-tier limit of 10 profiles reached. "
                         "Upgrade your Apify plan or wait until tomorrow.",
              "profileUrl": "", "profile_input": ""}
        with patch("ekoalu.apify_enrich.client.requests.post",
                   return_value=_resp([ko])):
            with pytest.raises(client.ApifyDailyLimitError):
                client.run_profile_scraper([URL])

    def test_limite_free_tier_arrete_les_lots_suivants(self, monkeypatch):
        """Des items OK avant la limite : on les garde, on STOPPE les lots
        suivants (payants pour rien), pas d'exception."""
        monkeypatch.setenv("EKOALU_APIFY_TOKEN", "tok-123")
        urls = [f"https://www.linkedin.com/in/lead-{i}/" for i in range(25)]
        ko = {"message": "Daily free-tier limit of 10 profiles reached",
              "profileUrl": "", "profile_input": ""}
        with patch("ekoalu.apify_enrich.client.requests.post",
                   return_value=_resp([APIMAESTRO_ITEM, ko])) as post:
            items = client.run_profile_scraper(urls)
        assert post.call_count == 1     # lots 2 et 3 jamais envoyes
        assert items == [APIMAESTRO_ITEM]

    def test_que_des_erreurs_acteur_leve(self, monkeypatch):
        monkeypatch.setenv("EKOALU_APIFY_TOKEN", "tok-123")
        with patch("ekoalu.apify_enrich.client.requests.post",
                   return_value=_resp([{"error": "free plan: UI only"}])):
            with pytest.raises(RuntimeError, match="que des erreurs acteur"):
                client.run_profile_scraper([URL])

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

    def test_item_apimaestro_mappe_vers_snapshot(self):
        """Forme apimaestro (acteur par defaut 15/07, copie du test reel)."""
        snap = map_actor_item(APIMAESTRO_ITEM)
        assert snap["full_name"] == "jean test"
        assert snap["headline"] == "Conducteur de travaux chez SOTEB"
        assert snap["summary"] == "20 ans de chantiers tertiaires."
        assert snap["public_identifier"] == "jean-test"
        assert snap["location_name"] == "Greater Lyon Area"
        assert snap["country_code"] == "FR"
        # le poste is_current passe en positions[0] (convention Voyager)
        assert snap["positions"][0]["title"] == "Conducteur de travaux"
        assert snap["positions"][0]["company_name"] == "SOTEB"
        assert snap["positions"][1]["company_name"] == "Eiffage"
        assert snap["source"] == "apify"

    def test_item_apimaestro_completude(self):
        filled, total, missing = snapshot_completeness(map_actor_item(APIMAESTRO_ITEM))
        assert (filled, total, missing) == (6, 6, [])

    def test_item_apimaestro_public_identifier_derive_de_l_url(self):
        snap = map_actor_item({"basic_info": {"headline": "x"}, "profileUrl": URL})
        assert snap["public_identifier"] == "jean-test"

    def test_item_not_found_marque(self):
        snap = map_actor_item({"message": "No profile found or wrong input",
                               "profileUrl": URL, "profile_input": URL})
        assert snap["not_found"] is True
        assert snap["public_identifier"] == "jean-test"


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
