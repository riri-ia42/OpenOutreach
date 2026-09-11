"""Contrôle d'identité des messages email en file, et régénération des fautifs.

Capture Richard du 11/09 (« problèmes de cohérence entre les noms affichés, les
mails et les sociétés »). Trois contrôles sur chaque message encore en attente :

1. salutation : le mail nomme une personne que la garde refuse (mandataire du
   registre, prénom seul, ou autre personne que le destinataire) ;
2. société : le corps cite une raison sociale que le domaine de l'adresse
   contredit (« BUREAU D'ETUDE MATTE » écrit à oza@oza.net) ;
3. cohérence d'affichage : dirigeant qui n'est pas une personne physique.

    python manage.py audit_identites                 # rapport seul
    python manage.py audit_identites --regenerate    # réécrit les messages fautifs

La régénération passe par les générateurs (gardes appliquées), donc coûte un
appel Claude par message. `fix_salutations` reste le correctif sans API quand
seule la salutation est en cause.
"""
from __future__ import annotations

import logging
import re
import unicodedata

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

_JURIDIQUE = {"sas", "sarl", "eurl", "sasu", "sci", "snc", "ets", "groupe", "societe", "les", "des"}


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()


def cites_company(body: str, entreprise: str) -> bool:
    """True si le corps nomme la raison sociale.

    Exige TOUS les mots significatifs : « BUREAU D'ETUDE MATTE » ne compte pas
    citee parce que le mail parle de « bureaux d'etudes » au sens du metier, il
    faudrait aussi y lire « Matte ». Deux mots suffisants donnaient ce faux
    positif sur toutes les raisons sociales bâties sur un terme de metier.
    """
    words = [w for w in _norm(entreprise).split() if len(w) > 3 and w not in _JURIDIQUE]
    return bool(words) and all(w in _norm(body) for w in words)


class Command(BaseCommand):
    help = "Audite (et régénère) les messages email dont l'identité est incohérente."

    def add_arguments(self, parser):
        parser.add_argument("--regenerate", action="store_true")
        parser.add_argument("--statuses", default="pending,approved")
        parser.add_argument("--limit", type=int, default=0)

    def handle(self, *args, **opts):
        from crm.models import Lead
        from ekoalu.email_canal.models import EmailLeadData
        from ekoalu.email_canal.sender import EMAIL_KINDS
        from ekoalu.email_generator.salutation import (
            company_confirmed_by_email, dirigeant_for_salutation,
        )
        from ekoalu.outbound_validation.models import PendingOutbound

        statuses = [s.strip() for s in opts["statuses"].split(",") if s.strip()]
        rows = list(PendingOutbound.objects.filter(kind__in=EMAIL_KINDS, status__in=statuses))
        pids = [r.prospect_public_id for r in rows]
        mails = dict(Lead.objects.filter(public_identifier__in=pids)
                     .values_list("public_identifier", "contact_email"))
        infos = {p: (d, e) for p, d, e in EmailLeadData.objects
                 .filter(lead__public_identifier__in=pids)
                 .values_list("lead__public_identifier", "dirigeant", "entreprise")}

        faulty = []
        for po in rows:
            dirigeant, entreprise = infos.get(po.prospect_public_id, ("", ""))
            email = mails.get(po.prospect_public_id) or ""
            body = po.content_to_send
            first = body.splitlines()[0] if body else ""
            reasons = []
            if re.match(r"^\s*Bonjour\s+(?!,)", first) and not dirigeant_for_salutation(
                    dirigeant, email, po.prospect_company or entreprise):
                reasons.append("salutation")
            if entreprise and cites_company(body, entreprise) \
                    and not company_confirmed_by_email(entreprise, email):
                reasons.append("société citée non confirmée")
            if reasons:
                faulty.append((po, entreprise, email, reasons))

        self.stdout.write(self.style.NOTICE(
            f"Messages en file : {len(rows)} | incohérences : {len(faulty)}"))
        for po, entreprise, email, reasons in faulty:
            self.stdout.write(f"  #{po.pk} {po.kind:15} {po.status:8} | {entreprise[:32]:32} | "
                              f"{email[:32]:32} | {', '.join(reasons)}")
        if not opts["regenerate"] or not faulty:
            return

        from ekoalu.views import _regenerate_outbound_draft

        todo = faulty[: opts["limit"]] if opts["limit"] else faulty
        done = failed = 0
        self.stdout.write(self.style.NOTICE(f"\nRégénération de {len(todo)} message(s)…"))
        for po, entreprise, email, _reasons in todo:
            ok, err = _regenerate_outbound_draft(po, "")
            if ok:
                done += 1
                first_line = po.content_to_send.splitlines()[0][:60]
                self.stdout.write(f"  #{po.pk} reecrit : {first_line}")
            else:
                failed += 1
                self.stdout.write(self.style.ERROR(f"  #{po.pk} échec : {err}"))
        self.stdout.write(self.style.SUCCESS(f"Régénérés : {done}, échecs : {failed}"))
        logger.info("audit_identites: %d regenere(s), %d echec(s)", done, failed)
