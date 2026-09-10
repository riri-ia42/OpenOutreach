"""Prépare automatiquement les rendez-vous prospects à venir.

    python manage.py prepare_rdv [--days 21] [--rdv ID] [--force] [--no-event] [--dry-run]

Ordonnanceur du parc : 07:10 lun-ven (après sync_rdv de la veille et avant la
journée). Enchaîne d'abord une synchro Bookings (sync_rdv) pour ne rater aucune
réservation de la nuit, puis pour chaque RDV planifié sans préparation :
faits → brief + deck (Claude) → fichiers data/rdv/<id>/ → créneau « Prépa »
30 min avant dans l'agenda de Richard, avec les liens du dashboard.
"""
from __future__ import annotations

import logging

from django.core.management import call_command
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Brief + deck + créneau de préparation pour chaque RDV Bookings à venir."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=21)
        parser.add_argument("--rdv", type=int, default=0, help="Un seul RDV (id ProspectRdv).")
        parser.add_argument("--force", action="store_true", help="Refaire même si déjà préparé.")
        parser.add_argument("--no-event", action="store_true", help="Ne pas créer le créneau agenda.")
        parser.add_argument("--no-sync", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        from ekoalu.email_canal.models import ProspectRdv
        from ekoalu.rdv_prep.service import pending_rdvs, prepare_one

        if not opts["no_sync"] and not opts["dry_run"]:
            call_command("sync_rdv", "--top", "60")
        if opts["rdv"]:
            rdvs = list(ProspectRdv.objects.filter(pk=opts["rdv"]))
        else:
            rdvs = pending_rdvs(opts["days"])
            if opts["force"]:
                rdvs = list(ProspectRdv.objects.filter(status=ProspectRdv.Status.PLANNED).order_by("start"))
        self.stdout.write(self.style.NOTICE(f"RDV à préparer : {len(rdvs)}"))
        done = failed = 0
        for rdv in rdvs:
            if rdv.prep_status == ProspectRdv.Prep.DONE and not opts["force"]:
                continue
            self.stdout.write(f"→ #{rdv.pk} {rdv.who} ({rdv.service}) {rdv.start:%d/%m %H:%M}")
            res = prepare_one(rdv, create_event=not opts["no_event"], dry_run=opts["dry_run"])
            if res["ok"]:
                done += 1
                self.stdout.write(self.style.SUCCESS(f"  brief {res['links'].get('brief')}"))
                if opts["dry_run"]:
                    ess = (res.get("content") or {}).get("essentiel") or {}
                    self.stdout.write(f"  qui : {ess.get('qui', '')[:160]}")
                    self.stdout.write(f"  pourquoi : {ess.get('pourquoi', '')} / {ess.get('pourquoi_detail', '')[:120]}")
            else:
                failed += 1
                self.stdout.write(self.style.ERROR(f"  échec : {res['reason']}"))
        self.stdout.write(self.style.SUCCESS(f"Préparés : {done}, échecs : {failed}"))
