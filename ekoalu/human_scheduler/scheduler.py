"""Scheduler EKOALU — décide délais et autorisation d'actions.

L'idée centrale : OpenOutreach voit `compute_human_delay(base_delay)`
et reçoit un délai ajusté qui respecte les fenêtres horaires (plages
actives, pause déjeuner, jours actifs, jours off aléatoires).

NB LOT E : les « pics gaussiens 10h/16h » historiques ont été SUPPRIMÉS
(code mort, jamais branché) — le pacing linéaire du read_guard + les jitters
de délais suffisent, décision CTO KISS 07/07.
"""
from __future__ import annotations

import datetime as dt
import logging
import random

from django.utils import timezone

from ekoalu import conf
from ekoalu.human_scheduler.budget import is_day_off
from ekoalu.human_scheduler.windows import (
    is_active_day,
    is_in_active_window,
    is_in_lunch_break,
    next_active_window_start,
)

logger = logging.getLogger(__name__)

# Date du dernier log « jour off » — pour ne logger qu'au PREMIER refus du jour.
_day_off_logged: dt.date | None = None


def is_action_allowed_now(
    now: dt.datetime | None = None, *, channel: str = "linkedin",
) -> bool:
    """True si on peut exécuter une action MAINTENANT.

    Vérifie :
    - jour off aléatoire (LOT E — canal LinkedIn uniquement : le canal email
      et les commandes manuelles restent actifs, passer channel="email")
    - jour actif (poids > 0)
    - dans une fenêtre horaire active
    - pas en pause déjeuner
    """
    global _day_off_logged
    now = now or timezone.localtime()
    if channel == "linkedin" and is_day_off(now.date()):
        # Ne logger que pour AUJOURD'HUI : la fonction est aussi appelée avec
        # des dates futures par le scan de fenêtre (next_active_window_start).
        today = timezone.localtime().date()
        if now.date() == today and _day_off_logged != today:
            _day_off_logged = today
            logger.info(
                "Jour off aléatoire — aucune action LinkedIn aujourd'hui (%s)",
                today.isoformat(),
            )
        return False
    if not is_active_day(now):
        return False
    if is_in_lunch_break(now):
        return False
    return is_in_active_window(now)


def next_active_slot(now: dt.datetime | None = None) -> dt.datetime:
    """Retourne le datetime du prochain créneau valide."""
    now = now or timezone.localtime()
    if is_action_allowed_now(now):
        return now
    return next_active_window_start(now)


def compute_human_delay(
    base_delay_seconds: float = 0.0,
    now: dt.datetime | None = None,
    rng: random.Random | None = None,
) -> float:
    """Ajuste un délai pour qu'il tombe dans une fenêtre active.

    Si on est dans une fenêtre active : ajoute un délai aléatoire
    MIN_DELAY_SECONDS..MAX_DELAY_SECONDS (sur la base de base_delay).
    Si hors fenêtre : décale jusqu'au prochain créneau actif + délai aléatoire.

    Args:
        base_delay_seconds: délai initial demandé par OpenOutreach
        now: datetime de référence (utile pour les tests)
        rng: générateur aléatoire (utile pour les tests déterministes)

    Returns:
        délai final en secondes (toujours >= base_delay_seconds)
    """
    rng = rng or random
    now = now or timezone.localtime()
    target = now + dt.timedelta(seconds=base_delay_seconds)

    # Calcule combien attendre pour atteindre une fenêtre active à partir de target
    if not is_action_allowed_now(target):
        target = next_active_window_start(target)

    # Ajoute un jitter humain (pour éviter pattern régulier)
    jitter = rng.uniform(conf.MIN_DELAY_SECONDS, conf.MAX_DELAY_SECONDS)

    final_dt = target + dt.timedelta(seconds=jitter)

    # Re-vérifie qu'on n'est pas sorti de la fenêtre (cas où jitter > fin de fenêtre)
    if not is_action_allowed_now(final_dt):
        final_dt = next_active_window_start(final_dt) + dt.timedelta(
            seconds=rng.uniform(conf.MIN_DELAY_SECONDS, conf.MAX_DELAY_SECONDS)
        )

    delta = (final_dt - now).total_seconds()
    # Garantit qu'on respecte au moins le base_delay demandé
    return max(delta, base_delay_seconds)
