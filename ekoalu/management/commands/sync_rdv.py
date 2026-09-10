"""Rattache les RDV Bookings aux prospects (fiche hub #252).

    python manage.py sync_rdv [--top 100]

Ordonnanceur du parc : un passage par jour ouvré après 18 h. Idempotent (clé =
id de la notification Bookings). Pousse un événement `prospection.rdv` au hub
pour chaque nouveau rendez-vous rapproché.
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Lit les notifications Bookings et rattache les RDV aux prospects."

    def add_arguments(self, parser):
        parser.add_argument("--top", type=int, default=100)

    def handle(self, *args, **opts):
        from ekoalu.email_canal.models import ProspectRdv
        from ekoalu.email_canal.rdv import fetch_notices, upsert_rdv

        notices = fetch_notices(top=opts["top"])
        if notices is None:
            self.stdout.write(self.style.ERROR("Outlook Gateway indisponible : rien synchronisé."))
            return
        created = matched = 0
        for n in notices:
            rdv, is_new = upsert_rdv(n)
            if rdv is None:
                continue
            if is_new:
                created += 1
                if rdv.lead_id:
                    matched += 1
                self._notify(rdv)
        # Agenda Graph : adresse, téléphone, lien Teams des RDV planifiés (10/09)
        try:
            from ekoalu.email_canal.rdv import enrich_from_calendar
            completed = enrich_from_calendar()
            if completed:
                self.stdout.write(f"Complétés depuis l'agenda : {completed}")
        except Exception as exc:  # noqa: BLE001 — l'agenda est un plus
            logger.warning("Enrichissement agenda impossible : %s", exc)
        # Passage planifié → tenu pour les RDV dont la date est passée
        for rdv in ProspectRdv.objects.filter(status=ProspectRdv.Status.PLANNED):
            rdv.refresh_status()
        orphans = ProspectRdv.objects.filter(lead__isnull=True).count()
        self.stdout.write(self.style.SUCCESS(
            f"Notifications lues : {len(notices)} | nouveaux RDV : {created} (rapprochés {matched}) | "
            f"sans prospect : {orphans} (à traiter à la main)",
        ))

    @staticmethod
    def _notify(rdv) -> None:
        try:
            from ekoalu.notifications.hub_events import post_event

            data = getattr(rdv.lead, "email_data", None) if rdv.lead else None
            post_event("prospection.rdv", "info",
                       f"RDV pris : {rdv.who or rdv.prospect_email} ({rdv.service})",
                       {"status": rdv.status, "channel": rdv.channel, "cold_variant": rdv.cold_variant,
                        "source": getattr(data, "source", ""), "naf": getattr(data, "code_naf", ""),
                        "start": rdv.start.isoformat() if rdv.start else "", "matched": bool(rdv.lead_id)})
        except Exception:  # noqa: BLE001 — best-effort
            logger.warning("Événement hub non déposé (sync_rdv)")
