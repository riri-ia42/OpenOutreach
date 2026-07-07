# linkedin/tasks/check_pending.py
"""Check pending task — re-checks one PENDING profile's connection status.

The next task (follow_up for CONNECTED, check_pending for still-PENDING)
is enqueued by the scheduler hook fired from set_profile_state. This
handler is responsible only for domain logic: running the status probe
and doubling the check_pending backoff when the profile is still pending.
"""
from __future__ import annotations

import logging

from termcolor import colored

from linkedin.db.deals import get_profile_dict_for_public_id, set_profile_state
from linkedin.enums import ProfileState
from linkedin.exceptions import SkipProfile

logger = logging.getLogger(__name__)


def _bump_backoff(session, public_id: str, current_hours: float) -> float:
    """Double the check_pending backoff on the Deal; return the new value."""
    from crm.models import Deal

    new_backoff = current_hours * 2
    deal = Deal.objects.filter(
        lead__public_identifier=public_id,
        campaign=session.campaign,
    ).first()
    if deal:
        deal.backoff_hours = new_backoff
        deal.save(update_fields=["backoff_hours"])
    return new_backoff


def handle_check_pending(task, session, qualifiers):
    from linkedin.actions.status import get_connection_status

    payload = task.payload
    public_id = payload["public_id"]
    backoff_hours = payload.get("backoff_hours", 24)

    logger.info(
        "[%s] %s %s",
        session.campaign, colored("\u25b6 check_pending", "magenta", attrs=["bold"]), public_id,
    )

    # D\u00e9fense en profondeur (LOT D) : lead disqualifi\u00e9 = no-op \u2014 aucune lecture
    # LinkedIn, cl\u00f4ture du Deal encore actif (miroir de handle_follow_up).
    from crm.models import Lead

    lead = Lead.objects.filter(public_identifier=public_id).first()
    if lead is not None and lead.disqualified:
        from crm.models import Deal
        from crm.models.deal import Outcome

        terminal = (ProfileState.COMPLETED.value, ProfileState.FAILED.value)
        deal = Deal.objects.filter(lead=lead, campaign=session.campaign).first()
        if deal and deal.state not in terminal:
            set_profile_state(
                session, public_id, ProfileState.FAILED.value,
                outcome=Outcome.NOT_INTERESTED.value,
                reason="Lead disqualifie \u2014 check_pending annule",
            )
        logger.info("[%s] check_pending %s skip: lead disqualifie", session.campaign, public_id)
        return

    profile_dict = get_profile_dict_for_public_id(session, public_id)
    if profile_dict is None:
        logger.warning("check_pending: no Deal for %s — skipping", public_id)
        return

    profile = profile_dict.get("profile") or profile_dict

    try:
        new_state = get_connection_status(session, profile)
    except SkipProfile as e:
        logger.warning("Skipping %s: %s", public_id, e)
        set_profile_state(session, public_id, ProfileState.FAILED.value)
        return

    if new_state == ProfileState.PENDING:
        # Still pending — double the backoff before set_profile_state so the
        # scheduler hook picks up the bumped value when enqueueing the next
        # check_pending.
        new_backoff = _bump_backoff(session, public_id, backoff_hours)
        logger.info(
            "%s still pending — backoff %.1fh → %.1fh",
            public_id, backoff_hours, new_backoff,
        )

    set_profile_state(session, public_id, new_state.value)
