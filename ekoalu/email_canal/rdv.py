"""Rattachement des rendez-vous Bookings aux prospects (fiche hub #252).

Chaque réservation Bookings envoie à Richard une notification « Nouvelle
réservation <Nom> société <X> pour <Service> » dont le corps porte l'adresse
mail du prospect, la date et l'heure. Le Gateway n'expose pas les participants
des événements d'agenda : ces notifications sont la source fiable.
Une annulation arrive de la même boîte avec « annul » dans l'objet.

`sync_rdv` : lit les notifications (lecture seule), rapproche le prospect par
adresse puis par domaine nominatif, écrit `ProspectRdv` de façon idempotente
(clé = id du message), déduit l'état : planifié → tenu une fois la date passée,
annulé si une annulation est reçue. Limite assumée : un RDV créé à la main sans
l'adresse du prospect n'est pas rapproché (ligne « sans prospect » au récap).
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass

from django.utils import timezone

from ekoalu.email_canal.relation_check import BOOKINGS_SENDER, is_company_domain

logger = logging.getLogger(__name__)

_MONTHS = {"janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
           "juillet": 7, "août": 8, "aout": 8, "septembre": 9, "octobre": 10,
           "novembre": 11, "décembre": 12, "decembre": 12}
_DATE_RE = re.compile(r"(\d{1,2})\s+([a-zéû]+)\s+(\d{4})", re.IGNORECASE)
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_TITLE_RE = re.compile(r"réservation\s+(.+?)\s+pour\s+(.+)$", re.IGNORECASE)


@dataclass
class BookingNotice:
    message_id: str
    subject: str
    received_at: str
    email: str
    who: str
    service: str
    start: dt.datetime | None
    end: dt.datetime | None
    cancelled: bool


def _strip_html(html: str) -> str:
    text = re.sub(r"<style[\s\S]*?</style>", " ", html or "")
    text = re.sub(r"<[^>]+>", "\n", text)
    return re.sub(r"[ \t]+", " ", text)


def parse_notice(msg: dict, body_text: str) -> BookingNotice | None:
    subject = msg.get("subject") or ""
    m = _TITLE_RE.search(subject)
    who, service = (m.group(1).strip(), m.group(2).strip()) if m else ("", "")
    emails = [e.lower() for e in _EMAIL_RE.findall(body_text) if not e.lower().endswith("@ekoalu.com")]
    email = emails[0] if emails else ""
    start = end = None
    dm = _DATE_RE.search(body_text)
    tm = _TIME_RE.search(body_text)
    if dm and dm.group(2).lower() in _MONTHS:
        day = dt.date(int(dm.group(3)), _MONTHS[dm.group(2).lower()], int(dm.group(1)))
        tz = timezone.get_current_timezone()
        if tm:
            start = timezone.make_aware(dt.datetime.combine(day, dt.time(int(tm.group(1)), int(tm.group(2)))), tz)
            end = timezone.make_aware(dt.datetime.combine(day, dt.time(int(tm.group(3)), int(tm.group(4)))), tz)
        else:
            start = timezone.make_aware(dt.datetime.combine(day, dt.time(9, 0)), tz)
    if not email and not who:
        return None
    return BookingNotice(
        message_id=msg.get("id") or "", subject=subject, received_at=msg.get("receivedDateTime") or "",
        email=email, who=who, service=service, start=start, end=end,
        cancelled="annul" in subject.lower(),
    )


_WHO_RE = re.compile(r"^(?P<nom>.+?)(?:\s+(?:société|societe|sté)\s+(?P<societe>.+))?$", re.IGNORECASE)


def match_lead_by_name(who: str):
    """Repli quand la notification ne porte pas l'adresse : « GEAY Lionel société
    SAGE ECO » → dirigeant (nom normalisé) puis raison sociale exacte."""
    from ekoalu.email_canal.models import EmailLeadData
    from ekoalu.person_identity import norm_person_name

    m = _WHO_RE.match((who or "").strip())
    if not m:
        return None
    name = norm_person_name(m.group("nom") or "")
    company = (m.group("societe") or "").strip()
    if name:
        for data in EmailLeadData.objects.exclude(dirigeant="").select_related("lead"):
            if norm_person_name(data.dirigeant) == name:
                return data.lead
    if company:
        data = EmailLeadData.objects.filter(entreprise__iexact=company).select_related("lead").first()
        if data:
            return data.lead
    return None


def match_lead(email: str, who: str = ""):
    """Lead par adresse exacte, sinon par domaine nominatif (le plus récemment
    cold-mailé), sinon par nom / société de la notification."""
    from crm.models import Lead
    from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

    if not email:
        return match_lead_by_name(who)
    lead = Lead.objects.filter(contact_email__iexact=email).first()
    if lead or not is_company_domain(email):
        return lead
    domain = email.rsplit("@", 1)[-1].lower()
    same = list(Lead.objects.filter(contact_email__iendswith="@" + domain))
    if not same:
        return match_lead_by_name(who)
    last_sent = {
        pid: sent for pid, sent in PendingOutbound.objects.filter(
            prospect_public_id__in=[x.public_identifier for x in same],
            kind=OutboundKind.EMAIL_COLD, status=OutboundStatus.SENT,
        ).values_list("prospect_public_id", "sent_at")
    }
    same.sort(key=lambda x: last_sent.get(x.public_identifier) or timezone.make_aware(dt.datetime(2000, 1, 1)),
              reverse=True)
    return same[0]


def fetch_notices(top: int = 100) -> list[BookingNotice] | None:
    from ekoalu.notifications.outlook_gateway import get_message, search_messages

    msgs = search_messages("réservation", top=top)
    if msgs is None:
        return None
    out: list[BookingNotice] = []
    for m in msgs:
        sender = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "").lower()
        if sender != BOOKINGS_SENDER:
            continue
        full = get_message(m["id"]) or {}
        body = (full.get("body") or {}).get("content") or m.get("bodyPreview") or ""
        notice = parse_notice(m, _strip_html(body))
        if notice:
            out.append(notice)
    return out


def upsert_rdv(notice: BookingNotice):
    """Crée/actualise la ligne ProspectRdv. Renvoie (rdv, created)."""
    from ekoalu.email_canal.inbox_poller import _cold_variant_for
    from ekoalu.email_canal.models import ProspectRdv

    lead = match_lead(notice.email, notice.who)
    if notice.cancelled:
        rdv = ProspectRdv.objects.filter(prospect_email=notice.email).order_by("-start").first() if notice.email else None
        if rdv and rdv.status != ProspectRdv.Status.CANCELLED:
            rdv.status = ProspectRdv.Status.CANCELLED
            rdv.save(update_fields=["status"])
        return rdv, False
    rdv, created = ProspectRdv.objects.get_or_create(
        event_id=notice.message_id,
        defaults={
            "lead": lead, "prospect_email": notice.email, "who": notice.who[:255],
            "service": notice.service[:128], "start": notice.start, "end": notice.end,
            "channel": _channel_for(lead), "cold_variant": _cold_variant_for(lead) if lead else "",
            "matched_by": ("email" if lead and notice.email and (lead.contact_email or "").lower() == notice.email
                           else "domain" if lead and notice.email else "name" if lead else ""),
        },
    )
    if rdv.lead is None and lead is not None:
        rdv.lead, rdv.matched_by = lead, ("email" if notice.email else "name")
        rdv.save(update_fields=["lead", "matched_by"])
    rdv.refresh_status()
    return rdv, created


def _channel_for(lead) -> str:
    if lead is None:
        return ""
    from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

    kinds = set(PendingOutbound.objects.filter(
        prospect_public_id=lead.public_identifier, status=OutboundStatus.SENT,
    ).values_list("kind", flat=True))
    if OutboundKind.EMAIL_COLD in kinds:
        return "email"
    if kinds & {OutboundKind.INVITATION, OutboundKind.FOLLOW_UP}:
        return "linkedin"
    return "autre"
