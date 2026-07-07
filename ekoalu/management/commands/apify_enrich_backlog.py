"""Enrichissement du backlog de leads URL-only via Apify (cookieless).

Wrapper de ``ekoalu/apify_enrich/service.py:enrich_urlonly_leads`` — c'est la
commande qu'appelle la tache planifiee (scripts/apify_enrich.ps1). Les fetches
Apify ne touchent PAS le compte LinkedIn et ne consomment PAS le cap lectures.

Usage :
    python manage.py apify_enrich_backlog                # jusqu'a 40 leads
    python manage.py apify_enrich_backlog --max 10
    python manage.py apify_enrich_backlog --dry-run      # liste, zero appel API

Garde-fous : plafond quotidien EKOALU_APIFY_DAILY_CAP (defaut 40),
kill-switch EKOALU_APIFY_ENRICH=0. Cf. docs/APIFY_ENRICH.md.
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Enrichit les leads URL-only (snapshot + embedding) via Apify — cookieless, plafond quotidien."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max", type=int, default=40,
            help="Nombre maximum de leads a enrichir sur ce run (defaut 40).",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Liste les leads candidats sans appel API ni ecriture DB.",
        )

    def handle(self, *args, **opts):
        from ekoalu.apify_enrich import client, service

        if opts["dry_run"]:
            self._dry_run(service, client, opts["max"])
            return

        stats = service.enrich_urlonly_leads(opts["max"])
        if not stats["enabled"]:
            self.stdout.write(self.style.WARNING(
                "Kill-switch actif (EKOALU_APIFY_ENRICH=0) — rien fait.",
            ))
            return
        self.stdout.write(
            f"Traites : {stats['selected']} — reussis : {stats['enriched']} — "
            f"echecs : {stats['failed']}",
        )
        self.stdout.write(
            f"Cout estime : ~{stats['cost_estimated_usd']:.3f} $ — "
            f"compteur du jour : {stats['used_today']}/{stats['cap']}",
        )
        if stats["selected"] == 0 and service.remaining_today() == 0:
            self.stdout.write(self.style.WARNING(
                "Plafond quotidien Apify atteint — reprise demain (reset a minuit).",
            ))
        elif stats["failed"] and not stats["enriched"]:
            self.stdout.write(self.style.WARNING(
                "Aucun lead enrichi (echec run ?) — les leads restent intacts, "
                "le repli Voyager du daemon les rattrapera.",
            ))
        else:
            self.stdout.write(self.style.SUCCESS("[OK]"))

    def _dry_run(self, service, client, max_leads: int) -> None:
        """Liste les candidats — AUCUN appel API, AUCUNE ecriture DB."""
        leads = service.candidate_leads(max_leads)
        for lead in leads:
            self.stdout.write(f"  [dry] {lead.linkedin_url} (cree {lead.creation_date:%Y-%m-%d})")
        cost = len(leads) * client.ESTIMATED_COST_PER_PROFILE_USD
        self.stdout.write(
            f"[dry-run] {len(leads)} leads candidats (--max {max_leads}), "
            f"cout estime ~{cost:.3f} $ — budget restant aujourd'hui : "
            f"{service.remaining_today()}/{service.daily_cap()}"
            + ("" if service.is_enabled() else " — KILL-SWITCH ACTIF"),
        )
        self.stdout.write(self.style.SUCCESS(
            "[dry-run] Aucun appel API, aucune ecriture DB.",
        ))
