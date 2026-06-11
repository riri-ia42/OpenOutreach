"""Tests de l'auto-STOP sur echecs d'auth LinkedIn repetes (ekoalu/auth_watch)."""
from __future__ import annotations

import pytest

from ekoalu import auth_watch, emergency_stop


@pytest.fixture(autouse=True)
def _reset_counter(monkeypatch):
    """Compteur process-local remis a zero + mail neutralise pour chaque test."""
    auth_watch.reset()
    monkeypatch.setattr(auth_watch, "_send_alert_mail", lambda *a, **k: None)
    yield
    auth_watch.reset()


class TestSeuil:
    def test_seuil_par_defaut_1(self, monkeypatch):
        # Decision Richard 10/06 : STOP des le 1er echec (insister aggrave le
        # checkpoint LinkedIn).
        monkeypatch.delenv("EKOALU_AUTH_FAILURES_BEFORE_STOP", raising=False)
        assert auth_watch.failures_before_stop() == 1

    def test_seuil_configurable_via_env(self, monkeypatch):
        monkeypatch.setenv("EKOALU_AUTH_FAILURES_BEFORE_STOP", "3")
        assert auth_watch.failures_before_stop() == 3

    def test_seuil_plancher_a_1(self, monkeypatch):
        # 0 ou negatif n'a pas de sens : on plafonne a 1.
        monkeypatch.setenv("EKOALU_AUTH_FAILURES_BEFORE_STOP", "0")
        assert auth_watch.failures_before_stop() == 1

    def test_premier_echec_engage_stop(self):
        assert auth_watch.record_auth_failure("t1") is True
        assert emergency_stop.is_stopped()
        meta = emergency_stop.status()
        assert meta["actor"] == "auto-stop"
        assert "echecs d'authentification" in meta["reason"]

    def test_mail_envoye_au_premier_echec(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            auth_watch, "_send_alert_mail", lambda n, ctx: calls.append((n, ctx)),
        )
        auth_watch.record_auth_failure("task=0")
        assert calls == [(1, "task=0")]

    def test_seuil_3_attend_3_echecs(self, monkeypatch):
        monkeypatch.setenv("EKOALU_AUTH_FAILURES_BEFORE_STOP", "3")
        assert auth_watch.record_auth_failure("t1") is False
        assert auth_watch.record_auth_failure("t2") is False
        assert auth_watch.record_auth_failure("t3") is True
        assert emergency_stop.is_stopped()


class TestReset:
    def test_reset_remet_le_compteur_a_zero(self, monkeypatch):
        monkeypatch.setenv("EKOALU_AUTH_FAILURES_BEFORE_STOP", "2")
        auth_watch.record_auth_failure("t1")
        auth_watch.reset()  # une task a reussi entre-temps
        assert auth_watch.record_auth_failure("t2") is False
        assert not emergency_stop.is_stopped()


class TestDejaStoppe:
    def test_pas_de_re_engage_si_deja_stoppe(self, monkeypatch):
        emergency_stop.engage(reason="stop manuel Richard", actor="richard")
        calls = []
        monkeypatch.setattr(
            auth_watch, "_send_alert_mail", lambda *a, **k: calls.append(1),
        )
        for i in range(5):
            assert auth_watch.record_auth_failure(f"t{i}") is False
        # Le sentinel manuel n'est pas ecrase
        assert emergency_stop.status()["actor"] == "richard"
        assert calls == []
