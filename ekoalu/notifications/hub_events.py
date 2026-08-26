"""Dépôt d'événements et de propositions dans hub-ekoalu (REGLES_CTO §14ter).

Complète hub_gate (qui LIT l'interrupteur mail_suspended) : ici on ÉCRIT dans
le hub — événements du fil et propositions à valider en 1 clic. Best-effort :
un hub down ne casse jamais l'appelant (la conformité doit rendre son verdict
même sans hub).
"""
from __future__ import annotations

import logging

import requests

from ekoalu.notifications.hub_gate import APP_KEY, _hub_token, _hub_url

logger = logging.getLogger(__name__)


def _post(path: str, payload: dict) -> bool:
    token = _hub_token()
    if not token:
        logger.warning("Jeton hub introuvable — %s non déposé", path)
        return False
    try:
        resp = requests.post(
            f"{_hub_url()}{path}",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=6,
        )
        if not resp.ok:
            logger.warning("hub %s → HTTP %s : %s", path, resp.status_code, resp.text[:200])
        return resp.ok
    except requests.RequestException as exc:
        logger.warning("hub injoignable (%s) — %s non déposé", exc, path)
        return False


def post_event(event_type: str, severity: str, title: str, payload: dict | None = None) -> bool:
    """Événement du fil hub. severity : info | warn | error | critical."""
    body: dict = {"app": APP_KEY, "type": event_type, "severity": severity, "title": title[:300]}
    if payload is not None:
        body["payload"] = payload
    return _post("/api/events", body)


def post_proposal(title: str, body_md: str, *, dedup: bool = True) -> bool:
    """Proposition à valider dans l'onglet Améliorations.

    dedup=True : ne dépose pas si une proposition PENDING au même titre existe
    déjà (évite d'empiler la même correction chaque matin NON CONFORME).
    """
    token = _hub_token()
    if dedup and token:
        try:
            resp = requests.get(
                f"{_hub_url()}/api/proposals",
                params={"app": APP_KEY, "status": "pending"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=6,
            )
            if resp.ok:
                titles = [p.get("title", "") for p in resp.json().get("proposals", [])]
                if title[:300] in titles:
                    logger.info("Proposition déjà en attente, non redéposée : %s", title[:80])
                    return True
        except (requests.RequestException, ValueError):
            pass  # dédup best-effort : en cas de doute, on dépose
    return _post(
        "/api/proposals",
        {"source": "app", "targetApp": APP_KEY, "title": title[:300], "bodyMd": body_md},
    )
