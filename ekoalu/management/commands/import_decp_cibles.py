"""Importe les cibles DECP (marchés publics attribués) en Lead mail-only.

Source : `seances/decp-cibles-prospection.json` de BDD PROSPECT, régénéré chaque
dimanche par la séance antichambre. Cf. `ekoalu/decp_import.py` (décisions Richard
2026-07-28 : tous les titulaires entrent, cibles prioritaires consommées d'abord).

Identifiants synthétiques IDENTIQUES à import_bdd_prospect (`bdd-prospect-<siren>`)
→ dédup inter-sources par SIREN garantie par l'unicité de public_identifier.

Usage :
    python manage.py import_decp_cibles --dry-run
    python manage.py import_decp_cibles                # source par défaut BDD PROSPECT
    python manage.py import_decp_cibles --source "chemin/custom.json" --limit 50
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ekoalu.bdd_prospect_import import (
    make_synthetic_linkedin_url,
    make_synthetic_public_identifier,
)
from ekoalu.decp_import import CONTACT_EMAIL_SOURCE_DECP, is_eligible, parse_cible

logger = logging.getLogger(__name__)

DEFAULT_SOURCE = (
    r"C:\Users\RI.GROS\Documents\CLAUDE\BDD PROSPECT\seances\decp-cibles-prospection.json"
)


class Command(BaseCommand):
    help = "Importe les titulaires de marchés publics (DECP) en Lead mail-only prioritaires."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source", default=DEFAULT_SOURCE,
            help="Chemin vers decp-cibles-prospection.json (défaut : BDD PROSPECT/seances/).",
        )
        parser.add_argument(
            "--limit", type=int, default=0,
            help="Limite d'inserts (0 = pas de limite).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="N'insère rien, affiche juste les stats.",
        )

    def handle(self, *args, **opts):
        source = Path(opts["source"])
        if not source.exists():
            raise CommandError(f"Fichier source introuvable : {source}")

        with source.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        raw_cibles = payload.get("cibles") if isinstance(payload, dict) else payload
        if not isinstance(raw_cibles, list):
            raise CommandError("Format inattendu : clé 'cibles' absente ou non-liste.")

        self.stdout.write(self.style.NOTICE(
            f"Source : {source.name} | cibles chargées : {len(raw_cibles)} | "
            f"généré le : {payload.get('genere_le', '?') if isinstance(payload, dict) else '?'} | "
            f"limit={opts['limit'] or 'aucune'} | dry_run={opts['dry_run']}",
        ))

        reject_counts: Counter = Counter()
        eligibles = []
        for raw in raw_cibles:
            cible = parse_cible(raw)
            if cible is None:
                reject_counts["no_email"] += 1
                continue
            reason = is_eligible(cible)
            if reason is None:
                eligibles.append(cible)
            else:
                reject_counts[reason] += 1

        # Le fichier est déjà trié cible_prioritaire d'abord ; on re-trie par
        # sécurité (l'ordre d'insertion = ordre FIFO du vivier pour les non-prioritaires).
        eligibles.sort(key=lambda c: (not c.cible_prioritaire,))

        nb_prio = sum(1 for c in eligibles if c.cible_prioritaire)
        self.stdout.write(self.style.WARNING(
            f"\n--- Stats filtrage ---\n"
            f"  éligibles    : {len(eligibles)} (dont {nb_prio} cibles prioritaires)\n"
            f"  rejetés      : {sum(reject_counts.values())}",
        ))
        for reason, n in reject_counts.most_common():
            self.stdout.write(f"    - {reason:20} : {n}")

        if opts["limit"] > 0:
            eligibles = eligibles[: opts["limit"]]

        if not eligibles:
            self.stdout.write(self.style.SUCCESS("Aucune cible à importer."))
            return

        from crm.models import Lead
        from ekoalu.email_canal.models import EmailLeadData

        if opts["dry_run"]:
            emails = [c.email for c in eligibles]
            pids = [make_synthetic_public_identifier(c.siren) for c in eligibles]
            dup_email = Lead.objects.filter(contact_email__in=emails).count()
            dup_pid = Lead.objects.filter(public_identifier__in=pids).count()
            self.stdout.write(self.style.SUCCESS(
                f"\n--- Dry-run ---\n"
                f"  candidats              : {len(eligibles)}\n"
                f"  déjà en DB (email)     : {dup_email}\n"
                f"  déjà en DB (siren/pid) : {dup_pid}\n"
                f"  insertables (approx)   : {len(eligibles) - max(dup_email, dup_pid)}",
            ))
            return

        created = 0
        created_prio = 0
        skipped_dup = 0
        errors = 0
        with transaction.atomic():
            for c in eligibles:
                public_id = make_synthetic_public_identifier(c.siren)
                if Lead.objects.filter(contact_email=c.email).exists():
                    skipped_dup += 1
                    continue
                if Lead.objects.filter(public_identifier=public_id).exists():
                    skipped_dup += 1
                    continue
                try:
                    lead = Lead.objects.create(
                        linkedin_url=make_synthetic_linkedin_url(c.siren),
                        public_identifier=public_id,
                        contact_email=c.email,
                        contact_email_source=CONTACT_EMAIL_SOURCE_DECP,
                    )
                    EmailLeadData.objects.create(
                        lead=lead,
                        source=EmailLeadData.SOURCE_DECP,
                        siren=c.siren,
                        entreprise=c.entreprise,
                        dirigeant=c.dirigeant,
                        code_naf=c.code_naf,
                        raw_json=c.raw,
                    )
                    created += 1
                    if c.cible_prioritaire:
                        created_prio += 1
                except Exception as exc:  # noqa: BLE001 — on log + compte, on continue
                    logger.error("import_decp_cibles: échec création %s (%s) : %s",
                                 c.email, c.siren, exc)
                    errors += 1

        self.stdout.write(self.style.SUCCESS(
            f"\n--- Import terminé ---\n"
            f"  créés              : {created} (dont {created_prio} cibles prioritaires)\n"
            f"  skippés (dup)      : {skipped_dup}\n"
            f"  erreurs            : {errors}",
        ))
        logger.info("import_decp_cibles: created=%d (prio=%d) skipped=%d errors=%d",
                    created, created_prio, skipped_dup, errors)
