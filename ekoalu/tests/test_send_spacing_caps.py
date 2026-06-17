"""Espacement des envois (P1-4) + caps invitations (P1-13) — revue 17/06.

P1-4 : le daemon draine 1 msg/tour → la temporisation inter-envoi de la boucle
ne s'execute jamais. On exige >= MIN_DELAY_SECONDS depuis le dernier envoi reel.
P1-13 : les caps anti-ban DAILY_INVITE_CAP + WEEKLY_INVITE_HARD_CAP n'avaient
aucun test de comportement (seulement la valeur de la constante).
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from ekoalu import conf
from ekoalu.outbound_validation import OutboundKind, OutboundStatus, PendingOutbound


def _seed_sent_invitations(n: int, ago_seconds: float) -> None:
    sent_at = timezone.now() - timedelta(seconds=ago_seconds)
    PendingOutbound.objects.bulk_create([
        PendingOutbound(
            prospect_public_id=f"sent-{int(ago_seconds)}-{i}",
            kind=OutboundKind.INVITATION,
            ai_draft="x",
            status=OutboundStatus.SENT,
            sent_at=sent_at,
        )
        for i in range(n)
    ])


def _approved_invitation() -> PendingOutbound:
    return PendingOutbound.objects.create(
        prospect_public_id="approved-1",
        kind=OutboundKind.INVITATION,
        ai_draft="x",
        status=OutboundStatus.APPROVED,
        approved_at=timezone.now(),
    )


def _run():
    from ekoalu.outbound_validation.sender import process_approved_queue

    with patch(
        "ekoalu.outbound_validation.sender.is_action_allowed_now", return_value=True,
    ), patch(
        "ekoalu.read_guard.guard.is_cap_reached", return_value=False,
    ):
        return process_approved_queue(session=MagicMock(), dry_run=True)


@pytest.mark.django_db
class TestSendSpacing:
    def test_bloque_si_dernier_envoi_trop_recent(self):
        """Un envoi LinkedIn < MIN_DELAY_SECONDS empeche le suivant (anti-rafale)."""
        _seed_sent_invitations(1, ago_seconds=5)  # il y a 5s (< 90s)
        _approved_invitation()

        stats = _run()

        assert stats["processed"] == 0
        assert stats["sent"] == 0
        assert stats["skipped"] >= 1

    def test_ok_si_dernier_envoi_assez_ancien(self):
        """Au-dela de MIN_DELAY_SECONDS, la file reprend."""
        _seed_sent_invitations(1, ago_seconds=conf.MIN_DELAY_SECONDS + 60)
        _approved_invitation()

        stats = _run()

        assert stats["processed"] >= 1  # a depasse le garde-fou d'espacement


@pytest.mark.django_db
class TestInvitationCaps:
    def test_cap_journalier_bloque_les_invitations(self):
        """DAILY_INVITE_CAP invitations envoyees en 24h → plus aucune ne part."""
        # Anciennes de >90s (passe l'espacement) mais < 24h (comptent au cap jour).
        _seed_sent_invitations(conf.DAILY_INVITE_CAP, ago_seconds=3600)
        appr = _approved_invitation()

        stats = _run()

        assert stats["sent"] == 0
        appr.refresh_from_db()
        assert appr.status == OutboundStatus.APPROVED  # pas envoyee

    def test_cap_hebdo_hard_bloque_les_invitations(self):
        """WEEKLY_INVITE_HARD_CAP invitations sur 7j → blocage total (hard cap)."""
        # A 3 jours : hors 24h (cap jour = 0) mais dans 7j (cap hebdo plein).
        _seed_sent_invitations(conf.WEEKLY_INVITE_HARD_CAP, ago_seconds=3 * 86400)
        appr = _approved_invitation()

        stats = _run()

        assert stats["sent"] == 0
        appr.refresh_from_db()
        assert appr.status == OutboundStatus.APPROVED
