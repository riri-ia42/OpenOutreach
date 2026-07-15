"""Tests du cablage Apify (07/07) : service enrich_urlonly_leads + commande.

Client Apify TOUJOURS mocke (patch de client.run_profile_scraper) — aucun
appel reseau. DB de test pytest-django.
"""
from __future__ import annotations

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import numpy as np
import pytest
from django.core.management import call_command
from django.utils import timezone

from ekoalu.apify_enrich import client, service


def _actor_item(pid: str) -> dict:
    return {
        "linkedinUrl": f"https://www.linkedin.com/in/{pid}/",
        "publicIdentifier": pid,
        "firstName": "Jean",
        "lastName": pid.capitalize(),
        "headline": "Conducteur de travaux TCE",
        "about": "20 ans de chantiers tertiaires.",
        "location": {"linkedinText": "Lyon, France"},
        "experience": [{"position": "Conducteur de travaux",
                        "companyName": "Leon Grosse"}],
    }


@pytest.fixture(autouse=True)
def _apify_configured(monkeypatch):
    monkeypatch.setenv("EKOALU_APIFY_TOKEN", "tok-123")


@pytest.fixture(autouse=True)
def _embed_mock():
    """Stub fastembed (pas de modele ONNX dans les tests ekoalu)."""
    with patch("linkedin.ml.embeddings.embed_text",
               return_value=np.ones(384, dtype=np.float32)):
        yield


@pytest.fixture
def campaign(db):
    from linkedin.models import Campaign

    return Campaign.objects.create(name="EKOALU - ABM - ApifyTest")


def _mk_lead(pid: str, campaign=None, url: str | None = None,
             age_days: int = 0, **kwargs):
    from crm.models import Lead
    from ekoalu.lead_routing.models import LeadDiscovery

    lead = Lead.objects.create(
        public_identifier=pid,
        linkedin_url=url or f"https://www.linkedin.com/in/{pid}/",
        creation_date=timezone.now() - timedelta(days=age_days),
        **kwargs,
    )
    if campaign is not None:
        LeadDiscovery.objects.create(lead=lead, campaign=campaign)
    return lead


# --------------------------------------------------------------------------
# service.enrich_urlonly_leads
# --------------------------------------------------------------------------

@pytest.mark.django_db
class TestEnrichUrlonlyLeads:
    def test_enrichit_stocke_snapshot_et_embedding(self, campaign):
        leads = [_mk_lead(f"lead-{i}", campaign) for i in range(2)]
        items = [_actor_item(ld.public_identifier) for ld in leads]

        with patch.object(client, "run_profile_scraper", return_value=items):
            stats = service.enrich_urlonly_leads(max_leads=10)

        assert stats["selected"] == 2
        assert stats["enriched"] == 2
        assert stats["failed"] == 0
        assert stats["used_today"] == 2
        for lead in leads:
            lead.refresh_from_db()
            assert lead.profile_snapshot["source"] == "apify"
            assert lead.profile_snapshot["fetched_at"]  # isoformat trace
            assert lead.profile_snapshot["headline"] == "Conducteur de travaux TCE"
            assert lead.profile_snapshot_at is not None
            assert lead.embedding is not None

    def test_plus_anciens_d_abord(self, campaign):
        _mk_lead("recent", campaign, age_days=1)
        _mk_lead("ancien", campaign, age_days=30)
        _mk_lead("moyen", campaign, age_days=10)

        with patch.object(client, "run_profile_scraper",
                          side_effect=lambda urls: [
                              _actor_item(u.rstrip("/").rsplit("/", 1)[-1])
                              for u in urls]) as run:
            stats = service.enrich_urlonly_leads(max_leads=2)

        assert stats["enriched"] == 2
        sent = run.call_args[0][0]
        assert "ancien" in sent[0] and "moyen" in sent[1]

    def test_respecte_plafond_quotidien(self, campaign, monkeypatch):
        monkeypatch.setenv("EKOALU_APIFY_DAILY_CAP", "2")
        for i in range(3):
            _mk_lead(f"lead-{i}", campaign)

        with patch.object(client, "run_profile_scraper",
                          side_effect=lambda urls: [
                              _actor_item(u.rstrip("/").rsplit("/", 1)[-1])
                              for u in urls]) as run:
            stats = service.enrich_urlonly_leads(max_leads=10)
            assert stats["selected"] == 2      # borne par le plafond, pas --max
            assert stats["used_today"] == 2

            # plafond atteint : arret propre, AUCUN nouvel appel API
            stats2 = service.enrich_urlonly_leads(max_leads=10)

        assert stats2["selected"] == 0
        assert run.call_count == 1

    def test_kill_switch_service_inactif(self, campaign, monkeypatch):
        monkeypatch.setenv("EKOALU_APIFY_ENRICH", "0")
        _mk_lead("lead-a", campaign)

        with patch.object(client, "run_profile_scraper") as run:
            stats = service.enrich_urlonly_leads(max_leads=10)

        assert stats["enabled"] is False
        assert stats["selected"] == 0
        run.assert_not_called()
        assert service.used_today() == 0

    def test_skip_synthetiques_disqualifies_campagnes_inactives(self, campaign):
        from linkedin.models import Campaign

        inactive = Campaign.objects.create(name="EKOALU - ABM - Pausee", active=False)
        good = _mk_lead("bon-lead", campaign)
        _mk_lead("bdd-prospect-123", campaign,
                 url="https://bdd-prospect.local/siren/123")   # mail-only
        _mk_lead("disqualifie", campaign, disqualified=True)
        _mk_lead("campagne-pausee", inactive)
        _mk_lead("sans-discovery", None)
        _mk_lead("deja-snapshot", campaign, profile_snapshot={"headline": "x"})

        assert [ld.pk for ld in service.candidate_leads(10)] == [good.pk]

    def test_echec_profil_lead_intact(self, campaign):
        ok = _mk_lead("present", campaign)
        absent = _mk_lead("absent", campaign)
        # l'acteur ne renvoie que le 1er profil + un item sans publicIdentifier
        items = [_actor_item("present"), {"headline": "sans identite"}]

        with patch.object(client, "run_profile_scraper", return_value=items):
            stats = service.enrich_urlonly_leads(max_leads=10)

        assert stats["enriched"] == 1
        assert stats["failed"] == 1
        # 15/07 : l'echec est REMBOURSE du plafond + trace dans failed
        assert service.used_today() == 1
        assert service.failed_today() == 1
        absent.refresh_from_db()
        assert absent.profile_snapshot is None      # lead laisse INTACT
        assert absent.embedding is None             # repli Voyager possible
        ok.refresh_from_db()
        assert ok.profile_snapshot["source"] == "apify"

    def test_run_en_echec_tous_les_leads_intacts(self, campaign):
        leads = [_mk_lead(f"lead-{i}", campaign) for i in range(2)]

        with patch.object(client, "run_profile_scraper",
                          side_effect=RuntimeError("Apify HTTP 500")):
            stats = service.enrich_urlonly_leads(max_leads=10)

        assert stats["failed"] == 2
        assert stats["enriched"] == 0
        # 15/07 : echecs rembourses du plafond (un actor HS ne doit pas
        # saturer le cap — cf. panne HarvestAPI limite 20 runs plan Free)
        assert service.used_today() == 0
        assert service.failed_today() == 2
        for lead in leads:
            lead.refresh_from_db()
            assert lead.profile_snapshot is None
            assert lead.embedding is None

    def test_echecs_rembourses_ne_bloquent_pas_le_lendemain_meme_jour(
        self, campaign, monkeypatch,
    ):
        """Actor HS : les tentatives echouees ne consomment pas le plafond —
        un retour a la normale dans la journee reprend immediatement."""
        monkeypatch.setenv("EKOALU_APIFY_DAILY_CAP", "2")
        _mk_lead("lead-ko", campaign)

        with patch.object(client, "run_profile_scraper",
                          side_effect=RuntimeError("limite 20 runs")):
            service.enrich_urlonly_leads(max_leads=10)
        assert service.remaining_today() == 2      # plafond integralement rendu

        _mk_lead("lead-ok", campaign)
        with patch.object(client, "run_profile_scraper",
                          side_effect=lambda urls: [
                              _actor_item(u.rstrip("/").rsplit("/", 1)[-1])
                              for u in urls]):
            stats = service.enrich_urlonly_leads(max_leads=10)
        assert stats["enriched"] == 2   # lead-ko retente + lead-ok


# --------------------------------------------------------------------------
# service.enrich_lead (chemin daemon, 1 profil)
# --------------------------------------------------------------------------

@pytest.mark.django_db
class TestEnrichLead:
    def test_succes_pose_snapshot_et_embedding(self, campaign):
        lead = _mk_lead("solo", campaign)
        with patch.object(client, "run_profile_scraper",
                          return_value=[_actor_item("solo")]):
            assert service.enrich_lead(lead) is True
        lead.refresh_from_db()
        assert lead.profile_snapshot["source"] == "apify"
        assert lead.embedding is not None
        assert service.used_today() == 1

    def test_non_configure_false_sans_appel(self, campaign, monkeypatch):
        monkeypatch.delenv("EKOALU_APIFY_TOKEN")
        lead = _mk_lead("solo", campaign)
        with patch.object(client, "run_profile_scraper") as run:
            assert service.enrich_lead(lead) is False
        run.assert_not_called()
        assert service.used_today() == 0

    def test_url_synthetique_false_sans_consommer(self, campaign):
        lead = _mk_lead("bdd-prospect-9", campaign,
                        url="https://bdd-prospect.local/siren/9")
        with patch.object(client, "run_profile_scraper") as run:
            assert service.enrich_lead(lead) is False
        run.assert_not_called()
        assert service.used_today() == 0

    def test_echec_run_false_lead_intact(self, campaign):
        lead = _mk_lead("solo", campaign)
        with patch.object(client, "run_profile_scraper",
                          side_effect=RuntimeError("timeout")):
            assert service.enrich_lead(lead) is False
        lead.refresh_from_db()
        assert lead.profile_snapshot is None
        # 15/07 : tentative remboursee + panne tracee
        assert service.used_today() == 0
        assert service.failed_today() == 1


# --------------------------------------------------------------------------
# commande apify_enrich_backlog
# --------------------------------------------------------------------------

@pytest.mark.django_db
class TestCommand:
    def test_dry_run_liste_sans_appel_api(self, campaign):
        lead = _mk_lead("candidat", campaign)
        out = StringIO()
        with patch.object(client, "run_profile_scraper") as run:
            call_command("apify_enrich_backlog", "--dry-run", stdout=out)
        run.assert_not_called()
        assert lead.linkedin_url in out.getvalue()
        assert "aucune ecriture DB" in out.getvalue()
        assert service.used_today() == 0   # dry-run ne consomme pas le plafond

    def test_run_reel_sortie_lisible(self, campaign):
        _mk_lead("candidat", campaign)
        out = StringIO()
        with patch.object(client, "run_profile_scraper",
                          return_value=[_actor_item("candidat")]):
            call_command("apify_enrich_backlog", "--max", "5", stdout=out)
        text = out.getvalue()
        assert "Traites : 1" in text
        assert "reussis : 1" in text
        assert "echecs : 0" in text
        assert "Cout estime" in text
        assert "compteur du jour : 1/40" in text

    def test_kill_switch_message_explicite(self, campaign, monkeypatch):
        monkeypatch.setenv("EKOALU_APIFY_ENRICH", "0")
        _mk_lead("candidat", campaign)
        out = StringIO()
        with patch.object(client, "run_profile_scraper") as run:
            call_command("apify_enrich_backlog", stdout=out)
        run.assert_not_called()
        assert "Kill-switch actif" in out.getvalue()
