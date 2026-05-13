"""Sender — envoie les PendingOutbound approuvés via LinkedIn.

Utilise les fonctions originales exposées par patch.py (sans repasser par
l interception). Respecte le scheduler humain entre chaque envoi.
"""
from __future__ import annotations

import logging
import random
import time

from django.utils import timezone

from ekoalu import conf
from ekoalu.human_scheduler import is_action_allowed_now
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound
from ekoalu.outbound_validation.patch import (
    get_original_send_connection_request,
    get_original_send_raw_message,
)

logger = logging.getLogger(__name__)


def _resolve_profile_dict(po: PendingOutbound) -> dict:
    """Construit le profile dict requis par les fonctions LinkedIn."""
    return {
        "public_identifier": po.prospect_public_id,
        "urn": po.prospect_urn,
        "url": f"https://www.linkedin.com/in/{po.prospect_public_id}/",
    }


def _send_invitation(session, po: PendingOutbound) -> tuple[bool, str]:
    """Envoie une invitation. Retourne (success, error_msg)."""
    original = get_original_send_connection_request()
    if original is None:
        return False, "patch not applied — original function unavailable"

    try:
        from linkedin.actions.search import visit_profile
        # Visite d abord le profil (humanisation)
        visit_profile(session, _resolve_profile_dict(po))
        # Puis envoie l invitation
        result = original(session, _resolve_profile_dict(po))
        # La fonction originale retourne ProfileState — on regarde si PENDING
        from linkedin.enums import ProfileState
        if result == ProfileState.PENDING:
            return True, ""
        return False, f"unexpected state after send: {result}"
    except Exception as e:
        logger.exception("Erreur envoi invitation %s", po.prospect_public_id)
        return False, str(e)


def _send_message(session, po: PendingOutbound) -> tuple[bool, str]:
    """Envoie un message (follow-up ou reply). Retourne (success, error_msg)."""
    original = get_original_send_raw_message()
    if original is None:
        return False, "patch not applied — original function unavailable"

    try:
        sent = original(session, _resolve_profile_dict(po), po.content_to_send)
        if sent:
            return True, ""
        return False, "send_raw_message returned False"
    except Exception as e:
        logger.exception("Erreur envoi message %s", po.prospect_public_id)
        return False, str(e)


def _bind_session_to_po_campaign(session, po: PendingOutbound) -> None:
    """Aligne session.campaign sur la Campaign du PendingOutbound.

    Indispensable pour que set_profile_state cible le bon Deal (un Lead peut
    avoir plusieurs Deals — un par Campaign).
    """
    if not po.campaign_id:
        return
    from linkedin.models import Campaign
    campaign = Campaign.objects.filter(pk=po.campaign_id).first()
    if campaign:
        session.campaign = campaign


def _advance_deal_state(session, po: PendingOutbound) -> None:
    """Pousse le Deal correspondant dans l'état attendu après envoi réussi.

    INVITATION → PENDING (enqueue check_pending task).
    FOLLOW_UP / REPLY → pas de transition (le Deal devrait déjà être CONNECTED).
    """
    if po.kind != OutboundKind.INVITATION:
        return
    try:
        from linkedin.db.deals import set_profile_state
        from linkedin.enums import ProfileState
        set_profile_state(
            session,
            po.prospect_public_id,
            ProfileState.PENDING.value,
            reason="Invitation envoyée via outbound_validation sender",
        )
    except Exception as e:
        logger.warning(
            "Deal state transition failed for %s after invitation send: %s",
            po.prospect_public_id, e,
        )


def send_one(session, po: PendingOutbound) -> bool:
    """Envoie un seul PendingOutbound. Met à jour son statut. Retourne True si envoyé."""
    if po.status != OutboundStatus.APPROVED:
        logger.warning("Skip PendingOutbound %s : status=%s (must be approved)", po.pk, po.status)
        return False

    logger.info(
        "Envoi PendingOutbound #%s : kind=%s prospect=%s",
        po.pk, po.kind, po.prospect_public_id,
    )

    _bind_session_to_po_campaign(session, po)

    if po.kind == OutboundKind.INVITATION:
        success, error = _send_invitation(session, po)
    elif po.kind in (OutboundKind.FOLLOW_UP, OutboundKind.REPLY):
        success, error = _send_message(session, po)
    else:
        success, error = False, f"unknown kind: {po.kind}"

    if success:
        po.status = OutboundStatus.SENT
        po.sent_at = timezone.now()
        po.error_message = ""
        _advance_deal_state(session, po)
    else:
        po.status = OutboundStatus.FAILED
        po.error_message = error[:1000] if error else "unknown error"

    po.save()
    return success


def process_approved_queue(
    session,
    max_messages: int = 5,
    dry_run: bool = False,
) -> dict:
    """Traite la file approved en respectant les contraintes EKOALU.

    Args:
        session: AccountSession LinkedIn active
        max_messages: nombre max à envoyer dans cette passe
        dry_run: si True, n'envoie pas mais log ce qui serait fait

    Returns:
        dict avec stats (processed, sent, failed, skipped)
    """
    stats = {"processed": 0, "sent": 0, "failed": 0, "skipped": 0}

    if not is_action_allowed_now():
        logger.info("Hors plage active EKOALU — aucun envoi")
        stats["skipped"] = PendingOutbound.objects.filter(
            status=OutboundStatus.APPROVED,
        ).count()
        return stats

    approved = PendingOutbound.objects.filter(
        status=OutboundStatus.APPROVED,
    ).order_by("approved_at")[:max_messages]

    for i, po in enumerate(approved):
        stats["processed"] += 1

        if dry_run:
            logger.info(
                "[DRY-RUN] Aurait envoyé : #%s kind=%s prospect=%s len=%d",
                po.pk, po.kind, po.prospect_public_id, len(po.content_to_send),
            )
            continue

        success = send_one(session, po)
        if success:
            stats["sent"] += 1
        else:
            stats["failed"] += 1

        # Délai humanisé entre 2 envois (sauf après le dernier)
        if i < len(approved) - 1:
            delay = random.uniform(conf.MIN_DELAY_SECONDS, conf.MAX_DELAY_SECONDS)
            logger.info("Délai EKOALU avant prochain envoi : %.0fs", delay)
            time.sleep(delay)

    return stats
