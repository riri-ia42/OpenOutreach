"""Nettoyage rétroactif : leads refusés mais encore actifs (= "noms qui reviennent").

Un lead qui a au moins un PendingOutbound FOLLOW_UP en statut REJECTED mais dont
un Deal est encore dans un état actif (Qualified/Ready/Pending/Connected) a été
refusé par un chemin qui n'a pas désactivé le lead (ancien refus Django Admin
avant le fix 03/06). Le daemon régénère donc un message à chaque cycle.

Cette commande désactive ces leads (cross-campagne) — même logique que le refus.

Usage :
    python manage.py stop_recurring_rejected --dry-run   # affiche sans rien écrire
    python manage.py stop_recurring_rejected             # exécute

Idempotent : une fois les leads disqualifiés, ils ne ressortent plus.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Désactive les leads refusés mais encore actifs (follow-up rejected + Deal actif)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="N'écrit rien, affiche juste ce qui serait fait",
        )

    def handle(self, *args, **opts):
        from crm.models import Deal
        from ekoalu.lead_exclusion import disqualify_leads
        from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound
        from linkedin.enums import ProfileState

        dry_run = opts["dry_run"]
        active = [
            ProfileState.QUALIFIED.value,
            ProfileState.READY_TO_CONNECT.value,
            ProfileState.PENDING.value,
            ProfileState.CONNECTED.value,
        ]

        # public_ids ayant un follow-up REJECTED
        rejected_ids = set(
            PendingOutbound.objects.filter(
                kind=OutboundKind.FOLLOW_UP,
                status=OutboundStatus.REJECTED,
            ).values_list("prospect_public_id", flat=True)
        )

        # ... et dont un Deal est encore actif (donc régénérera)
        stuck = sorted(
            Deal.objects.filter(
                lead__public_identifier__in=rejected_ids,
                lead__disqualified=False,
                state__in=active,
            ).values_list("lead__public_identifier", flat=True).distinct()
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY-RUN (rien écrit en base) ==="))
        self.stdout.write(f"Follow-ups rejetés (public_ids distincts) : {len(rejected_ids)}")
        self.stdout.write(f"Leads refusés mais encore actifs          : {len(stuck)}")
        for pid in stuck:
            self.stdout.write(f"  - {pid}")

        if not stuck:
            self.stdout.write(self.style.SUCCESS("Rien à nettoyer."))
            return

        if dry_run:
            return

        n_leads, n_deals = disqualify_leads(stuck, "Nettoyage refus récurrents (03/06)")
        self.stdout.write(self.style.SUCCESS(
            f"Leads disqualifiés : {n_leads} — Deals clôturés : {n_deals}",
        ))
