"""Import des corrections retour-mail (capture Richard 31/08).

Le worker retour-mail (Documents/CLAUDE/retour-mail, 24/7) détecte dans
l'Outlook de Richard les bounces et réponses automatiques issus de la
prospection et dépose ses corrections dans
`_partage/retour-mail-corrections.json`. Cette commande les applique :

  - hard_bounce (adresse morte OU départ) → Lead.email_bounced_at = now
    (le sender exclut déjà les leads bounced — même effet que bounce.py) ;
  - address_change (même personne, nouvelle adresse) → Lead.contact_email
    remplacé, bounce/unsub remis à zéro.

Chaque correction traitée reçoit 'prospection-ia' dans `imported_by`
(idempotence). Déclenchée automatiquement par retour-mail après chaque dépôt ;
relançable : `manage.py import_retour_mail [--dry-run]`.
"""
from __future__ import annotations

import json

from django.core.management.base import BaseCommand
from django.utils import timezone

from ekoalu.retour_mail_dropfile import drop_file_path  # noqa: F401 — chemin partagé avec la vérification avant envoi

APP = "prospection-ia"




class Command(BaseCommand):
    help = "Applique les corrections du worker retour-mail (bounces, changements d'adresse)"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Liste sans rien modifier")

    def handle(self, *args, **opts):
        from crm.models import Lead

        path = drop_file_path()
        if not path.is_file():
            self.stdout.write(f"Aucun drop-file ({path}) — rien à importer.")
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        pending = [c for c in data.get("corrections", []) if APP not in (c.get("imported_by") or [])]
        if not pending:
            self.stdout.write("Rien à importer (tout déjà traité).")
            return

        now = timezone.now()
        bounced = replaced = unknown = skipped = 0
        for c in pending:
            email = (c.get("email") or "").lower()
            if c.get("type") == "new_contact":
                # Migration de messagerie (validé Richard 2026-09-01) : les nouveaux contacts
                # sont créés dans l'antichambre (sas « a_valider ») et ajoutés à la liste de
                # masse Mailjet. Ici un Lead représente une personne découverte via
                # LinkedIn/DECP et exige linkedin_url + public_identifier : on n'en fabrique
                # pas depuis une simple adresse. Marqué importé pour ne pas rester en attente.
                skipped += 1
                if not opts["dry_run"]:
                    c["imported_by"] = [*(c.get("imported_by") or []), APP]
                continue
            leads = list(Lead.objects.filter(contact_email__iexact=email))
            if not leads:
                unknown += 1
            elif c.get("type") == "address_change" and c.get("new_email"):
                for lead in leads:
                    if not opts["dry_run"]:
                        lead.contact_email = c["new_email"].lower()
                        lead.email_bounced_at = None
                        lead.save(update_fields=["contact_email", "email_bounced_at"])
                    replaced += 1
                    self.stdout.write(f"  adresse remplacée : {email} -> {c['new_email']}")
            else:
                for lead in leads:
                    if lead.email_bounced_at is None and not opts["dry_run"]:
                        lead.email_bounced_at = now
                        lead.save(update_fields=["email_bounced_at"])
                    bounced += 1
                    self.stdout.write(f"  email_bounced_at : {email} [{c.get('provenance', '')}]")
            if not opts["dry_run"]:
                c["imported_by"] = [*(c.get("imported_by") or []), APP]

        if not opts["dry_run"]:
            data["updated_at"] = now.isoformat(timespec="seconds")
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        self.stdout.write(self.style.SUCCESS(
            f"Import terminé : {bounced} bounce(s), {replaced} remplacée(s), "
            f"{skipped} nouveau(x) contact(s) laissé(s) à l'antichambre, "
            f"{unknown} inconnue(s) du CRM (marquées importées)."
        ))
