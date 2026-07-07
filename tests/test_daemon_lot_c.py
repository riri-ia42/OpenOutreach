# tests/test_daemon_lot_c.py
"""LOT C : reconcile au boot + périodique, laisser-passer follow_up sous pacing.

La boucle infinie de ``run_daemon`` est stoppée en faisant lever une
BaseException (``_Stop``) par ``sleep_with_heartbeat`` : elle échappe au
``except Exception`` générique du daemon et fait sortir proprement du test.
"""
from __future__ import annotations

from contextlib import ExitStack
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from linkedin import daemon as daemon_module
from linkedin.daemon import run_daemon
from linkedin.models import Task


class _Stop(BaseException):
    """Sort de la boucle du daemon (BaseException : pas avalée par le daemon)."""


def _stop_sleep(*_args, **_kwargs):
    raise _Stop


@pytest.fixture(autouse=True)
def _daemon_enabled(monkeypatch):
    monkeypatch.delenv("EKOALU_DAEMON_TASKS_DISABLED", raising=False)


def _daemon_stack(stack: ExitStack, *, pacing_gated: bool = False):
    """Patches communs : pas de navigateur, pas de fichiers sentinelles réels."""
    stack.enter_context(
        patch("linkedin.daemon.sleep_with_heartbeat", side_effect=_stop_sleep))
    stack.enter_context(
        patch("linkedin.daemon.seconds_until_active", return_value=0.0))
    stack.enter_context(
        patch("ekoalu.emergency_stop.is_stopped", return_value=False))
    stack.enter_context(
        patch("ekoalu.llm_usage.api_limit_guard.is_limit_active", return_value=False))
    stack.enter_context(
        patch("ekoalu.outbound_validation.sender.process_approved_queue",
              return_value={}))
    stack.enter_context(
        patch("ekoalu.read_guard.guard.is_cap_reached", return_value=False))
    stack.enter_context(
        patch("ekoalu.read_guard.guard.is_paced_cap_reached",
              return_value=pacing_gated))
    stack.enter_context(patch("ekoalu.auth_watch.reset"))


@pytest.mark.django_db
def test_reconcile_au_boot_avant_la_boucle(fake_session):
    """reconcile() tourne UNE fois au démarrage, avant tout claim de task."""
    with ExitStack() as stack:
        mock_reconcile = stack.enter_context(
            patch("linkedin.tasks.scheduler.reconcile"))
        stack.enter_context(
            patch("linkedin.daemon.sleep_with_heartbeat", side_effect=_stop_sleep))
        # STOP d'urgence actif : la boucle dort dès la 1re itération —
        # le reconcile observé ne peut venir QUE du boot.
        stack.enter_context(
            patch("ekoalu.emergency_stop.is_stopped", return_value=True))

        with pytest.raises(_Stop):
            run_daemon(fake_session)

    mock_reconcile.assert_called_once_with(fake_session)


@pytest.mark.django_db
def test_reconcile_periodique_meme_file_non_vide(fake_session):
    """Avec un backlog permanent (des tasks toujours prêtes), reconcile tourne
    quand même dès que l'intervalle est écoulé (patché à 0 ici)."""
    # 3 tasks dues sur une campagne inexistante : chaque tour de boucle en
    # consomme une (mark_failed) sans toucher au navigateur.
    for i in range(3):
        Task.objects.create(
            task_type=Task.TaskType.CONNECT,
            scheduled_at=timezone.now() - timedelta(minutes=1),
            payload={"campaign_id": 999999, "seq": i},
        )

    with ExitStack() as stack:
        _daemon_stack(stack)
        stack.enter_context(patch("linkedin.daemon.RECONCILE_INTERVAL_SECONDS", 0))
        mock_reconcile = stack.enter_context(
            patch("linkedin.tasks.scheduler.reconcile"))

        with pytest.raises(_Stop):
            run_daemon(fake_session)

    # boot (1) + périodique avant chaque claim (>=3, intervalle 0)
    assert mock_reconcile.call_count >= 4
    assert Task.objects.filter(status=Task.Status.FAILED).count() == 3


@pytest.mark.django_db
def test_task_campagne_supprimee_marquee_failed_sans_crash(fake_session):
    """LOT D (tâche orpheline structurelle, cf. task 18293/campagne 38) : une
    task dont la campagne n'existe plus est marquée FAILED (avec completed_at,
    pour le cap retry) et la boucle continue sans crash."""
    orpheline = Task.objects.create(
        task_type=Task.TaskType.CHECK_PENDING,
        scheduled_at=timezone.now() - timedelta(minutes=1),
        payload={"campaign_id": 424242, "public_id": "fantome", "backoff_hours": 24},
    )

    with ExitStack() as stack:
        _daemon_stack(stack)
        stack.enter_context(patch("linkedin.tasks.scheduler.reconcile"))
        with pytest.raises(_Stop):
            run_daemon(fake_session)

    orpheline.refresh_from_db()
    assert orpheline.status == Task.Status.FAILED
    assert orpheline.completed_at is not None


@pytest.mark.django_db
def test_pacing_gate_laisse_passer_follow_up(fake_session):
    """Quand le pacing lectures gate, les follow_up passent quand même ;
    les connect restent bloqués jusqu'au déblocage du budget."""
    campaign = fake_session.campaign
    due = timezone.now() - timedelta(minutes=1)
    connect_task = Task.objects.create(
        task_type=Task.TaskType.CONNECT,
        scheduled_at=due,
        payload={"campaign_id": campaign.pk},
    )
    follow_task = Task.objects.create(
        task_type=Task.TaskType.FOLLOW_UP,
        scheduled_at=due,
        payload={"campaign_id": campaign.pk, "public_id": "alice"},
    )

    executed: list[int] = []

    def spy_follow_up(task, session, qualifiers):
        executed.append(task.pk)

    with ExitStack() as stack:
        _daemon_stack(stack, pacing_gated=True)
        stack.enter_context(patch("linkedin.tasks.scheduler.reconcile"))
        stack.enter_context(patch.dict(
            daemon_module._HANDLERS, {Task.TaskType.FOLLOW_UP: spy_follow_up}))

        with pytest.raises(_Stop):
            run_daemon(fake_session)

    assert executed == [follow_task.pk]
    follow_task.refresh_from_db()
    assert follow_task.status == Task.Status.COMPLETED
    connect_task.refresh_from_db()
    assert connect_task.status == Task.Status.PENDING  # bloqué par le gate
