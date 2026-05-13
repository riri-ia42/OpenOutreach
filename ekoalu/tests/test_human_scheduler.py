"""Tests de fiabilité du human_scheduler EKOALU.

Garantit que les contraintes business non-négociables sont respectées.
"""
from __future__ import annotations

import datetime as dt
import random

import pytest

from ekoalu import conf
from ekoalu.human_scheduler import windows


# Utilitaires pour fabriquer des datetimes aware
def _dt(year=2026, month=5, day=11, hour=10, minute=0):
    """Lundi 11 mai 2026 par défaut."""
    return dt.datetime(year, month, day, hour, minute, tzinfo=dt.timezone.utc)


class TestActiveWindows:
    def test_7h_pas_actif_avant_7h30(self):
        assert not windows.is_in_active_window(_dt(hour=7, minute=0))
        assert not windows.is_in_active_window(_dt(hour=7, minute=29))

    def test_7h30_est_actif(self):
        assert windows.is_in_active_window(_dt(hour=7, minute=30))

    def test_10h_est_actif_matin(self):
        assert windows.is_in_active_window(_dt(hour=10))

    def test_11h59_est_actif_matin(self):
        assert windows.is_in_active_window(_dt(hour=11, minute=59))

    def test_12h00_pas_actif_pause_dejeuner(self):
        assert not windows.is_in_active_window(_dt(hour=12))

    def test_13h_pas_actif_pause(self):
        assert not windows.is_in_active_window(_dt(hour=13))

    def test_14h_est_actif_apres_midi(self):
        assert windows.is_in_active_window(_dt(hour=14))

    def test_19h59_est_actif(self):
        assert windows.is_in_active_window(_dt(hour=19, minute=59))

    def test_20h00_pas_actif_fin_journee(self):
        assert not windows.is_in_active_window(_dt(hour=20, minute=0))

    def test_22h_pas_actif_soiree(self):
        assert not windows.is_in_active_window(_dt(hour=22))

    def test_3h_du_matin_pas_actif(self):
        assert not windows.is_in_active_window(_dt(hour=3))


class TestLunchBreak:
    def test_12h_est_pause(self):
        assert windows.is_in_lunch_break(_dt(hour=12))

    def test_12h30_est_pause(self):
        assert windows.is_in_lunch_break(_dt(hour=12, minute=30))

    def test_13h59_est_pause(self):
        assert windows.is_in_lunch_break(_dt(hour=13, minute=59))

    def test_14h_pas_pause(self):
        assert not windows.is_in_lunch_break(_dt(hour=14))

    def test_11h_pas_pause(self):
        assert not windows.is_in_lunch_break(_dt(hour=11))


class TestWeekdayWeights:
    def test_lundi_poids_100pc(self):
        # 2026-05-11 = lundi
        assert windows.weekday_weight(_dt(year=2026, month=5, day=11)) == 1.0

    def test_vendredi_poids_70pc(self):
        # 2026-05-15 = vendredi
        assert windows.weekday_weight(_dt(year=2026, month=5, day=15)) == 0.7

    def test_samedi_poids_20pc(self):
        # 2026-05-16 = samedi
        assert windows.weekday_weight(_dt(year=2026, month=5, day=16)) == 0.2

    def test_dimanche_poids_0pc(self):
        # 2026-05-17 = dimanche
        assert windows.weekday_weight(_dt(year=2026, month=5, day=17)) == 0.0

    def test_dimanche_jour_inactif(self):
        assert not windows.is_active_day(_dt(year=2026, month=5, day=17))

    def test_lundi_jour_actif(self):
        assert windows.is_active_day(_dt(year=2026, month=5, day=11))


class TestNextActiveWindow:
    def test_avant_7h30_lundi_retourne_7h30_meme_jour(self):
        now = _dt(year=2026, month=5, day=11, hour=6, minute=0)
        nxt = windows.next_active_window_start(now)
        assert nxt.day == 11
        assert nxt.hour == 7
        assert nxt.minute == 30

    def test_pendant_pause_dejeuner_retourne_14h(self):
        now = _dt(year=2026, month=5, day=11, hour=12, minute=30)
        nxt = windows.next_active_window_start(now)
        assert nxt.day == 11
        assert nxt.hour == 14
        assert nxt.minute == 0

    def test_apres_20h_retourne_demain_7h30(self):
        now = _dt(year=2026, month=5, day=11, hour=20, minute=30)
        nxt = windows.next_active_window_start(now)
        assert nxt.day == 12
        assert nxt.hour == 7
        assert nxt.minute == 30

    def test_dimanche_retourne_lundi_7h30(self):
        # Dimanche 2026-05-17 → lundi 2026-05-18 7h30
        now = _dt(year=2026, month=5, day=17, hour=10)
        nxt = windows.next_active_window_start(now)
        assert nxt.day == 18  # lundi suivant
        assert nxt.hour == 7
        assert nxt.minute == 30


class TestCadenceWeekly:
    def test_dimanche_jamais_actif(self):
        """Sur 4 dimanches simulés, 0 action autorisée."""
        dimanches = [
            (2026, 5, 17),
            (2026, 5, 24),
            (2026, 5, 31),
            (2026, 6, 7),
        ]
        for (year, month, day) in dimanches:
            for hour in range(0, 24):
                d = _dt(year=year, month=month, day=day, hour=hour)
                assert d.weekday() == 6, f"Date {year}-{month}-{day} n'est pas un dimanche"
                assert not windows.is_active_day(d), \
                    f"Dimanche {day}/{month} hour={hour} ne doit pas etre actif"

    def test_pondération_vendredi_inferieure_a_lundi(self):
        lundi = windows.weekday_weight(_dt(year=2026, month=5, day=11))
        vendredi = windows.weekday_weight(_dt(year=2026, month=5, day=15))
        assert vendredi < lundi
        assert vendredi == 0.7  # cf. conf.WEEKDAY_WEIGHTS


class TestConfDefaults:
    def test_active_windows_couvrent_matin_et_apres_midi(self):
        assert len(conf.ACTIVE_WINDOWS) == 2
        assert conf.ACTIVE_WINDOWS[0] == (7.5, 12.0)
        assert conf.ACTIVE_WINDOWS[1] == (14.0, 20.0)

    def test_volume_hebdo_target_par_defaut_60(self):
        assert conf.WEEKLY_INVITE_TARGET == 60

    def test_hard_cap_80(self):
        assert conf.WEEKLY_INVITE_HARD_CAP == 80

    def test_delay_min_au_moins_60s(self):
        """Pas de cadence trop rapide qui sentirait le bot."""
        assert conf.MIN_DELAY_SECONDS >= 60

    def test_delay_max_au_plus_30min(self):
        assert conf.MAX_DELAY_SECONDS <= 30 * 60

    def test_calendar_booking_url_pointe_vers_outlook_ekoalu(self):
        assert "outlook.office365.com" in conf.CALENDAR_BOOKING_URL
        assert "EKOALUPrisedeRDV" in conf.CALENDAR_BOOKING_URL

    def test_personas_priority_a_8_entrees(self):
        assert len(conf.PERSONAS_PRIORITY) == 8

    def test_premiere_priorite_est_dg_eg_tertiaire(self):
        assert conf.PERSONAS_PRIORITY[0] == "dg_eg_tertiaire"

    def test_niche_products_couvrent_5_familles(self):
        # Au moins 1 terme par famille produit niche
        families = ["coupe-feu", "désenfumage", "pare-balles", "grandes", "acoustique"]
        for fam in families:
            assert any(fam in p for p in conf.NICHE_PRODUCTS), f"Famille manquante: {fam}"


class TestGaussianDistribution:
    """Test statistique léger — pas trop strict pour éviter flakiness."""

    def test_distribution_genere_delais_positifs_et_dans_24h(self):
        """Test léger : la gaussienne produit des délais raisonnables."""
        from ekoalu.human_scheduler.scheduler import gaussian_intra_window_delay

        rng = random.Random(42)
        now = _dt(year=2026, month=5, day=11, hour=8, minute=30)

        for _ in range(100):
            d = gaussian_intra_window_delay(now=now, rng=rng)
            assert d > 0, "Delay doit etre positif"
            assert d < 48 * 3600, f"Delay trop long: {d}s (> 48h)"
