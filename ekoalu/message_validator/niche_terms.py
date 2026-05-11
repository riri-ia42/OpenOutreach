"""Termes "produits niches" EKOALU (stratégie wedge tertiaire technique).

Cf. mémoire strategie_commerciale_ekoalu.
"""
from __future__ import annotations

import re

from ekoalu import conf

# Repris depuis conf.NICHE_PRODUCTS pour single source of truth
NICHE_TERMS: list[str] = list(conf.NICHE_PRODUCTS)


def find_niche_terms(text: str) -> list[str]:
    """Retourne la liste des termes niches détectés (case-insensitive).

    Match en mot entier ou sous-chaîne selon que le terme contient des
    espaces / tirets.
    """
    if not text:
        return []
    found: list[str] = []
    text_lower = text.lower()
    for term in NICHE_TERMS:
        term_lower = term.lower()
        if " " in term_lower or "-" in term_lower:
            if term_lower in text_lower:
                found.append(term)
        else:
            pattern = r"\b" + re.escape(term_lower) + r"\b"
            if re.search(pattern, text_lower):
                found.append(term)
    return found


def contains_niche_term(text: str) -> bool:
    """True si le texte contient au moins 1 terme niche."""
    return bool(find_niche_terms(text))
