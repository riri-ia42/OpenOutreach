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

# Kinds envoyes par CE sender (navigateur LinkedIn). Les kinds email_* sont
# envoyes par ekoalu/email_canal (commande send_approved_emails via Graph) —
# les ramasser ici produisait des FAILED "unknown kind: email_cold".
LINKEDIN_KINDS = (OutboundKind.INVITATION, OutboundKind.FOLLOW_UP, OutboundKind.REPLY)


def _resolve_profile_dict(po: PendingOutbound) -> dict:
    """Construit le profile dict requis par les fonctions LinkedIn."""
    return {
        "public_identifier": po.prospect_public_id,
        "urn": po.prospect_urn,
        "url": f"https://www.linkedin.com/in/{po.prospect_public_id}/",
    }


def _lead_exclusion_reason(po: PendingOutbound) -> str:
    """Motif d'exclusion du Lead au MOMENT de l'envoi, ou '' s'il est contactable.

    Un opt-out email (unsubscribed_at, RGPD art. 21), un hard bounce
    (email_bounced_at) ou un refus Richard (disqualified) peuvent survenir APRES
    l'approbation d'une invitation/follow-up LinkedIn, sans toujours repasser par
    la cascade qui rejette le PendingOutbound. Sans ce garde-fou, le daemon
    enverrait quand meme (le canal email re-checke deja, pas le canal LinkedIn).
    Cf. revue 17/06 P1-2.
    """
    from crm.models import Lead

    lead = Lead.objects.filter(public_identifier=po.prospect_public_id).first()
    if lead is None:
        return ""
    if lead.disqualified:
        return "disqualified"
    if lead.unsubscribed_at is not None:
        return "unsubscribed"
    if lead.email_bounced_at is not None:
        return "bounced"
    return ""


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
        session.ensure_browser()
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

    # Garde-fou exclusion AU MOMENT de l'envoi (P1-2) : un opt-out / bounce /
    # refus Richard survenu apres l'approbation ne doit jamais partir.
    exclusion = _lead_exclusion_reason(po)
    if exclusion:
        po.status = OutboundStatus.REJECTED
        po.rejection_reason = f"Lead exclu au moment de l'envoi ({exclusion})"
        po.save(update_fields=["status", "rejection_reason"])
        logger.info(
            "Reject PendingOutbound #%s : lead %s exclu (%s)",
            po.pk, po.prospect_public_id, exclusion,
        )
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
            status=OutboundStatus.APPROVED, kind__in=LINKEDIN_KINDS,
        ).count()
        return stats

    # Cap dur : INVITATIONS uniquement (les DM follow-up ne sont pas cappes).
    # On compte sent_at sur 24h et 7 jours glissants pour respecter les
    # WEEKLY_INVITE_TARGET / DAILY_INVITE_CAP / WEEKLY_INVITE_HARD_CAP.
    from datetime import timedelta
    from django.utils import timezone

    now = timezone.localtime()

    # Espacement minimum PERSISTANT entre 2 envois LinkedIn (P1-4). Le daemon
    # draine 1 msg/tour (max_messages=1) → la temporisation inter-envoi de la
    # boucle (i < len-1) ne s'execute jamais : les envois partaient au rythme de
    # la boucle daemon = rafale (signature bot, constat Richard 04/06). On exige
    # >= MIN_DELAY_SECONDS depuis le dernier envoi LinkedIn reel (sent_at).
    last_sent_at = (
        PendingOutbound.objects.filter(
            kind__in=LINKEDIN_KINDS,
            status=OutboundStatus.SENT,
            sent_at__isnull=False,
        )
        .order_by("-sent_at")
        .values_list("sent_at", flat=True)
        .first()
    )
    if last_sent_at is not None:
        elapsed = (now - last_sent_at).total_seconds()
        if elapsed < conf.MIN_DELAY_SECONDS:
            logger.info(
                "Espacement envois LinkedIn : %.0fs depuis le dernier "
                "(< %ds) — on patiente",
                elapsed, conf.MIN_DELAY_SECONDS,
            )
            stats["skipped"] = PendingOutbound.objects.filter(
                status=OutboundStatus.APPROVED, kind__in=LINKEDIN_KINDS,
            ).count()
            return stats

    # LOT E : les caps QUOTIDIENS d'envois sont modules par le poids hebdo
    # (WEEKDAY_WEIGHTS) — avant, le samedi avait le meme cap que le lundi.
    # Ex. samedi (0.2) : 8 invitations/j nominal -> 2 effectives. Dimanche
    # (0.0) -> 0 (de toute facon hors jour actif). Les caps HEBDO restent
    # inchanges (ils lissent deja la semaine).
    from ekoalu.human_scheduler import budget
    day_weight = budget.daily_weight_factor(now.date())
    daily_invite_cap = max(0, round(conf.DAILY_INVITE_CAP * day_weight))
    daily_message_cap = max(0, round(conf.DAILY_MESSAGE_CAP * day_weight))

    invites_24h = PendingOutbound.objects.filter(
        kind=OutboundKind.INVITATION,
        status=OutboundStatus.SENT,
        sent_at__gte=now - timedelta(days=1),
    ).count()
    invites_7d = PendingOutbound.objects.filter(
        kind=OutboundKind.INVITATION,
        status=OutboundStatus.SENT,
        sent_at__gte=now - timedelta(days=7),
    ).count()
    if invites_24h >= daily_invite_cap:
        logger.info(
            "Cap journalier invitations atteint (%d/%d, poids jour %.1f) "
            "- on attend demain",
            invites_24h, daily_invite_cap, day_weight,
        )
        stats["skipped"] = PendingOutbound.objects.filter(
            status=OutboundStatus.APPROVED,
            kind=OutboundKind.INVITATION,
        ).count()
        invitations_blocked = True
    elif invites_7d >= conf.WEEKLY_INVITE_HARD_CAP:
        logger.warning(
            "Cap hebdo HARD atteint (%d/%d) - blocage total invitations",
            invites_7d, conf.WEEKLY_INVITE_HARD_CAP,
        )
        stats["skipped"] = PendingOutbound.objects.filter(
            status=OutboundStatus.APPROVED,
            kind=OutboundKind.INVITATION,
        ).count()
        invitations_blocked = True
    elif invites_7d >= conf.WEEKLY_INVITE_TARGET:
        logger.info(
            "Cible hebdo invitations atteinte (%d/%d) - on attend la rotation",
            invites_7d, conf.WEEKLY_INVITE_TARGET,
        )
        stats["skipped"] = PendingOutbound.objects.filter(
            status=OutboundStatus.APPROVED,
            kind=OutboundKind.INVITATION,
        ).count()
        invitations_blocked = True
    else:
        invitations_blocked = False

    # Cap journalier MESSAGES (follow-up + reply) — benchmark 2026 compte
    # gratuit : ~100/sem soit 15-20/j en zone sure. Cale via
    # EKOALU_DAILY_MESSAGE_CAP (conf.DAILY_MESSAGE_CAP).
    messages_24h = PendingOutbound.objects.filter(
        kind__in=(OutboundKind.FOLLOW_UP, OutboundKind.REPLY),
        status=OutboundStatus.SENT,
        sent_at__gte=now - timedelta(days=1),
    ).count()
    messages_blocked = messages_24h >= daily_message_cap
    if messages_blocked:
        logger.info(
            "Cap journalier messages atteint (%d/%d, poids jour %.1f) "
            "- on attend demain",
            messages_24h, daily_message_cap, day_weight,
        )
        stats["skipped"] += PendingOutbound.objects.filter(
            status=OutboundStatus.APPROVED,
            kind__in=(OutboundKind.FOLLOW_UP, OutboundKind.REPLY),
        ).count()

    # Cap LECTURES de profil atteint : l'envoi d'un follow-up/reply LIT la
    # fiche (materialize_profile_summary) -> sans cette garde ils partaient en
    # FAILED sur ReadCapExceededError (constat Richard 12/06, PO 402/405). On
    # les laisse APPROVED jusqu'au reset de minuit. Les invitations, elles,
    # passent par la page navigateur (pas l'API Voyager comptée) -> non bloquées.
    from ekoalu.read_guard.guard import is_cap_reached
    reads_blocked = is_cap_reached()
    if reads_blocked and not messages_blocked:
        logger.info(
            "Cap lectures profil atteint - follow-up/reply attendent minuit "
            "(l'envoi lit la fiche), les invitations continuent",
        )
        stats["skipped"] += PendingOutbound.objects.filter(
            status=OutboundStatus.APPROVED,
            kind__in=(OutboundKind.FOLLOW_UP, OutboundKind.REPLY),
        ).count()

    blocked_kinds = []
    if invitations_blocked:
        blocked_kinds.append(OutboundKind.INVITATION)
    if messages_blocked or reads_blocked:
        blocked_kinds.extend([OutboundKind.FOLLOW_UP, OutboundKind.REPLY])

    approved = PendingOutbound.objects.filter(
        status=OutboundStatus.APPROVED, kind__in=LINKEDIN_KINDS,
    ).exclude(kind__in=blocked_kinds).order_by("approved_at")[:max_messages]

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
