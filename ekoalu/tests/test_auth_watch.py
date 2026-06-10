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
    def test_seuil_par_defaut_3(self, monkeypatch):
        monkeypatch.delenv("EKOALU_AUTH_FAILURES_BEFORE_STOP", raising=False)
        assert auth_watch.failures_before_stop() == 3

    def test_sous_le_seuil_pas_de_stop(self):
        assert auth_watch.record_auth_failure("t1") is False
        assert auth_watch.record_auth_failure("t2") is False
        assert not emergency_stop.is_stopped()

    def test_au_seuil_engage_emergency_stop(self):
        auth_watch.record_auth_failure("t1")
        auth_watch.record_auth_failure("t2")
        assert auth_watch.record_auth_failure("t3") is True
        assert emergency_stop.is_stopped()
        meta = emergency_stop.status()
        assert meta["actor"] == "auto-stop"
        assert "echecs d'authentification" in meta["reason"]

    def test_mail_envoye_au_declenchement(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            auth_watch, "_send_alert_mail", lambda n, ctx: calls.append((n, ctx)),
        )
        for i in range(3):
            auth_watch.record_auth_failure(f"task={i}")
        assert calls == [(3, "task=2")]


class TestReset:
    def test_reset_remet_le_compteur_a_zero(self):
        auth_watch.record_auth_failure("t1")
        auth_watch.record_auth_failure("t2")
        auth_watch.reset()  # une task a reussi entre-temps
        assert auth_watch.record_auth_failure("t3") is False
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
