"""Tests fiabilité du groupe d'influence (influence_enrich + command)."""
from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from ekoalu.influence_enrich import (
    SOURCE_PATTERN,
    SOURCE_SITE,
    apply_pattern,
    build_influence_group,
    detect_pattern,
    extract_domain_emails,
    is_company_domain,
    is_generic_local,
    local_to_person_name,
    match_dirigeant,
    nominative_people_from_emails,
)


# --- Extraction / filtres ----------------------------------------------------


def test_extract_domain_emails_filtre_le_domaine():
    html = "a: jean.dupont@acme.fr b: contact@autre.fr c: J.MARTIN@ACME.FR"
    assert extract_domain_emails(html, "acme.fr") == {"jean.dupont@acme.fr", "j.martin@acme.fr"}


def test_generic_et_junk_locals_rejetes():
    for local in ("contact", "info", "compta", "devis", "recrutement", "noreply", "contact1"):
        assert is_generic_local(local), local
    assert not is_generic_local("jean.dupont")
    assert not is_generic_local("j-martin")


def test_local_to_person_name():
    assert local_to_person_name("jean.dupont") == "Jean Dupont"
    assert local_to_person_name("j.dupont") == "J. Dupont"
    assert local_to_person_name("marie-claire.petit") == "Marie Claire Petit"
    assert local_to_person_name("jdupont") == ""  # ambigu sans séparateur
    assert local_to_person_name("devis69") == ""


def test_match_dirigeant_sans_separateur():
    dirs = [("Jean", "Dupont")]
    assert match_dirigeant("jdupont", dirs) == "Jean Dupont"
    assert match_dirigeant("jeandupont", dirs) == "Jean Dupont"
    assert match_dirigeant("dupont", dirs) == "Jean Dupont"
    assert match_dirigeant("pmartin", dirs) == ""


def test_nominative_people_ecarte_les_alias():
    emails = {"jean.dupont@acme.fr", "contact@acme.fr", "xyz123@acme.fr"}
    people = nominative_people_from_emails(emails, [], SOURCE_SITE)
    assert [p.email for p in people] == ["jean.dupont@acme.fr"]
    assert people[0].display_name == "Jean Dupont"


# --- Pattern -----------------------------------------------------------------


def test_detect_pattern_et_apply():
    assert detect_pattern(["jean.dupont", "marie.petit"]) == "prenom.nom"
    assert detect_pattern(["j.dupont"]) == "p.nom"
    assert detect_pattern([]) == "prenom.nom"  # défaut
    assert apply_pattern("prenom.nom", "Émile", "De Sèze") == "emile.deseze"
    assert apply_pattern("p.nom", "Jean", "Dupont") == "j.dupont"


def test_is_company_domain():
    assert is_company_domain("acme.fr")
    assert not is_company_domain("gmail.com")
    assert not is_company_domain("")


# --- Assemblage du groupe ----------------------------------------------------


def test_build_influence_group_priorise_site_puis_pattern():
    group = build_influence_group(
        domain="acme.fr",
        site_emails={"jean.dupont@acme.fr"},
        google_emails=set(),
        dirigeants=[("Jean", "Dupont"), ("Paul", "Martin")],
        existing_emails={"contact@acme.fr"},
        max_persons=3,
    )
    emails = [p.email for p in group]
    # Jean Dupont trouvé sur le site (pas de doublon candidat), Paul Martin en pattern
    assert emails == ["jean.dupont@acme.fr", "paul.martin@acme.fr"]
    assert group[0].source == SOURCE_SITE
    assert group[1].source == SOURCE_PATTERN
    assert group[1].role == "dirigeant"


def test_build_influence_group_respecte_le_cap_et_les_existants():
    group = build_influence_group(
        domain="acme.fr",
        site_emails={"a.un@acme.fr", "b.deux@acme.fr", "c.trois@acme.fr", "d.quatre@acme.fr"},
        google_emails=set(),
        dirigeants=[],
        existing_emails={"a.un@acme.fr"},
        max_persons=2,
    )
    assert len(group) == 2
    assert "a.un@acme.fr" not in [p.email for p in group]


# --- Command (réseau mocké) --------------------------------------------------


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
        entreprise="ETS DENJEAN", code_naf="43.32B",
        raw_json={"cible_prioritaire": True, "marches": [{"objet": "Lot métallerie",
                  "critere": "métallerie", "montant": 68451, "date": "2026-07-09",
                  "lieu": "69123"}]},
    )


@pytest.mark.django_db
def test_command_cree_les_leads_personnes(decp_company, monkeypatch):
    from crm.models import Lead
    from ekoalu.email_canal.models import EmailLeadData
    from ekoalu.management.commands import enrich_influence_decp as cmd

    monkeypatch.setattr(cmd, "fetch_site_emails",
                        lambda domain: {"jean.dupont@denjean.fr"})
    monkeypatch.setattr(cmd, "fetch_google_emails", lambda domain: set())
    monkeypatch.setattr(cmd, "fetch_dirigeants",
                        lambda siren: [("Paul", "Denjean")])

    call_command("enrich_influence_decp", stdout=StringIO())

    people = EmailLeadData.objects.filter(source=EmailLeadData.SOURCE_DECP_INFLUENCE)
    assert people.count() == 2
    emails = {d.lead.contact_email for d in people}
    assert emails == {"jean.dupont@denjean.fr", "paul.denjean@denjean.fr"}
    for d in people:
        # héritage : priorité + contexte marché + nom de la personne
        assert d.raw_json["cible_prioritaire"] is True
        assert d.raw_json["marches"]
        assert d.dirigeant
        assert d.lead.public_identifier.startswith("bdd-prospect-300820354-i")
    # idempotence : rejeu → entreprise déjà faite, 0 création
    call_command("enrich_influence_decp", stdout=StringIO())
    assert EmailLeadData.objects.filter(
        source=EmailLeadData.SOURCE_DECP_INFLUENCE).count() == 2
    assert Lead.objects.count() == 3  # 1 entreprise + 2 personnes


@pytest.mark.django_db
def test_personnes_influence_prioritaires_dans_le_vivier(decp_company, monkeypatch):
    from ekoalu.email_canal.pool import cold_mail_candidates
    from ekoalu.management.commands import enrich_influence_decp as cmd

    monkeypatch.setattr(cmd, "fetch_site_emails",
                        lambda domain: {"jean.dupont@denjean.fr"})
    monkeypatch.setattr(cmd, "fetch_google_emails", lambda domain: set())
    monkeypatch.setattr(cmd, "fetch_dirigeants", lambda siren: [])
    call_command("enrich_influence_decp", stdout=StringIO())

    candidates, _ = cold_mail_candidates()
    # entreprise + personne, toutes deux en groupe prioritaire (ordre FIFO interne)
    assert {c.contact_email for c in candidates} == {"contact@denjean.fr",
                                                     "jean.dupont@denjean.fr"}


@pytest.mark.django_db
def test_command_skip_domaine_webmail(monkeypatch):
    from crm.models import Lead
    from ekoalu.email_canal.models import EmailLeadData

    lead = Lead.objects.create(
        linkedin_url="https://bdd-prospect.local/siren/111111111",
        public_identifier="bdd-prospect-111111111",
        contact_email="entreprise@gmail.com",
        contact_email_source="decp",
    )
    EmailLeadData.objects.create(lead=lead, source=EmailLeadData.SOURCE_DECP,
                                 siren="111111111",
                                 raw_json={"cible_prioritaire": True})
    out = StringIO()
    call_command("enrich_influence_decp", stdout=out)
    assert "skip domaine webmail/absent  : 1" in out.getvalue()
    assert EmailLeadData.objects.filter(
        source=EmailLeadData.SOURCE_DECP_INFLUENCE).count() == 0
