"""Tests fiabilité du sender outbound_validation."""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from ekoalu.outbound_validation import (
    OutboundKind,
    OutboundStatus,
    PendingOutbound,
)
from ekoalu.outbound_validation.sender import (
    process_approved_queue,
    send_one,
)


@pytest.mark.django_db
class TestSendOne:
    def test_skip_si_pas_approved(self):
        """send_one ne doit RIEN faire si le statut n est pas APPROVED."""
        po = PendingOutbound.objects.create(
            prospect_public_id="test-pending",
            kind=OutboundKind.INVITATION,
            ai_draft="x",
            status=OutboundStatus.PENDING,
        )
        session = MagicMock()
        result = send_one(session, po)
        assert result is False
        po.refresh_from_db()
        assert po.status == OutboundStatus.PENDING  # inchangé

    @pytest.mark.parametrize("field,value,motif", [
        ("disqualified", True, "disqualified"),
        ("unsubscribed_at", "now", "unsubscribed"),
        ("email_bounced_at", "now", "bounced"),
    ])
    def test_reject_si_lead_exclu(self, field, value, motif):
        """P1-2 : un lead exclu (refus Richard / opt-out / bounce) survenu apres
        l'approbation ne doit PAS recevoir l'envoi — il est rejete au lieu de partir."""
        from django.utils import timezone

        from crm.models import Lead

        if value == "now":
            value = timezone.now()
        lead = Lead.objects.create(
            linkedin_url=f"https://www.linkedin.com/in/excl-{field}/",
            public_identifier=f"excl-{field}",
            **{field: value},
        )
        po = PendingOutbound.objects.create(
            prospect_public_id=lead.public_identifier,
            kind=OutboundKind.INVITATION,
            ai_draft="x",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now(),
        )
        session = MagicMock()

        result = send_one(session, po)

        assert result is False
        po.refresh_from_db()
        assert po.status == OutboundStatus.REJECTED
        assert motif in po.rejection_reason

    def test_send_invitation_succes(self):
        """Si la fonction originale renvoie PENDING, on marque SENT."""
        from django.utils import timezone
        po = PendingOutbound.objects.create(
            prospect_public_id="test-invit-ok",
            prospect_urn="urn:li:test",
            kind=OutboundKind.INVITATION,
            ai_draft="invit",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now(),
        )
        session = MagicMock()

        from linkedin.enums import ProfileState
        mock_original = MagicMock(return_value=ProfileState.PENDING)

        with patch(
            "ekoalu.outbound_validation.sender.get_original_send_connection_request",
            return_value=mock_original,
        ), patch("ekoalu.outbound_validation.sender.visit_profile" if False else
                 "linkedin.actions.search.visit_profile", return_value=None):
            result = send_one(session, po)

        assert result is True
        po.refresh_from_db()
        assert po.status == OutboundStatus.SENT
        assert po.sent_at is not None

    def test_send_message_succes(self):
        from django.utils import timezone
        po = PendingOutbound.objects.create(
            prospect_public_id="test-msg-ok",
            prospect_urn="urn:li:test",
            kind=OutboundKind.FOLLOW_UP,
            ai_draft="hello",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now(),
        )
        session = MagicMock()

        mock_original = MagicMock(return_value=True)
        with patch(
            "ekoalu.outbound_validation.sender.get_original_send_raw_message",
            return_value=mock_original,
        ):
            result = send_one(session, po)

        assert result is True
        po.refresh_from_db()
        assert po.status == OutboundStatus.SENT

    def test_send_invitation_echec(self):
        """Si la fonction originale renvoie autre chose que PENDING, FAILED."""
        from django.utils import timezone
        po = PendingOutbound.objects.create(
            prospect_public_id="test-invit-fail",
            kind=OutboundKind.INVITATION,
            ai_draft="invit",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now(),
        )
        session = MagicMock()

        from linkedin.enums import ProfileState
        mock_original = MagicMock(return_value=ProfileState.QUALIFIED)

        with patch(
            "ekoalu.outbound_validation.sender.get_original_send_connection_request",
            return_value=mock_original,
        ), patch("linkedin.actions.search.visit_profile", return_value=None):
            result = send_one(session, po)

        assert result is False
        po.refresh_from_db()
        assert po.status == OutboundStatus.FAILED
        assert "unexpected state" in po.error_message

    def test_send_message_echec_si_returns_false(self):
        from django.utils import timezone
        po = PendingOutbound.objects.create(
            prospect_public_id="test-msg-fail",
            kind=OutboundKind.FOLLOW_UP,
            ai_draft="hello",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now(),
        )
        session = MagicMock()

        mock_original = MagicMock(return_value=False)
        with patch(
            "ekoalu.outbound_validation.sender.get_original_send_raw_message",
            return_value=mock_original,
        ):
            result = send_one(session, po)

        assert result is False
        po.refresh_from_db()
        assert po.status == OutboundStatus.FAILED

    def test_exception_dans_envoi_marque_failed(self):
        from django.utils import timezone
        po = PendingOutbound.objects.create(
            prospect_public_id="test-exc",
            kind=OutboundKind.FOLLOW_UP,
            ai_draft="hello",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now(),
        )
        session = MagicMock()

        mock_original = MagicMock(side_effect=RuntimeError("simulated network error"))
        with patch(
            "ekoalu.outbound_validation.sender.get_original_send_raw_message",
            return_value=mock_original,
        ):
            result = send_one(session, po)

        assert result is False
        po.refresh_from_db()
        assert po.status == OutboundStatus.FAILED
        assert "simulated network error" in po.error_message


@pytest.mark.django_db
class TestProcessApprovedQueue:
    def test_skip_si_hors_plage_active(self, monkeypatch):
        """Si on est hors plage horaire EKOALU, rien n est envoyé."""
        from django.utils import timezone
        # Crée 3 approuvés
        for i in range(3):
            PendingOutbound.objects.create(
                prospect_public_id=f"test-skip-{i}",
                kind=OutboundKind.INVITATION,
                ai_draft="x",
                status=OutboundStatus.APPROVED,
                approved_at=timezone.now(),
            )

        # Force is_action_allowed_now à False
        with patch(
            "ekoalu.outbound_validation.sender.is_action_allowed_now",
            return_value=False,
        ):
            stats = process_approved_queue(session=MagicMock(), dry_run=True)

        assert stats["processed"] == 0
        assert stats["sent"] == 0
        assert stats["skipped"] >= 3

    def test_dry_run_ne_change_rien(self):
        from django.utils import timezone
        po = PendingOutbound.objects.create(
            prospect_public_id="test-dryrun",
            kind=OutboundKind.INVITATION,
            ai_draft="invit",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now(),
        )

        with patch(
            "ekoalu.outbound_validation.sender.is_action_allowed_now",
            return_value=True,
        ):
            stats = process_approved_queue(
                session=MagicMock(), dry_run=True, max_messages=10,
            )

        assert stats["processed"] == 1
        assert stats["sent"] == 0
        po.refresh_from_db()
        assert po.status == OutboundStatus.APPROVED  # inchangé

    def test_respect_max_messages(self):
        """Si on a 5 approved mais max=2, on n en process que 2."""
        from django.utils import timezone
        for i in range(5):
            PendingOutbound.objects.create(
                prospect_public_id=f"test-max-{i}",
                kind=OutboundKind.INVITATION,
                ai_draft="x",
                status=OutboundStatus.APPROVED,
                approved_at=timezone.now(),
            )

        with patch(
            "ekoalu.outbound_validation.sender.is_action_allowed_now",
            return_value=True,
        ):
            stats = process_approved_queue(
                session=MagicMock(), max_messages=2, dry_run=True,
            )

        assert stats["processed"] == 2

    def test_file_linkedin_ignore_les_kinds_email(self):
        """Les email_* sont envoyes par email_canal (Graph), JAMAIS par ce
        sender LinkedIn — il les marquait FAILED 'unknown kind: email_cold'
        (bug du 10-11/06, 2 mails valides par Richard perdus)."""
        from django.utils import timezone
        po_mail = PendingOutbound.objects.create(
            prospect_public_id="bdd-prospect-123",
            kind=OutboundKind.EMAIL_COLD,
            subject="Sujet",
            ai_draft="corps",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now() - dt.timedelta(hours=2),
        )
        po_invit = PendingOutbound.objects.create(
            prospect_public_id="test-invit",
            kind=OutboundKind.INVITATION,
            ai_draft="x",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now(),
        )

        with patch(
            "ekoalu.outbound_validation.sender.is_action_allowed_now",
            return_value=True,
        ), patch(
            "ekoalu.outbound_validation.sender.send_one", return_value=True,
        ) as mock_send:
            stats = process_approved_queue(
                session=MagicMock(), max_messages=10, dry_run=False,
            )

        # Seule l invitation est traitee ; le mail reste APPROVED intact.
        assert stats["processed"] == 1
        sent_pks = [call.args[1].pk for call in mock_send.call_args_list]
        assert sent_pks == [po_invit.pk]
        po_mail.refresh_from_db()
        assert po_mail.status == OutboundStatus.APPROVED
        assert po_mail.error_message == ""

    def test_cap_journalier_messages_bloque_follow_up_pas_invitations(self, monkeypatch):
        """Au cap DAILY_MESSAGE_CAP (15/j zone sure compte gratuit), les
        follow-up/reply sont bloqués mais les invitations continuent."""
        from django.utils import timezone
        from ekoalu import conf

        monkeypatch.setattr(conf, "DAILY_MESSAGE_CAP", 2)
        # 2 messages déjà envoyés dans les 24h → cap atteint
        for i in range(2):
            PendingOutbound.objects.create(
                prospect_public_id=f"test-msgcap-sent-{i}",
                kind=OutboundKind.FOLLOW_UP,
                ai_draft="x",
                status=OutboundStatus.SENT,
                sent_at=timezone.now() - dt.timedelta(hours=1),
            )
        po_msg = PendingOutbound.objects.create(
            prospect_public_id="test-msgcap-fu",
            kind=OutboundKind.FOLLOW_UP,
            ai_draft="x",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now() - dt.timedelta(hours=1),
        )
        po_invit = PendingOutbound.objects.create(
            prospect_public_id="test-msgcap-invit",
            kind=OutboundKind.INVITATION,
            ai_draft="x",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now(),
        )

        with patch(
            "ekoalu.outbound_validation.sender.is_action_allowed_now",
            return_value=True,
        ), patch(
            "ekoalu.outbound_validation.sender.send_one", return_value=True,
        ) as mock_send:
            stats = process_approved_queue(
                session=MagicMock(), max_messages=10, dry_run=False,
            )

        sent_pks = [call.args[1].pk for call in mock_send.call_args_list]
        assert sent_pks == [po_invit.pk]
        po_msg.refresh_from_db()
        assert po_msg.status == OutboundStatus.APPROVED  # bloqué, pas failed
        assert stats["skipped"] >= 1

    def test_cap_lectures_atteint_follow_up_attendent_invitations_partent(self, monkeypatch):
        """Cap lectures profil atteint : les follow-up (qui LISENT la fiche à
        l'envoi) restent APPROVED au lieu de partir en FAILED ; les invitations
        (page navigateur, non comptée) continuent. Bug constaté 12/06 (PO 402/405)."""
        from django.utils import timezone

        monkeypatch.setattr(
            "ekoalu.read_guard.guard.is_cap_reached", lambda: True,
        )
        po_fu = PendingOutbound.objects.create(
            prospect_public_id="test-readcap-fu",
            kind=OutboundKind.FOLLOW_UP,
            ai_draft="x",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now() - dt.timedelta(hours=1),
        )
        po_invit = PendingOutbound.objects.create(
            prospect_public_id="test-readcap-invit",
            kind=OutboundKind.INVITATION,
            ai_draft="x",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now(),
        )

        with patch(
            "ekoalu.outbound_validation.sender.is_action_allowed_now",
            return_value=True,
        ), patch(
            "ekoalu.outbound_validation.sender.send_one", return_value=True,
        ) as mock_send:
            process_approved_queue(session=MagicMock(), max_messages=10, dry_run=False)

        sent_pks = [call.args[1].pk for call in mock_send.call_args_list]
        assert sent_pks == [po_invit.pk]
        po_fu.refresh_from_db()
        assert po_fu.status == OutboundStatus.APPROVED  # attend minuit, pas FAILED

    def test_sous_le_cap_messages_les_follow_up_partent(self, monkeypatch):
        from django.utils import timezone
        from ekoalu import conf

        monkeypatch.setattr(conf, "DAILY_MESSAGE_CAP", 15)
        po_msg = PendingOutbound.objects.create(
            prospect_public_id="test-msgok-fu",
            kind=OutboundKind.FOLLOW_UP,
            ai_draft="x",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now(),
        )

        with patch(
            "ekoalu.outbound_validation.sender.is_action_allowed_now",
            return_value=True,
        ), patch(
            "ekoalu.outbound_validation.sender.send_one", return_value=True,
        ) as mock_send:
            process_approved_queue(session=MagicMock(), max_messages=10, dry_run=False)

        sent_pks = [call.args[1].pk for call in mock_send.call_args_list]
        assert po_msg.pk in sent_pks
