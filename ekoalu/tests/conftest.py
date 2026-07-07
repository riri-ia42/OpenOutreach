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


@pytest.fixture(autouse=True)
def _neutralize_daily_humanisation(monkeypatch):
    """Neutralise les facteurs d'humanisation quotidiens (LOT E) par defaut.

    Poids hebdo = 1.0 et jitter journalier = 1.0 quel que soit le jour ou
    tourne la suite — sinon les tests de caps (read_guard, sender) donneraient
    des resultats differents un samedi (x0.2) ou un dimanche (x0), et le
    jitter fausserait les asserts exacts. Jours off aleatoires desactives
    (kill-switch env) — sinon la suite echouerait les jours off tires pour le
    mois courant. Les tests dedies (test_daily_budget.py) importent les
    fonctions reelles directement et re-activent l'env explicitement.
    """
    from ekoalu.human_scheduler import budget

    monkeypatch.setattr(budget, "daily_weight_factor", lambda d=None: 1.0)
    monkeypatch.setattr(budget, "daily_jitter_factor", lambda d=None: 1.0)
    monkeypatch.setenv("EKOALU_RANDOM_DAYS_OFF", "0")
    yield


@pytest.fixture(autouse=True)
def _isolate_apify_env(monkeypatch):
    """Purge l'env Apify : la suite doit etre deterministe que le token prod
    soit present ou non dans le shell (sinon le chemin Apify-first de
    _embed_urlonly_leads s'activerait dans les tests Voyager historiques).
    Les tests Apify posent explicitement leur env."""
    monkeypatch.delenv("EKOALU_APIFY_TOKEN", raising=False)
    monkeypatch.delenv("EKOALU_APIFY_ACTOR", raising=False)
    monkeypatch.delenv("EKOALU_APIFY_ENRICH", raising=False)
    monkeypatch.delenv("EKOALU_APIFY_DAILY_CAP", raising=False)
    yield


@pytest.fixture(autouse=True)
def _isolate_shared_exclusions(tmp_path_factory, monkeypatch):
    """Pointe la liste d'exclusion partagee vers un fichier absent + purge le
    cache TTL — sinon les tests liraient le VRAI _partage/exclusions.json
    (~2000 emails prod, non deterministe)."""
    from ekoalu import shared_exclusions

    p = tmp_path_factory.mktemp("exclusions") / "exclusions.json"
    monkeypatch.setenv(shared_exclusions.ENV_VAR, str(p))
    shared_exclusions._cache = None
    yield
    shared_exclusions._cache = None
