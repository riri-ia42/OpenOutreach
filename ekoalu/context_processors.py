"""Context processors EKOALU - donnees globales pour toutes les pages.

Le base template ekoalu/base.html consomme ces variables pour afficher le
header (statut daemon + badges) sur toutes les pages.
"""
from __future__ import annotations

from django.utils import timezone


def ekoalu_globals(request):
    """Variables partagees par toutes les pages /ekoalu/.

    Renvoie un dict vide pour les requetes non-staff ou hors namespace
    /ekoalu/ afin de ne pas faire de requetes inutiles.
    """
    path = getattr(request, "path", "")
    if not path.startswith("/ekoalu/"):
        return {}
    user = getattr(request, "user", None)
    if not user or not user.is_staff:
        return {}

    from ekoalu import conf
    from ekoalu.company_validation.models import ApprovedCompany, CompanyStatus
    from ekoalu.human_scheduler import is_action_allowed_now
    from ekoalu.human_scheduler.windows import (
        is_active_day,
        is_in_active_window,
        is_in_lunch_break,
        next_active_window_start,
    )
    from ekoalu.inbox_assist.models import PendingReply
    from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound
    from linkedin.models import LinkedInProfile

    now = timezone.localtime()
    active = is_action_allowed_now(now)
    reason = ""
    if not active:
        if not is_active_day(now):
            reason = "Jour off (samedi reduit / dimanche off)"
        elif is_in_lunch_break(now):
            reason = "Pause dejeuner (12h-14h)"
        elif not is_in_active_window(now):
            reason = "Hors plages actives (7h30-12h + 14h-20h)"
        else:
            reason = "Action bloquee"

    profile = LinkedInProfile.objects.filter(active=True).first()

    return {
        "daemon_status": {
            "active": active,
            "reason": reason,
            "next_active_at": None if active else next_active_window_start(now),
        },
        "active_profile": profile,
        "badge_pending_outbound": PendingOutbound.objects.filter(
            status=OutboundStatus.PENDING,
        ).count(),
        "badge_inbox": PendingReply.objects.filter(
            status=PendingReply.Status.PENDING,
        ).count(),
        "badge_companies": ApprovedCompany.objects.filter(
            status=CompanyStatus.PENDING,
        ).count(),
        "now_local": now,
        "ekoalu_conf": {
            "active_windows": conf.ACTIVE_WINDOWS,
            "weekday_weights": conf.WEEKDAY_WEIGHTS,
            "weekly_target": conf.WEEKLY_INVITE_TARGET,
            "weekly_cap": conf.WEEKLY_INVITE_HARD_CAP,
            "daily_cap": conf.DAILY_INVITE_CAP,
        },
    }
