"""Logique des fenêtres temporelles humaines.

Pas d'I/O, pas de DB — purement calcul sur datetime/heure.
Permet de tester sans contexte Django.
"""
from __future__ import annotations

import datetime as dt

from ekoalu import conf


def is_in_active_window(now: dt.datetime) -> bool:
    """True si `now` tombe dans une des plages actives configurées."""
    hour_float = now.hour + now.minute / 60.0
    for start, end in conf.ACTIVE_WINDOWS:
        if start <= hour_float < end:
            return True
    return False


def weekday_weight(now: dt.datetime) -> float:
    """Coefficient 0.0-1.0 selon le jour de la semaine (0=lundi)."""
    return conf.WEEKDAY_WEIGHTS.get(now.weekday(), 0.0)


def is_active_day(now: dt.datetime) -> bool:
    """True si le jour a un poids > 0."""
    return weekday_weight(now) > 0.0


def next_active_window_start(now: dt.datetime) -> dt.datetime:
    """Retourne le datetime du début de la prochaine fenêtre active.

    Itère jour par jour si nécessaire (max 8 jours pour éviter boucle infinie).
    """
    for day_offset in range(8):
        candidate_day = now + dt.timedelta(days=day_offset)

        if not _is_day_active(candidate_day):
            continue

        # Trouver la première fenêtre du jour qui n'est pas déjà passée
        sorted_windows = sorted(conf.ACTIVE_WINDOWS, key=lambda w: w[0])
        for start_h, _end_h in sorted_windows:
            start_dt = candidate_day.replace(
                hour=int(start_h),
                minute=int((start_h - int(start_h)) * 60),
                second=0,
                microsecond=0,
            )
            # Si on est dans le même jour et que cette fenêtre est passée, skip
            if day_offset == 0 and start_dt <= now:
                continue
            return start_dt

    raise RuntimeError("No active window found in next 8 days — config error?")


def _is_day_active(d: dt.datetime) -> bool:
    return conf.WEEKDAY_WEIGHTS.get(d.weekday(), 0.0) > 0.0


def is_in_lunch_break(now: dt.datetime) -> bool:
    """True si on est dans la pause déjeuner (entre 1ère et 2ème fenêtre).

    Suppose qu'il y a exactement 2 fenêtres consécutives matin/après-midi.
    """
    if len(conf.ACTIVE_WINDOWS) < 2:
        return False
    morning_end = conf.ACTIVE_WINDOWS[0][1]
    afternoon_start = conf.ACTIVE_WINDOWS[1][0]
    hour_float = now.hour + now.minute / 60.0
    return morning_end <= hour_float < afternoon_start
