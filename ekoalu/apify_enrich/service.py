"""Enrichissement des leads URL-only via Apify (cookieless) — service.

Cablage du 07/07 (GO Richard) : les lectures de fiche pour l'EMBED /
QUALIFICATION des leads sources passent par Apify au lieu de Voyager —
zero empreinte sur le compte LinkedIn de Richard, qui ne sert plus qu'a
ENGAGER (visites pre-invitation, invitations, messages, degres, sondes).

Garde-fous :
- Plafond quotidien de depense : env ``EKOALU_APIFY_DAILY_CAP`` (defaut 40),
  compteur DB ApifyUsageDay — on compte les TENTATIVES avant l'appel reseau,
  comme le read_guard.
- Kill-switch global : ``EKOALU_APIFY_ENRICH=0`` -> service inactif.
- AUCUNE incrementation du read_guard : un fetch Apify n'est PAS une lecture
  du compte (on ne passe jamais par PlaywrightLinkedinAPI.get_profile).
- Echec Apify (reseau, erreur acteur, profil introuvable) = lead laisse
  INTACT : le repli Voyager du daemon le rattrapera.
"""
from __future__ import annotations

import logging
import os

from django.utils import timezone

from ekoalu.apify_enrich import client
from ekoalu.apify_enrich.mapper import map_actor_item

logger = logging.getLogger(__name__)

DEFAULT_DAILY_CAP = 40

# Leads mail-only BDD PROSPECT : URL synthetique, aucun profil a scraper.
SYNTHETIC_URL_PREFIX = "https://bdd-prospect.local/"


def is_enabled() -> bool:
    """Kill-switch global : EKOALU_APIFY_ENRICH=0 desactive le service."""
    return os.environ.get("EKOALU_APIFY_ENRICH", "1").lower() not in ("0", "false", "no")


def daily_cap() -> int:
    try:
        return int(os.environ.get("EKOALU_APIFY_DAILY_CAP", DEFAULT_DAILY_CAP))
    except (ValueError, TypeError):
        return DEFAULT_DAILY_CAP


def used_today() -> int:
    from ekoalu.apify_enrich.models import ApifyUsageDay

    row = ApifyUsageDay.objects.filter(date=timezone.localdate()).first()
    return row.count if row else 0


def remaining_today() -> int:
    return max(0, daily_cap() - used_today())


def record_usage(n: int) -> None:
    """Compte ``n`` profils envoyes a Apify (AVANT l'appel reseau : on compte
    les tentatives, comme le read_guard ; les echecs sont rembourses apres
    coup par ``record_failures``)."""
    from django.db.models import F

    from ekoalu.apify_enrich.models import ApifyUsageDay

    row, _created = ApifyUsageDay.objects.get_or_create(date=timezone.localdate())
    ApifyUsageDay.objects.filter(pk=row.pk).update(count=F("count") + n)


def record_failures(n: int) -> None:
    """Rembourse ``n`` tentatives en echec du plafond + trace la panne.

    15/07 : l'actor HarvestAPI (plan Apify Free) s'est mis a echouer a 100 %
    (limite 20 runs) — les 40 tentatives/jour saturaient le plafond pour RIEN
    (le daemon croyait le cap atteint et repliait sur Voyager toute la
    journee) et la panne restait invisible. Un echec ne coute quasi rien cote
    Apify (facturation au resultat) : on le sort de ``count`` (plancher 0) et
    on l'accumule dans ``failed`` (consomme par la preco du recap du soir).
    """
    if n <= 0:
        return
    from django.db.models import F, Value
    from django.db.models.functions import Greatest

    from ekoalu.apify_enrich.models import ApifyUsageDay

    row, _created = ApifyUsageDay.objects.get_or_create(date=timezone.localdate())
    ApifyUsageDay.objects.filter(pk=row.pk).update(
        count=Greatest(F("count") - n, Value(0)),
        failed=F("failed") + n,
    )


def failed_today() -> int:
    from ekoalu.apify_enrich.models import ApifyUsageDay

    row = ApifyUsageDay.objects.filter(date=timezone.localdate()).first()
    return row.failed if row else 0


def apify_ready() -> bool:
    """True si le chemin Apify-first est utilisable MAINTENANT :
    token configure + pas kill-switche + plafond quotidien non atteint."""
    return is_enabled() and client.is_configured() and remaining_today() > 0


def candidate_leads(limit: int) -> list:
    """Leads URL-only a enrichir, plus anciens d'abord (backlog FIFO).

    Criteres : pas de snapshot, pas d'embedding, non disqualifie, URL de
    profil LinkedIn reelle (les mail-only ``bdd-prospect.local`` sont exclus),
    decouvert (LeadDiscovery) par au moins une campagne ACTIVE.
    """
    from crm.models import Lead

    if limit <= 0:
        return []
    return list(
        Lead.objects.filter(
            profile_snapshot__isnull=True,
            embedding__isnull=True,
            disqualified=False,
            linkedin_url__contains="linkedin.com/in/",
            discoveries__campaign__active=True,
        )
        .exclude(linkedin_url__startswith=SYNTHETIC_URL_PREFIX)
        .distinct()
        .order_by("creation_date")[:limit]
    )


def enrich_urlonly_leads(max_leads: int) -> dict:
    """Enrichit jusqu'a ``max_leads`` leads URL-only via Apify.

    S'arrete proprement au plafond quotidien. Retourne un dict de stats :
    enabled / selected / enriched / failed / cap / used_today / cost_estimated_usd.
    """
    stats = {
        "enabled": True, "selected": 0, "enriched": 0, "failed": 0,
        "cap": daily_cap(), "used_today": used_today(), "cost_estimated_usd": 0.0,
    }
    if not is_enabled():
        stats["enabled"] = False
        logger.info("Apify enrich inactif (kill-switch EKOALU_APIFY_ENRICH=0)")
        return stats

    budget = min(max_leads, remaining_today())
    if budget <= 0:
        logger.info(
            "Apify enrich : plafond quotidien atteint (%d/%d) — arret propre",
            stats["used_today"], stats["cap"],
        )
        return stats

    leads = candidate_leads(budget)
    stats["selected"] = len(leads)
    if not leads:
        logger.info("Apify enrich : aucun lead URL-only en backlog")
        return stats

    record_usage(len(leads))
    stats["cost_estimated_usd"] = round(
        len(leads) * client.ESTIMATED_COST_PER_PROFILE_USD, 4,
    )
    _run_and_apply(leads, stats)
    stats["used_today"] = used_today()
    logger.info(
        "Apify enrich : %d/%d leads enrichis (%d echecs, ~%.3f $)",
        stats["enriched"], stats["selected"], stats["failed"],
        stats["cost_estimated_usd"],
    )
    return stats


def _run_and_apply(leads: list, stats: dict) -> None:
    """Run acteur sur le lot + application des snapshots (stats en place)."""
    try:
        items = client.run_profile_scraper([ld.linkedin_url for ld in leads])
    except RuntimeError as exc:
        logger.warning(
            "Apify enrich : run en echec, %d leads laisses intacts "
            "(le repli Voyager du daemon les rattrapera) — %s", len(leads), exc,
        )
        stats["failed"] = len(leads)
        record_failures(stats["failed"])
        return
    by_pid = _snapshots_by_public_id(items)
    for lead in leads:
        if _apply_snapshot(lead, by_pid.get((lead.public_identifier or "").lower())):
            stats["enriched"] += 1
        else:
            stats["failed"] += 1
    record_failures(stats["failed"])


def enrich_lead(lead) -> bool:
    """Enrichit UN lead via Apify (chemin daemon Apify-first).

    False = pas fait (non pret / URL non exploitable / echec) -> l'appelant
    replie sur le chemin Voyager historique. Le lead reste intact en echec.
    """
    url = lead.linkedin_url or ""
    if "linkedin.com/in/" not in url or url.startswith(SYNTHETIC_URL_PREFIX):
        return False
    if not apify_ready():
        return False
    record_usage(1)
    try:
        items = client.run_profile_scraper([url])
    except RuntimeError as exc:
        logger.warning(
            "Apify enrich %s en echec (%s) — repli Voyager",
            lead.public_identifier, exc,
        )
        record_failures(1)
        return False
    by_pid = _snapshots_by_public_id(items)
    ok = _apply_snapshot(lead, by_pid.get((lead.public_identifier or "").lower()))
    if not ok:
        record_failures(1)
    return ok


def _snapshots_by_public_id(items: list[dict]) -> dict[str, dict]:
    """Items acteur -> {public_identifier (lower): snapshot mappe}."""
    out: dict[str, dict] = {}
    for item in items:
        snap = map_actor_item(item)
        pid = snap.get("public_identifier")
        if not pid:
            logger.warning("Item Apify sans publicIdentifier/URL exploitable — ignore")
            continue
        out[pid.lower()] = snap
    return out


def _apply_snapshot(lead, snap: dict | None) -> bool:
    """Stocke le snapshot + calcule l'embedding. False = lead laisse INTACT."""
    if not snap:
        logger.warning(
            "Apify : aucun snapshot exploitable pour %s — lead intact "
            "(repli Voyager)", lead.public_identifier,
        )
        return False
    now = timezone.now()
    snap = dict(snap)
    snap["fetched_at"] = now.isoformat()  # tracabilite a cote de source="apify"
    lead.profile_snapshot = snap
    lead.profile_snapshot_at = now
    lead.save(update_fields=["profile_snapshot", "profile_snapshot_at"])
    lead.embed_from_profile(snap)
    return True
