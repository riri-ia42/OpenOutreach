"""Garde-fou d'accroche (remarque Richard 28/07).

« Pas d'accroche trop racoleuse du type partenariat à valider, c'est trop
direct et ça se développe dans le mail. »

La règle est dans le prompt, mais un prompt n'est pas une garantie : sur la
génération du 28/07, 1 objet sur 25 est passé au travers
(« EKOALU – fabricant menuiseries aluminium tertiaire / partenariat fabrication »).
"""
from __future__ import annotations

import pytest

from ekoalu.message_validator.accroche_guard import (
    accroche_fix_instruction,
    enforce_accroche,
    find_accroche_violations,
)


class TestDetection:
    def test_cas_reel_du_28_07(self):
        objet = "EKOALU – fabricant menuiseries aluminium tertiaire / partenariat fabrication"
        assert "partenariat" in find_accroche_violations(objet)

    @pytest.mark.parametrize("objet", [
        "EKOALU – partenariat à valider",
        "Complémentarité entre nos ateliers ?",
        "Et si on travaillait ensemble ?",
        "Proposition de collaboration menuiserie",
        "Rapprochement fabricants Rhône-Alpes",
        "Synergie sur les marchés tertiaires",
    ])
    def test_objets_racoleurs_detectes(self, objet):
        assert find_accroche_violations(objet)

    @pytest.mark.parametrize("objet", [
        "EKOALU – acier et produits techniques pour vos dossiers aluminium",
        "EKOALU – aluminium, système Jansen et niches techniques à Chasselay",
        "Coupe-feu EI120 et désenfumage — atelier de Chasselay",
        "Menuiseries acier pour ERP : PV d'essais disponibles",
    ])
    def test_objets_factuels_acceptes(self, objet):
        """Ce sont les objets réellement produits après correction."""
        assert find_accroche_violations(objet) == []

    def test_casse_ignoree(self):
        assert find_accroche_violations("PARTENARIAT fabrication")

    def test_objet_vide(self):
        assert find_accroche_violations("") == []
        assert find_accroche_violations(None) == []


class TestRegeneration:
    def test_objet_factuel_laisse_intact(self):
        appels = []

        def regen(motif):
            appels.append(motif)
            return ("autre", "autre corps")

        s, b = enforce_accroche("Coupe-feu EI120 — atelier Chasselay", "corps", regen)
        assert (s, b) == ("Coupe-feu EI120 — atelier Chasselay", "corps")
        assert appels == [], "aucune régénération ne doit être déclenchée"

    def test_objet_racoleur_regenere(self):
        def regen(motif):
            assert "partenariat" in motif
            return ("Acier et coupe-feu — atelier Chasselay", "nouveau corps")

        s, b = enforce_accroche("Partenariat fabrication", "corps", regen)
        assert s == "Acier et coupe-feu — atelier Chasselay"
        assert b == "nouveau corps"

    def test_regeneration_vide_garde_l_original(self):
        """Ne jamais bloquer : le mail part en file de validation Richard."""
        s, b = enforce_accroche("Partenariat fabrication", "corps", lambda m: ("", ""))
        assert (s, b) == ("Partenariat fabrication", "corps")

    def test_regeneration_toujours_racoleuse_ne_bloque_pas(self):
        s, b = enforce_accroche(
            "Partenariat fabrication", "corps",
            lambda m: ("Collaboration menuiserie", "corps 2"),
        )
        assert s == "Collaboration menuiserie", "on garde, mais un warning est loggé"

    def test_une_seule_tentative(self):
        appels = []

        def regen(motif):
            appels.append(motif)
            return ("Toujours un partenariat", "corps")

        enforce_accroche("Partenariat", "corps", regen)
        assert len(appels) == 1, "jamais de boucle de régénération"


class TestInstruction:
    def test_motif_cite_les_mots_fautifs(self):
        motif = accroche_fix_instruction(["partenariat"])
        assert "partenariat" in motif
        assert "FACTUEL" in motif

    def test_motif_preserve_le_fond(self):
        motif = accroche_fix_instruction(["complémentarité"])
        assert "ne change pas" in motif


class TestPromptDeBase:
    """La règle doit être dans le prompt STANDARD, pas seulement dans les
    angles fabricant — c'est l'erreur du 28/07 (23 mails sur 25 non couverts)."""

    @pytest.mark.parametrize("variante", ["V1", "V2"])
    def test_regle_dans_les_deux_variantes(self, variante):
        from ekoalu.email_generator import prompts
        prompt = getattr(prompts, f"BASE_SYSTEM_PROMPT_{variante}")
        assert "ACCROCHE" in prompt
        assert "partenariat" in prompt
        assert "N'ANNONCE JAMAIS" in prompt
