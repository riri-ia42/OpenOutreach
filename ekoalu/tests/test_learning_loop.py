"""Tests de la boucle d'apprentissage des messages (LOT B, audit 07/07).

Couvre :
1. Few-shot CorrectionExample injecte dans le cold email
2. Mode relance sans pitch (follow-up)
3. Reject avec motif exploitable -> CorrectionExample REJECTION
4. Silotage : filtrage par canal d'abord, persona ensuite
5. Dedup consignes + regles durables (>= 3 occurrences)
6. Validator branche (1 regeneration, jamais de blocage dur)
7. used_in_prompt marque sur les exemples injectes
8. QualificationFeedback injectes dans le prompt qualifier
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.test import Client
from django.urls import reverse

from ekoalu import conf, learning
from ekoalu.follow_up.generator import _render_system_prompt, generate_ekoalu_dm
from ekoalu.inbox_assist.models import CorrectionExample, PendingReply
from ekoalu.message_validator.style_guard import enforce_style, find_style_violations

pytestmark = pytest.mark.django_db


# --- Helpers ----------------------------------------------------------------

def make_example(channel, persona_slug="", instruction="", ai_draft="brouillon IA",
                 final_sent="version Richard", kind=None):
    pr = PendingReply.objects.create(
        prospect_public_id="x", campaign_id=1,
        inbound_message="(test)",
        ai_draft=ai_draft, final_sent=final_sent,
        status=PendingReply.Status.SENT,
    )
    if kind is None:
        kind = (CorrectionExample.Kind.INSTRUCTION_ONLY if instruction
                else CorrectionExample.Kind.TEXT_CORRECTION)
    return CorrectionExample.objects.create(
        pending_reply=pr, persona_slug=persona_slug, channel=channel,
        kind=kind, similarity_ratio=0.5, instruction=instruction,
    )


class FakeAnthropicClient:
    """Client Anthropic factice : rejoue `responses` dans l'ordre, capture les appels."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        content = MagicMock()
        content.text = text
        resp = MagicMock()
        resp.content = [content]
        return resp


_CLEAN_DM = "Bonjour,\n\nQuestion coupe-feu EI30 ?\n\nOn en parle ?\n\nRichard"
_CLEAN_EMAIL = ("<sujet>Coupe-feu EI60</sujet><corps>Bonjour,\n\nUn point coupe-feu."
                "\n\n[LIEN_RDV]\n\nBien à vous,\nRichard Gros</corps>")


# --- Fix 1 : few-shot cold email --------------------------------------------

class TestColdEmailFewShot:
    def test_corrections_email_cold_injectees_dans_le_prompt(self):
        make_example(CorrectionExample.Channel.EMAIL_COLD,
                     final_sent="VERSION_RICHARD_COLD_TOKEN")
        client = FakeAnthropicClient([_CLEAN_EMAIL])
        with patch("ekoalu.email_generator.generator._get_anthropic_client",
                   return_value=client):
            from ekoalu.email_generator.generator import generate_cold_email
            d = generate_cold_email(entreprise="X", dirigeant="Y", code_naf="41.20B",
                                    activite="", ville="", dpt="",
                                    effectif_min=10, effectif_max=20)
        assert d.is_valid()
        system = client.calls[0]["system"]
        assert "VERSION_RICHARD_COLD_TOKEN" in system
        assert "EXEMPLES DE FEEDBACK RICHARD" in system

    def test_pas_de_contamination_dm_dans_cold_email(self):
        make_example(CorrectionExample.Channel.LINKEDIN_DM,
                     final_sent="TOKEN_DM_LINKEDIN")
        client = FakeAnthropicClient([_CLEAN_EMAIL])
        with patch("ekoalu.email_generator.generator._get_anthropic_client",
                   return_value=client):
            from ekoalu.email_generator.generator import generate_cold_email
            generate_cold_email(entreprise="X", dirigeant="", code_naf="",
                                activite="", ville="", dpt="",
                                effectif_min=0, effectif_max=0)
        assert "TOKEN_DM_LINKEDIN" not in client.calls[0]["system"]


# --- Fix 2 : mode relance sans pitch ----------------------------------------

class TestRelanceMode:
    def test_relance_system_prompt_sans_structure_4_blocs(self):
        prompt = _render_system_prompt(include_booking=False, relance=True)
        assert "BLOC 2" not in prompt
        assert "NE REPETE PAS" in prompt
        assert "Zero pitch" in prompt

    def test_premier_message_garde_structure_4_blocs(self):
        prompt = _render_system_prompt(include_booking=False, relance=False)
        assert "BLOC 2" in prompt

    def test_relance_ne_reappose_pas_le_bloc_signature(self):
        no_sig = "Merci pour votre retour. Le PV EI30 est dispo.\n\nRichard"
        client = FakeAnthropicClient([no_sig])
        with patch("ekoalu.follow_up.generator._get_anthropic_client",
                   return_value=client):
            out = generate_ekoalu_dm(public_id="x", relance=True)
        assert conf.SIGNATURE_EMAIL not in out
        assert out.endswith("Richard")

    def test_premier_message_signature_toujours_garantie(self):
        no_sig = "Bonjour,\n\nQuestion ?\n\nService coupe-feu.\n\nA echanger ?"
        client = FakeAnthropicClient([no_sig])
        with patch("ekoalu.follow_up.generator._get_anthropic_client",
                   return_value=client):
            out = generate_ekoalu_dm(public_id="x", relance=False)
        assert conf.SIGNATURE_NAME in out

    def test_patch_detecte_relance_via_messages_sortants(self):
        """_is_first_outgoing_dm pilote le mode relance dans le patch daemon."""
        from ekoalu.follow_up.patch import _is_first_outgoing_dm
        from crm.models import Deal, Lead
        from linkedin.models import Campaign
        from chat.models import ChatMessage
        from django.contrib.contenttypes.models import ContentType

        lead = Lead.objects.create(public_identifier="rel-test",
                                   linkedin_url="https://linkedin.com/in/rel-test")
        camp = Campaign.objects.create(name="EKOALU - Test relance")
        deal = Deal.objects.create(lead=lead, campaign=camp)
        assert _is_first_outgoing_dm(deal) is True
        ChatMessage.objects.create(
            content_type=ContentType.objects.get_for_model(Lead),
            object_id=lead.pk, is_outgoing=True, content="pitch envoye",
        )
        assert _is_first_outgoing_dm(deal) is False


# --- Fix 3 : reject -> apprentissage ----------------------------------------

class TestRejectLearning:
    @pytest.fixture
    def client_staff(self, django_user_model):
        django_user_model.objects.create_user(
            username="admin-ll", password="pwd12345", is_staff=True,
        )
        c = Client()
        c.login(username="admin-ll", password="pwd12345")
        return c

    def _make_outbound(self, kind="follow_up"):
        from ekoalu.outbound_validation.models import PendingOutbound
        return PendingOutbound.objects.create(
            prospect_public_id="rj-test", kind=kind, ai_draft="draft refuse",
        )

    def test_reject_avec_motif_exploitable_cree_apprentissage(self, client_staff):
        po = self._make_outbound()
        client_staff.post(
            reverse("ekoalu:outbound_detail", args=[po.pk]),
            data={"action": "reject",
                  "rejection_reason": "une relance ne doit pas repeter notre offre"},
        )
        ce = CorrectionExample.objects.filter(
            kind=CorrectionExample.Kind.REJECTION).first()
        assert ce is not None
        assert "ne doit pas repeter" in ce.instruction
        assert ce.channel == CorrectionExample.Channel.LINKEDIN_DM
        assert ce.pending_reply.ai_draft == "draft refuse"

    def test_reject_motif_technique_ne_cree_rien(self, client_staff):
        po = self._make_outbound()
        client_staff.post(
            reverse("ekoalu:outbound_detail", args=[po.pk]),
            data={"action": "reject", "rejection_reason": "Déjà en relation"},
        )
        assert not CorrectionExample.objects.filter(
            kind=CorrectionExample.Kind.REJECTION).exists()

    def test_reject_sans_motif_ne_cree_rien(self, client_staff):
        po = self._make_outbound()
        client_staff.post(
            reverse("ekoalu:outbound_detail", args=[po.pk]),
            data={"action": "reject", "rejection_reason": "  "},
        )
        assert not CorrectionExample.objects.filter(
            kind=CorrectionExample.Kind.REJECTION).exists()

    def test_reject_email_cold_va_dans_le_bon_canal(self, client_staff):
        po = self._make_outbound(kind="email_cold")
        client_staff.post(
            reverse("ekoalu:outbound_detail", args=[po.pk]),
            data={"action": "reject", "rejection_reason": "trop long, pas de chiffres"},
        )
        ce = CorrectionExample.objects.get(kind=CorrectionExample.Kind.REJECTION)
        assert ce.channel == CorrectionExample.Channel.EMAIL_COLD

    def test_rejection_rendue_dans_le_few_shot(self):
        make_example(CorrectionExample.Channel.LINKEDIN_DM,
                     instruction="pas de flatterie en ouverture",
                     ai_draft="draft flatteur", final_sent="",
                     kind=CorrectionExample.Kind.REJECTION)
        block = learning.build_few_shot(CorrectionExample.Channel.LINKEDIN_DM)
        assert "Richard a REFUSE ce message" in block
        assert "pas de flatterie en ouverture" in block


# --- Fix 4 : silotage par canal + persona -----------------------------------

class TestChannelSiloing:
    def test_canal_filtre_en_premier(self):
        make_example(CorrectionExample.Channel.EMAIL_COLD, final_sent="TOKEN_EMAIL")
        make_example(CorrectionExample.Channel.LINKEDIN_DM, final_sent="TOKEN_DM")
        block = learning.build_few_shot(CorrectionExample.Channel.LINKEDIN_DM)
        assert "TOKEN_DM" in block
        assert "TOKEN_EMAIL" not in block

    def test_persona_privilegie_si_assez_d_exemples(self):
        finals = [
            "Bonjour, un point coupe-feu EI30 pour vos chantiers ?",
            "Le desenfumage DENFC de vos halls est-il traite en interne ?",
            "On a documente 3 PV pare-balles BC2 le mois dernier, utile ?",
        ]
        for text in finals:
            make_example(CorrectionExample.Channel.LINKEDIN_DM,
                         persona_slug="dg_metallerie", final_sent=text)
        make_example(CorrectionExample.Channel.LINKEDIN_DM,
                     persona_slug="archi_tertiaire", final_sent="TOKEN_ARCHI")
        examples = learning.select_examples(
            CorrectionExample.Channel.LINKEDIN_DM, persona_slug="dg_metallerie")
        assert len(examples) == 3
        assert all(e.persona_slug == "dg_metallerie" for e in examples)

    def test_fallback_canal_si_persona_insuffisant(self):
        make_example(CorrectionExample.Channel.LINKEDIN_DM,
                     persona_slug="archi_tertiaire", final_sent="TOKEN_ARCHI")
        examples = learning.select_examples(
            CorrectionExample.Channel.LINKEDIN_DM, persona_slug="dg_metallerie")
        assert len(examples) == 1  # fallback canal entier

    def test_derive_channel_depuis_slug_et_pending(self):
        assert (CorrectionExample.derive_channel("email_reply_rdv_request")
                == CorrectionExample.Channel.EMAIL_REPLY)
        assert (CorrectionExample.derive_channel("email_cold")
                == CorrectionExample.Channel.EMAIL_COLD)
        assert (CorrectionExample.derive_channel("dg_metallerie")
                == CorrectionExample.Channel.LINKEDIN_DM)
        assert (CorrectionExample.derive_channel("", PendingReply.CHANNEL_EMAIL)
                == CorrectionExample.Channel.EMAIL_REPLY)

    def test_channel_for_outbound_kind(self):
        assert learning.channel_for_outbound_kind("follow_up") == "linkedin_dm"
        assert learning.channel_for_outbound_kind("invitation") == "linkedin_dm"
        assert learning.channel_for_outbound_kind("reply") == "linkedin_dm"
        assert learning.channel_for_outbound_kind("email_cold") == "email_cold"
        assert learning.channel_for_outbound_kind("email_follow_up") == "email_cold"


# --- Fix 5 : dedup + regles durables ----------------------------------------

class TestDedupAndRules:
    CONSIGNE = "une relance ne doit pas repeter nos competences et notre offre"

    def test_consignes_dupliquees_une_seule_fois_en_few_shot(self):
        for _ in range(12):
            make_example(CorrectionExample.Channel.LINKEDIN_DM,
                         instruction=self.CONSIGNE)
        make_example(CorrectionExample.Channel.LINKEDIN_DM,
                     final_sent="autre exemple totalement different")
        examples = learning.select_examples(CorrectionExample.Channel.LINKEDIN_DM)
        instructions = [e.instruction for e in examples if e.instruction]
        assert len(instructions) == 1  # la consigne x12 ne sature plus la fenetre
        assert len(examples) == 2

    def test_variantes_quasi_identiques_dedupliquees(self):
        make_example(CorrectionExample.Channel.LINKEDIN_DM,
                     instruction=self.CONSIGNE)
        make_example(CorrectionExample.Channel.LINKEDIN_DM,
                     instruction=self.CONSIGNE.upper() + " !")
        examples = learning.select_examples(CorrectionExample.Channel.LINKEDIN_DM)
        assert len(examples) == 1

    def test_consigne_recurrente_promue_en_regle_durable(self):
        for _ in range(3):
            make_example(CorrectionExample.Channel.LINKEDIN_DM,
                         instruction=self.CONSIGNE)
        make_example(CorrectionExample.Channel.LINKEDIN_DM,
                     instruction="consigne unique non recurrente sur autre sujet")
        rules = learning.learned_rules(CorrectionExample.Channel.LINKEDIN_DM)
        assert len(rules) == 1
        assert "relance" in rules[0]

    def test_regles_durables_en_tete_du_system_prompt_dm(self):
        for _ in range(3):
            make_example(CorrectionExample.Channel.LINKEDIN_DM,
                         instruction=self.CONSIGNE)
        client = FakeAnthropicClient([_CLEAN_DM])
        with patch("ekoalu.follow_up.generator._get_anthropic_client",
                   return_value=client):
            generate_ekoalu_dm(public_id="x")
        system = client.calls[0]["system"]
        assert system.startswith("=== REGLES APPRISES")
        assert self.CONSIGNE in system

    def test_regles_par_canal_non_contaminees(self):
        for _ in range(3):
            make_example(CorrectionExample.Channel.EMAIL_COLD,
                         instruction="consigne email recurrente sur les objets")
        assert learning.learned_rules(CorrectionExample.Channel.LINKEDIN_DM) == []


# --- Fix 6 : validator branche ----------------------------------------------

class TestStyleGuardWired:
    def test_find_style_violations(self):
        assert find_style_violations("De vraies synergies win-win, cordialement") != []
        assert find_style_violations("Bonjour, un point coupe-feu EI30. Bien a vous") == []

    def test_dm_regenere_une_fois_si_mot_banni(self):
        dirty = "Bonjour,\n\nDe belles synergies en vue !\n\nRichard Gros\nPresident"
        client = FakeAnthropicClient([dirty, _CLEAN_DM])
        with patch("ekoalu.follow_up.generator._get_anthropic_client",
                   return_value=client):
            out = generate_ekoalu_dm(public_id="x")
        assert len(client.calls) == 2
        assert "CORRECTION DE STYLE OBLIGATOIRE" in client.calls[1]["messages"][0]["content"]
        assert "synergies" not in out

    def test_pas_de_blocage_dur_si_violation_persiste(self):
        violations_persistantes = "Cordialement, avec de vraies synergies.\n\nRichard Gros"
        regen_called = []

        def regen(motif):
            regen_called.append(motif)
            return violations_persistantes

        out = enforce_style(violations_persistantes, regen, channel="test")
        assert len(regen_called) == 1  # UNE seule regeneration
        assert out == violations_persistantes  # le message part quand meme

    def test_cold_email_regenere_si_cordialement(self):
        dirty = ("<sujet>Test</sujet><corps>Bonjour,\n\nUn point.\n\n[LIEN_RDV]\n\n"
                 "Cordialement,\nRichard Gros</corps>")
        # _ensure_closing purge deja "Cordialement" en cloture -> utilise un mot
        # banni dans le corps pour tester la voie regeneration.
        dirty = ("<sujet>Test</sujet><corps>Bonjour,\n\nNos solutions clé en main.\n\n"
                 "[LIEN_RDV]\n\nBien à vous,\nRichard Gros</corps>")
        client = FakeAnthropicClient([dirty, _CLEAN_EMAIL])
        with patch("ekoalu.email_generator.generator._get_anthropic_client",
                   return_value=client):
            from ekoalu.email_generator.generator import generate_cold_email
            d = generate_cold_email(entreprise="X", dirigeant="", code_naf="",
                                    activite="", ville="", dpt="",
                                    effectif_min=0, effectif_max=0)
        assert len(client.calls) == 2
        assert "clé en main" not in d.body

    def test_reply_regenere_si_mot_banni(self):
        from ekoalu.email_generator.reply_generator import generate_email_reply
        from ekoalu.inbox_assist.intent_classifier import Intent

        dirty = ("<sujet>Re: q</sujet><corps>Merci, restant a votre disposition.\n"
                 "Bien à vous,\nRichard Gros</corps>")
        clean = ("<sujet>Re: q</sujet><corps>Le PV EI30 arrive demain.\n"
                 "Bien à vous,\nRichard Gros</corps>")
        client = FakeAnthropicClient([dirty, clean])
        with patch("ekoalu.email_generator.reply_generator._get_anthropic_client",
                   return_value=client):
            d = generate_email_reply(intent=Intent.TECHNICAL_QUESTION,
                                     inbound_subject="q", inbound_message="?")
        assert len(client.calls) == 2
        assert "disposition" not in d.body


# --- Fix 7 : used_in_prompt marque ------------------------------------------

class TestUsedInPrompt:
    def test_selection_marque_used_in_prompt(self):
        ex = make_example(CorrectionExample.Channel.LINKEDIN_DM)
        assert ex.used_in_prompt is False
        learning.select_examples(CorrectionExample.Channel.LINKEDIN_DM)
        ex.refresh_from_db()
        assert ex.used_in_prompt is True

    def test_exemple_non_injecte_reste_a_false(self):
        ex_email = make_example(CorrectionExample.Channel.EMAIL_COLD)
        learning.select_examples(CorrectionExample.Channel.LINKEDIN_DM)
        ex_email.refresh_from_db()
        assert ex_email.used_in_prompt is False


# --- Fix 8 : QualificationFeedback dans le qualifier -------------------------

class TestQualifierFeedbackInjection:
    def _make_feedback(self, kind, campaign_id=1, explanation="explication test",
                       claude_reason="profil hors cible"):
        from ekoalu.qualification_feedback.models import QualificationFeedback
        return QualificationFeedback.objects.create(
            prospect_public_id="fb-test", campaign_id=campaign_id,
            claude_reason=claude_reason, richard_explanation=explanation, kind=kind,
        )

    def test_bloc_contient_requalify_et_confirm(self):
        from ekoalu.qualification_feedback.injection import qualification_feedback_block
        from ekoalu.qualification_feedback.models import QualificationFeedback as QF
        self._make_feedback(QF.Kind.REQUALIFY, explanation="metallier = bonne cible")
        self._make_feedback(QF.Kind.CONFIRM_REJECT, explanation="RH sans influence")
        block = qualification_feedback_block(campaign_id=1)
        assert "REVERSED" in block
        assert "CONFIRMED" in block
        assert "metallier = bonne cible" in block

    def test_already_connected_exclu(self):
        from ekoalu.qualification_feedback.injection import qualification_feedback_block
        from ekoalu.qualification_feedback.models import QualificationFeedback as QF
        self._make_feedback(QF.Kind.ALREADY_CONNECTED, explanation="deja ma relation")
        assert qualification_feedback_block(campaign_id=1) == ""

    def test_meme_campagne_prioritaire_puis_globaux(self):
        from ekoalu.qualification_feedback.injection import (
            FEEDBACK_LIMIT, qualification_feedback_block,
        )
        from ekoalu.qualification_feedback.models import QualificationFeedback as QF
        for i in range(FEEDBACK_LIMIT):
            self._make_feedback(QF.Kind.REQUALIFY, campaign_id=1,
                                explanation=f"campagne un feedback numero {i}")
        self._make_feedback(QF.Kind.REQUALIFY, campaign_id=2,
                            explanation="feedback autre campagne")
        block = qualification_feedback_block(campaign_id=1)
        assert "campagne un feedback" in block
        assert "feedback autre campagne" not in block  # campagne 1 remplit la fenetre
        # Campagne sans feedback -> fallback global
        block2 = qualification_feedback_block(campaign_id=99)
        assert "feedback autre campagne" in block2

    def test_used_in_prompt_marque(self):
        from ekoalu.qualification_feedback.injection import qualification_feedback_block
        from ekoalu.qualification_feedback.models import QualificationFeedback as QF
        fb = self._make_feedback(QF.Kind.REQUALIFY)
        assert fb.used_in_prompt is False
        qualification_feedback_block(campaign_id=1)
        fb.refresh_from_db()
        assert fb.used_in_prompt is True

    def test_qualify_with_llm_injecte_le_bloc_dans_le_user_prompt(self):
        from ekoalu.qualification_feedback.models import QualificationFeedback as QF
        self._make_feedback(QF.Kind.REQUALIFY, campaign_id=7,
                            explanation="TOKEN_FEEDBACK_RICHARD")
        captured = {}

        class FakeAgent:
            def __init__(self, *args, **kwargs):
                pass

            def run_sync(self, user_prompt):
                captured["user_prompt"] = user_prompt
                out = MagicMock()
                out.output.qualified = True
                out.output.reason = "ok"
                return out

        with patch("pydantic_ai.Agent", FakeAgent):
            from linkedin.ml.qualifier import qualify_with_llm
            label, reason = qualify_with_llm(
                "profil test", product_docs="docs", campaign_objective="obj",
                model=MagicMock(), campaign_id=7,
            )
        assert label == 1
        assert "TOKEN_FEEDBACK_RICHARD" in captured["user_prompt"]
        assert "human feedback" in captured["user_prompt"]
