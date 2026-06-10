"""Cap dur sur les lectures de profil LinkedIn par jour.

Le checkpoint LinkedIn du 06/06 a ete cause par le volume de LECTURES de
fiches (qualifier : 1200-1760/j vs repere ~80/j compte gratuit), pas par les
envois — qui etaient deja plafonnes (8 invit/j, 80/sem). Ce module ferme le
trou : chaque appel Voyager profil (get_profile / get_connection_degree) est
compte, et au-dela du cap toute nouvelle lecture raise ReadCapExceededError.

- Cap configurable : env ``EKOALU_DAILY_PROFILE_READS_CAP`` (defaut 60/j,
  marge sous le repere 80).
- Compteur en DB (ProfileReadDay) : partage entre daemon et commandes cron.
- Reset naturel a minuit (la ligne du jour suivant repart a 0).
- Mail d'alerte a Richard la 1re fois que le cap est atteint dans la journee.
- Le daemon (linkedin/daemon.py) verifie ``is_cap_reached()`` en tete de
  boucle : cap atteint => plus aucune task (qualif/follow-up/check_pending),
  mais le drain de la file approved continue (les envois ne lisent pas de
  fiche via Voyager).
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime

logger = logging.getLogger(__name__)

DEFAULT_DAILY_READS_CAP = 60


class ReadCapExceededError(RuntimeError):
    """Levee quand une lecture de profil depasserait le cap journalier."""


def daily_reads_cap() -> int:
    """Lit le cap depuis l'env (defaut 60/j)."""
    try:
        return int(os.environ.get(
            "EKOALU_DAILY_PROFILE_READS_CAP", DEFAULT_DAILY_READS_CAP,
        ))
    except (ValueError, TypeError):
        return DEFAULT_DAILY_READS_CAP


def _today_local() -> date:
    return datetime.now().date()


def reads_today() -> int:
    """Nombre de lectures de profil comptees aujourd'hui."""
    from ekoalu.read_guard.models import ProfileReadDay

    row = ProfileReadDay.objects.filter(date=_today_local()).first()
    return row.count if row else 0


def is_cap_reached() -> bool:
    """True si le cap journalier de lectures est atteint."""
    return reads_today() >= daily_reads_cap()


def check_read_allowed() -> None:
    """Raise ReadCapExceededError si le cap est atteint.

    Appele AVANT chaque lecture Voyager (cf. read_guard/patch.py) : c'est la
    defense en profondeur — meme un appelant qui ignore le daemon (commande
    cron, shell) ne peut pas depasser le cap.
    """
    cap = daily_reads_cap()
    current = reads_today()
    if current >= cap:
        raise ReadCapExceededError(
            f"Cap lectures profil atteint : {current}/{cap} aujourd'hui "
            f"(EKOALU_DAILY_PROFILE_READS_CAP={cap}). Reset a minuit."
        )


def record_read(source: str = "") -> int:
    """Compte une lecture de profil (atomique). Retourne le total du jour.

    A appeler juste AVANT l'appel reseau : on compte les tentatives, pas les
    succes — LinkedIn voit la requete meme si elle echoue.
    """
    from django.db.models import F

    from ekoalu.read_guard.models import ProfileReadDay

    today = _today_local()
    row, _created = ProfileReadDay.objects.get_or_create(date=today)
    ProfileReadDay.objects.filter(pk=row.pk).update(count=F("count") + 1)
    row.refresh_from_db()

    cap = daily_reads_cap()
    if row.count >= cap and not row.notified:
        ProfileReadDay.objects.filter(pk=row.pk, notified=False).update(notified=True)
        logger.warning(
            "READ_CAP atteint : %d/%d lectures profil aujourd'hui (source=%s) "
            "— plus aucune lecture jusqu'a minuit",
            row.count, cap, source or "?",
        )
        _send_alert_mail(row.count, cap)
    return row.count


def _send_alert_mail(count: int, cap: int) -> None:
    """Mail best-effort a Richard quand le cap est atteint (1x/jour)."""
    try:
        from ekoalu.notifications.graph_mailer import is_configured, send_mail

        if not is_configured():
            logger.warning("Graph non configure : pas de mail READ_CAP")
            return

        html = f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,Segoe UI,sans-serif;max-width:700px;margin:0 auto;padding:20px;">
<h2 style="color:#d97706;border-bottom:2px solid #d97706;padding-bottom:6px;">
  Cap lectures profil LinkedIn atteint — lectures suspendues jusqu'a minuit
</h2>
<p>Le plafond journalier de lectures de fiches LinkedIn est atteint :
<b>{count}/{cap}</b> (<code>EKOALU_DAILY_PROFILE_READS_CAP={cap}</code>).</p>
<p>C'est le garde-fou pose apres le checkpoint du 06/06 (le qualifier lisait
1200-1760 fiches/jour vs repere ~80/j). Comportement :</p>
<ul>
  <li>Plus aucune lecture de profil jusqu'a minuit (reset auto).</li>
  <li>Le daemon ne traite plus de tasks (qualif / follow-up / check_pending).</li>
  <li>Les envois deja valides continuent (ils ne lisent pas de fiche).</li>
</ul>
<p><b>Rien a faire</b> si c'est une journee normale chargee. Si ca se repete
tous les jours, c'est que le pipeline lit trop : on baisse le volume de
qualification ou on passe le tri sur le sourcing Google.</p>
<p style="color:#9ca3af;font-size:0.85em;margin-top:30px;">
  Compteur : table ekoalu_profilereadday. Cap : env EKOALU_DAILY_PROFILE_READS_CAP.
</p>
</body></html>
"""
        send_mail(
            subject=f"[CAP] EKOALU prospection - {count}/{cap} lectures profil aujourd'hui",
            html_body=html,
        )
        logger.info("Mail READ_CAP envoye a Richard (%d/%d)", count, cap)
    except Exception:
        logger.exception("Mail READ_CAP echoue (cap actif quand meme)")
