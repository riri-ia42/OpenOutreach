"""Tests mini-fiche SERP (Lot 1, 02/09) — parser + persistance au sourcing."""
from __future__ import annotations

import pytest

from ekoalu.google_sourcing.snippet_profile import (
    build_snapshot,
    enrich_lead_from_serp,
    parse_serp,
)

pytestmark = pytest.mark.django_db


class TestParseSerp:
    def test_titre_trois_parties(self):
        p = parse_serp("Jean Dupont - Directeur de travaux - Vinci Construction | LinkedIn", "")
        assert p == {
            "full_name": "Jean Dupont",
            "headline": "Directeur de travaux",
            "company": "Vinci Construction",
            "location": None,
        }

    def test_titre_deux_parties_entreprise_via_chez(self):
        p = parse_serp(
            "Matthieu Carré - Gérant | LinkedIn",
            "Gérant chez ROOTS ARCHITECTURES · Lieu : Lyon · 500 relations",
        )
        assert p["headline"] == "Gérant"
        assert p["company"] == "ROOTS ARCHITECTURES"
        assert p["location"] == "Lyon"

    def test_tirets_en_dash(self):
        p = parse_serp("Marie Noël – Chargée d'affaires – ACME BAT - LinkedIn", "")
        assert p["full_name"] == "Marie Noël"
        assert p["headline"] == "Chargée d'affaires"
        assert p["company"] == "ACME BAT"

    def test_localisation_region_de(self):
        p = parse_serp("A B - Conducteur de travaux - X | LinkedIn",
                       "Région de Lyon, France · ...")
        assert p["location"].startswith("Lyon")

    def test_localisation_peripherie(self):
        p = parse_serp("A B - BE - X | LinkedIn", "Villeurbanne et périphérie")
        assert "Villeurbanne" in p["location"]

    def test_titre_nom_seul_inexploitable(self):
        assert parse_serp("Jean Dupont | LinkedIn", "quelque snippet") is None
        assert parse_serp("", "snippet") is None

    def test_suffixe_linkedin_france(self):
        p = parse_serp("Jean Dupont - PDG - ACME | LinkedIn France", "")
        assert p["company"] == "ACME"


class TestBuildSnapshot:
    def test_forme_interne_voyager(self):
        parsed = parse_serp("Jean Dupont - Gérant - ACME | LinkedIn",
                            "Lieu : Chasselay · alu")
        snap = build_snapshot("jean-dupont-123", "https://www.linkedin.com/in/jean-dupont-123",
                              parsed, "Lieu : Chasselay · alu")
        assert snap["source"] == "serper_snippet"
        assert snap["partial"] is True
        assert snap["headline"] == "Gérant"
        assert snap["first_name"] == "Jean"
        assert snap["last_name"] == "Dupont"
        assert snap["positions"][0]["company_name"] == "ACME"
        assert snap["location_name"] == "Chasselay"
        # le snippet brut nourrit l'embedding via summary
        assert "alu" in snap["summary"]


class TestEnrichLeadFromSerp:
    def _lead(self, pid="jean-dupont-123"):
        from crm.models import Lead

        return Lead.objects.create(
            public_identifier=pid,
            linkedin_url=f"https://www.linkedin.com/in/{pid}",
        )

    def test_pose_snapshot_et_embedding(self, monkeypatch):
        from ekoalu.google_sourcing.models import SerpMeta

        lead = self._lead()
        SerpMeta.objects.create(
            lead=lead,
            title="Jean Dupont - Gérant - ACME | LinkedIn",
            snippet="Lieu : Lyon",
        )
        embedded = {}
        monkeypatch.setattr("crm.models.Lead.embed_from_profile",
                            lambda self, prof: embedded.update(prof))
        assert enrich_lead_from_serp(lead) is True
        lead.refresh_from_db()
        assert lead.profile_snapshot["headline"] == "Gérant"
        assert lead.profile_snapshot["source"] == "serper_snippet"
        assert embedded["headline"] == "Gérant"

    def test_sans_serpmeta_false(self):
        lead = self._lead("autre-456")
        assert enrich_lead_from_serp(lead) is False

    def test_title_inexploitable_false(self):
        from ekoalu.google_sourcing.models import SerpMeta

        lead = self._lead("nom-seul-789")
        SerpMeta.objects.create(lead=lead, title="Jean Dupont | LinkedIn", snippet="")
        assert enrich_lead_from_serp(lead) is False
        lead.refresh_from_db()
        assert lead.profile_snapshot is None


class TestPersistanceAuSourcing:
    def test_persist_harvest_stocke_serpmeta(self):
        from crm.models import Lead
        from linkedin.models import Campaign
        from ekoalu.google_sourcing.models import SerpMeta
        from ekoalu.google_sourcing.service import SourcingResult, _Harvest, _persist_harvest

        campaign, _ = Campaign.objects.get_or_create(name="EKOALU - ABM - Test SERP")
        url = "https://www.linkedin.com/in/jean-serp-1"
        harvest = _Harvest(
            new_urls=[url],
            query_by_url={url: 'site:linkedin.com/in "ACME" "gérant"'},
            meta_by_url={url: {"title": "Jean Serp - Gérant - ACME | LinkedIn",
                               "snippet": "Lieu : Lyon"}},
        )
        _persist_harvest(campaign, harvest, SourcingResult(), dry_run=False)
        lead = Lead.objects.get(public_identifier="jean-serp-1")
        meta = SerpMeta.objects.get(lead=lead)
        assert meta.title.startswith("Jean Serp")
        assert "Lyon" in meta.snippet

    def test_redecouverte_met_a_jour_le_title(self):
        from crm.models import Lead
        from linkedin.models import Campaign
        from ekoalu.google_sourcing.models import SerpMeta
        from ekoalu.google_sourcing.service import SourcingResult, _Harvest, _persist_harvest

        campaign, _ = Campaign.objects.get_or_create(name="EKOALU - ABM - Test SERP 2")
        url = "https://www.linkedin.com/in/jean-serp-2"
        for title in ("Jean Serp - Ancien poste - X | LinkedIn",
                      "Jean Serp - Nouveau poste - Y | LinkedIn"):
            harvest = _Harvest(new_urls=[url], query_by_url={url: "q"},
                               meta_by_url={url: {"title": title, "snippet": ""}})
            _persist_harvest(campaign, harvest, SourcingResult(), dry_run=False)
        lead = Lead.objects.get(public_identifier="jean-serp-2")
        assert "Nouveau poste" in SerpMeta.objects.get(lead=lead).title
