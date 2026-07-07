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


def _map_position(raw: dict) -> dict:
    """Une experience acteur -> dict Position du snapshot Voyager."""
    return {
        # a confirmer au test reel : cle du titre de poste
        "title": _first(raw, "title", "position", "jobTitle"),
        # a confirmer au test reel : cle du nom d'entreprise
        "company_name": _first(raw, "companyName", "company", "subtitle"),
        "company_urn": None,  # jamais fourni par un scraper public
        # a confirmer au test reel : cle de la localisation du poste
        "location": _first(raw, "location", "jobLocation", "locationName"),
        # a confirmer au test reel : les dates acteur sont souvent du texte
        # libre ("Jan 2020 - Present") — pas mappe vers DateRange en V1
        "date_range": None,
        "description": _first(raw, "description", "descriptionHtml"),
        "urn": None,
    }


def _map_education(raw: dict) -> dict:
    """Une formation acteur -> dict Education du snapshot Voyager."""
    return {
        # a confirmer au test reel : cle du nom d'ecole
        "school_name": _first(raw, "schoolName", "school", "title"),
        "degree_name": _first(raw, "degreeName", "degree", "subtitle"),
        "field_of_study": _first(raw, "fieldOfStudy", "field"),
        "date_range": None,  # a confirmer au test reel (texte libre)
        "urn": None,
    }


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
        # a confirmer au test reel : cle de la liste d'experiences
        for p in _as_dict_list(_first(item, "experiences", "positions", "experience"))
    ]
    educations = [
        _map_education(e)
        # a confirmer au test reel : cle de la liste de formations
        for e in _as_dict_list(_first(item, "educations", "education", "schools"))
    ]

    return {
        "url": url,
        "urn": None,  # urn Voyager inaccessible sans session — reste lazy
        "full_name": full_name,
        "first_name": first_name,
        "last_name": last_name,
        # a confirmer au test reel : cle du headline
        "headline": _first(item, "headline", "title"),
        # a confirmer au test reel : cle du resume/about
        "summary": _first(item, "about", "summary", "description"),
        "public_identifier": public_identifier,
        # a confirmer au test reel : cle de la localisation profil
        "location_name": _first(
            item, "addressWithCountry", "location", "geoLocationName", "city",
        ),
        "geo": None,
        "industry": None,  # a confirmer au test reel : parfois "industry" (str)
        "country_code": _first(item, "countryCode", "country_code"),
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
