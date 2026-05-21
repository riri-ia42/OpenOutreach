"""Vues de monitoring live : santé du daemon + horodatage dernière interaction.

URLs :
- /ekoalu/health.json   → JSON brut (consommé par le dashboard et le watchdog)
- /ekoalu/live/         → dashboard HTML avec auto-refresh
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

logger = logging.getLogger(__name__)

HEALTH_JSON = Path(settings.ROOT_DIR) / "data" / "HEALTH.json"


def _read_health_json() -> dict:
    """Lit data/HEALTH.json (écrit par scripts/healthcheck.py). Renvoie un dict
    avec un statut UNKNOWN si le fichier n'existe pas / n'est pas parsable."""
    if not HEALTH_JSON.exists():
        return {"status": "UNKNOWN", "message": "HEALTH.json absent — le watchdog n'a pas encore tourné"}
    try:
        return json.loads(HEALTH_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "UNKNOWN", "message": f"HEALTH.json illisible : {exc}"}


def _compute_live_metrics() -> dict:
    """Calcule les métriques live depuis la DB (sans toucher au browser daemon)."""
    from linkedin.models import Task
    from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound

    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    last_24h = now - timedelta(hours=24)

    # Dernière interaction effective (task completed OU outbound sent)
    last_task = Task.objects.filter(status="completed").order_by("-completed_at").first()
    last_outbound = (
        PendingOutbound.objects.filter(status=OutboundStatus.SENT)
        .order_by("-sent_at").first()
    )
    candidates = []
    if last_task and last_task.completed_at:
        candidates.append(("task:" + (last_task.task_type or "?"), last_task.completed_at))
    if last_outbound and last_outbound.sent_at:
        candidates.append(("outbound:" + (last_outbound.kind or "?"), last_outbound.sent_at))

    last_label, last_at = (None, None)
    if candidates:
        last_label, last_at = max(candidates, key=lambda c: c[1])

    return {
        "now": now.isoformat(),
        "last_interaction_label": last_label,
        "last_interaction_at": last_at.isoformat() if last_at else None,
        "minutes_since_last_interaction": (
            int((now - last_at).total_seconds() / 60) if last_at else None
        ),
        "tasks_today_completed": Task.objects.filter(
            status="completed", completed_at__gte=today_start
        ).count(),
        "tasks_today_failed": Task.objects.filter(
            status="failed", completed_at__gte=today_start
        ).count(),
        "tasks_24h_completed": Task.objects.filter(
            status="completed", completed_at__gte=last_24h
        ).count(),
        "tasks_24h_failed": Task.objects.filter(
            status="failed", completed_at__gte=last_24h
        ).count(),
        "tasks_pending": Task.objects.filter(status="pending").count(),
        "outbound_today_sent": PendingOutbound.objects.filter(
            status=OutboundStatus.SENT, sent_at__gte=today_start
        ).count(),
        "outbound_pending_validation": PendingOutbound.objects.filter(
            status=OutboundStatus.PENDING
        ).count(),
    }


def health_json(request):
    """JSON consolidé : healthcheck.py output + métriques live DB.

    Public (pas de login requis) pour permettre au watchdog/curl de la consommer.
    """
    health = _read_health_json()
    live = _compute_live_metrics()
    return JsonResponse({"health": health, "live": live})


@staff_member_required
def live_dashboard(request):
    """Dashboard HTML avec polling toutes les 30s."""
    return render(request, "monitoring/live.html", {})
