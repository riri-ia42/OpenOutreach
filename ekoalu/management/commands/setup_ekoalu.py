"""Management command : crée les 8 Campaigns EKOALU dans OpenOutreach.

Usage:
    python manage.py setup_ekoalu --dry-run   # affiche ce qui serait créé
    python manage.py setup_ekoalu              # applique (idempotent)

Idempotent : si une Campaign avec le même nom existe déjà, met à jour les
champs sans la dupliquer.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from ekoalu.personas import list_personas_by_priority


class Command(BaseCommand):
    help = "Cree (ou met a jour) les 8 Campaigns EKOALU correspondant aux 8 personas."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Affiche ce qui serait fait sans modifier la DB",
        )
        parser.add_argument(
            "--user",
            type=str,
            default=None,
            help="Username Django a associer aux campagnes (defaut : premier user)",
        )

    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model
        from linkedin.models import Campaign

        User = get_user_model()
        dry_run = options["dry_run"]

        # Resolve user
        username = options.get("user")
        if username:
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                raise CommandError(f"User '{username}' introuvable.")
        else:
            user = User.objects.first()
            if not user and not dry_run:
                raise CommandError(
                    "Aucun user Django existant. Lancez d abord "
                    "`python manage.py createsuperuser` puis retentez."
                )

        personas = list_personas_by_priority()
        self.stdout.write(
            f"\n{'[DRY-RUN] ' if dry_run else ''}Setup EKOALU — {len(personas)} personas\n"
            + "=" * 70
        )

        created = 0
        updated = 0

        for persona in personas:
            campaign_name = f"EKOALU - {persona.label}"

            self.stdout.write(
                f"\n[{persona.priority}/{len(personas)}] {persona.slug}"
            )
            self.stdout.write(f"  Categorie: {persona.category.value}")
            self.stdout.write(f"  Geo: {persona.geo_scope}")
            self.stdout.write(f"  Titles: {persona.titles}")
            self.stdout.write(f"  Industries: {persona.industries}")
            self.stdout.write(f"  Keywords: {persona.search_keywords}")
            self.stdout.write(f"  Booking: {persona.booking_link}")

            if dry_run:
                self.stdout.write(self.style.WARNING(f"  -> DRY-RUN, pas de modification"))
                continue

            campaign, was_created = Campaign.objects.update_or_create(
                name=campaign_name,
                defaults={
                    "product_docs": persona.product_docs,
                    "campaign_objective": persona.campaign_objective,
                    "booking_link": persona.booking_link,
                    "is_freemium": False,
                },
            )

            if was_created:
                campaign.users.add(user)
                created += 1
                self.stdout.write(self.style.SUCCESS(f"  -> CREE (id={campaign.pk})"))
            else:
                # S assurer que le user est membre
                if not campaign.users.filter(pk=user.pk).exists():
                    campaign.users.add(user)
                updated += 1
                self.stdout.write(self.style.SUCCESS(f"  -> MIS A JOUR (id={campaign.pk})"))

        self.stdout.write("\n" + "=" * 70)
        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"[DRY-RUN] {len(personas)} campagnes seraient creees/mises a jour.")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"OK : {created} creees, {updated} mises a jour.")
            )
            self.stdout.write(
                f"\nUser proprietaire : {user.username if user else '(aucun)'}"
            )
            self.stdout.write(
                f"\nProchaine etape : lancer le daemon avec `python manage.py rundaemon`"
            )
