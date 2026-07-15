"""Tests du test de conformité quotidienne (daily_conformity, 15/07).

Chaque attendu du pipeline est vérifié ; toute non-conformité doit porter une
correction proposée. DB de test, aucun appel réseau (mail non envoyé).
"""
from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from ekoalu.management.commands.daily_conformity import (
    build_conformity_report,
    render_text,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_day_off(monkeypatch):
    monkeypatch.setenv("EKOALU_RANDOM_DAYS_OFF", "0")


def _next_weekday_pair():
    """Un `today` ouvré dont la veille est aussi ouvrée (mardi-vendredi)."""
    d = timezone.localdate()
    while d.weekday() not in (1, 2, 3, 4):  # mar-ven
        d += timedelta(days=1)
    return d


def _get(report, name):
    return next(c for c in report["checks"] if c["name"] == name)


def _campaign(active=True):
    from linkedin.models import Campaign

    return Campaign.objects.create(name="EKOALU - ABM - Conformite", active=active)


def _lead(pid, campaign, **kwargs):
    from crm.models import Lead
    from ekoalu.lead_routing.models import LeadDiscovery

    lead = Lead.objects.create(
        public_identifier=pid,
        linkedin_url=f"https://www.linkedin.com/in/{pid}/",
        **kwargs,
    )
    LeadDiscovery.objects.create(lead=lead, campaign=campaign)
    return lead


class TestChecks:
    def test_apify_hs_ko_avec_correction(self):
        from ekoalu.apify_enrich.models import ApifyUsageDay

        today = _next_weekday_pair()
        ApifyUsageDay.objects.create(date=today, count=0, failed=40)
        camp = _campaign()
        _lead("backlog-1", camp)  # backlog non vide

        report = build_conformity_report(today=today)
        c = _get(report, "Apify")
        assert c["ok"] is False
        assert "compte Apify" in c["correction"]
        assert report["conform"] is False

    def test_apify_ok_si_taux_suffisant(self):
        from ekoalu.apify_enrich.models import ApifyUsageDay

        today = _next_weekday_pair()
        ApifyUsageDay.objects.create(date=today, count=38, failed=2)
        report = build_conformity_report(today=today)
        assert _get(report, "Apify")["ok"] is True

    def test_sourcing_faible_ko_avec_correction(self):
        today = _next_weekday_pair()
        report = build_conformity_report(today=today)
        c = _get(report, "Sourcing")
        # 0 lead découvert aujourd'hui dans la DB de test
        assert c["ok"] is False
        assert "serper_rotation" in c["correction"]

    def test_connects_affames_ko(self):
        from linkedin.models import Task

        today = _next_weekday_pair()
        Task.objects.create(
            task_type=Task.TaskType.CONNECT,
            scheduled_at=timezone.now() - timedelta(days=3),
            payload={"campaign_id": 1},
        )
        report = build_conformity_report(today=today)
        c = _get(report, "Connects")
        assert c["ok"] is False
        assert "EKOALU_DAILY_CONNECT_QUOTA" in c["correction"]

    def test_qualification_zero_avec_backlog_ko(self):
        import numpy as np

        camp = _campaign()
        _lead("embed-1", camp, embedding=np.ones(384, dtype=np.float32).tobytes())

        today = _next_weekday_pair()
        report = build_conformity_report(today=today)
        c = _get(report, "Qualification")
        assert c["ok"] is False
        assert "handle_connect" in c["correction"]

    def test_envoi_bloque_ko(self):
        from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound

        po = PendingOutbound.objects.create(
            prospect_public_id="stuck", kind="follow_up",
            status=OutboundStatus.APPROVED, ai_draft="x", final_content="x",
        )
        PendingOutbound.objects.filter(pk=po.pk).update(
            approved_at=timezone.now() - timedelta(hours=30),
        )
        report = build_conformity_report(today=_next_weekday_pair())
        c = _get(report, "Envois")
        assert c["ok"] is False
        assert "caps" in c["correction"]

    def test_weekend_checks_matinaux_skippes(self):
        d = timezone.localdate()
        while d.weekday() != 6:  # dimanche
            d += timedelta(days=1)
        report = build_conformity_report(today=d)
        assert _get(report, "Apify")["skipped"] is True
        assert _get(report, "Sourcing")["skipped"] is True

    def test_toutes_non_conformites_ont_une_correction(self):
        report = build_conformity_report(today=_next_weekday_pair())
        for c in report["checks"]:
            if not c["ok"]:
                assert c["correction"], f"{c['name']} KO sans correction proposée"


class TestCommand:
    def test_no_send_ecrit_conformity_last(self, tmp_path, settings):
        settings.ROOT_DIR = tmp_path
        out = StringIO()
        call_command("daily_conformity", "--no-send", stdout=out)
        assert "VERDICT" in out.getvalue()
        assert (tmp_path / "data" / "conformity_last.md").exists()

    def test_render_text_liste_les_corrections(self):
        report = build_conformity_report(today=_next_weekday_pair())
        text = render_text(report)
        if not report["conform"]:
            assert "CORRECTION PROPOSÉE" in text
