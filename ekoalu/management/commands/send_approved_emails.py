"""Envoie les cold mails approuvés via Microsoft Graph (richard@ekoalu.com).

Sélectionne les PendingOutbound `kind in (email_cold, email_follow_up)` en statut
APPROVED, les envoie via le sender `ekoalu.email_canal.sender`, et met à jour
leur statut (SENT + sent_at OU FAILED + error_message).

Respecte par défaut le scheduler humain EKOALU (`is_action_allowed_now`).
Hard cap par exécution via `--max`.

Usage :
    python manage.py send_approved_emails --dry-run             # liste, n'envoie pas
    python manage.py send_approved_emails --max 3               # envoie 3 max
    python manage.py send_approved_emails --max 3 --ignore-schedule
"""
from __future__ import annotations

import logging
import random
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from ekoalu import conf
from ekoalu.email_canal.sender import EMAIL_KINDS, send_cold_email
from ekoalu.human_scheduler import is_action_allowed_now
from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound

logger = logging.getLogger(__name__)

# Un SENDING plus vieux que ça au début d'un run = crash pendant l'envoi
# précédent → issue incertaine. On repasse APPROVED avec un warning EXPLICITE
# (pas de renvoi silencieux : Richard peut vérifier si le mail est parti).
STALE_SENDING_HOURS = 2


def recover_stale_sending() -> int:
    """Repasse APPROVED les PendingOutbound email bloqués en SENDING > 2h."""
    cutoff = timezone.now() - timedelta(hours=STALE_SENDING_HOURS)
    stale = list(
        PendingOutbound.objects
        .filter(kind__in=EMAIL_KINDS, status=OutboundStatus.SENDING,
                claimed_at__lt=cutoff)
    )
    for po in stale:
        po.status = OutboundStatus.APPROVED
        po.save(update_fields=["status"])
        logger.warning(
            "PO #%s bloqué en SENDING depuis %s (crash pendant l'envoi ?) — "
            "repassé APPROVED : il sera RENVOYÉ à la prochaine passe. "
            "VÉRIFIER manuellement qu'un doublon n'est pas déjà parti vers %s.",
            po.pk, po.claimed_at, po.prospect_public_id,
        )
    return len(stale)


class Command(BaseCommand):
    help = "Envoie les cold mails approuvés (kind=email_cold/email_follow_up) via Graph."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max", type=int, default=5,
            help="Hard cap d'envois dans cette passe (défaut 5).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="N'envoie rien, log juste ce qui serait envoyé.",
        )
        parser.add_argument(
            "--ignore-schedule", action="store_true",
            help="Bypass is_action_allowed_now() (tests / envoi exceptionnel).",
        )

    def handle(self, *args, **opts):
        from ekoalu.emergency_stop import is_stopped
        if is_stopped():
            self.stdout.write(self.style.ERROR(
                "ARRET D'URGENCE actif (data/emergency_stop.flag) — 0 envoi. "
                "Reprise via le bouton du dashboard.",
            ))
            return

        max_n = int(opts["max"])
        dry_run = bool(opts["dry_run"])
        ignore_schedule = bool(opts["ignore_schedule"])

        # channel="email" : les jours off aléatoires LinkedIn (LOT E) ne
        # bloquent PAS le canal email — seules les plages horaires comptent.
        if not ignore_schedule and not is_action_allowed_now(channel="email"):
            self.stdout.write(self.style.WARNING(
                "Hors plage active EKOALU — 0 envoi. Utilise --ignore-schedule pour bypass.",
            ))
            return

        # Quota du jour étalé sur la journée (50 en semaine / 20 le samedi
        # matin / 0 férié-dimanche). Sans ce plafond glissant, le premier
        # créneau viderait tout le quota d'un coup = burst reconnaissable.
        from django.utils import timezone

        from ekoalu.email_canal.quota import (
            cold_mail_quota_for,
            cold_mails_sent_on,
            quota_reason,
            remaining_allowance,
        )
        today = timezone.localtime().date()
        quota = cold_mail_quota_for(today)
        already = cold_mails_sent_on(today)
        allowance = remaining_allowance()
        self.stdout.write(self.style.NOTICE(
            f"Quota du jour : {already}/{quota} déjà envoyés ({quota_reason(today)}) "
            f"— débloqué à cet instant : {allowance}",
        ))
        if allowance <= 0:
            self.stdout.write(self.style.SUCCESS(
                "Rien à envoyer maintenant (quota du jour atteint ou pas encore "
                "débloqué) — le créneau suivant prendra le relais.",
            ))
            return
        max_n = min(max_n, allowance)

        if not dry_run:
            recovered = recover_stale_sending()
            if recovered:
                self.stdout.write(self.style.WARNING(
                    f"{recovered} PO SENDING périmé(s) repassé(s) APPROVED "
                    "(voir warnings log : renvoi possible en doublon).",
                ))

        approved = list(
            PendingOutbound.objects
            .filter(kind__in=EMAIL_KINDS, status=OutboundStatus.APPROVED)
            .order_by("approved_at", "id")[:max_n]
        )
        self.stdout.write(self.style.NOTICE(
            f"À envoyer : {len(approved)} (cap={max_n}, dry_run={dry_run}, "
            f"ignore_schedule={ignore_schedule})",
        ))

        if not approved:
            self.stdout.write(self.style.SUCCESS("Aucun cold mail approuvé en attente."))
            return

        sent_count = 0
        failed_count = 0

        for i, po in enumerate(approved):
            preview = po.subject[:60] or "(sans objet)"
            self.stdout.write(f"\n→ #{po.pk} {po.prospect_company or po.prospect_public_id} | {preview}")

            if dry_run:
                self.stdout.write(self.style.NOTICE("  [DRY-RUN] non envoyé"))
                continue

            # Claim atomique (LOT D) : APPROVED→SENDING via UPDATE conditionnel.
            # rowcount 0 = un autre run (chevauchement Task Scheduler, jitter
            # 75 min) l'a déjà pris → skip, PAS de double envoi.
            claimed = PendingOutbound.objects.filter(
                pk=po.pk, status=OutboundStatus.APPROVED,
            ).update(status=OutboundStatus.SENDING, claimed_at=timezone.now())
            if not claimed:
                self.stdout.write(self.style.WARNING(
                    "  ↷ déjà pris par un autre process (claim raté), skip",
                ))
                logger.info("send_approved_emails: PO #%s déjà claimé ailleurs, skip", po.pk)
                continue

            success, error = send_cold_email(po)
            if success:
                po.status = OutboundStatus.SENT
                po.sent_at = timezone.now()
                po.error_message = ""
                po.save(update_fields=["status", "sent_at", "error_message"])
                sent_count += 1
                self.stdout.write(self.style.SUCCESS("  ✔ envoyé"))
            else:
                po.status = OutboundStatus.FAILED
                po.error_message = error[:1000]
                po.save(update_fields=["status", "error_message"])
                failed_count += 1
                self.stdout.write(self.style.ERROR(f"  ✘ échec : {error}"))

            # Délai humanisé entre 2 envois (sauf après le dernier)
            if i < len(approved) - 1:
                delay = random.uniform(conf.MIN_DELAY_SECONDS, conf.MAX_DELAY_SECONDS)
                self.stdout.write(f"  ⏳ délai EKOALU {delay:.0f}s avant prochain envoi")
                time.sleep(delay)

        self.stdout.write(self.style.SUCCESS(
            f"\n--- Bilan ---\n"
            f"  envoyés : {sent_count}\n"
            f"  échecs  : {failed_count}\n"
            f"  dry_run : {dry_run}",
        ))
        logger.info("send_approved_emails: sent=%d failed=%d dry_run=%s",
                    sent_count, failed_count, dry_run)
