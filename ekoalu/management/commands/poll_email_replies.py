"""Polle l'inbox Outlook et génère des brouillons de réponse pour les Lead connus.

Pour chaque message reçu depuis `--since-hours` heures :
- match `from_email` sur `Lead.contact_email`
- si match : crée `PendingReply(channel=email, intent=..., ai_draft=...)`
- sinon : ignore (mail non lié à la prospection)

Idempotence garantie par `inbound_message_id` (ID Graph).

Last-poll stocké en `data/email_inbox_last_poll.txt` (ISO UTC). En l'absence
de fichier, on poll les `--since-hours` dernières heures.

Usage :
    python manage.py poll_email_replies --since-hours 24
    python manage.py poll_email_replies --since-hours 1 --max 20
    python manage.py poll_email_replies --no-claude       # squelette sans appel Claude
    python manage.py poll_email_replies --dry-run         # affiche, n'écrit rien
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


def _last_poll_path() -> Path:
    base = Path(getattr(settings, "BASE_DIR", "."))
    return base / "data" / "email_inbox_last_poll.txt"


def _read_last_poll() -> str | None:
    p = _last_poll_path()
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _write_last_poll(iso_utc: str) -> None:
    p = _last_poll_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(iso_utc, encoding="utf-8")


class Command(BaseCommand):
    help = "Polle l'inbox Outlook et crée des PendingReply pour les Lead connus."

    def add_arguments(self, parser):
        parser.add_argument(
            "--since-hours", type=int, default=24,
            help="Fenêtre de poll si pas de last_poll connu (défaut 24h).",
        )
        parser.add_argument(
            "--max", type=int, default=50,
            help="Cap de messages à récupérer par appel Graph (défaut 50, max 100).",
        )
        parser.add_argument(
            "--no-claude", action="store_true",
            help="Crée les PendingReply sans appeler Claude (squelette, debug).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="N'écrit rien en DB — affiche juste les messages fetched.",
        )
        parser.add_argument(
            "--force-since",
            help="Override le since (ex: '2026-05-27T08:00:00Z'). Ignore last_poll.",
        )

    def handle(self, *args, **opts):
        # Détermine la borne basse de poll
        if opts["force_since"]:
            since = opts["force_since"]
        else:
            last_poll = _read_last_poll()
            if last_poll:
                since = last_poll
            else:
                hours = int(opts["since_hours"])
                since_dt = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
                since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        max_n = int(opts["max"])
        dry_run = bool(opts["dry_run"])
        no_claude = bool(opts["no_claude"])

        self.stdout.write(self.style.NOTICE(
            f"Poll inbox since={since} | max={max_n} | dry_run={dry_run} | "
            f"no_claude={no_claude}",
        ))

        if dry_run:
            from ekoalu.notifications.graph_mailer import list_inbox_messages
            msgs = list_inbox_messages(since_iso_utc=since, max_n=max_n)
            self.stdout.write(f"Messages fetched : {len(msgs)}")
            for m in msgs[:20]:
                self.stdout.write(
                    f"  - [{m['received_at']}] {m['from_email']:40} | "
                    f"{m['subject'][:60]}",
                )
            self.stdout.write(self.style.SUCCESS("Dry-run terminé (aucun PendingReply créé)."))
            return

        from ekoalu.email_canal.inbox_poller import poll_inbox
        stats = poll_inbox(
            since_iso_utc=since, max_n=max_n,
            generate_drafts=not no_claude,
        )

        # Avance le marqueur last_poll au "maintenant" UTC (on a tout traité jusque-là)
        now_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _write_last_poll(now_iso)

        self.stdout.write(self.style.SUCCESS(
            f"\n--- Bilan ---\n"
            f"  fetched         : {stats.fetched}\n"
            f"  déjà vus        : {stats.already_seen}\n"
            f"  pas de lead     : {stats.no_lead_match}\n"
            f"  brouillons OK   : {stats.drafts_created}\n"
            f"  brouillons KO   : {stats.drafts_failed}\n"
            f"  last_poll → {now_iso}",
        ))
        logger.info("poll_email_replies: fetched=%d created=%d failed=%d no_match=%d seen=%d",
                    stats.fetched, stats.drafts_created, stats.drafts_failed,
                    stats.no_lead_match, stats.already_seen)
