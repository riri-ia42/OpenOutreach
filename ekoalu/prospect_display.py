"""Helpers d'affichage prospect : nom propre, societe, ville.

Extraction heuristique depuis le slug LinkedIn + profile_summary (mem0 facts).
Utilise par les vues pour enrichir les listes (messages, validation,
disqualifies, fiche prospect) au-dela du simple slug brut.
"""
from __future__ import annotations

import re

_SLUG_NOISE_RE = re.compile(r"^[a-z]+[0-9]{2,}$|^[0-9a-f]{8,}$", re.IGNORECASE)
_LOCATION_MARKERS = (
    "location:", "lives in", "based in", "ville :", "ville:", "city:",
    "region:", "lieu :", "lieu:",
)
_COMPANY_MARKERS = (
    "company:", "works at ", "entreprise :", "entreprise:",
    "societe :", "societe:", "société :", "société:", "chez ",
)


def _facts_iter(profile_summary):
    """Itere les facts memo d'un profile_summary, quelque soit la forme."""
    if not profile_summary:
        return
    facts = profile_summary if isinstance(profile_summary, list) else (
        profile_summary.get("facts") if isinstance(profile_summary, dict) else []
    )
    for fact in facts or []:
        if isinstance(fact, dict):
            txt = fact.get("memory") or fact.get("text") or fact.get("fact") or ""
        else:
            txt = str(fact)
        if txt:
            yield txt


def display_name_from_slug(slug: str) -> str:
    """Convertit 'patrick-gomes-gcr-suffix' -> 'Patrick Gomes'.

    Heuristique : on prend les premiers tokens alphabetiques, on capitalise,
    on s'arrete des qu'on rencontre un token "bruit" (digits, hex, etc).
    Limite a 2 tokens pour eviter de coller le nom de societe.
    """
    if not slug:
        return ""
    tokens = []
    for tok in slug.split("-"):
        if not tok:
            continue
        if _SLUG_NOISE_RE.match(tok):
            break
        # ignore single-letter tokens (initiales, debris)
        if len(tok) < 2 and tokens:
            continue
        tokens.append(tok)
        if len(tokens) >= 2:
            break
    if not tokens:
        return slug
    return " ".join(t.capitalize() for t in tokens)


def extract_company(profile_summary) -> str:
    """Extrait le nom de societe depuis les facts mem0. Vide si introuvable."""
    for txt in _facts_iter(profile_summary):
        low = txt.lower()
        for marker in _COMPANY_MARKERS:
            if marker in low:
                idx = low.find(marker) + len(marker)
                value = txt[idx:].strip(".,;:- \n").split("\n")[0]
                if value and len(value) <= 140:
                    return value.strip()
    return ""


def extract_location(profile_summary) -> str:
    """Extrait la localisation depuis les facts mem0. Vide si introuvable."""
    for txt in _facts_iter(profile_summary):
        low = txt.lower()
        for marker in _LOCATION_MARKERS:
            if marker in low:
                idx = low.find(marker) + len(marker)
                value = txt[idx:].strip(".,;:- \n").split("\n")[0]
                if value and len(value) <= 120:
                    return value.strip()
    return ""


def resolve_prospect_display(slug: str, deal=None, company_hint: str = "") -> dict:
    """Renvoie un dict {name, company, location} pour les templates.

    Args:
        slug: public_identifier LinkedIn (toujours dispo)
        deal: Deal objet (pour acceder a profile_summary), optionnel
        company_hint: fallback company si profile_summary vide (ex : PendingOutbound.prospect_company)
    """
    name = display_name_from_slug(slug)
    profile_summary = getattr(deal, "profile_summary", None) if deal else None
    company = extract_company(profile_summary) or company_hint or ""
    location = extract_location(profile_summary)
    return {"name": name, "company": company, "location": location}


def resolve_for_lead(lead, deal=None) -> dict:
    """Variant qui prend un objet Lead. Utilise par fiche prospect."""
    slug = getattr(lead, "public_identifier", "")
    return resolve_prospect_display(slug, deal=deal)
