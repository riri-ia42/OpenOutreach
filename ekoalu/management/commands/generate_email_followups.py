"""Génère les relances mail (kind=email_follow_up) en file de validation.

Fiche hub #250 (09/09) + consigne Richard. Un cold mail SENT depuis >= 5 jours
ouvrés, sans réponse ni rebond ni refus, sans relance déjà faite → une relance,
plafonnée à 40 % du quota du jour. Lancée le matin par email_pipeline.ps1
juste après generate_cold_emails ; l'envoi passe par send_approved_emails
(réponse dans le fil Graph du cold mail d'origine).

    python manage.py generate_email_followups [--limit N] [--dry-run]
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Génère les relances mail J+5 ouvrés (file de validation Richard)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0,
                            help="Max à générer (0 = 40 %% du quota du jour, moins le déjà généré).")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        from ekoalu.email_canal.followup import (
            FOLLOWUP_VARIANT, eligible_followups, followup_enabled,
            followup_quota_for, followups_generated_on,
        )
        from ekoalu.email_generator.followup_generator import generate_email_followup
        from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

        if not followup_enabled():
            self.stdout.write(self.style.WARNING("Relances mail désactivées (EKOALU_EMAIL_FOLLOWUP=0)."))
            return
        today = timezone.localtime().date()
        limit = int(opts["limit"]) or max(0, followup_quota_for(today) - followups_generated_on(today))
        eligible = eligible_followups(today)
        self.stdout.write(self.style.NOTICE(
            f"Relances éligibles : {len(eligible)} | plafond du jour : {limit} | dry_run={opts['dry_run']}",
        ))
        if limit <= 0 or not eligible:
            self.stdout.write(self.style.SUCCESS("Rien à générer."))
            return

        created = skipped = 0
        for cold, lead in eligible[:limit]:
            data = getattr(lead, "email_data", None)
            label = (data.entreprise if data and data.entreprise else lead.contact_email)
            self.stdout.write(f"\n→ {label} (cold mail #{cold.pk} du {timezone.localtime(cold.sent_at):%d/%m})")
            draft = generate_email_followup(
                entreprise=getattr(data, "entreprise", ""), dirigeant=getattr(data, "dirigeant", ""),
                code_naf=getattr(data, "code_naf", ""), activite=getattr(data, "activite", ""),
                ville=getattr(data, "ville", ""), original_subject=cold.subject,
                original_body=cold.content_to_send, contact_email=lead.contact_email or "",
            )
            if not draft.is_valid():
                self.stdout.write(self.style.ERROR("  Génération vide, skip."))
                skipped += 1
                continue
            preview = draft.body[:160].replace("\n", " / ")
            self.stdout.write(f"  {preview}...")
            if opts["dry_run"]:
                continue
            PendingOutbound.objects.create(
                prospect_public_id=lead.public_identifier, prospect_urn="",
                prospect_company=getattr(data, "entreprise", "") or "",
                campaign_id=cold.campaign_id, campaign_name=cold.campaign_name or "Relance mail",
                kind=OutboundKind.EMAIL_FOLLOW_UP, subject=f"Re: {cold.subject}",
                prompt_variant=FOLLOWUP_VARIANT, ai_draft=draft.body,
                status=OutboundStatus.PENDING, parent=cold,
            )
            created += 1
        self.stdout.write(self.style.SUCCESS(
            f"\nRelances générées : {created} (skippées : {skipped}) — en attente de validation.",
        ))
        logger.info("generate_email_followups: created=%d skipped=%d", created, skipped)
