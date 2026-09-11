"""Cohérence nom / adresse / société dans les messages en file.

Capture Richard du 11/09 : « je vois des problèmes de cohérence entre les noms
affichés, les mails et les sociétés ». Deux défauts distincts sont couverts ici :
le corps citait une raison sociale que le domaine contredit, et « Régénérer »
sur une relance rejouait un cold mail complet.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from crm.models import Lead
from ekoalu.email_canal.models import EmailLeadData
from ekoalu.email_generator.followup_generator import FollowupDraft
from ekoalu.management.commands.audit_identites import cites_company
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound


class TestCitesCompany:
    def test_raison_sociale_nommee(self):
        assert cites_company("Bonjour, j'ai vu que CUBIK ARCHITECTURE travaille…", "CUBIK ARCHITECTURE")
        assert cites_company("Votre équipe chez Nepsen…", "NEPSEN")

    def test_terme_de_metier_ne_vaut_pas_citation(self):
        # « bureaux d'études » est le métier, pas le nom de la société : sans
        # « Matte », la société n'est pas nommée (faux positif du 11/09).
        body = "Nous cherchons à travailler avec des bureaux d'études du bâtiment."
        assert not cites_company(body, "BUREAU D'ETUDE MATTE")

    def test_mention_juridique_ignoree(self):
        assert cites_company("chez Claude Laumond", "SAS CLAUDE LAUMOND")


@pytest.mark.django_db
class TestRegenerationRelance:
    """« Régénérer » sur une relance doit appeler le générateur de RELANCE."""

    def _relance(self):
        lead = Lead.objects.create(linkedin_url="https://bdd-prospect.local/siren/1",
                                   public_identifier="bdd-prospect-1", contact_email="j.dupont@metal.fr")
        EmailLeadData.objects.create(lead=lead, source="bdd_prospect", siren="000000001",
                                     entreprise="METAL SA", dirigeant="Jean Dupont")
        cold = PendingOutbound.objects.create(
            prospect_public_id="bdd-prospect-1", kind=OutboundKind.EMAIL_COLD,
            status=OutboundStatus.SENT, subject="Objet initial", ai_draft="Corps du cold mail.")
        return PendingOutbound.objects.create(
            prospect_public_id="bdd-prospect-1", kind=OutboundKind.EMAIL_FOLLOW_UP,
            status=OutboundStatus.PENDING, subject="Re: Objet initial",
            ai_draft="ancienne relance", parent=cold)

    def test_routage_vers_le_generateur_de_relance(self):
        from ekoalu.views import _regenerate_outbound_draft

        relance = self._relance()
        with patch("ekoalu.email_generator.followup_generator.generate_email_followup",
                   return_value=FollowupDraft(body="Nouvelle relance sur un autre angle, assez longue pour etre valide.")) as gen, \
                patch("ekoalu.email_generator.generator.generate_cold_email") as cold_gen:
            ok, err = _regenerate_outbound_draft(relance, "")

        assert ok, err
        assert cold_gen.call_count == 0        # jamais le générateur de cold mail
        kwargs = gen.call_args.kwargs
        assert kwargs["original_subject"] == "Objet initial"     # le 1er mail est le contexte
        assert kwargs["original_body"] == "Corps du cold mail."
        assert kwargs["contact_email"] == "j.dupont@metal.fr"    # garde de salutation
        relance.refresh_from_db()
        assert relance.ai_draft.startswith("Nouvelle relance sur un autre angle")


@pytest.mark.django_db
class TestAffichageIdentite:
    """La liste de validation ne doit affirmer aucune identité que l'adresse
    contredit (2e capture Richard du 11/09, lignes entourées)."""

    def _lead(self, pid, email, dirigeant, entreprise):
        lead = Lead.objects.create(linkedin_url=f"https://bdd-prospect.local/siren/{pid}",
                                   public_identifier=f"bdd-prospect-{pid}", contact_email=email)
        EmailLeadData.objects.create(lead=lead, source="bdd_prospect", siren=pid,
                                     dirigeant=dirigeant, entreprise=entreprise)
        return lead

    def test_personne_morale_ne_sert_pas_de_nom_ni_de_placeholder(self):
        from ekoalu.prospect_display import resolve_prospect_display

        self._lead("633620067", "info@borello-isoclair.com", "V2R", "BORELLO ISOCLAIR")
        disp = resolve_prospect_display("bdd-prospect-633620067")
        # ni « V2R » (raison sociale), ni « Bdd Prospect » (placeholder du slug)
        assert disp["name"] == ""
        assert disp["company"] == "BORELLO ISOCLAIR"

    def test_nom_conserve_mais_signale_quand_l_adresse_le_contredit(self):
        from ekoalu.prospect_display import identity_warnings, resolve_prospect_display

        self._lead("501661912", "jean.lecuyer2@wanadoo.fr", "Stephanie Lopitaux", "EURL LOPITAUX")
        disp = resolve_prospect_display("bdd-prospect-501661912")
        assert disp["name"] == "Stephanie Lopitaux"      # Richard doit voir la donnée
        warnings = identity_warnings(disp["name"], disp["company"], "jean.lecuyer2@wanadoo.fr")
        assert warnings == ["nom non confirmé par l'adresse"]

    def test_boite_commune_de_la_societe_ne_declenche_rien(self):
        from ekoalu.prospect_display import identity_warnings

        assert identity_warnings("Jean Dumont", "DUMONT-SERVE CCBE", "contact@ds-ccbe.com") == []

    def test_societe_non_confirmee_signalee(self):
        from ekoalu.prospect_display import identity_warnings

        assert identity_warnings("", "NEPSEN", "antoine.roger@belem-ing.fr") == [
            "société non confirmée par l'adresse"]
