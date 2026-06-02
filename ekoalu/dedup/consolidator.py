"""Consolidation : 1 Lead = 1 Deal actif maximum.

Règle métier Richard (02/06/2026) : "Un contact ne doit pas être dans plusieurs
campagnes. Tu affectes celle qui te parraît la meilleure et plus de doublon."

## Algorithme

Pour chaque Lead ayant >= 2 Deals dans des états *actifs* (non terminaux) sur
des campagnes EKOALU différentes :

1. Élire le "meilleur" Deal selon priorité (cf. `pick_best_deal`) :
   - État le plus avancé : CONNECTED > PENDING > READY_TO_CONNECT > QUALIFIED
   - À égalité d'état : `update_date` le plus récent
   - À égalité finale : `id` le plus petit (oldest)
2. Les autres Deals → state=COMPLETED, outcome=DUPLICATE_CAMPAIGN, reason
   référence le Deal gardé. Ils sortent de la vue opérationnelle.
3. Tout `PendingOutbound` ouvert (PENDING/APPROVED) pour ces Deals "shadow"
   est passé en REJECTED (cleanup pipeline).

Bonus cohérence :
- Connected + outcome=pre_existing_relation → bascule en COMPLETED (terminal).
- Connected + outcome=duplicate_campaign (incohérent) → bascule en COMPLETED.

## Idempotence

Réexécuter la commande sur un état déjà propre ne change rien. Utilisée :
- Une fois après le bug initial pour assainir l'existant.
- En garde permanente (cron / management command à la demande).

## Non-régression

Le guard côté création (`linkedin/db/deals.py:_create_deal`) refuse désormais
les nouveaux Deals cross-campagne actifs : si un Lead a déjà un Deal actif sur
une autre campagne, le nouveau est créé directement en shadow.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import Count

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from crm.models import Deal

# États non terminaux : un Lead ne doit avoir qu'un seul Deal dans ces états.
ACTIVE_STATES: tuple[str, ...] = ("Qualified", "Ready_to_connect", "Pending", "Connected")

# Outcomes historiques (non shadow) dont les doublons cross-campagne sont aussi
# dédoublonnés en mode "1 contact = 1 ligne" — on garde le plus récent.
# `converted` est exclu (positif, ne doit jamais devenir shadow).
TERMINAL_DEDUP_OUTCOMES: tuple[str, ...] = (
    "unresponsive", "wrong_fit", "not_interested", "no_budget", "has_solution",
    "bad_timing", "unknown", "",
)

# Priorité d'arbitrage (plus haut = mieux)
_STATE_PRIORITY: dict[str, int] = {
    "Connected": 40,
    "Pending": 30,
    "Ready_to_connect": 20,
    "Qualified": 10,
}

# Outcomes "shadow" : Deal présent en base pour traçabilité mais hors pipeline.
SHADOW_OUTCOMES: tuple[str, ...] = ("duplicate_campaign", "pre_existing_relation")


@dataclass
class DedupReport:
    """Résumé d'une passe de consolidation (dry-run ou réelle)."""

    leads_scanned: int = 0
    leads_with_duplicates: int = 0
    deals_demoted_to_duplicate: int = 0
    deals_normalized_pre_existing: int = 0
    deals_normalized_inconsistent: int = 0
    completed_history_demoted: int = 0
    pending_outbound_cancelled: int = 0
    details: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "leads_scanned": self.leads_scanned,
            "leads_with_duplicates": self.leads_with_duplicates,
            "deals_demoted_to_duplicate": self.deals_demoted_to_duplicate,
            "deals_normalized_pre_existing": self.deals_normalized_pre_existing,
            "deals_normalized_inconsistent": self.deals_normalized_inconsistent,
            "completed_history_demoted": self.completed_history_demoted,
            "pending_outbound_cancelled": self.pending_outbound_cancelled,
            "details_count": len(self.details),
        }


def pick_best_deal(deals: list["Deal"]) -> "Deal":
    """Élit le meilleur Deal d'un Lead parmi N deals actifs cross-campagne.

    Priorité :
    1. État le plus avancé (Connected > Pending > Ready_to_connect > Qualified)
    2. À égalité : `update_date` le plus récent
    3. À égalité finale : `id` le plus petit (création plus ancienne)
    """
    if not deals:
        raise ValueError("pick_best_deal: liste vide")

    def key(d):
        return (
            _STATE_PRIORITY.get(d.state, 0),
            d.update_date,
            -d.pk,  # negative => smaller id wins on equal date
        )

    return max(deals, key=key)


def _cancel_open_outbound_for_deal(deal: "Deal", reason: str) -> int:
    """Rejette les PendingOutbound ouverts (PENDING/APPROVED) d'un Deal shadow."""
    from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound
    from django.utils import timezone

    open_statuses = [OutboundStatus.PENDING, OutboundStatus.APPROVED]
    qs = PendingOutbound.objects.filter(
        prospect_public_id=deal.lead.public_identifier,
        campaign_id=deal.campaign_id,
        status__in=open_statuses,
    )
    n = qs.update(
        status=OutboundStatus.REJECTED,
        rejection_reason=reason,
    )
    return n


def _demote_deal_to_duplicate(deal: "Deal", best_deal: "Deal", dry_run: bool) -> dict:
    """Passe un Deal en shadow `duplicate_campaign` + cancel ses PendingOutbound."""
    from crm.models import Outcome
    from linkedin.enums import ProfileState

    keep_label = best_deal.campaign.name if best_deal.campaign_id else f"#{best_deal.pk}"
    reason = (
        f"Doublon: campagne gardée « {keep_label} » (Deal #{best_deal.pk}, état "
        f"{best_deal.state}). Refermé automatiquement pour cohérence pipeline."
    )

    detail = {
        "lead": deal.lead.public_identifier,
        "demoted_deal_id": deal.pk,
        "demoted_campaign": deal.campaign.name if deal.campaign_id else "?",
        "demoted_state": deal.state,
        "kept_deal_id": best_deal.pk,
        "kept_campaign": keep_label,
        "po_cancelled": 0,
    }
    if dry_run:
        # Compte sans rien modifier (best-effort)
        from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound

        detail["po_cancelled"] = PendingOutbound.objects.filter(
            prospect_public_id=deal.lead.public_identifier,
            campaign_id=deal.campaign_id,
            status__in=[OutboundStatus.PENDING, OutboundStatus.APPROVED],
        ).count()
        return detail

    deal.state = ProfileState.COMPLETED.value
    deal.outcome = Outcome.DUPLICATE_CAMPAIGN.value
    deal.reason = reason[:500]
    deal.save(update_fields=["state", "outcome", "reason", "update_date"])
    detail["po_cancelled"] = _cancel_open_outbound_for_deal(
        deal, f"Deal demoted to duplicate_campaign (kept #{best_deal.pk})",
    )
    return detail


def _normalize_pre_existing(dry_run: bool) -> int:
    """Connected + outcome=pre_existing_relation → COMPLETED (terminal)."""
    from crm.models import Deal

    qs = Deal.objects.filter(state="Connected", outcome="pre_existing_relation")
    n = qs.count()
    if dry_run or n == 0:
        return n
    qs.update(state="Completed")
    return n


def _normalize_inconsistent_duplicates(dry_run: bool) -> int:
    """Connected + outcome=duplicate_campaign (incohérent) → COMPLETED."""
    from crm.models import Deal

    qs = Deal.objects.filter(state="Connected", outcome="duplicate_campaign")
    n = qs.count()
    if dry_run or n == 0:
        return n
    qs.update(state="Completed")
    return n


def _dedup_completed_history(
    *, campaign_prefix: str, dry_run: bool, report: DedupReport,
) -> None:
    """Dedup les Deals Completed historiques cross-campagne (1 contact = 1 ligne).

    Pour chaque Lead avec >=2 Deals Completed (outcome dans TERMINAL_DEDUP_OUTCOMES)
    sur campagnes EKOALU différentes : on garde le plus récent par update_date,
    les autres passent en `outcome=duplicate_campaign`.

    `converted` (positif) n'est jamais déclassé.
    """
    from crm.models import Deal, Outcome

    leads_dupes = (
        Deal.objects
        .filter(
            state="Completed",
            outcome__in=TERMINAL_DEDUP_OUTCOMES,
            campaign__name__startswith=campaign_prefix,
        )
        .values("lead_id")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
    )

    for r in leads_dupes:
        lead_id = r["lead_id"]
        deals = list(
            Deal.objects
            .filter(
                lead_id=lead_id,
                state="Completed",
                outcome__in=TERMINAL_DEDUP_OUTCOMES,
                campaign__name__startswith=campaign_prefix,
            )
            .select_related("lead", "campaign")
            .order_by("-update_date", "id")
        )
        if len(deals) <= 1:
            continue
        keep = deals[0]
        for d in deals[1:]:
            if dry_run:
                report.completed_history_demoted += 1
                continue
            d.outcome = Outcome.DUPLICATE_CAMPAIGN.value
            d.reason = (
                f"Doublon historique: campagne gardée « "
                f"{keep.campaign.name if keep.campaign_id else '?'} » "
                f"(Deal #{keep.pk}). Archivé pour cohérence dashboard."
            )[:500]
            d.save(update_fields=["outcome", "reason", "update_date"])
            report.completed_history_demoted += 1


@transaction.atomic
def consolidate_duplicate_deals(
    *, dry_run: bool = False, campaign_prefix: str = "EKOALU - ",
) -> DedupReport:
    """Passe idempotente : garantit 1 Deal actif max par Lead cross-campagne.

    Args:
        dry_run: si True, n'écrit rien en base, retourne juste le compte.
        campaign_prefix: ne touche que les campagnes EKOALU (par défaut).

    Returns:
        DedupReport avec compteurs + détails par lead consolidé.
    """
    from crm.models import Deal

    report = DedupReport()

    # 1. Étape de normalisation : Connected + shadow outcome → Completed
    report.deals_normalized_pre_existing = _normalize_pre_existing(dry_run)
    report.deals_normalized_inconsistent = _normalize_inconsistent_duplicates(dry_run)

    # 2. Identifie les Leads avec ≥2 Deals dans des états ACTIFS
    leads_dupes = (
        Deal.objects
        .filter(state__in=ACTIVE_STATES, campaign__name__startswith=campaign_prefix)
        .values("lead_id")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
    )
    lead_ids = [r["lead_id"] for r in leads_dupes]
    report.leads_with_duplicates = len(lead_ids)
    report.leads_scanned = (
        Deal.objects.filter(state__in=ACTIVE_STATES, campaign__name__startswith=campaign_prefix)
        .values("lead_id").distinct().count()
    )

    # 3. Pour chaque Lead concerné : élire le meilleur, demote les autres
    for lead_id in lead_ids:
        active_deals = list(
            Deal.objects
            .filter(lead_id=lead_id, state__in=ACTIVE_STATES, campaign__name__startswith=campaign_prefix)
            .select_related("lead", "campaign")
        )
        if len(active_deals) <= 1:
            continue
        best = pick_best_deal(active_deals)
        for d in active_deals:
            if d.pk == best.pk:
                continue
            detail = _demote_deal_to_duplicate(d, best, dry_run)
            report.details.append(detail)
            report.deals_demoted_to_duplicate += 1
            report.pending_outbound_cancelled += detail["po_cancelled"]

    # 4. Dedup des Completed historiques (1 contact = 1 ligne, même pour archives)
    _dedup_completed_history(
        campaign_prefix=campaign_prefix, dry_run=dry_run, report=report,
    )

    # 5. Pass de propreté : cancel tout PendingOutbound ouvert orphelin
    # (Deal correspondant déjà en outcome shadow mais PO oublié)
    if not dry_run:
        report.pending_outbound_cancelled += _cleanup_orphan_outbound()
    else:
        # En dry-run on compte juste
        from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound

        for po in PendingOutbound.objects.filter(
            status__in=[OutboundStatus.PENDING, OutboundStatus.APPROVED],
        ):
            d = Deal.objects.filter(
                lead__public_identifier=po.prospect_public_id, campaign_id=po.campaign_id,
            ).first()
            if d and d.outcome in SHADOW_OUTCOMES:
                report.pending_outbound_cancelled += 1

    logger.info("consolidate_duplicate_deals: %s", report.as_dict())
    return report


def _cleanup_orphan_outbound() -> int:
    """REJECT tout PendingOutbound ouvert dont le Deal est shadow.

    Sécurité : couvre les rows oubliés par le démotion atomique
    (ex. PO créé avant le fix, Deal déjà en duplicate_campaign).
    """
    from crm.models import Deal
    from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound

    n = 0
    open_qs = PendingOutbound.objects.filter(
        status__in=[OutboundStatus.PENDING, OutboundStatus.APPROVED],
    )
    for po in open_qs.iterator():
        d = Deal.objects.filter(
            lead__public_identifier=po.prospect_public_id, campaign_id=po.campaign_id,
        ).first()
        if d and d.outcome in SHADOW_OUTCOMES:
            po.status = OutboundStatus.REJECTED
            po.rejection_reason = f"Orphan cleanup: Deal #{d.pk} outcome={d.outcome}"
            po.save(update_fields=["status", "rejection_reason"])
            n += 1
    return n


def has_active_deal_elsewhere(*, lead, campaign) -> "Deal | None":
    """Renvoie un Deal actif cross-campagne s'il existe (pour guard création).

    Utilisé par `linkedin/db/deals.py:_create_deal` pour décider si un nouveau
    Deal doit naître en shadow.
    """
    from crm.models import Deal

    return (
        Deal.objects
        .filter(lead=lead, state__in=ACTIVE_STATES)
        .exclude(campaign=campaign)
        .select_related("campaign")
        .order_by("-update_date")
        .first()
    )
