"""Client HTTP Apify — run synchrone d'un acteur profile-scraper cookieless.

API v2 ``run-sync-get-dataset-items`` : POST de l'input acteur, la reponse
est directement la liste des items du dataset (pas de polling). Doc :
https://docs.apify.com/api/v2#/reference/actors/run-actor-synchronously

Config (env) :
- ``EKOALU_APIFY_TOKEN`` : token API du compte Apify (Settings > API tokens).
- ``EKOALU_APIFY_ACTOR`` : id de l'acteur, defaut ``DEFAULT_ACTOR``.

Seul point reseau du module : ``run_profile_scraper`` -> c'est lui qu'on
mocke dans les tests. JAMAIS de cookie LinkedIn dans le payload.
"""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.apify.com/v2"

# Acteur cookieless pressenti (~8 $/1000 profils, pas de cookie demande).
# A CONFIRMER AU TEST REEL : l'id exact et son schema d'input/output seront
# valides au premier run paye (cf. docs/APIFY_ENRICH.md). Surchargeable via
# EKOALU_APIFY_ACTOR sans toucher au code.
DEFAULT_ACTOR = "dev_fusion~linkedin-profile-scraper"

# Ordre de grandeur annonce par les acteurs cookieless du store Apify.
# A confirmer au test reel (facture Apify apres le run 10-20 profils).
ESTIMATED_COST_PER_PROFILE_USD = 0.008

# Timeout du run acteur cote Apify (secondes). Le timeout HTTP local est
# legerement superieur pour laisser l'API repondre proprement.
DEFAULT_RUN_TIMEOUT_S = 300


def _token() -> str:
    return os.environ.get("EKOALU_APIFY_TOKEN", "").strip()


def is_configured() -> bool:
    return bool(_token())


def actor_id() -> str:
    """Id de l'acteur au format URL Apify (``owner~name``)."""
    raw = os.environ.get("EKOALU_APIFY_ACTOR", "").strip() or DEFAULT_ACTOR
    return raw.replace("/", "~")


def build_input(urls: list[str]) -> dict:
    """Input acteur pour une liste d'URLs de profils LinkedIn PUBLICS.

    COOKIELESS PAR CONSTRUCTION : aucune cle cookie/li_at/session ici, et il
    ne doit JAMAIS y en avoir — on n'envoie que des URLs publiques.
    Cle ``profileUrls`` : a confirmer au test reel (schema d'input de
    l'acteur retenu) ; c'est la convention des profile-scrapers du store.
    """
    clean: list[str] = []
    for url in urls:
        url = (url or "").strip()
        if "linkedin.com/in/" not in url:
            raise ValueError(f"URL non-profil LinkedIn refusee : {url!r}")
        clean.append(url)
    if not clean:
        raise ValueError("Aucune URL de profil fournie")
    return {"profileUrls": clean}


def run_profile_scraper(
    urls: list[str], timeout_s: int = DEFAULT_RUN_TIMEOUT_S,
) -> list[dict]:
    """Run synchrone de l'acteur sur ``urls`` -> liste d'items JSON bruts.

    Leve ``RuntimeError`` avec un message actionnable si le token manque ou
    si l'API repond en erreur (token invalide, credit epuise, acteur inconnu).
    """
    token = _token()
    if not token:
        raise RuntimeError(
            "EKOALU_APIFY_TOKEN manquant dans l'environnement : creer le compte "
            "Apify + token puis renseigner .env (cf. docs/APIFY_ENRICH.md). "
            "Utiliser --dry-run pour tester sans token.",
        )

    endpoint = f"{API_BASE}/acts/{actor_id()}/run-sync-get-dataset-items"
    payload = build_input(urls)
    logger.info("Apify run: acteur=%s profils=%d", actor_id(), len(urls))
    resp = requests.post(
        endpoint,
        json=payload,
        params={"timeout": timeout_s, "format": "json"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout_s + 30,
    )
    if not resp.ok:
        raise RuntimeError(
            f"Apify HTTP {resp.status_code} sur {actor_id()} : "
            f"{resp.text[:300]} (token invalide ? credit epuise ? "
            "id acteur a verifier via EKOALU_APIFY_ACTOR)",
        )
    items = resp.json()
    if not isinstance(items, list):
        raise RuntimeError(f"Reponse Apify inattendue (pas une liste) : {str(items)[:200]}")
    logger.info("Apify run OK : %d items", len(items))
    return items
