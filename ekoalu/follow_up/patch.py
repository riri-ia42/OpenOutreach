"""Monkey-patch : override le message produit par le follow-up agent OpenOutreach
pour les campagnes EKOALU.

On garde la decision agent (action / outcome / follow_up_hours) mais on remplace
le texte du message par celui du generateur EKOALU (structure 4-blocs).

Idempotent : ne s'applique qu'une fois.

Kill switch : pour desactiver completement le mecanisme et revenir au generateur
OpenOutreach d'origine, mettre EKOALU_FOLLOW_UP_OVERRIDE_ENABLED=false dans .env
puis redemarrer Django.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_PATCH_APPLIED = False


def _is_override_enabled() -> bool:
    """Kill switch via env var. Default true."""
    return os.environ.get("EKOALU_FOLLOW_UP_OVERRIDE_ENABLED", "true").lower() not in (
        "false", "0", "no", "off",
    )


def _is_ekoalu_campaign(campaign) -> bool:
    name = getattr(campaign, "name", "") or ""
    return name.startswith("EKOALU - ")


def _persona_slug_for_campaign(campaign) -> str:
    """Recupere le slug du persona depuis le nom de la campagne EKOALU."""
    try:
        from ekoalu.personas import PERSONAS
    except Exception:
        return ""
    name = getattr(campaign, "name", "") or ""
    for p in PERSONAS.values():
        if p.label in name:
            return p.slug
    return ""


def _is_first_outgoing_dm(deal) -> bool:
    """Vrai si aucun message sortant n a deja ete envoye dans cette conversation."""
    try:
        from chat.models import ChatMessage
        from django.contrib.contenttypes.models import ContentType
        ct = ContentType.objects.get_for_model(deal.lead.__class__)
        return not ChatMessage.objects.filter(
            content_type=ct,
            object_id=deal.lead_id,
            is_outgoing=True,
        ).exists()
    except Exception:
        return True


def _format_recent_messages(deal, limit: int = 6) -> str:
    """Charge les derniers messages pour les passer au generateur EKOALU."""
    try:
        from chat.models import ChatMessage
        from django.contrib.contenttypes.models import ContentType
        ct = ContentType.objects.get_for_model(deal.lead.__class__)
        msgs = list(
            ChatMessage.objects
            .filter(content_type=ct, object_id=deal.lead_id)
            .order_by("-creation_date", "-pk")[:limit]
        )
    except Exception:
        return ""
    lines = []
    for m in reversed(msgs):
        content = (m.content or "").strip()
        if not content:
            continue
        speaker = "Moi" if m.is_outgoing else "Prospect"
        lines.append(f"{speaker} : {content}")
    return "\n".join(lines)


def apply_ekoalu_follow_up_patch() -> None:
    """Wrap linkedin.agents.follow_up.run_follow_up_agent pour les campagnes EKOALU."""
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    if not _is_override_enabled():
        logger.warning(
            "EKOALU follow_up patch DESACTIVE via EKOALU_FOLLOW_UP_OVERRIDE_ENABLED=false. "
            "Comportement OpenOutreach d'origine actif.",
        )
        _PATCH_APPLIED = True
        return

    try:
        from linkedin.agents import follow_up as fu_module
    except ImportError as e:
        logger.warning("Cannot patch ekoalu follow_up (linkedin not importable): %s", e)
        return

    original_run = fu_module.run_follow_up_agent

    def patched_run(session, deal):
        decision = original_run(session, deal)
        campaign = getattr(deal, "campaign", None)
        if not _is_ekoalu_campaign(campaign):
            return decision
        if decision.action != "send_message":
            return decision

        from ekoalu.follow_up.generator import generate_ekoalu_dm
        from ekoalu.follow_up.models import get_or_create_dm_config

        dm_cfg = get_or_create_dm_config(campaign)
        include_booking = (
            dm_cfg.include_booking_in_first_dm and _is_first_outgoing_dm(deal)
        )
        persona_slug = _persona_slug_for_campaign(campaign)
        recent_text = _format_recent_messages(deal)
        ekoalu_msg = generate_ekoalu_dm(
            public_id=deal.lead.public_identifier,
            profile_summary=deal.profile_summary,
            chat_summary=deal.chat_summary,
            recent_messages_text=recent_text,
            persona_slug=persona_slug,
            include_booking=include_booking,
        )
        if ekoalu_msg:
            decision.message = ekoalu_msg
            logger.info(
                "EKOALU follow_up override : message remplace pour %s (booking=%s)",
                deal.lead.public_identifier, include_booking,
            )
        else:
            logger.warning(
                "EKOALU follow_up override SKIP : generateur retour vide pour %s",
                deal.lead.public_identifier,
            )
        return decision

    fu_module.run_follow_up_agent = patched_run
    _PATCH_APPLIED = True
    logger.info("EKOALU follow_up patch appliquee")
