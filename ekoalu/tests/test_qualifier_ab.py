"""Tests A/B qualifier (challenger Haiku vs champion Sonnet)."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from ekoalu.qualifier_ab import runner


@pytest.fixture
def ab_paths(tmp_path, monkeypatch):
    """Redirige tous les fichiers d'etat A/B vers un tmp dir isole."""
    monkeypatch.setattr(runner, "_data_path", lambda name: str(tmp_path / name))
    return tmp_path


def _qualify_side_effect(*args, **kwargs):
    # champion = appel sans model ; challenger = appel avec model=...
    if kwargs.get("model") is not None:
        return (1, "haiku: tertiaire + metallerie")
    return (0, "sonnet: hors cible habitat")


class TestActivation:
    def test_inactive_sans_sentinel(self, ab_paths):
        assert runner.ab_is_active() is False

    def test_start_ab_pose_sentinel_et_retire_flag(self, ab_paths):
        # un kill-switch flag pre-existant doit etre retire
        open(str(ab_paths / runner.DISABLED_FLAG_NAME), "w").close()
        state = runner.start_ab(n=5, challenger_model="claude-haiku-4-5-20251001")
        assert state["remaining"] == 5
        assert runner.ab_is_active() is True
        assert not os.path.exists(str(ab_paths / runner.DISABLED_FLAG_NAME))

    def test_inactif_quand_quota_epuise(self, ab_paths):
        with open(str(ab_paths / runner.SENTINEL_NAME), "w") as fh:
            json.dump({"remaining": 0}, fh)
        assert runner.ab_is_active() is False


class TestDualScoring:
    def test_champion_decide_challenger_logge(self, ab_paths):
        runner.start_ab(n=3)
        with patch("linkedin.ml.qualifier.qualify_with_llm", side_effect=_qualify_side_effect), \
             patch("linkedin.llm.get_named_anthropic_model", return_value=MagicMock()), \
             patch("linkedin.models.SiteConfig.load", return_value=MagicMock(llm_provider="anthropic")):
            label, reason = runner.run_ab_qualification(
                "profil X", "docs", "objectif", "john-doe", 7,
            )
        # le verdict retourne est celui du CHAMPION (Sonnet -> 0)
        assert label == 0
        assert "sonnet" in reason
        # une ligne de resultat ecrite avec les 2 verdicts
        s = runner.summarize()
        assert s["total"] == 1
        row = s["rows"][0]
        assert row["champion_label"] == 0
        assert row["challenger_label"] == 1
        assert row["agree"] is False
        # quota decremente
        assert runner._read_sentinel()["remaining"] == 2

    def test_challenger_skip_si_provider_non_anthropic(self, ab_paths):
        runner.start_ab(n=2)
        with patch("linkedin.ml.qualifier.qualify_with_llm", side_effect=_qualify_side_effect), \
             patch("linkedin.models.SiteConfig.load", return_value=MagicMock(llm_provider="openai")):
            label, _ = runner.run_ab_qualification("p", "d", "o", "id1", 1)
        assert label == 0  # champion toujours rendu
        assert runner.summarize()["rows"][0]["challenger_label"] is None

    def test_challenger_erreur_ne_casse_pas_la_qualif(self, ab_paths):
        runner.start_ab(n=2)

        def champion_only(*a, **k):
            if k.get("model") is not None:
                raise RuntimeError("boom haiku")
            return (1, "sonnet ok")

        with patch("linkedin.ml.qualifier.qualify_with_llm", side_effect=champion_only), \
             patch("linkedin.llm.get_named_anthropic_model", return_value=MagicMock()), \
             patch("linkedin.models.SiteConfig.load", return_value=MagicMock(llm_provider="anthropic")):
            label, reason = runner.run_ab_qualification("p", "d", "o", "id2", 1)
        assert label == 1 and reason == "sonnet ok"
        row = runner.summarize()["rows"][0]
        assert row["challenger_label"] is None
        assert "error" in str(row["challenger_reason"])


class TestFinalisation:
    def test_dernier_score_repause_et_maile(self, ab_paths):
        runner.start_ab(n=1)
        with patch("linkedin.ml.qualifier.qualify_with_llm", side_effect=_qualify_side_effect), \
             patch("linkedin.llm.get_named_anthropic_model", return_value=MagicMock()), \
             patch("linkedin.models.SiteConfig.load", return_value=MagicMock(llm_provider="anthropic")), \
             patch("ekoalu.notifications.graph_mailer.send_mail") as mail:
            runner.run_ab_qualification("p", "d", "o", "last-id", 1)
        # sentinel supprime, flag de pause recree
        assert not os.path.exists(str(ab_paths / runner.SENTINEL_NAME))
        assert os.path.exists(str(ab_paths / runner.DISABLED_FLAG_NAME))
        assert runner.ab_is_active() is False
        mail.assert_called_once()

    def test_summarize_agrege(self, ab_paths):
        runner.start_ab(n=3)
        rows = [
            {"champion_label": 1, "challenger_label": 1, "agree": True},
            {"champion_label": 0, "challenger_label": 1, "agree": False},
            {"champion_label": 0, "challenger_label": None, "agree": False},
        ]
        with open(str(ab_paths / runner.RESULTS_NAME), "w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        s = runner.summarize()
        assert s["total"] == 3
        assert s["champion_qualified"] == 1
        assert s["challenger_scored"] == 2
        assert s["challenger_qualified"] == 2
        assert s["agreement"] == 1
        assert s["agreement_pct"] == 50.0
