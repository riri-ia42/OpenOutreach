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

# Acteur cookieless par defaut. Bascule 15/07 (GO Richard) : apimaestro accepte
# les comptes Apify FREE (teste en reel, 2 runs + echantillon 8/9), 0,005 $/
# profil pris sur le credit gratuit 5 $/mois = ZERO abonnement. HarvestAPI
# (4 $/1000) ecarte : essai limite a 20 runs cumules pour les comptes Free
# (panne totale constatee du 10 au 15/07) ; dev_fusion refuse l'API sur plan
# Free. Surchargeable via EKOALU_APIFY_ACTOR sans toucher au code.
DEFAULT_ACTOR = "apimaestro~linkedin-profile-batch-scraper-no-cookies-required"

# Mode facture HarvestAPI 4 $/1000 (sans email), utilise si EKOALU_APIFY_ACTOR
# repasse sur harvestapi (ex. limite 20 runs rearmee au cycle mensuel).
HARVESTAPI_MODE = "Profile details no email ($4 per 1k)"

# Timeout du run acteur cote Apify (secondes). Le timeout HTTP local est
# legerement superieur pour laisser l'API repondre proprement.
DEFAULT_RUN_TIMEOUT_S = 300


def estimated_cost_per_profile_usd() -> float:
    """Cout facture par profil selon l'acteur (fiche store, factures reelles)."""
    return 0.004 if "harvestapi" in actor_id() else 0.005


def batch_size() -> int:
    """Taille de lot par run-sync (endpoint plafonne ~300s cote Apify).

    HarvestAPI depassait la fenetre au-dela de 5 profils (dataset tronque,
    constate 07/07). apimaestro (batch natif) traite 10 profils bien sous les
    300s (teste en reel 15/07).
    """
    return 5 if "harvestapi" in actor_id() else 10


class ApifyDailyLimitError(RuntimeError):
    """L'acteur refuse : limite du free-tier atteinte (se rearme demain).

    Constate en reel 15/07 : apimaestro plafonne les comptes Apify FREE a
    10 profils/JOUR (« wait until tomorrow ») ; harvestapi a 20 runs cumules.
    Chaque tentative apres la limite est FACTUREE (~0,005 $/item d'erreur) —
    l'appelant doit saturer le plafond du jour pour arreter les frais.
    """


def _is_free_tier_limit(msg: str) -> bool:
    low = (msg or "").lower()
    return "free" in low and "limit" in low


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
    Schema selon l'acteur : apimaestro attend ``usernames`` (+ ``includeEmail``
    False — pas d'email enrichi, le point RGPD sensible est evite) ;
    HarvestAPI attend ``queries`` + le mode de facturation ; les autres
    profile-scrapers du store utilisent ``profileUrls`` (convention dev_fusion).
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
    if "apimaestro" in actor_id():
        return {"usernames": clean, "includeEmail": False}
    if "harvestapi" in actor_id():
        return {"profileScraperMode": HARVESTAPI_MODE, "queries": clean}
    return {"profileUrls": clean}


def run_profile_scraper(
    urls: list[str], timeout_s: int = DEFAULT_RUN_TIMEOUT_S,
) -> list[dict]:
    """Run de l'acteur sur ``urls`` (par lots de ``batch_size()``) -> items JSON.

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
    limit_hit = False
    size = batch_size()
    for start in range(0, len(urls), size):
        batch = urls[start:start + size]
        for item in _run_batch(batch, token, timeout_s):
            error = _actor_error(item)
            if error:
                errors.append(error)
                logger.warning("Item d'erreur acteur Apify : %s", error)
                limit_hit = limit_hit or _is_free_tier_limit(error)
            else:
                items.append(item)
        if limit_hit:
            break  # inutile (et payant) d'envoyer les lots suivants aujourd'hui
    if limit_hit and not items:
        raise ApifyDailyLimitError(f"Apify : limite free-tier — {errors[0][:200]}")
    if errors and not items:
        raise RuntimeError(f"Apify : que des erreurs acteur — {errors[0][:200]}")
    logger.info("Apify run OK : %d items (%d erreurs acteur)", len(items), len(errors))
    return items


def _actor_error(item) -> str | None:
    """Message d'erreur si ``item`` est un item d'erreur acteur A ECARTER.

    Formes observees en reel : HarvestAPI ``{"error": "..."}`` (seule cle) ;
    apimaestro ``{"message": "No profile found or wrong input", "profileUrl",
    "profile_input"}`` (pas de ``basic_info``).

    Un not-found apimaestro AVEC URL n'est PAS ecarte : il traverse jusqu'au
    service, qui disqualifie le lead (profil LinkedIn supprime/renomme =
    inutilisable ; sinon il reste en tete du backlog et est re-facture
    chaque jour — constate au smoke du 15/07).
    """
    if not isinstance(item, dict):
        return f"item non-dict : {str(item)[:100]}"
    if set(item) == {"error"}:
        return str(item["error"])
    if "message" in item and "basic_info" not in item and "headline" not in item:
        if item.get("profile_input") or item.get("profileUrl"):
            return None  # not-found cible : laisse passer vers le service
        return str(item.get("message"))
    return None


def _run_batch(batch: list[str], token: str, timeout_s: int) -> list[dict]:
    """Un appel run-sync sur un lot (<= batch_size(), tient dans les ~300s)."""
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
