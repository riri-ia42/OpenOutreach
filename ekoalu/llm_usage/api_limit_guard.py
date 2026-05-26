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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

SENTINEL_PATH = Path(settings.ROOT_DIR) / "data" / "api_limit_reached.json"

# Probe model : Haiku 4.5 -- input 1 token, max_tokens=1 = cout < 0.0001 $/probe
PROBE_MODEL = "claude-haiku-4-5-20251001"
PROBE_COOLDOWN = timedelta(hours=1)

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

    En plus de l'auto-purge a la date de reprise, on probe l'API toutes les
    PROBE_COOLDOWN (1h par defaut) avec un appel Haiku minimal (~0.0001 $/probe)
    pour detecter une montee du cap dans la console Anthropic avant la date.
    Si le probe passe (200) -> purge du sentinel + mail "reprise auto".
    """
    if not SENTINEL_PATH.exists():
        return False
    try:
        data = json.loads(SENTINEL_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Fichier illisible : on reste prudent, on bloque
        return True

    regain_iso = data.get("regain_at_utc", "")
    if regain_iso:
        try:
            regain_at = datetime.fromisoformat(regain_iso)
        except ValueError:
            return True
        if datetime.now(timezone.utc) >= regain_at:
            SENTINEL_PATH.unlink()
            logger.info(
                "API limit sentinel auto-purge (regain=%s atteint)", regain_iso,
            )
            return False

    # Probe Anthropic si cooldown ecoule
    if _probe_due(data):
        if _run_probe():
            SENTINEL_PATH.unlink()
            logger.info("API limit probe OK -- cap remonte, reprise auto")
            _send_recovery_mail()
            return False
        # Echec : on note la tentative pour respecter le cooldown
        data["last_probe_at"] = datetime.now(timezone.utc).isoformat()
        SENTINEL_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8",
        )
    return True


def _probe_due(sentinel_data: dict) -> bool:
    """True si on doit faire un nouveau probe (cooldown ecoule).

    Fallback sur `triggered_at_utc` pour ne pas re-probe immediatement apres
    la creation du sentinel (on vient justement de se prendre un 400, inutile
    de re-tester dans la foulee).
    """
    ref_iso = sentinel_data.get("last_probe_at") or sentinel_data.get("triggered_at_utc", "")
    if not ref_iso:
        return True
    try:
        ref_at = datetime.fromisoformat(ref_iso)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - ref_at >= PROBE_COOLDOWN


def _run_probe() -> bool:
    """Appelle Anthropic avec un payload minimal. True si 200, False sinon.

    Best-effort : toute erreur autre que succes est traitee comme cap toujours
    actif (par securite -- on prefere rester en pause que reprendre par erreur).
    """
    try:
        from anthropic import Anthropic

        from linkedin.models import SiteConfig
        cfg = SiteConfig.load()
        api_key = cfg.llm_api_key
        if not api_key:
            logger.warning("Probe API limit : pas d'API key configuree")
            return False
        client = Anthropic(api_key=api_key, max_retries=0)
        client.messages.create(
            model=PROBE_MODEL,
            max_tokens=1,
            messages=[{"role": "user", "content": "ok"}],
        )
        return True
    except Exception as exc:
        if is_usage_limit_error(exc):
            logger.info("Probe API limit : cap toujours actif (400 attendu)")
        else:
            logger.warning("Probe API limit : erreur inattendue %s", exc)
        return False


def _send_recovery_mail() -> None:
    """Mail best-effort quand le cap est remonte (reprise auto)."""
    try:
        from ekoalu.notifications.graph_mailer import is_configured, send_mail

        if not is_configured():
            return

        subject = "[OK] EKOALU prospection - cap Anthropic remonte, reprise auto"
        html = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,Segoe UI,sans-serif;max-width:700px;margin:0 auto;padding:20px;">
<h2 style="color:#16a34a;border-bottom:2px solid #16a34a;padding-bottom:6px;">
  Cap Anthropic remonte -- reprise prospection
</h2>
<p>Le probe horaire Anthropic vient de passer 200. Le sentinel a ete purge
automatiquement, le daemon va reprendre les envois LinkedIn au prochain cycle
(max 5 min).</p>
<ul>
  <li><a href="http://ekoalu-prospection:3210/ekoalu/live/">Monitoring live</a></li>
  <li><a href="http://ekoalu-prospection:3210/ekoalu/usage/">Conso Anthropic</a></li>
</ul>
</body></html>
"""
        send_mail(subject=subject, html_body=html)
        logger.info("Mail API_LIMIT recovery envoye a Richard")
    except Exception:
        logger.exception("Mail API_LIMIT recovery echoue (sentinel deja purge)")


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
