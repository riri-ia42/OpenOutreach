"""Monkey-patches du routage (appliques au boot via ekoalu/apps.py).

1. ``create_enriched_lead`` -> on enregistre LeadDiscovery(lead, campagne
   courante) pour memoriser quelle campagne a trouve le profil.
2. ``get_leads_for_qualification`` -> on restreint la liste aux profils
   decouverts pour CETTE campagne (fin du test croise). Kill-switch via
   ekoalu.lead_routing.config.scoped_qualification_enabled().
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_PATCH_APPLIED = False


def record_discovery(lead_id: int, campaign) -> None:
    """Rattache un lead a une campagne (idempotent)."""
    if lead_id is None or campaign is None:
        return
    try:
        from ekoalu.lead_routing.models import LeadDiscovery

        LeadDiscovery.objects.get_or_create(lead_id=lead_id, campaign=campaign)
    except Exception:
        logger.exception("Failed to record LeadDiscovery (lead=%s)", lead_id)


def apply_lead_routing_patch() -> None:
    """Wrap create_enriched_lead + get_leads_for_qualification. Idempotent."""
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return

    try:
        from linkedin.db import leads as leads_module
    except ImportError:
        logger.warning("Cannot patch lead_routing (linkedin.db.leads not importable)")
        return

    # -- 1. Enregistrement de la decouverte --------------------------------
    original_create = leads_module.create_enriched_lead

    def patched_create(session, url, profile):
        lead_pk = original_create(session, url, profile)
        if lead_pk is not None:
            record_discovery(lead_pk, getattr(session, "campaign", None))
        return lead_pk

    leads_module.create_enriched_lead = patched_create

    # -- 2. Scoping de la qualification ------------------------------------
    original_gql = leads_module.get_leads_for_qualification

    def patched_gql(session):
        base = original_gql(session)
        from ekoalu.lead_routing.config import scoped_qualification_enabled

        if not scoped_qualification_enabled():
            return base
        campaign = getattr(session, "campaign", None)
        if campaign is None or not base:
            return base

        from ekoalu.lead_routing.models import LeadDiscovery

        scoped_ids = set(
            LeadDiscovery.objects.filter(campaign=campaign).values_list(
                "lead_id", flat=True,
            )
        )
        scoped = [d for d in base if d.get("lead_id") in scoped_ids]
        if len(scoped) != len(base):
            logger.info(
                "EKOALU routing: campagne '%s' qualifie %d/%d profils (scopes a sa decouverte)",
                getattr(campaign, "name", "?"), len(scoped), len(base),
            )
        return scoped

    leads_module.get_leads_for_qualification = patched_gql

    _PATCH_APPLIED = True
    logger.info("EKOALU lead_routing patch applique (discovery + scoping qualification)")
