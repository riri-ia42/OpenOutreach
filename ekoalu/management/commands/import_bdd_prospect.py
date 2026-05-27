"""Importe des contacts depuis BDD PROSPECT EKOALU en Lead mail-only.

Source : `enrichis-sirene.json` du projet `BDD PROSPECT` (25 105 contacts enrichis SIREN,
champ `code_naf` certain). Cf. `ekoalu/bdd_prospect_import.py` pour les filtres.

Les Lead créés sont mail-only :
- `linkedin_url` = `https://bdd-prospect.local/siren/<siren>` (synthétique)
- `public_identifier` = `bdd-prospect-<siren>` (synthétique, identifiable au préfixe)
- `contact_email` = email vérifié
- `contact_email_source` = `"bdd_prospect"`

Idempotence : si un Lead existe déjà avec ce `contact_email` OU ce `public_identifier`,
le row est skip (pas de mise à jour V1).

Usage :
    python manage.py import_bdd_prospect --source "../BDD PROSPECT/enrichis-sirene.json" --dry-run
    python manage.py import_bdd_prospect --source "..." --priority P1 --min-effectif 10
    python manage.py import_bdd_prospect --source "..." --include-p2 --limit 100
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ekoalu.bdd_prospect_import import (
    CONTACT_EMAIL_SOURCE,
    NAF_EXCLUS,
    NAF_P1,
    NAF_P2,
    NAF_P3,
    EligibilityFilters,
    iter_eligible,
    make_synthetic_linkedin_url,
    make_synthetic_public_identifier,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Importe les contacts BDD PROSPECT en Lead mail-only (canal email)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source", required=True,
            help="Chemin vers enrichis-sirene.json (ou JSON même format).",
        )
        parser.add_argument(
            "--priority", choices=["P1", "P1P2", "all"], default="P1",
            help="Périmètre NAF : P1 (défaut), P1P2 (= P1+P2), all (P1+P2+P3).",
        )
        parser.add_argument(
            "--include-p2", action="store_true",
            help="Raccourci : équivalent --priority P1P2.",
        )
        parser.add_argument(
            "--min-effectif", type=int, default=10,
            help="Effectif min (CLAUDE.md=10). Mettre 0 pour désactiver.",
        )
        parser.add_argument(
            "--allow-no-dirigeant", action="store_true",
            help="Importer même sans dirigeant identifié.",
        )
        parser.add_argument(
            "--allow-generic-email", action="store_true",
            help="Importer même si l'email est générique (contact@, info@...).",
        )
        parser.add_argument(
            "--allow-b2c-domain", action="store_true",
            help="Importer même si l'email est sur un domaine B2C (gmail, wanadoo...).",
        )
        parser.add_argument(
            "--limit", type=int, default=0,
            help="Limite d'inserts (0 = pas de limite). Appliqué après filtrage.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="N'insère rien, affiche juste les stats.",
        )

    def handle(self, *args, **opts):
        source = Path(opts["source"])
        if not source.exists():
            raise CommandError(f"Fichier source introuvable : {source}")

        # Périmètre NAF
        if opts["include_p2"] or opts["priority"] == "P1P2":
            naf_allowed = NAF_P1 | NAF_P2
            label = "P1+P2"
        elif opts["priority"] == "all":
            naf_allowed = NAF_P1 | NAF_P2 | NAF_P3
            label = "P1+P2+P3"
        else:
            naf_allowed = NAF_P1
            label = "P1"

        filters = EligibilityFilters(
            naf_allowed=naf_allowed,
            naf_excluded=NAF_EXCLUS,
            min_effectif=opts["min_effectif"],
            require_dirigeant=not opts["allow_no_dirigeant"],
            require_nominative_email=not opts["allow_generic_email"],
            exclude_b2c_domains=not opts["allow_b2c_domain"],
        )

        self.stdout.write(self.style.NOTICE(
            f"Source : {source.name} | NAF={label} ({sorted(naf_allowed)}) | "
            f"min_eff={filters.min_effectif} | "
            f"req_dirigeant={filters.require_dirigeant} | "
            f"req_nominative={filters.require_nominative_email} | "
            f"excl_b2c={filters.exclude_b2c_domains} | "
            f"limit={opts['limit'] or 'aucune'} | dry_run={opts['dry_run']}",
        ))

        with source.open("r", encoding="utf-8") as f:
            raw_rows = json.load(f)
        if not isinstance(raw_rows, list):
            raise CommandError(f"Format inattendu : la racine doit être une liste (trouvé {type(raw_rows).__name__})")

        total_rows = len(raw_rows)
        self.stdout.write(f"Rows chargés : {total_rows}")

        # Évalue l'éligibilité en streaming
        reject_counts: Counter = Counter()
        eligibles: list = []
        for contact, reason in iter_eligible(raw_rows, filters):
            if reason is None:
                eligibles.append(contact)
            else:
                reject_counts[reason] += 1

        self.stdout.write(self.style.WARNING(
            f"\n--- Stats filtrage ---\n"
            f"  parsés       : {total_rows}\n"
            f"  éligibles    : {len(eligibles)}\n"
            f"  rejetés      : {sum(reject_counts.values())}",
        ))
        for reason, n in reject_counts.most_common():
            self.stdout.write(f"    - {reason:35} : {n}")

        if opts["limit"] > 0:
            eligibles = eligibles[: opts["limit"]]
            self.stdout.write(self.style.NOTICE(f"Cappé à limit={opts['limit']} → {len(eligibles)} candidats"))

        if not eligibles:
            self.stdout.write(self.style.SUCCESS("Aucun candidat à importer."))
            return

        if opts["dry_run"]:
            # Compte les doublons potentiels sans toucher la DB
            from crm.models import Lead
            emails_to_check = [c.email for c in eligibles]
            public_ids_to_check = [make_synthetic_public_identifier(c.siren) for c in eligibles]
            existing_emails = set(Lead.objects.filter(contact_email__in=emails_to_check)
                                  .values_list("contact_email", flat=True))
            existing_pids = set(Lead.objects.filter(public_identifier__in=public_ids_to_check)
                                .values_list("public_identifier", flat=True))
            dup_email = sum(1 for c in eligibles if c.email in existing_emails)
            dup_pid = sum(1 for c in eligibles if make_synthetic_public_identifier(c.siren) in existing_pids)
            insertables = len(eligibles) - max(dup_email, dup_pid)
            self.stdout.write(self.style.SUCCESS(
                f"\n--- Dry-run ---\n"
                f"  candidats      : {len(eligibles)}\n"
                f"  déjà en DB (email)         : {dup_email}\n"
                f"  déjà en DB (public_id)     : {dup_pid}\n"
                f"  insertables (approx)       : {insertables}",
            ))
            return

        # Insertion réelle
        from crm.models import Lead
        from ekoalu.email_canal.models import EmailLeadData
        created = 0
        skipped_dup = 0
        errors = 0
        with transaction.atomic():
            for c in eligibles:
                public_id = make_synthetic_public_identifier(c.siren)
                url = make_synthetic_linkedin_url(c.siren)
                # Idempotence : skip si email ou public_id déjà présent
                if Lead.objects.filter(contact_email=c.email).exists():
                    skipped_dup += 1
                    continue
                if Lead.objects.filter(public_identifier=public_id).exists():
                    skipped_dup += 1
                    continue
                try:
                    lead = Lead.objects.create(
                        linkedin_url=url,
                        public_identifier=public_id,
                        contact_email=c.email,
                        contact_email_source=CONTACT_EMAIL_SOURCE,
                    )
                    EmailLeadData.objects.create(
                        lead=lead,
                        source=EmailLeadData.SOURCE_BDD_PROSPECT,
                        siren=c.siren,
                        entreprise=c.entreprise,
                        dirigeant=c.dirigeant,
                        code_naf=c.code_naf,
                        activite=c.activite,
                        cp=c.cp,
                        dpt=c.dpt,
                        ville=c.ville,
                        effectif_min=c.effectif_min,
                        effectif_max=c.effectif_max,
                        raw_json=c.raw,
                    )
                    created += 1
                except Exception as exc:  # noqa: BLE001 — on log + compte, on continue
                    logger.error("import_bdd_prospect: échec création %s (%s) : %s",
                                 c.email, c.siren, exc)
                    errors += 1

        self.stdout.write(self.style.SUCCESS(
            f"\n--- Import terminé ---\n"
            f"  créés          : {created}\n"
            f"  skippés (dup)  : {skipped_dup}\n"
            f"  erreurs        : {errors}",
        ))
        logger.info("import_bdd_prospect: created=%d skipped=%d errors=%d",
                    created, skipped_dup, errors)
