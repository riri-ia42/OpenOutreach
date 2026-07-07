"""Budgets quotidiens humanisés (LOT E) — poids hebdo appliqué en FACTEUR.

Avant le LOT E, conf.WEEKDAY_WEIGHTS n'était consommé que comme booléen
jour actif/inactif : le compte saturait donc son cap de lectures TOUS les
jours au même niveau, y compris le samedi à 100 % — signature régulière,
exactement ce que le checkpoint du 06/06 a sanctionné.

Empilement du cap LECTURES effectif d'un jour :

    cap effectif = cap nominal (env/ramp) x poids hebdo (WEEKDAY_WEIGHTS)
                   x jitter journalier (0.85-1.0, déterministe par date)

Le jitter casse la signature « volume pile au cap chaque jour » ; il ne
s'applique qu'aux LECTURES — les caps d'envois quotidiens n'appliquent que
le poids hebdo (volumes déjà petits, l'arrondi mangerait le jitter).

S'y ajoutent les JOURS OFF aléatoires (conf.RANDOM_DAYS_OFF_PER_MONTH) :
1-2 jours ouvrés par mois SANS AUCUNE action LinkedIn (lectures, envois,
visites) — le canal email et les commandes manuelles restent actifs. Tirage
DÉTERMINISTE par mois (seed = hash 'YYYY-MM' + sel env EKOALU_HUMANIZE_SALT) :
stable au restart du daemon, testable. Kill-switch EKOALU_RANDOM_DAYS_OFF=0.

Consommé par ekoalu/read_guard/guard.py (cap lectures),
ekoalu/outbound_validation/sender.py (caps d'envois quotidiens) et
human_scheduler/scheduler.py + windows.py (jours off).
Pas d'I/O ni de DB — purement calcul, testable sans Django.
"""
from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import os
import random

from ekoalu import conf


def _today() -> dt.date:
    return dt.date.today()


def _seeded_rng(label: str) -> random.Random:
    """RNG déterministe : seed = sha256(sel env + label)."""
    salt = os.environ.get("EKOALU_HUMANIZE_SALT", "")
    digest = hashlib.sha256(f"{salt}:{label}".encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


# Bornes du jitter journalier sur le cap lectures (P3 audit : volume pile au
# cap chaque jour = signature régulière).
JITTER_MIN = 0.85
JITTER_MAX = 1.0


def daily_jitter_factor(d: dt.date | None = None) -> float:
    """Facteur aléatoire journalier dans [JITTER_MIN, JITTER_MAX].

    Déterministe par date (seed = hash date + sel env) : stable au restart
    du daemon, pas de flapping du cap en cours de journée.
    """
    d = d or _today()
    return _seeded_rng(f"jitter:{d.isoformat()}").uniform(JITTER_MIN, JITTER_MAX)


def daily_weight_factor(d: dt.date | None = None) -> float:
    """Poids du jour de semaine (conf.WEEKDAY_WEIGHTS), facteur 0.0-1.0.

    Lu/Ma/Je 1.0, Me 0.9, Ve 0.7, Sa 0.2, Di 0.0 — appliqué en facteur sur
    les caps quotidiens (lectures + invitations + messages).
    """
    d = d or _today()
    return conf.WEEKDAY_WEIGHTS.get(d.weekday(), 0.0)


def _days_off_enabled() -> bool:
    """Kill-switch : EKOALU_RANDOM_DAYS_OFF=0 désactive les jours off."""
    return os.environ.get("EKOALU_RANDOM_DAYS_OFF", "1").lower() not in (
        "0", "false", "no",
    )


def days_off_for_month(year: int, month: int) -> tuple[dt.date, ...]:
    """Jours off aléatoires du mois : 1 à RANDOM_DAYS_OFF_PER_MONTH jours
    OUVRÉS (lundi-vendredi), jamais 2 consécutifs, tirage déterministe."""
    rng = _seeded_rng(f"days-off:{year:04d}-{month:02d}")
    last_day = calendar.monthrange(year, month)[1]
    workdays = [
        dt.date(year, month, day)
        for day in range(1, last_day + 1)
        if dt.date(year, month, day).weekday() < 5
    ]
    count = rng.randint(1, max(1, conf.RANDOM_DAYS_OFF_PER_MONTH))
    picked: list[dt.date] = []
    for candidate in rng.sample(workdays, len(workdays)):
        if any(abs((candidate - p).days) <= 1 for p in picked):
            continue  # jamais 2 jours off consécutifs
        picked.append(candidate)
        if len(picked) >= count:
            break
    return tuple(sorted(picked))


def is_day_off(d: dt.date | None = None) -> bool:
    """True si `d` est un jour off aléatoire : AUCUNE action LinkedIn ce
    jour-là (lectures, envois, visites). Le canal email et les commandes
    manuelles restent actifs. Kill-switch : EKOALU_RANDOM_DAYS_OFF=0."""
    if not _days_off_enabled():
        return False
    d = d or _today()
    return d in days_off_for_month(d.year, d.month)
