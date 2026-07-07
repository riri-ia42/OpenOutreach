"""Tests du kill-switch moissonnage sidebar ("people also viewed").

P0 07/07 : chaque visite de fiche moissonnait jusqu'a 10 profils visibles sur
la page (~70 % du cap lectures/jour, leads hors-cible avec query vide).
Desormais COUPE par defaut, reactivable via EKOALU_SIDEBAR_HARVEST=1.
Le moissonnage des pages de resultats de recherche (search_people) reste actif.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from linkedin.actions.search import search_people, visit_profile

_PROFILE = {
    "url": "https://www.linkedin.com/in/cible-test/",
    "public_identifier": "cible-test",
}


def _session() -> MagicMock:
    session = MagicMock()
    session.page.url = "https://www.linkedin.com/feed/"
    return session


def test_visit_profile_ne_moissonne_plus_par_defaut(monkeypatch):
    monkeypatch.delenv("EKOALU_SIDEBAR_HARVEST", raising=False)
    with (
        patch("linkedin.actions.search._go_to_profile"),
        patch("linkedin.actions.search.extract_in_urls") as mock_extract,
        patch("linkedin.actions.search.discover_and_enrich") as mock_enrich,
    ):
        visit_profile(_session(), _PROFILE)
    mock_extract.assert_not_called()
    mock_enrich.assert_not_called()


def test_visit_profile_moissonne_avec_le_kill_switch(monkeypatch):
    monkeypatch.setenv("EKOALU_SIDEBAR_HARVEST", "1")
    with (
        patch("linkedin.actions.search._go_to_profile"),
        patch("linkedin.actions.search.extract_in_urls",
              return_value=["https://www.linkedin.com/in/voisin/"]),
        patch("linkedin.actions.search.discover_and_enrich") as mock_enrich,
    ):
        visit_profile(_session(), _PROFILE)
    mock_enrich.assert_called_once()


def test_search_people_moissonne_toujours(monkeypatch):
    """Le sourcing deliberee des pages de RESULTATS de recherche n'est PAS
    concerne par le kill-switch sidebar."""
    monkeypatch.delenv("EKOALU_SIDEBAR_HARVEST", raising=False)
    with (
        patch("linkedin.actions.search._initiate_search"),
        patch("linkedin.actions.search.extract_in_urls",
              return_value=["https://www.linkedin.com/in/resultat/"]),
        patch("linkedin.actions.search.discover_and_enrich") as mock_enrich,
    ):
        search_people(_session(), "conducteur de travaux Lyon")
    mock_enrich.assert_called_once()
