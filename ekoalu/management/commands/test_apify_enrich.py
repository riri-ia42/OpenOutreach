"""Test a blanc de l'enrichissement Apify cookieless (LOT F — squelette).

AUCUNE ecriture en DB : affiche les snapshots mappes + cout estime, c'est le
test reel 10-20 profils qui decidera du cablage dans le pipeline.

Usage :
    python manage.py test_apify_enrich --urls url1,url2 --dry-run
    python manage.py test_apify_enrich --from-serper 10 --dry-run
    python manage.py test_apify_enrich --from-serper 10        # appel API reel

Config : env EKOALU_APIFY_TOKEN (+ EKOALU_APIFY_ACTOR optionnel).
Cf. docs/APIFY_ENRICH.md (creation compte, criteres GO/NO-GO).
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)

# Les leads mail-only (BDD PROSPECT) ont une URL synthetique, pas un profil.
SYNTHETIC_URL_PREFIX = "https://bdd-prospect.local/"


class Command(BaseCommand):
    help = "Teste l'enrichissement de profils LinkedIn via Apify (cookieless, zero ecriture DB)."

    def add_arguments(self, parser):
        parser.add_argument("--urls", help="URLs de profils LinkedIn, separees par des virgules.")
        parser.add_argument(
            "--from-serper", type=int, metavar="N",
            help="Prend N leads URL-only (sans snapshot ni embedding) — lecture seule de la DB.",
        )
        parser.add_argument("--dry-run", action="store_true",
                            help="Affiche ce qui serait envoye a Apify, sans appeler l'API.")

    def handle(self, *args, **opts):
        from ekoalu.apify_enrich import client

        urls = self._collect_urls(opts)
        payload = client.build_input(urls)
        cost = len(urls) * client.ESTIMATED_COST_PER_PROFILE_USD
        self.stdout.write(
            f"Acteur : {client.actor_id()} — {len(urls)} profils, "
            f"cout estime ~{cost:.3f} $ (a confirmer au test reel).",
        )

        if opts["dry_run"]:
            self.stdout.write("[dry-run] Payload qui serait envoye (AUCUN cookie, URLs publiques) :")
            for url in payload["profileUrls"]:
                self.stdout.write(f"  [dry] {url}")
            self.stdout.write(self.style.SUCCESS("[dry-run] Aucun appel API, aucune ecriture DB."))
            return

        if not client.is_configured():
            raise CommandError(
                "EKOALU_APIFY_TOKEN manquant : creer le compte Apify + token "
                "(cf. docs/APIFY_ENRICH.md) ou utiliser --dry-run.",
            )
        items = client.run_profile_scraper(urls)
        self._report(items)

    def _collect_urls(self, opts) -> list[str]:
        """URLs cibles depuis --urls ou --from-serper (lecture seule DB)."""
        if bool(opts.get("urls")) == bool(opts.get("from_serper")):
            raise CommandError("Fournir --urls OU --from-serper N (exactement un des deux).")
        if opts.get("urls"):
            urls = [u.strip() for u in opts["urls"].split(",") if u.strip()]
        else:
            from crm.models import Lead
            urls = list(
                Lead.objects.filter(
                    profile_snapshot__isnull=True,
                    embedding__isnull=True,
                    disqualified=False,
                    linkedin_url__contains="linkedin.com/in/",
                )
                .exclude(linkedin_url__startswith=SYNTHETIC_URL_PREFIX)
                .order_by("-creation_date")
                .values_list("linkedin_url", flat=True)[: opts["from_serper"]],
            )
        if not urls:
            raise CommandError("Aucune URL cible (aucun lead URL-only sans snapshot ?).")
        return urls

    def _report(self, items: list[dict]) -> None:
        """Affiche chaque snapshot mappe (resume lisible) — zero ecriture DB."""
        from ekoalu.apify_enrich.mapper import map_actor_item, snapshot_completeness

        for item in items:
            snap = map_actor_item(item)
            filled, total, missing = snapshot_completeness(snap)
            pos = (snap["positions"] or [{}])[0]
            self.stdout.write(
                f"\n- {snap.get('full_name') or '(nom absent)'} "
                f"[{snap.get('public_identifier') or snap.get('url') or '?'}]\n"
                f"  headline : {snap.get('headline') or '-'}\n"
                f"  poste    : {pos.get('title') or '-'} @ {pos.get('company_name') or '-'}\n"
                f"  lieu     : {snap.get('location_name') or '-'}\n"
                f"  completude : {filled}/{total} champs cles"
                + (f" (manquants : {', '.join(missing)})" if missing else ""),
            )
        self.stdout.write(self.style.SUCCESS(
            f"\n{len(items)} items mappes. AUCUNE ecriture DB — "
            "comparer la completude au snapshot Voyager avant tout cablage.",
        ))
