"""Applique la garde de salutation aux messages email encore en file.

Capture Richard du 11/09 : des relances saluaient le dirigeant du registre
alors que l'adresse désigne quelqu'un d'autre (« Bonjour M. Duchateau » vers
ablampey@blampey.fr), ou un mandataire qui n'est pas une personne physique
(« CABINET EMMANUEL CHEVIGNARD »). La garde est désormais appliquée à la
génération ; cette commande répare les messages déjà rédigés, sans appeler
l'API : la ligne de salutation devient « Bonjour, », le reste est intact.

Le verdict est pris sur le dirigeant CONNU EN BASE (pas sur le nom écrit dans
le mail, qui n'est qu'un nom de famille) : un message n'est touché que si la
garde refuse ce dirigeant ET que le mail le nomme quand même.

    python manage.py fix_salutations [--dry-run] [--statuses pending,approved]
"""
from __future__ import annotations

import logging
import re

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

_SALUT_RE = re.compile(r"^\s*Bonjour\s+(?!,)(.+?)\s*,\s*$")


class Command(BaseCommand):
    help = "Retire les salutations nominatives risquées des emails en file."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--statuses", default="pending,approved")

    def handle(self, *args, **opts):
        from crm.models import Lead
        from ekoalu.email_canal.models import EmailLeadData
        from ekoalu.email_canal.sender import EMAIL_KINDS
        from ekoalu.email_generator.salutation import dirigeant_for_salutation
        from ekoalu.outbound_validation.models import PendingOutbound

        statuses = [s.strip() for s in opts["statuses"].split(",") if s.strip()]
        rows = list(PendingOutbound.objects.filter(kind__in=EMAIL_KINDS, status__in=statuses))
        pids = [r.prospect_public_id for r in rows]
        emails = dict(Lead.objects.filter(public_identifier__in=pids)
                      .values_list("public_identifier", "contact_email"))
        data = {
            pid: (dirigeant, entreprise)
            for pid, dirigeant, entreprise in EmailLeadData.objects
            .filter(lead__public_identifier__in=pids)
            .values_list("lead__public_identifier", "dirigeant", "entreprise")
        }
        fixed = 0
        for po in rows:
            body = po.content_to_send
            first = body.splitlines()[0] if body else ""
            if not _SALUT_RE.match(first):
                continue          # déjà « Bonjour, » ou pas de salutation
            dirigeant, entreprise = data.get(po.prospect_public_id, ("", ""))
            if dirigeant_for_salutation(dirigeant, emails.get(po.prospect_public_id) or "",
                                        po.prospect_company or entreprise):
                continue          # la garde autorise ce nom : on ne touche pas
            self.stdout.write(f"  #{po.pk} {po.prospect_company or entreprise or po.prospect_public_id} : "
                              f"« {first.strip()} » (base : {dirigeant or '?'}) → « Bonjour, »")
            if not opts["dry_run"]:
                po.final_content = "Bonjour," + body[len(first):]
                po.save(update_fields=["final_content"])
            fixed += 1
        self.stdout.write(self.style.SUCCESS(
            f"Salutations corrigées : {fixed} / {len(rows)} message(s) en file "
            f"(statuts {','.join(statuses)}, dry_run={opts['dry_run']})",
        ))
        logger.info("fix_salutations: %d corrige(s) sur %d", fixed, len(rows))
