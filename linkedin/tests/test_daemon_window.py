"""Le daemon suit la fenetre EKOALU (pause dejeuner incluse) — revue 17/06 P1-5.

Avant, `seconds_until_active` gardait les bornes OpenOutreach (7-20h sans pause
midi) : les lectures de profil (declencheur du checkpoint 06/06) tournaient
pendant le dejeuner. Desormais le gating suit `is_action_allowed_now` EKOALU.
"""
from __future__ import annotations

import datetime as dt

import ekoalu.human_scheduler as hs
from linkedin import daemon


def test_actif_retourne_zero(monkeypatch):
    monkeypatch.setattr(hs, "is_action_allowed_now", lambda: True)
    assert daemon.seconds_until_active() == 0.0


def test_inactif_attend_le_prochain_creneau(monkeypatch):
    from django.utils import timezone

    monkeypatch.setattr(hs, "is_action_allowed_now", lambda: False)
    future = timezone.localtime() + dt.timedelta(seconds=3600)
    monkeypatch.setattr(hs, "next_active_slot", lambda: future)

    secs = daemon.seconds_until_active()

    assert 3500 <= secs <= 3600  # ~1h jusqu'au prochain creneau
