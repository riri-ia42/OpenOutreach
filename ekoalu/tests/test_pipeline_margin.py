"""Marge de sécurité du pipe (consigne Richard 11/09).

Ce qui doit être protégé par des tests : la routine fabrique quand le STOCK
manque, elle ne fabrique PAS quand le stock est plein et que c'est l'expédition
qui traîne, et elle n'écrit à Richard que si SA validation est le blocage.
"""
from __future__ import annotations

import datetime as dt

import pytest

from ekoalu.pipeline_margin.measure import Canal, prochains_jours_actifs


def _canal(**kw) -> Canal:
    base = dict(cle="c", libelle="Canal", besoin_demain=10, besoin_marge=20,
                jours_fenetre=2, pret=0, en_validation=0, realise_hier=0, plafond_hier=10)
    base.update(kw)
    return Canal(**base)


class TestFenetre:
    def test_le_dimanche_est_saute_pas_le_samedi(self):
        vendredi = dt.date(2026, 9, 11)
        assert prochains_jours_actifs(vendredi, 2) == [
            dt.date(2026, 9, 12), dt.date(2026, 9, 14)]   # samedi puis LUNDI

    def test_un_ferie_est_saute(self):
        # 1er novembre 2026 tombe un dimanche, le 11 novembre un mercredi
        avant = dt.date(2026, 11, 10)
        suite = prochains_jours_actifs(avant, 2)
        assert dt.date(2026, 11, 11) not in suite
        assert suite == [dt.date(2026, 11, 12), dt.date(2026, 11, 13)]


class TestManque:
    def test_le_besoin_somme_les_jours_actifs_au_lieu_de_multiplier(self):
        """Un vendredi soir, 2 jours de marge = samedi (20) + lundi (50) = 70,
        pas deux fois samedi."""
        canal = _canal(besoin_demain=20, besoin_marge=70, jours_fenetre=2, pret=40)
        assert canal.manque() == 30

    def test_marge_tenue_ne_fabrique_rien(self):
        assert _canal(besoin_marge=20, pret=25).manque() == 0

    def test_couverture_sur_le_rythme_moyen(self):
        # 11 prets, fenetre samedi(2)+lundi(12)=14 sur 2 jours -> moyenne 7/jour
        canal = _canal(besoin_demain=2, besoin_marge=14, jours_fenetre=2, pret=11)
        assert round(canal.couverture_jours, 2) == 1.57


class TestBlocageValidation:
    def test_stock_en_validation_suffisant_est_un_blocage_richard(self):
        canal = _canal(besoin_demain=10, pret=4, en_validation=9)
        assert canal.bloque_par_validation is True

    def test_stock_pret_suffisant_n_est_pas_un_blocage(self):
        assert _canal(besoin_demain=10, pret=12, en_validation=50).bloque_par_validation is False

    def test_stock_globalement_insuffisant_n_est_pas_un_blocage_richard(self):
        """Rien à valider : c'est la fabrication qui manque, pas la décision."""
        assert _canal(besoin_demain=10, pret=2, en_validation=3).bloque_par_validation is False


class TestExpeditionSousPlafond:
    def test_signale_quand_le_stock_suffit_mais_que_rien_ne_part(self):
        canal = _canal(besoin_marge=20, pret=300, realise_hier=5, plafond_hier=50)
        assert canal.sous_regime is True
        assert canal.manque() == 0        # et surtout : on ne fabrique pas plus

    def test_ne_signale_pas_quand_le_stock_est_a_sec(self):
        assert _canal(besoin_marge=20, pret=1, realise_hier=0, plafond_hier=50).sous_regime is False


@pytest.mark.django_db
class TestCommande:
    def test_dry_run_n_ecrit_rien_et_n_alerte_pas(self, monkeypatch):
        from io import StringIO
        from unittest.mock import patch

        from django.core.management import call_command

        out = StringIO()
        with patch("ekoalu.notifications.graph_mailer.send_mail") as mail, \
                patch("ekoalu.pipeline_margin.service.corriger") as corr:
            call_command("pipeline_margin", "--dry-run", stdout=out)
        mail.assert_not_called()
        corr.assert_not_called()
        assert "Cold mails" in out.getvalue()

    def test_le_mail_part_seulement_sur_blocage_validation(self, monkeypatch):
        from io import StringIO
        from unittest.mock import patch

        from django.core.management import call_command
        from ekoalu.pipeline_margin.measure import Canal

        bloque = _canal(cle="email_cold", libelle="Cold mails", besoin_demain=10,
                        besoin_marge=20, pret=1, en_validation=25)
        assert bloque.bloque_par_validation
        with patch("ekoalu.pipeline_margin.service.releve", return_value=[bloque]), \
                patch("ekoalu.pipeline_margin.service.corriger", return_value=""), \
                patch("ekoalu.notifications.hub_events.post_event"), \
                patch("ekoalu.notifications.graph_mailer.send_mail",
                      return_value=True) as mail:
            call_command("pipeline_margin", stdout=StringIO())
        assert mail.call_count == 1
        assert mail.call_args.kwargs["category"] == "alert"

    def test_pas_de_mail_quand_richard_n_est_pas_le_blocage(self):
        from io import StringIO
        from unittest.mock import patch

        from django.core.management import call_command

        a_sec = _canal(pret=0, en_validation=0, besoin_marge=20)
        with patch("ekoalu.pipeline_margin.service.releve", return_value=[a_sec]), \
                patch("ekoalu.pipeline_margin.service.corriger", return_value="ok"), \
                patch("ekoalu.notifications.hub_events.post_event"), \
                patch("ekoalu.notifications.graph_mailer.send_mail") as mail:
            call_command("pipeline_margin", stdout=StringIO())
        mail.assert_not_called()
