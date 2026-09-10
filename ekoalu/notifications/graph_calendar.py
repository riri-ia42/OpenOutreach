"""Agenda Outlook de Richard via Microsoft Graph (jeton de graph_mailer).

Le jeton `.default` accordé à l'application couvre l'agenda (vérifié le
10/09/2026 : calendarView 200, création 201, suppression 204). Lecture des
événements avec leur corps (les rendez-vous Bookings y portent l'adresse et le
téléphone du prospect, que la notification mail ne contient pas) et création
d'un événement de préparation dans l'agenda de Richard (aucun participant :
rien ne part vers un tiers).
"""
from __future__ import annotations

import datetime as dt
import logging
import re

import requests

from ekoalu.notifications import graph_mailer as g

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(?:Numéro de téléphone|Phone Number)\s*:\s*([+\d][\d .]{7,})", re.IGNORECASE)
_TEAMS_RE = re.compile(r"https://teams\.microsoft\.com/(?:meet|l/meetup-join)/[^\s\"'<]+")


def _headers() -> dict:
    return {"Authorization": f"Bearer {g._get_access_token()}"}


def _user() -> str:
    return g._required("GRAPH_USER_EMAIL")


def list_events(start: dt.datetime, end: dt.datetime, top: int = 100) -> list[dict]:
    """Événements de l'agenda sur [start, end] (UTC), corps inclus. [] si Graph KO."""
    params = {
        "startDateTime": start.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDateTime": end.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "$select": "id,subject,start,end,body,bodyPreview,organizer,onlineMeeting,isCancelled,webLink,location",
        "$top": top,
    }
    try:
        resp = requests.get(f"{g.GRAPH_BASE}/users/{_user()}/calendarView", params=params,
                            headers=_headers(), timeout=30)
    except (requests.RequestException, g.GraphConfigError, g.GraphAuthError) as exc:
        logger.warning("Graph calendarView injoignable : %s", exc)
        return []
    if not resp.ok:
        logger.warning("Graph calendarView HTTP %s : %s", resp.status_code, resp.text[:200])
        return []
    return list(resp.json().get("value") or [])


def _strip_html(html: str) -> str:
    text = re.sub(r"<style[\s\S]*?</style>", " ", html or "")
    text = re.sub(r"<[^>]+>", "\n", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"[ \t]+", " ", text)


def booking_details(event: dict) -> dict:
    """Extrait d'un événement Bookings : email / téléphone du prospect, lien Teams."""
    body = _strip_html((event.get("body") or {}).get("content") or event.get("bodyPreview") or "")
    emails = [e.lower() for e in _EMAIL_RE.findall(body) if not e.lower().endswith("@ekoalu.com")]
    phone = _PHONE_RE.search(body)
    teams = (event.get("onlineMeeting") or {}).get("joinUrl") or ""
    m = _TEAMS_RE.search((event.get("body") or {}).get("content") or "")
    if m:
        teams = m.group(0)
    return {"email": emails[0] if emails else "", "phone": phone.group(1).strip() if phone else "",
            "teams_url": teams, "event_id": event.get("id") or "", "web_link": event.get("webLink") or ""}


def create_event(*, subject: str, start: dt.datetime, end: dt.datetime, body_html: str,
                 show_as: str = "busy") -> str:
    """Crée un événement SANS participant dans l'agenda de Richard. Renvoie l'id Graph."""
    tz = "Romance Standard Time"
    payload = {
        "subject": subject,
        "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": tz},
        "end": {"dateTime": end.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": tz},
        "body": {"contentType": "html", "content": body_html},
        "showAs": show_as,
        "isReminderOn": True,
        "reminderMinutesBeforeStart": 15,
    }
    resp = requests.post(f"{g.GRAPH_BASE}/users/{_user()}/events", json=payload,
                         headers=_headers(), timeout=30)
    if not resp.ok:
        raise g.GraphSendError(f"create event {resp.status_code}: {resp.text[:300]}")
    event_id = resp.json().get("id") or ""
    logger.info("Événement agenda créé : %s (%s)", subject, event_id[:16])
    return event_id
