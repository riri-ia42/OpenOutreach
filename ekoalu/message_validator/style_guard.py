"""Garde-fou de style post-generation, branche sur TOUS les canaux (audit 07/07).

Verifie mots bannis (jargon, tournures ampoulees, auto-eloges, closings creux)
+ clotures interdites ("Cordialement"). Si violation : 1 regeneration avec le
motif ; si la violation persiste, log warning et le message part quand meme en
file de validation Richard — jamais de blocage dur silencieux.
"""
from __future__ import annotations

import logging
import re

from ekoalu.message_validator.banned_words import find_banned_words

logger = logging.getLogger(__name__)

# "Bien cordialement" matche aussi via \bcordialement\b — un seul motif suffit.
_FORBIDDEN_CLOSING = re.compile(r"\bcordialement\b", re.IGNORECASE)


def find_style_violations(text: str) -> list[str]:
    """Liste des violations de la charte EKOALU dans `text` (vide si conforme)."""
    violations = list(find_banned_words(text))
    if _FORBIDDEN_CLOSING.search(text or ""):
        violations.append('cloture interdite "Cordialement" (charte : "Bien a vous")')
    return violations


def style_fix_instruction(violations: list[str]) -> str:
    """Motif de regeneration a joindre au prompt."""
    return (
        "CORRECTION DE STYLE OBLIGATOIRE : ta precedente version contenait des "
        f"elements interdits par la charte EKOALU : {', '.join(violations)}. "
        "Regenere le message en les supprimant, sans changer le fond."
    )


def enforce_style(text: str, regenerate, *, channel: str) -> str:
    """Valide `text` ; si violation, tente UNE regeneration via `regenerate(motif)`.

    `regenerate` : callable(motif: str) -> nouveau texte ("" si echec).
    Ne bloque jamais : au pire le texte (regenere ou non) part en file de
    validation Richard avec un warning en log.
    """
    violations = find_style_violations(text)
    if not violations:
        return text
    logger.warning("Style EKOALU viole (%s) : %s — regeneration", channel, violations)
    new_text = regenerate(style_fix_instruction(violations))
    if not new_text:
        logger.warning(
            "Regeneration style vide (%s) — on garde la version initiale "
            "(part en file de validation Richard)", channel,
        )
        return text
    remaining = find_style_violations(new_text)
    if remaining:
        logger.warning(
            "Style EKOALU toujours viole apres regeneration (%s) : %s — "
            "le message part quand meme en file de validation Richard",
            channel, remaining,
        )
    return new_text
