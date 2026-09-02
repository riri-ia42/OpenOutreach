"""Enrichissement du backlog URL-only via la CHAÎNE cookieless (02/09).

Remplace ``apify_enrich_backlog`` comme point d'entrée de la tâche planifiée
(scripts/apify_enrich.ps1) : Bright Data en lot d'abord (5 000/mois gratuits),
puis Apify (10/j free-tier), puis mini-fiche SERP pour les restants. Aucun de
ces chemins ne touche le compte LinkedIn ni le cap lectures.

Usage :
    python manage.py enrich_backlog              # jusqu'à 40 leads
    python manage.py enrich_backlog --max 60
    python manage.py enrich_backlog --dry-run
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = ("Enrichit les leads URL-only via la chaîne cookieless "
            "Bright Data → Apify → mini-fiche SERP.")

    def add_arguments(self, parser):
        parser.add_argument("--max", type=int, default=40,
                            help="Nombre maximum de leads à traiter (défaut 40).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Liste les candidats, zéro appel API.")

    def handle(self, *args, **opts):
        from ekoalu.apify_enrich import service as apify_service
        from ekoalu.brightdata_enrich import service as bd_service
        from ekoalu.google_sourcing.snippet_profile import enrich_lead_from_serp

        leads = apify_service.candidate_leads(opts["max"])
        if opts["dry_run"]:
            for lead in leads:
                self.stdout.write(f"  [dry] {lead.linkedin_url}")
            self.stdout.write(
                f"[dry-run] {len(leads)} candidats — Bright Data prêt : "
                f"{bd_service.brightdata_ready()} "
                f"({bd_service.used_this_month()}/{bd_service.monthly_cap()} ce mois) ; "
                f"Apify prêt : {apify_service.apify_ready()} "
                f"({apify_service.used_today()}/{apify_service.daily_cap()} aujourd'hui)",
            )
            return
        if not leads:
            self.stdout.write("Backlog vide — rien à enrichir. [OK]")
            return

        totals = {"brightdata": 0, "apify": 0, "serper_snippet": 0, "restants": 0}

        # 1. Bright Data en LOT (1 trigger pour tout le monde : efficace).
        remaining = list(leads)
        if bd_service.brightdata_ready():
            stats = bd_service.enrich_leads(remaining)
            totals["brightdata"] = stats["enriched"]
            remaining = [ld for ld in remaining if ld.embedding is None or _refresh_is_urlonly(ld)]

        # 2. Apify pour les restants (son service borne au quota du jour).
        if remaining and apify_service.apify_ready():
            for lead in list(remaining):
                if apify_service.enrich_lead(lead):
                    totals["apify"] += 1
                    remaining.remove(lead)
                if not apify_service.apify_ready():
                    break  # quota du jour épuisé/saturé : inutile d'insister

        # 3. Mini-fiche SERP pour ce qui reste (gratuit, partiel).
        for lead in list(remaining):
            if enrich_lead_from_serp(lead):
                totals["serper_snippet"] += 1
                remaining.remove(lead)
        totals["restants"] = len(remaining)

        enriched = totals["brightdata"] + totals["apify"] + totals["serper_snippet"]
        self.stdout.write(
            f"Traites : {len(leads)} — reussis : {enriched} "
            f"(brightdata {totals['brightdata']}, apify {totals['apify']}, "
            f"serp {totals['serper_snippet']}) — restants (repli Voyager daemon) : "
            f"{totals['restants']}",
        )
        self.stdout.write(self.style.SUCCESS("[OK]"))


def _refresh_is_urlonly(lead) -> bool:
    """Relit l'état embedding depuis la DB (enrich_leads a modifié en place)."""
    lead.refresh_from_db(fields=["embedding", "profile_snapshot"])
    return lead.embedding is None
