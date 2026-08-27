"""Tests affichage prospect : nom réel des leads mail-only (capture Richard 27/08).

Un lead mail-only porte un public_identifier synthétique (bdd-prospect-<siren>,
mailjet-hot-<slug>) : l'heuristique display_name_from_slug en déduit un
placeholder ("Bdd Prospect"). resolve_prospect_display doit alors afficher la
personne réelle depuis EmailLeadData.dirigeant.
"""
from __future__ import annotations

import pytest

from crm.models import Lead
from ekoalu.email_canal.models import EmailLeadData
from ekoalu.prospect_display import display_name_from_slug, resolve_prospect_display


def _mail_only_lead(public_id="bdd-prospect-350246039", dirigeant="Jean Clavagnier",
                    entreprise="SOFIPRE", ville="Lyon"):
    lead = Lead.objects.create(
        linkedin_url=f"https://bdd-prospect.local/siren/{public_id}",
        public_identifier=public_id,
        contact_email="j.clavagnier@sofipre.fr",
    )
    EmailLeadData.objects.create(
        lead=lead,
        source=EmailLeadData.SOURCE_BDD_PROSPECT,
        siren="350246039",
        entreprise=entreprise,
        dirigeant=dirigeant,
        ville=ville,
    )
    return lead


def test_slug_synthetique_donne_placeholder():
    # Le comportement heuristique brut (sans DB) reste inchangé — c'est lui
    # que resolve_prospect_display doit corriger.
    assert display_name_from_slug("bdd-prospect-350246039") == "Bdd Prospect"


@pytest.mark.django_db
def test_resolve_affiche_dirigeant_pour_lead_mail_only():
    _mail_only_lead()
    disp = resolve_prospect_display("bdd-prospect-350246039")
    assert disp["name"] == "Jean Clavagnier"
    assert disp["company"] == "SOFIPRE"
    assert disp["location"] == "Lyon"


@pytest.mark.django_db
def test_resolve_company_hint_prime_sur_email_data():
    # company_hint (PendingOutbound.prospect_company) reste prioritaire.
    _mail_only_lead()
    disp = resolve_prospect_display("bdd-prospect-350246039", company_hint="SOFIPRE SAS")
    assert disp["name"] == "Jean Clavagnier"
    assert disp["company"] == "SOFIPRE SAS"


@pytest.mark.django_db
def test_resolve_dirigeant_vide_garde_le_placeholder():
    _mail_only_lead(dirigeant="")
    disp = resolve_prospect_display("bdd-prospect-350246039")
    assert disp["name"] == "Bdd Prospect"
    assert disp["company"] == "SOFIPRE"


@pytest.mark.django_db
def test_resolve_slug_influence_decp():
    # Personnes du groupe d'influence : bdd-prospect-<siren>-iN
    _mail_only_lead(public_id="bdd-prospect-350246039-i2", dirigeant="Marie Perret")
    disp = resolve_prospect_display("bdd-prospect-350246039-i2")
    assert disp["name"] == "Marie Perret"


@pytest.mark.django_db
def test_resolve_slug_mailjet_hot():
    _mail_only_lead(public_id="mailjet-hot-paul-at-exemple-fr", dirigeant="Paul Martin")
    disp = resolve_prospect_display("mailjet-hot-paul-at-exemple-fr")
    assert disp["name"] == "Paul Martin"


@pytest.mark.django_db
def test_resolve_lead_mail_only_sans_email_data():
    # Lead synthétique sans EmailLeadData : pas de crash, placeholder conservé.
    Lead.objects.create(
        linkedin_url="https://bdd-prospect.local/siren/111111111",
        public_identifier="bdd-prospect-111111111",
        contact_email="x@y.fr",
    )
    disp = resolve_prospect_display("bdd-prospect-111111111")
    assert disp["name"] == "Bdd Prospect"


@pytest.mark.django_db
def test_resolve_slug_linkedin_classique_sans_requete_db():
    # Un slug LinkedIn normal ne déclenche aucun lookup EmailLeadData.
    disp = resolve_prospect_display("patrick-gomes-gcr")
    assert disp["name"] == "Patrick Gomes"
