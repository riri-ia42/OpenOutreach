# linkedin/tasks/follow_up.py
"""Follow-up task — runs the agentic follow-up for one CONNECTED profile."""
from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone
from termcolor import colored

from linkedin.models import ActionLog

logger = logging.getLogger(__name__)

# Required silence between nudges scales with unanswered count:
# 1 unanswered → 3d, 2 → 6d, 3 → 9d. Skips the LLM call while open.
MIN_DAYS_PER_UNANSWERED = 3


def _build_send_profile(deal) -> dict:
    """Minimal profile dict for ``send_raw_message`` and its fallbacks.

    Populated from the Lead row — all three send strategies (popup,
    direct-thread, API) now navigate by URN so no human-readable name
    is required.
    """
    lead = deal.lead
    return {
        "public_identifier": lead.public_identifier,
        "urn": lead.urn or "",
    }


def _has_pending_validation(public_id: str) -> bool:
    """True si un PendingOutbound FOLLOW_UP non terminal existe pour ce contact.

    Evite la boucle daemon -> agent (Claude) -> patched_send_raw_message
    -> generator (Claude) -> retourne False -> set_state(QUALIFIED) -> reconcile
    -> retour ici, qui faisait ~$10/jour en pertes pures sur les contacts en
    attente Richard (cf. analyse conso 01/06/2026).
    """
    try:
        from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound
        return PendingOutbound.objects.filter(
            prospect_public_id=public_id,
            kind=OutboundKind.FOLLOW_UP,
            status__in=[
                OutboundStatus.PENDING,
                OutboundStatus.APPROVED,
                OutboundStatus.BLOCKED_COMPANY,
            ],
        ).exists()
    except Exception:
        return False


def _too_soon_to_nudge(deal) -> bool:
    """Wait `unanswered_count * MIN_DAYS_PER_UNANSWERED` days between nudges."""
    from chat.models import ChatMessage
    from django.contrib.contenttypes.models import ContentType

    ct = ContentType.objects.get_for_model(type(deal.lead))
    messages = ChatMessage.objects.filter(content_type=ct, object_id=deal.lead_id)

    last = messages.order_by("-creation_date").first()
    if last is None or not last.is_outgoing:
        return False

    last_reply = messages.filter(is_outgoing=False).order_by("-creation_date").first()
    nudges = messages.filter(is_outgoing=True)
    if last_reply:
        nudges = nudges.filter(creation_date__gt=last_reply.creation_date)

    required = timedelta(days=nudges.count() * MIN_DAYS_PER_UNANSWERED)
    return timezone.now() - last.creation_date < required


def handle_follow_up(task, session, qualifiers):
    from crm.models import Deal
    from linkedin.actions.message import send_raw_message
    from linkedin.agents.follow_up import run_follow_up_agent
    from linkedin.db.deals import set_profile_state
    from linkedin.db.summaries import materialize_profile_summary_if_missing
    from linkedin.enums import INTERCEPTED, ProfileState
    from linkedin.tasks.scheduler import enqueue_follow_up

    payload = task.payload
    public_id = payload["public_id"]
    campaign_id = payload["campaign_id"]

    logger.info(
        "[%s] %s %s",
        session.campaign, colored("\u25b6 follow_up", "green", attrs=["bold"]), public_id,
    )

    # Rate limit check
    if not session.linkedin_profile.can_execute(ActionLog.ActionType.FOLLOW_UP):
        enqueue_follow_up(campaign_id, public_id, delay_seconds=3600)
        return

    deal = (
        Deal.objects.filter(lead__public_identifier=public_id, campaign=session.campaign)
        .select_related("lead", "campaign")
        .first()
    )
    if deal is None:
        logger.warning("follow_up: no Deal for %s — skipping", public_id)
        return

    # Defense en profondeur : un lead disqualifie (refus Richard, exclusion
    # permanente) ne doit JAMAIS regenerer de message. Si un Deal est encore
    # actif sur ce lead, on le cloture et on s'arrete (zero appel Claude).
    if getattr(deal.lead, "disqualified", False):
        from crm.models.deal import Outcome
        if deal.state not in (ProfileState.COMPLETED.value, ProfileState.FAILED.value):
            set_profile_state(
                session, public_id, ProfileState.FAILED.value,
                outcome=Outcome.NOT_INTERESTED.value,
                reason="Lead disqualifie (refus) — follow_up annule",
            )
        logger.info("[%s] follow_up %s skip: lead disqualifie", session.campaign, public_id)
        return

    if _too_soon_to_nudge(deal):
        logger.info("[%s] follow_up %s: too soon to nudge — re-enqueuing", session.campaign, public_id)
        enqueue_follow_up(campaign_id, public_id, delay_seconds=24 * 3600)
        return

    # EKOALU : si un PendingOutbound est deja en file de validation pour ce
    # contact, inutile d'appeler agent + generator (~$0.02 par cycle x 30 cycles/
    # jour avant fix). On attend que Richard valide/refuse/supprime via l-UI.
    if _has_pending_validation(public_id):
        logger.info(
            "[%s] follow_up %s skip: PendingOutbound deja en file (attente validation)",
            session.campaign, public_id,
        )
        enqueue_follow_up(campaign_id, public_id, delay_seconds=4 * 3600)
        return

    materialize_profile_summary_if_missing(deal, session)
    decision = run_follow_up_agent(session, deal)

    profile = _build_send_profile(deal)

    if decision.action == "send_message":
        logger.info("[%s] follow_up message for %s: %s", session.campaign, public_id, decision.message)
        sent = send_raw_message(session, profile, decision.message)
        if sent is INTERCEPTED:
            # LOT C : message capturé en file de validation (PendingOutbound) —
            # résultat NORMAL en mode require_approval, PAS un échec d'envoi.
            # Le Deal RESTE CONNECTED ; on repasse dans 4h, où le garde
            # _has_pending_validation court-circuitera l'agent tant que
            # Richard n'a pas statué.
            logger.info(
                "[%s] follow_up %s: message en file de validation — deal inchangé",
                session.campaign, public_id,
            )
            enqueue_follow_up(campaign_id, public_id, delay_seconds=4 * 3600)
            return
        if not sent:
            set_profile_state(session, public_id, ProfileState.QUALIFIED.value)
            logger.warning("follow_up for %s: send failed — moving to QUALIFIED for re-connection", public_id)
            return
        session.linkedin_profile.record_action(
            ActionLog.ActionType.FOLLOW_UP, session.campaign,
        )
        enqueue_follow_up(campaign_id, public_id, delay_seconds=decision.follow_up_hours * 3600)

    elif decision.action == "mark_completed":
        set_profile_state(session, public_id, ProfileState.COMPLETED.value, outcome=decision.outcome)
        logger.info("[%s] follow_up completed for %s: outcome=%s", session.campaign, public_id, decision.outcome)

    elif decision.action == "wait":
        enqueue_follow_up(campaign_id, public_id, delay_seconds=decision.follow_up_hours * 3600)
