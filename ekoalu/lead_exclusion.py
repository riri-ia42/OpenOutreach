"""Exclusion permanente d'un prospect (décision Richard 01/06/2026).

Un refus = exclusion permanente cross-campagne. Sans cette cascade, le dedup
`_has_open_outbound` ne regarde que PENDING/APPROVED/BLOCKED_COMPANY : un
PendingOutbound REJECTED ne bloque rien et le daemon régénère un nouveau
message au cycle suivant ("les noms reviennent après refus").

Source of truth unique : tous les chemins de refus (UI /messages/ single +
bulk, Django Admin) appellent `disqualify_leads`. Ne jamais poser REJECTED
sans appeler cette fonction.
"""
from __future__ import annotations


def disqualify_leads(public_ids: list[str], reason: str) -> tuple[int, int]:
    """Marque les leads disqualifiés + clôt tous leurs Deals non terminaux
    + rejette tous leurs PendingOutbound encore ouverts (vide la file de
    validation et le dedup, sinon le message ressort en "En attente").

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
        outcome=Outcome.NOT_INTERESTED.value,
        reason=f"Refus Richard: {reason}"[:500],
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

    return n_leads, n_deals
