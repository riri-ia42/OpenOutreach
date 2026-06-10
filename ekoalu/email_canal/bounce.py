"""Détection des bounces / NDR (rapports de non-remise) dans l'inbox.

Un hard bounce silencieux est double peine : le prospect ne reçoit rien ET la
réputation du domaine ekoalu.com se dégrade à chaque renvoi vers une adresse
morte. Le poller inbox passe chaque message ici AVANT le matching lead : si
c'est un NDR, on marque `Lead.email_bounced_at` et l'adresse sort
définitivement des envois (filtre dans `sender._resolve_recipient`).

Détection volontairement conservatrice (expéditeur postmaster/mailer-daemon ou
objet NDR connu) : un faux négatif coûte un renvoi inutile, un faux positif
exclurait un vrai prospect.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Expéditeurs système typiques des NDR (Microsoft 365, Exchange, MTA divers)
_BOUNCE_SENDER_PATTERNS = (
    "postmaster@",
    "mailer-daemon@",
    "microsoftexchange",  # MicrosoftExchange329e71ec88ae4615bbc36ab6ce41109e@...
)

# Préfixes d'objet NDR (FR + EN, Outlook/Exchange/MTA courants)
_BOUNCE_SUBJECT_PATTERNS = (
    "undeliverable",
    "non remis",
    "non remise",
    "échec de la remise",
    "echec de la remise",
    "mail delivery failed",
    "delivery status notification (failure)",
    "delivery has failed",
    "returned mail",
)

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def is_bounce_message(msg: dict) -> bool:
    """True si le message ressemble à un NDR (expéditeur système OU objet NDR)."""
    sender = (msg.get("from_email") or "").lower()
    subject = (msg.get("subject") or "").lower()
    if any(p in sender for p in _BOUNCE_SENDER_PATTERNS):
        return True
    return any(subject.startswith(p) or p in subject for p in _BOUNCE_SUBJECT_PATTERNS)


def find_bounced_lead(msg: dict):
    """Cherche le Lead dont l'adresse apparaît dans le corps du NDR.

    Le NDR cite l'adresse du destinataire original quelque part dans le body
    (et souvent dans l'objet). On extrait toutes les adresses et on prend le
    premier match contre Lead.contact_email. None si aucun.
    """
    from crm.models import Lead

    text = f"{msg.get('subject', '')}\n{msg.get('body_text', '')}"
    own_domain = "@ekoalu.com"
    seen = set()
    for candidate in _EMAIL_RE.findall(text):
        candidate = candidate.lower().rstrip(".")
        if candidate in seen or candidate.endswith(own_domain):
            continue
        seen.add(candidate)
        lead = Lead.objects.filter(contact_email__iexact=candidate).first()
        if lead:
            return lead
    return None


def process_bounce(msg: dict) -> str:
    """Marque le lead bouncé. Retourne "bounce_marked" ou "bounce_unmatched"."""
    from django.utils import timezone

    lead = find_bounced_lead(msg)
    if lead is None:
        logger.info(
            "NDR reçu sans lead correspondant (sujet=%r) — ignoré",
            (msg.get("subject") or "")[:80],
        )
        return "bounce_unmatched"

    if lead.email_bounced_at is None:
        lead.email_bounced_at = timezone.now()
        lead.save(update_fields=["email_bounced_at"])
        logger.warning(
            "Hard bounce : %s (%s) marqué email_bounced_at — exclu des envois",
            lead.public_identifier, lead.contact_email,
        )
    return "bounce_marked"
