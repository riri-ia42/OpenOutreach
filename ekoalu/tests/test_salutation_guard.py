"""Tests garde de salutation (incident ABAC/Domaison 28/08).

Le cold mail saluait « Bonjour M. Domaison » (dirigeant légal au registre)
sur l'adresse barrier@abac-ingenierie.fr — réponse : « Il n'y a pas de
M. Domaison chez nous. »
"""
from __future__ import annotations

from ekoalu.email_generator.salutation import dirigeant_for_salutation


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
