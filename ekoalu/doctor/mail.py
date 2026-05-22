"""Mail advisory envoye a Richard avec le diagnostic du doctor.

Tout texte est passe par redact() avant inclusion HTML pour neutraliser tout
secret eventuellement remonte par Claude.
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timezone

from ekoalu.doctor.redact import redact

logger = logging.getLogger(__name__)


_SEVERITY_COLOR = {
    "low": "#16a34a",
    "medium": "#eab308",
    "high": "#ea580c",
    "critical": "#dc2626",
}


def _e(text: str) -> str:
    """HTML escape + redact secrets."""
    return html.escape(redact(text or ""), quote=True)


def _actions_table(actions: list) -> str:
    if not actions:
        return "<p><em>Aucune action proposee.</em></p>"
    rows = []
    for a in actions:
        atype = _e(str(a.get("action_type", "?")))
        reason = _e(str(a.get("reason", "")))
        payload = _e(str(a.get("payload", {})))
        rows.append(
            f"<tr><td><code>{atype}</code></td>"
            f"<td>{reason}</td>"
            f"<td><code>{payload}</code></td></tr>",
        )
    return (
        "<table cellpadding='6' cellspacing='0' style='border-collapse:collapse;border:1px solid #e5e7eb;'>"
        "<thead><tr style='background:#f3f4f6;'>"
        "<th align='left'>Action</th><th align='left'>Pourquoi</th><th align='left'>Payload</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def build_advisory_email(incident, diagnosis: dict) -> tuple[str, str]:
    """Construit (subject, html) pour le mail advisory.

    *incident* est un DoctorIncident sauvegarde, *diagnosis* le dict parse de
    Claude.
    """
    sev = (diagnosis.get("severity") or "medium").lower()
    color = _SEVERITY_COLOR.get(sev, "#6b7280")
    confidence = diagnosis.get("confidence", 0.0)
    signature = diagnosis.get("signature", "")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    subject = (
        f"[Doctor-{sev.upper()}] EKOALU prospection - "
        f"{diagnosis.get('root_cause', 'anomalie')[:60]}"
    )

    advisory = diagnosis.get("advisory_summary", "")
    diagnosis_text = diagnosis.get("diagnosis", "")

    html_body = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,Segoe UI,sans-serif;max-width:760px;margin:0 auto;padding:20px;">
<h2 style="color:{color};border-bottom:2px solid {color};padding-bottom:6px;">
  ekoalu-doctor &mdash; incident #{incident.pk} ({_e(sev)})
</h2>
<p style="color:#6b7280;">{now} &middot; confidence {confidence:.2f} &middot; signature <code>{_e(signature)}</code></p>

<h3>Diagnostic</h3>
<p>{_e(diagnosis_text)}</p>

<h3>Cause racine</h3>
<p><strong>{_e(diagnosis.get('root_cause', ''))}</strong></p>

<h3>Recap (Richard)</h3>
<p style="background:#f9fafb;padding:12px;border-left:3px solid {color};">{_e(advisory)}</p>

<h3>Actions proposees</h3>
{_actions_table(diagnosis.get('actions', []))}

<h3>Liens utiles</h3>
<ul>
  <li><a href="http://ekoalu-prospection:3210/ekoalu/live/">Monitoring live</a></li>
  <li><a href="http://ekoalu-prospection:3210/ekoalu/usage/">Conso Anthropic</a></li>
  <li><a href="http://ekoalu-prospection:3210/admin/ekoalu/doctorincident/{incident.pk}/change/">Detail incident (admin)</a></li>
</ul>

<p style="color:#9ca3af;font-size:0.85em;margin-top:30px;">
  Mode advisory only -- aucune action n'a ete executee automatiquement.
  Tu peux appliquer les actions manuellement, ou laisser le watchdog auto-corriger
  s'il en est capable.
</p>
</body></html>
"""
    return subject, html_body


def send_advisory(incident, diagnosis: dict) -> None:
    """Envoie le mail advisory via Graph. Lance une exception si echec (le
    caller decide quoi faire)."""
    from ekoalu.notifications.graph_mailer import is_configured, send_mail

    if not is_configured():
        raise RuntimeError("Graph mailer non configure (variables GRAPH_* manquantes)")

    subject, html_body = build_advisory_email(incident, diagnosis)
    send_mail(subject=subject, html_body=html_body)
    logger.info("Doctor: mail advisory envoye pour incident #%s", incident.pk)
