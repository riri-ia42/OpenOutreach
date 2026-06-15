"""Tests du cap dur lectures profil LinkedIn/jour (ekoalu/read_guard)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from datetime import datetime

from ekoalu.read_guard import guard
from ekoalu.read_guard.guard import (
    PACING_MIN_FLOOR,
    ReadCapExceededError,
    check_read_allowed,
    daily_reads_cap,
    is_cap_reached,
    is_paced_cap_reached,
    reads_budget_now,
    reads_today,
    record_read,
)
from ekoalu.read_guard.models import ProfileReadDay
from ekoalu.read_guard.patch import _wrap_profile_read, apply_read_guard_patch

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_mail(monkeypatch):
    """Neutralise le mail d'alerte (testé séparément via un spy)."""
    monkeypatch.setattr(guard, "_send_alert_mail", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _no_ramp(monkeypatch):
    """Neutralise le ramp de cap programmé à date (EKOALU_PROFILE_READS_CAP_RAMP).

    Sinon, dès qu'une date du ramp en prod est atteinte (ex. 2026-06-15:75), il
    écrase le cap que ces tests fixent via EKOALU_DAILY_PROFILE_READS_CAP et les
    rend non déterministes selon le jour d'exécution.
    """
    monkeypatch.delenv("EKOALU_PROFILE_READS_CAP_RAMP", raising=False)


class TestCompteur:
    def test_reads_today_zero_sans_ligne(self):
        assert reads_today() == 0

    def test_record_read_incremente(self):
        assert record_read("test") == 1
        assert record_read("test") == 2
        assert reads_today() == 2

    def test_ligne_d_hier_n_affecte_pas_aujourd_hui(self):
        ProfileReadDay.objects.create(
            date=date.today() - timedelta(days=1), count=999,
        )
        assert reads_today() == 0
        assert not is_cap_reached()


class TestCap:
    def test_cap_par_defaut_40(self, monkeypatch):
        # Consensus praticien < 50/j ; on prend 40 de marge (benchmark 11/06).
        monkeypatch.delenv("EKOALU_DAILY_PROFILE_READS_CAP", raising=False)
        assert daily_reads_cap() == 40

    def test_cap_configurable_via_env(self, monkeypatch):
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "25")
        assert daily_reads_cap() == 25

    def test_env_invalide_retombe_sur_defaut(self, monkeypatch):
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "abc")
        assert daily_reads_cap() == 40

    def test_check_read_allowed_sous_le_cap(self, monkeypatch):
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "3")
        record_read("test")
        check_read_allowed()  # 1/3 : ne raise pas

    def test_check_read_allowed_raise_au_cap(self, monkeypatch):
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "2")
        record_read("test")
        record_read("test")
        with pytest.raises(ReadCapExceededError):
            check_read_allowed()

    def test_is_cap_reached(self, monkeypatch):
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "2")
        assert not is_cap_reached()
        record_read("test")
        record_read("test")
        assert is_cap_reached()


class TestCadencement:
    """Etalement intra-journee (anti-burst matinal, remarque Richard 15/06).

    Plage active 07h-20h (linkedin.conf) => 13h, soit 780 min.
    """

    def test_budget_plein_si_pacing_desactive(self, monkeypatch):
        monkeypatch.setenv("EKOALU_READ_PACING", "0")
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "75")
        # Meme a 07h05, tout le cap est dispo quand le pacing est coupe
        assert reads_budget_now(datetime(2026, 6, 15, 7, 5)) == 75

    def test_budget_plancher_a_l_ouverture(self, monkeypatch):
        monkeypatch.setenv("EKOALU_READ_PACING", "1")
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "75")
        # 07h00 pile : on ne debloque que le plancher (pas 75 d'un coup)
        assert reads_budget_now(datetime(2026, 6, 15, 7, 0)) == PACING_MIN_FLOOR

    def test_budget_plein_en_fin_de_plage(self, monkeypatch):
        monkeypatch.setenv("EKOALU_READ_PACING", "1")
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "75")
        assert reads_budget_now(datetime(2026, 6, 15, 20, 0)) == 75
        assert reads_budget_now(datetime(2026, 6, 15, 23, 0)) == 75

    def test_budget_croissant_sur_la_journee(self, monkeypatch):
        monkeypatch.setenv("EKOALU_READ_PACING", "1")
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "75")
        b9 = reads_budget_now(datetime(2026, 6, 15, 9, 0))
        b13 = reads_budget_now(datetime(2026, 6, 15, 13, 30))  # ~milieu plage
        b17 = reads_budget_now(datetime(2026, 6, 15, 17, 0))
        assert PACING_MIN_FLOOR <= b9 < b13 < b17 < 75
        # milieu de plage (~6h30/13h) ~= moitie du cap
        assert 33 <= b13 <= 42

    def test_burst_matinal_bloque(self, monkeypatch):
        """Le scenario du 15/06 : 50 lectures a 07h doit etre stoppe tot."""
        monkeypatch.setenv("EKOALU_READ_PACING", "1")
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "75")
        for _ in range(PACING_MIN_FLOOR):
            record_read("get_profile")
        # A 07h05, budget = plancher ; on l'a atteint => pacing bloque
        assert is_paced_cap_reached(datetime(2026, 6, 15, 7, 5))
        # Mais le cap DUR n'est pas atteint (5/75) : ce n'est qu'une temporisation
        assert not is_cap_reached()

    def test_pacing_laisse_passer_en_apres_midi(self, monkeypatch):
        monkeypatch.setenv("EKOALU_READ_PACING", "1")
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "75")
        for _ in range(PACING_MIN_FLOOR):
            record_read("get_profile")
        # 5 lectures a 17h : budget largement > 5 => on peut continuer
        assert not is_paced_cap_reached(datetime(2026, 6, 15, 17, 0))

    def test_cap_dur_implique_pacing_atteint(self, monkeypatch):
        monkeypatch.setenv("EKOALU_READ_PACING", "1")
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "2")
        record_read("test")
        record_read("test")
        # cap dur atteint => paced atteint quelle que soit l'heure (meme fin de plage)
        assert is_paced_cap_reached(datetime(2026, 6, 15, 19, 59))


class TestAlerteMail:
    def test_mail_envoye_une_seule_fois_au_franchissement(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            guard, "_send_alert_mail", lambda count, cap: calls.append(count),
        )
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "2")
        record_read("test")   # 1/2 : pas de mail
        record_read("test")   # 2/2 : mail
        record_read("test")   # 3/2 : notified deja pose, pas de re-mail
        assert calls == [2]
        assert ProfileReadDay.objects.get(date=date.today()).notified is True


class TestPatch:
    def test_wrapper_compte_puis_appelle(self, monkeypatch):
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "10")
        seen = []

        def fake_get_profile(self, *args, **kwargs):
            seen.append(args)
            return {"ok": True}

        wrapped = _wrap_profile_read(fake_get_profile, "test")
        assert wrapped(object(), "john-doe") == {"ok": True}
        assert reads_today() == 1
        assert seen == [("john-doe",)]

    def test_wrapper_raise_au_cap_sans_appeler(self, monkeypatch):
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "1")
        record_read("test")  # cap atteint
        called = []
        wrapped = _wrap_profile_read(
            lambda self: called.append(1), "test",
        )
        with pytest.raises(ReadCapExceededError):
            wrapped(object())
        assert called == []          # l'appel reseau n'a jamais eu lieu
        assert reads_today() == 1    # la tentative bloquee n'est pas comptee

    def test_apply_patch_marque_les_methodes(self):
        apply_read_guard_patch()
        from linkedin.api.client import PlaywrightLinkedinAPI

        assert getattr(PlaywrightLinkedinAPI.get_profile, "_ekoalu_read_guard", False)
        assert getattr(
            PlaywrightLinkedinAPI.get_connection_degree, "_ekoalu_read_guard", False,
        )
        # Idempotent : re-appliquer ne double-wrappe pas
        before = PlaywrightLinkedinAPI.get_profile
        apply_read_guard_patch()
        assert PlaywrightLinkedinAPI.get_profile is before
