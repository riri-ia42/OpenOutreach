"""Management command : re-evalue les Deals Failed contre d'autres Campaigns.

Quand un Deal a ete disqualifie contre Campaign A (ex: EG tertiaire), il
correspond peut-etre a la cible de Campaign B (ex: Promoteur). Ce script
recree des Deals QUALIFIED contre les Campaigns demandees pour permettre
au GP de re-qualifier via Claude.

Usage:
    # Voir ce qui serait fait
    python manage.py reeval_failed_leads --dry-run --target-campaign 4

    # Reevaluer les leads Failed (Campaign 1) contre Campaign 4 (maçonnerie)
    python manage.py reeval_failed_leads --target-campaign 4

    # Reevaluer contre plusieurs Campaigns
    python manage.py reeval_failed_leads --target-campaign 2,3,4,5,6,7,8
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Recree des Deals QUALIFIED pour les Leads disqualifies par d'autres "
        "Campaigns, afin que le daemon les reevalue contre les Campaigns cibles."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--target-campaign",
            type=str,
            required=True,
            help="ID(s) de Campaign cible (séparés par virgule). Ex: 4 ou 2,3,4",
        )
        parser.add_argument(
            "--from-campaign",
            type=int,
            default=None,
            help="Limiter aux leads Failed contre cette Campaign (ex: 1). "
                 "Si non précisé : tous les Leads non disqualifies au niveau Lead.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Affiche ce qui serait fait sans modifier la DB",
        )

    def handle(self, *args, **options):
        from crm.models import Deal, Lead
        from linkedin.enums import ProfileState
        from linkedin.models import Campaign

        dry_run = options["dry_run"]
        target_ids_raw = options["target_campaign"]
        try:
            target_ids = [int(x.strip()) for x in target_ids_raw.split(",")]
        except ValueError:
            raise CommandError("--target-campaign doit etre des entiers separes par virgule")

        from_pk = options.get("from_campaign")

        # Verifier que les Campaigns existent
        targets = list(Campaign.objects.filter(pk__in=target_ids))
        if len(targets) != len(target_ids):
            found_pks = {c.pk for c in targets}
            missing = [pk for pk in target_ids if pk not in found_pks]
            raise CommandError(f"Campaigns introuvables: {missing}")

        # Selectionner les leads candidats
        leads_qs = Lead.objects.filter(disqualified=False, embedding__isnull=False)
        if from_pk:
            # Leads avec un Deal Failed contre `from_pk`
            failed_lead_ids = Deal.objects.filter(
                campaign_id=from_pk, state=ProfileState.FAILED,
            ).values_list("lead_id", flat=True)
            leads_qs = leads_qs.filter(pk__in=failed_lead_ids)

        leads = list(leads_qs)

        self.stdout.write(
            f"\n{'[DRY-RUN] ' if dry_run else ''}Reeval Failed Leads"
            f"\n  Cibles : Campaigns {target_ids} ({[c.name for c in targets]})"
            f"\n  Source : {f'Failed contre Campaign #{from_pk}' if from_pk else 'TOUS Leads valides'}"
            f"\n  Leads candidats : {len(leads)}"
            f"\n" + "=" * 70
        )

        total_created = 0
        total_skipped = 0
        for lead in leads:
            for camp in targets:
                # Skip si Deal deja present pour ce lead + Campaign
                if Deal.objects.filter(lead=lead, campaign=camp).exists():
                    total_skipped += 1
                    continue
                if dry_run:
                    self.stdout.write(
                        f"  [WOULD CREATE] {lead.public_identifier} -> {camp.name[:40]}"
                    )
                else:
                    Deal.objects.create(
                        lead=lead,
                        campaign=camp,
                        state=ProfileState.QUALIFIED,
                    )
                    self.stdout.write(
                        f"  [CREATE] {lead.public_identifier} -> {camp.name[:40]}"
                    )
                total_created += 1

        self.stdout.write("\n" + "=" * 70)
        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"[DRY-RUN] {total_created} Deals seraient crees, "
                f"{total_skipped} skipped (deja existant)."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"OK : {total_created} Deals crees, {total_skipped} skipped."
            ))
            self.stdout.write(
                "\nProchaine etape : le daemon va automatiquement re-qualifier "
                "ces Deals contre Claude au prochain cycle."
            )
