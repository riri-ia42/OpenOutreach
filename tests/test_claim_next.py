# tests/test_claim_next.py
"""LOT C : priorité par type dans ``Task.objects.claim_next``.

À échéance due égale, follow_up puis check_pending passent avant connect —
un backlog permanent de connect ne doit plus affamer les relances.
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


@pytest.mark.django_db
class TestClaimNextPriorite:
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
