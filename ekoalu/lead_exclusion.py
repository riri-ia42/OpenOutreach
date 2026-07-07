"""Exclusion permanente d'un prospect (décision Richard 01/06/2026).

Un refus = exclusion permanente cross-campagne. Sans cette cascade, le dedup
`_has_open_outbound` ne regarde que PENDING/APPROVED/BLOCKED_COMPANY : un
PendingOutbound REJECTED ne bloque rien et le daemon régénère un nouveau
message au cycle suivant ("les noms reviennent après refus").

Source of truth unique : TOUS les chemins de disqualification (UI /messages/
single + bulk, Django Admin, bouton fiche lead, action already_connected,
connect Unreachable, auto-exclusion degree=1 au sourcing) appellent
`disqualify_leads`. Ne JAMAIS poser `Lead.disqualified=True` en direct :
cela laisse des Deals actifs + des tasks check_pending/follow_up fantômes
(LOT D 07/07 : 19 leads disqualifiés avec Deal actif constatés).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def disqualify_leads(
    public_ids: list[str],
    reason: str,
    *,
    outcome: str | None = None,
    deal_reason: str | None = None,
) -> tuple[int, int]:
    """Marque les leads disqualifiés + clôt tous leurs Deals non terminaux
    + rejette tous leurs PendingOutbound encore ouverts (vide la file de
    validation et le dedup) + annule leurs tasks ouvertes (check_pending/
    follow_up PENDING, sinon elles tournent à vide sur un lead mort).

    ``outcome``/``deal_reason`` permettent aux chemins non "refus Richard"
    (Unreachable, déjà-relation) de garder leur sémantique propre.

    Returns:
        (n_leads_disqualifiés, n_deals_clôturés)
    """
    from crm.models import Deal, Lead
    from crm.models.deal import Outcome
    from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound
    from linkedin.enums import ProfileState

    clean = list({pid for pid in public_ids if pid})
    if not clean:
        return 0, 0

    n_leads = Lead.objects.filter(
        public_identifier__in=clean,
        disqualified=False,
    ).update(disqualified=True)

    terminal = [ProfileState.COMPLETED.value, ProfileState.FAILED.value]
    n_deals = Deal.objects.filter(
        lead__public_identifier__in=clean,
    ).exclude(state__in=terminal).update(
        state=ProfileState.FAILED.value,
        outcome=outcome or Outcome.NOT_INTERESTED.value,
        reason=(deal_reason or f"Refus Richard: {reason}")[:500],
    )

    # Vide la file : tout PO ouvert (PENDING/APPROVED/BLOCKED_COMPANY) -> REJECTED.
    PendingOutbound.objects.filter(
        prospect_public_id__in=clean,
        status__in=[
            OutboundStatus.PENDING,
            OutboundStatus.APPROVED,
            OutboundStatus.BLOCKED_COMPANY,
        ],
    ).update(status=OutboundStatus.REJECTED, rejection_reason=f"Lead disqualifié: {reason}"[:500])

    _cancel_open_tasks(clean)

    return n_leads, n_deals


def _cancel_open_tasks(public_ids: list[str]) -> int:
    """Annule les tasks PENDING (check_pending/follow_up) des leads disqualifiés.

    Marquées COMPLETED (précédent daemon "drainée sans exécution", cf. pause
    campagne) — PAS FAILED, pour ne pas polluer le cap retry du scheduler.
    Les tasks connect sont campaign-level (pas de public_id) : rien à annuler,
    et le pool ready ne sert plus ce lead (Deals clos ci-dessus).
    """
    from django.utils import timezone

    from linkedin.models import Task

    n = Task.objects.filter(
        status=Task.Status.PENDING,
        task_type__in=[Task.TaskType.CHECK_PENDING, Task.TaskType.FOLLOW_UP],
        payload__public_id__in=public_ids,
    ).update(status=Task.Status.COMPLETED, completed_at=timezone.now())
    if n:
        logger.info(
            "Disqualification: %d task(s) check_pending/follow_up annulée(s) pour %s",
            n, public_ids,
        )
    return n
