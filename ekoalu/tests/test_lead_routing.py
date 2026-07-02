"""Tests du routage (scoping qualification par campagne d'origine) + criteres."""
from __future__ import annotations

import pytest

from ekoalu.lead_routing.config import scoped_qualification_enabled
from ekoalu.lead_routing.criteria import (
    CRITERIA_MARKER,
    build_refined_objective,
    normalize_geo,
)
from ekoalu.lead_routing.patch import apply_lead_routing_patch, record_discovery


@pytest.fixture(autouse=True)
def _routing_patch_applied():
    """Garantit que le patch est applique (idempotent)."""
    apply_lead_routing_patch()
    yield


class _Sess:
    """Stand-in minimal d'AccountSession : expose django_user + campaign."""

    def __init__(self, django_user, campaign):
        self.django_user = django_user
        self.campaign = campaign

    def ensure_browser(self):
        pass


@pytest.fixture
def session(db):
    from linkedin.models import Campaign
    from tests.factories import UserFactory

    user = UserFactory(username="routing-user")
    campaign = Campaign.objects.create(name="EKOALU - ABM - Test Boite")
    campaign.users.add(user)
    return _Sess(django_user=user, campaign=campaign)


def _make_lead(pid: str):
    from crm.models import Lead

    return Lead.objects.create(
        linkedin_url=f"https://www.linkedin.com/in/{pid}",
        public_identifier=pid,
    )


def _make_campaign(name: str, users=None):
    from linkedin.models import Campaign

    c = Campaign.objects.create(name=name)
    if users:
        c.users.add(users)
    return c


# --------------------------------------------------------------------------
# record_discovery
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_record_discovery_idempotent(session):
    from ekoalu.lead_routing.models import LeadDiscovery

    lead = _make_lead("alice-dupont")
    record_discovery(lead.pk, session.campaign)
    record_discovery(lead.pk, session.campaign)  # 2e appel = no-op

    assert LeadDiscovery.objects.filter(lead=lead, campaign=session.campaign).count() == 1


@pytest.mark.django_db
def test_record_discovery_noop_on_none(session):
    from ekoalu.lead_routing.models import LeadDiscovery

    record_discovery(None, session.campaign)
    record_discovery(123, None)
    assert LeadDiscovery.objects.count() == 0


# --------------------------------------------------------------------------
# Scoping de get_leads_for_qualification
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_qualification_scoped_to_discovering_campaign(session):
    """Un profil decouvert pour la campagne A n'est PAS qualifie pour B."""
    from linkedin.db import leads as leads_module
    from ekoalu.lead_routing.models import LeadDiscovery

    camp_a = session.campaign
    camp_b = _make_campaign("EKOALU - ABM - Autre Boite", users=session.django_user)

    lead_a = _make_lead("mine-a")
    _make_lead("orphan-x")  # decouvert par personne

    LeadDiscovery.objects.create(lead=lead_a, campaign=camp_a)

    # Campagne A : voit son lead
    ids_a = {d["lead_id"] for d in leads_module.get_leads_for_qualification(session)}
    assert lead_a.pk in ids_a

    # Campagne B : ne voit PAS le lead de A (fin du test croise)
    session.campaign = camp_b
    ids_b = {d["lead_id"] for d in leads_module.get_leads_for_qualification(session)}
    assert lead_a.pk not in ids_b


@pytest.mark.django_db
def test_unattributed_leads_excluded(session):
    """Un lead sans LeadDiscovery n'est qualifie par aucune campagne (anti-flood)."""
    from linkedin.db import leads as leads_module

    _make_lead("no-source-lead")
    ids = {d["lead_id"] for d in leads_module.get_leads_for_qualification(session)}
    assert ids == set()


@pytest.mark.django_db
def test_kill_switch_restores_full_base(session, monkeypatch):
    """Kill-switch off -> comportement d'origine (toute la base)."""
    from linkedin.db import leads as leads_module

    _make_lead("legacy-lead")
    monkeypatch.setenv("EKOALU_SCOPED_QUALIFICATION", "0")
    assert scoped_qualification_enabled() is False

    ids = {d["lead_id"] for d in leads_module.get_leads_for_qualification(session)}
    assert len(ids) >= 1  # le lead non attribue ressort quand le scoping est off


# --------------------------------------------------------------------------
# create_enriched_lead -> enregistre la decouverte
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_create_enriched_lead_records_discovery(session):
    from linkedin.db import leads as leads_module
    from ekoalu.lead_routing.models import LeadDiscovery

    profile = {
        "public_identifier": "bob-martin",
        "urn": "urn:li:fsd_profile:bob",
        "first_name": "Bob",
        "last_name": "Martin",
        "headline": "Conducteur de travaux",
    }
    pk = leads_module.create_enriched_lead(session, "https://www.linkedin.com/in/bob-martin", profile)
    assert pk is not None
    assert LeadDiscovery.objects.filter(lead_id=pk, campaign=session.campaign).exists()


@pytest.mark.django_db
def test_record_discovery_stocke_la_requete(session):
    from ekoalu.lead_routing.models import LeadDiscovery

    lead = _make_lead("carol-query")
    record_discovery(lead.pk, session.campaign, query="Dirigeant maçonnerie Savoie Isère")
    ld = LeadDiscovery.objects.get(lead=lead, campaign=session.campaign)
    assert ld.query == "Dirigeant maçonnerie Savoie Isère"


@pytest.mark.django_db
def test_create_enriched_lead_loggue_le_mot_cle_natif(session):
    """La requete native courante (posee par run_search) est journalisee."""
    from linkedin.db import leads as leads_module
    from ekoalu.lead_routing.models import LeadDiscovery

    session._ekoalu_search_keyword = "Conducteur travaux maçonnerie Lyon"
    profile = {"public_identifier": "dan-native", "urn": "urn:li:fsd_profile:dan",
               "first_name": "Dan", "last_name": "Native", "headline": "Maçon"}
    pk = leads_module.create_enriched_lead(session, "https://www.linkedin.com/in/dan-native", profile)
    ld = LeadDiscovery.objects.get(lead_id=pk, campaign=session.campaign)
    assert ld.query == "Conducteur travaux maçonnerie Lyon"


# --------------------------------------------------------------------------
# Criteres affines (builder pur)
# --------------------------------------------------------------------------

def test_normalize_geo_tolerates_typos():
    assert normalize_geo("NATIONNAL") == "national"
    assert normalize_geo("national") == "national"
    assert normalize_geo("REGIONNAL") == "regional"
    assert normalize_geo("régional") == "regional"
    assert normalize_geo("") == ""


def test_build_refined_objective_adds_block():
    out = build_refined_objective("Objectif de base.", "Orienter operationnels, pas RH", "NATIONNAL")
    assert "Objectif de base." in out
    assert CRITERIA_MARKER in out
    assert "NATIONAL" in out
    assert "operationnels" in out


def test_build_refined_objective_idempotent():
    base = "Objectif de base."
    once = build_refined_objective(base, "Crit X", "REGIONNAL")
    twice = build_refined_objective(once, "Crit X", "REGIONNAL")
    assert once == twice
    assert once.count(CRITERIA_MARKER) == 1


def test_build_refined_objective_empty_inputs_noop():
    base = "Objectif de base."
    assert build_refined_objective(base, "", "") == base


# --------------------------------------------------------------------------
# backfill_lead_sources
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_backfill_from_non_failed_deals(session):
    from io import StringIO

    from django.core.management import call_command
    from crm.models import Deal
    from linkedin.enums import ProfileState
    from ekoalu.lead_routing.models import LeadDiscovery

    lead_active = _make_lead("active-deal-lead")
    lead_failed = _make_lead("failed-only-lead")

    Deal.objects.create(lead=lead_active, campaign=session.campaign, state=ProfileState.CONNECTED.value)
    Deal.objects.create(lead=lead_failed, campaign=session.campaign, state=ProfileState.FAILED.value)

    call_command("backfill_lead_sources", stdout=StringIO())

    assert LeadDiscovery.objects.filter(lead=lead_active, campaign=session.campaign).exists()
    assert not LeadDiscovery.objects.filter(lead=lead_failed).exists()
