"""Client de recherche Google via Serper.dev.

Remplace Google Custom Search JSON API (fermee aux nouveaux clients par Google,
verdict 11/06/2026 — voir PROGRESS.md). Serper renvoie les vrais resultats
Google : meme couverture linkedin.com/in, format JSON simple.

Tarif : 2 500 requetes d'essai gratuites, puis credits (50 $ / 50 000).
1 credit par requete avec num<=10. Isolable pour les tests : ``search_raw``
est le seul appel reseau.
"""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

ENDPOINT = "https://google.serper.dev/search"
MAX_PAGE = 10  # ~100 resultats max par requete, comme l'ancienne API


def _config() -> str:
    return os.environ.get("SERPER_API_KEY", "").strip()


def is_configured() -> bool:
    return bool(_config())


def search_raw(query: str, num: int = 10, page: int = 1, timeout: int = 20) -> list[dict]:
    """Appel brut a Serper. Renvoie la liste ``organic`` (peut etre vide).

    Chaque item a au moins ``link``/``title``/``snippet``. Seul point reseau
    du module -> c'est lui qu'on mocke dans les tests.
    """
    key = _config()
    if not key:
        raise RuntimeError("SERPER_API_KEY manquant")
    payload = {
        "q": query,
        "num": min(max(num, 1), 10),  # num<=10 : 1 credit par requete
        "page": max(page, 1),
        "gl": "fr",
        "hl": "fr",
    }
    resp = requests.post(
        ENDPOINT, json=payload, timeout=timeout,
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
    )
    resp.raise_for_status()
    return resp.json().get("organic", []) or []


def search_linkedin_profiles(query: str, max_results: int = 10) -> list[str]:
    """URLs de profils LinkedIn ``/in/`` pour la requete, dedupliquees par public_id."""
    from linkedin.url_utils import url_to_public_id

    urls: list[str] = []
    seen: set[str] = set()
    page = 1
    while len(urls) < max_results and page <= MAX_PAGE:
        items = search_raw(query, num=10, page=page)
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
        page += 1
    return urls
