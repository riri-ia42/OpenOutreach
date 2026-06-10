"""Tests détection bounces/NDR (ekoalu/email_canal/bounce.py + intégration poller)."""
from __future__ import annotations

import pytest

from crm.models import Lead
from ekoalu.email_canal.bounce import find_bounced_lead, is_bounce_message, process_bounce
from ekoalu.email_canal.inbox_poller import process_message

pytestmark = pytest.mark.django_db


def _lead(slug: str, email: str, **kwargs) -> Lead:
    return Lead.objects.create(
        public_identifier=slug,
        linkedin_url=f"https://www.linkedin.com/in/{slug}/",
        contact_email=email,
        **kwargs,
    )


def _ndr_msg(body: str, sender="postmaster@outlook.com",
             subject="Undeliverable: Menuiserie alu coupe-feu") -> dict:
    return {
        "id": "ndr-001",
        "from_email": sender,
        "subject": subject,
        "body_text": body,
    }


class TestDetection:
    def test_postmaster_est_un_bounce(self):
        assert is_bounce_message(_ndr_msg("x"))

    def test_mailer_daemon_est_un_bounce(self):
        assert is_bounce_message(_ndr_msg("x", sender="MAILER-DAEMON@mta.orange.fr"))

    def test_exchange_system_est_un_bounce(self):
        assert is_bounce_message(_ndr_msg(
            "x", sender="MicrosoftExchange329e71ec88ae4615bbc36ab6ce41109e@ekoalu.com",
            subject="Non remis : Menuiserie alu",
        ))

    def test_sujet_ndr_francais(self):
        assert is_bounce_message(
            {"from_email": "system@mta.fr", "subject": "Échec de la remise : test", "body_text": ""}
        )

    def test_mail_normal_pas_un_bounce(self):
        assert not is_bounce_message({
            "from_email": "jean.dupont@entreprise.fr",
            "subject": "RE: Menuiserie alu coupe-feu",
            "body_text": "Bonjour, intéressé par vos châssis EI60.",
        })


class TestMarquage:
    def test_marque_le_lead_dont_l_adresse_est_dans_le_body(self):
        lead = _lead("jean-d", "jean.dupont@entreprise.fr")
        msg = _ndr_msg(
            "Your message to jean.dupont@entreprise.fr couldn't be delivered.\n"
            "jean.dupont@entreprise.fr wasn't found at entreprise.fr."
        )
        assert process_bounce(msg) == "bounce_marked"
        lead.refresh_from_db()
        assert lead.email_bounced_at is not None

    def test_ignore_les_adresses_ekoalu_dans_le_ndr(self):
        _lead("riri", "richard@ekoalu.com")  # ne doit jamais matcher
        msg = _ndr_msg("Sender: richard@ekoalu.com — recipient inconnu@nowhere.fr not found")
        assert process_bounce(msg) == "bounce_unmatched"

    def test_ndr_sans_lead_correspondant(self):
        assert process_bounce(_ndr_msg("recipient unknown@autre.fr not found")) == "bounce_unmatched"

    def test_idempotent_date_conservee(self):
        lead = _lead("marc-p", "marc@pme.fr")
        msg = _ndr_msg("marc@pme.fr not found")
        process_bounce(msg)
        lead.refresh_from_db()
        first = lead.email_bounced_at
        process_bounce(msg)
        lead.refresh_from_db()
        assert lead.email_bounced_at == first


class TestIntegrationPoller:
    def test_process_message_route_les_ndr(self):
        lead = _lead("luc-m", "luc@batiment.fr")
        result = process_message(_ndr_msg("luc@batiment.fr unknown"), generate_draft=False)
        assert result == "bounce_marked"
        lead.refresh_from_db()
        assert lead.email_bounced_at is not None
        # Aucun PendingReply créé pour un NDR
        from ekoalu.inbox_assist.models import PendingReply
        assert PendingReply.objects.count() == 0


class TestExclusionEnvoi:
    def test_sender_refuse_un_lead_bounce(self):
        from django.utils import timezone

        from ekoalu.email_canal.sender import _resolve_recipient
        from ekoalu.outbound_validation.models import OutboundKind, PendingOutbound

        _lead("dead-mail", "dead@pme.fr", email_bounced_at=timezone.now())
        po = PendingOutbound.objects.create(
            prospect_public_id="dead-mail", kind=OutboundKind.EMAIL_COLD,
            ai_draft="corps", subject="Sujet",
        )
        assert _resolve_recipient(po) is None
