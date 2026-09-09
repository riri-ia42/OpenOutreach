"""Dedup « message deja en file » — helpers importables (hors monkey-patch).

Contexte (panne 05-08/09, proposition hub #253) : une invitation EN ATTENTE
de validation Richard laisse le Deal en QUALIFIED. Or ``promote_to_ready``
re-promouvait ce Deal a chaque cycle (P > seuil), ``handle_connect`` lisait
degre + fiche (2 lectures LinkedIn), puis le dedup du patch renvoyait
INTERCEPTED -> QUALIFIED -> re-promotion. Boucle : 55 connects / 54 « deja
en file » / 15 qualifications sur 6 000 lignes de log, quota connect 12/j
et budget lectures consommes par 2 candidats fantomes, 0 deal cree.

Ces helpers permettent au pool ready et au handler connect d'ecarter le
candidat AVANT toute lecture LinkedIn. Statuts « ouverts » identiques a ceux
du patch (pending / approved / blocked_company).
"""
from __future__ import annotations

from collections.abc import Iterable


def _open_statuses() -> list[str]:
    from ekoalu.outbound_validation.models import OutboundStatus

    return [
        OutboundStatus.PENDING,
        OutboundStatus.APPROVED,
        OutboundStatus.BLOCKED_COMPANY,
    ]


def public_ids_with_open_outbound(public_ids: Iterable[str], kind: str) -> set[str]:
    """Sous-ensemble de ``public_ids`` ayant un PendingOutbound ouvert de ce
    ``kind``, toutes campagnes confondues (regle ABM EKOALU)."""
    from ekoalu.outbound_validation.models import PendingOutbound

    ids = {pid for pid in public_ids if pid}
    if not ids:
        return set()
    return set(
        PendingOutbound.objects.filter(
            prospect_public_id__in=ids,
            kind=kind,
            status__in=_open_statuses(),
        ).values_list("prospect_public_id", flat=True)
    )


def has_open_invitation(public_id: str) -> bool:
    """True si une invitation attend deja validation/envoi pour ce prospect."""
    from ekoalu.outbound_validation.models import OutboundKind

    return bool(public_ids_with_open_outbound([public_id], OutboundKind.INVITATION))
