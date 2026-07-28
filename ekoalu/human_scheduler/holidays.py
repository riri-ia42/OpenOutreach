"""Jours fériés français — aucune action de prospection (demande Richard 28/07).

Une entreprise française ne prospecte pas le 14 juillet : envoyer ce jour-là est
un marqueur de bot aussi net qu'un envoi à 3h du matin. Ce module est donc branché
sur `is_action_allowed_now` pour TOUS les canaux (email ET LinkedIn).

Les 11 jours fériés légaux (art. L3133-1 du Code du travail) : 4 sont mobiles et
dépendent de Pâques, calculée par l'algorithme de Meeus/Jones/Butcher — aucune
dépendance externe, valable pour le calendrier grégorien.

Non couverts volontairement (KISS, EKOALU est en Rhône-Alpes) : les jours fériés
locaux d'Alsace-Moselle (Vendredi saint, 26 décembre) et d'outre-mer.

Kill-switch : `EKOALU_FRENCH_HOLIDAYS=0`.
"""
from __future__ import annotations

import datetime as dt
import os
from functools import lru_cache

# Jours fériés fixes : (mois, jour) -> nom
_FIXED: dict[tuple[int, int], str] = {
    (1, 1): "Jour de l'an",
    (5, 1): "Fête du Travail",
    (5, 8): "Victoire 1945",
    (7, 14): "Fête nationale",
    (8, 15): "Assomption",
    (11, 1): "Toussaint",
    (11, 11): "Armistice 1918",
    (12, 25): "Noël",
}

# Jours fériés mobiles : décalage en jours depuis le dimanche de Pâques
_EASTER_OFFSETS: dict[int, str] = {
    1: "Lundi de Pâques",
    39: "Ascension",
    50: "Lundi de Pentecôte",
}


@lru_cache(maxsize=32)
def easter_sunday(year: int) -> dt.date:
    """Dimanche de Pâques (grégorien) — algorithme de Meeus/Jones/Butcher."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lam = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lam) // 451
    month, day = divmod(h + lam - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


@lru_cache(maxsize=32)
def french_holidays(year: int) -> dict[dt.date, str]:
    """Les 11 jours fériés légaux français d'une année : {date: nom}."""
    days = {dt.date(year, m, d): name for (m, d), name in _FIXED.items()}
    easter = easter_sunday(year)
    for offset, name in _EASTER_OFFSETS.items():
        days[easter + dt.timedelta(days=offset)] = name
    return days


def _enabled() -> bool:
    return os.environ.get("EKOALU_FRENCH_HOLIDAYS", "1") != "0"


def holiday_name(d: dt.date) -> str | None:
    """Nom du jour férié, ou None si `d` est un jour ordinaire."""
    if not _enabled():
        return None
    return french_holidays(d.year).get(d)


def is_french_holiday(d: dt.date) -> bool:
    """True si `d` est un jour férié français (aucune action ce jour-là)."""
    return holiday_name(d) is not None
