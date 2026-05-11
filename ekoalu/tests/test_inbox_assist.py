"""Tests fiabilité du module inbox_assist (V1 minimal).

Couvre :
- classifieur d'intention (rule-based)
- modèles PendingReply / CorrectionExample (similarité)
"""
from __future__ import annotations

import pytest

from ekoalu.inbox_assist import Intent, classify_intent
from ekoalu.inbox_assist.models import CorrectionExample, PendingReply


class TestIntentClassifier:
    @pytest.mark.parametrize("text", [
        "Salut, on peut se voir la semaine prochaine ?",
        "Quand pouvez-vous m'appeler ?",
        "Cool, on cale un RDV ?",
        "Vos disponibilites cette semaine ?",
        "On peut caler une visio jeudi ?",
        "On se rencontre ?",
        "Vous me recevez quand ?",
    ])
    def test_rdv_request_detecte(self, text):
        assert classify_intent(text) == Intent.RDV_REQUEST, f"Should be RDV: {text!r}"

    @pytest.mark.parametrize("text", [
        "C'est quoi le delta U entre RPT 24 et 32 ?",
        "Sur EI60 vous avez quelle valeur Rw ?",
        "Comment vous traitez la jonction beton ?",
        "Quelle epaisseur de vitrage pour Rw 42 dB ?",
        "Sur Cortizo COR70 RPT vous avez un detail ?",
        "Le coupe-feu EI120 ca se gere comment en hauteur ?",
    ])
    def test_technical_question_detectee(self, text):
        assert classify_intent(text) == Intent.TECHNICAL_QUESTION, f"Should be TECH: {text!r}"

    @pytest.mark.parametrize("text", [
        "C'est trop cher pour nous.",
        "On a deja un fournisseur.",
        "On travaille avec un autre menuisier.",
        "Pas de budget pour ca.",
        "Pas une priorite cette annee.",
        "On a deja signe avec un autre.",
    ])
    def test_objection_detectee(self, text):
        assert classify_intent(text) == Intent.OBJECTION, f"Should be OBJ: {text!r}"

    @pytest.mark.parametrize("text", [
        "Pouvez-vous me retirer de votre liste ?",
        "Merci d'arreter ces messages.",
        "Ne plus me contacter SVP.",
        "Stop messages.",
        "Pas interessé, retire mes coordonnees.",
        "Je souhaite me desabonner.",
    ])
    def test_opt_out_detecte(self, text):
        assert classify_intent(text) == Intent.OPT_OUT, f"Should be OPT_OUT: {text!r}"

    @pytest.mark.parametrize("text", [
        "Bonjour, comment ca va ?",
        "Joli post sur LinkedIn !",
        "Bonne semaine.",
        "Merci.",
    ])
    def test_off_topic_par_defaut(self, text):
        assert classify_intent(text) == Intent.OFF_TOPIC, f"Should be OFF_TOPIC: {text!r}"

    def test_priorite_opt_out_sur_rdv(self):
        """Si message contient à la fois opt-out et RDV, OPT_OUT gagne."""
        text = "Pas interesse par un RDV, retirez-moi de votre liste."
        assert classify_intent(text) == Intent.OPT_OUT

    def test_priorite_rdv_sur_technical(self):
        """RDV gagne contre une question technique (RDV = action immédiate)."""
        text = "Pouvez-vous m'appeler pour qu'on parle de l EI60 ?"
        assert classify_intent(text) == Intent.RDV_REQUEST

    def test_texte_vide_retourne_off_topic(self):
        assert classify_intent("") == Intent.OFF_TOPIC
        assert classify_intent("   ") == Intent.OFF_TOPIC


@pytest.mark.django_db
class TestPendingReplyModel:
    def test_creation_pending_reply_avec_defaults(self):
        pr = PendingReply.objects.create(
            prospect_public_id="john-doe-123",
            inbound_message="Salut, on peut se voir ?",
            ai_draft="Avec plaisir, voici mon Bookings...",
        )
        assert pr.status == PendingReply.Status.PENDING
        assert pr.intent == Intent.OFF_TOPIC.value  # défaut
        assert pr.final_sent == ""
        assert pr.sent_at is None

    def test_str_pending_reply(self):
        pr = PendingReply.objects.create(
            prospect_public_id="jane-smith-99",
            inbound_message="Question EI60 ?",
            ai_draft="Reponse...",
            intent=Intent.TECHNICAL_QUESTION.value,
        )
        s = str(pr)
        assert "jane-smith-99" in s
        assert "technical_question" in s.lower() or "TECHNICAL" in s


@pytest.mark.django_db
class TestCorrectionExample:
    def test_similarity_ratio_identique(self):
        ratio = CorrectionExample.compute_similarity_ratio(
            "Hello world", "Hello world"
        )
        assert ratio == 1.0

    def test_similarity_ratio_proche_de_1_si_petite_modif(self):
        ratio = CorrectionExample.compute_similarity_ratio(
            "Bonjour, voici le detail technique sur EI60.",
            "Salut, voici le detail technique sur EI60.",
        )
        # Modification "Bonjour" → "Salut" : ratio doit être > 0.85
        assert ratio > 0.85

    def test_similarity_ratio_faible_si_reecrit(self):
        ratio = CorrectionExample.compute_similarity_ratio(
            "Hello, can we have a meeting next week?",
            "Bonjour, voici une fiche technique sur le coupe-feu.",
        )
        assert ratio < 0.5

    def test_creation_correction_example_depuis_pending(self):
        pr = PendingReply.objects.create(
            prospect_public_id="lead-42",
            inbound_message="Question ?",
            ai_draft="Reponse generee par AI",
            final_sent="Reponse modifiee par Richard",
            status=PendingReply.Status.SENT,
        )
        ce = CorrectionExample.from_pending(pr, persona_slug="archi_tertiaire")
        assert ce.pending_reply == pr
        assert ce.persona_slug == "archi_tertiaire"
        assert 0.0 < ce.similarity_ratio < 1.0
        assert ce.used_in_prompt is False
        assert isinstance(ce.diff_lines, list)
