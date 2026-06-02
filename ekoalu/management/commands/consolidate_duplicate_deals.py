"""Consolidation cross-campagne : 1 Lead = 1 Deal actif maximum.

Usage :
    python manage.py consolidate_duplicate_deals --dry-run    # affiche sans rien écrire
    python manage.py consolidate_duplicate_deals              # exécute la consolidation
    python manage.py consolidate_duplicate_deals -v 2         # verbose (détail par lead)

Idempotent : peut être relancé sans danger.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Consolide les Deals doublons cross-campagne (1 Lead = 1 Deal actif)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="N'écrit rien, affiche juste ce qui serait fait",
        )

    def handle(self, *args, **opts):
        from ekoalu.dedup import consolidate_duplicate_deals

        dry_run = opts["dry_run"]
        report = consolidate_duplicate_deals(dry_run=dry_run)

        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY-RUN (rien écrit en base) ==="))
        self.stdout.write(self.style.SUCCESS(
            f"Leads scannés (états actifs) : {report.leads_scanned}",
        ))
        self.stdout.write(self.style.SUCCESS(
            f"Leads avec doublons          : {report.leads_with_duplicates}",
        ))
        self.stdout.write(self.style.SUCCESS(
            f"Deals -> duplicate_campaign   : {report.deals_demoted_to_duplicate}",
        ))
        self.stdout.write(self.style.SUCCESS(
            f"Connected/pre_existing -> Completed : {report.deals_normalized_pre_existing}",
        ))
        self.stdout.write(self.style.SUCCESS(
            f"Connected/duplicate_camp -> Completed : {report.deals_normalized_inconsistent}",
        ))
        self.stdout.write(self.style.SUCCESS(
            f"Completed historiques dedup  : {report.completed_history_demoted}",
        ))
        self.stdout.write(self.style.SUCCESS(
            f"PendingOutbound annulés      : {report.pending_outbound_cancelled}",
        ))

        if opts["verbosity"] >= 2 and report.details:
            self.stdout.write("\n--- Détail par lead consolidé ---")
            for d in report.details:
                self.stdout.write(
                    f"  {d['lead']:35s} demoted #{d['demoted_deal_id']} "
                    f"({d['demoted_state']:10s} on '{d['demoted_campaign'][:30]:30s}') "
                    f"-> keep #{d['kept_deal_id']} ('{d['kept_campaign'][:30]:30s}') "
                    f"PO_cancel={d['po_cancelled']}",
                )
