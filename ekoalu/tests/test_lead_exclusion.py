"""Tests exclusion permanente : refus -> disqualify + clôture Deals + vide la file."""
from __future__ import annotations

import pytest

from crm.models import Deal, Lead
from ekoalu.lead_exclusion import disqualify_leads
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound
from linkedin.enums import ProfileState
from linkedin.models import Campaign

pytestmark = pytest.mark.django_db


def _make_lead_with_connected_deal(slug: str, campaign: Campaign) -> Lead:
    lead = Lead.objects.create(
        public_identifier=slug, linkedin_url=f"https://www.linkedin.com/in/{slug}/",
    )
    Deal.objects.create(lead=lead, campaign=campaign, state=ProfileState.CONNECTED.value)
    return lead


def test_disqualify_cascade_lead_deal_and_outbound():
    campaign = Campaign.objects.create(name="EKOALU - Test")
    lead = _make_lead_with_connected_deal("alice-x", campaign)
    po = PendingOutbound.objects.create(
        prospect_public_id="alice-x", campaign_id=campaign.pk,
        kind=OutboundKind.FOLLOW_UP, ai_draft="hello", status=OutboundStatus.PENDING,
    )

    n_leads, n_deals = disqualify_leads(["alice-x"], "test")

    assert n_leads == 1 and n_deals == 1
    lead.refresh_from_db()
    assert lead.disqualified is True
    assert Deal.objects.get(lead=lead).state == ProfileState.FAILED.value
    po.refresh_from_db()
    assert po.status == OutboundStatus.REJECTED


def test_disqualify_idempotent_and_handles_empty():
    assert disqualify_leads([], "x") == (0, 0)
    assert disqualify_leads([""], "x") == (0, 0)
    campaign = Campaign.objects.create(name="EKOALU - Test")
    _make_lead_with_connected_deal("bob-y", campaign)
    disqualify_leads(["bob-y"], "first")
    # 2e passe : lead déjà disqualifié -> 0 nouveau lead
    n_leads, _ = disqualify_leads(["bob-y"], "second")
    assert n_leads == 0


def test_sent_outbound_not_touched():
    """Un message déjà envoyé (SENT) reste SENT — on ne réécrit pas l'historique."""
    campaign = Campaign.objects.create(name="EKOALU - Test")
    _make_lead_with_connected_deal("carol-z", campaign)
    sent = PendingOutbound.objects.create(
        prospect_public_id="carol-z", campaign_id=campaign.pk,
        kind=OutboundKind.FOLLOW_UP, ai_draft="hi", status=OutboundStatus.SENT,
    )
    disqualify_leads(["carol-z"], "x")
    sent.refresh_from_db()
    assert sent.status == OutboundStatus.SENT
