"""Enrichissement des leads URL-only via Bright Data — service.

Fournisseur PRIMAIRE de la chaîne d'enrichissement cookieless (02/09).
Mêmes conventions que apify_enrich/service.py :
- comptage AVANT l'appel réseau, remboursement des échecs ;
- échec = lead laissé INTACT (le fournisseur suivant de la chaîne rattrape) ;
- profil introuvable (dead_page) = lead disqualifié (sort du backlog) ;
- AUCUNE incrémentation du read_guard (fetch cookieless, pas une lecture).

Garde-fous :
- Plafond MENSUEL ``EKOALU_BRIGHTDATA_MONTHLY_CAP`` (défaut 4500, sous les
  5 000 gratuits/mois) — on ne paie jamais sans décision explicite.
- Kill-switch ``EKOALU_BRIGHTDATA_ENRICH=0``.
"""
from __future__ import annotations

import logging
import os

import requests
from django.utils import timezone

from ekoalu.brightdata_enrich import client
from ekoalu.brightdata_enrich.mapper import map_record

logger = logging.getLogger(__name__)

DEFAULT_MONTHLY_CAP = 4500  # marge sous les 5 000 gratuits

SYNTHETIC_URL_PREFIX = "https://bdd-prospect.local/"


def is_enabled() -> bool:
    return os.environ.get("EKOALU_BRIGHTDATA_ENRICH", "1").lower() not in ("0", "false", "no")


def monthly_cap() -> int:
    try:
        return int(os.environ.get("EKOALU_BRIGHTDATA_MONTHLY_CAP", DEFAULT_MONTHLY_CAP))
    except (ValueError, TypeError):
        return DEFAULT_MONTHLY_CAP


def _month_key() -> str:
    return timezone.localdate().strftime("%Y-%m")


def used_this_month() -> int:
    from ekoalu.brightdata_enrich.models import BrightdataUsageMonth

    row = BrightdataUsageMonth.objects.filter(month=_month_key()).first()
    return row.count if row else 0


def remaining_this_month() -> int:
    return max(0, monthly_cap() - used_this_month())


def record_usage(n: int) -> None:
    from django.db.models import F

    from ekoalu.brightdata_enrich.models import BrightdataUsageMonth

    row, _created = BrightdataUsageMonth.objects.get_or_create(month=_month_key())
    BrightdataUsageMonth.objects.filter(pk=row.pk).update(count=F("count") + n)


def record_failures(n: int) -> None:
    if n <= 0:
        return
    from django.db.models import F, Value
    from django.db.models.functions import Greatest

    from ekoalu.brightdata_enrich.models import BrightdataUsageMonth

    row, _created = BrightdataUsageMonth.objects.get_or_create(month=_month_key())
    BrightdataUsageMonth.objects.filter(pk=row.pk).update(
        count=Greatest(F("count") - n, Value(0)),
        failed=F("failed") + n,
    )


def brightdata_ready() -> bool:
    """True si le chemin Bright Data est utilisable MAINTENANT."""
    return is_enabled() and client.is_configured() and remaining_this_month() > 0


def enrich_lead(lead) -> bool:
    """Enrichit UN lead. False = pas fait -> fournisseur suivant de la chaîne.

    Chemin du daemon : délai d'attente court (90 s). Un trigger + poll complet
    de 300 s pour un seul profil immobilisait la boucle de qualification.
    """
    results = enrich_leads([lead], poll_timeout=client.SINGLE_POLL_TIMEOUT_SECONDS)
    return results.get("enriched", 0) > 0


def enrich_leads(leads: list, *, poll_timeout: float | None = None) -> dict:
    """Enrichit un LOT de leads (1 trigger + 1 poll pour tout le lot).

    Retourne ``{selected, enriched, failed}``. Les leads non couverts par la
    réponse restent intacts (repli fournisseur suivant).
    """
    stats = {"selected": 0, "enriched": 0, "failed": 0}
    usable = [
        ld for ld in leads
        if "linkedin.com/in/" in (ld.linkedin_url or "")
        and not (ld.linkedin_url or "").startswith(SYNTHETIC_URL_PREFIX)
    ]
    if not usable or not brightdata_ready():
        return stats

    budget = min(len(usable), remaining_this_month())
    usable = usable[:budget]
    stats["selected"] = len(usable)
    record_usage(len(usable))
    try:
        records = client.run_profile_scraper(
            [ld.linkedin_url for ld in usable],
            poll_timeout=poll_timeout or client.poll_timeout_for(len(usable)),
        )
    except (client.BrightdataError, requests.RequestException) as exc:
        logger.warning(
            "Bright Data : run en échec, %d leads laissés intacts "
            "(fournisseur suivant de la chaîne) — %s", len(usable), exc,
        )
        stats["failed"] = len(usable)
        record_failures(stats["failed"])
        return stats

    by_pid = _snapshots_by_public_id(records)
    for lead in usable:
        if _apply_snapshot(lead, by_pid.get((lead.public_identifier or "").lower())):
            stats["enriched"] += 1
        else:
            stats["failed"] += 1
    record_failures(stats["failed"])
    if stats["selected"]:
        logger.info("Bright Data : %d/%d leads enrichis (%d échecs, %d/%d ce mois-ci)",
                    stats["enriched"], stats["selected"], stats["failed"],
                    used_this_month(), monthly_cap())
    return stats


def _snapshots_by_public_id(records: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for record in records:
        snap = map_record(record)
        if not snap:
            continue
        pid = snap.get("public_identifier")
        if pid:
            out[pid.lower()] = snap
    return out


def _apply_snapshot(lead, snap: dict | None) -> bool:
    """Même contrat que apify_enrich : not_found -> disqualification,
    succès -> snapshot + embedding, échec -> lead INTACT."""
    if snap and snap.get("not_found"):
        from ekoalu.lead_exclusion import disqualify_leads

        disqualify_leads(
            [lead.public_identifier],
            reason="Profil LinkedIn introuvable (Bright Data dead_page)",
            outcome="unresponsive",
        )
        logger.warning("Bright Data : profil INTROUVABLE pour %s — lead disqualifié",
                       lead.public_identifier)
        return False
    if not snap:
        return False
    now = timezone.now()
    snap = dict(snap)
    snap["fetched_at"] = now.isoformat()
    lead.profile_snapshot = snap
    lead.profile_snapshot_at = now
    lead.save(update_fields=["profile_snapshot", "profile_snapshot_at"])
    lead.embed_from_profile(snap)
    return True
