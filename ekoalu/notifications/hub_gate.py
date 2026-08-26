"""Interrupteur central des mails de report — hub-ekoalu (REGLES_CTO §14ter).

Le hub (port 3390) porte un interrupteur `mail_suspended_prospection-ia` que
Richard bascule depuis /reglages. Avant chaque mail de REPORT (récap, rapport,
cap informatif), on consulte `GET /api/mail-gate?app=prospection-ia` : si
`suspended=true`, le mail n'est pas envoyé (le contenu vit dans le hub).

Les ALERTES CRITIQUES (STOP LinkedIn, daemon zombie, budget LLM) ne passent
JAMAIS par ce gate — elles doivent toujours atteindre Richard.

Fail-open : hub injoignable ou réponse invalide → on envoie (un mail en trop
vaut mieux qu'un report silencieusement perdu). Cache 60 s pour ne pas marteler
le hub quand plusieurs mails partent d'affilée.
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

APP_KEY = "prospection-ia"
_CACHE_TTL_SECONDS = 60.0

_cache_value: bool | None = None
_cache_at: float = 0.0


def _hub_url() -> str:
    return os.environ.get("EKOALU_HUB_URL", "http://127.0.0.1:3390").rstrip("/")


def _hub_token() -> str:
    """Jeton d'ingestion du hub : env, sinon lu dans hub-ekoalu/.env (même TSE)."""
    token = os.environ.get("EKOALU_HUB_TOKEN", "").strip()
    if token:
        return token
    env_path = Path(__file__).resolve().parents[4] / "hub-ekoalu" / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*HUB_INGEST_TOKEN\s*=\s*(.+?)\s*$", line)
            if m:
                return m.group(1)
    except OSError:
        pass
    return ""


def reports_suspended(*, now: float | None = None) -> bool:
    """True si Richard a suspendu les mails de report de prospection-ia."""
    global _cache_value, _cache_at
    current = time.monotonic() if now is None else now
    if _cache_value is not None and current - _cache_at < _CACHE_TTL_SECONDS:
        return _cache_value

    suspended = False
    token = _hub_token()
    if token:
        try:
            resp = requests.get(
                f"{_hub_url()}/api/mail-gate",
                params={"app": APP_KEY},
                headers={"Authorization": f"Bearer {token}"},
                timeout=4,
            )
            if resp.ok:
                suspended = bool(resp.json().get("suspended", False))
            else:
                logger.warning("mail-gate hub HTTP %s — fail-open (envoi)", resp.status_code)
        except (requests.RequestException, ValueError) as exc:
            logger.warning("mail-gate hub injoignable (%s) — fail-open (envoi)", exc)
    else:
        logger.warning("Jeton hub introuvable — fail-open (envoi)")

    _cache_value = suspended
    _cache_at = current
    return suspended


def reset_cache() -> None:
    """Pour les tests."""
    global _cache_value, _cache_at
    _cache_value = None
    _cache_at = 0.0
