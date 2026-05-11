"""Tests fiabilité des 8 personas EKOALU."""
from __future__ import annotations

import pytest

from ekoalu import conf
from ekoalu.personas import (
    PERSONAS,
    Persona,
    PersonaCategory,
    get_persona,
    list_personas_by_priority,
)


class TestPersonasInventory:
    def test_exactement_8_personas(self):
        assert len(PERSONAS) == 8

    def test_priorities_uniques_1_a_8(self):
        priorities = sorted(p.priority for p in PERSONAS.values())
        assert priorities == [1, 2, 3, 4, 5, 6, 7, 8]

    def test_slugs_uniques(self):
        slugs = [p.slug for p in PERSONAS.values()]
        assert len(slugs) == len(set(slugs))

    def test_4_dirigeants_3_prescripteurs_1_promoteur(self):
        by_cat = {
            PersonaCategory.DIRIGEANT_CONNEXE: 0,
            PersonaCategory.PRESCRIPTEUR: 0,
            PersonaCategory.PROMOTEUR: 0,
            PersonaCategory.OUVERTURE: 0,
        }
        for p in PERSONAS.values():
            by_cat[p.category] += 1
        assert by_cat[PersonaCategory.DIRIGEANT_CONNEXE] == 4
        assert by_cat[PersonaCategory.PRESCRIPTEUR] == 3
        assert by_cat[PersonaCategory.PROMOTEUR] == 1


class TestPersonaPriorite:
    def test_priorite_1_dirigeant_eg(self):
        p = list_personas_by_priority()[0]
        assert p.slug == "dg_eg_tertiaire"
        assert p.category == PersonaCategory.DIRIGEANT_CONNEXE

    def test_ordre_de_priorite_coherent_avec_conf(self):
        ordered = list_personas_by_priority()
        slugs_in_order = [p.slug for p in ordered]
        # Les 4 premiers doivent être les dirigeants
        assert all(
            PERSONAS[s].category == PersonaCategory.DIRIGEANT_CONNEXE
            for s in slugs_in_order[:4]
        )


class TestPersonaProductDocs:
    @pytest.mark.parametrize("slug", list(PERSONAS.keys()))
    def test_product_docs_mentionne_chasselay(self, slug):
        assert "Chasselay" in PERSONAS[slug].product_docs

    @pytest.mark.parametrize("slug", list(PERSONAS.keys()))
    def test_product_docs_mentionne_coupe_feu(self, slug):
        """Tous les personas voient le coupe-feu comme cible commerciale (wedge)."""
        text = PERSONAS[slug].product_docs.lower()
        assert "coupe-feu" in text or "ei30" in text or "ei60" in text

    @pytest.mark.parametrize("slug", list(PERSONAS.keys()))
    def test_product_docs_mentionne_tertiaire(self, slug):
        text = PERSONAS[slug].product_docs.lower()
        assert "tertiaire" in text

    @pytest.mark.parametrize("slug", list(PERSONAS.keys()))
    def test_product_docs_ne_contient_pas_mot_banni(self, slug):
        from ekoalu.message_validator import find_banned_words
        banned = find_banned_words(PERSONAS[slug].product_docs)
        assert banned == [], f"product_docs de {slug} contient: {banned}"


class TestPersonaBooking:
    @pytest.mark.parametrize("slug", list(PERSONAS.keys()))
    def test_chaque_persona_a_un_booking_link(self, slug):
        assert PERSONAS[slug].booking_link
        assert "outlook.office365.com" in PERSONAS[slug].booking_link


class TestPersonaSearch:
    @pytest.mark.parametrize("slug", list(PERSONAS.keys()))
    def test_chaque_persona_a_des_keywords(self, slug):
        assert len(PERSONAS[slug].search_keywords) >= 2

    @pytest.mark.parametrize("slug", list(PERSONAS.keys()))
    def test_chaque_persona_a_des_titres(self, slug):
        assert len(PERSONAS[slug].titles) >= 1

    @pytest.mark.parametrize("slug", list(PERSONAS.keys()))
    def test_chaque_persona_a_des_industries(self, slug):
        assert len(PERSONAS[slug].industries) >= 1


class TestPersonaGeoScope:
    def test_bet_prescripteur_est_national(self):
        """Les niches techniques (BET) → national."""
        assert PERSONAS["bet_prescripteur"].geo_scope == "national"

    def test_dirigeants_sont_regionaux_par_defaut(self):
        for slug in ["dg_eg_tertiaire", "dg_charpente_metal", "dg_metallerie", "dg_maconnerie_tertiaire"]:
            assert PERSONAS[slug].geo_scope == "regional"


class TestGetPersona:
    def test_get_persona_par_slug(self):
        p = get_persona("dg_eg_tertiaire")
        assert isinstance(p, Persona)
        assert p.slug == "dg_eg_tertiaire"

    def test_get_persona_inexistant_leve_keyerror(self):
        with pytest.raises(KeyError):
            get_persona("inexistant")


class TestPersonasMatchConf:
    def test_personas_priority_dans_conf_correspondent_aux_slugs(self):
        for slug in conf.PERSONAS_PRIORITY:
            assert slug in PERSONAS, f"Slug {slug} dans conf mais pas dans PERSONAS"
