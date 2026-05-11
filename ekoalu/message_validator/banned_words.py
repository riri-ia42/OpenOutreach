"""Mots et tournures bannis dans les messages EKOALU.

Cf. MARKETING.md §4.1 et mémoire valeurs_ekoalu.
"""
from __future__ import annotations

import os
import re

# Jargon commercial creux
_JARGON_COMMERCIAL = [
    "synergies",
    "synergie",
    "win-win",
    "ROI",
    "disruption",
    "value-prop",
    "value proposition",
    "stratégie 360",
    "solutions clé en main",
    "solutions clés en main",
    "value-added",
    "value added",
]

# Tournures ampoulées
_TOURNURES_AMPOULEES = [
    "permettez-moi",
    "permettez moi",
    "j'aurais le plaisir",
    "j aurais le plaisir",
    "n'hésitez surtout pas",
    "n hésitez surtout pas",
    "dans l'optique de",
    "dans l optique de",
    "à l'instar de",
    "à l instar de",
    "il me serait agréable",
]

# Auto-éloges
_AUTO_ELOGES = [
    "acteur incontournable",
    "leader sur",
    "référence sur",
    "excellence",
    "à l'écoute",
    "à l ecoute",
    "passion",
]

# Closings creux
_CLOSINGS_CREUX = [
    "au plaisir d'échanger",
    "au plaisir d echanger",
    "restant à votre disposition",
    "restant a votre disposition",
    "dans l'attente de votre retour",
    "dans l attente de votre retour",
]

BANNED_WORDS: list[str] = (
    _JARGON_COMMERCIAL
    + _TOURNURES_AMPOULEES
    + _AUTO_ELOGES
    + _CLOSINGS_CREUX
)

# Extra mots bannis via env (séparés par virgule)
_extra = os.environ.get("EKOALU_EXTRA_BANNED_WORDS", "").strip()
if _extra:
    BANNED_WORDS = BANNED_WORDS + [w.strip() for w in _extra.split(",") if w.strip()]


def find_banned_words(text: str) -> list[str]:
    """Retourne la liste des mots bannis détectés dans `text` (case-insensitive).

    Match en sous-chaîne pour les multi-mots. Pour les mots simples, match en
    mot entier pour éviter faux positifs (ex: "ROI" ne matche pas "héroïque").
    """
    if not text:
        return []
    found: list[str] = []
    text_lower = text.lower()
    for banned in BANNED_WORDS:
        banned_lower = banned.lower()
        # Si le mot banni contient un espace ou tiret, match en sous-chaîne
        if " " in banned_lower or "-" in banned_lower or "'" in banned_lower:
            if banned_lower in text_lower:
                found.append(banned)
        else:
            # Mot simple : match en mot entier
            pattern = r"\b" + re.escape(banned_lower) + r"\b"
            if re.search(pattern, text_lower):
                found.append(banned)
    return found


def contains_banned_word(text: str) -> bool:
    """True si le texte contient au moins 1 mot banni."""
    return bool(find_banned_words(text))
