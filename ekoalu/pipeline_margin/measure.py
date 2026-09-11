"""Mesure de la couverture du pipe, canal par canal.

Constat Richard du 11/09 : « nous sommes très en dessous de ces max ». Le relevé
sur 15 jours montre que l'écart n'est PAS au même endroit selon le canal —
d'où cette mesure en deux grandeurs distinctes, à ne jamais confondre :

- **la couverture** : combien de jours de stock prêt le canal a devant lui. Un
  stock court se corrige en fabriquant plus (génération, enrichissement).
- **le réalisé** : ce qui est effectivement parti hier, face au plafond du jour.
  Un réalisé court avec un stock plein ne se corrige PAS en fabriquant plus :
  le goulot est à l'expédition, et fabriquer davantage ne ferait que gonfler
  une file déjà pleine.

Au 11/09 : le mail a 299 messages prêts pour 50/jour (six jours de couverture)
mais n'en expédie que 20 à 45 ; LinkedIn a 10 prospects prêts pour un plafond
de 12 (moins d'un jour) et n'envoie que 0 à 7 invitations.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

# Jours de stock visés devant soi. Un jour de couverture, c'est déjà être en
# retard : la fabrication n'est pas instantanée (enrichissement, qualification,
# génération, validation de Richard).
DEFAULT_MARGIN_DAYS = 2.0


@dataclass
class Canal:
    """État d'un canal face à son plafond du lendemain."""

    cle: str
    libelle: str
    besoin_demain: int          # plafond du prochain jour actif
    besoin_marge: int           # SOMME des plafonds des N prochains jours actifs
    pret: int                   # stock immédiatement expédiable
    en_validation: int          # stock bloqué par la validation de Richard
    realise_hier: int
    plafond_hier: int
    jours_fenetre: int = 1      # nombre de jours actifs couverts par besoin_marge
    correction: str = ""        # ce que la routine peut faire, "" si rien
    notes: list[str] = field(default_factory=list)

    @property
    def stock_total(self) -> int:
        return self.pret + self.en_validation

    @property
    def couverture_jours(self) -> float:
        """Jours de stock devant soi, sur le rythme MOYEN de la fenêtre.

        Rapporter le stock au seul plafond de demain trompe : un vendredi soir,
        11 prospects prêts face au samedi (plafond 2) affichent 5 jours de
        couverture alors que lundi en réclame 12 à lui seul.
        """
        if self.besoin_marge <= 0 or self.jours_fenetre <= 0:
            return float("inf")
        moyenne = self.besoin_marge / self.jours_fenetre
        return self.stock_total / moyenne if moyenne else float("inf")

    def manque(self) -> int:
        """Combien d'unités fabriquer pour tenir la marge. 0 si elle est tenue.

        Le besoin est la SOMME des plafonds des prochains jours actifs, pas le
        plafond de demain multiplié : un vendredi soir, deux jours de marge
        valent samedi (20 mails) plus lundi (50), pas deux fois samedi.
        """
        return max(0, self.besoin_marge - self.stock_total)

    @property
    def bloque_par_validation(self) -> bool:
        """Le stock existe mais il attend Richard.

        C'est le SEUL cas que la routine ne peut pas corriger seule, donc le
        seul qui justifie un mail (consigne Richard 11/09).
        """
        return self.pret < self.besoin_demain <= self.stock_total

    @property
    def sous_regime(self) -> bool:
        """Expédie moins que son plafond alors que le stock ne manque pas."""
        return (self.plafond_hier > 0
                and self.realise_hier < 0.6 * self.plafond_hier
                and self.couverture_jours >= 1.0)


def prochains_jours_actifs(jour: dt.date, combien: int) -> list[dt.date]:
    """Les `combien` prochains jours où le pipe tourne.

    Samedi compte (quota réduit), dimanche et fériés français non.
    """
    from ekoalu.human_scheduler import holidays

    out: list[dt.date] = []
    candidat = jour
    for _ in range(60):
        if len(out) >= combien:
            break
        candidat += dt.timedelta(days=1)
        if candidat.weekday() != 6 and not holidays.is_french_holiday(candidat):
            out.append(candidat)
    return out


def jour_ouvre_suivant(jour: dt.date) -> dt.date:
    """Prochain jour où le pipe tourne."""
    suivants = prochains_jours_actifs(jour, 1)
    return suivants[0] if suivants else jour + dt.timedelta(days=1)
