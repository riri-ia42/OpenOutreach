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

from django.core.management.base import BaseCommand
from django.utils import timezone

from ekoalu import conf
from ekoalu.email_canal.sender import EMAIL_KINDS, send_cold_email
from ekoalu.human_scheduler import is_action_allowed_now
from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound

logger = logging.getLogger(__name__)


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
        max_n = int(opts["max"])
        dry_run = bool(opts["dry_run"])
        ignore_schedule = bool(opts["ignore_schedule"])

        if not ignore_schedule and not is_action_allowed_now():
            self.stdout.write(self.style.WARNING(
                "Hors plage active EKOALU — 0 envoi. Utilise --ignore-schedule pour bypass.",
            ))
            return

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
