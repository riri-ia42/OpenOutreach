"""Source des profils LinkedIn via Google Custom Search (ABM, chantier #3).

Trouve des URLs de profils via Google (pas de recherche LinkedIn), cree des
leads URL-only rattaches a leur campagne (LeadDiscovery). Le DAEMON les enrichit
et les qualifie ensuite dans sa propre session browser (on n'ouvre pas de 2e
navigateur ici).

Usage :
    python manage.py source_via_google --campaign "Leon Grosse" --dry-run
    python manage.py source_via_google --campaign "Leon Grosse" --max 15
    python manage.py source_via_google --max 10 --max-queries 90   # toutes les ABM

Credits Serper : 1 credit/requete -> --max-queries borne la conso (defaut 90).
Config : env SERPER_API_KEY.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Source des profils LinkedIn via Google Custom Search (campagnes ABM)."

    def add_arguments(self, parser):
        parser.add_argument("--campaign", help="Filtre nom de campagne (substring). Defaut : toutes les ABM.")
        parser.add_argument("--max", type=int, default=20, help="Max profils crees par campagne.")
        parser.add_argument("--per-query", type=int, default=10, help="Resultats demandes par requete (<=10).")
        parser.add_argument("--max-queries", type=int, default=90, help="Plafond global de requetes Google (quota).")
        parser.add_argument("--dry-run", action="store_true", help="Affiche les URLs sans rien creer.")

    def handle(self, *args, **opts):
        from crm.models import Lead
        from linkedin.models import Campaign
        from linkedin.url_utils import public_id_to_url, url_to_public_id
        from ekoalu.google_sourcing import client, queries
        from ekoalu.lead_routing.patch import record_discovery

        dry = opts["dry_run"]
        if not dry and not client.is_configured():
            raise CommandError(
                "SERPER_API_KEY manquant dans l'environnement (.env). "
                "Cf. README. Utilise --dry-run pour tester sans clef.",
            )

        camps = Campaign.objects.filter(name__icontains=" ABM - ").order_by("name")
        if opts["campaign"]:
            camps = camps.filter(name__icontains=opts["campaign"])
        camps = list(camps)
        if not camps:
            self.stdout.write("Aucune campagne ABM correspondante.")
            return

        query_budget = opts["max_queries"]
        total_created = 0
        skipped: list[str] = []

        for c in camps:
            if query_budget <= 0:
                skipped.append(c.name)
                continue

            qlist = queries.build_abm_queries(c)
            if not qlist:
                self.stdout.write(f"[?] {c.name} : entreprise cible introuvable, ignoree.")
                continue

            found: list[str] = []
            seen: set[str] = set()
            for q in qlist:
                if len(found) >= opts["max"] or query_budget <= 0:
                    break
                query_budget -= 1
                try:
                    urls = client.search_linkedin_profiles(q, max_results=opts["per_query"])
                except Exception as e:  # reseau/quota : on n'arrete pas tout
                    self.stderr.write(f"  [!] requete echouee ({q!r}) : {e}")
                    continue
                for u in urls:
                    pid = url_to_public_id(u)
                    if pid and pid not in seen:
                        seen.add(pid)
                        found.append(u)

            found = found[: opts["max"]]
            created = 0
            for u in found:
                pid = url_to_public_id(u)
                if not pid:
                    continue
                if dry:
                    self.stdout.write(f"  [dry] {c.name} <- {public_id_to_url(pid)}")
                    continue
                lead, _ = Lead.objects.get_or_create(
                    public_identifier=pid,
                    defaults={"linkedin_url": public_id_to_url(pid)},
                )
                record_discovery(lead.pk, c)
                created += 1

            total_created += created
            verb = "trouves" if dry else "crees/rattaches"
            self.stdout.write(f"{c.name} : {len(found)} profils {verb} (budget requetes restant : {query_budget}).")

        if skipped:
            self.stdout.write(self.style.WARNING(
                f"Quota requetes epuise — {len(skipped)} campagnes NON traitees : "
                + ", ".join(s.replace('EKOALU - ', '') for s in skipped[:10])
                + (" ..." if len(skipped) > 10 else ""),
            ))
        self.stdout.write(self.style.SUCCESS(
            f"Termine : {total_created} leads {'simules' if dry else 'crees/rattaches'} "
            f"({len(camps) - len(skipped)} campagnes traitees). "
            "Le daemon les enrichira et qualifiera (scope routage).",
        ))
