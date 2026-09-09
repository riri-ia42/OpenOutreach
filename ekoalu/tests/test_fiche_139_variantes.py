"""Fiche hub #139 (09/09) : 100 % v2, surcharges de variantes par fichier,
règle d'arrêt de l'A/B, learner hebdomadaire (corpus sans appel API)."""
from __future__ import annotations

import json

import pytest

from ekoalu.email_generator import prompts
from ekoalu.email_generator.ab_rule import (
    MIN_SENT,
    evaluate_ab,
    read_winner,
    record_winner,
    winner_path,
)


@pytest.fixture(autouse=True)
def _variants_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("EKOALU_PROMPT_VARIANTS_DIR", str(tmp_path))
    return tmp_path


class TestBasculeV2:
    def test_v1_a_poids_zero_et_v2_toujours_tiree(self):
        assert prompts.PROMPT_VARIANTS["v1"][1] == 0.0
        assert prompts.PROMPT_VARIANTS["v2"][1] > 0
        assert {prompts.pick_variant() for _ in range(100)} == {"v2"}
        assert prompts.DEFAULT_VARIANT == "v2"

    def test_render_v1_encore_possible_pour_l_historique(self):
        assert "Bien" in prompts.render_system_prompt("v1") or len(prompts.render_system_prompt("v1")) > 100


class TestSurchargesFichier:
    def test_v3_declaree_dans_active_json(self, tmp_path):
        (tmp_path / "v3.txt").write_text("PROMPT V3 test\n{signature_block}", encoding="utf-8")
        (tmp_path / "active.json").write_text(json.dumps({
            "variants": {"v3": {"file": "v3.txt", "weight": 1.0}},
        }), encoding="utf-8")
        reg = prompts.active_variants()
        assert "v3" in reg and reg["v3"][1] == 1.0
        assert {prompts.pick_variant() for _ in range(200)} == {"v2", "v3"}
        assert prompts.render_system_prompt("v3").startswith("PROMPT V3 test")

    def test_v3_sans_marqueur_signature_ignoree(self, tmp_path):
        (tmp_path / "v3.txt").write_text("prompt incomplet", encoding="utf-8")
        (tmp_path / "active.json").write_text(json.dumps({
            "variants": {"v3": {"file": "v3.txt", "weight": 1.0}},
        }), encoding="utf-8")
        assert "v3" not in prompts.active_variants()

    def test_fichier_absent_ou_invalide_sans_effet(self, tmp_path):
        (tmp_path / "active.json").write_text("{pas du json", encoding="utf-8")
        assert prompts.active_variants()["v2"][1] == 1.0


class TestRegleArret:
    def test_pas_de_verdict_sous_le_seuil(self):
        assert evaluate_ab({"v2": 99, "v3": 200}, {"v2": 10, "v3": 5}) is None
        # écart < 2 points
        assert evaluate_ab({"v2": 200, "v3": 200}, {"v2": 10, "v3": 8}) is None

    def test_verdict_et_persistance_idempotente(self, tmp_path):
        v = evaluate_ab({"v2": 200, "v3": 150}, {"v2": 6, "v3": 12})
        assert v is not None and v.winner == "v3" and v.loser == "v2"
        assert record_winner(v) is True
        assert read_winner() == "v3"
        assert record_winner(v) is False          # déjà conclu
        data = json.loads(winner_path().read_text(encoding="utf-8"))
        assert data["winner_sent"] == 150 and MIN_SENT == 100

    def test_vainqueur_coupe_les_perdantes(self, tmp_path):
        (tmp_path / "v3.txt").write_text("V3 {signature_block}", encoding="utf-8")
        (tmp_path / "active.json").write_text(json.dumps({
            "variants": {"v3": {"file": "v3.txt", "weight": 1.0}},
        }), encoding="utf-8")
        record_winner(evaluate_ab({"v2": 200, "v3": 150}, {"v2": 6, "v3": 12}))
        reg = prompts.active_variants()
        assert reg["v3"][1] == 1.0 and reg["v2"][1] == 0.0
        assert {prompts.pick_variant() for _ in range(50)} == {"v3"}

    def test_active_restreint_aux_variantes_en_lice(self):
        # v1 (poids 0) a 300 envois mais n'est plus en lice : pas de verdict
        assert evaluate_ab({"v1": 300, "v2": 300}, {"v1": 30, "v2": 5}, active={"v2"}) is None


@pytest.mark.django_db
class TestLearnerCorpus:
    def test_corpus_et_extraction_prompt(self):
        from django.utils import timezone

        from ekoalu.management.commands.learner_weekly import _extract_prompt, build_corpus
        from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

        PendingOutbound.objects.create(
            prospect_public_id="bdd-prospect-1", kind=OutboundKind.EMAIL_COLD,
            status=OutboundStatus.SENT, ai_draft="Bonjour", prompt_variant="v2",
            subject="Objet", sent_at=timezone.now(),
        )
        corpus = build_corpus(days=30, max_mails=10)
        assert len(corpus["mails"]) == 1 and corpus["mails"][0]["variante"] == "v2"
        json.dumps(corpus)  # sérialisable
        text = "## Analyse\nx\n## Prompt v3\n```prompt\nPROMPT {signature_block}\n```\n## Mots-clés"
        assert _extract_prompt(text) == "PROMPT {signature_block}"
        assert _extract_prompt("rien") is None

    def test_dry_run_sans_api(self, tmp_path):
        from django.core.management import call_command
        from django.utils import timezone

        from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

        PendingOutbound.objects.create(
            prospect_public_id="bdd-prospect-2", kind=OutboundKind.EMAIL_COLD,
            status=OutboundStatus.SENT, ai_draft="Bonjour", prompt_variant="v2", sent_at=timezone.now(),
        )
        call_command("learner_weekly", "--dry-run")
        assert list((tmp_path / "proposals").glob("corpus_*.json"))
