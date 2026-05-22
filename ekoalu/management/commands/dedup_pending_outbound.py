"""Purge les doublons PendingOutbound (meme prospect, plusieurs campagnes).

Avant fix ABM dedup multi-campagnes (22/05/2026), le daemon creait un
PendingOutbound par (prospect, campagne) -- donc un prospect cible par 9
campagnes ABM avait 9 invitations identiques en attente. Risque ban LinkedIn
+ multiplication couts IA.

Cette commande garde 1 PendingOutbound par (prospect_public_id, kind), celui
qui a le min(id) (= cree en premier). Les autres sont supprimes en base.

Usage :
    python manage.py dedup_pending_outbound --dry-run    # lecture seule, montre ce qui serait fait
    python manage.py dedup_pending_outbound              # execute la purge
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand
from django.db.models import Count

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Purge les doublons PendingOutbound (1 par (prospect, kind) - garde min id)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Ne supprime rien, affiche juste ce qui serait fait")

    def handle(self, *args, **opts):
        from django.apps import apps
        PendingOutbound = apps.get_model("ekoalu", "PendingOutbound")
        OutboundStatus = PendingOutbound._meta.get_field("status").choices
        open_statuses = [s[0] for s in OutboundStatus
                         if s[0] in ("pending", "approved", "blocked_company")]

        # Trouve les (prospect_public_id, kind) en doublon
        dups = PendingOutbound.objects.filter(
            status__in=open_statuses,
        ).values("prospect_public_id", "kind").annotate(
            n=Count("id"),
        ).filter(n__gt=1).order_by("-n")

        total_dups = dups.count()
        total_to_delete = 0
        details = []

        for d in dups:
            pid = d["prospect_public_id"]
            kind = d["kind"]
            n = d["n"]
            # Garde le min(id), supprime les autres
            ids = list(PendingOutbound.objects.filter(
                prospect_public_id=pid,
                kind=kind,
                status__in=open_statuses,
            ).order_by("id").values_list("id", flat=True))
            keep_id = ids[0]
            delete_ids = ids[1:]
            total_to_delete += len(delete_ids)
            details.append({
                "prospect": pid,
                "kind": kind,
                "keep": keep_id,
                "delete": delete_ids,
            })

        self.stdout.write(self.style.WARNING(
            f"{total_dups} (prospect, kind) en doublon - {total_to_delete} PendingOutbound a supprimer",
        ))
        for d in details:
            self.stdout.write(
                f"  {d['prospect'][:30]:30} | {d['kind']:15} | keep=#{d['keep']} | delete={d['delete']}",
            )

        if opts["dry_run"]:
            self.stdout.write(self.style.SUCCESS("Dry-run termine -- rien supprime."))
            return

        if total_to_delete == 0:
            self.stdout.write(self.style.SUCCESS("Aucun doublon, rien a faire."))
            return

        # Execute la purge
        all_delete_ids = [i for d in details for i in d["delete"]]
        deleted, _ = PendingOutbound.objects.filter(id__in=all_delete_ids).delete()
        self.stdout.write(self.style.SUCCESS(
            f"{deleted} PendingOutbound supprimes ({total_dups} prospects en doublon resolus).",
        ))
        logger.info("dedup_pending_outbound: supprime %d rows", deleted)
