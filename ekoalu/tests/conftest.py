"""Fixtures partagees des tests ekoalu.

testpaths=tests dans pytest.ini -> les fixtures de tests/conftest.py ne
couvrent PAS ekoalu/tests/. Ce conftest local isole les sentinels prod (poses
par Richard en exploitation) pour que la suite ekoalu soit deterministe quel
que soit l'etat live du serveur.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_emergency_sentinel(tmp_path_factory, monkeypatch):
    """Redirige le sentinel d'arret d'urgence vers un chemin temporaire vierge.

    Sinon le flag live ``data/emergency_stop.flag`` (bouton STOP pose par
    Richard) ferait echouer les tests des commandes d'envoi. Les tests de
    test_emergency_stop.py re-redirigent via leur propre ``_isolate_sentinel``.
    """
    from ekoalu import emergency_stop

    p = tmp_path_factory.mktemp("emstop") / "emergency_stop.flag"
    monkeypatch.setattr(emergency_stop, "SENTINEL_PATH", p)
    yield
