"""Cree des PendingOutbound INVITATION APPROVED pour les Deals Qualified
EKOALU qui n'en ont pas encore.

Cas d'usage : rattrapage apres requalification manuelle (cf vue
deals_filtered/requalify). Si Richard a clique "Remettre en boucle" avant
l'introduction de l'auto-requeue, ses Deals Qualified sont sans invitation.

Usage :
    python manage.py requeue_qualified_invitations            # rattrape tout
    python manage.py requeue_qualified_invitations --dry-run  # preview
    python manage.py requeue_qualified_invitations --since=2026-05-20  # depuis date
"""
from __future__ import annotations

from datetime import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Cree PendingOutbound INVITATION APPROVED pour les Deals Qualified EKOALU sans invitation en file."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--since",
            type=str,
            default=None,
            help="Filtre Deals avec update_date >= cette date (format YYYY-MM-DD)",
        )

    def handle(self, *args, **opts):
        from crm.models import Deal
        from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound
        from ekoalu.views import _requeue_invitation_approved

        qs = (
            Deal.objects
            .filter(state="Qualified", campaign__name__startswith="EKOALU - ")
            .select_related("lead", "campaign")
        )
        if opts.get("since"):
            try:
                since_dt = timezone.make_aware(
                    datetime.strptime(opts["since"], "%Y-%m-%d"),
                    timezone.get_current_timezone(),
                )
                qs = qs.filter(update_date__gte=since_dt)
            except ValueError:
                self.stderr.write(f"Date invalide : {opts['since']} (attendu YYYY-MM-DD)")
                return

        dry_run = opts.get("dry_run", False)
        eligible = []
        skipped = 0
        for d in qs:
            existing = PendingOutbound.objects.filter(
                prospect_public_id=d.lead.public_identifier,
                campaign_id=d.campaign_id,
                kind=OutboundKind.INVITATION,
            ).exclude(status__in=[OutboundStatus.SENT, OutboundStatus.REJECTED]).first()
            if existing and existing.status == OutboundStatus.APPROVED:
                skipped += 1
                continue
            eligible.append(d)

        self.stdout.write(
            f"\n{'[DRY-RUN] ' if dry_run else ''}"
            f"{len(eligible)} Deals Qualified a (re)mettre en file invitation. "
            f"{skipped} deja en APPROVED.\n"
        )
        for d in eligible:
            self.stdout.write(
                f"  - {d.lead.public_identifier:35} | {d.campaign.name[:40]}"
            )
            if not dry_run:
                po = _requeue_invitation_approved(d)
                if po:
                    self.stdout.write(self.style.SUCCESS(f"    -> PendingOutbound #{po.pk} APPROVED"))
        if dry_run:
            self.stdout.write(self.style.WARNING("\n[DRY-RUN] Aucune modification appliquee."))
        else:
            self.stdout.write(self.style.SUCCESS(f"\nOK : {len(eligible)} invitation(s) en file APPROVED."))
