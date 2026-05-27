"""Tests fiabilité du module bdd_prospect_import + management command."""
from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command

from ekoalu.bdd_prospect_import import (
    CONTACT_EMAIL_SOURCE,
    NAF_EXCLUS,
    NAF_P1,
    NAF_P2,
    REJECT_EFFECTIF_TOO_SMALL,
    REJECT_EMAIL_B2C,
    REJECT_EMAIL_GENERIC,
    REJECT_NAF_EXCLUDED,
    REJECT_NAF_NOT_TARGET,
    REJECT_NO_DIRIGEANT,
    REJECT_NO_SIREN,
    EligibilityFilters,
    SYNTHETIC_PUBLIC_ID_PREFIX,
    SYNTHETIC_URL_PREFIX,
    is_eligible,
    is_synthetic_lead_url,
    iter_eligible,
    make_synthetic_linkedin_url,
    make_synthetic_public_identifier,
    parse_contact,
)


# --- Builders fixture --------------------------------------------------------


def _raw(email="dupont@acme-bat.com", siren="123456789", code_naf="41.20B",
         dirigeant="JEAN DUPONT", entreprise="ACME BAT",
         effectif_min=10, effectif_max=49, ville="LYON", cp="69001"):
    return {
        "email": email,
        "properties": {
            "siren": siren,
            "code_naf": code_naf,
            "dirigeant": dirigeant,
            "entreprise": entreprise,
            "effectif_min": str(effectif_min),
            "effectif_max": str(effectif_max),
            "ville": ville,
            "cp": cp,
            "dpt": cp[:2],
            "activite": "ENTREPRISES DE MENUISERIE",
        },
    }


# --- parse_contact -----------------------------------------------------------


class TestParseContact:
    def test_parse_complet(self):
        c = parse_contact(_raw())
        assert c.email == "dupont@acme-bat.com"
        assert c.siren == "123456789"
        assert c.code_naf == "41.20B"
        assert c.effectif_min == 10
        assert c.effectif_max == 49

    def test_email_lowercased(self):
        c = parse_contact(_raw(email="JEAN.DUPONT@Acme.COM"))
        assert c.email == "jean.dupont@acme.com"

    def test_code_naf_uppercased(self):
        c = parse_contact(_raw(code_naf="41.20b"))
        assert c.code_naf == "41.20B"

    def test_email_manquant_retourne_none(self):
        assert parse_contact({"email": "", "properties": {}}) is None
        assert parse_contact({"properties": {}}) is None

    def test_effectif_string_ou_int(self):
        c1 = parse_contact(_raw(effectif_min="20"))
        c2 = parse_contact({**_raw(), "properties": {**_raw()["properties"], "effectif_min": 20}})
        assert c1.effectif_min == 20
        assert c2.effectif_min == 20

    def test_effectif_vide_donne_zero(self):
        raw = _raw()
        raw["properties"]["effectif_min"] = ""
        raw["properties"]["effectif_max"] = None
        c = parse_contact(raw)
        assert c.effectif_min == 0
        assert c.effectif_max == 0

    def test_properties_absent(self):
        c = parse_contact({"email": "x@y.fr"})
        assert c.email == "x@y.fr"
        assert c.siren == ""
        assert c.code_naf == ""


# --- is_eligible -------------------------------------------------------------


class TestEligibilityNaf:
    def test_p1_accepte_4120b(self):
        c = parse_contact(_raw(code_naf="41.20B"))
        assert is_eligible(c, EligibilityFilters()) is None

    def test_p1_accepte_4332b(self):
        c = parse_contact(_raw(code_naf="43.32B"))
        assert is_eligible(c, EligibilityFilters()) is None

    def test_p1_accepte_2511z(self):
        c = parse_contact(_raw(code_naf="25.11Z"))
        assert is_eligible(c, EligibilityFilters()) is None

    def test_p1_rejette_7111z_par_defaut(self):
        c = parse_contact(_raw(code_naf="71.11Z"))
        assert is_eligible(c, EligibilityFilters()) == REJECT_NAF_NOT_TARGET

    def test_p1p2_accepte_7111z(self):
        c = parse_contact(_raw(code_naf="71.11Z"))
        f = EligibilityFilters(naf_allowed=NAF_P1 | NAF_P2)
        assert is_eligible(c, f) is None

    def test_exclus_2512z_rejette_meme_si_dans_allowed(self):
        c = parse_contact(_raw(code_naf="25.12Z"))
        # On force 25.12Z dans allowed pour vérifier que excluded prime
        f = EligibilityFilters(naf_allowed=frozenset({"25.12Z"}))
        assert is_eligible(c, f) == REJECT_NAF_EXCLUDED

    def test_exclus_4120a_rejette(self):
        c = parse_contact(_raw(code_naf="41.20A"))
        assert is_eligible(c, EligibilityFilters()) == REJECT_NAF_EXCLUDED


class TestEligibilityEffectif:
    def test_effectif_inf_min_rejette(self):
        c = parse_contact(_raw(effectif_min=2, effectif_max=5))
        assert is_eligible(c, EligibilityFilters(min_effectif=10)) == REJECT_EFFECTIF_TOO_SMALL

    def test_effectif_max_satisfait_seuil(self):
        # min=0 mais max=20 → on accepte car on prend le max des deux
        c = parse_contact(_raw(effectif_min=0, effectif_max=20))
        assert is_eligible(c, EligibilityFilters(min_effectif=10)) is None

    def test_min_effectif_zero_desactive(self):
        c = parse_contact(_raw(effectif_min=1, effectif_max=2))
        assert is_eligible(c, EligibilityFilters(min_effectif=0)) is None


class TestEligibilityDirigeant:
    def test_dirigeant_vide_rejette(self):
        c = parse_contact(_raw(dirigeant=""))
        assert is_eligible(c, EligibilityFilters()) == REJECT_NO_DIRIGEANT

    def test_dirigeant_zero_string_rejette(self):
        # Cas réel observé dans enrichis-sirene.json : "dirigeant": "0"
        c = parse_contact(_raw(dirigeant="0"))
        assert is_eligible(c, EligibilityFilters()) == REJECT_NO_DIRIGEANT

    def test_allow_no_dirigeant(self):
        c = parse_contact(_raw(dirigeant=""))
        f = EligibilityFilters(require_dirigeant=False)
        assert is_eligible(c, f) is None


class TestEligibilityEmail:
    def test_contact_at_rejette(self):
        c = parse_contact(_raw(email="contact@acme-bat.com"))
        assert is_eligible(c, EligibilityFilters()) == REJECT_EMAIL_GENERIC

    def test_info_at_rejette(self):
        c = parse_contact(_raw(email="info@acme-bat.com"))
        assert is_eligible(c, EligibilityFilters()) == REJECT_EMAIL_GENERIC

    def test_commercial_at_rejette(self):
        c = parse_contact(_raw(email="commercial@acme-bat.com"))
        assert is_eligible(c, EligibilityFilters()) == REJECT_EMAIL_GENERIC

    def test_contact1_at_rejette_aussi(self):
        c = parse_contact(_raw(email="contact1@acme-bat.com"))
        assert is_eligible(c, EligibilityFilters()) == REJECT_EMAIL_GENERIC

    def test_nominatif_accepte(self):
        c = parse_contact(_raw(email="jean.dupont@acme-bat.com"))
        assert is_eligible(c, EligibilityFilters()) is None

    def test_domaine_b2c_gmail_rejette(self):
        c = parse_contact(_raw(email="jean.dupont@gmail.com"))
        assert is_eligible(c, EligibilityFilters()) == REJECT_EMAIL_B2C

    def test_domaine_b2c_wanadoo_rejette(self):
        c = parse_contact(_raw(email="dupont@wanadoo.fr"))
        assert is_eligible(c, EligibilityFilters()) == REJECT_EMAIL_B2C

    def test_allow_generic_email(self):
        c = parse_contact(_raw(email="contact@acme-bat.com"))
        f = EligibilityFilters(require_nominative_email=False)
        assert is_eligible(c, f) is None

    def test_allow_b2c_domain(self):
        c = parse_contact(_raw(email="jean.dupont@gmail.com"))
        f = EligibilityFilters(exclude_b2c_domains=False)
        assert is_eligible(c, f) is None


class TestEligibilitySiren:
    def test_siren_vide_rejette(self):
        c = parse_contact(_raw(siren=""))
        assert is_eligible(c, EligibilityFilters()) == REJECT_NO_SIREN


# --- Iter eligible -----------------------------------------------------------


class TestIterEligible:
    def test_compte_eligibles_et_rejets(self):
        rows = [
            _raw(email="ok1@acme.com", siren="111"),
            _raw(email="ok2@acme.com", siren="222"),
            _raw(email="contact@acme.com", siren="333"),  # generic
            _raw(email="dupont@gmail.com", siren="444"),  # b2c
            _raw(email="x@y.com", siren="555", code_naf="25.12Z"),  # excluded
            {"email": "", "properties": {}},  # parse → None (skip)
        ]
        results = list(iter_eligible(rows, EligibilityFilters()))
        # 5 parsés (email vide skip silencieux), 2 éligibles, 3 rejets
        assert len(results) == 5
        eligibles = [c for c, r in results if r is None]
        rejets = [r for c, r in results if r is not None]
        assert len(eligibles) == 2
        assert sorted(rejets) == sorted([REJECT_EMAIL_GENERIC, REJECT_EMAIL_B2C, REJECT_NAF_EXCLUDED])


# --- Helpers synthétiques ----------------------------------------------------


class TestSyntheticHelpers:
    def test_url_prefixe(self):
        assert make_synthetic_linkedin_url("123456789") == f"{SYNTHETIC_URL_PREFIX}123456789"

    def test_public_id_prefixe(self):
        assert make_synthetic_public_identifier("123456789") == f"{SYNTHETIC_PUBLIC_ID_PREFIX}123456789"

    def test_is_synthetic_lead_url(self):
        assert is_synthetic_lead_url(make_synthetic_linkedin_url("999")) is True
        assert is_synthetic_lead_url("https://www.linkedin.com/in/jdupont") is False
        assert is_synthetic_lead_url(None) is False
        assert is_synthetic_lead_url("") is False


# --- Management command (intégration Django) ---------------------------------


@pytest.fixture
def fixture_source(tmp_path):
    """Crée un mini JSON source côté tmp_path et retourne son chemin."""
    rows = [
        _raw(email="alice.martin@bat-pro.fr", siren="111111111", code_naf="41.20B",
             dirigeant="ALICE MARTIN", effectif_min=15, effectif_max=49),
        _raw(email="bob@charpente-foret.fr", siren="222222222", code_naf="25.11Z",
             dirigeant="BOB FOREST", effectif_min=20, effectif_max=49),
        # Rejet : NAF concurrent
        _raw(email="rejet1@x.com", siren="333", code_naf="25.12Z"),
        # Rejet : email générique
        _raw(email="contact@x-bat.fr", siren="444444444", code_naf="41.20B",
             dirigeant="X X", effectif_min=10, effectif_max=49),
        # Rejet : effectif trop faible
        _raw(email="claude@petit-art.fr", siren="555555555", code_naf="43.32B",
             dirigeant="CLAUDE PETIT", effectif_min=1, effectif_max=2),
    ]
    path = tmp_path / "enrichis-test.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


class TestImportCommand:
    def test_dry_run_ne_cree_aucun_lead(self, fixture_source, db):
        from crm.models import Lead
        nb_before = Lead.objects.count()
        out = StringIO()
        call_command("import_bdd_prospect", source=str(fixture_source),
                     dry_run=True, stdout=out)
        assert Lead.objects.count() == nb_before
        assert "Dry-run" in out.getvalue()

    def test_import_reel_cree_les_eligibles(self, fixture_source, db):
        from crm.models import Lead
        out = StringIO()
        call_command("import_bdd_prospect", source=str(fixture_source), stdout=out)
        # 2 éligibles attendus (alice + bob), 3 rejetés
        leads = Lead.objects.filter(contact_email_source=CONTACT_EMAIL_SOURCE)
        assert leads.count() == 2
        emails = set(leads.values_list("contact_email", flat=True))
        assert emails == {"alice.martin@bat-pro.fr", "bob@charpente-foret.fr"}

    def test_synthetic_url_et_public_id_appliqués(self, fixture_source, db):
        from crm.models import Lead
        call_command("import_bdd_prospect", source=str(fixture_source), stdout=StringIO())
        alice = Lead.objects.get(contact_email="alice.martin@bat-pro.fr")
        assert alice.public_identifier == "bdd-prospect-111111111"
        assert alice.linkedin_url == "https://bdd-prospect.local/siren/111111111"
        assert alice.contact_email_source == CONTACT_EMAIL_SOURCE

    def test_email_lead_data_persistee(self, fixture_source, db):
        """L'import crée aussi un EmailLeadData avec NAF/dirigeant/effectif."""
        from crm.models import Lead
        from ekoalu.email_canal.models import EmailLeadData

        call_command("import_bdd_prospect", source=str(fixture_source), stdout=StringIO())
        alice = Lead.objects.get(contact_email="alice.martin@bat-pro.fr")
        data = alice.email_data  # related_name="email_data"
        assert data.source == EmailLeadData.SOURCE_BDD_PROSPECT
        assert data.siren == "111111111"
        assert data.code_naf == "41.20B"
        assert data.dirigeant == "ALICE MARTIN"
        assert data.entreprise == "ACME BAT"
        assert data.effectif_min == 15
        assert data.effectif_max == 49
        assert data.ville == "LYON"
        # raw_json conservé pour debug/retraitement
        assert data.raw_json["email"] == "alice.martin@bat-pro.fr"

    def test_idempotence_skip_doublon_email(self, fixture_source, db):
        from crm.models import Lead
        # 1er import : crée 2 leads
        call_command("import_bdd_prospect", source=str(fixture_source), stdout=StringIO())
        nb_after_first = Lead.objects.filter(contact_email_source=CONTACT_EMAIL_SOURCE).count()
        # 2e import : 0 nouveau, 2 skipped
        out = StringIO()
        call_command("import_bdd_prospect", source=str(fixture_source), stdout=out)
        assert Lead.objects.filter(contact_email_source=CONTACT_EMAIL_SOURCE).count() == nb_after_first
        assert "skippés (dup)  : 2" in out.getvalue()

    def test_limit_cap_les_inserts(self, fixture_source, db):
        from crm.models import Lead
        call_command("import_bdd_prospect", source=str(fixture_source),
                     limit=1, stdout=StringIO())
        assert Lead.objects.filter(contact_email_source=CONTACT_EMAIL_SOURCE).count() == 1

    def test_include_p2_etend_le_perimetre(self, tmp_path, db):
        from crm.models import Lead
        rows = [
            _raw(email="archi@studio-x.fr", siren="999999999", code_naf="71.11Z",
                 dirigeant="ARCHI X", effectif_min=15, effectif_max=20),
        ]
        path = tmp_path / "p2.json"
        path.write_text(json.dumps(rows), encoding="utf-8")
        # Sans --include-p2 : 0 inséré
        call_command("import_bdd_prospect", source=str(path), stdout=StringIO())
        assert Lead.objects.filter(contact_email="archi@studio-x.fr").count() == 0
        # Avec --include-p2 : 1 inséré
        call_command("import_bdd_prospect", source=str(path), include_p2=True, stdout=StringIO())
        assert Lead.objects.filter(contact_email="archi@studio-x.fr").count() == 1

    def test_source_introuvable_leve_command_error(self, db):
        from django.core.management.base import CommandError
        with pytest.raises(CommandError):
            call_command("import_bdd_prospect", source="/nope/inexistant.json", stdout=StringIO())
