"""Active l'A/B qualifier (challenger Haiku vs champion Sonnet) sur N qualifications.

Leve le kill-switch ``qualifier_disabled.flag`` et pose le sentinel d'A/B. Le daemon
score alors chaque profil avec les deux modeles ; a epuisement du quota il se re-met
en pause et maile le recap.

    python manage.py start_qualifier_ab --n 50
    python manage.py start_qualifier_ab --status
    python manage.py start_qualifier_ab --stop
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from ekoalu.qualifier_ab import runner


class Command(BaseCommand):
    help = "Active/inspecte l'A/B qualifier Haiku vs Sonnet."

    def add_arguments(self, parser):
        parser.add_argument("--n", type=int, default=50, help="Nombre de qualifications a comparer.")
        parser.add_argument("--challenger", type=str, default=runner.DEFAULT_CHALLENGER,
                            help="Modele challenger (defaut Haiku 4.5).")
        parser.add_argument("--status", action="store_true", help="Affiche l'etat courant.")
        parser.add_argument("--stop", action="store_true", help="Arrete l'A/B et re-pause le qualifier.")

    def handle(self, *args, **opts):
        if opts["status"]:
            state = runner._read_sentinel()
            s = runner.summarize()
            self.stdout.write(f"actif: {runner.ab_is_active()} | sentinel: {state}")
            self.stdout.write(
                f"resultats: {s['total']} scores | Sonnet OUI={s['champion_qualified']} "
                f"| Haiku OUI={s['challenger_qualified']}/{s['challenger_scored']} "
                f"| accord={s['agreement_pct']}%")
            return
        if opts["stop"]:
            runner._finalize(runner._read_sentinel() or {})
            self.stdout.write(self.style.WARNING("A/B arrete, qualifier re-pause, recap maile."))
            return

        state = runner.start_ab(n=opts["n"], challenger_model=opts["challenger"])
        self.stdout.write(self.style.SUCCESS(
            f"A/B active : {state['remaining']} qualifs, challenger={state['challenger_model']}. "
            f"Le qualifier est reactive ; il se re-pausera et mailera le recap a la fin."))
