"""Tests anti-doublon PERSONNE (capture Richard 27/08 13:15).

Le même humain existe souvent sous 2 leads : lead société (contact@, dirigeant
renseigné) + personne du groupe d'influence (prenom.nom@). Le dedup par email
exact ne le voyait pas — Francis Boyat (STEEL METAL 01) était 2 fois dans la
file de validation.
"""
from __future__ import annotations

import pytest

from crm.models import Lead
from ekoalu.email_canal.models import EmailLeadData
from ekoalu.email_canal.pool import cold_mail_candidates
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound
from ekoalu.person_identity import norm_person_name, same_person


class TestPersonIdentity:
    def test_norm_ignore_casse_accents_ordre(self):
        assert norm_person_name("Adil El mansouri") == norm_person_name("EL MANSOURI Adil")
        assert norm_person_name("Jean-Phillippe Mazet") == norm_person_name("MAZET Jean Phillippe")
        assert norm_person_name("Sébastien Baud") == norm_person_name("sebastien baud")

    def test_personnes_differentes(self):
        assert not same_person("Francis Boyat", "Didier Fayard")
        assert not same_person("", "Didier Fayard")
        assert not same_person("", "")


def _lead(public_id, siren, dirigeant, email, entreprise="ACME"):
    lead = Lead.objects.create(
        linkedin_url=f"https://bdd-prospect.local/siren/{public_id}",
        public_identifier=public_id,
        contact_email=email,
    )
    EmailLeadData.objects.create(
        lead=lead, source=EmailLeadData.SOURCE_BDD_PROSPECT,
        siren=siren, entreprise=entreprise, dirigeant=dirigeant,
    )
    return lead


@pytest.mark.django_db
class TestPoolPersonDedup:
    def test_personne_deja_sollicitee_via_autre_lead_exclue(self):
        # Lead société : cold mail déjà envoyé
        societe = _lead("bdd-prospect-899", "899012157", "Francis Boyat",
                        "contact@steel-metal.com")
        PendingOutbound.objects.create(
            prospect_public_id=societe.public_identifier,
            kind=OutboundKind.EMAIL_COLD, status=OutboundStatus.SENT, ai_draft="x",
        )
        # Même humain, lead influence nominatif : NE DOIT PAS être candidat
        _lead("bdd-prospect-899-i1", "899012157", "Francis Boyat",
              "francis.boyat@steel-metal.com")
        candidates, _ = cold_mail_candidates()
        slugs = [c.public_identifier for c in candidates]
        assert "bdd-prospect-899-i1" not in slugs

    def test_meme_personne_deux_fois_candidate_une_seule_gardee(self):
        _lead("bdd-prospect-330", "330994211", "Jean Mazet", "accueil@mazet.fr")
        _lead("bdd-prospect-330-i1", "330994211", "Jean Mazet", "jean.mazet@mazet.fr")
        candidates, _ = cold_mail_candidates()
        memes = [c for c in candidates
                 if c.public_identifier.startswith("bdd-prospect-330")]
        assert len(memes) == 1

    def test_personnes_differentes_meme_societe_toutes_candidates(self):
        # Groupe d'influence multi-contacts : voulu (règle cible prioritaire)
        _lead("bdd-prospect-950-i2", "950009944", "Matthieu Millet",
              "matthieu.millet@leny-alain.fr", entreprise="LE NY")
        _lead("bdd-prospect-950-i3", "950009944", "Cyril Teoli",
              "cyril.teoli@leny-alain.fr", entreprise="LE NY")
        candidates, _ = cold_mail_candidates()
        slugs = [c.public_identifier for c in candidates]
        assert "bdd-prospect-950-i2" in slugs
        assert "bdd-prospect-950-i3" in slugs

    def test_personne_refusee_via_autre_lead_exclue(self):
        societe = _lead("bdd-prospect-777", "777000111", "Paul Roux", "contact@roux.fr")
        PendingOutbound.objects.create(
            prospect_public_id=societe.public_identifier,
            kind=OutboundKind.EMAIL_COLD, status=OutboundStatus.REJECTED, ai_draft="x",
        )
        _lead("bdd-prospect-777-i1", "777000111", "Paul Roux", "paul.roux@roux.fr")
        candidates, _ = cold_mail_candidates()
        slugs = [c.public_identifier for c in candidates]
        assert "bdd-prospect-777-i1" not in slugs
