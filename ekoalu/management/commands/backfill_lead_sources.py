"""Backfill LeadDiscovery pour les leads deja en cours (avant le routage).

Le scoping de qualification ne juge un profil que pour la campagne qui l'a
DECOUVERT (LeadDiscovery). Les leads anterieurs au routage n'ont pas cette
trace. Cette commande la reconstruit a partir des Deals NON-FAILED : un lead
rattache a un Deal vivant (Qualified/Ready/Pending/Connected/Completed) est
attribue a la campagne de ce Deal, pour qu'il continue a circuler.

Les leads n'ayant que des Deals FAILED (= deja rejetes partout) ne sont PAS
backfilles : ils restent hors qualification (c'est le but, vider le bruit).

Usage :
    python manage.py backfill_lead_sources --dry-run
    python manage.py backfill_lead_sources

Idempotent (get_or_create).
"""
from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Reconstruit LeadDiscovery depuis les Deals non-FAILED (leads en cours)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Affiche sans ecrire")

    def handle(self, *args, **opts):
        from crm.models import Deal
        from linkedin.enums import ProfileState
        from ekoalu.lead_routing.models import LeadDiscovery

        dry = opts["dry_run"]

        deals = (
            Deal.objects.exclude(state=ProfileState.FAILED.value)
            .values_list("lead_id", "campaign_id")
            .distinct()
        )
        pairs = [(lid, cid) for lid, cid in deals if lid and cid]

        created = 0
        existing = 0
        for lead_id, campaign_id in pairs:
            if LeadDiscovery.objects.filter(lead_id=lead_id, campaign_id=campaign_id).exists():
                existing += 1
                continue
            if dry:
                created += 1
                continue
            _, was_created = LeadDiscovery.objects.get_or_create(
                lead_id=lead_id, campaign_id=campaign_id,
            )
            created += 1 if was_created else 0

        verb = "seraient crees" if dry else "crees"
        self.stdout.write(
            f"LeadDiscovery : {created} {verb}, {existing} deja presents "
            f"({len(pairs)} paires lead/campagne non-FAILED)."
        )
        if dry:
            self.stdout.write("(dry-run : rien ecrit)")
