"""Scheduler EKOALU — décide délais et autorisation d'actions.

L'idée centrale : OpenOutreach voit `compute_human_delay(base_delay)`
et reçoit un délai ajusté qui respecte les fenêtres horaires et la
distribution gaussienne intra-plage.
"""
from __future__ import annotations

import datetime as dt
import random

from django.utils import timezone

from ekoalu import conf
from ekoalu.human_scheduler.windows import (
    is_active_day,
    is_in_active_window,
    is_in_lunch_break,
    next_active_window_start,
)


def is_action_allowed_now(now: dt.datetime | None = None) -> bool:
    """True si on peut exécuter une action MAINTENANT.

    Vérifie :
    - jour actif (poids > 0)
    - dans une fenêtre horaire active
    - pas en pause déjeuner
    """
    now = now or timezone.localtime()
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


def gaussian_intra_window_delay(
    now: dt.datetime | None = None,
    rng: random.Random | None = None,
) -> float:
    """Retourne un délai en secondes qui amène vers un pic gaussien.

    Si on est avant le pic, le délai cible le pic.
    Si on est après le pic, le délai cible le pic du jour suivant.

    Utilisé pour augmenter la densité d'actions autour de 10h et 16h.
    """
    rng = rng or random
    now = now or timezone.localtime()
    hour_now = now.hour + now.minute / 60.0

    # Choisir le pic le plus proche dans le futur
    candidates = []
    if hour_now < conf.GAUSSIAN_MORNING_MU:
        candidates.append(conf.GAUSSIAN_MORNING_MU)
    if hour_now < conf.GAUSSIAN_AFTERNOON_MU:
        candidates.append(conf.GAUSSIAN_AFTERNOON_MU)
    if not candidates:
        # Demain matin
        candidates.append(24.0 + conf.GAUSSIAN_MORNING_MU)

    target_h = candidates[0]
    # Variation gaussienne autour du pic
    actual_h = rng.gauss(target_h, conf.GAUSSIAN_SIGMA)

    # Clamp dans la journée
    actual_h = max(0.0, min(actual_h, 23.99))

    target_dt = now.replace(hour=int(actual_h), minute=int((actual_h - int(actual_h)) * 60), second=0)
    if target_dt < now:
        target_dt += dt.timedelta(days=1)

    return (target_dt - now).total_seconds()
