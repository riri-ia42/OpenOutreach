"""Génère des cold mails EKOALU pour les Lead du canal email (BDD PROSPECT).

Pour chaque Lead éligible :
- a un `contact_email`
- a un `EmailLeadData` (source bdd_prospect ou autre, peu importe)
- pas d'`unsubscribed_at`
- n'a PAS déjà un PendingOutbound(kind=email_cold) en statut ouvert
  (pending/approved/sent/blocked_company) — idempotence stricte

Génère un cold mail via Claude (Sonnet 4.6 par défaut) et le persiste en
`PendingOutbound(kind=email_cold, subject=..., ai_draft=body, status=pending)`
pour validation Richard avant envoi.

Usage :
    python manage.py generate_cold_emails --dry-run             # affiche, n'écrit rien
    python manage.py generate_cold_emails --limit 5             # max 5 cold mails
    python manage.py generate_cold_emails --limit 5 --dpt 69    # filtre département

Coût : ~0.005 $ par mail (Sonnet 4.6, prompt ~1200 tok + sortie ~400 tok).
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from ekoalu.email_generator import generate_cold_email, has_niche_mention
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

logger = logging.getLogger(__name__)

# Statuts qui bloquent une nouvelle génération (cold mail "en cours" ou déjà envoyé)
_BLOCKING_STATUSES = (
    OutboundStatus.PENDING,
    OutboundStatus.APPROVED,
    OutboundStatus.SENT,
    OutboundStatus.BLOCKED_COMPANY,
)


class Command(BaseCommand):
    help = "Génère des cold mails (kind=email_cold) pour les Lead canal email sans mail en cours."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=5,
            help="Nombre max de cold mails à générer dans cette passe (défaut 5).",
        )
        parser.add_argument(
            "--dpt", default="",
            help="Filtre département (ex : 69). Vide = tous départements.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Génère ET affiche le mail mais NE crée PAS de PendingOutbound.",
        )
        parser.add_argument(
            "--source", default="",
            help="Filtre source EmailLeadData (bdd_prospect/manual/...). Vide = toutes.",
        )

    def handle(self, *args, **opts):
        from crm.models import Lead
        from ekoalu.email_canal.models import EmailLeadData

        limit = int(opts["limit"])
        dpt = opts["dpt"].strip()
        source = opts["source"].strip()
        dry_run = bool(opts["dry_run"])

        # 1. Lead avec contact_email + EmailLeadData + pas désinscrit
        leads_qs = (
            Lead.objects
            .filter(contact_email__isnull=False, unsubscribed_at__isnull=True)
            .exclude(contact_email="")
            .filter(email_data__isnull=False)
        )
        if dpt:
            leads_qs = leads_qs.filter(email_data__dpt=dpt)
        if source:
            leads_qs = leads_qs.filter(email_data__source=source)

        # 2. Exclure ceux qui ont déjà un PendingOutbound email_cold "en cours"
        blocked_public_ids = set(
            PendingOutbound.objects
            .filter(kind=OutboundKind.EMAIL_COLD, status__in=_BLOCKING_STATUSES)
            .values_list("prospect_public_id", flat=True)
        )
        candidates = [
            lead for lead in leads_qs.select_related("email_data")
            if lead.public_identifier not in blocked_public_ids
        ]

        self.stdout.write(self.style.NOTICE(
            f"Candidats avant cap : {len(candidates)} | dpt={dpt or 'tous'} | "
            f"source={source or 'toutes'} | limit={limit} | dry_run={dry_run}",
        ))

        if not candidates:
            self.stdout.write(self.style.SUCCESS("Aucun candidat à générer."))
            return

        capped = candidates[:limit]
        self.stdout.write(f"Génération : {len(capped)} cold mails")

        created = 0
        skipped_empty = 0
        skipped_no_niche = 0

        for lead in capped:
            data: EmailLeadData = lead.email_data
            self.stdout.write(f"\n→ {data.entreprise or lead.contact_email} "
                              f"({data.code_naf}, {data.ville})")

            draft = generate_cold_email(
                entreprise=data.entreprise,
                dirigeant=data.dirigeant,
                code_naf=data.code_naf,
                activite=data.activite,
                ville=data.ville,
                dpt=data.dpt,
                effectif_min=data.effectif_min,
                effectif_max=data.effectif_max,
            )

            if not draft.is_valid():
                self.stdout.write(self.style.ERROR("  ⚠ Génération vide, skip."))
                skipped_empty += 1
                continue

            if not has_niche_mention(draft.body):
                self.stdout.write(self.style.WARNING(
                    "  ⚠ Aucun produit niche mentionné dans le corps, skip "
                    "(violation règle marketing).",
                ))
                skipped_no_niche += 1
                continue

            # Aperçu
            self.stdout.write(f"  Objet : {draft.subject}")
            preview = draft.body[:200].replace("\n", " ⏎ ")
            self.stdout.write(f"  Corps : {preview}{'…' if len(draft.body) > 200 else ''}")

            if dry_run:
                continue

            PendingOutbound.objects.create(
                prospect_public_id=lead.public_identifier,
                prospect_company=data.entreprise[:255],
                kind=OutboundKind.EMAIL_COLD,
                subject=draft.subject,
                ai_draft=draft.body,
                status=OutboundStatus.PENDING,
                prompt_variant=draft.variant_used,
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(
            f"\n--- Bilan ---\n"
            f"  générés         : {created}\n"
            f"  skip (vide)     : {skipped_empty}\n"
            f"  skip (niche)    : {skipped_no_niche}\n"
            f"  dry_run         : {dry_run}",
        ))
        logger.info("generate_cold_emails: created=%d skipped_empty=%d skipped_no_niche=%d dry_run=%s",
                    created, skipped_empty, skipped_no_niche, dry_run)
