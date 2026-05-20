"""Tests fiabilite du generateur EKOALU DM follow-up + apprentissage instruction."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ekoalu import conf
from ekoalu.follow_up.generator import (
    BASE_SYSTEM_PROMPT,
    _build_few_shot,
    _extract_first_name,
    _render_system_prompt,
    detect_first_name,
    generate_ekoalu_dm,
    has_niche_mention,
)
from ekoalu.follow_up.models import CampaignDmConfig, get_or_create_dm_config
from ekoalu.follow_up.patch import (
    _is_ekoalu_campaign,
    _is_override_enabled,
    _persona_slug_for_campaign,
)
from ekoalu.inbox_assist.models import CorrectionExample, PendingReply


# ---- BASE_SYSTEM_PROMPT : regles structurelles ----------------------------

class TestBasePrompt:
    def test_prompt_contains_4_blocs(self):
        for marker in ("BLOC 1", "BLOC 2", "BLOC 3", "BLOC 4"):
            assert marker in BASE_SYSTEM_PROMPT

    def test_prompt_bans_flatterie_terms(self):
        for term in ("belle trajectoire", "parcours impressionnant", "surement", "bien costaud"):
            assert term in BASE_SYSTEM_PROMPT, f"Le terme banni '{term}' doit etre cite dans le prompt"

    def test_prompt_bans_commercial_jargon(self):
        for term in ("synergies", "win-win", "ROI", "permettez-moi", "j'aurais le plaisir"):
            assert term in BASE_SYSTEM_PROMPT

    def test_prompt_requires_niche_mention(self):
        assert "coupe-feu" in BASE_SYSTEM_PROMPT
        assert "EI" in BASE_SYSTEM_PROMPT
        assert "desenfumage" in BASE_SYSTEM_PROMPT.lower() or "désenfumage" in BASE_SYSTEM_PROMPT


# ---- Signature injection --------------------------------------------------

class TestSignatureInjection:
    def test_signature_uses_conf_values(self, monkeypatch):
        monkeypatch.setattr(conf, "SIGNATURE_NAME", "Test User")
        monkeypatch.setattr(conf, "SIGNATURE_TITLE", "CEO TEST")
        monkeypatch.setattr(conf, "SIGNATURE_MOBILE", "06 12 34 56 78")
        monkeypatch.setattr(conf, "SIGNATURE_EMAIL", "test@example.com")
        sig = conf.render_signature()
        assert sig == "Test User\nCEO TEST\n06 12 34 56 78\ntest@example.com"

    def test_render_system_prompt_with_booking(self, monkeypatch):
        monkeypatch.setattr(conf, "CALENDAR_BOOKING_URL", "https://example.com/book")
        prompt = _render_system_prompt(include_booking=True)
        assert "https://example.com/book" in prompt
        assert "PEUX inclure" in prompt

    def test_render_system_prompt_without_booking(self):
        prompt = _render_system_prompt(include_booking=False)
        assert "N'inclus PAS de lien" in prompt


# ---- Extraction prenom ----------------------------------------------------

class TestFirstNameExtraction:
    def test_from_facts_marker_first_name(self):
        summary = [{"memory": "first_name: Patrick"}]
        assert _extract_first_name("patrick-gomes", summary, None) == "Patrick"

    def test_from_facts_marker_prenom_fr(self):
        summary = [{"memory": "prenom: jean-marc"}]
        assert _extract_first_name("jm-test", summary, None) == "Jean-marc"

    def test_fallback_to_slug(self):
        assert _extract_first_name("patrick-gomes-gcr", None, None) == "Patrick"

    def test_no_data_returns_empty(self):
        assert _extract_first_name("", None, None) == ""

    def test_detect_first_name_public_helper(self):
        assert detect_first_name("orhan-celik") == "Orhan"


# ---- Niche detector -------------------------------------------------------

class TestNicheDetector:
    @pytest.mark.parametrize("text", [
        "coupe-feu EI60",
        "Coupe feu en EI 30",
        "DENFC desenfumage",
        "mur-rideau Sapa",
        "vitrage pare-balles BC3",
        "Grandes dimensions",
        "acoustique Rw>40",
    ])
    def test_has_niche_mention(self, text):
        assert has_niche_mention(text), f"'{text}' devrait être détecté comme niche"

    @pytest.mark.parametrize("text", [
        "Bonjour, vous allez bien ?",
        "Nous fabriquons des chaises.",
        "",
    ])
    def test_no_niche_mention(self, text):
        assert not has_niche_mention(text)


# ---- Few-shot apprentissage : 3 kinds -------------------------------------

@pytest.mark.django_db
class TestFewShotKinds:
    def _make_pending(self, ai_draft="brouillon AI", final_sent="version finale"):
        return PendingReply.objects.create(
            prospect_public_id="x", campaign_id=1,
            inbound_message="(test)",
            ai_draft=ai_draft, final_sent=final_sent,
            status=PendingReply.Status.SENT,
        )

    def test_correction_example_kind_text_correction(self):
        pr = self._make_pending(ai_draft="A", final_sent="B")
        ex = CorrectionExample.from_pending(pr, persona_slug="test")
        assert ex.kind == CorrectionExample.Kind.TEXT_CORRECTION
        assert ex.instruction == ""

    def test_correction_example_kind_instruction_only(self):
        pr = self._make_pending(ai_draft="meme texte", final_sent="meme texte")
        ex = CorrectionExample.from_pending(
            pr, persona_slug="test", instruction="sois plus direct",
        )
        assert ex.kind == CorrectionExample.Kind.INSTRUCTION_ONLY
        assert ex.instruction == "sois plus direct"

    def test_correction_example_kind_both(self):
        pr = self._make_pending(ai_draft="texte original", final_sent="texte totalement different reecrit")
        ex = CorrectionExample.from_pending(
            pr, persona_slug="test", instruction="raccourcis",
        )
        assert ex.kind == CorrectionExample.Kind.BOTH
        assert ex.instruction == "raccourcis"

    def test_few_shot_injection_includes_all_kinds(self):
        # 1 text_correction
        pr1 = self._make_pending(ai_draft="aa", final_sent="bb")
        CorrectionExample.from_pending(pr1, persona_slug="test")
        # 1 instruction_only
        pr2 = self._make_pending(ai_draft="xx", final_sent="xx")
        CorrectionExample.from_pending(pr2, persona_slug="test", instruction="sois bref")
        # 1 both
        pr3 = self._make_pending(ai_draft="orig", final_sent="reecriture complete differente")
        CorrectionExample.from_pending(pr3, persona_slug="test", instruction="ajoute mention coupe-feu")

        block = _build_few_shot(persona_slug="test", limit=10)
        assert "EXEMPLES DE FEEDBACK RICHARD" in block
        assert "CONSIGNE DE RICHARD : sois bref" in block
        assert "CONSIGNE DE RICHARD : ajoute mention coupe-feu" in block
        assert "AI a propose" in block

    def test_few_shot_empty_when_no_examples(self):
        block = _build_few_shot(persona_slug="absent", limit=5)
        assert block == ""


# ---- Patch follow_up : helpers + kill switch ------------------------------

class TestPatchHelpers:
    def test_is_ekoalu_campaign_true(self):
        c = MagicMock()
        c.name = "EKOALU - Dirigeant truc"
        assert _is_ekoalu_campaign(c)

    def test_is_ekoalu_campaign_false(self):
        c = MagicMock()
        c.name = "Autre campagne"
        assert not _is_ekoalu_campaign(c)

    def test_persona_slug_extraction(self):
        c = MagicMock()
        c.name = "EKOALU - Dirigeant Entreprise Generale tertiaire"
        slug = _persona_slug_for_campaign(c)
        assert slug == "dg_eg_tertiaire"

    def test_persona_slug_unknown(self):
        c = MagicMock()
        c.name = "EKOALU - Persona inconnu"
        assert _persona_slug_for_campaign(c) == ""

    def test_kill_switch_default_true(self, monkeypatch):
        monkeypatch.delenv("EKOALU_FOLLOW_UP_OVERRIDE_ENABLED", raising=False)
        assert _is_override_enabled() is True

    def test_kill_switch_false_disables(self, monkeypatch):
        monkeypatch.setenv("EKOALU_FOLLOW_UP_OVERRIDE_ENABLED", "false")
        assert _is_override_enabled() is False

    @pytest.mark.parametrize("val", ["0", "no", "off", "False"])
    def test_kill_switch_falsy_variants(self, monkeypatch, val):
        monkeypatch.setenv("EKOALU_FOLLOW_UP_OVERRIDE_ENABLED", val)
        assert _is_override_enabled() is False


# ---- CampaignDmConfig -----------------------------------------------------

@pytest.mark.django_db
class TestCampaignDmConfig:
    def test_get_or_create_creates_default(self):
        from linkedin.models import Campaign
        c = Campaign.objects.create(name="EKOALU - Test")
        cfg = get_or_create_dm_config(c)
        assert cfg.campaign_id == c.pk
        assert cfg.include_booking_in_first_dm is False

    def test_get_or_create_idempotent(self):
        from linkedin.models import Campaign
        c = Campaign.objects.create(name="EKOALU - Test2")
        cfg1 = get_or_create_dm_config(c)
        cfg2 = get_or_create_dm_config(c)
        assert cfg1.pk == cfg2.pk


# ---- generate_ekoalu_dm : structure 4-blocs + niche obligatoire -----------

@pytest.mark.django_db
class TestGenerateEkoaluDm:
    def _mock_anthropic(self, response_text: str):
        """Helper : monte un mock Anthropic qui renvoie response_text."""
        client = MagicMock()
        msg = MagicMock()
        msg.text = response_text
        client.messages.create.return_value.content = [msg]
        return client

    def test_generates_with_4_blocs_and_signature(self, monkeypatch):
        ideal_response = """Bonjour Patrick,

Gerez-vous des projets tertiaires (bureaux, ERP) ?

Chez EKOALU (Chasselay 69), nous fabriquons de la menuiserie alu, acier et bois technique : coupe-feu EI30/60/120, desenfumage, mur-rideau, pare-balles, acoustique Rw>40. Atelier integre.

Souhaitez-vous en echanger ?

Richard Gros
Président EKOALU
06 XX XX XX XX
richard@ekoalu.com"""
        with patch("ekoalu.follow_up.generator._get_anthropic_client") as mocked:
            mocked.return_value = self._mock_anthropic(ideal_response)
            out = generate_ekoalu_dm(public_id="patrick-test")
        assert "Bonjour Patrick" in out or "Bonjour, " in out
        assert has_niche_mention(out)
        assert conf.SIGNATURE_NAME in out
        # 4 blocs minimum = au moins 3 lignes vides separant
        assert out.count("\n\n") >= 3

    def test_appends_signature_if_missing(self):
        # Claude oublie la signature : on doit la rajouter
        response_no_sig = "Bonjour,\n\nQuestion test ?\n\nService coupe-feu.\n\nA echanger ?"
        with patch("ekoalu.follow_up.generator._get_anthropic_client") as mocked:
            mocked.return_value = self._mock_anthropic(response_no_sig)
            out = generate_ekoalu_dm(public_id="test-slug")
        assert conf.SIGNATURE_NAME in out
        assert conf.SIGNATURE_EMAIL in out

    def test_returns_empty_when_no_client(self):
        with patch("ekoalu.follow_up.generator._get_anthropic_client") as mocked:
            mocked.return_value = None
            out = generate_ekoalu_dm(public_id="x")
        assert out == ""

    def test_instruction_injected_in_user_message(self):
        captured_payload = {}

        client = MagicMock()
        msg = MagicMock()
        msg.text = "Bonjour,\n\nQ ?\n\nService coupe-feu.\n\nEchanger ?"
        client.messages.create.return_value.content = [msg]

        def capture(**kwargs):
            captured_payload.update(kwargs)
            mock_resp = MagicMock()
            mock_resp.content = [msg]
            return mock_resp

        client.messages.create.side_effect = capture

        with patch("ekoalu.follow_up.generator._get_anthropic_client") as mocked:
            mocked.return_value = client
            generate_ekoalu_dm(
                public_id="test",
                instruction="raccourcis et supprime la mention chantier",
            )

        user_msg = captured_payload["messages"][0]["content"]
        assert "raccourcis et supprime la mention chantier" in user_msg
        assert "CONSIGNE EXPLICITE DE RICHARD" in user_msg
