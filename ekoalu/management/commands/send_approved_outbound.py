"""Management command : envoie les PendingOutbound approuvés.

Ouvre une session LinkedIn (utilise le LinkedInProfile actif), traite jusqu à
N messages approuvés en respectant les délais EKOALU, ferme la session.

À lancer manuellement ou via cron quand des messages sont en attente.

Usage :
    python manage.py send_approved_outbound --dry-run
    python manage.py send_approved_outbound --max 3
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Envoie les PendingOutbound approuvés via LinkedIn "
        "(en respectant les délais EKOALU)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="N'envoie rien, log ce qui serait fait",
        )
        parser.add_argument(
            "--max",
            type=int,
            default=5,
            help="Nombre max de messages à envoyer (defaut: 5)",
        )
        parser.add_argument(
            "--handle",
            type=str,
            default=None,
            help="Username Django (defaut: premier profil actif)",
        )

    def handle(self, *args, **options):
        from ekoalu.outbound_validation.config import is_approval_required
        from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound
        from ekoalu.outbound_validation.sender import process_approved_queue
        from linkedin.browser.registry import get_or_create_session, resolve_profile

        dry_run = options["dry_run"]
        max_messages = options["max"]

        # Verif qu'on est bien en mode validation (sinon le patch n'a pas tourné)
        if not is_approval_required():
            self.stdout.write(self.style.WARNING(
                "EKOALU_APPROVAL_MODE=auto_send — la queue PendingOutbound est vide en mode normal."
            ))

        # Compter ce qu'on a à envoyer
        nb_approved = PendingOutbound.objects.filter(status=OutboundStatus.APPROVED).count()
        if nb_approved == 0:
            self.stdout.write("Aucun PendingOutbound 'approved' à envoyer.")
            return

        self.stdout.write(
            f"\n{'[DRY-RUN] ' if dry_run else ''}Envoi des PendingOutbound approuvés"
            f"\n  Total approved en queue : {nb_approved}"
            f"\n  Max à traiter ce run : {max_messages}"
            f"\n" + "=" * 70
        )

        # Ouvrir session LinkedIn
        profile = resolve_profile(options.get("handle"))
        if not profile:
            raise CommandError(
                "Aucun LinkedInProfile actif trouvé. "
                "Lancez `python manage.py configure_ekoalu` d'abord."
            )

        self.stdout.write(f"\nLinkedInProfile : {profile.linkedin_username}")
        self.stdout.write(f"\nOuverture session LinkedIn (browser Patchright)...")

        session = get_or_create_session(profile)
        if not session.campaigns:
            raise CommandError(f"Aucune Campaign trouvee pour {profile}")

        session.campaign = session.campaigns[0]  # default
        self.stdout.write(f"Session OK\n")

        # Process
        stats = process_approved_queue(
            session=session,
            max_messages=max_messages,
            dry_run=dry_run,
        )

        self.stdout.write("\n" + "=" * 70)
        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"[DRY-RUN] processed={stats['processed']} (aucun envoi réel)"
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"OK : sent={stats['sent']} failed={stats['failed']} "
                f"skipped={stats['skipped']}"
            ))
