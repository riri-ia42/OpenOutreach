"""Tests brique K : croisement variante A/B cold mail ↔ réponse reçue."""
from __future__ import annotations

import pytest
from django.utils import timezone

from crm.models import Lead
from ekoalu.email_canal.inbox_poller import _cold_variant_for, process_message
from ekoalu.inbox_assist.models import PendingReply
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

pytestmark = pytest.mark.django_db


def _lead(slug: str, email: str) -> Lead:
    return Lead.objects.create(
        public_identifier=slug,
        linkedin_url=f"https://www.linkedin.com/in/{slug}/",
        contact_email=email,
    )


def _sent_cold(pid: str, variant: str, when=None) -> PendingOutbound:
    po = PendingOutbound.objects.create(
        prospect_public_id=pid, kind=OutboundKind.EMAIL_COLD,
        ai_draft="corps", subject="Sujet", status=OutboundStatus.SENT,
        prompt_variant=variant,
    )
    po.sent_at = when or timezone.now()
    po.save(update_fields=["sent_at"])
    return po


def test_variante_du_dernier_cold_envoye():
    lead = _lead("k-1", "k1@pme.fr")
    _sent_cold("k-1", "v1", when=timezone.now() - timezone.timedelta(days=10))
    _sent_cold("k-1", "v2", when=timezone.now())
    assert _cold_variant_for(lead) == "v2"


def test_vide_si_aucun_cold_envoye():
    lead = _lead("k-2", "k2@pme.fr")
    assert _cold_variant_for(lead) == ""


def test_reply_taggee_avec_la_variante():
    _lead("k-3", "k3@pme.fr")
    _sent_cold("k-3", "v1")
    msg = {
        "id": "graph-k3",
        "from_email": "k3@pme.fr",
        "subject": "RE: Menuiserie alu",
        "body_text": "Intéressé, pouvez-vous m'en dire plus sur les châssis EI60 ?",
    }
    assert process_message(msg, generate_draft=False) == "draft_created"
    pr = PendingReply.objects.get(inbound_message_id="graph-k3")
    assert pr.cold_variant == "v1"


def test_reply_sans_cold_prealable_variante_vide():
    _lead("k-4", "k4@pme.fr")
    msg = {
        "id": "graph-k4",
        "from_email": "k4@pme.fr",
        "subject": "Question",
        "body_text": "Bonjour, vous faites du mur-rideau ?",
    }
    process_message(msg, generate_draft=False)
    assert PendingReply.objects.get(inbound_message_id="graph-k4").cold_variant == ""
