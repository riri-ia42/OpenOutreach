"""Arret d'urgence manuel (bouton STOP du dashboard).

Sentinel `data/emergency_stop.flag` pose par Richard via le bouton STOP quand
l'outil dysfonctionne (emballement, comportement anormal, doute). Garde-fou de
derniere ligne, prioritaire sur tout le reste.

Difference avec les autres garde-fous :
- `budget_guard` auto-purge a minuit (nouveau jour = nouveau budget)
- `api_limit_guard` auto-purge a la date de reprise Anthropic
- CE sentinel ne se purge JAMAIS tout seul : il reste actif tant que Richard
  ne clique pas explicitement "Reprendre" dans le dashboard. Un arret d'urgence
  ne doit pas se lever par surprise.

Honore par :
- `linkedin/daemon.py` (boucle principale) : aucune task LinkedIn ni drain de
  la file approved tant que le sentinel existe (le daemon dort et re-check).
- `send_approved_emails` / `send_approved_outbound` /
  `send_approved_email_replies` (commandes cron) : early-return sans envoi.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

SENTINEL_PATH = Path(settings.ROOT_DIR) / "data" / "emergency_stop.flag"


def is_stopped() -> bool:
    """True si l'arret d'urgence est actif (sentinel present)."""
    return SENTINEL_PATH.exists()


def engage(reason: str = "", actor: str = "") -> None:
    """Pose le sentinel : tout le pipeline se met en pause au prochain cycle."""
    payload = {
        "engaged_at_local": datetime.now().isoformat(),
        "engaged_at_utc": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "actor": actor,
    }
    SENTINEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    SENTINEL_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    logger.warning(
        "EMERGENCY STOP engage par %s : %s",
        actor or "(inconnu)", reason or "(pas de raison)",
    )


def release() -> bool:
    """Leve l'arret d'urgence. True si un sentinel a ete supprime."""
    if SENTINEL_PATH.exists():
        SENTINEL_PATH.unlink()
        logger.warning("EMERGENCY STOP leve manuellement")
        return True
    return False


def status() -> dict | None:
    """Metadata du sentinel actif, ou None s'il n'y en a pas."""
    if not SENTINEL_PATH.exists():
        return None
    try:
        return json.loads(SENTINEL_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"engaged_at_local": "", "reason": "(metadata illisible)", "actor": ""}
