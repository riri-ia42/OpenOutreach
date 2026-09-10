"""Collecte des faits pour un rendez-vous (lecture seule, best-effort).

Sources, dans l'ordre du README de la trame : base prospection-ia, Outlook
Gateway (échanges), registre des entreprises (API recherche-entreprises),
LinkedIn public via Serper (title + snippet = poste et employeur). Chaque
source absente ou en panne laisse un champ vide : le brief le dit.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field

import requests

logger = logging.getLogger(__name__)

API_ENTREPRISES = "https://recherche-entreprises.api.gouv.fr/search"
_GENERIC_DOMAINS = {"gmail.com", "orange.fr", "wanadoo.fr", "free.fr", "hotmail.fr", "hotmail.com",
                    "outlook.fr", "outlook.com", "yahoo.fr", "laposte.net", "sfr.fr", "icloud.com"}


@dataclass
class RdvFacts:
    who: str
    email: str
    phone: str
    company_hint: str
    service: str
    start_iso: str
    end_iso: str
    teams_url: str
    company: dict = field(default_factory=dict)      # registre des entreprises
    lead: dict = field(default_factory=dict)         # base prospection-ia
    history: list = field(default_factory=list)      # messages sortants / réponses
    outlook: list = field(default_factory=list)      # échanges Outlook (from/to/subject/date)
    linkedin: dict = field(default_factory=dict)     # title/snippet/link
    sources: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def company_hint_from(who: str, email: str) -> str:
    """« GEAY Lionel société SAGE ECO » → SAGE ECO ; sinon racine du domaine pro."""
    m = re.search(r"\b(?:société|societe|sté)\s+(.+)$", who or "", re.IGNORECASE)
    if m:
        return m.group(1).strip()
    d = domain_of(email)
    if d and d not in _GENERIC_DOMAINS:
        return d.split(".")[0].replace("-", " ")
    return ""


def registre_entreprise(query: str) -> dict:
    if not query:
        return {}
    try:
        resp = requests.get(API_ENTREPRISES, params={"q": query, "per_page": 3}, timeout=15)
        results = resp.json().get("results") or [] if resp.ok else []
    except (requests.RequestException, ValueError) as exc:
        logger.warning("API entreprises KO (%s) : %s", query, exc)
        return {}
    actives = [r for r in results if r.get("etat_administratif") == "A"] or results
    if not actives:
        return {}
    r = actives[0]
    siege = r.get("siege") or {}
    return {
        "nom": r.get("nom_complet"), "siren": r.get("siren"), "naf": r.get("activite_principale"),
        "creation": r.get("date_creation"), "effectif_tranche": r.get("tranche_effectif_salarie"),
        "nature_juridique": r.get("nature_juridique"), "adresse": siege.get("adresse"),
        "dirigeants": [f"{d.get('prenoms', '')} {d.get('nom', '')} ({d.get('qualite', '')})".strip()
                       for d in (r.get("dirigeants") or [])[:4]],
    }


def base_prospection(email: str, who: str) -> tuple[dict, list]:
    from crm.models import Lead
    from ekoalu.email_canal.rdv import match_lead
    from ekoalu.inbox_assist.models import PendingReply
    from ekoalu.outbound_validation.models import PendingOutbound

    lead = match_lead(email, who)
    if lead is None:
        return {}, []
    data = getattr(lead, "email_data", None)
    info = {
        "public_id": lead.public_identifier, "email": lead.contact_email,
        "source": getattr(data, "source", ""), "entreprise": getattr(data, "entreprise", ""),
        "dirigeant": getattr(data, "dirigeant", ""), "naf": getattr(data, "code_naf", ""),
        "ville": getattr(data, "ville", ""), "siren": getattr(data, "siren", ""),
    }
    snap = lead.profile_snapshot or {}
    if snap:
        info["linkedin_headline"] = snap.get("headline", "")
        info["positions"] = [f"{p.get('title', '')} chez {p.get('company_name', '')}"
                             for p in (snap.get("positions") or [])[:4]]
    history = []
    for po in PendingOutbound.objects.filter(prospect_public_id=lead.public_identifier).order_by("created_at"):
        history.append({"date": (po.sent_at or po.created_at).strftime("%Y-%m-%d"), "type": po.kind,
                        "statut": po.status, "objet": po.subject, "extrait": po.content_to_send[:300]})
    for r in PendingReply.objects.filter(prospect_public_id=lead.public_identifier).order_by("created_at"):
        history.append({"date": r.created_at.strftime("%Y-%m-%d"), "type": "reponse", "intent": r.intent,
                        "extrait": (r.inbound_message or "")[:300]})
    return info, history


def outlook_exchanges(email: str, who: str) -> list:
    from ekoalu.notifications.outlook_gateway import search_messages

    out = []
    queries = [q for q in (email, (who or "").split(" société")[0].strip()) if q]
    for q in queries[:2]:
        for m in (search_messages(q, top=10) or []):
            sender = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "")
            if sender.lower() == "ekoaluprisederdv@ekoalu.com":
                continue
            out.append({"date": (m.get("receivedDateTime") or "")[:10], "from": sender,
                        "to": ",".join(((r.get("emailAddress") or {}).get("address", ""))
                                       for r in (m.get("toRecipients") or [])[:3]),
                        "objet": (m.get("subject") or "")[:100],
                        "extrait": (m.get("bodyPreview") or "")[:200]})
    seen, dedup = set(), []
    for x in out:
        key = (x["date"], x["objet"])
        if key not in seen:
            seen.add(key)
            dedup.append(x)
    return dedup[:8]


def linkedin_public(who: str, company: str) -> dict:
    """Title + snippet Google du profil LinkedIn (1 crédit Serper), sans lecture LinkedIn."""
    name = re.sub(r"\s+(?:société|societe|sté)\s+.*$", "", who or "", flags=re.IGNORECASE).strip()
    if not name:
        return {}
    try:
        from ekoalu.google_sourcing.client import search_linkedin_results
        results = search_linkedin_results(f'"{name}" {company}'.strip(), num=3)
    except Exception as exc:  # noqa: BLE001 — Serper absent ou quota : champ vide
        logger.warning("Serper indisponible pour %s : %s", name, exc)
        return {}
    if not results:
        return {}
    r = results[0]
    return {"title": r.get("title", ""), "snippet": r.get("snippet", ""), "link": r.get("link", "")}


def collect(rdv, details: dict) -> RdvFacts:
    """`rdv` = ProspectRdv ; `details` = booking_details(event) (email/phone/teams)."""
    from django.utils import timezone

    email = (details.get("email") or rdv.prospect_email or "").lower()
    company_hint = company_hint_from(rdv.who, email)
    facts = RdvFacts(
        who=rdv.who, email=email, phone=details.get("phone", ""), company_hint=company_hint,
        service=rdv.service, start_iso=timezone.localtime(rdv.start).isoformat() if rdv.start else "",
        end_iso=timezone.localtime(rdv.end).isoformat() if rdv.end else "",
        teams_url=details.get("teams_url") or rdv.teams_url or "",
    )
    facts.company = registre_entreprise(company_hint)
    if facts.company:
        facts.sources.append("API recherche-entreprises")
    facts.lead, facts.history = base_prospection(email, rdv.who)
    if facts.lead:
        facts.sources.append("base prospection-ia")
    facts.outlook = outlook_exchanges(email, rdv.who)
    if facts.outlook:
        facts.sources.append("Outlook (Gateway)")
    facts.linkedin = linkedin_public(rdv.who, facts.company.get("nom") or company_hint)
    if facts.linkedin:
        facts.sources.append("LinkedIn public (Serper)")
    return facts
