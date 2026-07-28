"""Quota email 50/j + 20 samedi + 0 ferie (decision Richard 28/07).

Tests de fiabilite metier : ce sont ces regles qui protegent la reputation du
domaine ekoalu.com et evitent la signature de bot.
"""
from __future__ import annotations

import datetime as dt

import pytest

from ekoalu import conf
from ekoalu.email_canal.quota import (
    SEND_PACING_MIN_FLOOR,
    cold_mail_quota_for,
    quota_reason,
    send_budget_now,
)
from ekoalu.human_scheduler.holidays import easter_sunday, french_holidays, is_french_holiday


class TestJoursFeries:
    def test_paques_connues(self):
        # Dates de reference (calendrier gregorien)
        assert easter_sunday(2026) == dt.date(2026, 4, 5)
        assert easter_sunday(2027) == dt.date(2027, 3, 28)
        assert easter_sunday(2024) == dt.date(2024, 3, 31)

    def test_onze_jours_feries_par_an(self):
        for year in (2026, 2027, 2028):
            assert len(french_holidays(year)) == 11, f"annee {year}"

    @pytest.mark.parametrize("day,nom", [
        (dt.date(2026, 1, 1), "Jour de l'an"),
        (dt.date(2026, 5, 1), "Fête du Travail"),
        (dt.date(2026, 5, 8), "Victoire 1945"),
        (dt.date(2026, 7, 14), "Fête nationale"),
        (dt.date(2026, 8, 15), "Assomption"),
        (dt.date(2026, 11, 11), "Armistice 1918"),
        (dt.date(2026, 12, 25), "Noël"),
        (dt.date(2026, 4, 6), "Lundi de Pâques"),      # Paques 05/04/2026
        (dt.date(2026, 5, 14), "Ascension"),           # Paques + 39
        (dt.date(2026, 5, 25), "Lundi de Pentecôte"),  # Paques + 50
    ])
    def test_feries_2026(self, day, nom):
        assert french_holidays(day.year)[day] == nom

    def test_jour_ordinaire_non_ferie(self):
        assert not is_french_holiday(dt.date(2026, 7, 28))  # un mardi ordinaire


class TestQuotaJournalier:
    def test_semaine_50(self):
        # lundi 27/07/2026 -> vendredi 31/07/2026
        for day in (dt.date(2026, 7, 27), dt.date(2026, 7, 31)):
            assert cold_mail_quota_for(day) == conf.DAILY_COLD_MAIL_TARGET == 50

    def test_samedi_20(self):
        samedi = dt.date(2026, 8, 1)
        assert samedi.weekday() == 5
        assert cold_mail_quota_for(samedi) == conf.SATURDAY_COLD_MAIL_TARGET == 20

    def test_dimanche_zero(self):
        dimanche = dt.date(2026, 8, 2)
        assert dimanche.weekday() == 6
        assert cold_mail_quota_for(dimanche) == 0

    def test_ferie_zero_meme_en_semaine(self):
        quatorze = dt.date(2026, 7, 14)   # un mardi
        assert quatorze.weekday() == 1
        assert cold_mail_quota_for(quatorze) == 0
        assert "férié" in quota_reason(quatorze)

    def test_ferie_zero_meme_un_samedi(self):
        # 15/08/2026 (Assomption) tombe un samedi : 0, pas 20.
        assomption = dt.date(2026, 8, 15)
        assert assomption.weekday() == 5
        assert cold_mail_quota_for(assomption) == 0


class TestEtalementSurLaJournee:
    """Le quota doit se debloquer progressivement : un seul creneau ne peut
    jamais partir avec les 50 mails du jour (burst = signature de bot)."""

    def _at(self, hour, minute=0, day=dt.date(2026, 7, 28)):
        return dt.datetime.combine(day, dt.time(hour, minute))

    def test_aucun_burst_au_premier_creneau(self):
        budget = send_budget_now(self._at(8, 0))
        assert budget <= 15, f"8h00 debloque deja {budget} mails = burst matinal"

    def test_budget_croissant_dans_la_journee(self):
        budgets = [send_budget_now(self._at(h)) for h in (8, 10, 15, 18)]
        assert budgets == sorted(budgets), budgets
        assert budgets[0] < budgets[-1]

    def test_quota_plein_en_fin_de_plage(self):
        assert send_budget_now(self._at(20, 30)) == 50

    def test_jamais_au_dessus_du_quota(self):
        for h in range(0, 24):
            assert send_budget_now(self._at(h)) <= 50

    def test_plancher_avant_ouverture(self):
        assert send_budget_now(self._at(5, 0)) == SEND_PACING_MIN_FLOOR

    def test_ferie_zero_a_toute_heure(self):
        for h in (8, 12, 16, 20):
            assert send_budget_now(self._at(h, day=dt.date(2026, 7, 14))) == 0

    def test_samedi_etale_sur_la_matinee_seulement(self):
        samedi = dt.date(2026, 8, 1)
        # A midi, la fenetre samedi est finie -> quota samedi entierement debloque
        assert send_budget_now(self._at(12, 30, day=samedi)) == 20
        # ... et jamais plus que 20 dans la journee
        assert send_budget_now(self._at(18, 0, day=samedi)) == 20

    def test_courbe_differente_selon_les_jours(self):
        """Le jitter journalier doit faire varier la courbe : deux jours
        ouvres consecutifs ne doivent pas debloquer exactement pareil."""
        lundi = send_budget_now(self._at(10, 0, day=dt.date(2026, 7, 27)))
        mardi = send_budget_now(self._at(10, 0, day=dt.date(2026, 7, 28)))
        mercredi = send_budget_now(self._at(10, 0, day=dt.date(2026, 7, 29)))
        assert len({lundi, mardi, mercredi}) > 1, "courbe identique = pattern regulier"
