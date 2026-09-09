"""Relance mail unique, cinq jours ouvrés après le cold mail (fiche hub #250).

Constat : 557 cold mails depuis juin, aucun prospect n'a jamais reçu un
deuxième message. Règle : UNE relance, pas plus, envoyée dans le fil du cold
mail, seulement si le prospect n'a ni répondu, ni rebondi, ni refusé, ni été
sorti de la prospection. Même circuit de validation Richard, même quota
journalier (plafond FOLLOWUP_SHARE du quota du jour pour que les nouveaux
contacts continuent). Sources qui répondent d'abord, réserve Mailjet en queue.

Consigne Richard (09/09, prime sur la fiche) : la relance couvre un AUTRE
angle que le premier mail — dire qu'EKOALU fait aussi la menuiserie standard
en plus de la sécurité incendie, donc traite un chantier dans sa globalité —
et propose de tester un chiffrage sur un dossier en cours.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from typing import TYPE_CHECKING

from django.utils import timezone

from ekoalu.human_scheduler.holidays import is_french_holiday
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

if TYPE_CHECKING:  # pragma: no cover
    from crm.models import Lead

logger = logging.getLogger(__name__)

FOLLOWUP_AFTER_WORKING_DAYS = 5
FOLLOWUP_SHARE = 0.40          # part max du quota du jour consacrée aux relances
FOLLOWUP_VARIANT = "relance_v1"


def followup_enabled() -> bool:
    return os.environ.get("EKOALU_EMAIL_FOLLOWUP", "1").lower() in ("1", "true", "yes")


def is_working_day(d: dt.date) -> bool:
    return d.weekday() < 5 and not is_french_holiday(d)


def working_days_between(start: dt.date, end: dt.date) -> int:
    """Jours ouvrés strictement après `start` jusqu'à `end` inclus."""
    if end <= start:
        return 0
    n, d = 0, start
    while d < end:
        d += dt.timedelta(days=1)
        if is_working_day(d):
            n += 1
    return n


def followup_quota_for(day: dt.date) -> int:
    from ekoalu.email_canal.quota import cold_mail_quota_for

    return int(cold_mail_quota_for(day) * FOLLOWUP_SHARE)


def followups_generated_on(day: dt.date) -> int:
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(dt.datetime.combine(day, dt.time.min), tz)
    return PendingOutbound.objects.filter(
        kind=OutboundKind.EMAIL_FOLLOW_UP, created_at__gte=start,
        created_at__lt=start + dt.timedelta(days=1),
    ).count()


def _already_followed(public_ids: set[str]) -> set[str]:
    """Tout follow-up existant, quel que soit son statut : jamais plus de 2 mails."""
    return set(
        PendingOutbound.objects
        .filter(kind=OutboundKind.EMAIL_FOLLOW_UP, prospect_public_id__in=public_ids)
        .values_list("prospect_public_id", flat=True)
    )


def _replied(public_ids: set[str]) -> set[str]:
    """Toute réponse (même hors sujet) éteint la relance : le prospect a parlé."""
    from ekoalu.inbox_assist.models import PendingReply

    return set(
        PendingReply.objects
        .filter(channel=PendingReply.CHANNEL_EMAIL, prospect_public_id__in=public_ids)
        .values_list("prospect_public_id", flat=True)
    )


def eligible_followups(today: dt.date | None = None) -> list[tuple[PendingOutbound, "Lead"]]:
    """(cold mail d'origine, lead) éligibles à la relance, triés par rendement."""
    from crm.models import Lead
    from ekoalu.email_canal.yield_score import build_yield_table, rank_key_factory
    from ekoalu.shared_exclusions import excluded_emails
    from ekoalu.sorties.service import is_company_excluded

    today = today or timezone.localtime().date()
    horizon = today - dt.timedelta(days=FOLLOWUP_AFTER_WORKING_DAYS)   # borne large
    cold = list(
        PendingOutbound.objects
        .filter(kind=OutboundKind.EMAIL_COLD, status=OutboundStatus.SENT,
                sent_at__isnull=False, sent_at__lte=timezone.make_aware(
                    dt.datetime.combine(horizon, dt.time.max), timezone.get_current_timezone()))
        .order_by("sent_at")
    )
    if not cold:
        return []
    pids = {po.prospect_public_id for po in cold}
    followed, replied, shared = _already_followed(pids), _replied(pids), excluded_emails()
    leads = {
        lead.public_identifier: lead
        for lead in Lead.objects.filter(public_identifier__in=pids).select_related("email_data")
    }
    out: list[tuple[PendingOutbound, Lead]] = []
    seen: set[str] = set()
    for po in cold:
        pid = po.prospect_public_id
        lead = leads.get(pid)
        if pid in seen or lead is None or pid in followed or pid in replied:
            continue
        if working_days_between(timezone.localtime(po.sent_at).date(), today) < FOLLOWUP_AFTER_WORKING_DAYS:
            continue
        if (lead.disqualified or lead.unsubscribed_at or lead.email_bounced_at
                or not lead.contact_email or lead.contact_email.lower() in shared):
            continue
        data = getattr(lead, "email_data", None)
        if data is not None and data.siren and is_company_excluded(data.siren):
            continue
        seen.add(pid)
        out.append((po, lead))
    key = rank_key_factory(build_yield_table())
    out.sort(key=lambda t: key(t[1]))
    return out
