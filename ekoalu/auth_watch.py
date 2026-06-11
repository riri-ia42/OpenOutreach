"""Auto-STOP sur echecs d'authentification LinkedIn repetes (checkpoint/401).

Le 06/06, LinkedIn a tue la session (checkpoint de securite) : le daemon a
boucle 492 fois sur reauthenticate-echec-retry pendant que Richard n'etait pas
devant l'ecran — c'est lui qui a du declencher le STOP a la main a 16h33.

Ce module automatise ce reflexe : le daemon signale chaque task tombee en
``AuthenticationError`` via ``record_auth_failure()`` ; au bout de N echecs
CONSECUTIFS (defaut 3, env ``EKOALU_AUTH_FAILURES_BEFORE_STOP``), on engage
l'arret d'urgence (sentinel emergency_stop, ne se leve QUE via le bouton
"Reprendre" du dashboard) + mail d'alerte a Richard.

Insister contre un checkpoint est exactement ce qu'il ne faut pas faire :
chaque tentative de re-login aggrave le signal cote LinkedIn.

Compteur en memoire process (le scenario vise est une boucle DANS le daemon).
``reset()`` est appele apres chaque task reussie.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_FAILURES_BEFORE_STOP = 1

_consecutive_failures = 0


def failures_before_stop() -> int:
    """Seuil d'echecs consecutifs avant auto-STOP (defaut 1 = STOP immediat).

    Decision Richard 10/06 : insister contre un checkpoint LinkedIn aggrave le
    signal. On coupe DES le 1er echec d'auth - le compte n'est de toute facon
    pas reutilisable tant que le checkpoint n'est pas resolu a la main.
    """
    try:
        return max(1, int(os.environ.get(
            "EKOALU_AUTH_FAILURES_BEFORE_STOP", DEFAULT_FAILURES_BEFORE_STOP,
        )))
    except (ValueError, TypeError):
        return DEFAULT_FAILURES_BEFORE_STOP


def reset() -> None:
    """Une task LinkedIn a reussi : la session est saine, on repart de zero."""
    global _consecutive_failures
    _consecutive_failures = 0


def record_auth_failure(context: str = "") -> bool:
    """Compte un echec d'auth. True si l'auto-STOP vient d'etre engage.

    A appeler dans la branche ``except AuthenticationError`` du daemon, que la
    re-authentification ait reussi ou non : si la session re-401 aussitot,
    c'est le meme blocage cote LinkedIn.
    """
    global _consecutive_failures
    _consecutive_failures += 1
    threshold = failures_before_stop()
    logger.warning(
        "Echec auth LinkedIn %d/%d (consecutifs) — %s",
        _consecutive_failures, threshold, context or "(sans contexte)",
    )
    if _consecutive_failures < threshold:
        return False

    from ekoalu import emergency_stop

    if emergency_stop.is_stopped():
        return False  # deja a l'arret (manuel ou auto) : rien a faire

    reason = (
        f"AUTO-STOP : {_consecutive_failures} echecs d'authentification "
        f"LinkedIn consecutifs (checkpoint/401 probable). {context}".strip()
    )
    emergency_stop.engage(reason=reason, actor="auto-stop")
    _send_alert_mail(_consecutive_failures, context)
    return True


def _send_alert_mail(n_failures: int, context: str) -> None:
    """Mail urgent best-effort a Richard."""
    try:
        from ekoalu.notifications.graph_mailer import is_configured, send_mail

        if not is_configured():
            logger.warning("Graph non configure : pas de mail AUTO-STOP")
            return

        html = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,Segoe UI,sans-serif;max-width:700px;margin:0 auto;padding:20px;">
<h2 style="color:#dc2626;border-bottom:2px solid #dc2626;padding-bottom:6px;">
  AUTO-STOP — LinkedIn bloque la session (checkpoint/401 probable)
</h2>
<p>Le daemon a subi <b>{n_failures} echecs d'authentification consecutifs</b>
et s'est mis a l'arret TOUT SEUL pour ne pas aggraver le signal cote LinkedIn
(le 06/06, la boucle de re-login avait tourne 492 fois).</p>
<p>Contexte : <code>{context or "(non precise)"}</code></p>

<h3>Quoi faire (dans l'ordre)</h3>
<ol>
  <li><b>Ne PAS relancer l'outil.</b> L'arret est volontaire.</li>
  <li>Ouvrir LinkedIn <b>a la main</b> dans un navigateur normal sur la machine
      habituelle, et resoudre le checkpoint (verification securite).</li>
  <li>Laisser le compte se reposer au moins 24-48h apres resolution.</li>
  <li>Reprendre via le bouton <b>&#9654; Reprendre</b> du dashboard :
      <a href="http://ekoalu-prospection:3210/ekoalu/">http://ekoalu-prospection:3210/ekoalu/</a></li>
</ol>

<p style="color:#9ca3af;font-size:0.85em;margin-top:30px;">
  Sentinel : data/emergency_stop.flag (actor=auto-stop). Seuil :
  EKOALU_AUTH_FAILURES_BEFORE_STOP={failures_before_stop()}.
</p>
</body></html>
"""
        send_mail(
            subject=f"[URGENT] EKOALU prospection - AUTO-STOP apres {n_failures} echecs auth LinkedIn",
            html_body=html,
        )
        logger.info("Mail AUTO-STOP envoye a Richard")
    except Exception:
        logger.exception("Mail AUTO-STOP echoue (arret engage quand meme)")
