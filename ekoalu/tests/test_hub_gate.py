"""Interrupteur mail_suspended du hub : gating des mails de report.

Règles (capture Richard 2026-08-26) :
- category="report" + destinataire @ekoalu.com + interrupteur ON → supprimé ;
- category="alert" (STOP LinkedIn, zombie, budget) → JAMAIS supprimé ;
- category="prospect" ou destinataire externe → jamais supprimé ;
- hub injoignable → fail-open (on envoie).
"""
from __future__ import annotations

import pytest
import requests

from ekoalu.notifications import hub_gate
from ekoalu.notifications.graph_mailer import _report_gate_active


@pytest.fixture(autouse=True)
def _fresh_cache():
    hub_gate.reset_cache()
    yield
    hub_gate.reset_cache()


class _Resp:
    def __init__(self, payload, ok=True, status_code=200):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code

    def json(self):
        return self._payload


def test_report_interne_supprime_quand_interrupteur_on(monkeypatch):
    monkeypatch.setattr(hub_gate, "_hub_token", lambda: "tok")
    monkeypatch.setattr(hub_gate.requests, "get", lambda *a, **k: _Resp({"suspended": True}))
    assert _report_gate_active("report", "richard@ekoalu.com") is True


def test_report_interne_passe_quand_interrupteur_off(monkeypatch):
    monkeypatch.setattr(hub_gate, "_hub_token", lambda: "tok")
    monkeypatch.setattr(hub_gate.requests, "get", lambda *a, **k: _Resp({"suspended": False}))
    assert _report_gate_active("report", "richard@ekoalu.com") is False


def test_alerte_critique_jamais_supprimee(monkeypatch):
    monkeypatch.setattr(hub_gate, "_hub_token", lambda: "tok")
    monkeypatch.setattr(hub_gate.requests, "get", lambda *a, **k: _Resp({"suspended": True}))
    assert _report_gate_active("alert", "richard@ekoalu.com") is False


def test_cold_mail_prospect_jamais_supprime(monkeypatch):
    monkeypatch.setattr(hub_gate, "_hub_token", lambda: "tok")
    monkeypatch.setattr(hub_gate.requests, "get", lambda *a, **k: _Resp({"suspended": True}))
    # category explicite prospect, ET destinataire externe avec category défaut
    assert _report_gate_active("prospect", "richard@ekoalu.com") is False
    assert _report_gate_active("report", "prospect@entreprise.fr") is False


def test_hub_injoignable_fail_open(monkeypatch):
    monkeypatch.setattr(hub_gate, "_hub_token", lambda: "tok")

    def _boom(*a, **k):
        raise requests.ConnectionError("hub down")

    monkeypatch.setattr(hub_gate.requests, "get", _boom)
    assert _report_gate_active("report", "richard@ekoalu.com") is False


def test_cache_60s_une_seule_requete(monkeypatch):
    calls = []
    monkeypatch.setattr(hub_gate, "_hub_token", lambda: "tok")
    monkeypatch.setattr(
        hub_gate.requests, "get",
        lambda *a, **k: calls.append(1) or _Resp({"suspended": True}),
    )
    assert hub_gate.reports_suspended() is True
    assert hub_gate.reports_suspended() is True
    assert len(calls) == 1
