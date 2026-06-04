"""Client Google Custom Search JSON API.

Quota gratuit : 100 requetes/jour. 10 resultats max par requete (pagination via
``start``). Isolable pour les tests : ``search_raw`` est le seul appel reseau.
"""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

ENDPOINT = "https://www.googleapis.com/customsearch/v1"
MAX_START = 91  # l'API plafonne a 100 resultats (start 1..91 par pas de 10)


def _config() -> tuple[str, str]:
    return (
        os.environ.get("GOOGLE_CSE_API_KEY", "").strip(),
        os.environ.get("GOOGLE_CSE_CX", "").strip(),
    )


def is_configured() -> bool:
    key, cx = _config()
    return bool(key and cx)


def search_raw(query: str, num: int = 10, start: int = 1, timeout: int = 20) -> list[dict]:
    """Appel brut a Custom Search. Renvoie la liste ``items`` (peut etre vide).

    Seul point reseau du module -> c'est lui qu'on mocke dans les tests.
    """
    key, cx = _config()
    if not (key and cx):
        raise RuntimeError("GOOGLE_CSE_API_KEY / GOOGLE_CSE_CX manquants")
    params = {
        "key": key, "cx": cx, "q": query,
        "num": min(max(num, 1), 10), "start": start,
    }
    resp = requests.get(ENDPOINT, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("items", []) or []


def search_linkedin_profiles(query: str, max_results: int = 10) -> list[str]:
    """URLs de profils LinkedIn ``/in/`` pour la requete, dedupliquees par public_id."""
    from linkedin.url_utils import url_to_public_id

    urls: list[str] = []
    seen: set[str] = set()
    start = 1
    while len(urls) < max_results and start <= MAX_START:
        items = search_raw(query, num=10, start=start)
        if not items:
            break
        for it in items:
            link = (it.get("link") or "").strip()
            if "/in/" not in link:
                continue
            pid = url_to_public_id(link)
            if not pid or pid in seen:
                continue
            seen.add(pid)
            urls.append(link)
            if len(urls) >= max_results:
                break
        start += 10
    return urls
