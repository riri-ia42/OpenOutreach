"""Tests du cap dur lectures profil LinkedIn/jour (ekoalu/read_guard)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from ekoalu.read_guard import guard
from ekoalu.read_guard.guard import (
    ReadCapExceededError,
    check_read_allowed,
    daily_reads_cap,
    is_cap_reached,
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
    def test_cap_par_defaut_60(self, monkeypatch):
        monkeypatch.delenv("EKOALU_DAILY_PROFILE_READS_CAP", raising=False)
        assert daily_reads_cap() == 60

    def test_cap_configurable_via_env(self, monkeypatch):
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "25")
        assert daily_reads_cap() == 25

    def test_env_invalide_retombe_sur_defaut(self, monkeypatch):
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "abc")
        assert daily_reads_cap() == 60

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
