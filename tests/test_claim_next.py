# tests/test_claim_next.py
"""Priorités de ``Task.objects.claim_next``.

15/07 — quota connect : tant que moins de EKOALU_DAILY_CONNECT_QUOTA connects
ont été servies aujourd'hui, une connect due passe EN PREMIER (la priorité
LOT C sans plancher a affamé les connect du 08 au 13/07 : 0 servie → 0
qualification → 0 invitation). Au-delà du quota, priorité LOT C : à échéance
due égale, follow_up puis check_pending passent avant connect.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from linkedin.models import Task


def _mk(task_type, minutes_ago: float = 5, **payload):
    return Task.objects.create(
        task_type=task_type,
        scheduled_at=timezone.now() - timedelta(minutes=minutes_ago),
        payload=payload or {"campaign_id": 1},
    )


def _mk_done_connect(n: int) -> None:
    """n tasks connect terminées AUJOURD'HUI (consomment le quota)."""
    for i in range(n):
        Task.objects.create(
            task_type=Task.TaskType.CONNECT,
            status=Task.Status.COMPLETED,
            scheduled_at=timezone.now() - timedelta(hours=2),
            completed_at=timezone.now() - timedelta(minutes=30),
            payload={"campaign_id": 100 + i},
        )


@pytest.mark.django_db
class TestQuotaConnect:
    """15/07 : plancher quotidien de connects servies, prioritaire sur LOT C."""

    def test_connect_due_passe_en_premier_sous_le_quota(self):
        connect = _mk(Task.TaskType.CONNECT, campaign_id=1)
        _mk(Task.TaskType.FOLLOW_UP, campaign_id=1, public_id="a")
        _mk(Task.TaskType.CHECK_PENDING, campaign_id=1, public_id="b")

        assert Task.objects.claim_next().pk == connect.pk

    def test_quota_atteint_priorite_lot_c_reprend(self, monkeypatch):
        monkeypatch.setenv("EKOALU_DAILY_CONNECT_QUOTA", "3")
        _mk_done_connect(3)
        _mk(Task.TaskType.CONNECT, campaign_id=1)
        follow = _mk(Task.TaskType.FOLLOW_UP, campaign_id=1, public_id="a")

        assert Task.objects.claim_next().pk == follow.pk

    def test_connects_d_hier_ne_consomment_pas_le_quota(self, monkeypatch):
        monkeypatch.setenv("EKOALU_DAILY_CONNECT_QUOTA", "1")
        Task.objects.create(
            task_type=Task.TaskType.CONNECT,
            status=Task.Status.COMPLETED,
            scheduled_at=timezone.now() - timedelta(days=1, hours=2),
            completed_at=timezone.now() - timedelta(days=1),
            payload={"campaign_id": 9},
        )
        connect = _mk(Task.TaskType.CONNECT, campaign_id=1)
        _mk(Task.TaskType.FOLLOW_UP, campaign_id=1, public_id="a")

        assert Task.objects.claim_next().pk == connect.pk

    def test_quota_zero_desactive_le_plancher(self, monkeypatch):
        monkeypatch.setenv("EKOALU_DAILY_CONNECT_QUOTA", "0")
        _mk(Task.TaskType.CONNECT, campaign_id=1)
        follow = _mk(Task.TaskType.FOLLOW_UP, campaign_id=1, public_id="a")

        assert Task.objects.claim_next().pk == follow.pk

    def test_quota_ignore_sur_claim_restreint(self):
        """Le laisser-passer pacing (task_types=follow_up) ne sert JAMAIS de
        connect, quota ou pas — une connect lit une fiche à coup sûr."""
        _mk(Task.TaskType.CONNECT, campaign_id=1)

        assert Task.objects.claim_next(task_types=(Task.TaskType.FOLLOW_UP,)) is None

    def test_fifo_entre_connects_sous_le_quota(self):
        older = _mk(Task.TaskType.CONNECT, minutes_ago=60, campaign_id=1)
        _mk(Task.TaskType.CONNECT, minutes_ago=5, campaign_id=2)

        assert Task.objects.claim_next().pk == older.pk

    def test_connect_non_due_ne_passe_pas(self):
        Task.objects.create(
            task_type=Task.TaskType.CONNECT,
            scheduled_at=timezone.now() + timedelta(hours=1),
            payload={"campaign_id": 1},
        )
        follow = _mk(Task.TaskType.FOLLOW_UP, campaign_id=1, public_id="a")

        assert Task.objects.claim_next().pk == follow.pk


@pytest.mark.django_db
class TestClaimNextPriorite:
    """Priorité LOT C (quota désactivé ou consommé)."""

    @pytest.fixture(autouse=True)
    def _quota_off(self, monkeypatch):
        monkeypatch.setenv("EKOALU_DAILY_CONNECT_QUOTA", "0")

    def test_follow_up_puis_check_pending_avant_connect(self):
        """À échéance égale : follow_up > check_pending > connect."""
        connect = _mk(Task.TaskType.CONNECT, campaign_id=1)
        follow = _mk(Task.TaskType.FOLLOW_UP, campaign_id=1, public_id="a")
        check = _mk(Task.TaskType.CHECK_PENDING, campaign_id=1, public_id="b")

        assert Task.objects.claim_next().pk == follow.pk
        follow.mark_completed()
        assert Task.objects.claim_next().pk == check.pk
        check.mark_completed()
        assert Task.objects.claim_next().pk == connect.pk

    def test_connect_plus_ancien_ne_monopolise_pas(self):
        """Un connect dû depuis longtemps ne passe PAS avant un follow_up dû."""
        _mk(Task.TaskType.CONNECT, minutes_ago=120, campaign_id=1)
        follow = _mk(Task.TaskType.FOLLOW_UP, minutes_ago=1, campaign_id=1, public_id="a")

        assert Task.objects.claim_next().pk == follow.pk

    def test_fifo_conserve_au_sein_du_meme_type(self):
        older = _mk(Task.TaskType.CONNECT, minutes_ago=60, campaign_id=1)
        _mk(Task.TaskType.CONNECT, minutes_ago=5, campaign_id=2)

        assert Task.objects.claim_next().pk == older.pk

    def test_task_non_due_jamais_claimee(self):
        Task.objects.create(
            task_type=Task.TaskType.FOLLOW_UP,
            scheduled_at=timezone.now() + timedelta(hours=1),
            payload={"campaign_id": 1, "public_id": "a"},
        )
        connect = _mk(Task.TaskType.CONNECT, campaign_id=1)

        # follow_up pas encore due : le connect dû passe
        assert Task.objects.claim_next().pk == connect.pk

    def test_filtre_task_types(self):
        """task_types restreint le claim (laisser-passer follow_up sous pacing)."""
        _mk(Task.TaskType.CONNECT, campaign_id=1)

        assert Task.objects.claim_next(task_types=(Task.TaskType.FOLLOW_UP,)) is None

        follow = _mk(Task.TaskType.FOLLOW_UP, campaign_id=1, public_id="a")
        claimed = Task.objects.claim_next(task_types=(Task.TaskType.FOLLOW_UP,))
        assert claimed.pk == follow.pk
