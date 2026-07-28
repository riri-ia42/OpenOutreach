"""Classe les sociétés du vivier en fabricant / revendeur-poseur (lecture du site).

Le flag `fabricant` des cibles DECP est purement NAF-based (4 codes) : déclaratif,
jamais audité, faux dans les deux sens. Cette commande lit le site web et tranche
sur des preuves de production.

Coût (décision Richard 28/07) : Haiku 4.5 en Batch API (−50 %), escalade Sonnet
uniquement sur les cas incertains. ~0,50 $ pour 242 sociétés.

Usage :
    python manage.py detect_fabricants --dry-run            # scrape + coût estimé, 0 appel LLM
    python manage.py detect_fabricants --limit 20           # passe réelle sur 20
    python manage.py detect_fabricants --source decp        # filtre par source
    python manage.py detect_fabricants --priority-only      # cibles prioritaires DECP
    python manage.py detect_fabricants --no-escalation      # Haiku seul
    python manage.py detect_fabricants --recheck            # re-teste les déjà classées
"""
from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand

from ekoalu.fabricant_detect.classifier import (
    MODEL_CHEAP,
    MODEL_ESCALATION,
    ClassifyInput,
    classify_with_escalation,
)
from ekoalu.fabricant_detect.fetch import SiteText, domain_from_email, fetch_many
from ekoalu.fabricant_detect.models import FabricantVerdict

logger = logging.getLogger(__name__)

# Ordre de grandeur mesuré : ~3000 tokens en entrée, ~200 en sortie par société.
EST_INPUT_TOKENS = 3000
EST_OUTPUT_TOKENS = 200


class Command(BaseCommand):
    help = "Classe les sociétés du vivier en fabricant / revendeur via leur site web."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0,
                            help="Nombre max de sociétés (0 = toutes).")
        parser.add_argument("--source", default="",
                            help="Filtre EmailLeadData.source (decp, bdd_prospect...).")
        parser.add_argument("--priority-only", action="store_true",
                            help="Seulement les cibles prioritaires DECP.")
        parser.add_argument("--no-escalation", action="store_true",
                            help="Haiku seul, pas d'escalade Sonnet.")
        parser.add_argument("--recheck", action="store_true",
                            help="Re-teste les sociétés déjà classées.")
        parser.add_argument("--workers", type=int, default=8,
                            help="Sites sondés en parallèle (défaut 8).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Scrape et estime le coût, sans aucun appel LLM.")

    def handle(self, *args, **opts):
        from ekoalu.email_canal.models import EmailLeadData

        limit = int(opts["limit"])
        source = opts["source"].strip()
        dry_run = bool(opts["dry_run"])

        qs = EmailLeadData.objects.exclude(siren="").select_related("lead")
        if source:
            qs = qs.filter(source=source)
        if opts["priority_only"]:
            qs = qs.filter(source=EmailLeadData.SOURCE_DECP)

        if not opts["recheck"]:
            deja = set(FabricantVerdict.objects.values_list("siren", flat=True))
        else:
            deja = set()

        candidates = []
        skipped_b2c = 0
        skipped_done = 0
        for data in qs:
            if data.siren in deja:
                skipped_done += 1
                continue
            if opts["priority_only"] and not (data.raw_json or {}).get("cible_prioritaire"):
                continue
            email = (data.lead.contact_email or "").strip()
            domain = domain_from_email(email)
            if not domain:
                skipped_b2c += 1
                continue
            candidates.append((data, domain))

        if limit:
            candidates = candidates[:limit]

        self.stdout.write(self.style.NOTICE(
            f"À analyser : {len(candidates)} société(s) "
            f"| déjà classées : {skipped_done} | sans domaine (B2C) : {skipped_b2c}",
        ))
        if not candidates:
            self.stdout.write(self.style.SUCCESS("Rien à faire."))
            return

        # --- Phase 1 : scraping (aucun coût LLM, mais c'est le vrai goulot) ---
        workers = int(opts["workers"])
        self.stdout.write(f"Récupération des sites ({workers} en parallèle)…")
        started = time.monotonic()
        sites = fetch_many([dom for _, dom in candidates], workers=workers)
        self.stdout.write(
            f"  {len(candidates)} site(s) sondé(s) en {time.monotonic() - started:.0f}s",
        )

        items: list[ClassifyInput] = []
        fetch_failures: list[tuple] = []
        for data, domain in candidates:
            site = sites.get(domain) or SiteText(domain=domain, error="non sondé")
            if not site.usable:
                fetch_failures.append((data, domain, site))
                continue
            items.append(ClassifyInput(
                siren=data.siren,
                entreprise=data.entreprise,
                code_naf=data.code_naf,
                ville=data.ville,
                url=site.url,
                text=site.text,
            ))
            self._remember_pages(data, domain, site)

        self.stdout.write(self.style.NOTICE(
            f"Sites exploitables : {len(items)}/{len(candidates)} "
            f"({len(fetch_failures)} injoignables ou trop pauvres)",
        ))

        # Les échecs de scraping sont un résultat en soi : on les persiste en
        # `indetermine` pour ne pas les re-scraper à chaque passe.
        if not dry_run:
            for data, domain, site in fetch_failures:
                self._save(data, domain, site, {
                    "verdict": FabricantVerdict.INDETERMINE,
                    "confiance": "basse",
                    "justification": f"Site non exploitable : {site.error}",
                }, fetch_error=site.error)

        if not items:
            self.stdout.write(self.style.WARNING("Aucun site exploitable."))
            return

        est = self._estimate_cost(len(items))
        self.stdout.write(self.style.NOTICE(
            f"Coût estimé : {est:.2f} $ (Haiku batch) — plafond quotidien "
            f"{self._budget_cap():.2f} $",
        ))

        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                "\n--- DRY-RUN : aucun appel LLM ---",
            ))
            for item in items[:5]:
                preview = item.text[:150].replace("\n", " ")
                self.stdout.write(f"  {item.entreprise[:38]:38} {item.url}")
                self.stdout.write(f"      {preview}…")
            return

        # --- Phase 2 : classification ---
        client = self._client()
        if client is None:
            self.stdout.write(self.style.ERROR("Pas de client Anthropic — abandon."))
            return

        verdicts, escalated = classify_with_escalation(
            client, items, escalate=not opts["no_escalation"],
        )
        self.stdout.write(self.style.NOTICE(
            f"Verdicts : {len(verdicts)}/{len(items)} | escalades Sonnet : {escalated}",
        ))

        # --- Phase 3 : persistance ---
        by_siren = {d.siren: (d, dom) for d, dom in candidates}
        counts = {"fabricant": 0, "revendeur_poseur": 0, "indetermine": 0}
        for siren, verdict in verdicts.items():
            if siren not in by_siren:
                continue
            data, domain = by_siren[siren]
            site_url = next((i.url for i in items if i.siren == siren), "")
            self._save(data, domain, None, verdict, url=site_url)
            counts[verdict.get("verdict", "indetermine")] = (
                counts.get(verdict.get("verdict", "indetermine"), 0) + 1
            )

        self.stdout.write(self.style.SUCCESS(
            f"\n--- Bilan ---\n"
            f"  fabricants      : {counts['fabricant']}\n"
            f"  revendeurs      : {counts['revendeur_poseur']}\n"
            f"  indéterminés    : {counts['indetermine']}\n"
            f"  escalades       : {escalated} (modèle {MODEL_ESCALATION})",
        ))

    # ------------------------------------------------------------------ utils

    def _remember_pages(self, data, domain, site) -> None:
        """Mémorise les pages lues (debug), sans écrire le verdict."""
        self._pages_cache = getattr(self, "_pages_cache", {})
        self._pages_cache[data.siren] = (site.url, site.pages_fetched)

    def _save(self, data, domain, site, verdict: dict, *,
              url: str = "", fetch_error: str = "") -> None:
        cached_url, pages = getattr(self, "_pages_cache", {}).get(data.siren, ("", []))
        FabricantVerdict.objects.update_or_create(
            siren=data.siren,
            defaults={
                "entreprise": data.entreprise[:255],
                "code_naf": data.code_naf,
                "domain": domain,
                "url": (url or cached_url or (site.url if site else ""))[:500],
                "verdict": verdict.get("verdict", FabricantVerdict.INDETERMINE),
                "confiance": verdict.get("confiance", "basse"),
                "materiaux": verdict.get("materiaux", []),
                "indices_fabrication": verdict.get("indices_fabrication", []),
                "indices_negoce": verdict.get("indices_negoce", []),
                "marques_produits_finis": verdict.get("marques_produits_finis", []),
                "justification": verdict.get("justification", ""),
                "model_used": verdict.get("_model", ""),
                "escalated": bool(verdict.get("_escalated")),
                "fetch_error": fetch_error[:128],
                "pages_fetched": pages,
            },
        )

    def _estimate_cost(self, n: int) -> float:
        from ekoalu.llm_usage.pricing import get_pricing
        price_in, price_out = get_pricing(MODEL_CHEAP)
        cost = (n * EST_INPUT_TOKENS / 1e6) * price_in
        cost += (n * EST_OUTPUT_TOKENS / 1e6) * price_out
        return cost * 0.5  # Batch API : −50 %

    def _budget_cap(self) -> float:
        import os
        return float(os.environ.get("EKOALU_DAILY_BUDGET_USD", "4"))

    def _client(self):
        from ekoalu.email_generator.generator import _get_anthropic_client
        return _get_anthropic_client()
