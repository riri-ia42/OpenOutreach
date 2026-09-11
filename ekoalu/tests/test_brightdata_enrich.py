"""Tests Bright Data (Lot 2, 02/09) — client mocké, mapper, service, chaîne."""
from __future__ import annotations

import pytest

from ekoalu.brightdata_enrich import client, service
from ekoalu.brightdata_enrich.mapper import map_record

pytestmark = pytest.mark.django_db


RECORD_NOMINAL = {
    "url": "https://www.linkedin.com/in/jean-dupont-123",
    "name": "Jean Dupont",
    "position": "Directeur de travaux chez Vinci",
    "about": "20 ans de gros oeuvre.",
    "city": "Lyon, Auvergne-Rhône-Alpes, France",
    "country_code": "FR",
    "experience": [
        {"title": "Directeur de travaux", "company": "Vinci Construction",
         "location": "Lyon", "description": "Bureaux et ERP"},
        {"title": "Conducteur de travaux", "company": "Eiffage"},
    ],
    "education": [{"title": "INSA Lyon", "degree": "Ingénieur"}],
}


class TestMapper:
    def test_record_nominal(self):
        snap = map_record(RECORD_NOMINAL)
        assert snap["public_identifier"] == "jean-dupont-123"
        assert snap["full_name"] == "Jean Dupont"
        assert snap["first_name"] == "Jean"
        assert snap["headline"].startswith("Directeur de travaux")
        assert snap["location_name"].startswith("Lyon")
        assert snap["source"] == "brightdata"
        assert snap["positions"][0]["company_name"] == "Vinci Construction"
        assert snap["educations"][0]["school_name"] == "INSA Lyon"

    def test_profil_pauvre_current_company_synthetise_position(self):
        """Smoke réel 02/09 (david-cantais, 28 relations) : experience=null mais
        current_company présent — l'entreprise doit atteindre l'embedding."""
        snap = map_record({
            "url": "https://www.linkedin.com/in/david-cantais-426345248",
            "name": "david cantais",
            "position": None,
            "experience": None,
            "current_company": {"name": "GSE Intégration", "location": None},
            "current_company_name": "GSE Intégration",
            "city": "Yvetot, Normandy, France",
            "country_code": "FR",
        })
        assert snap["positions"] == [{
            "title": None, "company_name": "GSE Intégration", "company_urn": None,
            "location": None, "date_range": None, "description": None, "urn": None,
        }]

    def test_item_erreur_ignore(self):
        assert map_record({"error": "crawl failed", "url": "x"}) is None

    def test_dead_page_marque_not_found(self):
        snap = map_record({
            "error_code": "dead_page",
            "error": "Page not found",
            "url": "https://www.linkedin.com/in/parti-ailleurs",
        })
        assert snap == {"not_found": True, "public_identifier": "parti-ailleurs"}

    def test_record_sans_url_ignore(self):
        assert map_record({"name": "X"}) is None

    def test_non_dict_ignore(self):
        assert map_record(["liste"]) is None


class TestClient:
    def test_trigger_renvoie_snapshot_id(self, monkeypatch):
        captured = {}

        class _Resp:
            status_code = 200
            text = ""
            def raise_for_status(self):
                pass
            def json(self):
                return {"snapshot_id": "s_test123"}

        def _post(url, **kw):
            captured["url"] = url
            captured["params"] = kw.get("params")
            captured["json"] = kw.get("json")
            return _Resp()

        monkeypatch.setenv("EKOALU_BRIGHTDATA_TOKEN", "tok")
        monkeypatch.setattr("ekoalu.brightdata_enrich.client.requests.post", _post)
        sid = client.trigger(["https://www.linkedin.com/in/x"])
        assert sid == "s_test123"
        assert captured["params"]["dataset_id"] == client.DEFAULT_DATASET_ID
        assert captured["json"] == [{"url": "https://www.linkedin.com/in/x"}]

    def test_trigger_auth_refusee(self, monkeypatch):
        class _Resp:
            status_code = 401
            text = "unauthorized"
            def raise_for_status(self):
                raise AssertionError("ne doit pas arriver ici")
            def json(self):
                return {}

        monkeypatch.setenv("EKOALU_BRIGHTDATA_TOKEN", "tok")
        monkeypatch.setattr("ekoalu.brightdata_enrich.client.requests.post",
                            lambda *a, **k: _Resp())
        with pytest.raises(client.BrightdataError, match="auth"):
            client.trigger(["https://www.linkedin.com/in/x"])

    def test_run_poll_jusqu_a_ready(self, monkeypatch):
        monkeypatch.setenv("EKOALU_BRIGHTDATA_TOKEN", "tok")
        monkeypatch.setattr(client, "trigger", lambda urls: "s_1")
        statuses = iter(["running", "ready"])
        monkeypatch.setattr(client, "_progress", lambda sid, timeout=30: next(statuses))
        monkeypatch.setattr(client, "fetch_snapshot",
                            lambda sid, timeout=60: [RECORD_NOMINAL])
        monkeypatch.setattr(client.time, "sleep", lambda s: None)
        records = client.run_profile_scraper(["u"])
        assert records == [RECORD_NOMINAL]

    def test_run_statut_failed_leve(self, monkeypatch):
        monkeypatch.setenv("EKOALU_BRIGHTDATA_TOKEN", "tok")
        monkeypatch.setattr(client, "trigger", lambda urls: "s_1")
        monkeypatch.setattr(client, "_progress", lambda sid, timeout=30: "failed")
        with pytest.raises(client.BrightdataError, match="failed"):
            client.run_profile_scraper(["u"])


def _lead(pid="jean-dupont-123"):
    from crm.models import Lead

    return Lead.objects.create(
        public_identifier=pid,
        linkedin_url=f"https://www.linkedin.com/in/{pid}",
    )


class TestService:
    def test_compteur_mensuel_et_remboursement(self, monkeypatch):
        monkeypatch.setenv("EKOALU_BRIGHTDATA_MONTHLY_CAP", "100")
        assert service.used_this_month() == 0
        service.record_usage(10)
        assert service.used_this_month() == 10
        service.record_failures(4)
        assert service.used_this_month() == 6
        assert service.remaining_this_month() == 94

    def test_pas_pret_sans_token(self, monkeypatch):
        monkeypatch.delenv("EKOALU_BRIGHTDATA_TOKEN", raising=False)
        assert service.brightdata_ready() is False

    def test_kill_switch(self, monkeypatch):
        monkeypatch.setenv("EKOALU_BRIGHTDATA_TOKEN", "tok")
        monkeypatch.setenv("EKOALU_BRIGHTDATA_ENRICH", "0")
        assert service.brightdata_ready() is False

    def test_enrich_leads_succes(self, monkeypatch):
        monkeypatch.setenv("EKOALU_BRIGHTDATA_TOKEN", "tok")
        lead = _lead()
        monkeypatch.setattr("ekoalu.brightdata_enrich.client.run_profile_scraper",
                            lambda urls, **kw: [RECORD_NOMINAL])
        monkeypatch.setattr("crm.models.Lead.embed_from_profile",
                            lambda self, prof: None)
        stats = service.enrich_leads([lead])
        assert stats == {"selected": 1, "enriched": 1, "failed": 0}
        lead.refresh_from_db()
        assert lead.profile_snapshot["source"] == "brightdata"
        assert service.used_this_month() == 1

    def test_echec_run_rembourse_et_laisse_intact(self, monkeypatch):
        monkeypatch.setenv("EKOALU_BRIGHTDATA_TOKEN", "tok")
        lead = _lead("intact-1")

        def _boom(urls, **kw):
            raise client.BrightdataError("panne")

        monkeypatch.setattr("ekoalu.brightdata_enrich.client.run_profile_scraper", _boom)
        stats = service.enrich_leads([lead])
        assert stats["failed"] == 1
        lead.refresh_from_db()
        assert lead.profile_snapshot is None
        assert service.used_this_month() == 0  # remboursé

    def test_url_synthetique_jamais_envoyee(self, monkeypatch):
        from crm.models import Lead

        monkeypatch.setenv("EKOALU_BRIGHTDATA_TOKEN", "tok")
        lead = Lead.objects.create(
            public_identifier="bdd-prospect-123456789",
            linkedin_url="https://bdd-prospect.local/siren/123456789",
        )
        monkeypatch.setattr(
            "ekoalu.brightdata_enrich.client.run_profile_scraper",
            lambda urls, **kw: pytest.fail("ne doit jamais être appelé"),
        )
        assert service.enrich_leads([lead]) == {"selected": 0, "enriched": 0, "failed": 0}

    def test_dead_page_disqualifie(self, monkeypatch):
        monkeypatch.setenv("EKOALU_BRIGHTDATA_TOKEN", "tok")
        lead = _lead("parti-ailleurs")
        monkeypatch.setattr(
            "ekoalu.brightdata_enrich.client.run_profile_scraper",
            lambda urls, **kw: [{"error_code": "dead_page", "error": "not found",
                                 "url": lead.linkedin_url}],
        )
        service.enrich_leads([lead])
        lead.refresh_from_db()
        assert lead.disqualified is True


class TestChaine:
    def test_ordre_brightdata_puis_apify_puis_serp(self, monkeypatch):
        from ekoalu import enrichment_chain

        calls = []
        providers = (
            ("brightdata", lambda: True, lambda ld: calls.append("bd") or False),
            ("apify", lambda: True, lambda ld: calls.append("apify") or True),
            ("serper_snippet", lambda: True,
             lambda ld: pytest.fail("ne doit pas être atteint")),
        )
        monkeypatch.setattr(enrichment_chain, "_providers", lambda: providers)
        assert enrichment_chain.enrich_lead_cookieless(object()) == "apify"
        assert calls == ["bd", "apify"]

    def test_fournisseur_pas_pret_saute(self, monkeypatch):
        from ekoalu import enrichment_chain

        providers = (
            ("brightdata", lambda: False,
             lambda ld: pytest.fail("pas prêt : ne doit pas être appelé")),
            ("serper_snippet", lambda: True, lambda ld: True),
        )
        monkeypatch.setattr(enrichment_chain, "_providers", lambda: providers)
        assert enrichment_chain.enrich_lead_cookieless(object()) == "serper_snippet"

    def test_exception_fournisseur_continue(self, monkeypatch):
        from ekoalu import enrichment_chain

        def _boom(ld):
            raise RuntimeError("fournisseur cassé")

        providers = (
            ("brightdata", lambda: True, _boom),
            ("apify", lambda: True, lambda ld: True),
        )
        monkeypatch.setattr(enrichment_chain, "_providers", lambda: providers)

        class _L:
            public_identifier = "x"

        assert enrichment_chain.enrich_lead_cookieless(_L()) == "apify"

    def test_tous_echouent_none(self, monkeypatch):
        from ekoalu import enrichment_chain

        providers = (("apify", lambda: True, lambda ld: False),)
        monkeypatch.setattr(enrichment_chain, "_providers", lambda: providers)
        assert enrichment_chain.enrich_lead_cookieless(object()) is None

    def test_ordre_reel_des_fournisseurs(self):
        from ekoalu.enrichment_chain import _providers

        names = [name for name, _r, _e in _providers()]
        assert names == ["brightdata", "apify", "serper_snippet"]


class TestRegleCookie:
    """RÈGLE ABSOLUE : rien de notre session LinkedIn ne part chez le
    fournisseur. Le test existait côté Apify, pas côté Bright Data (constat
    11/09) — la garantie ne reposait que sur la forme du payload."""

    def test_trigger_n_envoie_ni_cookie_ni_session(self, monkeypatch):
        import json as _json

        captured = {}

        class _Resp:
            status_code = 200
            text = ""
            def raise_for_status(self):
                return None
            def json(self):
                return {"snapshot_id": "s_1"}

        def _post(url, **kw):
            captured["json"] = kw.get("json")
            captured["headers"] = kw.get("headers") or {}
            return _Resp()

        monkeypatch.setenv("EKOALU_BRIGHTDATA_TOKEN", "tok")
        monkeypatch.setattr("ekoalu.brightdata_enrich.client.requests.post", _post)
        client.trigger(["https://www.linkedin.com/in/jean-dupont-123"])

        sent = _json.dumps(captured["json"]).lower()
        for banned in ("cookie", "li_at", "session", "jsessionid", "csrf"):
            assert banned not in sent
        # seule l'adresse publique part, rien d'autre
        assert captured["json"] == [{"url": "https://www.linkedin.com/in/jean-dupont-123"}]
        # et aucun en-tête ne transporte de cookie (seul le jeton Bright Data)
        assert set(k.lower() for k in captured["headers"]) <= {"authorization", "content-type"}


class TestDelaiDAttente:
    """Le délai d'attente du snapshot suit la taille du lot (11/09).

    Sans ça, monter la passe quotidienne à 200 profils faisait expirer le
    snapshot au bout des 300 s d'origine : tout le lot échouait et se
    remboursait, pour zéro profil enrichi.
    """

    def test_petit_lot_garde_le_plancher(self):
        assert client.poll_timeout_for(1) == client.POLL_TIMEOUT_SECONDS
        assert client.poll_timeout_for(40) == client.POLL_TIMEOUT_SECONDS

    def test_gros_lot_obtient_plus_de_temps(self):
        # 200 profils : ~3 s/profil mesuré, on budgète le double
        assert client.poll_timeout_for(200) == 1200
        assert client.poll_timeout_for(200) > client.POLL_TIMEOUT_SECONDS

    def test_plafonne(self):
        assert client.poll_timeout_for(100000) == client.POLL_TIMEOUT_MAX_SECONDS

    def test_le_chemin_unitaire_ne_bloque_pas_le_daemon(self, monkeypatch):
        """enrich_lead (daemon) plafonne l'attente à 90 s, pas 300."""
        vu = {}

        def _run(urls, poll_interval=10, poll_timeout=None):
            vu["timeout"] = poll_timeout
            return []

        monkeypatch.setattr(service.client, "run_profile_scraper", _run)
        monkeypatch.setattr(service, "brightdata_ready", lambda: True)
        monkeypatch.setattr(service, "remaining_this_month", lambda: 100)
        monkeypatch.setattr(service, "record_usage", lambda n: None)
        monkeypatch.setattr(service, "record_failures", lambda n: None)

        class _Lead:
            linkedin_url = "https://www.linkedin.com/in/jean-dupont-123"
            public_identifier = "jean-dupont-123"

        service.enrich_lead(_Lead())
        assert vu["timeout"] == client.SINGLE_POLL_TIMEOUT_SECONDS == 90


class TestProfilSupprime:
    """Un profil « page morte » ne doit pas être repayé chez le fournisseur
    suivant dans la même passe (constat 11/09)."""

    def test_lead_disqualifie_sort_de_la_liste_de_travail(self, monkeypatch):
        from crm.models import Lead
        from ekoalu.management.commands.enrich_backlog import _still_to_enrich

        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/jean-dupont-123",
            public_identifier="jean-dupont-123")
        assert _still_to_enrich(lead) is True          # sans fiche, actif

        lead.disqualified = True                       # Bright Data : dead_page
        lead.save(update_fields=["disqualified"])
        assert _still_to_enrich(lead) is False         # ne repart pas chez Apify

    def test_lead_enrichi_sort_aussi_de_la_liste(self):
        from crm.models import Lead
        from ekoalu.management.commands.enrich_backlog import _still_to_enrich

        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/marie-durand-9",
            public_identifier="marie-durand-9")
        lead.embedding = b"x" * 8
        lead.save(update_fields=["embedding"])
        assert _still_to_enrich(lead) is False
