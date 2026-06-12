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


def _inline_attachments(images: dict[str, bytes]) -> list[dict]:
    """Convertit {cid: bytes_png} en attachments Graph inline (img src=cid:...)."""
    import base64

    return [
        {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": f"{cid}.png",
            "contentType": "image/png",
            "contentBytes": base64.b64encode(data).decode("ascii"),
            "contentId": cid,
            "isInline": True,
        }
        for cid, data in images.items()
        if data
    ]


def _file_attachments(files: list[tuple[str, str, bytes]]) -> list[dict]:
    """Convertit [(nom, content_type, bytes)] en attachments Graph classiques
    (pièces jointes visibles, pas inline). Limite : ~3 Mo/fichier (la requête
    sendMail JSON est plafonnée à 4 Mo base64 comprise par Graph)."""
    import base64

    return [
        {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": name,
            "contentType": content_type,
            "contentBytes": base64.b64encode(data).decode("ascii"),
        }
        for name, content_type, data in files
        if data
    ]


# Au-dela : sendMail JSON (limite Graph 4 Mo base64 comprise) ne passe plus.
LARGE_ATTACH_THRESHOLD = 2_300_000
# Chunks d'upload : multiple de 320 KiB exige par Graph.
_UPLOAD_CHUNK = 327_680 * 10  # 3,125 Mo


def _send_via_upload_session(
    *, subject, html_body, recipient, user_email, token,
    inline_images, file_attachments,
) -> None:
    """Flux brouillon -> createUploadSession (chunks) -> send.

    Requis pour les PJ > LARGE_ATTACH_THRESHOLD. Le brouillon envoyé part en
    Éléments envoyés comme un sendMail classique.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 1. Brouillon (avec le logo inline, petit, en attachment direct)
    message = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html_body},
        "toRecipients": [{"emailAddress": {"address": recipient}}],
    }
    if inline_images:
        message["attachments"] = _inline_attachments(inline_images)
    resp = requests.post(
        f"{GRAPH_BASE}/users/{user_email}/messages",
        json=message, headers=headers, timeout=30,
    )
    if not resp.ok:
        raise GraphSendError(f"draft {resp.status_code}: {resp.text[:300]}")
    msg_id = resp.json()["id"]

    # 2. Upload des grosses PJ par chunks
    for name, content_type, data in (file_attachments or []):
        sess = requests.post(
            f"{GRAPH_BASE}/users/{user_email}/messages/{msg_id}/attachments/createUploadSession",
            json={"AttachmentItem": {
                "attachmentType": "file",
                "name": name,
                "size": len(data),
                "contentType": content_type,
            }},
            headers=headers, timeout=30,
        )
        if not sess.ok:
            raise GraphSendError(f"uploadSession {sess.status_code}: {sess.text[:300]}")
        upload_url = sess.json()["uploadUrl"]
        for start in range(0, len(data), _UPLOAD_CHUNK):
            end = min(start + _UPLOAD_CHUNK, len(data))
            chunk_resp = requests.put(
                upload_url,
                data=data[start:end],
                headers={
                    "Content-Length": str(end - start),
                    "Content-Range": f"bytes {start}-{end - 1}/{len(data)}",
                },
                timeout=120,
            )
            if chunk_resp.status_code not in (200, 201, 202):
                raise GraphSendError(
                    f"upload chunk {chunk_resp.status_code}: {chunk_resp.text[:300]}",
                )

    # 3. Envoi du brouillon
    resp = requests.post(
        f"{GRAPH_BASE}/users/{user_email}/messages/{msg_id}/send",
        headers=headers, timeout=30,
    )
    if resp.status_code != 202:
        raise GraphSendError(f"draft send {resp.status_code}: {resp.text[:300]}")


def send_mail(
    *,
    subject: str,
    html_body: str,
    text_body: str | None = None,
    to: str | None = None,
    inline_images: dict[str, bytes] | None = None,
    file_attachments: list[tuple[str, str, bytes]] | None = None,
) -> None:
    """Envoie un mail via Graph sendMail.

    Args:
        subject: Sujet
        html_body: Corps HTML
        text_body: (ignoré — Graph rend bien le HTML)
        to: destinataire (défaut richard@ekoalu.com via GRAPH_ALERT_RECIPIENT)
        inline_images: {content_id: bytes_png} embarqués inline — référencés
            dans le HTML via ``<img src="cid:<content_id>">`` (ex : logo signature)
        file_attachments: [(nom_fichier, content_type, bytes)] — pièces jointes
            classiques (ex : guide des solutions PDF sur les cold mails)

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
    # Au-dela de ~2,3 Mo de PJ, la requete sendMail JSON depasse la limite
    # Graph de 4 Mo (base64 x1,37) -> bascule sur le flux brouillon + upload
    # session (chunks, jusqu'a 150 Mo). Ex : guide des solutions 5,5 Mo.
    total_files = sum(len(d) for _, _, d in (file_attachments or []))
    if total_files > LARGE_ATTACH_THRESHOLD:
        _send_via_upload_session(
            subject=subject, html_body=html_body, recipient=recipient,
            user_email=user_email, token=token,
            inline_images=inline_images, file_attachments=file_attachments,
        )
        logger.info("Mail Graph (upload session, PJ %.1f Mo) envoyé à %s — sujet: %s",
                    total_files / 1024 / 1024, recipient, subject[:80])
        return

    attachments = []
    if inline_images:
        attachments += _inline_attachments(inline_images)
    if file_attachments:
        attachments += _file_attachments(file_attachments)
    if attachments:
        payload["message"]["attachments"] = attachments

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


def send_reply(
    *,
    original_message_id: str,
    body_html: str,
    inline_images: dict[str, bytes] | None = None,
) -> None:
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
    if inline_images:
        payload["message"] = {"attachments": _inline_attachments(inline_images)}

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
