"""Tests dedup cross-campagne : 1 Lead = 1 Deal actif maximum."""
from __future__ import annotations

import pytest

from ekoalu.dedup.consolidator import (
    SHADOW_OUTCOMES,
    consolidate_duplicate_deals,
    has_active_deal_elsewhere,
    pick_best_deal,
)


@pytest.fixture
def lead_factory(db):
    from crm.models import Lead

    counter = {"i": 0}

    def make(slug: str | None = None):
        counter["i"] += 1
        slug = slug or f"test-lead-{counter['i']}"
        return Lead.objects.create(
            public_identifier=slug,
            linkedin_url=f"https://www.linkedin.com/in/{slug}/",
        )

    return make


@pytest.fixture
def campaign_factory(db):
    from linkedin.models import Campaign

    counter = {"i": 0}

    def make(name: str | None = None):
        counter["i"] += 1
        name = name or f"EKOALU - ABM - Test {counter['i']}"
        return Campaign.objects.create(name=name)

    return make


@pytest.fixture
def deal_factory(db):
    from crm.models import Deal

    def make(lead, campaign, state="Qualified", outcome=""):
        return Deal.objects.create(
            lead=lead, campaign=campaign, state=state, outcome=outcome,
        )

    return make


# ── pick_best_deal ────────────────────────────────────────────────────


@pytest.mark.django_db
class TestPickBestDeal:
    def test_priorite_etat_avance(self, lead_factory, campaign_factory, deal_factory):
        """Connected > Pending > Ready_to_connect > Qualified."""
        lead = lead_factory()
        c1, c2, c3 = campaign_factory(), campaign_factory(), campaign_factory()
        d_qual = deal_factory(lead, c1, "Qualified")
        d_conn = deal_factory(lead, c2, "Connected")
        d_pend = deal_factory(lead, c3, "Pending")
        best = pick_best_deal([d_qual, d_conn, d_pend])
        assert best.pk == d_conn.pk

    def test_egalite_etat_garde_plus_recent(self, lead_factory, campaign_factory, deal_factory):
        from django.utils import timezone
        from datetime import timedelta

        lead = lead_factory()
        c1, c2 = campaign_factory(), campaign_factory()
        d_old = deal_factory(lead, c1, "Pending")
        d_new = deal_factory(lead, c2, "Pending")
        # Forcer un update_date plus ancien sur d_old
        from crm.models import Deal
        Deal.objects.filter(pk=d_old.pk).update(update_date=timezone.now() - timedelta(days=2))
        d_old.refresh_from_db()
        d_new.refresh_from_db()
        assert pick_best_deal([d_old, d_new]).pk == d_new.pk

    def test_un_seul_deal_renvoie_lui_meme(self, lead_factory, campaign_factory, deal_factory):
        lead = lead_factory()
        c = campaign_factory()
        d = deal_factory(lead, c, "Connected")
        assert pick_best_deal([d]).pk == d.pk


# ── consolidate_duplicate_deals ───────────────────────────────────────


@pytest.mark.django_db
class TestConsolidate:
    def test_idempotent_sans_doublons(self, lead_factory, campaign_factory, deal_factory):
        """Si aucun doublon, la passe ne modifie rien."""
        lead = lead_factory()
        c = campaign_factory()
        deal_factory(lead, c, "Connected")
        r = consolidate_duplicate_deals()
        assert r.leads_with_duplicates == 0
        assert r.deals_demoted_to_duplicate == 0

    def test_dedup_3_campagnes_garde_meilleure(self, lead_factory, campaign_factory, deal_factory):
        """1 Lead × 3 Deals → garde Connected, démote Pending + Qualified."""
        from crm.models import Deal

        lead = lead_factory("coline-test")
        c_qual = campaign_factory("EKOALU - ABM - Q")
        c_conn = campaign_factory("EKOALU - ABM - C")
        c_pend = campaign_factory("EKOALU - ABM - P")
        deal_factory(lead, c_qual, "Qualified")
        d_conn = deal_factory(lead, c_conn, "Connected")
        deal_factory(lead, c_pend, "Pending")

        r = consolidate_duplicate_deals()
        assert r.leads_with_duplicates == 1
        assert r.deals_demoted_to_duplicate == 2

        # Le Deal Connected est conservé tel quel
        d_conn.refresh_from_db()
        assert d_conn.state == "Connected"
        assert d_conn.outcome == ""

        # Les 2 autres sont shadow Completed/duplicate_campaign
        others = Deal.objects.filter(lead=lead).exclude(pk=d_conn.pk)
        assert others.count() == 2
        for d in others:
            assert d.state == "Completed"
            assert d.outcome == "duplicate_campaign"
            assert "Doublon" in d.reason

    def test_cancel_pending_outbound_des_shadows(
        self, lead_factory, campaign_factory, deal_factory,
    ):
        """Les PendingOutbound des Deals démontés sont REJECTED."""
        from ekoalu.outbound_validation.models import (
            OutboundKind,
            OutboundStatus,
            PendingOutbound,
        )

        lead = lead_factory("rothblum-test")
        c_keep = campaign_factory("EKOALU - ABM - A")
        c_drop = campaign_factory("EKOALU - ABM - B")
        d_keep = deal_factory(lead, c_keep, "Connected")  # gardé
        d_drop = deal_factory(lead, c_drop, "Pending")    # démonté
        # PO follow-up sur la campagne shadow
        po = PendingOutbound.objects.create(
            prospect_public_id="rothblum-test",
            campaign_id=c_drop.pk,
            kind=OutboundKind.FOLLOW_UP,
            ai_draft="brouillon shadow",
            status=OutboundStatus.PENDING,
        )

        r = consolidate_duplicate_deals()
        assert r.pending_outbound_cancelled >= 1

        po.refresh_from_db()
        assert po.status == OutboundStatus.REJECTED
        assert "duplicate_campaign" in po.rejection_reason

        d_keep.refresh_from_db()
        d_drop.refresh_from_db()
        assert d_keep.state == "Connected"
        assert d_drop.outcome == "duplicate_campaign"

    def test_normalize_connected_pre_existing(self, lead_factory, campaign_factory, deal_factory):
        """Connected + outcome=pre_existing_relation → Completed."""
        lead = lead_factory()
        c = campaign_factory()
        d = deal_factory(lead, c, "Connected", outcome="pre_existing_relation")
        r = consolidate_duplicate_deals()
        d.refresh_from_db()
        assert r.deals_normalized_pre_existing == 1
        assert d.state == "Completed"
        assert d.outcome == "pre_existing_relation"

    def test_dry_run_ne_modifie_rien(self, lead_factory, campaign_factory, deal_factory):
        """--dry-run : compte sans écrire."""
        from crm.models import Deal

        lead = lead_factory()
        c1, c2 = campaign_factory(), campaign_factory()
        d1 = deal_factory(lead, c1, "Connected")
        d2 = deal_factory(lead, c2, "Pending")

        r = consolidate_duplicate_deals(dry_run=True)
        assert r.deals_demoted_to_duplicate == 1

        # Aucun changement effectif
        d1.refresh_from_db()
        d2.refresh_from_db()
        assert d1.state == "Connected"
        assert d2.state == "Pending"
        assert d2.outcome == ""

    def test_ignore_campagnes_non_ekoalu(self, lead_factory, campaign_factory, deal_factory):
        """Les campagnes hors EKOALU- ne sont pas touchées (autre client / freemium)."""
        lead = lead_factory()
        c_ekoalu = campaign_factory("EKOALU - ABM - A")
        c_freemium = campaign_factory("freemium-default")
        deal_factory(lead, c_ekoalu, "Connected")
        deal_factory(lead, c_freemium, "Pending")
        r = consolidate_duplicate_deals()
        # Pas de doublon vu côté EKOALU (1 seul Deal EKOALU)
        assert r.leads_with_duplicates == 0


# ── Guard à la création (_create_deal) ─────────────────────────────────


@pytest.mark.django_db
class TestCreateDealGuard:
    def test_create_deal_force_shadow_si_deja_actif_ailleurs(
        self, lead_factory, campaign_factory, deal_factory,
    ):
        """Si lead a déjà Deal actif sur autre campagne → nouveau Deal = shadow."""
        from crm.models import Deal
        from linkedin.db.deals import _create_deal

        lead = lead_factory()
        c_existing = campaign_factory("EKOALU - ABM - Existing")
        c_new = campaign_factory("EKOALU - ABM - New")
        deal_factory(lead, c_existing, "Connected")

        # Simuler une session avec campaign=c_new
        class FakeSession:
            campaign = c_new

        new_deal = _create_deal(lead=lead, state="Qualified", session=FakeSession())
        assert new_deal.state == "Completed"
        assert new_deal.outcome == "duplicate_campaign"
        assert "Auto-bloque" in new_deal.reason
        # Le Deal "Existing" reste intact
        assert Deal.objects.filter(lead=lead, state="Connected").count() == 1

    def test_create_deal_FAILED_pas_bloque(
        self, lead_factory, campaign_factory, deal_factory,
    ):
        """Un Deal FAILED (LLM rejection) peut coexister avec un actif ailleurs."""
        from linkedin.db.deals import _create_deal

        lead = lead_factory()
        c1 = campaign_factory("EKOALU - ABM - X")
        c2 = campaign_factory("EKOALU - ABM - Y")
        deal_factory(lead, c1, "Connected")

        class FakeSession:
            campaign = c2

        new_deal = _create_deal(lead=lead, state="Failed", session=FakeSession(), outcome="wrong_fit")
        # Failed n'est pas bloqué : il naît bien en Failed
        assert new_deal.state == "Failed"
        assert new_deal.outcome == "wrong_fit"

    def test_has_active_deal_elsewhere(self, lead_factory, campaign_factory, deal_factory):
        lead = lead_factory()
        c1, c2 = campaign_factory(), campaign_factory()
        deal_factory(lead, c1, "Connected")
        assert has_active_deal_elsewhere(lead=lead, campaign=c2) is not None
        assert has_active_deal_elsewhere(lead=lead, campaign=c1) is None


# ── Vues : exclusion des shadows ────────────────────────────────────────


@pytest.mark.django_db
class TestViewsExcludeShadow:
    def test_deals_filtered_connected_exclut_duplicate_campaign(
        self, lead_factory, campaign_factory, deal_factory,
    ):
        """L'onglet Connectés ne montre PAS les Deals Completed/duplicate_campaign."""
        from django.contrib.auth import get_user_model
        from django.test import Client
        from django.urls import reverse

        User = get_user_model()
        u = User.objects.create_user(username="t", password="p", is_staff=True)
        c = Client()
        c.login(username="t", password="p")

        lead = lead_factory("shadowtest-unique-slug")
        camp_main = campaign_factory("EKOALU - ABM - UniqueMainCampaign")
        camp_shadow = campaign_factory("EKOALU - ABM - UniqueShadowCampaign")
        deal_factory(lead, camp_main, "Connected")
        deal_factory(lead, camp_shadow, "Completed", outcome="duplicate_campaign")

        r = c.get(reverse("ekoalu:deals_filtered") + "?state=connected")
        assert r.status_code == 200
        # La campagne Main doit apparaître, la Shadow non (test sur noms uniques)
        assert b"UniqueMainCampaign" in r.content
        assert b"UniqueShadowCampaign" not in r.content

    def test_outbound_list_exclut_po_de_deal_shadow(
        self, lead_factory, campaign_factory, deal_factory,
    ):
        """L'onglet Messages ne montre PAS les PO dont le Deal est shadow."""
        from django.contrib.auth import get_user_model
        from django.test import Client
        from django.urls import reverse
        from ekoalu.outbound_validation.models import (
            OutboundKind, OutboundStatus, PendingOutbound,
        )

        User = get_user_model()
        u = User.objects.create_user(username="t2", password="p", is_staff=True)
        c = Client()
        c.login(username="t2", password="p")

        lead = lead_factory("po-shadow-test")
        camp_main = campaign_factory("EKOALU - ABM - Main2")
        camp_shadow = campaign_factory("EKOALU - ABM - Shadow2")
        deal_factory(lead, camp_main, "Connected")
        deal_factory(lead, camp_shadow, "Completed", outcome="duplicate_campaign")
        # PO sur Main : doit apparaître
        po_main = PendingOutbound.objects.create(
            prospect_public_id="po-shadow-test", campaign_id=camp_main.pk,
            kind=OutboundKind.FOLLOW_UP, ai_draft="brouillon main",
            status=OutboundStatus.PENDING,
        )
        # PO sur Shadow : doit être masqué
        po_shadow = PendingOutbound.objects.create(
            prospect_public_id="po-shadow-test", campaign_id=camp_shadow.pk,
            kind=OutboundKind.FOLLOW_UP, ai_draft="brouillon shadow",
            status=OutboundStatus.PENDING,
        )

        r = c.get(reverse("ekoalu:outbound_list") + "?status=pending")
        assert r.status_code == 200
        assert b"brouillon main" in r.content
        assert b"brouillon shadow" not in r.content


# ── SHADOW_OUTCOMES constante ──────────────────────────────────────────


def test_shadow_outcomes_includes_expected():
    assert "duplicate_campaign" in SHADOW_OUTCOMES
    assert "pre_existing_relation" in SHADOW_OUTCOMES
