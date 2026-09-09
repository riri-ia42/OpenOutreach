"""Passe unique « déjà en relation » sur le vivier cold mail (fiche hub #251).

    python manage.py check_outlook_relations [--limit 300] [--dry-run]

À une recherche par seconde, 2 400 leads = ~40 min : lancer par tranches
(--limit) ou une fois en fin de journée. Les leads écartés sortent de la
file avec le motif automatique ; les autres sont laissés tels quels (le
contrôle du matin les revérifie au moment de la génération).
"""
from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Contrôle Outlook « déjà en relation » sur le vivier cold mail."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=300)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        from ekoalu.email_canal.pool import cold_mail_candidates
        from ekoalu.email_canal.relation_check import (
            _our_subjects_for, check_relation, mark_relation, relation_check_enabled,
        )

        if not relation_check_enabled():
            self.stdout.write(self.style.WARNING("Contrôle désactivé (EKOALU_RELATION_CHECK=0)."))
            return
        candidates, _ = cold_mail_candidates()
        todo = [c for c in candidates if not getattr(c.email_data, "relation_existante", None)][: opts["limit"]]
        self.stdout.write(self.style.NOTICE(f"À vérifier : {len(todo)} lead(s) (vivier {len(candidates)})"))
        hits = checked = 0
        for lead in todo:
            res = check_relation(lead.contact_email, _our_subjects_for(lead.public_identifier))
            if res is None:
                self.stdout.write(self.style.ERROR("Gateway indisponible, arrêt."))
                break
            checked += 1
            if res:
                hits += 1
                self.stdout.write(f"  écarté : {lead.contact_email} ({res.reason()})")
                if not opts["dry_run"]:
                    mark_relation(lead, res)
        self.stdout.write(self.style.SUCCESS(f"Vérifiés : {checked}, écartés : {hits}, dry_run={opts['dry_run']}"))
