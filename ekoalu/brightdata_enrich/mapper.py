"""Mapping enregistrement Bright Data -> format ``profile_snapshot`` interne.

Cible : le format Voyager (cf. apify_enrich/mapper.py, mêmes conventions).
Champs du dataset « LinkedIn people profiles » d'après la doc et les exemples
publics Bright Data ; chaque clé est mappée défensivement (plusieurs candidates)
et sera confirmée au smoke test réel (``manage.py brightdata_smoke``).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SNAPSHOT_SOURCE = "brightdata"


def _first(item: dict, *keys: str):
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _as_dict_list(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, dict)]


def _map_position(raw: dict) -> dict:
    return {
        "title": _first(raw, "title", "position", "positions"),
        "company_name": _first(raw, "company", "company_name", "subtitle"),
        "company_urn": None,
        "location": _first(raw, "location", "location_name"),
        "date_range": None,  # start_date/end_date Bright Data = texte libre
        "description": _first(raw, "description", "description_html"),
        "urn": None,
    }


def _map_education(raw: dict) -> dict:
    return {
        "school_name": _first(raw, "title", "school", "school_name"),
        "degree_name": _first(raw, "degree", "degree_name"),
        "field_of_study": _first(raw, "field", "field_of_study"),
        "date_range": None,
        "urn": None,
    }


def map_record(record: dict) -> dict | None:
    """Un enregistrement Bright Data -> profile_snapshot interne.

    None si l'enregistrement est un item d'erreur (clé ``error`` ou
    ``warning_code`` sans données) — le lead reste intact, repli suivant.
    ``dead_page`` (profil supprimé) -> snapshot marqueur ``not_found`` comme
    le mapper Apify, pour la même cascade de disqualification.
    """
    if not isinstance(record, dict):
        return None
    if record.get("error") or record.get("error_code"):
        if "dead_page" in str(record.get("error_code", "")) or \
                "not found" in str(record.get("error", "")).lower():
            return {"not_found": True,
                    "public_identifier": _public_id(record)}
        logger.warning("Bright Data : item erreur (%s) — ignore",
                       record.get("error_code") or record.get("error"))
        return None

    pid = _public_id(record)
    if not pid:
        logger.warning("Bright Data : enregistrement sans URL/id exploitable — ignore")
        return None

    experience = _as_dict_list(record.get("experience"))
    full_name = _first(record, "name", "full_name")
    names = (full_name or "").split(None, 1)
    return {
        "url": _first(record, "url", "input_url", "linkedin_url"),
        "urn": None,
        "full_name": full_name,
        "first_name": _first(record, "first_name") or (names[0] if names else None),
        "last_name": _first(record, "last_name") or (names[1] if len(names) > 1 else None),
        # « position » = headline dans ce dataset ; "about" = résumé
        "headline": _first(record, "position", "headline"),
        "summary": _first(record, "about", "summary"),
        "public_identifier": pid,
        "location_name": _first(record, "city", "location", "region"),
        "geo": None,
        "industry": None,
        "country_code": _first(record, "country_code"),
        "supported_locales": [],
        "positions": [_map_position(p) for p in experience],
        "educations": [_map_education(e) for e in _as_dict_list(record.get("education"))],
        "connection_distance": None,
        "connection_degree": None,
        "source": SNAPSHOT_SOURCE,
    }


def _public_id(record: dict):
    from linkedin.url_utils import url_to_public_id

    direct = _first(record, "public_identifier", "id", "linkedin_id")
    if isinstance(direct, str) and direct and "/" not in direct \
            and not direct.startswith("ACo"):  # les ids internes ACoAA... ne sont pas des slugs
        return direct
    url = _first(record, "url", "input_url", "linkedin_url")
    if isinstance(url, str) and url:
        return url_to_public_id(url)
    return None
