"""Envoie les PendingReply email approuvés via Microsoft Graph (reply threadé).

Sélectionne les `PendingReply(channel=email, status=APPROVED)`, envoie via
`ekoalu.email_canal.reply_sender`, et met à jour le statut :
- succès → SENT + sent_at = now
- échec  → FAILED + error_message = "..."

Respecte le scheduler EKOALU par défaut (`is_action_allowed_now`).
Hard cap par exécution via `--max`.

Usage :
    python manage.py send_approved_email_replies --dry-run
    python manage.py send_approved_email_replies --max 5
    python manage.py send_approved_email_replies --max 5 --ignore-schedule
"""
from __future__ import annotations

import logging
import random
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from ekoalu import conf
from ekoalu.email_canal.reply_sender import send_email_reply
from ekoalu.human_scheduler import is_action_allowed_now
from ekoalu.inbox_assist.models import PendingReply

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Envoie les PendingReply email approuvés via Graph reply (threading auto)."

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
            help="Bypass is_action_allowed_now().",
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
            PendingReply.objects
            .filter(channel=PendingReply.CHANNEL_EMAIL,
                    status=PendingReply.Status.APPROVED)
            .order_by("created_at", "id")[:max_n]
        )
        self.stdout.write(self.style.NOTICE(
            f"À envoyer : {len(approved)} (cap={max_n}, dry_run={dry_run}, "
            f"ignore_schedule={ignore_schedule})",
        ))

        if not approved:
            self.stdout.write(self.style.SUCCESS("Aucune reply approuvée en attente."))
            return

        sent_count = 0
        failed_count = 0

        for i, pr in enumerate(approved):
            preview = pr.inbound_subject[:60] or "(sans objet)"
            self.stdout.write(f"\n→ PR #{pr.pk} {pr.sender_email} | Re: {preview}")

            if dry_run:
                self.stdout.write(self.style.NOTICE("  [DRY-RUN] non envoyé"))
                continue

            success, error = send_email_reply(pr)
            if success:
                pr.status = PendingReply.Status.SENT
                pr.sent_at = timezone.now()
                pr.error_message = ""
                pr.save(update_fields=["status", "sent_at", "error_message"])
                sent_count += 1
                self.stdout.write(self.style.SUCCESS("  ✔ envoyé"))
            else:
                pr.status = PendingReply.Status.FAILED
                pr.error_message = error[:1000]
                pr.save(update_fields=["status", "error_message"])
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
        logger.info("send_approved_email_replies: sent=%d failed=%d dry_run=%s",
                    sent_count, failed_count, dry_run)
