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

# Acteur cookieless par defaut (4 $/1000 profils, "No cookies or account
# required"). Choisi au test reel du 07/07 : dev_fusion~linkedin-profile-scraper
# refuse les runs API sur le plan Apify Free ("run through the UI only") ;
# HarvestAPI accepte l'API et coute 2,5x moins cher. Surchargeable via
# EKOALU_APIFY_ACTOR sans toucher au code.
DEFAULT_ACTOR = "harvestapi~linkedin-profile-scraper"

# Mode facture 4 $/1000 (sans decouverte d'email — inutile ici, on a deja
# l'URL LinkedIn et le canal email a sa propre source BDD PROSPECT).
HARVESTAPI_MODE = "Profile details no email ($4 per 1k)"

# 4 $/1000 profils (mode "no email" HarvestAPI). Confirme sur la fiche store ;
# la facture reelle du run de test fait foi (cf. docs/APIFY_ENRICH.md).
ESTIMATED_COST_PER_PROFILE_USD = 0.004

# Timeout du run acteur cote Apify (secondes). Le timeout HTTP local est
# legerement superieur pour laisser l'API repondre proprement.
DEFAULT_RUN_TIMEOUT_S = 300

# L'endpoint run-sync est plafonne a ~300s cote Apify : 15 profils d'un coup
# depassent la fenetre et rendent un dataset tronque (constate au test reel
# 07/07 : 1 item au lieu de 15). On decoupe donc en lots.
BATCH_SIZE = 5


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
    Schema selon l'acteur : HarvestAPI attend ``queries`` + le mode de
    facturation ; les autres profile-scrapers du store utilisent
    ``profileUrls`` (convention dev_fusion et similaires).
    """
    from urllib.parse import unquote

    clean: list[str] = []
    for url in urls:
        # Les URLs Serper arrivent percent-encodees (fran%C3%A7ois...) —
        # decodees avant envoi pour matcher le public_identifier LinkedIn.
        url = unquote((url or "").strip())
        if "linkedin.com/in/" not in url:
            raise ValueError(f"URL non-profil LinkedIn refusee : {url!r}")
        clean.append(url)
    if not clean:
        raise ValueError("Aucune URL de profil fournie")
    if "harvestapi" in actor_id():
        return {"profileScraperMode": HARVESTAPI_MODE, "queries": clean}
    return {"profileUrls": clean}


def run_profile_scraper(
    urls: list[str], timeout_s: int = DEFAULT_RUN_TIMEOUT_S,
) -> list[dict]:
    """Run de l'acteur sur ``urls`` (par lots de ``BATCH_SIZE``) -> items JSON.

    Leve ``RuntimeError`` avec un message actionnable si le token manque, si
    l'API repond en erreur (token invalide, credit epuise, acteur inconnu) ou
    si l'acteur ne renvoie QUE des items d'erreur (ex. plan Free refuse).
    Les items d'erreur isoles sont ecartes avec un warning.
    """
    token = _token()
    if not token:
        raise RuntimeError(
            "EKOALU_APIFY_TOKEN manquant dans l'environnement : creer le compte "
            "Apify + token puis renseigner .env (cf. docs/APIFY_ENRICH.md). "
            "Utiliser --dry-run pour tester sans token.",
        )

    items: list[dict] = []
    errors: list[str] = []
    for start in range(0, len(urls), BATCH_SIZE):
        batch = urls[start:start + BATCH_SIZE]
        for item in _run_batch(batch, token, timeout_s):
            if isinstance(item, dict) and set(item) == {"error"}:
                errors.append(str(item["error"]))
                logger.warning("Item d'erreur acteur Apify : %s", item["error"])
            else:
                items.append(item)
    if errors and not items:
        raise RuntimeError(f"Apify : que des erreurs acteur — {errors[0][:200]}")
    logger.info("Apify run OK : %d items (%d erreurs acteur)", len(items), len(errors))
    return items


def _run_batch(batch: list[str], token: str, timeout_s: int) -> list[dict]:
    """Un appel run-sync sur un lot (<= BATCH_SIZE, tient dans les ~300s)."""
    endpoint = f"{API_BASE}/acts/{actor_id()}/run-sync-get-dataset-items"
    payload = build_input(batch)
    logger.info("Apify run: acteur=%s profils=%d", actor_id(), len(batch))
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
    return items
