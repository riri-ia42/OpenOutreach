"""Management command : configure OpenOutreach pour EKOALU.

Lit les variables depuis :
1. `.env.production` à la racine du projet (parse via python-dotenv — SAFE
   pour caractères spéciaux dans passwords, contrairement à `source` bash)
2. Variables d'environnement (override .env.production si présentes)

Idempotent. À lancer une fois que `setup_ekoalu` a déjà créé les 8 Campaigns.

Usage :
    python manage.py configure_ekoalu --dry-run
    python manage.py configure_ekoalu
"""
from __future__ import annotations

import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from dotenv import dotenv_values

from ekoalu import conf

# Racine du projet (parent du dossier openoutreach/)
PROJECT_ROOT = Path(__file__).resolve().parents[4]
ENV_FILE = PROJECT_ROOT / ".env.production"


def _load_var(key: str) -> str:
    """Lit une variable depuis .env.production (priorité), sinon env shell."""
    if ENV_FILE.exists():
        dotenv = dotenv_values(ENV_FILE)
        if key in dotenv and dotenv[key]:
            return dotenv[key].strip()
    return os.environ.get(key, "").strip()


REQUIRED_ENV_VARS = [
    "EKOALU_LINKEDIN_EMAIL",
    "EKOALU_LINKEDIN_PASSWORD",
    "ANTHROPIC_API_KEY",
]


class Command(BaseCommand):
    help = (
        "Configure SiteConfig (LLM Anthropic) + LinkedInProfile EKOALU "
        "depuis les variables d environnement."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Affiche ce qui serait fait sans modifier la DB",
        )
        parser.add_argument(
            "--ai-model",
            type=str,
            default="claude-sonnet-4-6",
            help="Modèle Anthropic (defaut: claude-sonnet-4-6)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        ai_model = options["ai_model"]

        # Lire depuis .env.production (priorité) ou environnement
        linkedin_email = _load_var("EKOALU_LINKEDIN_EMAIL")
        linkedin_password = _load_var("EKOALU_LINKEDIN_PASSWORD")
        anthropic_key = _load_var("ANTHROPIC_API_KEY")

        missing = []
        if not linkedin_email:
            missing.append("EKOALU_LINKEDIN_EMAIL")
        if not linkedin_password:
            missing.append("EKOALU_LINKEDIN_PASSWORD")
        if not anthropic_key:
            missing.append("ANTHROPIC_API_KEY")

        if missing:
            source = (
                f".env.production ({ENV_FILE})" if ENV_FILE.exists()
                else "variables d environnement"
            )
            raise CommandError(
                f"Variables manquantes dans {source} :\n  - "
                + "\n  - ".join(missing)
                + "\n\nVerifiez le fichier .env.production a la racine du projet."
            )

        self.stdout.write(
            f"\n{'[DRY-RUN] ' if dry_run else ''}Configuration EKOALU\n"
            + "=" * 70
        )
        self.stdout.write(f"  LinkedIn email           : {linkedin_email}")
        self.stdout.write(f"  LinkedIn password        : {'*' * len(linkedin_password)}")
        self.stdout.write(f"  Anthropic API key        : sk-ant-...{anthropic_key[-6:]}")
        self.stdout.write(f"  AI model                 : {ai_model}")
        self.stdout.write(f"  Connect weekly limit     : {conf.WEEKLY_INVITE_TARGET}")
        self.stdout.write(f"  Connect daily limit      : {conf.DAILY_INVITE_CAP}")
        self.stdout.write(f"  Follow-up daily limit    : 20  (cible EKOALU)")
        self.stdout.write(f"  Booking link             : {conf.CALENDAR_BOOKING_URL}")

        if dry_run:
            self.stdout.write("\n" + self.style.WARNING(
                "DRY-RUN — aucune modification. Relancez sans --dry-run pour appliquer."
            ))
            return

        # SiteConfig (LLM)
        from linkedin.models import SiteConfig

        site_cfg = SiteConfig.load()
        site_cfg.llm_provider = SiteConfig.LLMProvider.ANTHROPIC
        site_cfg.llm_api_key = anthropic_key
        site_cfg.ai_model = ai_model
        site_cfg.save()
        self.stdout.write(self.style.SUCCESS("  -> SiteConfig (Anthropic) sauvegardé"))

        # LinkedInProfile + User
        from django.contrib.auth.models import User
        from linkedin.models import LinkedInProfile

        username = linkedin_email.split("@")[0].lower().replace(".", "_").replace("+", "_")
        user, created_user = User.objects.get_or_create(
            username=username,
            defaults={
                "email": linkedin_email,
                "is_staff": True,
                "is_active": True,
            },
        )
        if created_user:
            user.set_unusable_password()
            user.save()
            self.stdout.write(self.style.SUCCESS(f"  -> User Django '{username}' créé"))
        else:
            self.stdout.write(f"  -> User Django '{username}' existait déjà")

        # Associer le user à toutes les Campaigns EKOALU
        from linkedin.models import Campaign

        ekoalu_campaigns = Campaign.objects.filter(name__startswith="EKOALU - ")
        for c in ekoalu_campaigns:
            c.users.add(user)
        self.stdout.write(
            f"  -> User associé à {ekoalu_campaigns.count()} Campaigns EKOALU"
        )

        profile, created_profile = LinkedInProfile.objects.update_or_create(
            user=user,
            defaults={
                "linkedin_username": linkedin_email,
                "linkedin_password": linkedin_password,
                "subscribe_newsletter": False,  # GDPR France
                "connect_daily_limit": conf.DAILY_INVITE_CAP,
                "connect_weekly_limit": conf.WEEKLY_INVITE_TARGET,
                "follow_up_daily_limit": 20,
                "active": True,
                "legal_accepted": True,
            },
        )
        if created_profile:
            self.stdout.write(self.style.SUCCESS(
                f"  -> LinkedInProfile créé (id={profile.pk})"
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"  -> LinkedInProfile mis à jour (id={profile.pk})"
            ))

        self.stdout.write("\n" + "=" * 70)
        self.stdout.write(self.style.SUCCESS("Configuration EKOALU appliquée."))
        self.stdout.write(
            "\nProchaines étapes :"
            "\n  1. Vérifier profil LinkedIn (mention 'CEO EKOALU' + signature Booking)"
            "\n  2. Lancer le smoke test : python scripts/smoke_e2e.py --dry-run"
            "\n  3. Si OK : lancer le daemon : python manage.py rundaemon"
        )
