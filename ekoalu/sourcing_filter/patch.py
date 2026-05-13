"""Monkey-patch : auto-exclusion des deja-relations (degree=1) au sourcing.

Applique au boot via ekoalu/apps.py. Wrapper create_enriched_lead pour
detecter le connection_degree dans le profile dict Voyager et marquer le
Lead disqualified avant qu'il ne passe en qualification (economie Claude API).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_PATCH_APPLIED = False


def apply_sourcing_filter_patch() -> None:
    """Wrap linkedin.db.leads.create_enriched_lead. Idempotent."""
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return

    try:
        from linkedin.db import leads as leads_module
    except ImportError:
        logger.warning("Cannot patch sourcing_filter (linkedin.db.leads not importable)")
        return

    original = leads_module.create_enriched_lead

    def patched(session, url, profile):
        result = original(session, url, profile)
        if result is None:
            return result

        # Detection degree=1 : deja une relation -> exclusion permanente
        degree = profile.get("connection_degree")
        # Voyager peut renvoyer "DISTANCE_1" / "DISTANCE_2" via certains champs
        if degree is None:
            dist = profile.get("connection_distance") or ""
            if isinstance(dist, str) and "DISTANCE_1" in dist.upper():
                degree = 1

        if degree != 1:
            return result

        try:
            from crm.models import Lead
            from ekoalu.qualification_feedback.models import QualificationFeedback

            lead = Lead.objects.filter(pk=result).first()
            if not lead:
                return result
            lead.disqualified = True
            lead.save(update_fields=["disqualified"])

            campaign = getattr(session, "campaign", None)
            QualificationFeedback.objects.create(
                prospect_public_id=lead.public_identifier,
                campaign_id=campaign.pk if campaign else None,
                campaign_name=campaign.name if campaign else "",
                claude_reason="(auto-detect au sourcing: connection_degree=1)",
                richard_explanation="Auto-exclu : prospect deja en relation LinkedIn (1er degre)",
                kind=QualificationFeedback.Kind.ALREADY_CONNECTED,
            )
            logger.info(
                "EKOALU: %s auto-exclu au sourcing (deja relation, degree=1)",
                lead.public_identifier,
            )
        except Exception:
            logger.exception("Failed to mark lead as already-connected at sourcing")

        return result

    leads_module.create_enriched_lead = patched
    _PATCH_APPLIED = True
    logger.info("EKOALU sourcing_filter patch applique (auto-exclusion degree=1)")
