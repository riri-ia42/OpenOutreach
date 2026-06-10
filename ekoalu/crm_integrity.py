"""Audit de coherence du CRM (Lead / Deal / PendingOutbound).

Les incidents passes (noms qui reviennent apres refus, doublons multi-ABM,
backlog failed jamais trie) avaient tous la meme racine : un etat incoherent
entre les 3 modeles qui ne se voyait qu'une fois le degat fait. Ce module
detecte ces incoherences a froid, et repare celles qui ont une correction
evidente et sans risque.

Anomalies detectees :

- ``disqualified_active_deals`` : lead disqualifie mais Deal encore actif
  (le daemon regenererait des messages).            FIX : clore le Deal.
- ``open_po_dead_lead``         : PO pending/approved d'un lead disqualifie
  ou desinscrit.                                    FIX : rejeter le PO.
- ``open_po_obsolete_deal``     : invitation pending/approved alors que le
  Deal est deja Connected/Completed/Failed.         FIX : rejeter le PO.
- ``duplicate_open_po``         : >=2 PO ouverts pour le meme
  (prospect, kind) — le dedup a un trou quelque part. FIX : garder le plus
  recent, rejeter les autres.
- ``multi_active_deals``        : lead avec >=2 Deals actifs (doublon
  cross-campagne).  REPORT seulement -> ``consolidate_duplicate_deals``.

Expose ``collect_anomalies()`` (lecture seule) + ``fix_anomalies()`` (safe
fixes). Consomme par la commande ``check_crm_integrity`` et le daily_recap.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

OPEN_PO_STATUSES = ("pending", "approved")


def _active_states() -> list[str]:
    from linkedin.enums import ProfileState

    return [
        ProfileState.QUALIFIED.value,
        ProfileState.READY_TO_CONNECT.value,
        ProfileState.PENDING.value,
        ProfileState.CONNECTED.value,
    ]


def collect_anomalies() -> dict[str, list]:
    """Detecte les incoherences. Lecture seule, sans effet de bord."""
    from crm.models import Deal, Lead
    from ekoalu.outbound_validation.models import OutboundKind, PendingOutbound
    from linkedin.enums import ProfileState

    active = _active_states()
    anomalies: dict[str, list] = {}

    # 1. Lead disqualifie avec Deal encore actif
    anomalies["disqualified_active_deals"] = list(
        Deal.objects.filter(lead__disqualified=True, state__in=active)
        .values_list("pk", "lead__public_identifier", "state")
    )

    # 2. PO ouvert d'un lead mort (disqualifie ou desinscrit)
    dead_pids = set(
        Lead.objects.filter(disqualified=True).values_list("public_identifier", flat=True)
    ) | set(
        Lead.objects.exclude(unsubscribed_at=None).values_list("public_identifier", flat=True)
    )
    anomalies["open_po_dead_lead"] = list(
        PendingOutbound.objects.filter(
            status__in=OPEN_PO_STATUSES, prospect_public_id__in=dead_pids,
        ).values_list("pk", "prospect_public_id", "kind")
    )

    # 3. Invitation ouverte alors que le Deal est deja Connected/Completed/Failed
    done_states = {
        ProfileState.CONNECTED.value,
        ProfileState.COMPLETED.value,
        ProfileState.FAILED.value,
    }
    deal_state = {
        (pid, cid): state
        for pid, cid, state in Deal.objects.values_list(
            "lead__public_identifier", "campaign_id", "state",
        )
    }
    obsolete = []
    for pk, pid, cid in PendingOutbound.objects.filter(
        status__in=OPEN_PO_STATUSES, kind=OutboundKind.INVITATION,
    ).values_list("pk", "prospect_public_id", "campaign_id"):
        if deal_state.get((pid, cid)) in done_states:
            obsolete.append((pk, pid, deal_state[(pid, cid)]))
    anomalies["open_po_obsolete_deal"] = obsolete

    # 4. Doublons de PO ouverts pour le meme (prospect, kind)
    from collections import defaultdict

    open_pos = defaultdict(list)
    for pk, pid, kind in PendingOutbound.objects.filter(
        status__in=OPEN_PO_STATUSES,
    ).order_by("created_at").values_list("pk", "prospect_public_id", "kind"):
        open_pos[(pid, kind)].append(pk)
    anomalies["duplicate_open_po"] = [
        (pid, kind, pks) for (pid, kind), pks in open_pos.items() if len(pks) > 1
    ]

    # 5. Lead avec >=2 Deals actifs (cross-campagne)
    from django.db.models import Count

    anomalies["multi_active_deals"] = list(
        Deal.objects.filter(state__in=active)
        .values("lead__public_identifier")
        .annotate(n=Count("pk"))
        .filter(n__gte=2)
        .values_list("lead__public_identifier", "n")
    )

    return anomalies


def total_issues(anomalies: dict[str, list]) -> int:
    return sum(len(v) for v in anomalies.values())


def fix_anomalies(anomalies: dict[str, list]) -> dict[str, int]:
    """Applique les corrections sans risque. Retourne le compte par categorie.

    ``multi_active_deals`` n'est PAS corrige ici (la consolidation choisit un
    "meilleur" deal — logique dediee dans ``consolidate_duplicate_deals``).
    """
    from crm.models import Deal
    from crm.models.deal import Outcome
    from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound
    from linkedin.enums import ProfileState

    fixed: dict[str, int] = {}

    deal_pks = [pk for pk, _pid, _state in anomalies["disqualified_active_deals"]]
    fixed["disqualified_active_deals"] = Deal.objects.filter(pk__in=deal_pks).update(
        state=ProfileState.FAILED.value,
        outcome=Outcome.NOT_INTERESTED.value,
        reason="check_crm_integrity: lead disqualifie, deal encore actif",
    )

    po_pks = [pk for pk, _pid, _kind in anomalies["open_po_dead_lead"]]
    fixed["open_po_dead_lead"] = PendingOutbound.objects.filter(pk__in=po_pks).update(
        status=OutboundStatus.REJECTED,
        rejection_reason="check_crm_integrity: lead disqualifie/desinscrit",
    )

    po_pks = [pk for pk, _pid, _state in anomalies["open_po_obsolete_deal"]]
    fixed["open_po_obsolete_deal"] = PendingOutbound.objects.filter(pk__in=po_pks).update(
        status=OutboundStatus.REJECTED,
        rejection_reason="check_crm_integrity: deal deja traite",
    )

    dup_pks = []
    for _pid, _kind, pks in anomalies["duplicate_open_po"]:
        dup_pks.extend(pks[:-1])  # garde le plus recent (liste triee par created_at)
    fixed["duplicate_open_po"] = PendingOutbound.objects.filter(pk__in=dup_pks).update(
        status=OutboundStatus.REJECTED,
        rejection_reason="check_crm_integrity: doublon (un PO plus recent existe)",
    )

    for key, n in fixed.items():
        if n:
            logger.info("check_crm_integrity fix %s : %d corriges", key, n)
    return fixed
