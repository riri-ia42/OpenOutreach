"""Tests fiabilité du module decp_import + command import_decp_cibles + priorisation pool."""
from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command

from ekoalu.decp_import import (
    CONTACT_EMAIL_SOURCE_DECP,
    REJECT_NO_MARCHE,
    REJECT_NO_SIREN,
    build_marche_contexte,
    is_eligible,
    parse_cible,
)


# --- Builders ----------------------------------------------------------------


def _marche(date="2026-07-09", critere="métallerie", montant=68451.0, lieu="69123",
            objet="Lot 06 métallerie serrurerie — extension EAJE", zone="aura"):
    return {"date": date, "critere": critere, "montant": montant, "lieu": lieu,
            "objet": objet, "zone": zone}


def _raw(email="contact@denjean.fr", siren="300820354", entreprise="ETS DENJEAN",
         code_naf="43.32B", cible_prioritaire=True, fabricant=False,
         produit_cible="classique_ra", in_base=False, marches=None, **extra):
    d = {
        "email": email, "siren": siren, "entreprise": entreprise,
        "code_naf": code_naf, "dirigeant": "Paul Denjean",
        "email_dirigeant": "paul.denjean@denjean.fr",
        "cible_prioritaire": cible_prioritaire, "fabricant": fabricant,
        "produit_cible": produit_cible, "in_base": in_base,
        "marches": [_marche()] if marches is None else marches,
    }
    d.update(extra)
    return d


# --- parse_cible / is_eligible ----------------------------------------------


def test_parse_cible_nominal():
    c = parse_cible(_raw())
    assert c.email == "contact@denjean.fr"
    assert c.siren == "300820354"
    assert c.cible_prioritaire is True
    assert c.fabricant is False
    assert len(c.marches) == 1
    assert is_eligible(c) is None


def test_parse_cible_sans_email_retourne_none():
    assert parse_cible(_raw(email="")) is None
    assert parse_cible(_raw(email="pas-un-email")) is None


def test_eligibilite_rejette_sans_siren_ou_sans_marche():
    assert is_eligible(parse_cible(_raw(siren=""))) == REJECT_NO_SIREN
    assert is_eligible(parse_cible(_raw(marches=[]))) == REJECT_NO_MARCHE


def test_email_generique_accepte():
    """Contrairement à bdd_prospect, contact@ est un point d'entrée valable :
    le titulaire vient de gagner un lot, on le contacte quand même."""
    c = parse_cible(_raw(email="contact@entreprise.fr"))
    assert is_eligible(c) is None


# --- build_marche_contexte ---------------------------------------------------


def test_contexte_contient_marche_et_consigne_sobre():
    ctx = build_marche_contexte(_raw())
    assert "marchés publics" in ctx
    assert "métallerie" in ctx
    assert "68 451 EUR" in ctx
    assert "flatterie" in ctx  # consigne anti-félicitations (jumeau numérique)


def test_contexte_prioritaire_mentionne_non_fabricant():
    assert "ne FABRIQUE pas" in build_marche_contexte(_raw(cible_prioritaire=True))
    assert "ne FABRIQUE pas" not in build_marche_contexte(_raw(cible_prioritaire=False))


def test_contexte_vide_sans_marches():
    assert build_marche_contexte(None) == ""
    assert build_marche_contexte({"marches": []}) == ""


def test_contexte_limite_aux_deux_marches_les_plus_recents():
    marches = [_marche(date=f"2026-0{m}-01", objet=f"Objet {m}") for m in (1, 2, 3)]
    ctx = build_marche_contexte(_raw(marches=marches))
    assert "Objet 3" in ctx and "Objet 2" in ctx
    assert "Objet 1" not in ctx


def test_contexte_montant_absent():
    ctx = build_marche_contexte(_raw(marches=[_marche(montant=None)]))
    assert "montant non publié" in ctx


# --- Command import_decp_cibles ---------------------------------------------


def _write_source(tmp_path, cibles):
    p = tmp_path / "decp-cibles-prospection.json"
    p.write_text(json.dumps({"genere_le": "2026-07-28", "cibles": cibles}),
                 encoding="utf-8")
    return p


@pytest.mark.django_db
def test_import_cree_lead_et_email_data(tmp_path):
    from crm.models import Lead
    from ekoalu.email_canal.models import EmailLeadData

    src = _write_source(tmp_path, [_raw()])
    out = StringIO()
    call_command("import_decp_cibles", "--source", str(src), stdout=out)

    lead = Lead.objects.get(contact_email="contact@denjean.fr")
    assert lead.public_identifier == "bdd-prospect-300820354"
    assert lead.contact_email_source == CONTACT_EMAIL_SOURCE_DECP
    data = EmailLeadData.objects.get(lead=lead)
    assert data.source == EmailLeadData.SOURCE_DECP
    assert data.raw_json["cible_prioritaire"] is True
    assert data.raw_json["marches"]


@pytest.mark.django_db
def test_import_idempotent_par_siren_et_email(tmp_path):
    from crm.models import Lead

    src = _write_source(tmp_path, [_raw()])
    call_command("import_decp_cibles", "--source", str(src), stdout=StringIO())
    # Rejeu identique + même siren avec autre email → 0 création
    src2 = _write_source(tmp_path, [_raw(), _raw(email="autre@denjean.fr")])
    out = StringIO()
    call_command("import_decp_cibles", "--source", str(src2), stdout=out)
    assert Lead.objects.count() == 1
    assert "créés              : 0" in out.getvalue()


@pytest.mark.django_db
def test_dry_run_ne_cree_rien(tmp_path):
    from crm.models import Lead

    src = _write_source(tmp_path, [_raw()])
    call_command("import_decp_cibles", "--source", str(src), "--dry-run",
                 stdout=StringIO())
    assert Lead.objects.count() == 0


# --- Priorisation du vivier --------------------------------------------------


@pytest.mark.django_db
def test_pool_place_cibles_prioritaires_decp_en_tete(tmp_path):
    from ekoalu.email_canal.pool import cold_mail_candidates

    # 1) un lead bdd_prospect « ancien » (inséré en premier = tête FIFO normale)
    from crm.models import Lead
    from ekoalu.email_canal.models import EmailLeadData

    old = Lead.objects.create(
        linkedin_url="https://bdd-prospect.local/siren/111111111",
        public_identifier="bdd-prospect-111111111",
        contact_email="vieux@lead.fr",
    )
    EmailLeadData.objects.create(lead=old, source=EmailLeadData.SOURCE_BDD_PROSPECT,
                                 siren="111111111")

    # 2) import DECP : une cible prioritaire + une non prioritaire
    src = _write_source(tmp_path, [
        _raw(email="nonprio@x.fr", siren="222222222", cible_prioritaire=False),
        _raw(email="prio@x.fr", siren="333333333", cible_prioritaire=True),
    ])
    call_command("import_decp_cibles", "--source", str(src), stdout=StringIO())

    candidates, _ = cold_mail_candidates()
    emails = [c.contact_email for c in candidates]
    assert emails[0] == "prio@x.fr"  # cible prioritaire DECP devant tout le monde
    assert set(emails[1:]) == {"vieux@lead.fr", "nonprio@x.fr"}
