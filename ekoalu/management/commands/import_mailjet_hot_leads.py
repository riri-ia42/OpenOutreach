"""Importe les leads chauds Mailjet (ouvreurs/cliqueurs) déposés par mailing-mailjet.

Source : `data/mailjet_hot_leads.json`, alimenté par la commande `sync-prospection`
du projet mailing-mailjet après chaque campagne. Format d'une entrée :
    {"email", "source": "mailjet", "campaign_id", "ref", "event_type": "open|click",
     "event_at", "entreprise", "dirigeant", "siren", "code_naf", "activite",
     "cp", "dpt", "ville", "effectif_min", "effectif_max"}

Contrairement à `import_bdd_prospect`, AUCUN filtre NAF/effectif/email nominatif :
le signal d'engagement (ouverture/clic d'une campagne EKOALU) prime sur l'ICP.
La génération puis l'envoi des mails personnalisés restent validés par Richard
(PendingOutbound) — cet import ne déclenche aucun envoi.

Usage :
    python manage.py import_mailjet_hot_leads --dry-run
    python manage.py import_mailjet_hot_leads
    python manage.py import_mailjet_hot_leads --source "path/custom.json" --event click
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ekoalu.bdd_prospect_import import (
    make_synthetic_linkedin_url,
    make_synthetic_public_identifier,
)

logger = logging.getLogger(__name__)

DEFAULT_SOURCE = Path(settings.BASE_DIR).parent / "data" / "mailjet_hot_leads.json"


def _to_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _synthetic_identity(row: dict) -> tuple[str, str]:
    """(linkedin_url, public_identifier) synthétiques.

    Avec siren : mêmes conventions que import_bdd_prospect (dédoublonne entre canaux).
    Sans siren : identité dérivée de l'email.
    """
    siren = (row.get("siren") or "").strip()
    if siren:
        return make_synthetic_linkedin_url(siren), make_synthetic_public_identifier(siren)
    # lowercase : sinon deux casses du même email produisent 2 identités (LOT D)
    slug = (row.get("email") or "").strip().lower().replace("@", "-at-").replace(".", "-")
    return f"https://mailjet-hot.local/{slug}", f"mailjet-hot-{slug}"


class Command(BaseCommand):
    help = "Importe les ouvreurs/cliqueurs Mailjet en Lead mail-only (canal email)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source", default=str(DEFAULT_SOURCE),
            help=f"Chemin du dépôt JSON (défaut : {DEFAULT_SOURCE}).",
        )
        parser.add_argument(
            "--event", choices=["open", "click", "all"], default="all",
            help="Ne retenir que les ouvreurs, les cliqueurs, ou tous (défaut).",
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
            raise CommandError(f"Dépôt introuvable : {source} "
                               "(lancer sync-prospection côté mailing-mailjet ?)")
        rows = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise CommandError("Format inattendu : la racine doit être une liste.")

        if opts["event"] != "all":
            rows = [r for r in rows if r.get("event_type") == opts["event"]]
        rows = [r for r in rows if (r.get("email") or "").strip()]
        if opts["limit"] > 0:
            rows = rows[: opts["limit"]]

        self.stdout.write(self.style.NOTICE(
            f"Source : {source} | event={opts['event']} | candidats={len(rows)} | "
            f"dry_run={opts['dry_run']}",
        ))
        if not rows:
            self.stdout.write(self.style.SUCCESS("Aucun candidat à importer."))
            return

        from django.db.models.functions import Lower

        from crm.models import Lead
        from ekoalu.email_canal.models import EmailLeadData
        from ekoalu.shared_exclusions import excluded_emails

        # Dédup insensible à la casse (LOT D) : emails candidats ET emails DB
        # normalisés lowercase des DEUX côtés (avant : set en casse brute vs
        # comparaison lowercase = doublon possible).
        emails = [(r.get("email") or "").strip().lower() for r in rows]
        existing = set(
            Lead.objects.annotate(email_lc=Lower("contact_email"))
            .filter(email_lc__in=emails)
            .values_list("email_lc", flat=True)
        )
        shared_excluded = excluded_emails()
        n_excluded = sum(1 for e in emails if e in shared_excluded)
        if opts["dry_run"]:
            self.stdout.write(self.style.SUCCESS(
                f"\n--- Dry-run ---\n"
                f"  candidats   : {len(rows)}\n"
                f"  déjà en DB  : {sum(1 for e in emails if e in existing)}\n"
                f"  exclus (partagé) : {n_excluded}\n"
                f"  insertables : {sum(1 for e in emails if e not in existing and e not in shared_excluded)}",
            ))
            return

        created = 0
        skipped_dup = 0
        skipped_excluded = 0
        errors = 0
        with transaction.atomic():
            for row in rows:
                email = row["email"].strip().lower()
                url, public_id = _synthetic_identity(row)
                if email in shared_excluded:
                    # Bounce/unsubscribe connu du canal Mailjet — on n'importe
                    # pas un contact qu'on n'a pas le droit de recontacter.
                    skipped_excluded += 1
                    continue
                if (email in existing
                        or Lead.objects.filter(public_identifier=public_id).exists()):
                    skipped_dup += 1
                    continue
                try:
                    lead = Lead.objects.create(
                        linkedin_url=url,
                        public_identifier=public_id,
                        contact_email=email,
                        contact_email_source=EmailLeadData.SOURCE_MAILJET_HOT,
                    )
                    EmailLeadData.objects.create(
                        lead=lead,
                        source=EmailLeadData.SOURCE_MAILJET_HOT,
                        siren=row.get("siren", ""),
                        entreprise=row.get("entreprise", ""),
                        dirigeant=row.get("dirigeant", ""),
                        code_naf=row.get("code_naf", ""),
                        activite=row.get("activite", ""),
                        cp=row.get("cp", ""),
                        dpt=row.get("dpt", ""),
                        ville=row.get("ville", ""),
                        effectif_min=_to_int(row.get("effectif_min")),
                        effectif_max=_to_int(row.get("effectif_max")),
                        raw_json=row,
                    )
                    existing.add(email)
                    created += 1
                except Exception as exc:  # noqa: BLE001 — on log + compte, on continue
                    logger.error("import_mailjet_hot_leads: échec création %s : %s",
                                 email, exc)
                    errors += 1

        self.stdout.write(self.style.SUCCESS(
            f"\n--- Import terminé ---\n"
            f"  créés            : {created}\n"
            f"  skippés (dup)    : {skipped_dup}\n"
            f"  skippés (exclus) : {skipped_excluded}\n"
            f"  erreurs          : {errors}",
        ))
        logger.info(
            "import_mailjet_hot_leads: created=%d skipped=%d excluded=%d errors=%d",
            created, skipped_dup, skipped_excluded, errors,
        )
