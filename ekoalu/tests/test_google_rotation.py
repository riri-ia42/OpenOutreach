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

    def test_profil_connu_ne_consomme_pas_le_quota(self):
        """P0 07/07 : un profil deja en base ne compte plus dans max_profiles ;
        les requetes-roles suivantes sont deroulees pour trouver du NOUVEAU."""
        from crm.models import Lead
        from ekoalu.lead_routing.models import LeadDiscovery

        camp = self._campaign("EKOALU - ABM - Vinci")
        for pid in ("deja-la-1", "deja-la-2"):
            Lead.objects.create(
                public_identifier=pid,
                linkedin_url=f"https://www.linkedin.com/in/{pid}/",
            )
        pages = [
            # requete-role 1 : Google ressert les memes profils connus
            self._results(
                "https://www.linkedin.com/in/deja-la-1/",
                "https://www.linkedin.com/in/deja-la-2/",
            ),
            # requete-role 2 : un vrai nouveau
            self._results("https://www.linkedin.com/in/tout-neuf/"),
        ]
        with patch(
            "ekoalu.google_sourcing.client.search_linkedin_results",
            side_effect=pages,
        ) as mock_search:
            res = source_campaign(camp, max_profiles=1, query_budget=9)

        assert mock_search.call_count == 2   # la requete 2 a bien tourne
        assert res.urls_found == 1
        assert res.new_leads == 1
        assert res.already_known == 2        # rattaches, hors quota
        # les connus restent rattaches a la campagne (LeadDiscovery)
        assert LeadDiscovery.objects.filter(
            campaign=camp, lead__public_identifier="deja-la-1").exists()
        assert LeadDiscovery.objects.filter(campaign=camp).count() == 3

    def test_pagination_page2_quand_page1_majoritairement_connue(self, monkeypatch):
        """Page 1 pleine et 100% connue -> on paye 1 credit pour la page 2 ;
        page 2 creuse -> pas de page 3."""
        from crm.models import Lead

        monkeypatch.delenv("EKOALU_SERPER_MAX_PAGES", raising=False)
        camp = self._campaign("EKOALU - ABM - Pagine")
        known = [f"connu-{i}" for i in range(10)]
        for pid in known:
            Lead.objects.create(
                public_identifier=pid,
                linkedin_url=f"https://www.linkedin.com/in/{pid}/",
            )
        page1 = self._results(*[f"https://www.linkedin.com/in/{p}/" for p in known])
        page2 = self._results("https://www.linkedin.com/in/nouveau-p2/")
        pages_called: list[int] = []

        def fake_search(q, num=10, page=1):
            pages_called.append(page)
            return page1 if page == 1 else page2

        with (
            patch("ekoalu.google_sourcing.client.search_linkedin_results",
                  side_effect=fake_search),
            patch("ekoalu.google_sourcing.queries.build_queries",
                  return_value=["q-unique"]),
        ):
            res = source_campaign(camp, max_profiles=5, query_budget=9)

        assert pages_called == [1, 2]
        assert res.queries_used == 2      # 1 credit par page
        assert res.new_leads == 1         # trouve en page 2
        assert res.already_known == 10

    def test_pagination_plafonnee_a_max_pages(self, monkeypatch):
        """Toutes les pages pleines et connues -> on s'arrete au plafond (3 pages
        par defaut, configurable via EKOALU_SERPER_MAX_PAGES)."""
        from crm.models import Lead

        monkeypatch.delenv("EKOALU_SERPER_MAX_PAGES", raising=False)
        camp = self._campaign("EKOALU - ABM - Plafond")
        for page in range(1, 5):
            for i in range(10):
                Lead.objects.create(
                    public_identifier=f"connu-{page}-{i}",
                    linkedin_url=f"https://www.linkedin.com/in/connu-{page}-{i}/",
                )
        pages_called: list[int] = []

        def fake_search(q, num=10, page=1):
            pages_called.append(page)
            return self._results(
                *[f"https://www.linkedin.com/in/connu-{page}-{i}/" for i in range(10)])

        with (
            patch("ekoalu.google_sourcing.client.search_linkedin_results",
                  side_effect=fake_search),
            patch("ekoalu.google_sourcing.queries.build_queries",
                  return_value=["q-unique"]),
        ):
            res = source_campaign(camp, max_profiles=5, query_budget=9)
        assert pages_called == [1, 2, 3]

        # plafond configurable
        monkeypatch.setenv("EKOALU_SERPER_MAX_PAGES", "1")
        pages_called.clear()
        with (
            patch("ekoalu.google_sourcing.client.search_linkedin_results",
                  side_effect=fake_search),
            patch("ekoalu.google_sourcing.queries.build_queries",
                  return_value=["q-unique"]),
        ):
            source_campaign(camp, max_profiles=5, query_budget=9)
        assert pages_called == [1]

    def test_pas_de_pagination_quand_page_majoritairement_nouvelle(self):
        """Une page pleine de NOUVEAUX profils ne declenche pas la page 2."""
        camp = self._campaign("EKOALU - ABM - Neuf")
        page1 = self._results(
            *[f"https://www.linkedin.com/in/neuf-{i}/" for i in range(10)])
        with (
            patch("ekoalu.google_sourcing.client.search_linkedin_results",
                  return_value=page1) as mock_search,
            patch("ekoalu.google_sourcing.queries.build_queries",
                  return_value=["q-unique"]),
        ):
            res = source_campaign(camp, max_profiles=15, query_budget=9)
        assert mock_search.call_count == 1
        assert res.new_leads == 10

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
        empty = SourcingResult(queries_used=2, new_leads=0, all_queries_run=True)
        s1 = update_rotation_state(camp, empty)
        assert s1.exhausted is False
        s2 = update_rotation_state(camp, empty)
        assert s2.exhausted is True
        assert s2.consecutive_empty_runs == 2

    def test_run_partiel_ne_compte_pas_pour_l_epuisement(self):
        """P2 07/07 : un run qui n'a pas deroule TOUTES ses requetes-roles
        (budget epuise avant la fin) ne pousse pas vers l'epuisement."""
        camp = self._campaign("EKOALU - ABM - Partielle")
        partial = SourcingResult(queries_used=1, new_leads=0, all_queries_run=False)
        for _ in range(4):
            s = update_rotation_state(camp, partial)
        assert s.exhausted is False
        assert s.consecutive_empty_runs == 0
        # un run COMPLET vide, lui, incremente (sans effacer l'historique)
        s = update_rotation_state(
            camp, SourcingResult(queries_used=9, new_leads=0, all_queries_run=True))
        assert s.consecutive_empty_runs == 1
        assert s.exhausted is False

    def test_source_campaign_flag_all_queries_run(self):
        """all_queries_run vrai quand toutes les requetes-roles ont tourne,
        faux quand le budget coupe le run avant la fin."""
        camp = self._campaign("EKOALU - ABM - Flag")
        with patch(
            "ekoalu.google_sourcing.client.search_linkedin_results",
            return_value=[],
        ):
            complete = source_campaign(camp, max_profiles=10, query_budget=20)
            partial = source_campaign(camp, max_profiles=10, query_budget=3)
        assert complete.all_queries_run is True    # 9 requetes-roles <= 20
        assert partial.all_queries_run is False    # budget 3 < 9 requetes-roles

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
