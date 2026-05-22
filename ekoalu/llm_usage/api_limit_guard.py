"""Garde-fou API Anthropic cap mensuel.

Quand le plafond mensuel configure dans la console Anthropic est atteint, l'API
renvoie un 400 avec un message du genre :

    "You have reached your specified API usage limits. You will regain access
     on 2026-06-01 at 00:00 UTC."

Sans gestion explicite, le daemon enchainait des FAILED tasks en rafale +
polluait la DB jusqu'au cap retries. Ce module :

1. Detecte l'exception (regex sur le message)
2. Cree un sentinel fichier `data/api_limit_reached.json` avec la date de reprise
3. Envoie un mail urgent a Richard (best-effort)
4. Le daemon (linkedin/daemon.py) check ce sentinel au debut de chaque cycle
   et bascule en kill-switch tant qu'il est actif
5. Le sentinel se purge automatiquement quand la date de reprise est passee
   (pas besoin d'action manuelle apres reset mensuel ou montee du cap console)

Voir memoire : `cap_mensuel_anthropic.md`.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

SENTINEL_PATH = Path(settings.ROOT_DIR) / "data" / "api_limit_reached.json"

# Message Anthropic typique :
#   "You have reached your specified API usage limits. You will regain access
#    on 2026-06-01 at 00:00 UTC."
_LIMIT_PATTERN = re.compile(r"reached your specified API usage limits", re.IGNORECASE)
_DATE_PATTERN = re.compile(
    r"regain access on (\d{4}-\d{2}-\d{2}).*?(\d{2}:\d{2})", re.IGNORECASE,
)


def is_usage_limit_error(exc: BaseException) -> bool:
    """True si l'exception correspond au cap mensuel Anthropic."""
    return bool(_LIMIT_PATTERN.search(str(exc) or ""))


def _parse_regain_at_utc(exc_msg: str) -> tuple[str, str]:
    """Extrait (text_lisible, iso_utc) de la date 'regain access on' du message.

    Retourne ('', '') si introuvable.
    """
    m = _DATE_PATTERN.search(exc_msg)
    if not m:
        return "", ""
    date_str, time_str = m.group(1), m.group(2)
    text = f"{date_str} {time_str} UTC"
    iso = f"{date_str}T{time_str}:00+00:00"
    return text, iso


def is_limit_active() -> bool:
    """True si le sentinel existe ET la date de reprise n'est pas passee.

    Si la date est passee, on supprime le sentinel et on renvoie False (reset
    automatique). Si pas de date dans le sentinel (cas exotique), on traite
    comme actif pour rester safe.
    """
    if not SENTINEL_PATH.exists():
        return False
    try:
        data = json.loads(SENTINEL_PATH.read_text(encoding="utf-8"))
        regain_iso = data.get("regain_at_utc", "")
        if not regain_iso:
            return True
        regain_at = datetime.fromisoformat(regain_iso)
        if datetime.now(timezone.utc) >= regain_at:
            SENTINEL_PATH.unlink()
            logger.info(
                "API limit sentinel auto-purge (regain=%s atteint)", regain_iso,
            )
            return False
        return True
    except (OSError, json.JSONDecodeError, ValueError):
        # Fichier illisible / date corrompue : on reste prudent, on bloque
        return True


def trigger_limit_reached(exc: BaseException) -> None:
    """Cree le sentinel + envoie le mail urgent (idempotent).

    Si le sentinel existe deja avec une date future identique, on ne re-mail
    pas (evite le spam si plusieurs appels concurrents echouent).
    """
    msg = str(exc) or ""
    regain_text, regain_iso = _parse_regain_at_utc(msg)

    # Idempotence : ne re-cree pas si deja actif avec meme date
    if SENTINEL_PATH.exists():
        try:
            existing = json.loads(SENTINEL_PATH.read_text(encoding="utf-8"))
            if existing.get("regain_at_utc") == regain_iso:
                logger.debug("API limit sentinel deja actif, skip mail")
                return
        except (OSError, json.JSONDecodeError):
            pass

    payload = {
        "triggered_at_utc": datetime.now(timezone.utc).isoformat(),
        "regain_at_utc": regain_iso,
        "regain_text": regain_text,
        "error_message": msg[:500],
    }
    SENTINEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    SENTINEL_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    logger.warning(
        "API limit reached -- sentinel cree, daemon va basculer en pause (regain=%s)",
        regain_text or "?",
    )

    _send_alert_mail(regain_text)


def _send_alert_mail(regain_text: str) -> None:
    """Mail best-effort. Ne raise jamais : le sentinel est plus important."""
    try:
        from ekoalu.notifications.graph_mailer import is_configured, send_mail

        if not is_configured():
            logger.warning("Graph non configure : pas de mail API_LIMIT")
            return

        subject = (
            f"[URGENT] EKOALU prospection - cap Anthropic atteint "
            f"(reprise {regain_text or '?'})"
        )
        html = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,Segoe UI,sans-serif;max-width:700px;margin:0 auto;padding:20px;">
<h2 style="color:#dc2626;border-bottom:2px solid #dc2626;padding-bottom:6px;">
  Cap mensuel Anthropic atteint
</h2>
<p>L'API a renvoye un 400 ("usage limits"). Le daemon LinkedIn va automatiquement
basculer en pause au prochain cycle pour eviter de polluer la queue avec des
FAILED tasks.</p>

<h3>Reprise</h3>
<ul>
  <li><b>Date de reprise automatique :</b> {regain_text or '(inconnu)'}</li>
  <li><b>Sentinel :</b> <code>data/api_limit_reached.json</code> (auto-purge a la date de reprise)</li>
</ul>

<h3>Pour reprendre AVANT la date :</h3>
<ol>
  <li>Aller sur <a href="https://console.anthropic.com/settings/limits">console.anthropic.com/settings/limits</a></li>
  <li>Relever le cap mensuel</li>
  <li>Supprimer <code>data/api_limit_reached.json</code> sur la machine TSE</li>
  <li>Le daemon reprendra tout seul au prochain cycle (max 5 min)</li>
</ol>

<h3>Liens utiles</h3>
<ul>
  <li><a href="http://ekoalu-prospection:3210/ekoalu/usage/">Conso Anthropic du mois</a></li>
  <li><a href="http://ekoalu-prospection:3210/ekoalu/live/">Monitoring live</a></li>
</ul>

<p style="color:#9ca3af;font-size:0.85em;margin-top:30px;">
  90 PendingOutbound preserves en queue. Tu peux continuer a valider/editer/refuser
  dans /ekoalu/messages/ -- ils seront drainages a la reprise.
</p>
</body></html>
"""
        send_mail(subject=subject, html_body=html)
        logger.info("Mail API_LIMIT envoye a Richard")
    except Exception:
        logger.exception("Mail API_LIMIT echoue (sentinel cree quand meme)")
