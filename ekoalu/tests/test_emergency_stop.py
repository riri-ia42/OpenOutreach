"""Tests arret d'urgence : sentinel + vue toggle + guard commandes d'envoi."""
from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse


@pytest.fixture(autouse=True)
def _isolate_sentinel(tmp_path, monkeypatch):
    """Redirige le sentinel vers tmp_path pour isoler chaque test."""
    from ekoalu import emergency_stop

    p = tmp_path / "emergency_stop.flag"
    monkeypatch.setattr(emergency_stop, "SENTINEL_PATH", p)
    yield p
    if p.exists():
        p.unlink()


@pytest.fixture
def staff_client(db):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    User.objects.create_user(
        username="estop_admin", password="pwd12345", is_staff=True,
    )
    c = Client()
    c.login(username="estop_admin", password="pwd12345")
    return c


class TestSentinelModule:
    def test_pas_de_sentinel_pas_de_stop(self, _isolate_sentinel):
        from ekoalu.emergency_stop import is_stopped, status

        assert is_stopped() is False
        assert status() is None

    def test_engage_cree_sentinel_avec_metadata(self, _isolate_sentinel):
        from ekoalu.emergency_stop import engage, is_stopped, status

        engage(reason="daemon emballe", actor="richard")
        assert is_stopped() is True
        assert _isolate_sentinel.exists()

        meta = status()
        assert meta["reason"] == "daemon emballe"
        assert meta["actor"] == "richard"
        assert meta["engaged_at_local"]

    def test_release_supprime_sentinel_idempotent(self, _isolate_sentinel):
        from ekoalu.emergency_stop import engage, is_stopped, release

        engage(reason="x")
        assert is_stopped() is True

        assert release() is True
        assert is_stopped() is False
        assert release() is False  # 2e fois = rien a lever

    def test_status_metadata_illisible_ne_crashe_pas(self, _isolate_sentinel):
        from ekoalu.emergency_stop import status

        _isolate_sentinel.write_text("pas du json", encoding="utf-8")
        meta = status()
        assert meta is not None
        assert "illisible" in meta["reason"]


@pytest.mark.django_db
class TestEmergencyStopView:
    def test_engage_via_post(self, staff_client, _isolate_sentinel):
        from ekoalu.emergency_stop import is_stopped

        r = staff_client.post(
            reverse("ekoalu:emergency_stop"),
            {"action": "engage", "reason": "test"},
        )
        assert r.status_code == 302
        assert is_stopped() is True

    def test_release_via_post(self, staff_client, _isolate_sentinel):
        from ekoalu.emergency_stop import engage, is_stopped

        engage(reason="test")
        r = staff_client.post(
            reverse("ekoalu:emergency_stop"), {"action": "release"},
        )
        assert r.status_code == 302
        assert is_stopped() is False

    def test_get_refuse(self, staff_client, _isolate_sentinel):
        # require_POST -> 405 sur GET
        r = staff_client.get(reverse("ekoalu:emergency_stop"))
        assert r.status_code == 405

    def test_non_staff_redirige(self, _isolate_sentinel, db):
        c = Client()
        r = c.post(reverse("ekoalu:emergency_stop"), {"action": "engage"})
        assert r.status_code in (301, 302)  # redirect login admin
        from ekoalu.emergency_stop import is_stopped
        assert is_stopped() is False


@pytest.mark.django_db
class TestSendCommandsHonorStop:
    def test_send_approved_emails_bloque_si_stop(self, _isolate_sentinel):
        """Avec le sentinel actif, la commande ne touche pas au sender."""
        from unittest.mock import patch

        from django.core.management import call_command

        from ekoalu.emergency_stop import engage

        engage(reason="test")
        with patch(
            "ekoalu.email_canal.sender.send_cold_email",
        ) as mock_send:
            call_command("send_approved_emails", "--max", "5")
        mock_send.assert_not_called()
