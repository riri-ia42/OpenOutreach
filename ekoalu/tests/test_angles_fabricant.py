"""Angle commercial selon ce que le confrère fabrique (décision Richard 28/07).

Règle : on ne propose JAMAIS ce qu'il sait déjà faire.
  alu seul      → acier + produits techniques
  acier seul    → alu + Jansen acier + produits techniques
  alu ET acier  → sous-traitance + produits techniques
  bois/PVC seul → alu + acier + produits techniques  (extension, à confirmer)
"""
from __future__ import annotations

import pytest

from ekoalu.fabricant_detect.angles import (
    ANGLE_ACIER_VERS_ALU,
    ANGLE_ALU_VERS_ACIER,
    ANGLE_COMPLEMENT_TOTAL,
    ANGLE_SOUS_TRAITANCE,
    ANGLE_STANDARD,
    angle_for_materiaux,
    angle_for_siren,
)


class TestRegleRichard:
    def test_fabricant_alu_on_propose_acier(self):
        assert angle_for_materiaux(["alu"]) is ANGLE_ALU_VERS_ACIER

    def test_fabricant_acier_on_propose_alu_et_jansen(self):
        assert angle_for_materiaux(["acier"]) is ANGLE_ACIER_VERS_ALU

    def test_fabricant_alu_et_acier_on_propose_sous_traitance(self):
        assert angle_for_materiaux(["alu", "acier"]) is ANGLE_SOUS_TRAITANCE
        assert angle_for_materiaux(["acier", "alu"]) is ANGLE_SOUS_TRAITANCE

    def test_fabricant_alu_et_pvc_reste_un_angle_acier(self):
        """Le PVC ne change rien : ce qui manque, c'est le métal noir."""
        assert angle_for_materiaux(["alu", "pvc"]) is ANGLE_ALU_VERS_ACIER

    def test_fabricant_bois_ou_pvc_tout_est_complementaire(self):
        """Extension de la règle — Richard n'a cadré que alu/acier/les deux."""
        assert angle_for_materiaux(["bois"]) is ANGLE_COMPLEMENT_TOTAL
        assert angle_for_materiaux(["pvc"]) is ANGLE_COMPLEMENT_TOTAL

    def test_materiaux_inconnus_pas_de_devinette(self):
        assert angle_for_materiaux([]) is ANGLE_STANDARD
        assert angle_for_materiaux(None) is ANGLE_STANDARD

    def test_casse_et_espaces_tolerees(self):
        assert angle_for_materiaux([" ALU ", "Acier"]) is ANGLE_SOUS_TRAITANCE


class TestContenuDesAngles:
    def test_on_ne_propose_jamais_ce_qu_il_fabrique(self):
        """Le cœur de la consigne : ne pas se poser en concurrent."""
        for angle in (ANGLE_ALU_VERS_ACIER, ANGLE_ACIER_VERS_ALU,
                      ANGLE_SOUS_TRAITANCE, ANGLE_COMPLEMENT_TOTAL):
            assert "ce qu'il fabrique déjà" in angle.contexte
            assert "CONFRÈRE FABRICANT" in angle.contexte

    def test_angle_alu_ne_repropose_pas_d_alu(self):
        assert "PAS d'aluminium" in ANGLE_ALU_VERS_ACIER.contexte
        assert "ACIER" in ANGLE_ALU_VERS_ACIER.contexte

    def test_angle_acier_cite_jansen(self):
        assert "JANSEN" in ANGLE_ACIER_VERS_ALU.contexte
        assert "ALUMINIUM" in ANGLE_ACIER_VERS_ALU.contexte

    def test_angle_double_parle_sous_traitance(self):
        assert "SOUS-TRAITANCE" in ANGLE_SOUS_TRAITANCE.contexte

    def test_produits_techniques_partout(self):
        """Les niches sont la porte d'entrée quel que soit l'angle."""
        for angle in (ANGLE_ALU_VERS_ACIER, ANGLE_ACIER_VERS_ALU,
                      ANGLE_SOUS_TRAITANCE, ANGLE_COMPLEMENT_TOTAL):
            assert "coupe-feu" in angle.contexte
            assert "pare-balles" in angle.contexte

    def test_politiques_sensibles_respectees(self):
        """Cortizo jamais cité en externe, Sepalumic retiré de la com."""
        for angle in (ANGLE_ALU_VERS_ACIER, ANGLE_ACIER_VERS_ALU,
                      ANGLE_SOUS_TRAITANCE, ANGLE_COMPLEMENT_TOTAL, ANGLE_STANDARD):
            assert "Cortizo" not in angle.contexte
            assert "Sepalumic" not in angle.contexte

    def test_angle_standard_est_vide(self):
        """Pas d'angle = rien d'injecté, le mail reste un cold classique."""
        assert not ANGLE_STANDARD
        assert ANGLE_STANDARD.contexte == ""


@pytest.mark.django_db
class TestAngleDepuisLaBase:
    def _verdict(self, **kw):
        from ekoalu.fabricant_detect.models import FabricantVerdict
        defaults = {"siren": "111", "verdict": "fabricant",
                    "confiance": "haute", "materiaux": ["alu"]}
        defaults.update(kw)
        return FabricantVerdict.objects.create(**defaults)

    def test_fabricant_avere_donne_son_angle(self):
        self._verdict(siren="123", materiaux=["acier"])
        assert angle_for_siren("123") is ANGLE_ACIER_VERS_ALU

    def test_revendeur_garde_le_discours_standard(self):
        self._verdict(siren="456", verdict="revendeur_poseur", materiaux=["alu"])
        assert angle_for_siren("456") is ANGLE_STANDARD

    def test_verdict_peu_sur_ne_change_pas_le_discours(self):
        """On n'adapte jamais le discours sur une supposition."""
        self._verdict(siren="789", confiance="basse", materiaux=["alu"])
        assert angle_for_siren("789") is ANGLE_STANDARD

    def test_indetermine_reste_standard(self):
        self._verdict(siren="321", verdict="indetermine", materiaux=["alu"])
        assert angle_for_siren("321") is ANGLE_STANDARD

    def test_societe_inconnue(self):
        assert angle_for_siren("000") is ANGLE_STANDARD
        assert angle_for_siren("") is ANGLE_STANDARD

    def test_cas_reel_vmv_alu_et_pvc(self):
        """V.M.V. (passe du 28/07) : fabricant alu+PVC → angle acier."""
        self._verdict(siren="vmv", materiaux=["alu", "pvc"])
        assert angle_for_siren("vmv") is ANGLE_ALU_VERS_ACIER

    def test_cas_reel_bouvier_freres_bois(self):
        """BOUVIER FRÈRES : charpente/menuiserie bois → tout le métal est ouvert."""
        self._verdict(siren="bouvier", materiaux=["bois"])
        assert angle_for_siren("bouvier") is ANGLE_COMPLEMENT_TOTAL
