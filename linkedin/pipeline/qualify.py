# linkedin/pipeline/qualify.py
"""Qualify orchestration for the lazy chain."""
from __future__ import annotations

import logging
import os

import numpy as np
from termcolor import colored

from linkedin.ml.qualifier import BayesianQualifier

logger = logging.getLogger(__name__)


def _qualifier_disabled() -> bool:
    """Kill-switch ingestion : evite la qualification LLM tout en laissant
    le daemon traiter les Deals existants (Pending/Ready/Connected/Completed).

    Active via :
    - env var ``DAEMON_DISABLE_QUALIFIER=1`` (deploy permanent au boot)
    - OU fichier sentinel ``data/qualifier_disabled.flag`` (toggle live, lu a
      chaque appel, pas besoin de redemarrer le daemon)

    Cas d'usage : pic de consommation API (cf. 26-27/05/2026, 2000 qualif/jour
    a 7.6$c piece) ou mise en pause volontaire le temps de revoir l'ICP /
    filtres amont. Supprimer le fichier ou unset l'env var pour reprendre.
    """
    if os.environ.get("DAEMON_DISABLE_QUALIFIER", "").lower() in ("1", "true", "yes"):
        return True
    from django.conf import settings
    sentinel = os.path.join(getattr(settings, "BASE_DIR", "."), "data", "qualifier_disabled.flag")
    return os.path.exists(sentinel)


def fetch_qualification_candidates(session):
    """Return Lead rows (with embeddings) for leads awaiting qualification."""
    from crm.models import Lead
    from linkedin.db.leads import get_leads_for_qualification

    leads = get_leads_for_qualification(session)
    if not leads:
        return []

    lead_ids = {ld["lead_id"] for ld in leads}

    candidates = list(
        Lead.objects.filter(pk__in=lead_ids, embedding__isnull=False)
        .order_by("creation_date")
    )
    if candidates:
        return candidates

    # Robustness fallback: embed any lead that was missed at discovery time
    for ld in leads:
        lead = Lead.objects.filter(pk=ld["lead_id"]).first()
        if not lead or lead.embedding is not None:
            continue
        if lead.get_embedding(session) is not None:
            return [lead]

    return []


def run_qualification(session, qualifier: BayesianQualifier) -> str | None:
    """Qualify one unlabelled profile via BALD/auto-decision/LLM. Returns public_id or None."""
    if _qualifier_disabled():
        logger.info(
            "qualifier disabled via DAEMON_DISABLE_QUALIFIER -- "
            "ingestion paused, daemon continues on existing deals",
        )
        return None

    # EKOALU : toutes les lectures de fiche de ce bloc (enrichissement embedding
    # + texte profil pour le verdict) servent a SELECTIONNER un nouveau candidat
    # -> on les marque "selection" pour la ventilation efficacite (Richard 15/06).
    from ekoalu.read_guard.guard import read_purpose
    with read_purpose("selection"):
        return _run_qualification_inner(session, qualifier)


def _run_qualification_inner(session, qualifier: BayesianQualifier) -> str | None:
    from linkedin.ml.qualifier import qualify_with_llm, format_prediction

    candidates = fetch_qualification_candidates(session)
    if not candidates:
        return None

    logger.info(colored("\u25b6 qualify", "blue", attrs=["bold"]))

    # Balance-driven candidate selection
    selection_score = None
    if len(candidates) == 1:
        candidate = candidates[0]
    else:
        embeddings = np.array([c.embedding_array for c in candidates], dtype=np.float32)
        result = qualifier.acquisition_scores(embeddings)

        if result is None:
            candidate = candidates[0]
        else:
            strategy, scores = result
            best_idx = int(np.argmax(scores))
            candidate = candidates[best_idx]
            selection_score = (strategy, float(scores[best_idx]))
            n_neg, n_pos = qualifier.class_counts
            logger.info("Strategy: %s (neg=%d, pos=%d)",
                        colored(strategy, "cyan", attrs=["bold"]), n_neg, n_pos)

    lead_id = candidate.pk
    public_id = candidate.public_identifier
    embedding = candidate.embedding_array

    result = qualifier.predict(embedding)

    if result is not None:
        pred_prob, entropy, std = result
        stats = format_prediction(pred_prob, entropy, std, qualifier.n_obs)
        sel = f", {selection_score[0]}={selection_score[1]:.4f}" if selection_score else ""
        logger.debug("%s (%s%s) — querying LLM", public_id, stats, sel)
    else:
        logger.debug("%s GP not fitted (%d obs) — querying LLM", public_id, qualifier.n_obs)

    profile_text = _fetch_profile_text(session, lead_id, public_id)
    if not profile_text:
        logger.warning("No profile text for lead %d \u2014 disqualifying", lead_id)
        _save_qualification_result(session, qualifier, lead_id, public_id, embedding, 0, "no profile text available")
        return public_id

    campaign = session.campaign
    from ekoalu.qualifier_ab.runner import ab_is_active, run_ab_qualification
    if ab_is_active():
        # Mode A/B : champion (Sonnet) decide, challenger (Haiku) logge en parallele.
        label, reason = run_ab_qualification(
            profile_text, campaign.product_docs, campaign.campaign_objective,
            public_id, campaign.id,
        )
    else:
        label, reason = qualify_with_llm(
            profile_text,
            product_docs=campaign.product_docs,
            campaign_objective=campaign.campaign_objective,
        )
    _save_qualification_result(session, qualifier, lead_id, public_id, embedding, label, reason)
    return public_id


def _save_qualification_result(session, qualifier: BayesianQualifier, lead_id: int, public_id: str, embedding: np.ndarray, label: int, reason: str):
    # LLM rejections are tracked as FAILED Deals with "Disqualified" closing reason
    # (campaign-scoped), not as Lead.disqualified (permanent account-level exclusion).
    from linkedin.db.deals import create_disqualified_deal
    from linkedin.db.leads import promote_lead_to_deal

    qualifier.update(embedding, label)

    if label == 1:
        try:
            promote_lead_to_deal(session, public_id, reason=reason)
        except ValueError as e:
            logger.warning("Cannot promote %s: %s \u2014 disqualifying", public_id, e)
            create_disqualified_deal(session, public_id, reason=str(e))
            return
        logger.info("%s %s: %s", public_id, colored("QUALIFIED", "green", attrs=["bold"]), reason)
    else:
        create_disqualified_deal(session, public_id, reason=reason)


def _fetch_profile_text(session, lead_id: int, public_id: str) -> str | None:
    from crm.models import Lead
    from linkedin.ml.profile_text import build_profile_text

    lead = Lead.objects.filter(pk=lead_id).first()
    if not lead:
        return None
    profile_data = lead.get_profile(session)
    if not profile_data:
        return None
    return build_profile_text({"profile": profile_data})
