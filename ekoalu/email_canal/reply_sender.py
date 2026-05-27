"""Envoi des PendingReply email approuvés via Microsoft Graph reply (threading auto).

Wrapper sur `graph_mailer.send_reply` :
- Choisit `final_sent` si Richard a édité, sinon `ai_draft` brut
- Convertit body texte → HTML (réutilise le helper du cold mail sender)
- Appelle Graph reply (qui gère In-Reply-To / Conversation auto)
- Retourne (success: bool, error_msg: str) sans lever d'exception
"""
from __future__ import annotations

import logging

from ekoalu.email_canal.sender import text_body_to_html
from ekoalu.inbox_assist.models import PendingReply
from ekoalu.notifications.graph_mailer import (
    GraphAuthError,
    GraphConfigError,
    GraphSendError,
    send_reply,
)

logger = logging.getLogger(__name__)


def _resolve_body(pr: PendingReply) -> str:
    """Choisit final_sent (édité Richard) sinon ai_draft (brut Claude)."""
    if pr.final_sent and pr.final_sent.strip():
        return pr.final_sent
    return pr.ai_draft


def send_email_reply(pr: PendingReply) -> tuple[bool, str]:
    """Envoie un PendingReply email. Retourne (success, error).

    Pré-conditions vérifiées :
    - channel == "email"
    - inbound_message_id non vide (sinon impossible de répondre threadé)
    - body (final_sent ou ai_draft) non vide

    NE met PAS à jour le statut PendingReply — c'est la responsabilité de
    l'appelant (le management command), pour rester testable et réutilisable.
    """
    if pr.channel != PendingReply.CHANNEL_EMAIL:
        return False, f"channel non email: {pr.channel}"
    if not pr.inbound_message_id:
        return False, "inbound_message_id vide (impossible de répondre threadé)"

    body_text = _resolve_body(pr)
    if not body_text.strip():
        return False, "body vide (final_sent et ai_draft tous les deux vides)"

    body_html = text_body_to_html(body_text)

    try:
        send_reply(original_message_id=pr.inbound_message_id, body_html=body_html)
    except GraphConfigError as exc:
        logger.error("Graph mal configuré : %s", exc)
        return False, f"graph_config: {exc}"
    except GraphAuthError as exc:
        logger.error("Graph auth failed : %s", exc)
        return False, f"graph_auth: {exc}"
    except GraphSendError as exc:
        logger.error("Graph reply KO pour PR #%s : %s", pr.pk, exc)
        return False, f"graph_send: {exc}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Échec inattendu envoi reply PR #%s", pr.pk)
        return False, f"unexpected: {exc}"

    logger.info("Reply email envoyée (PR #%s, sender=%s)",
                pr.pk, pr.sender_email)
    return True, ""
