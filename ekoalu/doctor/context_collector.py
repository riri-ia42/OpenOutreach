"""Collecte le contexte que le doctor envoie a Claude pour diagnostic.

Toutes les lectures sont read-only. La taille du contexte est bornee (tail
de log, top N process, etc.) pour rester sous ~5k tokens input.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings


HEALTH_JSON = Path(settings.ROOT_DIR) / "data" / "HEALTH.json"
DAEMON_LOG = Path(settings.ROOT_DIR) / "data" / "daemon.log"
WATCHDOG_LOG = Path(settings.ROOT_DIR) / "data" / "watchdog.log"
WATCHDOG_STATE = Path(settings.ROOT_DIR) / "data" / "watchdog_state.json"
ALERT_STATE = Path(settings.ROOT_DIR) / "data" / "alert_state.json"


def _safe_read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _tail_text(path: Path, max_lines: int) -> str:
    if not path.exists():
        return ""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(size, 64 * 1024)
            f.seek(max(0, size - chunk))
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    lines = data.splitlines()
    return "\n".join(lines[-max_lines:])


def _list_python_processes() -> list[dict]:
    """Liste les process python actifs via wmic (Windows). Sans command line si
    indisponible : on garde au moins le PID."""
    try:
        result = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "ProcessId,WorkingSetSize,CommandLine", "/format:csv"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        out: list[dict] = []
        for line in result.stdout.splitlines():
            parts = line.strip().split(",")
            # Format : Node, CommandLine, ProcessId, WorkingSetSize
            if len(parts) < 4 or parts[2] == "ProcessId":
                continue
            try:
                pid = int(parts[2])
            except ValueError:
                continue
            try:
                mem_mb = round(int(parts[3]) / (1024 * 1024), 1)
            except ValueError:
                mem_mb = 0.0
            cmd = parts[1][:200]
            out.append({"pid": pid, "mem_mb": mem_mb, "cmd": cmd})
        return out
    except (OSError, subprocess.SubprocessError):
        return []


def _anthropic_cost_24h_by_context() -> dict:
    """Repartition du cout Anthropic des dernieres 24h par context."""
    from django.apps import apps
    from django.db.models import Count, Sum

    ClaudeUsageLog = apps.get_model("ekoalu", "ClaudeUsageLog")
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    qs = ClaudeUsageLog.objects.filter(timestamp__gte=since).values("context").annotate(
        n=Count("id"), cost=Sum("cost_usd"),
    ).order_by("-cost")
    return {
        "total_calls": ClaudeUsageLog.objects.filter(timestamp__gte=since).count(),
        "total_cost_usd": float(
            ClaudeUsageLog.objects.filter(timestamp__gte=since).aggregate(t=Sum("cost_usd"))["t"] or 0,
        ),
        "by_context": [
            {
                "context": r["context"] or "(vide)",
                "calls": r["n"],
                "cost_usd": float(r["cost"] or 0),
            }
            for r in qs
        ],
    }


def _pending_outbound_counts() -> dict:
    from django.apps import apps

    PendingOutbound = apps.get_model("ekoalu", "PendingOutbound")
    counts: dict = {}
    for status, _ in PendingOutbound._meta.get_field("status").choices:
        counts[status] = PendingOutbound.objects.filter(status=status).count()
    return counts


def _task_counts() -> dict:
    from linkedin.models import Task

    counts: dict = {}
    for status, _ in Task.Status.choices:
        counts[status] = Task.objects.filter(status=status).count()
    return counts


def _env_flags() -> dict:
    """Snapshot des flags non secrets que le doctor doit connaitre."""
    import os
    flags = (
        "EKOALU_DAEMON_TASKS_DISABLED",
        "EKOALU_ENABLE_PROMPT_CACHE",
        "EKOALU_WEEKLY_INVITE_TARGET",
        "EKOALU_DAILY_INVITE_CAP",
        "ANTHROPIC_MODEL",
    )
    return {name: os.environ.get(name, "") for name in flags}


def collect_context() -> dict:
    """Construit le dict de contexte transmis a Claude pour diagnostic.

    Le caller (manage.py ekoalu_doctor) passe ce dict au prompt Claude. La
    redaction des secrets se fait juste avant l'envoi via redact_dict().
    """
    return {
        "now_utc": datetime.now(timezone.utc).isoformat(),
        "health": _safe_read_json(HEALTH_JSON),
        "watchdog_state": _safe_read_json(WATCHDOG_STATE),
        "alert_state": _safe_read_json(ALERT_STATE),
        "daemon_log_tail_200": _tail_text(DAEMON_LOG, 200),
        "watchdog_log_tail_50": _tail_text(WATCHDOG_LOG, 50),
        "python_processes": _list_python_processes(),
        "anthropic_24h": _anthropic_cost_24h_by_context(),
        "pending_outbound": _pending_outbound_counts(),
        "tasks": _task_counts(),
        "env_flags": _env_flags(),
    }
