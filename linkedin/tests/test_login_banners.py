"""Tests des dismiss d'overlays LinkedIn (comply gate + bandeau cookies).

Pas de vrai navigateur : on simule un `page` Patchright avec des locators dont
on contrôle la visibilité. Vérifie que `dismiss_cookie_consent` clique bien
"Accept" quand le bandeau est present, et ne fait rien sinon (remarque Richard
16/06 : le bandeau cookies revenait a chaque session faute de clic).
"""
from __future__ import annotations

import pytest

from linkedin.browser.login import dismiss_cookie_consent


class _FakeTimeoutError(Exception):
    pass


@pytest.fixture(autouse=True)
def _patch_timeout(monkeypatch):
    """Remplace l'import paresseux de patchright.TimeoutError par le notre."""
    import types

    fake_mod = types.SimpleNamespace(TimeoutError=_FakeTimeoutError)
    monkeypatch.setitem(
        __import__("sys").modules, "patchright.sync_api", fake_mod,
    )


class _FakeLocator:
    def __init__(self, visible: bool, clicks: list):
        self._visible = visible
        self._clicks = clicks

    @property
    def first(self):
        return self

    def wait_for(self, state="visible", timeout=0):
        if not self._visible:
            raise _FakeTimeoutError("not visible")

    def click(self):
        self._clicks.append(True)


class _FakePage:
    """Renvoie un locator visible uniquement si le bandeau est present."""

    def __init__(self, banner_present: bool):
        self.banner_present = banner_present
        self.clicks: list = []

    def locator(self, *a, **k):
        return _FakeLocator(self.banner_present, self.clicks)

    def get_by_role(self, *a, **k):
        return _FakeLocator(self.banner_present, self.clicks)


def test_clique_accept_si_banniere_presente():
    page = _FakePage(banner_present=True)
    assert dismiss_cookie_consent(page, timeout_ms=1) is True
    assert page.clicks == [True]  # un seul clic


def test_ne_fait_rien_si_pas_de_banniere():
    page = _FakePage(banner_present=False)
    assert dismiss_cookie_consent(page, timeout_ms=1) is False
    assert page.clicks == []
