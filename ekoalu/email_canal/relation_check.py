"""Contrôle « déjà en relation » dans Outlook avant d'écrire (fiche hub #251).

14 refus « déjà en relation » tapés à la main en 30 jours : l'information est
dans la boîte de Richard. Avant de sélectionner un lead pour un cold mail, on
cherche son adresse via l'Outlook Gateway (lecture seule), puis, si l'adresse
est nominative sur un domaine d'entreprise, le domaine. Un échange trouvé
(reçu de lui, ou envoyé par nous en dehors de nos propres cold mails) marque
le lead « relation existante » et le sort de la file, motif automatique
« Déjà en relation (N mails, dernier le …) ».

Gateway indisponible = pas de vérification, on continue (log + événement hub).
Une recherche par seconde au plus (throttle dans outlook_gateway).
Kill-switch : EKOALU_RELATION_CHECK=0.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.utils import timezone

from ekoalu.bdd_prospect_import import B2C_DOMAINS, _is_generic_local_part

if TYPE_CHECKING:  # pragma: no cover
    from crm.models import Lead

logger = logging.getLogger(__name__)

OUR_MAILBOX = "richard@ekoalu.com"
BOOKINGS_SENDER = "ekoaluprisederdv@ekoalu.com"


def relation_check_enabled() -> bool:
    return os.environ.get("EKOALU_RELATION_CHECK", "1").lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class RelationHit:
    count: int
    last_at: str          # ISO du dernier échange
    matched: str          # "email" | "domain"

    def reason(self) -> str:
        day = self.last_at[:10]
        return f"Déjà en relation ({self.count} mail(s), dernier le {day[8:10]}/{day[5:7]}/{day[:4]})"

    def as_json(self) -> dict:
        return {"count": self.count, "last_at": self.last_at, "matched": self.matched,
                "checked_at": timezone.now().isoformat(timespec="seconds")}


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def is_company_domain(email: str) -> bool:
    d = _domain(email)
    return bool(d) and d not in B2C_DOMAINS


def _addr(block: dict | None) -> str:
    return ((block or {}).get("emailAddress") or {}).get("address", "").lower()


MASS_MAIL_RECIPIENTS = 5   # au-delà : envoi groupé, pas une relation


def _relevant(msg: dict, needle: str, by_domain: bool, ignore_subjects: set[str]) -> bool:
    """Un message compte s'il vient du prospect (ou de son domaine), ou si RICHARD
    le lui a écrit (hors nos cold mails, hors envois groupés). Un tiers qui nous
    met tous les deux en copie (candidature spontanée à 500 adresses) ne compte
    pas, ni une adresse seulement citée dans un corps de mail (la recherche
    Outlook est plein texte)."""
    sender = _addr(msg.get("from"))
    if sender and sender not in (OUR_MAILBOX, BOOKINGS_SENDER):
        return sender.endswith("@" + needle) if by_domain else sender == needle
    if sender != OUR_MAILBOX:
        return False
    recipients = [_addr(r) for r in (msg.get("toRecipients") or []) + (msg.get("ccRecipients") or [])]
    if len(recipients) > MASS_MAIL_RECIPIENTS:
        return False
    to_them = any((r.endswith("@" + needle) if by_domain else r == needle) for r in recipients)
    if not to_them:
        return False
    return (msg.get("subject") or "").strip().lower() not in ignore_subjects


def _scan(query: str, needle: str, by_domain: bool, ignore_subjects: set[str]) -> RelationHit | None | bool:
    """None = Gateway KO ; False = rien ; RelationHit = échange trouvé."""
    from ekoalu.notifications.outlook_gateway import search_messages

    msgs = search_messages(query, top=25)
    if msgs is None:
        return None
    hits = [m for m in msgs if _relevant(m, needle, by_domain, ignore_subjects)]
    if not hits:
        return False
    last = max((m.get("receivedDateTime") or "") for m in hits)
    return RelationHit(count=len(hits), last_at=last, matched="domain" if by_domain else "email")


def check_relation(email: str, ignore_subjects: set[str] | None = None) -> RelationHit | None | bool:
    """Recherche par adresse, puis par domaine si nominatif sur un domaine pro.
    None = Gateway indisponible (ne pas conclure)."""
    email = (email or "").strip().lower()
    if not email:
        return False
    ignore = {s.strip().lower() for s in (ignore_subjects or set())}
    res = _scan(email, email, False, ignore)
    if res is None or res:
        return res
    if is_company_domain(email) and not _is_generic_local_part(email):
        return _scan("@" + _domain(email), _domain(email), True, ignore)
    return False


def _our_subjects_for(public_id: str) -> set[str]:
    from ekoalu.outbound_validation.models import OutboundKind, PendingOutbound

    return {
        s.strip().lower() for s in PendingOutbound.objects.filter(
            prospect_public_id=public_id, kind=OutboundKind.EMAIL_COLD,
        ).exclude(subject="").values_list("subject", flat=True)
    }


def mark_relation(lead: "Lead", hit: RelationHit) -> None:
    """Pose relation_existante et sort le lead de la prospection (motif automatique)."""
    from crm.models.deal import Outcome
    from ekoalu.lead_exclusion import disqualify_leads

    data = getattr(lead, "email_data", None)
    if data is not None:
        data.relation_existante = hit.as_json()
        data.save(update_fields=["relation_existante"])
    disqualify_leads([lead.public_identifier], hit.reason(),
                     outcome=Outcome.PRE_EXISTING_RELATION.value)
    logger.info("Lead %s écarté : %s", lead.public_identifier, hit.reason())


@dataclass
class ScreenResult:
    kept: list
    removed: int = 0
    gateway_down: bool = False


def screen_candidates(candidates: list, limit: int) -> ScreenResult:
    """Parcourt les candidats dans l'ordre jusqu'à en garder `limit` sans relation.
    Gateway KO au premier appel = on garde tel quel (pas de vérification)."""
    if not relation_check_enabled():
        return ScreenResult(kept=list(candidates[:limit]))
    kept: list = []
    removed = 0
    for lead in candidates:
        if len(kept) >= limit:
            break
        data = getattr(lead, "email_data", None)
        if data is not None and data.relation_existante:
            continue
        res = check_relation(lead.contact_email or "", _our_subjects_for(lead.public_identifier))
        if res is None:
            logger.warning("Contrôle Outlook indisponible : cold mails générés sans vérification")
            rest = [x for x in candidates if x not in kept][: limit - len(kept)]
            return ScreenResult(kept=kept + rest, removed=removed, gateway_down=True)
        if res:
            mark_relation(lead, res)
            removed += 1
            continue
        if data is not None:
            data.relation_existante = None
        kept.append(lead)
    return ScreenResult(kept=kept, removed=removed)
