"""Audit de coherence CRM (Lead/Deal/PendingOutbound) + safe fixes.

Usage :
    python manage.py check_crm_integrity          # rapport seul (lecture)
    python manage.py check_crm_integrity --fix    # applique les corrections sures

Voir ekoalu/crm_integrity.py pour la liste des anomalies et la politique de fix.
Idempotent : deux runs --fix d'affilee => 2e run a zero anomalie corrigeable.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

LABELS = {
    "disqualified_active_deals": "Leads disqualifies avec Deal encore actif",
    "open_po_dead_lead": "PO ouverts d'un lead disqualifie/desinscrit",
    "open_po_obsolete_deal": "Invitations ouvertes sur deal deja traite",
    "duplicate_open_po": "Doublons de PO ouverts (meme prospect+kind)",
    "multi_active_deals": "Leads avec >=2 Deals actifs (cross-campagne)",
}


class Command(BaseCommand):
    help = "Audite la coherence Lead/Deal/PendingOutbound. --fix applique les corrections sures."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix", action="store_true",
            help="Applique les corrections sans risque (defaut : rapport seul)",
        )

    def handle(self, *args, **opts):
        from ekoalu.crm_integrity import collect_anomalies, fix_anomalies, total_issues

        anomalies = collect_anomalies()
        total = total_issues(anomalies)

        self.stdout.write(f"Anomalies detectees : {total}")
        for key, items in anomalies.items():
            label = LABELS.get(key, key)
            self.stdout.write(f"  {label} : {len(items)}")
            for item in items[:10]:
                self.stdout.write(f"     - {item}")
            if len(items) > 10:
                self.stdout.write(f"     ... et {len(items) - 10} autres")

        if total == 0:
            self.stdout.write(self.style.SUCCESS("CRM coherent, rien a corriger."))
            return

        if not opts["fix"]:
            self.stdout.write(self.style.WARNING(
                "Rapport seul (--fix pour corriger). multi_active_deals se traite via"
                " 'manage.py consolidate_duplicate_deals'.",
            ))
            return

        fixed = fix_anomalies(anomalies)
        for key, n in fixed.items():
            if n:
                self.stdout.write(f"  Corrige {LABELS.get(key, key)} : {n}")
        if anomalies["multi_active_deals"]:
            self.stdout.write(self.style.WARNING(
                f"  Non corrige ici : {len(anomalies['multi_active_deals'])} leads multi-deals"
                " -> lancer 'manage.py consolidate_duplicate_deals'",
            ))
        self.stdout.write(self.style.SUCCESS("Corrections appliquees."))
