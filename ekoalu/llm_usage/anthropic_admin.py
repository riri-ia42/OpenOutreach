"""Client minimal pour Anthropic Admin API (consultation conso/cout).

Endpoints utilises (docs : docs.anthropic.com/en/api/admin-api/usage-cost) :
- GET /v1/organizations/usage_report/messages
- GET /v1/organizations/cost_report

Auth : header `x-api-key: sk-ant-admin01-...` -- Admin API Key separee de la cle
API normale (creee sur console.anthropic.com / Settings / Admin Keys, owner-only).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterator

import requests

logger = logging.getLogger(__name__)

ADMIN_API_BASE = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_TIMEOUT = 30


class AdminConfigError(RuntimeError):
    """Cle admin manquante."""


class AdminAPIError(RuntimeError):
    """Echec d'un appel a l'Admin API."""


@dataclass
class UsageBucket:
    """Une ligne de conso (1 jour, 1 modele, 1 cle API)."""

    starts_at: datetime
    ends_at: datetime
    model: str
    api_key_id: str
    workspace_id: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int


@dataclass
class CostBucket:
    """Une ligne de cout (1 jour, 1 modele)."""

    starts_at: datetime
    ends_at: datetime
    model: str
    workspace_id: str
    amount_usd: float


def _get_admin_key() -> str:
    key = os.environ.get("ANTHROPIC_ADMIN_API_KEY", "").strip()
    if not key:
        raise AdminConfigError(
            "ANTHROPIC_ADMIN_API_KEY manquante. Cree une Admin Key sur "
            "https://console.anthropic.com/settings/admin-keys"
        )
    return key


def _request(path: str, params: dict) -> dict:
    headers = {
        "x-api-key": _get_admin_key(),
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    resp = requests.get(
        f"{ADMIN_API_BASE}{path}",
        headers=headers,
        params=params,
        timeout=DEFAULT_TIMEOUT,
    )
    if not resp.ok:
        raise AdminAPIError(f"Admin API {path} -> {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _iter_paginated(path: str, params: dict) -> Iterator[dict]:
    """Itere sur les pages (has_more / page_token)."""
    page = 0
    next_token: str | None = None
    while True:
        page += 1
        if next_token:
            params["page"] = next_token
        data = _request(path, dict(params))
        for item in data.get("data", []):
            yield item
        if not data.get("has_more"):
            return
        next_token = data.get("next_page")
        if not next_token:
            return
        if page > 50:
            logger.warning("Admin API pagination capped at 50 pages on %s", path)
            return


def _parse_dt(s: str) -> datetime:
    """Parse ISO 8601 -> aware UTC datetime."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def fetch_usage_messages(start: date, end: date, bucket_width: str = "1d") -> list[UsageBucket]:
    """Recupere l'usage en tokens, agrege par jour (defaut) sur [start, end)."""
    starting_at = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    ending_at = datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    params = {
        "starting_at": starting_at,
        "ending_at": ending_at,
        "bucket_width": bucket_width,
        "group_by[]": ["model", "api_key_id", "workspace_id"],
        "limit": 1000,
    }
    buckets: list[UsageBucket] = []
    for item in _iter_paginated("/v1/organizations/usage_report/messages", params):
        for result in item.get("results", []):
            buckets.append(UsageBucket(
                starts_at=_parse_dt(item["starting_at"]),
                ends_at=_parse_dt(item["ending_at"]),
                model=result.get("model") or "",
                api_key_id=result.get("api_key_id") or "",
                workspace_id=result.get("workspace_id") or "",
                input_tokens=int(result.get("uncached_input_tokens", 0) or 0),
                output_tokens=int(result.get("output_tokens", 0) or 0),
                cache_creation_tokens=int(result.get("cache_creation_input_tokens", 0) or 0),
                cache_read_tokens=int(result.get("cache_read_input_tokens", 0) or 0),
            ))
    return buckets


def fetch_cost(start: date, end: date) -> list[CostBucket]:
    """Recupere le cout USD agrege par jour sur [start, end)."""
    starting_at = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    ending_at = datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    params = {
        "starting_at": starting_at,
        "ending_at": ending_at,
        "group_by[]": ["workspace_id", "description"],
        "limit": 1000,
    }
    buckets: list[CostBucket] = []
    for item in _iter_paginated("/v1/organizations/cost_report", params):
        for result in item.get("results", []):
            amount = result.get("amount") or {}
            buckets.append(CostBucket(
                starts_at=_parse_dt(item["starting_at"]),
                ends_at=_parse_dt(item["ending_at"]),
                model=result.get("description") or "",
                workspace_id=result.get("workspace_id") or "",
                amount_usd=float(amount.get("value", 0) or 0),
            ))
    return buckets


def default_window(days_back: int = 7) -> tuple[date, date]:
    """Fenetre par defaut : [aujourd'hui - N jours, demain) UTC."""
    today = datetime.now(timezone.utc).date()
    return today - timedelta(days=days_back), today + timedelta(days=1)
