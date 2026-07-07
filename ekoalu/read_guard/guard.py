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

import contextlib
import contextvars
import logging
import os
from datetime import date, datetime

logger = logging.getLogger(__name__)

# Usage courant d'une lecture de fiche (pour la ventilation efficacité du tri,
# demande Richard 15/06 : distinguer les lectures qui servent à SÉLECTIONNER
# de nouveaux candidats de celles de follow-up/relance — seules les premières
# comptent dans le taux d'efficacité réel). Posé via read_purpose() autour des
# appels Voyager : "selection" (qualif/enrichissement) ou "follow_up".
_read_purpose: contextvars.ContextVar[str] = contextvars.ContextVar(
    "ekoalu_read_purpose", default="",
)


@contextlib.contextmanager
def read_purpose(label: str):
    """Marque toutes les lectures de fiche (get_profile) faites dans ce bloc
    avec l'usage ``label`` (ex. "selection", "follow_up"). N'affecte PAS les
    contrôles de degré (get_connection_degree restent comptés à part)."""
    token = _read_purpose.set(label)
    try:
        yield
    finally:
        _read_purpose.reset(token)

# Repere 2026 compte gratuit : ~80 lectures/jour admissibles. Montee decidee
# par Richard le 12/06 : 60/j tout de suite, 75/j a partir du lundi 15/06
# (via EKOALU_PROFILE_READS_CAP_RAMP). L'incident du 06/06 etait a ~1700/j.
DEFAULT_DAILY_READS_CAP = 40


class ReadCapExceededError(RuntimeError):
    """Levee quand une lecture de profil depasserait le cap journalier."""


def _ramp_cap(today: date) -> int | None:
    """Cap programme a date via EKOALU_PROFILE_READS_CAP_RAMP.

    Format : "YYYY-MM-DD:cap[,YYYY-MM-DD:cap...]" — le cap retenu est celui
    de la derniere date atteinte. None si aucune date atteinte / env absent.
    """
    raw = os.environ.get("EKOALU_PROFILE_READS_CAP_RAMP", "").strip()
    if not raw:
        return None
    best: tuple[date, int] | None = None
    for part in raw.split(","):
        try:
            d_str, cap_str = part.strip().split(":")
            d = date.fromisoformat(d_str)
            cap = int(cap_str)
        except (ValueError, TypeError):
            logger.warning("EKOALU_PROFILE_READS_CAP_RAMP : segment invalide %r", part)
            continue
        if d <= today and (best is None or d > best[0]):
            best = (d, cap)
    return best[1] if best else None


def _nominal_reads_cap() -> int:
    """Cap NOMINAL : ramp programme si une date est atteinte, sinon env/defaut."""
    ramped = _ramp_cap(_today_local())
    if ramped is not None:
        return ramped
    try:
        return int(os.environ.get(
            "EKOALU_DAILY_PROFILE_READS_CAP", DEFAULT_DAILY_READS_CAP,
        ))
    except (ValueError, TypeError):
        return DEFAULT_DAILY_READS_CAP


def daily_reads_cap() -> int:
    """Cap EFFECTIF du jour = nominal (env/ramp) x poids hebdo.

    LOT E (anti-signature « pile au cap ») : saturer le meme cap tous les
    jours — samedi compris — etait une signature reguliere. Le poids hebdo
    (WEEKDAY_WEIGHTS : Me 0.9, Ve 0.7, Sa 0.2, Di 0) module le nominal. Le
    plancher PACING_MIN_FLOOR du pacing intra-journee reste applique dans
    reads_budget_now.
    """
    from ekoalu.human_scheduler import budget

    factor = budget.daily_weight_factor()
    return max(0, round(_nominal_reads_cap() * factor))


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


# ── Cadencement intra-journee (anti-burst matinal) ───────────────────────
#
# Probleme constate le 15/06 (remarque Richard) : 100% des lectures partaient
# dans la 1re heure de la plage active (07h). 75 vues de profils en ~1h a la
# connexion puis silence = signature comportementale de bot, et le cap etait
# crame avant 09h => plus de budget pour les relances de la journee.
#
# Fix : le cap journalier n'est PAS disponible d'un coup au reveil. Il se
# DEBLOQUE progressivement sur la plage active (lineaire). Le daemon n'execute
# de tache lisant des fiches que dans la limite du budget deja debloque, et
# dort entre deux => les lectures s'etalent en filet sur la journee.
#
# C'est un plafond MOU (gating du daemon, jamais de raise) distinct du cap dur
# journalier (check_read_allowed, qui lui raise). Kill-switch EKOALU_READ_PACING=0.

PACING_MIN_FLOOR = 5  # lectures debloquees des l'ouverture (demarrage non fige)


def _pacing_enabled() -> bool:
    return os.environ.get("EKOALU_READ_PACING", "1").lower() in ("1", "true", "yes")


def _active_window_hours() -> tuple[int, int]:
    """Bornes [start, end[ de la plage active, en heure locale."""
    from linkedin.conf import ACTIVE_END_HOUR, ACTIVE_START_HOUR
    return ACTIVE_START_HOUR, ACTIVE_END_HOUR


def reads_budget_now(now: datetime | None = None) -> int:
    """Budget de lectures DEBLOQUE a cet instant (etalement intra-journee).

    Lineaire sur la plage active : a l'ouverture ~PACING_MIN_FLOOR, en fin de
    plage = cap plein. Hors plage active (avant ouverture) = plancher ; apres
    fermeture ou pacing desactive = cap plein (pas de gating). Force un debit
    ~cap / duree_plage au lieu d'un burst au reveil.
    """
    cap = daily_reads_cap()
    if not _pacing_enabled():
        return cap
    now = now or datetime.now()
    start, end = _active_window_hours()
    minutes_into = (now.hour - start) * 60 + now.minute
    total = max(1, (end - start) * 60)
    if minutes_into >= total:
        return cap          # plage terminee : tout le cap est disponible
    if minutes_into <= 0:
        return min(cap, PACING_MIN_FLOOR)   # avant/au tout debut de plage
    import math
    budget = math.ceil(cap * minutes_into / total)
    return min(cap, max(PACING_MIN_FLOOR, budget))


def is_paced_cap_reached(now: datetime | None = None) -> bool:
    """True si le budget de lectures DEBLOQUE a cet instant est atteint.

    Inclut le cap dur (un cap dur atteint implique le plafond mou atteint).
    Utilise par le daemon pour temporiser entre les bursts de lectures.
    """
    if is_cap_reached():
        return True
    return reads_today() >= reads_budget_now(now)


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
    succes — LinkedIn voit la requete meme si elle echoue. La ventilation par
    ``source`` (get_profile / get_connection_degree...) alimente la ligne
    « dont » du dashboard.
    """
    from django.db.models import F

    from ekoalu.read_guard.models import ProfileReadDay

    today = _today_local()
    row, _created = ProfileReadDay.objects.get_or_create(date=today)
    ProfileReadDay.objects.filter(pk=row.pk).update(count=F("count") + 1)
    row.refresh_from_db()

    # Ventilation par usage (read-modify-write : course bénigne, le total
    # de référence reste `count` qui est lui atomique). Pour une lecture de
    # fiche (get_profile), l'usage actif (selection/follow_up) prime sur le nom
    # de méthode → le dashboard isole les lectures qui ont servi à la sélection.
    purpose = _read_purpose.get()
    key = source or "autre"
    if purpose and source == "get_profile":
        key = purpose
    row.sources[key] = row.sources.get(key, 0) + 1
    ProfileReadDay.objects.filter(pk=row.pk).update(sources=row.sources)

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
