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


def send_reply(*, original_message_id: str, body_html: str) -> None:
    """Envoie une réponse threadée à un message existant via Graph reply.

    Graph gère automatiquement les headers In-Reply-To / References / threading
    Conversation Outlook : on a juste à fournir l'ID Graph du message source et
    le corps du commentaire. Les destinataires sont déduits du message original
    (équivalent du bouton "Répondre" dans Outlook).

    Args:
        original_message_id: ID Graph du message auquel on répond
            (= PendingReply.inbound_message_id).
        body_html: corps HTML de la réponse (inclura un footer "On <date>, X wrote:"
            automatique côté Outlook).

    Raises:
        GraphConfigError, GraphAuthError, GraphSendError.
    """
    if not original_message_id:
        raise GraphSendError("original_message_id vide")

    user_email = _required("GRAPH_USER_EMAIL")
    token = _get_access_token()

    payload = {
        "comment": body_html,
        # On peut surcharger le message (subject, toRecipients) si besoin via "message":
        # ici on laisse Graph reprendre l'original et on injecte juste notre comment.
    }

    resp = requests.post(
        f"{GRAPH_BASE}/users/{user_email}/messages/{original_message_id}/reply",
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
            f"{GRAPH_BASE}/users/{user_email}/messages/{original_message_id}/reply",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
    if not resp.ok and resp.status_code != 202:
        raise GraphSendError(f"reply {resp.status_code}: {resp.text[:300]}")
    logger.info("Reply Graph envoyée (msg_id=%s)", original_message_id)


def list_inbox_messages(*, since_iso_utc: str, max_n: int = 50) -> list[dict]:
    """Récupère les messages de la boîte de réception depuis `since_iso_utc`.

    Args:
        since_iso_utc: borne basse au format ISO 8601 UTC, ex "2026-05-27T08:00:00Z".
            Tous les messages avec `receivedDateTime >= since_iso_utc` sont retournés.
        max_n: nombre max de messages à récupérer (cap Graph $top, défaut 50).

    Returns:
        Liste de dicts normalisés : `{id, subject, from_email, from_name,
        received_at, body_text, body_html, is_read}`. Ordre = plus récent d'abord.

    Raises:
        GraphConfigError, GraphAuthError, GraphSendError (mauvais nom mais réutilisé).
    """
    user_email = _required("GRAPH_USER_EMAIL")
    token = _get_access_token()

    # On filtre côté serveur pour limiter le volume. $select pour économiser bande.
    params = {
        "$filter": f"receivedDateTime ge {since_iso_utc}",
        "$orderby": "receivedDateTime desc",
        "$top": str(min(max_n, 100)),
        "$select": "id,subject,from,receivedDateTime,bodyPreview,body,isRead",
    }
    resp = requests.get(
        f"{GRAPH_BASE}/users/{user_email}/mailFolders/Inbox/messages",
        params=params,
        headers={
            "Authorization": f"Bearer {token}",
            "Prefer": "outlook.body-content-type='text'",  # body en text plain
        },
        timeout=30,
    )
    if not resp.ok:
        raise GraphSendError(f"list_inbox_messages {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    messages = []
    for raw in data.get("value", []):
        from_addr = (raw.get("from") or {}).get("emailAddress") or {}
        body = raw.get("body") or {}
        messages.append({
            "id": raw.get("id", ""),
            "subject": raw.get("subject", "") or "",
            "from_email": (from_addr.get("address") or "").lower(),
            "from_name": from_addr.get("name") or "",
            "received_at": raw.get("receivedDateTime", ""),
            "body_text": body.get("content", "") or raw.get("bodyPreview", ""),
            "body_html": "",  # on a forcé content-type=text via Prefer
            "is_read": bool(raw.get("isRead", False)),
        })
    return messages
