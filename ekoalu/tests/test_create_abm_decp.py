"""Tests de la règle cible prioritaire → campagne ABM LinkedIn (create_abm_decp)."""
from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from ekoalu.management.commands.create_abm_decp import abm_campaign_name, build_objective


def test_abm_campaign_name_normalise():
    assert abm_campaign_name("ETS DENJEAN (ETABLISSEMENTS DENJEAN)") == "EKOALU - ABM - ETS DENJEAN"
    assert abm_campaign_name("  CVI  ") == "EKOALU - ABM - CVI"
    assert len(abm_campaign_name("X" * 300)) == 200


@pytest.fixture
def decp_company(db):
    from crm.models import Lead
    from ekoalu.email_canal.models import EmailLeadData

    lead = Lead.objects.create(
        linkedin_url="https://bdd-prospect.local/siren/300820354",
        public_identifier="bdd-prospect-300820354",
        contact_email="contact@denjean.fr",
        contact_email_source="decp",
    )
    return EmailLeadData.objects.create(
        lead=lead, source=EmailLeadData.SOURCE_DECP, siren="300820354",
        entreprise="ETS DENJEAN", code_naf="43.32B", ville="CHAPONOST",
        raw_json={"cible_prioritaire": True,
                  "marches": [{"objet": "Lot 06 métallerie", "critere": "métallerie",
                               "date": "2026-07-09", "montant": 68451, "lieu": "69123"}]},
    )


@pytest.fixture
def template_campaign(db):
    from linkedin.models import Campaign

    return Campaign.objects.create(
        name="EKOALU - ABM - Modele", active=True,
        booking_link="https://cal.example/rdv", product_docs="docs produit",
        action_fraction=0.3,
    )


def test_build_objective_contient_marche_et_non_fabricant(decp_company):
    obj = build_objective(decp_company)
    assert "métallerie" in obj
    assert "NON-fabricant" in obj
    assert "coupe-feu" in obj


@pytest.mark.django_db
def test_command_cree_campagne_abm_clonee(decp_company, template_campaign):
    from linkedin.models import Campaign

    call_command("create_abm_decp", stdout=StringIO())
    c = Campaign.objects.get(name="EKOALU - ABM - ETS DENJEAN")
    assert c.active is True
    assert c.booking_link == template_campaign.booking_link
    assert c.product_docs == template_campaign.product_docs
    assert c.action_fraction == template_campaign.action_fraction
    assert "marchés publics" in c.campaign_objective
    # Idempotence : rejeu → rien de nouveau
    call_command("create_abm_decp", stdout=StringIO())
    assert Campaign.objects.filter(name__startswith="EKOALU - ABM - ETS DENJEAN").count() == 1


@pytest.mark.django_db
def test_command_ignore_non_prioritaires_sans_flag(template_campaign):
    from crm.models import Lead
    from ekoalu.email_canal.models import EmailLeadData
    from linkedin.models import Campaign

    lead = Lead.objects.create(
        linkedin_url="https://bdd-prospect.local/siren/111111111",
        public_identifier="bdd-prospect-111111111",
        contact_email="contact@fab.fr", contact_email_source="decp",
    )
    EmailLeadData.objects.create(
        lead=lead, source=EmailLeadData.SOURCE_DECP, siren="111111111",
        entreprise="FABRICANT X", raw_json={"cible_prioritaire": False, "marches": []},
    )
    call_command("create_abm_decp", stdout=StringIO())
    assert not Campaign.objects.filter(name="EKOALU - ABM - FABRICANT X").exists()
    call_command("create_abm_decp", "--all-decp", stdout=StringIO())
    assert Campaign.objects.filter(name="EKOALU - ABM - FABRICANT X").exists()


@pytest.mark.django_db
def test_dry_run_ne_cree_rien(decp_company, template_campaign):
    from linkedin.models import Campaign

    call_command("create_abm_decp", "--dry-run", stdout=StringIO())
    assert not Campaign.objects.filter(name="EKOALU - ABM - ETS DENJEAN").exists()
