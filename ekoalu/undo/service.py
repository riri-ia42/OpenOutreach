"""Capture de l'état précédent d'une action, et retour arrière.

Principe : au moment d'agir, on photographie les seules lignes que l'action va
changer (statut d'un message, disqualification d'un lead, état d'un deal, tâches
annulées, entrées du registre des sorties). Annuler = réécrire ces valeurs. On
ne « re-calcule » jamais l'état d'avant, on le rejoue tel quel — c'est ce qui
permet d'annuler une sortie de société sans ressusciter des leads qui étaient
déjà disqualifiés pour une autre raison.

Portée volontairement courte (`RETENTION`) : ce journal sert le bouton
« Annuler la dernière action », pas l'audit.
"""
from __future__ import annotations

import datetime as dt
import logging

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

RETENTION = dt.timedelta(days=7)
_TERMINAL_SENT = "sent"


# --- Photographie -----------------------------------------------------------

def snapshot_outbounds(ids) -> list[dict]:
    """Statut actuel des messages visés, avant modification."""
    from ekoalu.outbound_validation.models import PendingOutbound

    return [
        {"id": pk, "status": status, "rejection_reason": reason or "", "sent_at": _iso(sent_at)}
        for pk, status, reason, sent_at in PendingOutbound.objects
        .filter(pk__in=list(ids))
        .values_list("pk", "status", "rejection_reason", "sent_at")
    ]


def snapshot_leads(public_ids) -> dict:
    """État complet de ce qu'une disqualification va toucher.

    Appeler AVANT `disqualify_leads` : après, l'ancien état est perdu.
    """
    from crm.models import Deal, Lead
    from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound
    from linkedin.models import Task

    clean = sorted({p for p in public_ids if p})
    if not clean:
        return {"leads": [], "deals": [], "outbounds": [], "tasks": []}

    open_statuses = [OutboundStatus.PENDING, OutboundStatus.APPROVED, OutboundStatus.BLOCKED_COMPANY]
    return {
        "leads": list(
            Lead.objects.filter(public_identifier__in=clean, disqualified=False)
            .values_list("public_identifier", flat=True)
        ),
        "deals": [
            {"id": pk, "state": state, "outcome": outcome or "", "reason": reason or ""}
            for pk, state, outcome, reason in Deal.objects
            .filter(lead__public_identifier__in=clean)
            .values_list("pk", "state", "outcome", "reason")
        ],
        "outbounds": snapshot_outbounds(
            PendingOutbound.objects
            .filter(prospect_public_id__in=clean, status__in=open_statuses)
            .values_list("pk", flat=True)
        ),
        "tasks": list(
            Task.objects.filter(
                status=Task.Status.PENDING,
                task_type__in=[Task.TaskType.CHECK_PENDING, Task.TaskType.FOLLOW_UP],
                payload__public_id__in=clean,
            ).values_list("pk", flat=True)
        ),
    }


def record(kind: str, label: str, payload: dict):
    """Enregistre une action annulable et purge les vieilles entrées."""
    from ekoalu.undo.models import UndoEntry

    entry = UndoEntry.objects.create(kind=kind, label=label[:255], payload=payload)
    UndoEntry.objects.filter(created_at__lt=timezone.now() - RETENTION).delete()
    return entry


def last_undoable():
    """Dernière action encore annulable, ou None."""
    from ekoalu.undo.models import UndoEntry

    return UndoEntry.objects.filter(undone_at__isnull=True).order_by("-created_at").first()


# --- Retour arrière ---------------------------------------------------------

def undo_last() -> tuple[bool, str]:
    """Annule la dernière action. Renvoie (succès, message pour Richard)."""
    entry = last_undoable()
    if entry is None:
        return False, "Aucune action à annuler."
    with transaction.atomic():
        detail = _restore(entry.payload)
        entry.undone_at = timezone.now()
        entry.save(update_fields=["undone_at"])
    logger.info("Annulation de « %s » : %s", entry.label, detail)
    return True, f"Annulé : {entry.label}. {detail}"


def _restore(payload: dict) -> str:
    from crm.models import Deal, Lead
    from ekoalu.outbound_validation.models import PendingOutbound
    from ekoalu.sorties.models import ProspectionSortie
    from linkedin.models import Task

    bits = []

    n = 0
    for row in payload.get("outbounds", []):
        # Un message DÉJÀ PARTI ne revient pas : on ne touche pas à une ligne
        # dont le statut a changé pour « sent » depuis la photographie.
        n += PendingOutbound.objects.filter(pk=row["id"]).exclude(status=_TERMINAL_SENT).update(
            status=row["status"], rejection_reason=row.get("rejection_reason", ""),
        )
    if n:
        bits.append(f"{n} message(s) remis en « {payload['outbounds'][0]['status']} »")

    slugs = payload.get("leads", [])
    if slugs:
        n = Lead.objects.filter(public_identifier__in=slugs).update(disqualified=False)
        bits.append(f"{n} prospect(s) remis en prospection")

    for row in payload.get("deals", []):
        Deal.objects.filter(pk=row["id"]).update(
            state=row["state"], outcome=row["outcome"], reason=row["reason"],
        )

    task_ids = payload.get("tasks", [])
    if task_ids:
        Task.objects.filter(pk__in=task_ids).update(
            status=Task.Status.PENDING, completed_at=None,
        )

    sortie_ids = payload.get("sorties", [])
    if sortie_ids:
        n, _ = ProspectionSortie.objects.filter(pk__in=sortie_ids).delete()
        if n:
            bits.append("registre des sorties nettoyé")
        from ekoalu.sorties.service import export_shared_json, invalidate_cache
        invalidate_cache()
        export_shared_json()

    return " ; ".join(bits) if bits else "rien à restaurer (déjà modifié depuis)."


def _iso(value) -> str:
    return value.isoformat() if value else ""
