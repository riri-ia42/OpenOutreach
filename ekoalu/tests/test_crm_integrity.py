"""Tests de l'audit de coherence CRM (ekoalu/crm_integrity + commande)."""
from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from crm.models import Deal, Lead
from ekoalu.crm_integrity import collect_anomalies, fix_anomalies, total_issues
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound
from linkedin.enums import ProfileState
from linkedin.models import Campaign

pytestmark = pytest.mark.django_db


@pytest.fixture
def campaign():
    return Campaign.objects.create(name="EKOALU - Test")


def _lead(slug: str, **kwargs) -> Lead:
    return Lead.objects.create(
        public_identifier=slug,
        linkedin_url=f"https://www.linkedin.com/in/{slug}/",
        **kwargs,
    )


def test_base_saine_zero_anomalie(campaign):
    lead = _lead("sain-1")
    Deal.objects.create(lead=lead, campaign=campaign, state=ProfileState.PENDING.value)
    PendingOutbound.objects.create(
        prospect_public_id="sain-1", campaign_id=campaign.pk,
        kind=OutboundKind.INVITATION, ai_draft="x", status=OutboundStatus.PENDING,
    )
    assert total_issues(collect_anomalies()) == 0


def test_detecte_lead_disqualifie_deal_actif(campaign):
    lead = _lead("zombie-1", disqualified=True)
    deal = Deal.objects.create(lead=lead, campaign=campaign, state=ProfileState.CONNECTED.value)

    anomalies = collect_anomalies()
    assert len(anomalies["disqualified_active_deals"]) == 1

    fix_anomalies(anomalies)
    deal.refresh_from_db()
    assert deal.state == ProfileState.FAILED.value
    assert total_issues(collect_anomalies()) == 0  # idempotent


def test_detecte_po_ouvert_lead_mort(campaign):
    _lead("mort-1", disqualified=True)
    po = PendingOutbound.objects.create(
        prospect_public_id="mort-1", campaign_id=campaign.pk,
        kind=OutboundKind.FOLLOW_UP, ai_draft="x", status=OutboundStatus.APPROVED,
    )
    anomalies = collect_anomalies()
    assert len(anomalies["open_po_dead_lead"]) == 1
    fix_anomalies(anomalies)
    po.refresh_from_db()
    assert po.status == OutboundStatus.REJECTED


def test_detecte_invitation_ouverte_deal_traite(campaign):
    lead = _lead("done-1")
    Deal.objects.create(lead=lead, campaign=campaign, state=ProfileState.CONNECTED.value)
    po = PendingOutbound.objects.create(
        prospect_public_id="done-1", campaign_id=campaign.pk,
        kind=OutboundKind.INVITATION, ai_draft="x", status=OutboundStatus.PENDING,
    )
    anomalies = collect_anomalies()
    assert len(anomalies["open_po_obsolete_deal"]) == 1
    fix_anomalies(anomalies)
    po.refresh_from_db()
    assert po.status == OutboundStatus.REJECTED


def test_detecte_doublon_po_ouvert_garde_le_plus_recent(campaign):
    _lead("dup-1")
    old = PendingOutbound.objects.create(
        prospect_public_id="dup-1", campaign_id=campaign.pk,
        kind=OutboundKind.INVITATION, ai_draft="vieux", status=OutboundStatus.PENDING,
    )
    new = PendingOutbound.objects.create(
        prospect_public_id="dup-1", campaign_id=campaign.pk,
        kind=OutboundKind.INVITATION, ai_draft="recent", status=OutboundStatus.PENDING,
    )
    anomalies = collect_anomalies()
    assert len(anomalies["duplicate_open_po"]) == 1
    fix_anomalies(anomalies)
    old.refresh_from_db()
    new.refresh_from_db()
    assert old.status == OutboundStatus.REJECTED
    assert new.status == OutboundStatus.PENDING


def test_multi_active_deals_report_seulement(campaign):
    other = Campaign.objects.create(name="EKOALU - Autre")
    lead = _lead("multi-1")
    Deal.objects.create(lead=lead, campaign=campaign, state=ProfileState.CONNECTED.value)
    Deal.objects.create(lead=lead, campaign=other, state=ProfileState.PENDING.value)

    anomalies = collect_anomalies()
    assert anomalies["multi_active_deals"] == [("multi-1", 2)]
    fix_anomalies(anomalies)
    # non corrige (consolidate_duplicate_deals s'en charge)
    assert len(collect_anomalies()["multi_active_deals"]) == 1


def test_commande_rapport_et_fix(campaign):
    _lead("cmd-1", disqualified=True)
    PendingOutbound.objects.create(
        prospect_public_id="cmd-1", campaign_id=campaign.pk,
        kind=OutboundKind.INVITATION, ai_draft="x", status=OutboundStatus.PENDING,
    )
    out = StringIO()
    call_command("check_crm_integrity", stdout=out)
    assert "Anomalies detectees : 1" in out.getvalue()
    # rapport seul : rien corrige
    assert PendingOutbound.objects.filter(status=OutboundStatus.PENDING).count() == 1

    out = StringIO()
    call_command("check_crm_integrity", "--fix", stdout=out)
    assert PendingOutbound.objects.filter(status=OutboundStatus.REJECTED).count() == 1

    out = StringIO()
    call_command("check_crm_integrity", stdout=out)
    assert "CRM coherent" in out.getvalue()
