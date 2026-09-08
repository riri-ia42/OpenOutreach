"""Tests signes typographiques IA (capture Richard 08/09).

« Interdire les grands traits et autres signes de l'IA » — le tiret cadratin
d'un DM parti le 08/07 (« dans les deux sens — ce n'est jamais... ») trahit la
génération : Richard n'écrit jamais ces signes. Bannis de TOUS les canaux via
find_style_violations (déjà branché sur DM/cold mail/reply + badge UI).
"""
from __future__ import annotations

from ekoalu.message_validator.banned_words import find_ai_signs
from ekoalu.message_validator.style_guard import find_style_violations


class TestFindAiSigns:
    def test_tiret_cadratin(self):
        text = "Un échange rapide permet d'identifier des opportunités — jamais du temps perdu."
        signs = find_ai_signs(text)
        assert any("cadratin" in s for s in signs)

    def test_tiret_demi_cadratin(self):
        assert any("demi-cadratin" in s for s in find_ai_signs("lundi – vendredi"))

    def test_trait_union_simple_autorise(self):
        assert find_ai_signs("un savoir-faire coupe-feu, du sur-mesure") == []

    def test_puce_et_markdown(self):
        assert find_ai_signs("• premier point") != []
        assert find_ai_signs("c'est **important**") != []
        assert find_ai_signs("- un point\n- un autre") != []
        assert find_ai_signs("# Titre\ntexte") != []
        assert find_ai_signs("voir [notre site](https://ekoalu.com)") != []

    def test_emojis_et_fleches(self):
        assert find_ai_signs("On avance 🚀") != []
        assert find_ai_signs("✅ validé") != []
        assert find_ai_signs("étape 1 → étape 2") != []
        assert find_ai_signs("⚠ attention") != []

    def test_message_style_richard_propre(self):
        text = (
            "Bonjour Monsieur Caruhel,\n\n"
            "Vos chantiers intègrent-ils des châssis coupe-feu EI30 ou du désenfumage ?\n\n"
            "On fabrique à Chasselay (69), alu et acier. 15 minutes en visio suffisent "
            "pour le vérifier...\n\n"
            "Bien à vous,\nRichard Gros\nPrésident EKOALU\n06 14 26 31 24"
        )
        assert find_ai_signs(text) == []

    def test_guillemets_francais_et_apostrophes_autorises(self):
        assert find_ai_signs("le « coupe-feu », c'est l'atelier d'EKOALU") == []

    def test_vide(self):
        assert find_ai_signs("") == []
        assert find_ai_signs(None) == []


class TestIntegrationStyleGuard:
    def test_style_violations_inclut_les_signes(self):
        violations = find_style_violations("De vraies opportunités — parlons-en 🚀")
        assert any("cadratin" in v for v in violations)
        assert any("émoji" in v or "picto" in v for v in violations)

    def test_cumul_mot_banni_et_signe(self):
        violations = find_style_violations("Des synergies — parlons-en")
        assert "synergies" in violations
        assert any("cadratin" in v for v in violations)
