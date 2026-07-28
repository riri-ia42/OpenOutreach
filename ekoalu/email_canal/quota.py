"""Quota quotidien de cold mails + étalement aléatoire sur la journée.

Décision Richard 28/07 : **50/jour du lundi au vendredi, 20 le samedi matin,
0 les jours fériés français** (et 0 le dimanche).

Deux mécanismes complémentaires, tous deux nécessaires :

1. **Quota du jour** (`cold_mail_quota_for`) — le plafond dur.
2. **Budget débloqué à l'instant t** (`send_budget_now`) — le quota se libère
   PROGRESSIVEMENT sur la plage active, avec un jitter horaire aléatoire. Sans
   ça, le premier créneau de la journée viderait tout le quota d'un coup : 50
   mails en 20 minutes puis plus rien = signature de bot (c'est exactement le
   problème corrigé côté lectures LinkedIn le 15/06, cf. `read_guard`).

L'aléatoire vient de trois couches indépendantes qui se composent :
- `RandomDelay` du Task Scheduler sur chaque créneau (0-1h) ;
- ce budget progressif, bruité par un jitter déterministe-par-jour ;
- les délais humanisés 90-1800 s entre deux mails d'un même créneau.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
import os

from ekoalu import conf
from ekoalu.human_scheduler.holidays import holiday_name

# Plancher : un créneau ne part jamais totalement à vide en début de journée,
# sinon les premiers créneaux ne servent à rien et tout se tasse le soir.
SEND_PACING_MIN_FLOOR = 3


def _weekday_target(weekday: int) -> int:
    """Cible du jour (0=lundi). Samedi = matinée seulement."""
    if weekday == 5:
        return conf.SATURDAY_COLD_MAIL_TARGET
    if weekday == 6:
        return 0
    return conf.DAILY_COLD_MAIL_TARGET


def cold_mail_quota_for(day: dt.date) -> int:
    """Nombre max de cold mails pour ce jour. 0 = aucun envoi (férié/dimanche)."""
    if holiday_name(day):
        return 0
    return _weekday_target(day.weekday())


def quota_reason(day: dt.date) -> str:
    """Explication lisible du quota (logs + rapport de conformité)."""
    ferie = holiday_name(day)
    if ferie:
        return f"jour férié ({ferie})"
    if day.weekday() == 6:
        return "dimanche"
    if day.weekday() == 5:
        return "samedi (matinée)"
    return "jour ouvré"


def _pacing_enabled() -> bool:
    return os.environ.get("EKOALU_EMAIL_PACING", "1") != "0"


def _send_window_hours(day: dt.date) -> tuple[float, float]:
    """Amplitude d'étalement des envois : toute la plage active, sauf le
    samedi où Richard veut la matinée uniquement."""
    first_start = min(w[0] for w in conf.ACTIVE_WINDOWS)
    if day.weekday() == 5:
        return first_start, conf.ACTIVE_WINDOWS[0][1]  # matin seulement
    return first_start, max(w[1] for w in conf.ACTIVE_WINDOWS)


def _daily_jitter(day: dt.date) -> float:
    """Facteur 0.85-1.15 déterministe par jour : la courbe de déblocage n'est
    jamais identique deux jours de suite (sinon le débit horaire est régulier
    au mail près, donc reconnaissable)."""
    salt = os.environ.get("EKOALU_HUMANIZE_SALT", "")
    digest = hashlib.sha256(f"email-pacing:{salt}:{day.isoformat()}".encode()).digest()
    return 0.85 + (digest[0] / 255.0) * 0.30


def send_budget_now(now: dt.datetime | None = None) -> int:
    """Nombre de cold mails autorisés depuis le début de la journée, à l'instant t.

    Croît linéairement (bruité) sur la fenêtre d'envoi du jour. Avant la
    fenêtre : le plancher. Après : le quota plein.
    """
    now = now or dt.datetime.now()
    quota = cold_mail_quota_for(now.date())
    if quota <= 0:
        return 0
    if not _pacing_enabled():
        return quota

    start, end = _send_window_hours(now.date())
    minutes_into = (now.hour - start) * 60 + now.minute
    total = max(1, int((end - start) * 60))
    if minutes_into >= total:
        return quota
    if minutes_into <= 0:
        return min(quota, SEND_PACING_MIN_FLOOR)

    budget = math.ceil(quota * (minutes_into / total) * _daily_jitter(now.date()))
    return min(quota, max(SEND_PACING_MIN_FLOOR, budget))


def cold_mails_sent_on(day: dt.date) -> int:
    """Cold mails réellement partis ce jour-là (source de vérité = DB)."""
    from django.utils import timezone

    from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

    tz = timezone.get_current_timezone()
    start = timezone.make_aware(dt.datetime.combine(day, dt.time.min), tz)
    return PendingOutbound.objects.filter(
        kind=OutboundKind.EMAIL_COLD,
        status=OutboundStatus.SENT,
        sent_at__gte=start,
        sent_at__lt=start + dt.timedelta(days=1),
    ).count()


def remaining_allowance(now: dt.datetime | None = None) -> int:
    """Combien de cold mails ce créneau a le droit d'envoyer, ici et maintenant."""
    from django.utils import timezone

    now = now or timezone.localtime()
    return max(0, send_budget_now(now) - cold_mails_sent_on(now.date()))
