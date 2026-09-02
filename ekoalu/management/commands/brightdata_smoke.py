"""Smoke test Bright Data — à lancer une fois le compte créé et le token posé.

Valide la chaîne complète sur UNE URL réelle : trigger -> poll -> snapshot ->
mapping -> affichage des champs clés. Aucune écriture DB (sauf --apply).

Usage :
    python manage.py brightdata_smoke --url https://www.linkedin.com/in/xxx/
    python manage.py brightdata_smoke --url ... --apply   # écrit sur le lead
"""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Teste l'enrichissement Bright Data sur une URL de profil réelle."

    def add_arguments(self, parser):
        parser.add_argument("--url", required=True, help="URL publique linkedin.com/in/...")
        parser.add_argument("--apply", action="store_true",
                            help="Applique snapshot+embedding au Lead correspondant s'il existe.")

    def handle(self, *args, **opts):
        from ekoalu.brightdata_enrich import client
        from ekoalu.brightdata_enrich.mapper import map_record

        if not client.is_configured():
            raise CommandError(
                "EKOALU_BRIGHTDATA_TOKEN absent — créer le compte (voir "
                "docs/BRIGHTDATA_ONBOARDING.md) puis poser le token dans .env.production.")

        url = opts["url"]
        self.stdout.write(f"Trigger dataset {client.dataset_id()} sur {url} ...")
        records = client.run_profile_scraper([url])
        self.stdout.write(f"{len(records)} enregistrement(s) reçus.")
        if not records:
            raise CommandError("Aucun enregistrement — vérifier l'URL et le dataset.")

        snap = map_record(records[0])
        self.stdout.write("--- Champs mappés (format interne) ---")
        if snap:
            for key in ("public_identifier", "full_name", "headline",
                        "location_name", "country_code"):
                self.stdout.write(f"  {key:20s}: {snap.get(key)}")
            self.stdout.write(f"  positions           : {len(snap.get('positions') or [])}")
            if snap.get("positions"):
                p = snap["positions"][0]
                self.stdout.write(f"    [0] {p.get('title')} @ {p.get('company_name')}")
        else:
            self.stdout.write(self.style.WARNING("Mapping vide — enregistrement brut :"))
            self.stdout.write(json.dumps(records[0], indent=2, ensure_ascii=False)[:3000])
            raise CommandError("Adapter mapper.py aux clés réelles ci-dessus.")

        if opts["apply"]:
            from crm.models import Lead
            from linkedin.url_utils import url_to_public_id

            pid = url_to_public_id(url)
            lead = Lead.objects.filter(public_identifier=pid).first()
            if not lead:
                raise CommandError(f"Aucun Lead {pid} en base — rien à appliquer.")
            from ekoalu.brightdata_enrich.service import _apply_snapshot
            ok = _apply_snapshot(lead, snap)
            self.stdout.write(self.style.SUCCESS(f"Snapshot appliqué au lead {pid}: {ok}"))
        self.stdout.write(self.style.SUCCESS("[OK] Bright Data opérationnel."))
