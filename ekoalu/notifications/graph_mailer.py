"""Envoi de mails via Microsoft Graph API (sendMail).

Utilise le refresh_token flow OAuth2 — credentials partagés avec mail-assistant
(voir .env.production : GRAPH_CLIENT_ID, GRAPH_TENANT_ID, GRAPH_CLIENT_SECRET,
GRAPH_REFRESH_TOKEN, GRAPH_USER_EMAIL).

Une dérogation expresse de Richard permet d'envoyer sans validation préalable
TANT QUE le destinataire unique est richard@ekoalu.com.
"""
from __future__ import annotations

import logging
import os
import threading
import time

import requests

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
DEFAULT_SCOPE = "https://graph.microsoft.com/.default offline_access"

_token_lock = threading.Lock()
_cached_token: str | None = None
_token_expires_at: float = 0.0


class GraphConfigError(RuntimeError):
    """Credentials Graph absentes ou incomplètes."""


class GraphAuthError(RuntimeError):
    """Echec d'auth Graph (token endpoint a renvoyé une erreur)."""


class GraphSendError(RuntimeError):
    """Echec d'envoi sendMail."""


def _required(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise GraphConfigError(f"Variable d'environnement {name} manquante")
    return val


def _get_access_token() -> str:
    """Récupère un access_token Graph (cache 50 min, refresh via refresh_token)."""
    global _cached_token, _token_expires_at
    with _token_lock:
        now = time.monotonic()
        if _cached_token and now < _token_expires_at - 60:
            return _cached_token

        tenant = _required("GRAPH_TENANT_ID")
        client_id = _required("GRAPH_CLIENT_ID")
        client_secret = _required("GRAPH_CLIENT_SECRET")
        refresh_token = _required("GRAPH_REFRESH_TOKEN")

        resp = requests.post(
            TOKEN_URL_TEMPLATE.format(tenant=tenant),
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": DEFAULT_SCOPE,
            },
            timeout=20,
        )
        if not resp.ok:
            raise GraphAuthError(f"Token endpoint {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise GraphAuthError(f"Pas d'access_token dans la réponse: {data}")
        expires_in = int(data.get("expires_in", 3600))
        _cached_token = token
        _token_expires_at = now + expires_in
        return token


def send_mail(
    *,
    subject: str,
    html_body: str,
    text_body: str | None = None,
    to: str | None = None,
) -> None:
    """Envoie un mail via Graph sendMail.

    Args:
        subject: Sujet
        html_body: Corps HTML
        text_body: (ignoré — Graph rend bien le HTML)
        to: destinataire (défaut richard@ekoalu.com via GRAPH_ALERT_RECIPIENT)

    Raises:
        GraphConfigError, GraphAuthError, GraphSendError.
    """
    recipient = (to or os.environ.get("GRAPH_ALERT_RECIPIENT", "richard@ekoalu.com")).strip()
    if not recipient:
        raise GraphConfigError("Destinataire manquant (GRAPH_ALERT_RECIPIENT vide).")

    user_email = _required("GRAPH_USER_EMAIL")
    token = _get_access_token()

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": recipient}}],
        },
        "saveToSentItems": True,
    }

    resp = requests.post(
        f"{GRAPH_BASE}/users/{user_email}/sendMail",
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    if resp.status_code == 401:
        # Token vient peut-être d'expirer — on invalide et on retente une fois
        global _cached_token, _token_expires_at
        with _token_lock:
            _cached_token = None
            _token_expires_at = 0.0
        token = _get_access_token()
        resp = requests.post(
            f"{GRAPH_BASE}/users/{user_email}/sendMail",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
    if not resp.ok and resp.status_code != 202:
        raise GraphSendError(f"sendMail {resp.status_code}: {resp.text[:300]}")
    logger.info("Mail Graph envoyé à %s — sujet: %s", recipient, subject[:80])


def is_configured() -> bool:
    """True si toutes les variables Graph sont présentes."""
    try:
        for name in ("GRAPH_CLIENT_ID", "GRAPH_TENANT_ID", "GRAPH_CLIENT_SECRET",
                     "GRAPH_REFRESH_TOKEN", "GRAPH_USER_EMAIL"):
            _required(name)
        return True
    except GraphConfigError:
        return False
