"""Budgets quotidiens humanisés (LOT E) — poids hebdo appliqué en FACTEUR.

Avant le LOT E, conf.WEEKDAY_WEIGHTS n'était consommé que comme booléen
jour actif/inactif : le compte saturait donc son cap de lectures TOUS les
jours au même niveau, y compris le samedi à 100 % — signature régulière,
exactement ce que le checkpoint du 06/06 a sanctionné.

Empilement du cap effectif d'un jour :

    cap effectif = cap nominal (env/ramp) x poids hebdo (WEEKDAY_WEIGHTS)

Consommé par ekoalu/read_guard/guard.py (cap lectures) et
ekoalu/outbound_validation/sender.py (caps d'envois quotidiens).
Pas d'I/O ni de DB — purement calcul, testable sans Django.
"""
from __future__ import annotations

import datetime as dt

from ekoalu import conf


def _today() -> dt.date:
    return dt.date.today()


def daily_weight_factor(d: dt.date | None = None) -> float:
    """Poids du jour de semaine (conf.WEEKDAY_WEIGHTS), facteur 0.0-1.0.

    Lu/Ma/Je 1.0, Me 0.9, Ve 0.7, Sa 0.2, Di 0.0 — appliqué en facteur sur
    les caps quotidiens (lectures + invitations + messages).
    """
    d = d or _today()
    return conf.WEEKDAY_WEIGHTS.get(d.weekday(), 0.0)
