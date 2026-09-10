"""Orchestration : un RDV → faits → brief + deck → fichiers → créneau de préparation."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

PREP_BEFORE_MINUTES = 30
LOOKAHEAD_DAYS = 21


def public_base_url() -> str:
    return os.environ.get("EKOALU_PUBLIC_BASE_URL", "http://ekoalu-prospection:3210").rstrip("/")


def rdv_dir(rdv) -> Path:
    base = Path(getattr(settings, "BASE_DIR", ".")) / "data" / "rdv"
    return base / str(rdv.pk)


def links_for(rdv) -> dict:
    b = f"{public_base_url()}/ekoalu/rdv/{rdv.pk}"
    return {"brief": f"{b}/brief/", "deck": f"{b}/deck/", "pdf": f"{b}/deck.pdf"}


def find_calendar_event(rdv) -> dict:
    """Événement Bookings correspondant dans l'agenda Graph (même début, non annulé)."""
    from ekoalu.notifications.graph_calendar import booking_details, list_events

    if not rdv.start:
        return {}
    events = list_events(rdv.start - dt.timedelta(hours=2), rdv.start + dt.timedelta(hours=2))
    target = rdv.start.astimezone(dt.timezone.utc).replace(second=0, microsecond=0)
    for ev in events:
        if ev.get("isCancelled"):
            continue
        raw = (ev.get("start") or {}).get("dateTime") or ""
        try:
            start = dt.datetime.fromisoformat(raw[:19]).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        if abs((start - target).total_seconds()) <= 120 and "EKOALUPrisedeRDV".lower() in json.dumps(ev.get("organizer") or {}).lower():
            return booking_details(ev)
    return {}


def prepare_one(rdv, *, create_event: bool = True, dry_run: bool = False) -> dict:
    """Prépare un RDV. Renvoie {"ok": bool, "links": {...}, "reason": str}."""
    from ekoalu.email_canal.models import ProspectRdv
    from ekoalu.rdv_prep.research import collect
    from ekoalu.rdv_prep.render import deck_to_pdf, render_brief, render_deck
    from ekoalu.rdv_prep.writer import write_brief

    details = find_calendar_event(rdv)
    if details.get("email") and not rdv.prospect_email:
        rdv.prospect_email = details["email"]
    if details.get("teams_url"):
        rdv.teams_url = details["teams_url"]
    if details.get("event_id"):
        rdv.calendar_event_id = details["event_id"]
    if rdv.lead_id is None and details.get("email"):
        from ekoalu.email_canal.rdv import match_lead
        lead = match_lead(details["email"], rdv.who)
        if lead is not None:
            rdv.lead, rdv.matched_by = lead, "email"
    facts = collect(rdv, details)
    content = write_brief(facts.as_dict())
    if not content or not content.get("deroule"):
        rdv.prep_status = ProspectRdv.Prep.FAILED
        rdv.save()
        return {"ok": False, "links": {}, "reason": "rédaction Claude vide"}
    links = links_for(rdv)
    brief_html = render_brief(facts.as_dict(), content, deck_url=links["deck"])
    deck_html = render_deck(facts.as_dict(), content.get("deck") or {})
    if dry_run:
        return {"ok": True, "links": links, "reason": "dry-run (rien écrit)", "content": content}
    out = rdv_dir(rdv)
    out.mkdir(parents=True, exist_ok=True)
    (out / "facts.json").write_text(json.dumps(facts.as_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "brief.html").write_text(brief_html, encoding="utf-8")
    (out / "deck.html").write_text(deck_html, encoding="utf-8")
    pdf_ok = deck_to_pdf(deck_html, out / "deck.pdf")
    rdv.prep_dir = str(out)
    rdv.prep_status = ProspectRdv.Prep.DONE
    rdv.prep_at = timezone.now()
    if create_event and rdv.start:
        try:
            rdv.prep_event_id = _create_prep_event(rdv, links, pdf_ok)
        except Exception as exc:  # noqa: BLE001 — l'événement est un plus, le brief existe
            logger.warning("Créneau de préparation non créé pour RDV %s : %s", rdv.pk, exc)
            _mail_fallback(rdv, links)
    rdv.save()
    _hub(rdv, links)
    return {"ok": True, "links": links, "reason": "", "pdf": pdf_ok}


def _create_prep_event(rdv, links: dict, pdf_ok: bool) -> str:
    from ekoalu.notifications.graph_calendar import create_event

    start_local = timezone.localtime(rdv.start)
    prep_start = start_local - dt.timedelta(minutes=PREP_BEFORE_MINUTES)
    company = rdv.who
    body = (
        f"<p><strong>{rdv.service or 'Rendez-vous'} : {company}</strong>, "
        f"{start_local:%A %d/%m %H:%M} (créneau suivant).</p>"
        f"<p>Brief d'entretien : <a href=\"{links['brief']}\">{links['brief']}</a></p>"
        f"<p>Deck (8 slides, flèches) : <a href=\"{links['deck']}\">{links['deck']}</a></p>"
        + (f"<p>PDF du deck : <a href=\"{links['pdf']}\">{links['pdf']}</a></p>" if pdf_ok else "")
        + (f"<p>Teams du RDV : <a href=\"{rdv.teams_url}\">{rdv.teams_url}</a></p>" if rdv.teams_url else "")
        + "<p>Brief généré automatiquement : vérifier les points marqués non confirmés.</p>"
    )
    return create_event(subject=f"Prépa {rdv.service or 'RDV'} {company} (brief + deck)",
                        start=prep_start.replace(tzinfo=None), end=start_local.replace(tzinfo=None),
                        body_html=body)


def _mail_fallback(rdv, links: dict) -> None:
    try:
        from ekoalu.notifications.graph_mailer import send_mail

        send_mail(subject=f"Prépa RDV {rdv.who} : brief et deck prêts",
                  html_body=f"<p>Brief : <a href=\"{links['brief']}\">{links['brief']}</a><br>"
                            f"Deck : <a href=\"{links['deck']}\">{links['deck']}</a></p>",
                  to=os.environ.get("GRAPH_ALERT_RECIPIENT", "richard@ekoalu.com"), category="alert")
    except Exception:  # noqa: BLE001
        logger.warning("Mail de repli non envoyé (RDV %s)", rdv.pk)


def _hub(rdv, links: dict) -> None:
    try:
        from ekoalu.notifications.hub_events import post_event

        post_event("prospection.rdv_prep", "info", f"RDV préparé : {rdv.who} ({rdv.service})",
                   {"status": "prepared", "rdv": rdv.pk, "brief": links["brief"], "deck": links["deck"]})
    except Exception:  # noqa: BLE001
        pass


def pending_rdvs(days: int = LOOKAHEAD_DAYS):
    from ekoalu.email_canal.models import ProspectRdv

    now = timezone.now()
    return list(
        ProspectRdv.objects
        .filter(status=ProspectRdv.Status.PLANNED, start__gte=now, start__lte=now + dt.timedelta(days=days))
        .exclude(prep_status=ProspectRdv.Prep.DONE)
        .order_by("start")
    )
