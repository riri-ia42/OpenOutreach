"""Client minimal de l'Outlook Gateway (mail-assistant, port 3334) — LECTURE.

Partagé par la relance mail en fil (fiche #250 : retrouver le cold mail
envoyé), le contrôle « déjà en relation » (fiche #251 : recherche par adresse
et domaine) et le rattachement des RDV Bookings (fiche #252 : notifications).

Jeton : env OUTLOOK_GATEWAY_TOKEN, sinon lu dans mail-assistant/.env (même
TSE). Best-effort : un Gateway injoignable renvoie None, jamais d'exception
vers l'appelant — les pipelines continuent sans la vérification.
Aucune écriture ici : les envois passent par graph_mailer (garde
confirmExternalSend côté Gateway pour les tiers).
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

_MIN_INTERVAL_S = 1.0   # une recherche par seconde maximum (fiche #251)
_last_call = 0.0


def gateway_url() -> str:
    return os.environ.get("OUTLOOK_GATEWAY_URL", "http://127.0.0.1:3334").rstrip("/")


def gateway_token() -> str:
    token = os.environ.get("OUTLOOK_GATEWAY_TOKEN", "").strip()
    if token:
        return token
    env_path = Path(__file__).resolve().parents[4] / "mail-assistant" / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*OUTLOOK_GATEWAY_TOKEN\s*=\s*(.+?)\s*$", line)
            if m:
                return m.group(1).strip().strip('"')
    except OSError:
        pass
    return ""


def enabled() -> bool:
    return os.environ.get("EKOALU_OUTLOOK_GATEWAY", "1").lower() in ("1", "true", "yes")


def _throttle() -> None:
    global _last_call
    wait = _MIN_INTERVAL_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _call(method: str, path: str, body: dict | None = None, timeout: float = 20.0) -> Any | None:
    if not enabled():
        return None
    token = gateway_token()
    if not token:
        logger.warning("Outlook Gateway : jeton introuvable, appel %s ignoré", path)
        return None
    _throttle()
    try:
        resp = requests.request(
            method, f"{gateway_url()}/outlook{path}", json=body,
            headers={"Authorization": f"Bearer {token}"}, timeout=timeout,
        )
    except requests.RequestException as exc:
        logger.warning("Outlook Gateway injoignable (%s %s) : %s", method, path, exc)
        return None
    if not resp.ok:
        logger.warning("Outlook Gateway %s %s → HTTP %s", method, path, resp.status_code)
        return None
    try:
        return resp.json() if resp.text else {}
    except ValueError:
        return None


def search_messages(query: str, *, top: int = 25, folder: str | None = None) -> list[dict] | None:
    """Messages (tous dossiers ou `folder`, ex 'sentitems'). None = Gateway KO."""
    body: dict[str, Any] = {"query": query, "top": top}
    if folder:
        body["folder"] = folder
    data = _call("POST", "/search", body)
    if data is None:
        return None
    return list(data.get("messages") or [])


def get_message(message_id: str) -> dict | None:
    data = _call("GET", "/message/" + requests.utils.quote(message_id, safe=""))
    if data is None:
        return None
    return data.get("message") or data


def calendar(top: int = 100) -> list[dict] | None:
    data = _call("GET", f"/calendar?top={int(top)}")
    if data is None:
        return None
    return list(data.get("events") or [])
