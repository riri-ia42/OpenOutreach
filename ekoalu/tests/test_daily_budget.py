"""Tests LOT E — humanisation des budgets quotidiens (ekoalu/human_scheduler/budget).

Constat audit 07/07 : saturation 150/150 lectures TOUS les jours (samedi
compris a 100 %), poids hebdo jamais appliques en facteur, volume pile au cap
= signature reguliere (le checkpoint du 06/06 a ete cause par les lectures).

NB : le conftest neutralise budget.* par defaut (setattr sur le module) ; ici
on importe les fonctions REELLES directement (liees a l'import du module de
test, avant le monkeypatch), et on re-patche explicitement pour les tests
d'integration guard/sender.
"""
from __future__ import annotations

import datetime as dt

import pytest

# Imports directs = fonctions reelles (le conftest ne patche que l'attribut
# de module, pas ces references locales).
from ekoalu.human_scheduler.budget import daily_weight_factor

SATURDAY = dt.date(2026, 7, 11)   # samedi
MONDAY = dt.date(2026, 7, 6)      # lundi
SUNDAY = dt.date(2026, 7, 12)     # dimanche


class TestDailyWeightFactor:
    def test_samedi_20_pourcent(self):
        assert daily_weight_factor(SATURDAY) == 0.2

    def test_lundi_100_pourcent(self):
        assert daily_weight_factor(MONDAY) == 1.0

    def test_dimanche_zero(self):
        assert daily_weight_factor(SUNDAY) == 0.0

    def test_vendredi_70_pourcent(self):
        assert daily_weight_factor(dt.date(2026, 7, 10)) == 0.7


@pytest.mark.django_db
class TestCapLecturesPondere:
    """Le cap lectures effectif = nominal x poids jour (point 1 audit)."""

    def _force_weight(self, monkeypatch, weight: float):
        from ekoalu.human_scheduler import budget
        monkeypatch.setattr(budget, "daily_weight_factor", lambda d=None: weight)

    def test_samedi_cap_a_20_pourcent_du_nominal(self, monkeypatch):
        monkeypatch.delenv("EKOALU_PROFILE_READS_CAP_RAMP", raising=False)
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "80")
        self._force_weight(monkeypatch, 0.2)
        from ekoalu.read_guard.guard import daily_reads_cap
        assert daily_reads_cap() == 16

    def test_dimanche_cap_zero(self, monkeypatch):
        monkeypatch.delenv("EKOALU_PROFILE_READS_CAP_RAMP", raising=False)
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "80")
        self._force_weight(monkeypatch, 0.0)
        from ekoalu.read_guard.guard import daily_reads_cap, is_cap_reached
        assert daily_reads_cap() == 0
        assert is_cap_reached()

    def test_jour_plein_cap_nominal(self, monkeypatch):
        monkeypatch.delenv("EKOALU_PROFILE_READS_CAP_RAMP", raising=False)
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "80")
        self._force_weight(monkeypatch, 1.0)
        from ekoalu.read_guard.guard import daily_reads_cap
        assert daily_reads_cap() == 80


@pytest.mark.django_db
class TestCapsEnvoisPonderes:
    """Les caps quotidiens invitations/messages sont modules pareil (point 1)."""

    def _seed_sent(self, kind, n: int):
        from datetime import timedelta

        from django.utils import timezone

        from ekoalu.outbound_validation import (
            OutboundKind, OutboundStatus, PendingOutbound,
        )
        sent_at = timezone.now() - timedelta(hours=2)
        PendingOutbound.objects.bulk_create([
            PendingOutbound(
                prospect_public_id=f"sent-{kind}-{i}",
                kind=kind,
                ai_draft="x",
                status=OutboundStatus.SENT,
                sent_at=sent_at,
            )
            for i in range(n)
        ])
        return OutboundKind, OutboundStatus, PendingOutbound

    def _run(self, monkeypatch, weight: float):
        from unittest.mock import MagicMock, patch

        from ekoalu.human_scheduler import budget
        from ekoalu.outbound_validation.sender import process_approved_queue

        monkeypatch.setattr(budget, "daily_weight_factor", lambda d=None: weight)
        with patch(
            "ekoalu.outbound_validation.sender.is_action_allowed_now",
            return_value=True,
        ), patch(
            "ekoalu.read_guard.guard.is_cap_reached", return_value=False,
        ):
            return process_approved_queue(session=MagicMock(), dry_run=True)

    def test_samedi_invitations_bloquees_bien_avant_le_cap_nominal(
        self, monkeypatch,
    ):
        """Cap nominal 8/j, samedi (0.2) -> 2 effectives : 2 envoyees = bloque."""
        from django.utils import timezone

        from ekoalu import conf
        monkeypatch.setattr(conf, "DAILY_INVITE_CAP", 8)
        OutboundKind, OutboundStatus, PendingOutbound = self._seed_sent(
            "invitation", 2,
        )
        appr = PendingOutbound.objects.create(
            prospect_public_id="appr-inv",
            kind=OutboundKind.INVITATION,
            ai_draft="x",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now(),
        )

        stats = self._run(monkeypatch, weight=0.2)

        assert stats["sent"] == 0
        appr.refresh_from_db()
        assert appr.status == OutboundStatus.APPROVED  # pas envoyee

    def test_samedi_messages_bloques_a_20_pourcent(self, monkeypatch):
        """Cap nominal 15/j, samedi (0.2) -> 3 effectifs : 3 envoyes = bloque."""
        from django.utils import timezone

        from ekoalu import conf
        monkeypatch.setattr(conf, "DAILY_MESSAGE_CAP", 15)
        OutboundKind, OutboundStatus, PendingOutbound = self._seed_sent(
            "follow_up", 3,
        )
        appr = PendingOutbound.objects.create(
            prospect_public_id="appr-msg",
            kind=OutboundKind.FOLLOW_UP,
            ai_draft="x",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now(),
        )

        stats = self._run(monkeypatch, weight=0.2)

        assert stats["sent"] == 0
        appr.refresh_from_db()
        assert appr.status == OutboundStatus.APPROVED

    def test_jour_plein_rien_ne_change(self, monkeypatch):
        """Poids 1.0 : 2 invitations envoyees sur cap 8 -> la file continue."""
        from django.utils import timezone

        from ekoalu import conf
        monkeypatch.setattr(conf, "DAILY_INVITE_CAP", 8)
        OutboundKind, OutboundStatus, PendingOutbound = self._seed_sent(
            "invitation", 2,
        )
        PendingOutbound.objects.create(
            prospect_public_id="appr-inv2",
            kind=OutboundKind.INVITATION,
            ai_draft="x",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now(),
        )

        stats = self._run(monkeypatch, weight=1.0)

        assert stats["processed"] == 1  # dry_run : aurait envoye
