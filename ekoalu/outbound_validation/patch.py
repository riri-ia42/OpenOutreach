"""Monkey-patch d'interception des envois LinkedIn.

Quand approval_mode = require_approval :
- send_connection_request → crée PendingOutbound(INVITATION) au lieu d'envoyer
- send_raw_message → crée PendingOutbound(FOLLOW_UP) au lieu d'envoyer

Le daemon retourne un état "neutre" (QUALIFIED inchangé pour invit, False pour message)
de sorte qu'OpenOutreach pense que l'action a échoué/n'a pas progressé : la queue
de validation devient le seul chemin pour envoyer.

Le sender (outbound_validation.sender) utilise les fonctions originales exposées
ci-dessous pour envoyer les messages approuvés sans repasser par l'interception.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_PATCH_APPLIED = False

# Références vers les fonctions originales OpenOutreach, exposées pour
# le sender. Remplies par apply_outbound_validation_patch().
_original_send_connection_request = None
_original_send_raw_message = None


def get_original_send_connection_request():
    """Retourne la fonction originale send_connection_request (sans patch)."""
    return _original_send_connection_request


def get_original_send_raw_message():
    """Retourne la fonction originale send_raw_message (sans patch)."""
    return _original_send_raw_message


def apply_outbound_validation_patch() -> None:
    """Wrap send_connection_request + send_raw_message pour rediriger vers PendingOutbound.

    Idempotent : ne s applique qu une fois.
    """
    global _PATCH_APPLIED, _original_send_connection_request, _original_send_raw_message
    if _PATCH_APPLIED:
        return

    try:
        from linkedin.actions import connect as connect_module
        from linkedin.actions import message as message_module
        from linkedin.enums import ProfileState
    except ImportError as e:
        logger.warning("Cannot patch outbound_validation (linkedin not importable): %s", e)
        return

    original_send_connection = connect_module.send_connection_request
    original_send_raw_message = message_module.send_raw_message

    # Exposer les originales pour le sender
    _original_send_connection_request = original_send_connection
    _original_send_raw_message = original_send_raw_message

    def patched_send_connection_request(session, profile):
        from ekoalu.outbound_validation.config import is_approval_required
        from ekoalu.outbound_validation.models import OutboundKind, PendingOutbound

        if not is_approval_required():
            return original_send_connection(session, profile)

        # Mode require_approval : on crée PendingOutbound au lieu d'envoyer
        public_id = profile.get("public_identifier", "")
        urn = profile.get("urn", "")
        campaign = getattr(session, "campaign", None)
        campaign_id = getattr(campaign, "pk", None)
        campaign_name = getattr(campaign, "name", "") if campaign else ""

        PendingOutbound.objects.create(
            prospect_public_id=public_id,
            prospect_urn=urn,
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            kind=OutboundKind.INVITATION,
            ai_draft="(Invitation LinkedIn sans note)",
        )
        logger.info(
            "EKOALU: invitation pour %s capturee en file de validation (pas envoyee)",
            public_id,
        )
        # Retourne QUALIFIED pour qu'OpenOutreach ne marque pas comme PENDING
        # (le vrai changement d'état arrivera quand Richard valide via UI)
        return ProfileState.QUALIFIED

    def patched_send_raw_message(session, profile, message):
        from ekoalu.outbound_validation.config import is_approval_required
        from ekoalu.outbound_validation.models import OutboundKind, PendingOutbound

        if not is_approval_required():
            return original_send_raw_message(session, profile, message)

        public_id = profile.get("public_identifier", "")
        urn = profile.get("urn", "")
        campaign = getattr(session, "campaign", None)
        campaign_id = getattr(campaign, "pk", None)
        campaign_name = getattr(campaign, "name", "") if campaign else ""

        PendingOutbound.objects.create(
            prospect_public_id=public_id,
            prospect_urn=urn,
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            kind=OutboundKind.FOLLOW_UP,
            ai_draft=message,
        )
        logger.info(
            "EKOALU: message pour %s capture en file de validation (pas envoye)",
            public_id,
        )
        # Retourne False pour qu'OpenOutreach pense que l'envoi a échoué
        # (retry pas idéal mais évite que l'état avance prématurément)
        return False

    connect_module.send_connection_request = patched_send_connection_request
    message_module.send_raw_message = patched_send_raw_message
    _PATCH_APPLIED = True
    logger.info("EKOALU outbound_validation patch applique (mode require_approval)")
