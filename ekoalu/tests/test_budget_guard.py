"""Tests budget_guard : sentinel + court-circuit appels Claude."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_sentinel(tmp_path, monkeypatch):
    """Redirige le sentinel vers tmp_path pour isoler chaque test."""
    from ekoalu.llm_usage import budget_guard

    p = tmp_path / "daily_budget_exceeded.json"
    monkeypatch.setattr(budget_guard, "SENTINEL_PATH", p)
    yield p
    if p.exists():
        p.unlink()


@pytest.mark.django_db
class TestBudgetGuard:
    def test_pas_de_sentinel_pas_de_blocage(self, _isolate_sentinel):
        from ekoalu.llm_usage.budget_guard import is_budget_exceeded
        assert is_budget_exceeded() is False

    def test_trigger_cree_sentinel_et_bloque(self, _isolate_sentinel):
        from ekoalu.llm_usage.budget_guard import _trigger_budget_exceeded, is_budget_exceeded

        # Mail mocke pour eviter Graph call
        with patch("ekoalu.llm_usage.budget_guard._send_alert_mail"):
            _trigger_budget_exceeded(11.14, 4.0, [("follow_up_agent", 633, 6.47)])

        assert _isolate_sentinel.exists()
        assert is_budget_exceeded() is True
        data = json.loads(_isolate_sentinel.read_text(encoding="utf-8"))
        assert data["cost_today_usd"] == 11.14
        assert data["budget_usd"] == 4.0
        assert data["top_contexts"][0]["context"] == "follow_up_agent"

    def test_sentinel_d_hier_auto_purge(self, _isolate_sentinel):
        """Reset auto a minuit local : un sentinel datant d'hier doit etre purge."""
        from ekoalu.llm_usage.budget_guard import is_budget_exceeded

        hier = (datetime.now() - timedelta(days=1)).isoformat()
        _isolate_sentinel.write_text(
            json.dumps({
                "triggered_at_local": hier,
                "budget_usd": 4.0,
                "cost_today_usd": 5.0,
                "top_contexts": [],
            }),
            encoding="utf-8",
        )
        assert is_budget_exceeded() is False
        assert not _isolate_sentinel.exists()

    def test_acknowledge_supprime_sentinel(self, _isolate_sentinel):
        from ekoalu.llm_usage.budget_guard import _trigger_budget_exceeded, acknowledge

        with patch("ekoalu.llm_usage.budget_guard._send_alert_mail"):
            _trigger_budget_exceeded(5.0, 4.0, [])
        assert _isolate_sentinel.exists()

        assert acknowledge() is True
        assert not _isolate_sentinel.exists()
        assert acknowledge() is False  # idempotent : 2e fois = False

    def test_check_and_trigger_appelle_quand_cumul_depasse(self, _isolate_sentinel):
        from ekoalu.llm_usage.budget_guard import check_and_trigger_if_exceeded
        from ekoalu.llm_usage.models import ClaudeUsageLog

        ClaudeUsageLog.objects.create(
            model="claude-sonnet-4-6",
            input_tokens=1000, output_tokens=100,
            cost_usd=5.0,
            context="follow_up_agent",
        )
        with patch("ekoalu.llm_usage.budget_guard._send_alert_mail"):
            check_and_trigger_if_exceeded()
        assert _isolate_sentinel.exists()

    def test_check_and_trigger_ne_trigger_pas_sous_seuil(self, _isolate_sentinel):
        from ekoalu.llm_usage.budget_guard import check_and_trigger_if_exceeded
        from ekoalu.llm_usage.models import ClaudeUsageLog

        ClaudeUsageLog.objects.create(
            model="claude-sonnet-4-6",
            input_tokens=100, output_tokens=10,
            cost_usd=2.50,
            context="follow_up_agent",
        )
        check_and_trigger_if_exceeded()
        assert not _isolate_sentinel.exists()
