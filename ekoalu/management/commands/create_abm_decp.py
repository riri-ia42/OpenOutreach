"""RÈGLE cible prioritaire (Richard 2026-07-28) : société prioritaire → tous les
contacts, enrichis, dans les DEUX pipes (mailing + LinkedIn).

Volet LinkedIn : crée une campagne ABM « EKOALU - ABM - <Entreprise> » pour
chaque entreprise prioritaire DECP. Le reste est déjà automatique :
- sourcing Serper en rotation (`source_via_google_rotate`) : 9 requêtes-postes
  site:linkedin.com/in "<Entreprise>" "<rôle>" (directeur, conducteur de travaux,
  chargé d'affaires, BE, acheteur…)
- qualification Claude puis engagement humanisé (caps 60 invitations/semaine).

Les réglages (users, booking_link, product_docs, action_fraction) sont clonés
depuis la campagne ABM active la plus récente — mêmes conditions d'engagement.
`campaign_objective` reçoit le contexte marché DECP (sert au qualifier).

Le volet mailing est couvert par import_decp_cibles + enrich_influence_decp.

Usage :
    python manage.py create_abm_decp --dry-run
    python manage.py create_abm_decp            # prioritaires uniquement
    python manage.py create_abm_decp --limit 20
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)

ABM_PREFIX = "EKOALU - ABM - "


def abm_campaign_name(entreprise: str) -> str:
    """Nom de campagne ABM normalisé (200 chars max, parenthèses SIRENE retirées)."""
    clean = (entreprise or "").split("(")[0].strip()
    return (ABM_PREFIX + clean)[:200]


def build_objective(data) -> str:
    """Objectif de campagne : contexte DECP factuel pour le qualifier Claude."""
    raw = data.raw_json or {}
    marches = raw.get("marches") or []
    lignes = []
    for m in marches[:2]:
        objet = (m.get("objet") or "").replace("\n", " ")[:120]
        lignes.append(f"- {m.get('date', '?')} : {m.get('critere', 'lot')} — {objet}")
    prio = "Poseur NON-fabricant : cherche un fabricant pour sa fourniture aluminium.\n" \
        if raw.get("cible_prioritaire") else ""
    return (
        f"Cible prioritaire DECP ({data.code_naf}, {data.ville or data.dpt}). "
        f"Vient de remporter des marchés publics :\n" + "\n".join(lignes) + "\n"
        + prio
        + "Objectif : identifier les opérationnels (travaux, chargés d'affaires, BE, achats) "
        "et proposer EKOALU comme fabricant de menuiseries aluminium (standard + techniques : "
        "coupe-feu, désenfumage, pare-balles)."
    )


class Command(BaseCommand):
    help = "Crée une campagne LinkedIn ABM par entreprise prioritaire DECP (règle Richard 28/07)."

    def add_arguments(self, parser):
        parser.add_argument("--all-decp", action="store_true",
                            help="Toutes les entreprises DECP (défaut : cibles prioritaires).")
        parser.add_argument("--limit", type=int, default=0,
                            help="Limite de campagnes créées ce run (0 = toutes).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Affiche les campagnes qui seraient créées.")

    def handle(self, *args, **opts):
        from linkedin.models import Campaign
        from ekoalu.email_canal.models import EmailLeadData

        template = (
            Campaign.objects.filter(name__contains=" ABM - ", active=True)
            .order_by("-pk").first()
        )
        if template is None:
            raise CommandError("Aucune campagne ABM active à cloner (réglages users/booking).")

        qs = EmailLeadData.objects.filter(source=EmailLeadData.SOURCE_DECP)
        companies = [d for d in qs
                     if opts["all_decp"] or (d.raw_json or {}).get("cible_prioritaire")]

        existing = set(Campaign.objects.values_list("name", flat=True))
        todo = []
        seen_names = set()
        for data in companies:
            if not (data.entreprise or "").strip():
                continue
            name = abm_campaign_name(data.entreprise)
            if name in existing or name in seen_names:
                continue
            seen_names.add(name)
            todo.append((name, data))
        if opts["limit"] > 0:
            todo = todo[: opts["limit"]]

        self.stdout.write(self.style.NOTICE(
            f"Entreprises prioritaires DECP : {len(companies)} | campagnes à créer : {len(todo)} "
            f"(modèle réglages : « {template.name} ») | dry_run={opts['dry_run']}",
        ))

        created = 0
        for name, data in todo:
            self.stdout.write(f"→ {name}")
            if opts["dry_run"]:
                continue
            campaign = Campaign.objects.create(
                name=name,
                product_docs=template.product_docs,
                campaign_objective=build_objective(data),
                booking_link=template.booking_link,
                active=True,
                is_freemium=template.is_freemium,
                action_fraction=template.action_fraction,
            )
            campaign.users.set(template.users.all())
            created += 1

        self.stdout.write(self.style.SUCCESS(
            f"\n--- Bilan ---\n"
            f"  campagnes ABM créées : {created}\n"
            f"  déjà existantes      : {len(companies) - len(todo)}\n"
            f"  dry_run              : {opts['dry_run']}\n"
            f"Le sourcing Serper en rotation + la qualification prendront le relais automatiquement.",
        ))
        logger.info("create_abm_decp: created=%d", created)
