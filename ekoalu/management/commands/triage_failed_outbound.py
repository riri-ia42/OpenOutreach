"""Triage des PendingOutbound en echec (backlog accumule depuis mai).

Etat constate 10/06 : 141 PO status=failed jamais tries — 118 victimes du bug
asyncio (corrige 22/05), 18 echecs transitoires de compose (corriges 03/06),
le reste divers. Beaucoup sont devenus obsoletes entre-temps.

Regles de triage, dans l'ordre (la premiere qui matche gagne) :

1. Lead disqualifie ou desinscrit          -> REJECTED (ne jamais recontacter)
2. Un autre PO (sent/pending/approved) existe
   pour le meme (prospect, kind)           -> REJECTED (remplace, doublon evite)
3. Le Deal vise est deja passe a un etat qui
   rend l'envoi sans objet :
   - invitation alors que Deal Connected/Completed/Failed
   - follow_up  alors que Deal Completed/Failed
                                           -> REJECTED (obsolete)
4. Sinon (echec transitoire, lead sain)    -> re-PENDING pour re-validation
   Richard (le contexte a pu changer depuis la generation : on ne renvoie
   pas en approved automatiquement).

Usage :
    python manage.py triage_failed_outbound --dry-run   # affiche sans ecrire
    python manage.py triage_failed_outbound             # execute

Idempotent : une fois tries, plus aucun PO failed ne reste.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

TRIAGE_TAG = "triage_failed_outbound"


class Command(BaseCommand):
    help = "Trie les PendingOutbound failed : rejet des obsoletes, re-pending des transitoires."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="N'ecrit rien, affiche juste ce qui serait fait",
        )

    def handle(self, *args, **opts):
        from crm.models import Deal, Lead
        from ekoalu.outbound_validation.models import (
            OutboundKind,
            OutboundStatus,
            PendingOutbound,
        )
        from linkedin.enums import ProfileState

        dry_run = opts["dry_run"]
        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY-RUN (rien ecrit en base) ==="))

        failed = list(
            PendingOutbound.objects.filter(status=OutboundStatus.FAILED)
            .order_by("created_at")
        )
        self.stdout.write(f"PendingOutbound failed a trier : {len(failed)}")
        if not failed:
            self.stdout.write(self.style.SUCCESS("Rien a trier."))
            return

        pids = {po.prospect_public_id for po in failed}
        dead_leads = set(
            Lead.objects.filter(public_identifier__in=pids)
            .filter(disqualified=True)
            .values_list("public_identifier", flat=True)
        ) | set(
            Lead.objects.filter(public_identifier__in=pids)
            .exclude(unsubscribed_at=None)
            .values_list("public_identifier", flat=True)
        )

        # (prospect, kind) ayant un PO encore utile (envoye ou en file)
        superseding = set(
            PendingOutbound.objects.filter(
                prospect_public_id__in=pids,
                status__in=[
                    OutboundStatus.SENT,
                    OutboundStatus.PENDING,
                    OutboundStatus.APPROVED,
                ],
            ).values_list("prospect_public_id", "kind")
        )

        # Etat des deals par (public_id, campaign_id)
        deal_states = {
            (pid, cid): state
            for pid, cid, state in Deal.objects.filter(
                lead__public_identifier__in=pids,
            ).values_list("lead__public_identifier", "campaign_id", "state")
        }

        obsolete_states = {
            OutboundKind.INVITATION.value: {
                ProfileState.CONNECTED.value,
                ProfileState.COMPLETED.value,
                ProfileState.FAILED.value,
            },
            OutboundKind.FOLLOW_UP.value: {
                ProfileState.COMPLETED.value,
                ProfileState.FAILED.value,
            },
        }

        rejected, repending = [], []
        for po in failed:
            pid, kind = po.prospect_public_id, po.kind
            deal_state = deal_states.get((pid, po.campaign_id))
            if pid in dead_leads:
                reason = "lead disqualifie/desinscrit"
            elif (pid, kind) in superseding:
                reason = "remplace par un PO plus recent (sent/pending/approved)"
            elif deal_state in obsolete_states.get(kind, set()):
                reason = f"obsolete (deal deja {deal_state})"
            else:
                repending.append(po)
                continue
            rejected.append((po, reason))

        self.stdout.write(f"  -> a rejeter      : {len(rejected)}")
        for po, reason in rejected[:15]:
            self.stdout.write(f"     #{po.pk} {po.kind:11s} {po.prospect_public_id} — {reason}")
        if len(rejected) > 15:
            self.stdout.write(f"     ... et {len(rejected) - 15} autres")
        self.stdout.write(f"  -> a re-pending   : {len(repending)} (re-validation Richard)")
        for po in repending:
            self.stdout.write(f"     #{po.pk} {po.kind:11s} {po.prospect_public_id}")

        if dry_run:
            return

        for po, reason in rejected:
            po.status = OutboundStatus.REJECTED
            po.rejection_reason = f"{TRIAGE_TAG}: {reason}"
            po.save(update_fields=["status", "rejection_reason"])
        for po in repending:
            po.status = OutboundStatus.PENDING
            po.error_message = f"({TRIAGE_TAG}: echec transitoire, remis en validation) {po.error_message}"[:1000]
            po.save(update_fields=["status", "error_message"])

        self.stdout.write(self.style.SUCCESS(
            f"Triage termine : {len(rejected)} rejetes, {len(repending)} remis en validation.",
        ))
