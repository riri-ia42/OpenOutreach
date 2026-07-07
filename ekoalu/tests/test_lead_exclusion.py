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


# --- LOT D : annulation des tasks ouvertes + chemins routés ------------------


def test_cascade_annule_les_tasks_ouvertes():
    """check_pending/follow_up PENDING du lead disqualifié = annulées (COMPLETED),
    la task d'un AUTRE lead et la connect campaign-level restent intactes."""
    from linkedin.models import Task

    campaign = Campaign.objects.create(name="EKOALU - Test")
    _make_lead_with_connected_deal("dave-t", campaign)
    from django.utils import timezone
    now = timezone.now()
    t_check = Task.objects.create(
        task_type=Task.TaskType.CHECK_PENDING, scheduled_at=now,
        payload={"campaign_id": campaign.pk, "public_id": "dave-t", "backoff_hours": 24},
    )
    t_follow = Task.objects.create(
        task_type=Task.TaskType.FOLLOW_UP, scheduled_at=now,
        payload={"campaign_id": campaign.pk, "public_id": "dave-t"},
    )
    t_autre = Task.objects.create(
        task_type=Task.TaskType.FOLLOW_UP, scheduled_at=now,
        payload={"campaign_id": campaign.pk, "public_id": "autre-lead"},
    )
    t_connect = Task.objects.create(
        task_type=Task.TaskType.CONNECT, scheduled_at=now,
        payload={"campaign_id": campaign.pk},
    )

    disqualify_leads(["dave-t"], "test tasks")

    t_check.refresh_from_db(); t_follow.refresh_from_db()
    t_autre.refresh_from_db(); t_connect.refresh_from_db()
    assert t_check.status == Task.Status.COMPLETED
    assert t_check.completed_at is not None
    assert t_follow.status == Task.Status.COMPLETED
    assert t_autre.status == Task.Status.PENDING
    assert t_connect.status == Task.Status.PENDING


def test_cascade_outcome_et_reason_personnalises():
    """Les chemins non "refus Richard" (Unreachable, déjà-relation) gardent
    leur sémantique via outcome/deal_reason."""
    from crm.models.deal import Outcome

    campaign = Campaign.objects.create(name="EKOALU - Test")
    lead = _make_lead_with_connected_deal("erin-u", campaign)
    disqualify_leads(
        ["erin-u"], "deja relation",
        outcome=Outcome.PRE_EXISTING_RELATION.value,
        deal_reason="Deja relation LinkedIn: reseau Richard",
    )
    deal = Deal.objects.get(lead=lead)
    assert deal.outcome == Outcome.PRE_EXISTING_RELATION.value
    assert deal.reason == "Deja relation LinkedIn: reseau Richard"


def test_disqualify_lead_linkedin_route_vers_la_cascade():
    """linkedin.db.leads.disqualify_lead (chemin connect Unreachable) passe par
    la cascade : Deal clos (unresponsive) + PO ouvert rejeté."""
    from crm.models.deal import Outcome
    from linkedin.db.leads import disqualify_lead

    campaign = Campaign.objects.create(name="EKOALU - Test")
    lead = _make_lead_with_connected_deal("frank-v", campaign)
    po = PendingOutbound.objects.create(
        prospect_public_id="frank-v", campaign_id=campaign.pk,
        kind=OutboundKind.INVITATION, ai_draft="inv", status=OutboundStatus.PENDING,
    )

    disqualify_lead("frank-v", reason="Unreachable: no Connect button after 3 attempts")

    lead.refresh_from_db()
    assert lead.disqualified is True
    deal = Deal.objects.get(lead=lead)
    assert deal.state == ProfileState.FAILED.value
    assert deal.outcome == Outcome.UNRESPONSIVE.value
    assert "Unreachable" in deal.reason
    po.refresh_from_db()
    assert po.status == OutboundStatus.REJECTED


def test_disqualify_lead_inconnu_ne_crashe_pas():
    from linkedin.db.leads import disqualify_lead
    disqualify_lead("inexistant-slug")  # warning + no-op
