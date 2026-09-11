"""Tests garde de salutation (incident ABAC/Domaison 28/08).

Le cold mail saluait « Bonjour M. Domaison » (dirigeant légal au registre)
sur l'adresse barrier@abac-ingenierie.fr — réponse : « Il n'y a pas de
M. Domaison chez nous. »
"""
from __future__ import annotations

from ekoalu.email_generator.salutation import (
    clean_person_name,
    dirigeant_for_salutation,
    is_person_name,
)


class TestDirigeantForSalutation:
    def test_incident_abac_nom_supprime(self):
        # LE cas vécu : adresse nominative d'une autre personne
        assert dirigeant_for_salutation("CYRIL DOMAISON", "barrier@abac-ingenierie.fr") == ""

    def test_adresse_generique_garde_le_dirigeant(self):
        assert dirigeant_for_salutation("Paul Denjean", "contact@denjean.fr") == "Paul Denjean"
        assert dirigeant_for_salutation("Paul Denjean", "accueil@denjean.fr") == "Paul Denjean"
        assert dirigeant_for_salutation("Paul Denjean", "agence@acropole-eco.com") == "Paul Denjean"

    def test_adresse_nominative_qui_recoupe_garde_le_nom(self):
        assert dirigeant_for_salutation("Francis Boyat", "francis.boyat@steel-metal.com") == "Francis Boyat"
        assert dirigeant_for_salutation("Jérôme Mochkovitch", "j.mochkovitch@cm-economistes.fr") == "Jérôme Mochkovitch"
        # initiale + nom collés
        assert dirigeant_for_salutation("Jean Dupont", "jdupont@exemple.fr") == "Jean Dupont"

    def test_prenom_seul_recoupe(self):
        assert dirigeant_for_salutation("Lionel Geay", "l.geay.eco@orange.fr") == "Lionel Geay"

    def test_ville_en_local_part_nom_supprime(self):
        # albertville@etta-etba.com avec dirigeant Domaison : boîte d'agence,
        # pas de recoupement → prudence, pas de nom
        assert dirigeant_for_salutation("CYRIL DOMAISON", "albertville@etta-etba.com") == ""

    def test_entrees_vides_inchangees(self):
        assert dirigeant_for_salutation("", "barrier@abac.fr") == ""
        assert dirigeant_for_salutation("Paul Denjean", "") == "Paul Denjean"
        assert dirigeant_for_salutation("Paul Denjean", "sans-arobase") == "Paul Denjean"


class TestPersonneMorale:
    """Capture Richard 11/09 : le champ « dirigeant » des imports DECP porte
    parfois un mandataire (cabinet comptable, holding) ou un prénom seul."""

    def test_raison_sociale_refusee(self):
        for x in ("CABINET EMMANUEL CHEVIGNARD", "ARDOUREL & MATHONIER",
                  "BROCARD PARTICIPATIONS", "DELT'AX", "V2R", "VIMABER",
                  "SAS CLAUDE LAUMOND", "MENUISERIE DU FOREZ"):
            assert not is_person_name(x), x
            assert dirigeant_for_salutation(x, "contact@exemple.fr", "EXEMPLE") == ""

    def test_prenom_seul_refuse(self):
        assert not is_person_name("CHLOE")
        assert dirigeant_for_salutation("CHLOE", "secretariat@rolando-poisson.fr",
                                        "ROLANDO POISSON") == ""

    def test_vraies_personnes_acceptees(self):
        for x in ("Eric Duchateau", "Charles Tassin de saint pereuse",
                  "Mourad Ait arab", "Karim Cheikhrouhou"):
            assert is_person_name(x), x

    def test_casse_normalisee(self):
        assert clean_person_name("Charles Tassin de saint pereuse") == "Charles Tassin de Saint Pereuse"
        assert clean_person_name("KARIM CHEIKHROUHOU") == "Karim Cheikhrouhou"
        assert clean_person_name("jean-pierre MARTIN") == "Jean-Pierre Martin"
        assert clean_person_name("") == ""


class TestBoiteDeSociete:
    """Une boîte commune de la société garde le nom du dirigeant ; une adresse
    nominative d'une autre personne ne le garde pas, même sur le domaine de la
    société (ablampey@blampey.fr vs dirigeant Eric Duchateau)."""

    def test_local_repris_de_la_raison_sociale(self):
        assert dirigeant_for_salutation("Charles Tassin", "serrurerie-nouvelle@orange.fr",
                                        "SERRURERIE NOUVELLE") == "Charles Tassin"

    def test_sigle_pointe(self):
        assert dirigeant_for_salutation("Karim Cheikhrouhou", "vmv@vmv.fr", "V.M.V.") == "Karim Cheikhrouhou"

    def test_initiale_plus_nom_reste_nominatif(self):
        # ablampey = A. Blampey (une personne), pas la boîte « blampey »
        assert dirigeant_for_salutation("Eric Duchateau", "ablampey@blampey.fr", "BLAMPEY S.A.S.") == ""

    def test_nom_noye_dans_un_local_composite(self):
        # felicitedavidpro@outlook.fr = David-alexandre Felicite (vrai destinataire)
        assert dirigeant_for_salutation("David-alexandre Felicite", "felicitedavidpro@outlook.fr",
                                        "D.A.F. COUVERTURE BARDAGE") == "David-alexandre Felicite"

    def test_autre_personne_sur_webmail(self):
        assert dirigeant_for_salutation("Stephanie Lopitaux", "jean.lecuyer2@wanadoo.fr",
                                        "EURL LOPITAUX") == ""

