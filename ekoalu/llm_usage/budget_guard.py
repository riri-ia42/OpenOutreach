"""Garde-fou budget journalier Claude API.

Si la conso cumulee du jour depasse `DAILY_BUDGET_USD` (defaut 4 $), on cree
un sentinel + on envoie un mail d'analyse a Richard. Tous les appels Claude
suivants raise BudgetExceededError jusqu'a :

- minuit local (auto-purge le lendemain)
- OU acquittement manuel via /ekoalu/budget/resume/ (staff_required)

But : couper l'hemorragie automatiquement quand un poste s'emballe (cf. pics
27/05 a $34 sans garde-fou). Permet d'investiguer avant de relancer.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

SENTINEL_PATH = Path(settings.ROOT_DIR) / "data" / "daily_budget_exceeded.json"
DEFAULT_BUDGET_USD = 4.0


class BudgetExceededError(RuntimeError):
    """Levee par le wrapper Claude quand le budget journalier est depasse."""


def daily_budget_usd() -> float:
    """Lit la limite depuis l'env (defaut 4.0)."""
    try:
        return float(os.environ.get("EKOALU_DAILY_BUDGET_USD", DEFAULT_BUDGET_USD))
    except (ValueError, TypeError):
        return DEFAULT_BUDGET_USD


def _today_local() -> date:
    """Date locale (le budget est journalier en heure locale, pas UTC)."""
    return datetime.now().date()


def is_budget_exceeded() -> bool:
    """True si le sentinel est actif ET concerne la date du jour.

    Auto-purge si le sentinel date d'hier ou avant (nouveau jour = nouveau budget).
    """
    if not SENTINEL_PATH.exists():
        return False
    try:
        data = json.loads(SENTINEL_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True

    triggered_iso = data.get("triggered_at_local", "")
    if not triggered_iso:
        return True
    try:
        triggered_date = datetime.fromisoformat(triggered_iso).date()
    except ValueError:
        return True

    if triggered_date < _today_local():
        SENTINEL_PATH.unlink()
        logger.info("Budget sentinel auto-purge (nouveau jour %s)", _today_local())
        return False
    return True


def acknowledge() -> bool:
    """Supprime le sentinel (appele par /ekoalu/budget/resume/). True si supprime."""
    if SENTINEL_PATH.exists():
        SENTINEL_PATH.unlink()
        logger.info("Budget sentinel acquitte manuellement par Richard")
        return True
    return False


def _today_cost_breakdown() -> tuple[float, list[tuple[str, int, float]]]:
    """Calcule (cumul_total, top_contexts_jour). Best-effort."""
    from django.db.models import Count, Sum

    from ekoalu.llm_usage.models import ClaudeUsageLog

    today = _today_local()
    qs = ClaudeUsageLog.objects.filter(timestamp__date=today)
    total = float(qs.aggregate(s=Sum("cost_usd"))["s"] or 0)
    rows = (
        qs.values("context")
        .annotate(n=Count("id"), cost=Sum("cost_usd"))
        .order_by("-cost")[:5]
    )
    breakdown = [
        (r["context"] or "(unknown)", int(r["n"]), float(r["cost"] or 0))
        for r in rows
    ]
    return total, breakdown


def check_and_trigger_if_exceeded() -> None:
    """Appele apres chaque log Claude. Trigger si seuil franchi (best-effort)."""
    if SENTINEL_PATH.exists():
        return  # deja trigger pour aujourd-hui
    try:
        total, breakdown = _today_cost_breakdown()
        budget = daily_budget_usd()
        if total >= budget:
            _trigger_budget_exceeded(total, budget, breakdown)
    except Exception:
        logger.exception("budget_guard check failed")


def _trigger_budget_exceeded(
    total: float,
    budget: float,
    breakdown: list[tuple[str, int, float]],
) -> None:
    """Cree le sentinel + envoie le mail d-analyse a Richard."""
    payload = {
        "triggered_at_local": datetime.now().isoformat(),
        "triggered_at_utc": datetime.now(timezone.utc).isoformat(),
        "budget_usd": budget,
        "cost_today_usd": total,
        "top_contexts": [
            {"context": ctx, "n_calls": n, "cost_usd": c} for ctx, n, c in breakdown
        ],
    }
    SENTINEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    SENTINEL_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    logger.warning(
        "BUDGET DEPASSE jour=%s cumul=$%.2f seuil=$%.2f -- sentinel cree, "
        "tout appel Claude va raise BudgetExceededError",
        _today_local(), total, budget,
    )
    _send_alert_mail(total, budget, breakdown)


def _send_alert_mail(
    total: float,
    budget: float,
    breakdown: list[tuple[str, int, float]],
) -> None:
    """Mail d'alerte best-effort avec analyse de cause."""
    try:
        from ekoalu.notifications.graph_mailer import is_configured, send_mail

        if not is_configured():
            logger.warning("Graph non configure : pas de mail BUDGET")
            return

        subject = (
            f"[STOP] EKOALU prospection - budget journalier depasse "
            f"(${total:.2f} / ${budget:.2f})"
        )

        rows_html = "\n".join(
            f"<tr><td style='padding:4px 8px;border:1px solid #e5e7eb'>{ctx}</td>"
            f"<td style='padding:4px 8px;border:1px solid #e5e7eb;text-align:right'>{n}</td>"
            f"<td style='padding:4px 8px;border:1px solid #e5e7eb;text-align:right'>${c:.2f}</td></tr>"
            for ctx, n, c in breakdown
        )
        top_ctx = breakdown[0][0] if breakdown else "(unknown)"
        top_cost = breakdown[0][2] if breakdown else 0

        html = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,Segoe UI,sans-serif;max-width:700px;margin:0 auto;padding:20px;">
<h2 style="color:#dc2626;border-bottom:2px solid #dc2626;padding-bottom:6px;">
  Budget Claude journalier depasse -- process arrete
</h2>
<p>La conso cumulee du <b>{_today_local()}</b> a depasse le seuil configure
(<code>EKOALU_DAILY_BUDGET_USD={budget:.2f}</code>).
Tous les appels Claude suivants vont raise <code>BudgetExceededError</code>
jusqu'a acquittement OU minuit (auto-reset).</p>

<table style="border-collapse:collapse;margin:12px 0">
  <tr><td style="padding:4px 12px"><b>Cumul aujourd'hui :</b></td>
      <td style="padding:4px 12px;color:#dc2626;font-weight:bold">${total:.2f}</td></tr>
  <tr><td style="padding:4px 12px"><b>Seuil :</b></td>
      <td style="padding:4px 12px">${budget:.2f}</td></tr>
  <tr><td style="padding:4px 12px"><b>Top poste :</b></td>
      <td style="padding:4px 12px"><code>{top_ctx}</code> (${top_cost:.2f})</td></tr>
</table>

<h3>Breakdown par context (top 5)</h3>
<table style="border-collapse:collapse;font-size:0.9em">
  <tr style="background:#f3f4f6">
    <th style="padding:4px 8px;border:1px solid #e5e7eb;text-align:left">Context</th>
    <th style="padding:4px 8px;border:1px solid #e5e7eb">Appels</th>
    <th style="padding:4px 8px;border:1px solid #e5e7eb">Coût</th>
  </tr>
  {rows_html}
</table>

<h3>Action attendue</h3>
<p>Decide si on reprend ou pas :</p>
<ul>
  <li><b>Reprendre maintenant :</b>
      <a href="http://ekoalu-prospection:3210/ekoalu/budget/resume/">
        Acquitter le sentinel (staff requis)
      </a>
      -- a faire seulement apres analyse de la cause.</li>
  <li><b>Laisser jusqu-a demain :</b> ne rien faire, reset auto a minuit local.</li>
</ul>

<h3>Pistes d'investigation</h3>
<ul>
  <li>Top poste = <code>{top_ctx}</code> : verifier si boucle de retry, prompt
      anormalement long, ou volume soudain.</li>
  <li><a href="http://ekoalu-prospection:3210/ekoalu/usage/">Dashboard conso Anthropic</a></li>
  <li><a href="http://ekoalu-prospection:3210/ekoalu/live/">Monitoring live</a></li>
</ul>

<p style="color:#9ca3af;font-size:0.85em;margin-top:30px;">
  Sentinel : <code>data/daily_budget_exceeded.json</code>. Auto-purge le {_today_local().isoformat()}+1.
</p>
</body></html>
"""
        send_mail(subject=subject, html_body=html)
        logger.info("Mail BUDGET envoye a Richard (cumul=$%.2f)", total)
    except Exception:
        logger.exception("Mail BUDGET echoue (sentinel cree quand meme)")
