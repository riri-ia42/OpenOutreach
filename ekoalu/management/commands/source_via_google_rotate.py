"""Rotation Serper quotidienne — sert TOUTES les campagnes ABM à tour de rôle.

Logique (décision Richard 12/06) :
- on sert d'abord la campagne ABM active la moins récemment servie ;
- on s'arrête quand le quota de NOUVEAUX leads du jour est atteint
  (--new-leads-target) ou quand le budget de requêtes est consommé ;
- une campagne sans AUCUN nouveau profil sur 2 passages consécutifs est
  marquée épuisée et sort de la rotation (--include-exhausted pour la
  resservir, --reset-exhausted pour tout remettre à zéro, ex. mensuel).

Usage :
    python manage.py source_via_google_rotate                  # run quotidien
    python manage.py source_via_google_rotate --dry-run
    python manage.py source_via_google_rotate --reset-exhausted
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Rotation Serper : sert les campagnes ABM à tour de rôle, détecte l'épuisement."

    def add_arguments(self, parser):
        parser.add_argument("--new-leads-target", type=int, default=30,
                            help="Quota de NOUVEAUX leads pour ce run (défaut 30).")
        parser.add_argument("--max-queries", type=int, default=60,
                            help="Budget global de requêtes Serper = crédits (défaut 60).")
        parser.add_argument("--per-campaign-max", type=int, default=15,
                            help="Max profils par campagne et par passage (défaut 15).")
        parser.add_argument("--per-campaign-queries", type=int, default=9,
                            help="Max requêtes par campagne et par passage (défaut 9 = 1/poste).")
        parser.add_argument("--include-exhausted", action="store_true",
                            help="Resert aussi les campagnes marquées épuisées.")
        parser.add_argument("--reset-exhausted", action="store_true",
                            help="Remet à zéro tous les drapeaux épuisée puis sort.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        from linkedin.models import Campaign, LinkedInProfile
        from ekoalu.google_sourcing import client
        from ekoalu.google_sourcing.models import GoogleSourcingState
        from ekoalu.google_sourcing.service import source_campaign, update_rotation_state

        if opts["reset_exhausted"]:
            n = GoogleSourcingState.objects.filter(exhausted=True).update(
                exhausted=False, consecutive_empty_runs=0,
            )
            self.stdout.write(self.style.SUCCESS(f"{n} campagnes remises en rotation."))
            return

        if not opts["dry_run"] and not client.is_configured():
            raise CommandError("SERPER_API_KEY manquant dans l'environnement.")

        # Campagnes ABM ACTIVES (rattachées au profil LinkedIn actif)
        profile = LinkedInProfile.objects.filter(active=True).first()
        camps_qs = Campaign.objects.filter(name__icontains=" ABM - ")
        if profile:
            camps_qs = camps_qs.filter(users=profile.user)

        # Tri rotation : jamais servies d'abord, puis les moins récemment servies
        states = {
            s.campaign_id: s
            for s in GoogleSourcingState.objects.filter(campaign__in=camps_qs)
        }
        camps = sorted(
            camps_qs,
            key=lambda c: (
                states[c.pk].last_run_at.timestamp()
                if c.pk in states and states[c.pk].last_run_at else 0.0
            ),
        )
        if not opts["include_exhausted"]:
            skipped_exhausted = [
                c.name for c in camps if c.pk in states and states[c.pk].exhausted
            ]
            camps = [c for c in camps if not (c.pk in states and states[c.pk].exhausted)]
        else:
            skipped_exhausted = []

        if not camps:
            self.stdout.write("Aucune campagne ABM active en rotation.")
            return

        target = opts["new_leads_target"]
        budget = opts["max_queries"]
        total_new = total_queries = 0

        for c in camps:
            if total_new >= target or budget <= 0:
                break
            res = source_campaign(
                c,
                max_profiles=opts["per_campaign_max"],
                per_query=10,
                query_budget=min(opts["per_campaign_queries"], budget),
                dry_run=opts["dry_run"],
            )
            budget -= res.queries_used
            total_queries += res.queries_used
            total_new += res.new_leads
            if not opts["dry_run"]:
                state = update_rotation_state(c, res)
                flag = " [ÉPUISÉE]" if state.exhausted else ""
            else:
                flag = " [dry-run]"
            self.stdout.write(
                f"{c.name.replace('EKOALU - ', '')} : "
                f"{res.new_leads} nouveaux / {res.already_known} déjà connus "
                f"({res.queries_used} requêtes){flag}",
            )

        if skipped_exhausted:
            self.stdout.write(
                f"Campagnes épuisées hors rotation ({len(skipped_exhausted)}) : "
                + ", ".join(s.replace("EKOALU - ABM - ", "") for s in skipped_exhausted[:12])
                + (" ..." if len(skipped_exhausted) > 12 else ""),
            )
        self.stdout.write(self.style.SUCCESS(
            f"Rotation terminée : {total_new} nouveaux leads, "
            f"{total_queries} crédits Serper consommés. "
            "Le daemon enrichit/qualifie sous le cap lectures.",
        ))
