"""Frappe humaine variable par caractere (revue 17/06 P1-6).

Avant : `locator.type(text, delay=X)` tirait UN delai pour toute la chaine et
message.py forcait 10-50 ms/char (1200-6000 c/min, surhumain). Desormais : 1
frappe + 1 delai PAR caractere, dans la cadence cible 200-400 c/min.
"""
from __future__ import annotations

from linkedin.browser import nav


class _FakeLocator:
    def __init__(self):
        self.typed: list[str] = []

    def type(self, text, **kw):
        self.typed.append(text)


def test_frappe_caractere_par_caractere(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(nav.time, "sleep", lambda s: sleeps.append(s))

    loc = _FakeLocator()
    nav.human_type(loc, "abcde")

    assert loc.typed == ["a", "b", "c", "d", "e"]  # 1 frappe / caractere
    assert len(sleeps) == 5                          # 1 delai / caractere
    for s in sleeps:                                  # cadence 200-400 c/min
        assert 0.150 <= s <= 0.300


def test_delais_variables(monkeypatch):
    """Le rythme doit etre IRREGULIER (pas un delai constant = signature bot)."""
    sleeps: list[float] = []
    monkeypatch.setattr(nav.time, "sleep", lambda s: sleeps.append(s))

    nav.human_type(_FakeLocator(), "x" * 60)

    assert len(set(sleeps)) > 1


def test_bornes_explicites_respectees(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(nav.time, "sleep", lambda s: sleeps.append(s))

    nav.human_type(_FakeLocator(), "abc", min_delay=200, max_delay=200)

    assert sleeps == [0.200, 0.200, 0.200]
