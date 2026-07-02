"""Tests routage sourcing (ABM = Serper only) + rotation Serper + ramp cap lectures."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ekoalu.google_sourcing.routing import is_abm_campaign, native_search_allowed
from ekoalu.google_sourcing.service import source_campaign, update_rotation_state, SourcingResult


# --------------------------------------------------------------------------
# routing : recherche native coupée pour les ABM
# --------------------------------------------------------------------------

def test_campagne_abm_detectee_par_nom():
    c = SimpleNamespace(name="EKOALU - ABM - Léon Grosse")
    assert is_abm_campaign(c) is True


def test_campagne_globale_non_abm():
    c = SimpleNamespace(name="EKOALU - Dirigeants métalliers Rhône-Alpes", abm_link=None)
    assert is_abm_campaign(c) is False


def test_native_search_refusee_pour_abm(monkeypatch):
    monkeypatch.delenv("EKOALU_ABM_NATIVE_SEARCH", raising=False)
    c = SimpleNamespace(name="EKOALU - ABM - ROMETAL")
    assert native_search_allowed(c) is False


def test_native_search_autorisee_pour_globale(monkeypatch):
    monkeypatch.delenv("EKOALU_ABM_NATIVE_SEARCH", raising=False)
    c = SimpleNamespace(name="EKOALU - Maçons tertiaires 69", abm_link=None)
    assert native_search_allowed(c) is True


def test_kill_switch_reactive_la_native(monkeypatch):
    monkeypatch.setenv("EKOALU_ABM_NATIVE_SEARCH", "1")
    c = SimpleNamespace(name="EKOALU - ABM - ROMETAL")
    assert native_search_allowed(c) is True


@pytest.mark.django_db
def test_run_search_skip_abm(monkeypatch):
    """run_search retourne None direct pour une ABM (aucune recherche LinkedIn)."""
    monkeypatch.delenv("EKOALU_ABM_NATIVE_SEARCH", raising=False)
    from linkedin.models import Campaign
    from linkedin.pipeline.search import run_search

    camp = Campaign.objects.create(name="EKOALU - ABM - Test Native Off")
    session = SimpleNamespace(campaign=camp)
    with patch("linkedin.actions.search.search_people") as mock_search:
        assert run_search(session) is None
    mock_search.assert_not_called()


@pytest.mark.django_db
def test_run_search_pose_le_mot_cle_natif(monkeypatch):
    """run_search expose le mot-cle courant sur la session (lu par lead_routing),
    puis le nettoie apres la recherche."""
    monkeypatch.delenv("EKOALU_ABM_NATIVE_SEARCH", raising=False)
    from linkedin.models import Campaign, SearchKeyword
    from linkedin.pipeline.search import run_search

    camp = Campaign.objects.create(name="EKOALU - MACON")
    SearchKeyword.objects.create(campaign=camp, keyword="Conducteur travaux maçonnerie Lyon")
    session = SimpleNamespace(campaign=camp)

    seen = {}

    def fake_search_people(sess, keyword, page=1):
        seen["during"] = getattr(sess, "_ekoalu_search_keyword", None)

    with patch("linkedin.actions.search.search_people", side_effect=fake_search_people):
        kw = run_search(session)

    assert kw == "Conducteur travaux maçonnerie Lyon"
    assert seen["during"] == "Conducteur travaux maçonnerie Lyon"   # pose pendant
    assert session._ekoalu_search_keyword == ""                     # nettoye apres


@pytest.mark.django_db
def test_run_search_skip_secteur(monkeypatch):
    """run_search retourne None direct pour une SECTEUR (Serper only, pas de natif)."""
    monkeypatch.delenv("EKOALU_ABM_NATIVE_SEARCH", raising=False)
    from linkedin.models import Campaign
    from linkedin.pipeline.search import run_search

    camp = Campaign.objects.create(name="EKOALU - SECTEUR - Bailleurs sociaux RA")
    session = SimpleNamespace(campaign=camp)
    with patch("linkedin.actions.search.search_people") as mock_search:
        assert run_search(session) is None
    mock_search.assert_not_called()


@pytest.mark.django_db
def test_rotation_inclut_les_campagnes_secteur(monkeypatch):
    """La rotation Serper sert aussi les campagnes SECTEUR (pas que ABM)."""
    from io import StringIO
    from django.core.management import call_command
    from django.contrib.auth import get_user_model
    from linkedin.models import Campaign, LinkedInProfile

    user = get_user_model().objects.create(username="rota-test")
    prof = LinkedInProfile.objects.create(
        user=user, linkedin_username="rota", active=True)
    sect = Campaign.objects.create(
        name="EKOALU - SECTEUR - Bailleurs sociaux RA", active=True)
    sect.users.add(user)

    monkeypatch.setenv("SERPER_API_KEY", "k")
    served: list[str] = []

    def fake_source(campaign, **kw):
        served.append(campaign.name)
        return SourcingResult(campaign_name=campaign.name, queries_used=1, new_leads=0)

    with patch("ekoalu.google_sourcing.service.source_campaign",
               side_effect=fake_source):
        call_command("source_via_google_rotate", "--new-leads-target", "1",
                     "--max-queries", "9", stdout=StringIO())

    assert any("SECTEUR - Bailleurs sociaux RA" in n for n in served)


# --------------------------------------------------------------------------
# service : sourcing par campagne + état de rotation
# --------------------------------------------------------------------------

@pytest.mark.django_db
class TestSourceCampaign:
    def _campaign(self, name="EKOALU - ABM - Léon Grosse"):
        from linkedin.models import Campaign
        return Campaign.objects.create(name=name)

    @staticmethod
    def _results(*urls):
        # titre neutre "métreur" : passe le pré-filtre (domaine bâtiment)
        return [{"link": u, "title": "Métreur", "snippet": ""} for u in urls]

    def test_cree_leads_et_compte_les_nouveaux(self):
        camp = self._campaign()
        results = self._results(
            "https://www.linkedin.com/in/jean-test-1/",
            "https://www.linkedin.com/in/marie-test-2/",
        )
        with patch(
            "ekoalu.google_sourcing.client.search_linkedin_results",
            return_value=results,
        ):
            res = source_campaign(camp, max_profiles=10, query_budget=1)

        assert res.new_leads == 2
        assert res.already_known == 0
        assert res.queries_used == 1

    def test_profils_deja_connus_comptes_separement(self):
        camp = self._campaign("EKOALU - ABM - Eiffage")
        results = self._results("https://www.linkedin.com/in/deja-connu-1/")
        with patch(
            "ekoalu.google_sourcing.client.search_linkedin_results",
            return_value=results,
        ):
            r1 = source_campaign(camp, max_profiles=10, query_budget=1)
            r2 = source_campaign(camp, max_profiles=10, query_budget=1)

        assert r1.new_leads == 1
        assert r2.new_leads == 0
        assert r2.already_known == 1

    def test_budget_requetes_respecte(self):
        camp = self._campaign("EKOALU - ABM - Bouygues")
        with patch(
            "ekoalu.google_sourcing.client.search_linkedin_results",
            return_value=[],
        ) as mock_search:
            res = source_campaign(camp, max_profiles=10, query_budget=3)
        assert mock_search.call_count == 3
        assert res.queries_used == 3


@pytest.mark.django_db
class TestRotationState:
    def _campaign(self, name):
        from linkedin.models import Campaign
        return Campaign.objects.create(name=name)

    def test_epuisement_apres_2_runs_vides(self):
        camp = self._campaign("EKOALU - ABM - Epuisee")
        empty = SourcingResult(queries_used=2, new_leads=0)
        s1 = update_rotation_state(camp, empty)
        assert s1.exhausted is False
        s2 = update_rotation_state(camp, empty)
        assert s2.exhausted is True
        assert s2.consecutive_empty_runs == 2

    def test_nouveau_lead_reset_le_compteur(self):
        camp = self._campaign("EKOALU - ABM - Vivante")
        update_rotation_state(camp, SourcingResult(queries_used=1, new_leads=0))
        s = update_rotation_state(camp, SourcingResult(queries_used=1, new_leads=3))
        assert s.consecutive_empty_runs == 0
        assert s.exhausted is False
        assert s.total_new_leads == 3


# --------------------------------------------------------------------------
# read_guard : ramp du cap à date
# --------------------------------------------------------------------------

class TestReadsCapRamp:
    def test_avant_la_date_cap_env(self, monkeypatch):
        from ekoalu.read_guard import guard
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "60")
        monkeypatch.setenv("EKOALU_PROFILE_READS_CAP_RAMP", "2026-06-15:75")
        monkeypatch.setattr(guard, "_today_local", lambda: date(2026, 6, 12))
        assert guard.daily_reads_cap() == 60

    def test_a_partir_de_la_date_cap_rampe(self, monkeypatch):
        from ekoalu.read_guard import guard
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "60")
        monkeypatch.setenv("EKOALU_PROFILE_READS_CAP_RAMP", "2026-06-15:75")
        monkeypatch.setattr(guard, "_today_local", lambda: date(2026, 6, 15))
        assert guard.daily_reads_cap() == 75

    def test_plusieurs_paliers_le_dernier_atteint_gagne(self, monkeypatch):
        from ekoalu.read_guard import guard
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "40")
        monkeypatch.setenv(
            "EKOALU_PROFILE_READS_CAP_RAMP", "2026-06-15:75,2026-06-01:60",
        )
        monkeypatch.setattr(guard, "_today_local", lambda: date(2026, 6, 20))
        assert guard.daily_reads_cap() == 75

    def test_segment_invalide_ignore(self, monkeypatch):
        from ekoalu.read_guard import guard
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "60")
        monkeypatch.setenv("EKOALU_PROFILE_READS_CAP_RAMP", "n-importe-quoi")
        monkeypatch.setattr(guard, "_today_local", lambda: date(2026, 6, 16))
        assert guard.daily_reads_cap() == 60
