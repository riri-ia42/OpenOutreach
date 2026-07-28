"""Génère des cold mails EKOALU pour les Lead du canal email (BDD PROSPECT).

Pour chaque Lead éligible :
- a un `contact_email`
- a un `EmailLeadData` (source bdd_prospect ou autre, peu importe)
- pas d'`unsubscribed_at`
- n'est PAS `disqualified` (refus Richard = exclusion permanente — sinon on
  régénère un cold mail chaque jour pour un prospect déjà refusé)
- n'a PAS déjà un PendingOutbound(kind=email_cold) en statut ouvert, refusé OU
  échoué (pending/approved/sent/blocked_company/rejected/failed/expired) —
  idempotence stricte

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

from ekoalu.email_canal.pool import cold_mail_candidates
from ekoalu.email_generator import generate_cold_email, has_niche_mention
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Génère des cold mails (kind=email_cold) pour les Lead canal email sans mail en cours."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=0,
            help="Nombre max de cold mails à générer. 0 (défaut) = quota du "
                 "jour (50 en semaine, 20 le samedi, 0 férié/dimanche), moins "
                 "ce qui a déjà été généré aujourd'hui.",
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

    def _quota_du_jour(self) -> int:
        """Reste à générer aujourd'hui = quota du jour - déjà généré.

        La soustraction rend la commande idempotente : le rattrapage matinal
        (durcissement 24/07) peut la relancer sans doubler la production.
        """
        from django.utils import timezone

        from ekoalu.email_canal.quota import cold_mail_quota_for, quota_reason
        from ekoalu.outbound_validation.models import OutboundKind, PendingOutbound

        today = timezone.localtime().date()
        quota = cold_mail_quota_for(today)
        if quota <= 0:
            self.stdout.write(self.style.SUCCESS(
                f"Quota du jour = 0 ({quota_reason(today)}) — aucune génération.",
            ))
            return 0

        start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        deja = PendingOutbound.objects.filter(
            kind=OutboundKind.EMAIL_COLD, created_at__gte=start,
        ).count()
        reste = max(0, quota - deja)
        self.stdout.write(self.style.NOTICE(
            f"Quota du jour : {quota} ({quota_reason(today)}) — déjà généré {deja} "
            f"→ à générer {reste}",
        ))
        if reste <= 0:
            self.stdout.write(self.style.SUCCESS("Quota du jour déjà atteint."))
        return reste

    def handle(self, *args, **opts):
        from ekoalu.email_canal.models import EmailLeadData

        limit = int(opts["limit"])
        dpt = opts["dpt"].strip()
        source = opts["source"].strip()
        dry_run = bool(opts["dry_run"])

        if limit <= 0:
            limit = self._quota_du_jour()
            if limit <= 0:
                return

        # Vivier = source unique partagée avec daily_conformity (email_canal.pool) :
        # lead avec contact_email + EmailLeadData, ni désinscrit, ni bouncé, ni
        # disqualifié, sans cold mail déjà en cours/envoyé/refusé, hors liste
        # d'exclusion partagée mailing-mailjet.
        candidates, skipped_excluded = cold_mail_candidates(dpt=dpt, source=source)
        if skipped_excluded:
            self.stdout.write(self.style.WARNING(
                f"Skip {skipped_excluded} lead(s) : liste d'exclusion partagée "
                "(_partage/exclusions.json)",
            ))

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

            # Lead DECP (entreprise ou personne du groupe d'influence) :
            # le marché public gagné sert d'accroche factuelle
            contexte = ""
            if data.source in (EmailLeadData.SOURCE_DECP, EmailLeadData.SOURCE_DECP_INFLUENCE):
                from ekoalu.decp_import import build_marche_contexte
                contexte = build_marche_contexte(data.raw_json)
                if contexte:
                    self.stdout.write("  (contexte DECP : marché gagné injecté)")

            draft = generate_cold_email(
                entreprise=data.entreprise,
                dirigeant=data.dirigeant,
                code_naf=data.code_naf,
                activite=data.activite,
                ville=data.ville,
                dpt=data.dpt,
                effectif_min=data.effectif_min,
                effectif_max=data.effectif_max,
                contexte=contexte,
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
