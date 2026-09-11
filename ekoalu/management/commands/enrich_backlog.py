"""Enrichissement du backlog URL-only via la CHAÎNE cookieless (02/09).

Remplace ``apify_enrich_backlog`` comme point d'entrée de la tâche planifiée
(scripts/apify_enrich.ps1) : Bright Data en lot d'abord (5 000/mois gratuits),
puis Apify (10/j free-tier), puis mini-fiche SERP pour les restants. Aucun de
ces chemins ne touche le compte LinkedIn ni le cap lectures.

Le plafond par passe est monté de 40 à 200 le 11/09 (décision Richard) : les 40
étaient hérités du free-tier Apify (10/jour), sans rapport avec Bright Data qui
en autorise 4 500 par mois. Le délai d'attente du snapshot suit la taille du lot
(`client.poll_timeout_for`), sinon un lot de 200 expirait au bout des 300 s
d'origine et se remboursait en entier pour zéro profil.

Usage :
    python manage.py enrich_backlog              # jusqu'à 200 leads
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
        parser.add_argument("--max", type=int, default=200,
                            help="Nombre maximum de leads à traiter (défaut 200).")
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
            remaining = [ld for ld in remaining if _still_to_enrich(ld)]

        # 2. Apify pour les restants (son service borne au quota du jour).
        if remaining and apify_service.apify_ready():
            for lead in list(remaining):
                if apify_service.enrich_lead(lead):
                    totals["apify"] += 1
                    remaining.remove(lead)
                if not apify_service.apify_ready():
                    break  # quota du jour épuisé/saturé : inutile d'insister

        # 3. Mini-fiche SERP pour ce qui reste (gratuit, partiel). Apify aussi
        # disqualifie sur « profil introuvable » : on re-filtre avant.
        remaining = [ld for ld in remaining if _still_to_enrich(ld)]
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


def _still_to_enrich(lead) -> bool:
    """Le lead doit-il être présenté au fournisseur suivant ?

    Non s'il a maintenant une fiche, et non s'il vient d'être DISQUALIFIÉ :
    Bright Data répondant « page morte » écarte le prospect mais le laisse sans
    fiche, donc il repassait à Apify puis à la mini-fiche SERP dans la même
    passe — du quota dépensé pour un profil qui n'existe plus (constat 11/09).
    """
    lead.refresh_from_db(fields=["embedding", "profile_snapshot", "disqualified"])
    return lead.embedding is None and not lead.disqualified
