"""Tests de la commande triage_failed_outbound (chantier fiabilisation CRM)."""
from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from crm.models import Deal, Lead
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound
from linkedin.enums import ProfileState
from linkedin.models import Campaign

pytestmark = pytest.mark.django_db


def _run(*args) -> str:
    out = StringIO()
    call_command("triage_failed_outbound", *args, stdout=out)
    return out.getvalue()


def _failed_po(pid: str, campaign: Campaign, kind=OutboundKind.INVITATION) -> PendingOutbound:
    return PendingOutbound.objects.create(
        prospect_public_id=pid, campaign_id=campaign.pk,
        kind=kind, ai_draft="draft", status=OutboundStatus.FAILED,
        error_message="Playwright Sync API inside the asyncio loop",
    )


@pytest.fixture
def campaign():
    return Campaign.objects.create(name="EKOALU - Test")


def test_lead_disqualifie_rejete(campaign):
    Lead.objects.create(
        public_identifier="dead-1",
        linkedin_url="https://www.linkedin.com/in/dead-1/",
        disqualified=True,
    )
    po = _failed_po("dead-1", campaign)
    _run()
    po.refresh_from_db()
    assert po.status == OutboundStatus.REJECTED
    assert "disqualifie" in po.rejection_reason


def test_remplace_par_po_plus_recent_rejete(campaign):
    Lead.objects.create(
        public_identifier="dup-1", linkedin_url="https://www.linkedin.com/in/dup-1/",
    )
    old = _failed_po("dup-1", campaign)
    PendingOutbound.objects.create(
        prospect_public_id="dup-1", campaign_id=campaign.pk,
        kind=OutboundKind.INVITATION, ai_draft="new", status=OutboundStatus.SENT,
    )
    _run()
    old.refresh_from_db()
    assert old.status == OutboundStatus.REJECTED
    assert "remplace" in old.rejection_reason


def test_invitation_obsolete_si_deal_connected(campaign):
    lead = Lead.objects.create(
        public_identifier="conn-1", linkedin_url="https://www.linkedin.com/in/conn-1/",
    )
    Deal.objects.create(lead=lead, campaign=campaign, state=ProfileState.CONNECTED.value)
    po = _failed_po("conn-1", campaign)
    _run()
    po.refresh_from_db()
    assert po.status == OutboundStatus.REJECTED
    assert "obsolete" in po.rejection_reason


def test_follow_up_pas_obsolete_si_deal_connected(campaign):
    """Deal Connected = le follow-up reste pertinent -> re-pending."""
    lead = Lead.objects.create(
        public_identifier="fup-1", linkedin_url="https://www.linkedin.com/in/fup-1/",
    )
    Deal.objects.create(lead=lead, campaign=campaign, state=ProfileState.CONNECTED.value)
    po = _failed_po("fup-1", campaign, kind=OutboundKind.FOLLOW_UP)
    _run()
    po.refresh_from_db()
    assert po.status == OutboundStatus.PENDING
    assert "triage_failed_outbound" in po.error_message


def test_transitoire_lead_sain_repasse_pending(campaign):
    lead = Lead.objects.create(
        public_identifier="ok-1", linkedin_url="https://www.linkedin.com/in/ok-1/",
    )
    Deal.objects.create(lead=lead, campaign=campaign, state=ProfileState.PENDING.value)
    po = _failed_po("ok-1", campaign)
    _run()
    po.refresh_from_db()
    assert po.status == OutboundStatus.PENDING
    assert po.ai_draft == "draft"  # brouillon conserve


def test_dry_run_n_ecrit_rien(campaign):
    Lead.objects.create(
        public_identifier="dead-2",
        linkedin_url="https://www.linkedin.com/in/dead-2/",
        disqualified=True,
    )
    po = _failed_po("dead-2", campaign)
    out = _run("--dry-run")
    po.refresh_from_db()
    assert po.status == OutboundStatus.FAILED
    assert "DRY-RUN" in out


def test_idempotent(campaign):
    lead = Lead.objects.create(
        public_identifier="ok-2", linkedin_url="https://www.linkedin.com/in/ok-2/",
    )
    Deal.objects.create(lead=lead, campaign=campaign, state=ProfileState.PENDING.value)
    _failed_po("ok-2", campaign)
    _run()
    out = _run()
    assert "Rien a trier" in out
