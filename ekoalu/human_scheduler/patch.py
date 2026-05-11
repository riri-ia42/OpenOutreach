"""Monkey-patch sur linkedin/tasks/scheduler.py:_insert_task.

Intercepte la création de Tasks OpenOutreach pour appliquer notre logique
humaine sur le `delay_seconds`. Ne touche pas à la signature ou au reste
du comportement OpenOutreach — c'est volontairement minimal et réversible.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_PATCH_APPLIED = False


def apply_human_scheduler_patch() -> None:
    """Wrap `_insert_task` pour ajuster `delay_seconds` selon conf EKOALU.

    Idempotent : ne s'applique qu'une fois (flag _PATCH_APPLIED).
    """
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return

    try:
        from linkedin.tasks import scheduler as openoutreach_scheduler
    except ImportError as e:
        logger.warning("Cannot patch human_scheduler (linkedin.tasks not importable): %s", e)
        return

    original_insert_task = openoutreach_scheduler._insert_task

    def patched_insert_task(task_type, payload, delay_seconds, dedup_keys=None):
        from ekoalu.human_scheduler.scheduler import compute_human_delay

        adjusted_delay = compute_human_delay(base_delay_seconds=delay_seconds)
        if adjusted_delay != delay_seconds:
            logger.debug(
                "EKOALU scheduler: task=%s base_delay=%.1fs adjusted=%.1fs",
                task_type, delay_seconds, adjusted_delay,
            )
        return original_insert_task(
            task_type=task_type,
            payload=payload,
            delay_seconds=adjusted_delay,
            dedup_keys=dedup_keys,
        )

    openoutreach_scheduler._insert_task = patched_insert_task
    _PATCH_APPLIED = True
    logger.info("EKOALU human_scheduler patch applied on linkedin._insert_task")
