"""Liste d'exclusion partagée avec le projet mailing-mailjet (LOT D 07/07).

Fichier commun `_partage/exclusions.json` (bounces + unsubscribes, ~2000
entrées, alimenté par mailing-mailjet), promis « lu avant tout envoi » par le
CLAUDE.md projet. Consommé par :
- `email_canal/sender._resolve_recipient` (blocage à l'envoi)
- `generate_cold_emails` (skip génération)
- `import_mailjet_hot_leads` (skip import)

Format : {"exclusions": [{"email", "reason", "source", "added_at"}]}.
Tolérant : fichier absent/illisible => warning + liste vide (le canal email ne
doit pas tomber parce qu'un AUTRE projet a déplacé/cassé le fichier).
Comparaison email lowercase. Cache mémoire TTL court (60 s).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = "C:/Users/RI.GROS/Documents/CLAUDE/_partage/exclusions.json"
ENV_VAR = "EKOALU_SHARED_EXCLUSIONS_PATH"
CACHE_TTL_SECONDS = 60.0

_cache: tuple[float, frozenset[str]] | None = None


def exclusions_path() -> Path:
    """Chemin du fichier partagé (surchargable via EKOALU_SHARED_EXCLUSIONS_PATH)."""
    return Path(os.environ.get(ENV_VAR) or DEFAULT_PATH)


def _load() -> frozenset[str]:
    path = exclusions_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning(
            "Liste d'exclusion partagée introuvable (%s) — AUCUNE exclusion appliquée",
            path,
        )
        return frozenset()
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Liste d'exclusion partagée illisible (%s : %s) — AUCUNE exclusion appliquée",
            path, exc,
        )
        return frozenset()
    entries = raw.get("exclusions", []) if isinstance(raw, dict) else []
    emails = {
        str(entry.get("email") or "").strip().lower()
        for entry in entries
        if isinstance(entry, dict)
    }
    emails.discard("")
    return frozenset(emails)


def excluded_emails(*, refresh: bool = False) -> frozenset[str]:
    """Set (lowercase) des emails exclus, avec cache TTL court."""
    global _cache
    now = time.monotonic()
    if not refresh and _cache is not None and now - _cache[0] < CACHE_TTL_SECONDS:
        return _cache[1]
    emails = _load()
    _cache = (now, emails)
    return emails


def is_excluded(email: str | None) -> bool:
    """True si l'email figure dans la liste d'exclusion partagée."""
    if not email:
        return False
    return email.strip().lower() in excluded_emails()
