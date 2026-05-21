"""Envoie une alerte mail a Richard quand le daemon est detecte zombie ou recupere.

Usage :
- python manage.py alert_zombie --escalate    (zombie non resolu, action humaine requise)
- python manage.py alert_zombie --resolved    (zombie auto-corrige, info)

Derogation Richard : le mail part SANS confirmation tant que destinataire unique
est richard@ekoalu.com (GRAPH_ALERT_RECIPIENT).

Cooldown : 1 mail max / 30 min (etat dans data/alert_state.json) pour eviter le
spam si le watchdog detecte plusieurs zombies consecutifs.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

ALERT_STATE = Path(settings.ROOT_DIR) / "data" / "alert_state.json"
HEALTH_JSON = Path(settings.ROOT_DIR) / "data" / "HEALTH.json"
COOLDOWN_MINUTES = 30


def _read_state() -> dict:
    if not ALERT_STATE.exists():
        return {}
    try:
        return json.loads(ALERT_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(state: dict) -> None:
    ALERT_STATE.parent.mkdir(parents=True, exist_ok=True)
    ALERT_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_health() -> dict:
    if not HEALTH_JSON.exists():
        return {"status": "UNKNOWN", "message": "HEALTH.json absent"}
    try:
        return json.loads(HEALTH_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "UNKNOWN", "message": f"HEALTH.json illisible : {exc}"}


def _build_html(kind: str, health: dict) -> tuple[str, str]:
    """Renvoie (subject, html)."""
    if kind == "escalate":
        emoji = "[!]"
        color = "#dc2626"
        title = "Daemon LinkedIn ZOMBIE - intervention requise"
        bullet = "Le watchdog n'a pas reussi a relancer le daemon. Action manuelle requise."
    else:
        emoji = "[OK]"
        color = "#16a34a"
        title = "Daemon LinkedIn redemarre OK"
        bullet = "Le watchdog (ou un trigger Claude) a auto-corrige une regression zombie."

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subject = f"{emoji} EKOALU prospection - {title}"

    last_completed = health.get("last_completed_at") or "(jamais)"
    pending = health.get("pending_total", "?")
    failed_30 = health.get("failed_in_window", "?")
    completed_30 = health.get("completed_in_window", "?")
    asyncio_reg = "OUI" if health.get("asyncio_regression") else "non"

    html = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, Segoe UI, sans-serif; max-width: 700px; margin: 0 auto; padding: 20px;">
<h2 style="color: {color}; border-bottom: 2px solid {color}; padding-bottom: 6px;">{emoji} {title}</h2>
<p style="color: #6b7280;">{now}</p>
<p>{bullet}</p>

<h3>Etat actuel</h3>
<ul>
  <li>Statut healthcheck : <strong>{health.get('status', '?')}</strong></li>
  <li>Message : <code>{health.get('message', '')}</code></li>
  <li>Regression asyncio detectee : <strong>{asyncio_reg}</strong></li>
  <li>Derniere task completed : <code>{last_completed}</code></li>
  <li>Tasks pending : <strong>{pending}</strong></li>
  <li>30 dernieres min : <strong>{completed_30}</strong> reussies / <strong>{failed_30}</strong> echouees</li>
</ul>

<h3>Liens utiles</h3>
<ul>
  <li><a href="http://ekoalu-prospection:3210/ekoalu/live/">Monitoring live (auto-refresh)</a></li>
  <li><a href="http://ekoalu-prospection:3210/ekoalu/health.json">Health JSON brut</a></li>
  <li><a href="http://ekoalu-prospection:3210/ekoalu/">Dashboard prospection</a></li>
</ul>
"""
    if kind == "escalate":
        html += """
<h3 style="color: #dc2626;">Actions a faire</h3>
<ol>
  <li>Connecter en Tailscale a la machine SRV-TSE (100.102.28.86)</li>
  <li>Ouvrir Claude Code dans le projet prospection-ia</li>
  <li>Demander : "le daemon est zombie, investigue et corrige"</li>
  <li>Verifier le monitoring live revient HEALTHY</li>
</ol>
"""
    html += "</body></html>"
    return subject, html


class Command(BaseCommand):
    help = "Envoie une alerte mail a Richard (escalate ou resolved)."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--escalate", action="store_true",
                           help="Zombie non resolu, action humaine requise")
        group.add_argument("--resolved", action="store_true",
                           help="Zombie auto-corrige (info)")
        parser.add_argument("--force", action="store_true",
                            help="Ignore le cooldown 30 min")

    def handle(self, *args, **opts):
        kind = "escalate" if opts["escalate"] else "resolved"

        # Cooldown : pas plus d'1 mail / 30 min de meme type (sauf --force)
        state = _read_state()
        last_sent_iso = state.get(f"last_{kind}_at")
        if last_sent_iso and not opts["force"]:
            try:
                last_sent = datetime.fromisoformat(last_sent_iso)
                if datetime.now(timezone.utc) - last_sent < timedelta(minutes=COOLDOWN_MINUTES):
                    self.stdout.write(self.style.WARNING(
                        f"Cooldown actif (dernier {kind} il y a moins de {COOLDOWN_MINUTES} min). "
                        f"Utiliser --force pour outrepasser."
                    ))
                    return
            except ValueError:
                pass

        health = _read_health()

        # Tente Graph
        try:
            from ekoalu.notifications.graph_mailer import is_configured, send_mail
            if not is_configured():
                self.stdout.write(self.style.ERROR(
                    "Graph non configure (GRAPH_* manquantes dans .env). Pas d'alerte envoyee."
                ))
                return
            subject, html = _build_html(kind, health)
            send_mail(subject=subject, html_body=html)
            state[f"last_{kind}_at"] = datetime.now(timezone.utc).isoformat()
            _write_state(state)
            self.stdout.write(self.style.SUCCESS(f"Alerte {kind} envoyee"))
            logger.info("Alerte %s envoyee", kind)
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Echec envoi alerte : {exc}"))
            logger.exception("Echec alerte %s", kind)
            raise
