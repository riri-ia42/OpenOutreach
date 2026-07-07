"""Mapping item acteur Apify -> format ``profile_snapshot`` interne.

Le format cible est celui produit par ``linkedin/api/voyager.py:
parse_linkedin_voyager_response`` (stocke dans ``Lead.profile_snapshot``) :
headline, positions[].company_name, location_name, etc. — c'est ce que
lisent le verdict LLM, les resumes follow-up et la generation de messages.

Le schema de sortie exact de l'acteur n'est PAS connu avant le test reel :
chaque champ est mappe defensivement (``dict.get`` sur plusieurs cles
candidates observees sur les profile-scrapers du store Apify), les champs
introuvables restent ``None``. Chaque choix de cle est a confirmer au test
reel (commentaires par champ ci-dessous).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SNAPSHOT_SOURCE = "apify"


def _first(item: dict, *keys: str):
    """Premiere valeur non vide parmi les cles candidates."""
    for key in keys:
        value = item.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _location_text(value):
    """Localisation acteur -> texte. HarvestAPI renvoie un dict
    ``{"linkedinText": "France", "parsed": {"text": ...}}`` (confirme au
    test reel 07/07) ; d'autres acteurs renvoient une chaine."""
    if isinstance(value, dict):
        return value.get("linkedinText") or (value.get("parsed") or {}).get("text")
    return value


def _map_position(raw: dict) -> dict:
    """Une experience acteur -> dict Position du snapshot Voyager."""
    return {
        # HarvestAPI : "position" (confirme au test reel 07/07)
        "title": _first(raw, "position", "title", "jobTitle"),
        # HarvestAPI : "companyName" (confirme au test reel 07/07)
        "company_name": _first(raw, "companyName", "company", "subtitle"),
        "company_urn": None,  # jamais fourni par un scraper public
        "location": _location_text(
            _first(raw, "location", "jobLocation", "locationName"),
        ),
        # dates acteur = texte libre ("Nov 2023 - Present") — pas de DateRange en V1
        "date_range": None,
        "description": _first(raw, "description", "descriptionHtml"),
        "urn": None,
    }


def _map_education(raw: dict) -> dict:
    """Une formation acteur -> dict Education du snapshot Voyager."""
    return {
        # HarvestAPI : "schoolName" / "degree" / "fieldOfStudy" (confirme 07/07)
        "school_name": _first(raw, "schoolName", "school", "title"),
        "degree_name": _first(raw, "degree", "degreeName", "subtitle"),
        "field_of_study": _first(raw, "fieldOfStudy", "field"),
        "date_range": None,  # texte libre ("2008 - 2009")
        "urn": None,
    }


def _country_code(item: dict):
    """HarvestAPI : countryCode vit dans le dict location (confirme 07/07)."""
    direct = _first(item, "countryCode", "country_code")
    if direct:
        return direct
    location = item.get("location")
    if isinstance(location, dict):
        return location.get("countryCode")
    return None


def _as_dict_list(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, dict)]


def map_actor_item(item: dict) -> dict:
    """Item JSON brut de l'acteur -> dict au format ``profile_snapshot``.

    Champs jamais fournis par un scraper cookieless (urn Voyager, degre de
    connexion) : ``None`` — les consommateurs du snapshot les tolerent deja.
    Cle supplementaire ``source: "apify"`` pour tracer la provenance.
    """
    # a confirmer au test reel : cle de l'URL du profil
    url = _first(item, "linkedinUrl", "url", "profileUrl", "inputUrl")
    # a confirmer au test reel : publicIdentifier direct ou derive de l'URL
    public_identifier = _first(item, "publicIdentifier", "publicId")
    if not public_identifier and url:
        from linkedin.url_utils import url_to_public_id
        public_identifier = url_to_public_id(url)

    first_name = _first(item, "firstName", "first_name")
    last_name = _first(item, "lastName", "last_name")
    full_name = _first(item, "fullName", "name", "full_name")
    if not full_name and (first_name or last_name):
        full_name = f"{first_name or ''} {last_name or ''}".strip()

    positions = [
        _map_position(p)
        # HarvestAPI : "experience" = parcours complet, "currentPosition" =
        # poste(s) en cours (confirme 07/07) — on prefere le parcours complet
        for p in _as_dict_list(_first(
            item, "experience", "experiences", "positions", "currentPosition",
        ))
    ]
    educations = [
        _map_education(e)
        # HarvestAPI : "profileTopEducation" (confirme 07/07)
        for e in _as_dict_list(_first(
            item, "education", "educations", "profileTopEducation", "schools",
        ))
    ]

    return {
        "url": url,
        "urn": None,  # urn Voyager inaccessible sans session — reste lazy
        "full_name": full_name,
        "first_name": first_name,
        "last_name": last_name,
        # HarvestAPI : "headline" (confirme 07/07)
        "headline": _first(item, "headline", "title"),
        # HarvestAPI : "about" (confirme 07/07)
        "summary": _first(item, "about", "summary", "description"),
        "public_identifier": public_identifier,
        # HarvestAPI : location est un dict {"linkedinText": ...} (confirme 07/07)
        "location_name": _location_text(_first(
            item, "location", "addressWithCountry", "geoLocationName", "city",
        )),
        "geo": None,
        "industry": None,
        "country_code": _country_code(item),
        "supported_locales": [],
        "positions": positions,
        "educations": educations,
        "connection_distance": None,  # sans session : pas de notion de degre
        "connection_degree": None,
        "source": SNAPSHOT_SOURCE,
    }


def snapshot_completeness(snapshot: dict) -> tuple[int, int, list[str]]:
    """(remplis, total, manquants) sur les champs cles vs snapshot Voyager.

    Critere GO/NO-GO du test reel : ces champs alimentent le verdict LLM et
    la generation de messages — s'ils sont majoritairement vides, l'acteur
    ne remplace pas la lecture Voyager.
    """
    key_fields = [
        "full_name", "headline", "summary", "location_name",
        "public_identifier", "positions",
    ]
    missing = [f for f in key_fields if not snapshot.get(f)]
    return len(key_fields) - len(missing), len(key_fields), missing
