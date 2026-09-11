"""Relevé des canaux et corrections applicables."""
from __future__ import annotations

import datetime as dt
import logging

from ekoalu.pipeline_margin.measure import (
    DEFAULT_MARGIN_DAYS, Canal, jour_ouvre_suivant, prochains_jours_actifs,
)

logger = logging.getLogger(__name__)


def _plafond_invitations(jour: dt.date) -> int:
    from ekoalu import conf
    from ekoalu.human_scheduler import budget

    return max(0, round(conf.DAILY_INVITE_CAP * budget.daily_weight_factor(jour)))


def _envois(jour: dt.date, kinds: list[str]) -> int:
    from django.utils import timezone

    from ekoalu.outbound_validation.models import PendingOutbound

    debut = timezone.make_aware(dt.datetime.combine(jour, dt.time.min))
    return PendingOutbound.objects.filter(
        kind__in=kinds, sent_at__gte=debut, sent_at__lt=debut + dt.timedelta(days=1),
    ).count()


def _stock(kind: str) -> tuple[int, int]:
    """(prêt à partir, en attente de validation de Richard) pour un type."""
    from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound

    qs = PendingOutbound.objects.filter(kind=kind)
    return (qs.filter(status=OutboundStatus.APPROVED).count(),
            qs.filter(status=OutboundStatus.PENDING).count())


def releve(jour: dt.date | None = None, marge_jours: float = DEFAULT_MARGIN_DAYS) -> list[Canal]:
    """État des canaux face aux plafonds des prochains jours actifs."""
    import math

    from django.utils import timezone

    from ekoalu.email_canal.followup import followup_quota_for
    from ekoalu.email_canal.quota import cold_mail_quota_for
    from ekoalu.outbound_validation.models import OutboundKind

    jour = jour or timezone.localdate()
    demain = jour_ouvre_suivant(jour)
    hier = jour - dt.timedelta(days=1)
    fenetre = prochains_jours_actifs(jour, max(1, math.ceil(marge_jours)))

    pret, valid = _stock(OutboundKind.EMAIL_COLD)
    cold = Canal(
        cle="email_cold", libelle="Cold mails",
        besoin_demain=cold_mail_quota_for(demain),
        besoin_marge=sum(cold_mail_quota_for(d) for d in fenetre),
        jours_fenetre=len(fenetre),
        pret=pret, en_validation=valid,
        realise_hier=_envois(hier, [OutboundKind.EMAIL_COLD]),
        plafond_hier=cold_mail_quota_for(hier),
        correction="generate_cold_emails",
    )

    pret, valid = _stock(OutboundKind.EMAIL_FOLLOW_UP)
    relance = Canal(
        cle="email_follow_up", libelle="Relances mail",
        besoin_demain=followup_quota_for(demain),
        besoin_marge=sum(followup_quota_for(d) for d in fenetre),
        jours_fenetre=len(fenetre),
        pret=pret, en_validation=valid,
        realise_hier=_envois(hier, [OutboundKind.EMAIL_FOLLOW_UP]),
        plafond_hier=followup_quota_for(hier),
        correction="generate_email_followups",
    )

    invit = _canal_invitations(demain, hier, fenetre)
    vivier = _canal_vivier(demain, fenetre)
    return [cold, relance, invit, vivier]


def _canal_invitations(demain: dt.date, hier: dt.date, fenetre: list[dt.date]) -> Canal:
    """Prospects prêts à recevoir une invitation LinkedIn.

    Le « stock » n'est pas une file de messages mais un vivier de deals :
    qualifiés et promus prêts à connecter. C'est lui qui était à sec au 11/09
    (10 prêts pour un plafond de 12), pas la file de validation.
    """
    from crm.models import Deal
    from ekoalu.outbound_validation.models import OutboundKind
    from linkedin.enums import ProfileState

    prets = Deal.objects.filter(state__in=[ProfileState.READY_TO_CONNECT.value,
                                           ProfileState.QUALIFIED.value]).count()
    _, en_validation = _stock(OutboundKind.INVITATION)
    return Canal(
        cle="linkedin_invite", libelle="Invitations LinkedIn",
        besoin_demain=_plafond_invitations(demain),
        besoin_marge=sum(_plafond_invitations(d) for d in fenetre),
        jours_fenetre=len(fenetre),
        pret=prets, en_validation=en_validation,
        realise_hier=_envois(hier, [OutboundKind.INVITATION]),
        plafond_hier=_plafond_invitations(hier),
        correction="enrich_backlog",
        notes=["le stock est un vivier de prospects qualifiés, pas une file de messages"],
    )


def _canal_vivier(demain: dt.date, fenetre: list[dt.date]) -> Canal:
    """Leads disponibles pour fabriquer des cold mails."""
    from ekoalu.email_canal.pool import cold_mail_candidates
    from ekoalu.email_canal.quota import cold_mail_quota_for

    pool, _ = cold_mail_candidates()
    return Canal(
        cle="vivier", libelle="Vivier cold mail",
        besoin_demain=cold_mail_quota_for(demain),
        besoin_marge=sum(cold_mail_quota_for(d) for d in fenetre),
        jours_fenetre=len(fenetre),
        pret=len(pool), en_validation=0,
        realise_hier=0, plafond_hier=0,
        correction="",   # un import est une décision, pas une routine
        notes=["réalimentation = import DECP ou BDD PROSPECT, décision de Richard"],
    )


def corriger(canal: Canal, manque: int) -> str:
    """Applique la correction du canal. Renvoie le compte rendu."""
    from django.core.management import call_command

    if not canal.correction or manque <= 0:
        return ""
    try:
        if canal.correction == "generate_cold_emails":
            call_command("generate_cold_emails", limit=manque)
        elif canal.correction == "generate_email_followups":
            call_command("generate_email_followups", limit=manque)
        elif canal.correction == "enrich_backlog":
            # Enrichir alimente la qualification, qui alimente le vivier de
            # prospects prêts. Effet différé d'un cycle, d'où la marge.
            call_command("enrich_backlog", max=max(manque * 5, 50))
        else:
            return f"correction inconnue : {canal.correction}"
    except Exception as exc:  # noqa: BLE001 — une correction ratée ne casse pas la passe
        logger.exception("Correction %s en échec", canal.correction)
        return f"{canal.correction} en échec : {exc}"
    return f"{canal.correction} lancé pour {manque}"
