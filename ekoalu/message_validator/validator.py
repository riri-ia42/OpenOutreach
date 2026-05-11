"""Validator pipeline EKOALU.

Combine :
- Pas de mot banni
- Si persona catégorie 1 (dirigeants) : au moins 1 terme niche
- Longueur respectée selon step (300 char max pour invitation LinkedIn)
- Lien Bookings absent des invitations + message #1 + follow-ups
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field

from ekoalu import conf
from ekoalu.message_validator.banned_words import find_banned_words
from ekoalu.message_validator.niche_terms import find_niche_terms


class MessageStep(str, enum.Enum):
    INVITATION = "invitation"           # Demande de connexion (300 char max)
    MESSAGE_1 = "message_1"             # 1er message post-acceptation
    FOLLOWUP_1 = "followup_1"           # Relance J+7
    FOLLOWUP_2 = "followup_2"           # Relance J+14
    REPLY = "reply"                     # Réponse à un prospect (inbox_assist)


class PersonaCategory(str, enum.Enum):
    DIRIGEANT_CONNEXE = "c1"            # EG, charpentier, métallier, maçon
    PRESCRIPTEUR = "c2"                 # Archi, MOE, BET
    PROMOTEUR = "c3"
    OUVERTURE = "c4"


# Mots-clés "demande RDV" qui ne doivent PAS être dans message_1 / follow-ups
_RDV_KEYWORDS = [
    "rdv", "rendez-vous", "rendez vous",
    "appel", "appeler", "call",
    "agenda", "disponibilité", "disponibilites",
    "creneau", "créneau",
    "visio",
]


@dataclass
class ValidationContext:
    step: MessageStep
    persona_category: PersonaCategory
    # Intention déclarée (utile pour reply : si intention=RDV_REQUEST, le lien
    # booking est autorisé)
    intent: str | None = None


@dataclass
class ValidationResult:
    passing: bool
    issues: list[str] = field(default_factory=list)
    found_banned: list[str] = field(default_factory=list)
    found_niche: list[str] = field(default_factory=list)
    length: int = 0


def validate_message(text: str, context: ValidationContext) -> ValidationResult:
    """Valide un message contre les règles EKOALU.

    Returns ValidationResult avec passing=True si tout est OK.
    """
    issues: list[str] = []

    # 1. Mots bannis
    banned_found = find_banned_words(text)
    if banned_found:
        issues.append(f"mots_bannis: {banned_found}")

    # 2. Termes niches (obligatoire pour dirigeants catégorie 1, sauf en reply technique)
    niche_found = find_niche_terms(text)
    if context.persona_category == PersonaCategory.DIRIGEANT_CONNEXE:
        if context.step in (MessageStep.INVITATION, MessageStep.MESSAGE_1):
            if not niche_found:
                issues.append(
                    "manque_terme_niche: stratégie wedge — persona dirigeant doit "
                    "mentionner au moins 1 produit niche (coupe-feu, désenfumage, "
                    "pare-balles, grandes dim, acoustique)"
                )

    # 3. Longueur invitation < 300 char (limite LinkedIn)
    length = len(text)
    if context.step == MessageStep.INVITATION and length > 300:
        issues.append(f"invitation_trop_longue: {length} > 300 char")

    # 4. Pas de demande RDV dans invitation / message_1 / follow-ups
    if context.step in (
        MessageStep.INVITATION,
        MessageStep.MESSAGE_1,
        MessageStep.FOLLOWUP_1,
        MessageStep.FOLLOWUP_2,
    ):
        rdv_found = [k for k in _RDV_KEYWORDS if k in text.lower()]
        if rdv_found:
            issues.append(f"demande_rdv_premature: {rdv_found} (donner avant de demander)")

    # 5. Lien Booking absent de invitation / message_1 / follow-ups
    if context.step in (
        MessageStep.INVITATION,
        MessageStep.MESSAGE_1,
        MessageStep.FOLLOWUP_1,
        MessageStep.FOLLOWUP_2,
    ):
        if "outlook.office365.com" in text.lower() or "bookings" in text.lower():
            issues.append(
                f"lien_booking_premature: les liens de RDV n'apparaissent qu'en "
                f"réponse à un signal positif"
            )

    return ValidationResult(
        passing=len(issues) == 0,
        issues=issues,
        found_banned=banned_found,
        found_niche=niche_found,
        length=length,
    )
