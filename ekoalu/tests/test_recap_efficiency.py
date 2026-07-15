"""Tests de l'analyse d'efficacité + préco du recap du soir (demande Richard 16/06)."""
from __future__ import annotations

import pytest
from django.utils import timezone

from ekoalu.management.commands.daily_recap import build_efficiency_analysis

pytestmark = pytest.mark.django_db


def _campaign(name="EKOALU - Recap test"):
    from linkedin.models import Campaign
    return Campaign.objects.create(name=name)


def _read_day(day, sources, count):
    from ekoalu.read_guard.models import ProfileReadDay
    return ProfileReadDay.objects.create(date=day, count=count, sources=sources)


def _deals(camp, n, state="Qualified", outcome=""):
    from crm.models import Deal, Lead
    for i in range(n):
        slug = f"rec-{state}-{outcome}-{i}"
        lead = Lead.objects.create(public_identifier=slug,
                                   linkedin_url=f"https://www.linkedin.com/in/{slug}")
        Deal.objects.create(lead=lead, campaign=camp, state=state, outcome=outcome)


def test_metriques_decomposees(monkeypatch):
    monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "150")
    monkeypatch.delenv("EKOALU_PROFILE_READS_CAP_RAMP", raising=False)
    today = timezone.localdate()
    _read_day(today, {"selection": 10, "follow_up": 5,
                      "get_connection_degree": 3, "visit_profile": 2}, 20)
    camp = _campaign()
    _deals(camp, 3, state="Qualified")

    eff = build_efficiency_analysis(today)
    assert eff["total"] == 20
    assert eff["selection"] == 10
    assert eff["follow_up"] == 5
    assert eff["degree"] == 3
    assert eff["visit"] == 2
    assert eff["selected"] == 3
    assert eff["efficacite"] == 30.0          # 3/10
    assert eff["part_recherche"] == 50.0      # 10/20


def test_preco_efficacite_faible(monkeypatch):
    monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "150")
    monkeypatch.delenv("EKOALU_PROFILE_READS_CAP_RAMP", raising=False)
    today = timezone.localdate()
    _read_day(today, {"selection": 20}, 20)
    camp = _campaign()
    _deals(camp, 1, state="Qualified")                       # 1 sélectionné
    _deals(camp, 8, state="Failed", outcome="wrong_fit")     # 8 rejets

    eff = build_efficiency_analysis(today)
    assert eff["efficacite"] == 5.0
    assert "Efficacité tri faible" in eff["preco_top"]


def test_preco_ras_si_sain(monkeypatch):
    monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "150")
    monkeypatch.delenv("EKOALU_PROFILE_READS_CAP_RAMP", raising=False)
    today = timezone.localdate()
    _read_day(today, {"selection": 20}, 20)   # part recherche 100%, total<90% cap
    camp = _campaign()
    _deals(camp, 7, state="Qualified")        # 35% efficacité, > 15
    eff = build_efficiency_analysis(today)
    assert eff["preco_top"].startswith("RAS")


def test_preco_volume_insuffisant_pas_de_fausse_alerte(monkeypatch):
    """Tôt le matin (peu de lectures), pas d'alerte efficacité sur du bruit."""
    monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "150")
    monkeypatch.delenv("EKOALU_PROFILE_READS_CAP_RAMP", raising=False)
    today = timezone.localdate()
    _read_day(today, {"selection": 2}, 2)     # seulement 2 lectures, 0 sélection
    camp = _campaign()
    eff = build_efficiency_analysis(today)
    # efficacité 0% mais sel<10 → la règle efficacité ne se déclenche PAS
    assert "Efficacité tri faible" not in " ".join(eff["precos"])


def test_preco_apify_hs(monkeypatch):
    """15/07 : actor Apify en panne (echecs sans aucune reussite) = preco n°1."""
    monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "150")
    monkeypatch.delenv("EKOALU_PROFILE_READS_CAP_RAMP", raising=False)
    from ekoalu.apify_enrich.models import ApifyUsageDay
    today = timezone.localdate()
    ApifyUsageDay.objects.create(date=today, count=0, failed=40)
    _read_day(today, {"selection": 20}, 20)
    camp = _campaign()
    _deals(camp, 7, state="Qualified")

    eff = build_efficiency_analysis(today)
    assert eff["apify_failed"] == 40
    assert eff["apify_ok"] == 0
    assert eff["preco_top"].startswith("Apify HS")


def test_preco_apify_pas_d_alerte_si_reussites(monkeypatch):
    """Des echecs partiels avec des reussites = pas de panne, pas d'alerte."""
    monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "150")
    monkeypatch.delenv("EKOALU_PROFILE_READS_CAP_RAMP", raising=False)
    from ekoalu.apify_enrich.models import ApifyUsageDay
    today = timezone.localdate()
    ApifyUsageDay.objects.create(date=today, count=24, failed=16)
    _read_day(today, {"selection": 20}, 20)
    camp = _campaign()
    _deals(camp, 7, state="Qualified")

    eff = build_efficiency_analysis(today)
    assert "Apify HS" not in " ".join(eff["precos"])
