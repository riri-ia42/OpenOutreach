"""Marge de sécurité du pipe : corrige à mi-journée et en fin de journée.

Consigne Richard du 11/09 : « nous sommes très en dessous de ces max, il faut
mettre une routine qui corrige à mi-journée et fin de journée pour le jour
d'après. M'alerte par mail si c'est ma non validation qui risque de bloquer.
Toujours avoir dans le pipe des marges de sécurité. »

La routine compare, canal par canal, le STOCK PRÊT au plafond du prochain jour
ouvré, et fabrique ce qui manque pour tenir `--marge` jours d'avance.

Elle distingue deux manques qui n'ont pas le même remède :

- **stock court** → elle fabrique (génération de mails, enrichissement qui
  réalimente la qualification) ;
- **stock plein mais expédition en dessous du plafond** → elle NE fabrique PAS.
  Gonfler une file déjà pleine ne fait pas partir un mail de plus. Le cas est
  signalé tel quel, pour que le goulot soit traité là où il est.

Mail à Richard dans un seul cas : le stock existe mais il attend SA validation.
C'est le seul blocage que la routine ne peut pas lever elle-même.

    python manage.py pipeline_margin --dry-run     # relevé seul
    python manage.py pipeline_margin               # relevé + corrections
    python manage.py pipeline_margin --marge 3
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from ekoalu.pipeline_margin.measure import DEFAULT_MARGIN_DAYS

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Maintient une marge de sécurité dans le pipe pour le jour suivant."

    def add_arguments(self, parser):
        parser.add_argument("--marge", type=float, default=DEFAULT_MARGIN_DAYS,
                            help=f"Jours de stock visés (défaut {DEFAULT_MARGIN_DAYS}).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Relevé seul, aucune correction, aucun mail.")
        parser.add_argument("--no-mail", action="store_true",
                            help="Corrige mais n'alerte pas Richard.")

    def handle(self, *args, **opts):
        from ekoalu.pipeline_margin.service import corriger, releve

        marge = float(opts["marge"])
        canaux = releve(marge_jours=marge)
        lignes, corrections, bloques, sous_regime = [], [], [], []

        for canal in canaux:
            manque = canal.manque()
            couv = canal.couverture_jours
            couv_txt = "∞" if couv == float("inf") else f"{couv:.1f} j"
            lignes.append(
                f"[{'KO' if manque else 'OK'}] {canal.libelle}: {canal.pret} prêt(s) "
                f"+ {canal.en_validation} en validation | demain {canal.besoin_demain}, "
                f"{marge:g} prochains jours actifs {canal.besoin_marge} "
                f"(couverture {couv_txt})",
            )
            if canal.bloque_par_validation:
                bloques.append(canal)
            if canal.sous_regime:
                sous_regime.append(canal)
                lignes.append(
                    f"       >> expédition sous le plafond : {canal.realise_hier} "
                    f"envoyé(s) hier pour {canal.plafond_hier} possibles, alors que le "
                    f"stock suffit. Fabriquer plus n'y changerait rien.",
                )
            if manque and not opts["dry_run"]:
                compte_rendu = corriger(canal, manque)
                if compte_rendu:
                    corrections.append(f"{canal.libelle} : {compte_rendu}")
                    lignes.append(f"       >> {compte_rendu}")
            elif manque:
                lignes.append(f"       >> manque {manque} (correction : "
                              f"{canal.correction or 'décision de Richard'})")

        texte = "\n".join(lignes)
        self.stdout.write(texte)
        if not opts["dry_run"]:
            self._pousser_hub(canaux, corrections, bloques, sous_regime)
        if bloques and not opts["dry_run"] and not opts["no_mail"]:
            self._alerter(bloques, marge)
        elif bloques:
            self.stdout.write(self.style.WARNING(
                f"{len(bloques)} canal/canaux bloqué(s) par la validation (mail non envoyé)."))

    # -- effets de bord -----------------------------------------------------

    def _alerter(self, bloques, marge: float) -> None:
        """Mail à Richard — uniquement quand SA validation est le blocage."""
        from ekoalu.notifications import graph_mailer

        lignes = [
            "Le pipe a le stock nécessaire pour demain, mais il attend ta validation.",
            "",
        ]
        for canal in bloques:
            lignes.append(
                f"- {canal.libelle} : {canal.pret} prêt(s) à partir pour "
                f"{canal.besoin_demain} attendus demain, et {canal.en_validation} "
                f"message(s) en attente de ta décision.",
            )
        lignes += [
            "",
            "Valider ici : http://ekoalu-prospection:3210/ekoalu/messages/?status=pending",
            "",
            "C'est le seul manque que la routine ne peut pas combler seule : elle "
            "fabrique ce qui manque, elle ne valide pas à ta place.",
        ]
        ok = graph_mailer.send_mail(
            subject="Prospection : ta validation bloque le pipe de demain",
            body="\n".join(lignes),
            category="alert",
        )
        self.stdout.write(self.style.WARNING(
            f"Mail d'alerte à Richard : {'envoyé' if ok else 'ÉCHEC'}"))

    def _pousser_hub(self, canaux, corrections, bloques, sous_regime) -> None:
        from ekoalu.notifications.hub_events import post_event

        manquants = [c.libelle for c in canaux if c.manque()]
        post_event(
            "prospection.pipeline_margin",
            "warn" if (bloques or sous_regime) else "info",
            "Marge du pipe : " + ("à combler" if manquants else "tenue"),
            {
                "status": "sous_marge" if manquants else "ok",
                "canaux": [
                    {
                        "cle": c.cle, "besoin_demain": c.besoin_demain,
                        "besoin_marge": c.besoin_marge,
                        "pret": c.pret, "en_validation": c.en_validation,
                        "couverture_jours": (None if c.couverture_jours == float("inf")
                                             else round(c.couverture_jours, 2)),
                        "realise_hier": c.realise_hier, "plafond_hier": c.plafond_hier,
                    }
                    for c in canaux
                ],
                "corrections": corrections,
                "bloque_par_validation": [c.cle for c in bloques],
                "expedition_sous_plafond": [c.cle for c in sous_regime],
            },
        )
