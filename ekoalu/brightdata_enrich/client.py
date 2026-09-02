"""Client Bright Data Web Scraper API — dataset « LinkedIn people profiles ».

Décision Richard 02/09 : fournisseur PRIMAIRE d'enrichissement cookieless.
Free tier : 5 000 enregistrements/mois, renouvelés le 1er (pas un essai) ;
au-delà 1,50 $/1000 en pay-as-you-go. Le plus solide juridiquement du marché
(jugements Meta 01/2024 et X 05/2024 en sa faveur).

API asynchrone en 2 temps (docs.brightdata.com, vérifié 02/09/2026) :
1. POST /datasets/v3/trigger?dataset_id=...  body [{"url": ...}, ...]
   -> {"snapshot_id": "s_..."}
2. Poll GET /datasets/v3/progress/{snapshot_id} jusqu'à status=ready
   puis GET /datasets/v3/snapshot/{snapshot_id}?format=json -> [records]

RÈGLE ABSOLUE inchangée : URLs publiques uniquement, jamais notre cookie.
Seul point réseau du module : requests -> mocké dans les tests.
"""
from __future__ import annotations

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

BASE = "https://api.brightdata.com/datasets/v3"
# Dataset officiel « LinkedIn people profiles » (collect by URL).
DEFAULT_DATASET_ID = "gd_l1viktl72bvl7bjuj0"

POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_SECONDS = 300  # les petits lots sortent en 1-3 min


class BrightdataError(RuntimeError):
    """Erreur Bright Data non transitoire (auth, quota, snapshot failed)."""


def _token() -> str:
    return os.environ.get("EKOALU_BRIGHTDATA_TOKEN", "").strip()


def dataset_id() -> str:
    return os.environ.get("EKOALU_BRIGHTDATA_DATASET", DEFAULT_DATASET_ID).strip()


def is_configured() -> bool:
    return bool(_token())


def _headers() -> dict:
    return {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}


def trigger(urls: list[str], timeout: int = 30) -> str:
    """Déclenche la collecte. Renvoie le snapshot_id."""
    if not urls:
        raise BrightdataError("aucune URL a collecter")
    resp = requests.post(
        f"{BASE}/trigger",
        params={"dataset_id": dataset_id(), "include_errors": "true"},
        json=[{"url": u} for u in urls],
        headers=_headers(),
        timeout=timeout,
    )
    if resp.status_code in (401, 403):
        raise BrightdataError(f"auth refusee ({resp.status_code}) — verifier EKOALU_BRIGHTDATA_TOKEN")
    resp.raise_for_status()
    snapshot_id = (resp.json() or {}).get("snapshot_id")
    if not snapshot_id:
        raise BrightdataError(f"reponse trigger sans snapshot_id : {resp.text[:200]}")
    return snapshot_id


def _progress(snapshot_id: str, timeout: int = 30) -> str:
    resp = requests.get(f"{BASE}/progress/{snapshot_id}", headers=_headers(), timeout=timeout)
    resp.raise_for_status()
    return (resp.json() or {}).get("status", "")


def fetch_snapshot(snapshot_id: str, timeout: int = 60) -> list[dict]:
    """Récupère les enregistrements d'un snapshot prêt."""
    resp = requests.get(
        f"{BASE}/snapshot/{snapshot_id}",
        params={"format": "json"},
        headers=_headers(),
        timeout=timeout,
    )
    if resp.status_code == 202:
        return []  # pas encore prêt (l'appelant re-poll)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else [data]


def run_profile_scraper(urls: list[str],
                        poll_interval: float = POLL_INTERVAL_SECONDS,
                        poll_timeout: float = POLL_TIMEOUT_SECONDS) -> list[dict]:
    """Collecte synchrone (trigger + poll) d'un lot d'URLs de profils.

    Lève BrightdataError sur échec définitif, requests.RequestException sur
    panne réseau — mêmes familles d'erreurs que le client Apify, pour que
    la chaîne d'enrichissement les traite uniformément.
    """
    snapshot_id = trigger(urls)
    deadline = time.monotonic() + poll_timeout
    while time.monotonic() < deadline:
        status = _progress(snapshot_id)
        if status == "ready":
            return fetch_snapshot(snapshot_id)
        if status in ("failed", "canceled"):
            raise BrightdataError(f"snapshot {snapshot_id} en statut {status}")
        time.sleep(poll_interval)
    raise BrightdataError(
        f"snapshot {snapshot_id} pas pret apres {int(poll_timeout)}s (statut poll)")


def estimated_cost_per_profile_usd() -> float:
    """1,50 $/1000 au-delà du free tier — 0 tant qu'on reste sous les 5 000/mois.
    On retourne le tarif payant pour rester conservateur dans les stats."""
    return 0.0015
