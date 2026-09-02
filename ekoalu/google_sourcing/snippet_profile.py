"""Mini-fiche profil depuis le title/snippet Google (Serper) — fournisseur
d'enrichissement cookieless de dernier recours.

Décision Richard 02/09 : le titre SERP d'un profil LinkedIn suit le patron
« Prénom Nom - Poste - Entreprise | LinkedIn » et le snippet (meta description
LinkedIn) porte souvent la localisation (« Lieu : Lyon ») — une mini-fiche
gratuite que le sourcing recevait déjà et jetait après le pré-filtre.

Produit un ``profile_snapshot`` au format interne (source ``serper_snippet``),
suffisant pour l'embedding + le tri LLM. Il est PARTIEL (pas d'expériences
détaillées) : il n'est utilisé qu'en repli quand Bright Data ET Apify ont
échoué, AVANT le dernier recours Voyager qui consomme une lecture du compte.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

SNAPSHOT_SOURCE = "serper_snippet"

# " - ", " – " (en dash), " — " (em dash) : tous vus dans les titres SERP.
_SEP_RE = re.compile(r"\s+[-–—]\s+")
# Suffixe " | LinkedIn", " | LinkedIn France", " - LinkedIn"…
_LINKEDIN_SUFFIX_RE = re.compile(r"\s*[|\-–—]\s*LinkedIn.*$", re.IGNORECASE)

# Localisation dans le snippet (meta description LinkedIn, formats FR/EN).
_LOCATION_PATTERNS = (
    re.compile(r"(?:Lieu|Location|Localisation)\s*:\s*([^·•;|.]+)", re.IGNORECASE),
    re.compile(r"Région de ([\w\sÀ-ÿ'-]+)", re.IGNORECASE),
    re.compile(r"([\w\sÀ-ÿ'-]{3,40}) et périphérie", re.IGNORECASE),
)
# « Directeur de travaux chez Vinci · … » — entreprise de secours si le title
# n'en donne pas.
_CHEZ_RE = re.compile(r"\bchez\s+([A-ZÀ-Þ0-9][^·•;|.]{1,60})")


def parse_serp(title: str, snippet: str) -> dict | None:
    """Title + snippet SERP -> dict ``{full_name, headline, company, location}``.

    None si le title ne permet même pas d'extraire un nom + un poste — dans ce
    cas la mini-fiche n'apporterait rien au tri (le nom seul n'aide pas).
    """
    cleaned = _LINKEDIN_SUFFIX_RE.sub("", (title or "").strip())
    parts = [p.strip() for p in _SEP_RE.split(cleaned) if p.strip()]
    if len(parts) < 2:
        return None

    full_name = parts[0]
    headline = parts[1]
    company = " - ".join(parts[2:]) if len(parts) > 2 else None

    snippet = snippet or ""
    if not company:
        m = _CHEZ_RE.search(snippet)
        if m:
            company = m.group(1).strip()

    location = None
    for pattern in _LOCATION_PATTERNS:
        m = pattern.search(snippet)
        if m:
            location = m.group(1).strip()
            break

    return {
        "full_name": full_name,
        "headline": headline,
        "company": company,
        "location": location,
    }


def build_snapshot(public_identifier: str, url: str, parsed: dict,
                   snippet: str) -> dict:
    """Mini-fiche -> format ``profile_snapshot`` interne (clés Voyager).

    Le snippet brut est placé en ``summary`` : il porte souvent expérience/
    formation en vrac — autant de signal pour l'embedding et le verdict LLM.
    """
    positions = []
    if parsed.get("headline") or parsed.get("company"):
        positions.append({
            "title": parsed.get("headline"),
            "company_name": parsed.get("company"),
            "company_urn": None,
            "location": parsed.get("location"),
            "date_range": None,
            "description": None,
            "urn": None,
        })
    names = (parsed.get("full_name") or "").split(None, 1)
    return {
        "url": url,
        "urn": None,
        "full_name": parsed.get("full_name"),
        "first_name": names[0] if names else None,
        "last_name": names[1] if len(names) > 1 else None,
        "headline": parsed.get("headline"),
        "summary": (snippet or "")[:1000] or None,
        "public_identifier": public_identifier,
        "location_name": parsed.get("location"),
        "geo": None,
        "industry": None,
        "country_code": None,
        "supported_locales": [],
        "positions": positions,
        "educations": [],
        "connection_distance": None,
        "connection_degree": None,
        "source": SNAPSHOT_SOURCE,
        "partial": True,  # mini-fiche : pas d'expériences détaillées
    }


def enrich_lead_from_serp(lead) -> bool:
    """Pose snapshot + embedding depuis la SerpMeta stockée au sourcing.

    False = pas de SerpMeta ou title inexploitable -> l'appelant continue la
    chaîne (Voyager en dernier recours). Zéro appel réseau, zéro lecture.
    """
    from django.utils import timezone

    try:
        meta = lead.serp_meta
    except Exception:  # noqa: BLE001 — pas de SerpMeta pour ce lead
        return False

    parsed = parse_serp(meta.title, meta.snippet)
    if not parsed:
        return False

    snap = build_snapshot(lead.public_identifier, lead.linkedin_url or "",
                          parsed, meta.snippet)
    now = timezone.now()
    snap["fetched_at"] = now.isoformat()
    lead.profile_snapshot = snap
    lead.profile_snapshot_at = now
    lead.save(update_fields=["profile_snapshot", "profile_snapshot_at"])
    lead.embed_from_profile(snap)
    logger.info("Mini-fiche SERP posée pour %s (%r @ %r)",
                lead.public_identifier, parsed.get("headline"), parsed.get("company"))
    return True
