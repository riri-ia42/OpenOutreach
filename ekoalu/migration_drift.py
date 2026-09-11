"""Détection du décalage entre le code chargé et le schéma de la base.

Fiche hub du 11/09 : 27 tâches LinkedIn perdues en deux jours sur

    sqlite3.IntegrityError: NOT NULL constraint failed:
    ekoalu_pendingoutbound.graph_message_id

Cause : le daemon (`manage.py rundaemon`) est un process de longue durée. Il
avait démarré le 09/09 à 09h01, la migration 0030 a été appliquée le même jour
à 16h51. Django fige les classes de modèle à l'import : le process continuait
donc d'écrire des INSERT sans la colonne ajoutée depuis, et SQLite les refusait
faute de valeur par défaut au niveau du schéma. Chaque refus faisait perdre une
invitation ou une relance, silencieusement du point de vue de Richard.

Le correctif immédiat est un redémarrage. Le correctif DURABLE est ici : le
daemon compare, à chaque tour, l'horodatage de la dernière migration appliquée
à sa propre heure de démarrage. Si la base a bougé après lui, il s'arrête
proprement — le watchdog le relance avec le code à jour.

On ne pose pas de `DEFAULT` SQL sur la colonne : SQLite impose une
reconstruction de table pour ça, et Django n'utilise de toute façon pas les
valeurs par défaut du schéma. Le décalage est le vrai problème, pas la colonne.
"""
from __future__ import annotations

import datetime as dt
import logging

logger = logging.getLogger(__name__)

ENV_KILL_SWITCH = "EKOALU_MIGRATION_DRIFT_GUARD"


def guard_enabled() -> bool:
    import os

    return os.environ.get(ENV_KILL_SWITCH, "1").lower() not in ("0", "false", "no")


def last_migration_applied_at() -> dt.datetime | None:
    """Horodatage de la dernière migration appliquée, toutes apps confondues."""
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("SELECT MAX(applied) FROM django_migrations")
        brut = cur.fetchone()[0]
    if not brut:
        return None
    if isinstance(brut, dt.datetime):
        return _aware(brut)
    for forme in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return _aware(dt.datetime.strptime(str(brut), forme))
        except ValueError:
            continue
    logger.warning("Horodatage de migration illisible : %r", brut)
    return None


def _aware(valeur: dt.datetime) -> dt.datetime:
    from django.utils import timezone

    if timezone.is_naive(valeur):
        return timezone.make_aware(valeur, dt.timezone.utc)
    return valeur


def drift_detecte(demarrage: dt.datetime) -> dt.datetime | None:
    """Renvoie l'horodatage de la migration fautive, None si le code est à jour.

    `demarrage` est l'heure à laquelle le process a chargé ses modèles.
    """
    if not guard_enabled():
        return None
    applique = last_migration_applied_at()
    if applique is None:
        return None
    return applique if applique > demarrage else None


def signaler(applique: dt.datetime, demarrage: dt.datetime) -> None:
    """Trace vérifiable : log daté + événement hub (la revue de nuit le lit)."""
    from ekoalu.notifications.hub_events import post_event

    message = (
        "Migration appliquée APRÈS le démarrage de ce process "
        f"(migration {applique:%Y-%m-%d %H:%M}, démarrage {demarrage:%Y-%m-%d %H:%M}) : "
        "les modèles chargés en mémoire sont périmés, les écritures peuvent "
        "échouer sur une colonne inconnue. Arrêt pour relance par le watchdog."
    )
    logger.error(message)
    post_event(
        "prospection.migration_drift", "error",
        "Daemon arrêté : son code est antérieur à la dernière migration",
        {
            "status": "drift",
            "migration_appliquee": applique.isoformat(),
            "demarrage_process": demarrage.isoformat(),
            "consequence": "ecritures refusees par la base (NOT NULL sur colonne inconnue)",
            "action": "arret propre, relance par le watchdog avec le code a jour",
        },
    )
