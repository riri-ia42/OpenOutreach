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
