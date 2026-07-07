"""Tests priorite d'embedding des leads URL-only (Serper) a la qualification.

P0 07/07 : avant, un lead URL-only n'etait embedde QUE si le pool embedde de
la campagne etait vide — comme le pool est refill en continu, les leads Serper
n'etaient JAMAIS lus. Desormais on embedde d'abord jusqu'a N leads URL-only
par cycle (env EKOALU_URLONLY_EMBED_PER_CYCLE, defaut 2).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from linkedin.pipeline.qualify import fetch_qualification_candidates


@pytest.fixture
def campaign(db):
    from linkedin.models import Campaign

    return Campaign.objects.create(name="EKOALU - ABM - PrioTest")


@pytest.fixture(autouse=True)
def _no_pacing_sleep():
    """LOT C : une pause 20-45s sépare deux embeds consécutifs — neutralisée
    dans les tests (le test dédié ci-dessous vérifie qu'elle est bien tirée)."""
    with patch("linkedin.pipeline.qualify.time.sleep") as mock_sleep:
        yield mock_sleep


def _mk_lead(pid: str, embedded: bool):
    from crm.models import Lead

    kwargs = {}
    if embedded:
        kwargs["embedding"] = np.ones(384, dtype=np.float32).tobytes()
    return Lead.objects.create(
        public_identifier=pid,
        linkedin_url=f"https://www.linkedin.com/in/{pid}/",
        **kwargs,
    )


def _payload(leads) -> list[dict]:
    return [
        {"lead_id": ld.pk, "public_identifier": ld.public_identifier,
         "url": ld.linkedin_url, "meta": {}}
        for ld in leads
    ]


def _fake_get_embedding(self, session):
    emb = np.ones(384, dtype=np.float32)
    self.embedding = emb.tobytes()
    self.save(update_fields=["embedding"])
    return emb


@pytest.mark.django_db
def test_urlonly_embeddes_en_priorite_meme_pool_non_vide(monkeypatch, campaign):
    """Le pool embedde n'affame plus les leads Serper : N URL-only embeddes
    par cycle AVANT de servir le pool."""
    from crm.models import Lead

    monkeypatch.delenv("EKOALU_URLONLY_EMBED_PER_CYCLE", raising=False)
    pool = [_mk_lead(f"embedde-{i}", True) for i in range(3)]
    urlonly = [_mk_lead(f"serper-{i}", False) for i in range(3)]
    session = SimpleNamespace(campaign=campaign)

    with (
        patch("linkedin.db.leads.get_leads_for_qualification",
              return_value=_payload(pool + urlonly)),
        patch.object(Lead, "get_embedding", _fake_get_embedding),
    ):
        candidates = fetch_qualification_candidates(session)

    pids = {c.public_identifier for c in candidates}
    assert {"serper-0", "serper-1"} <= pids       # N=2 par defaut, en priorite
    assert "serper-2" not in pids                 # au-dela de N : prochain cycle
    assert {f"embedde-{i}" for i in range(3)} <= pids   # pool toujours servi


@pytest.mark.django_db
def test_urlonly_n_configurable_par_env(monkeypatch, campaign):
    from crm.models import Lead

    monkeypatch.setenv("EKOALU_URLONLY_EMBED_PER_CYCLE", "1")
    _mk_lead("embedde-a", True)
    urlonly = [_mk_lead(f"serper-{i}", False) for i in range(2)]
    session = SimpleNamespace(campaign=campaign)

    with (
        patch("linkedin.db.leads.get_leads_for_qualification",
              return_value=_payload(list(Lead.objects.all()))),
        patch.object(Lead, "get_embedding", _fake_get_embedding),
    ):
        candidates = fetch_qualification_candidates(session)

    pids = {c.public_identifier for c in candidates}
    assert "serper-0" in pids
    assert "serper-1" not in pids
    assert urlonly[1].pk not in {c.pk for c in candidates}


@pytest.mark.django_db
def test_urlonly_desactive_avec_n_zero(monkeypatch, campaign):
    from crm.models import Lead

    monkeypatch.setenv("EKOALU_URLONLY_EMBED_PER_CYCLE", "0")
    _mk_lead("embedde-a", True)
    _mk_lead("serper-a", False)
    session = SimpleNamespace(campaign=campaign)

    with (
        patch("linkedin.db.leads.get_leads_for_qualification",
              return_value=_payload(list(Lead.objects.all()))),
        patch.object(Lead, "get_embedding", _fake_get_embedding),
    ):
        candidates = fetch_qualification_candidates(session)

    assert [c.public_identifier for c in candidates] == ["embedde-a"]


@pytest.mark.django_db
def test_pause_20_45s_entre_deux_embeds_consecutifs(monkeypatch, campaign, _no_pacing_sleep):
    """LOT C : deux lectures Voyager d'une même task ne partent jamais
    back-to-back — pause tirée dans [enrich_min, enrich_max] (20-45s)."""
    from crm.models import Lead
    from linkedin.conf import CAMPAIGN_CONFIG

    monkeypatch.delenv("EKOALU_URLONLY_EMBED_PER_CYCLE", raising=False)
    urlonly = [_mk_lead(f"serper-{i}", False) for i in range(2)]
    session = SimpleNamespace(campaign=campaign)

    with (
        patch("linkedin.db.leads.get_leads_for_qualification",
              return_value=_payload(urlonly)),
        patch.object(Lead, "get_embedding", _fake_get_embedding),
    ):
        fetch_qualification_candidates(session)

    # 2 embeds => exactement 1 pause, entre les deux (pas avant le premier)
    assert _no_pacing_sleep.call_count == 1
    pause = _no_pacing_sleep.call_args[0][0]
    assert CAMPAIGN_CONFIG["enrich_min_delay_seconds"] <= pause <= CAMPAIGN_CONFIG["enrich_max_delay_seconds"]
    assert CAMPAIGN_CONFIG["enrich_min_delay_seconds"] >= 20


@pytest.mark.django_db
def test_cap_lectures_stoppe_l_embed_sans_casser_le_cycle(monkeypatch, campaign):
    """ReadCapExceededError pendant l'embed = on sert le pool deja embedde."""
    from crm.models import Lead
    from ekoalu.read_guard.guard import ReadCapExceededError

    monkeypatch.delenv("EKOALU_URLONLY_EMBED_PER_CYCLE", raising=False)
    _mk_lead("embedde-a", True)
    _mk_lead("serper-a", False)
    session = SimpleNamespace(campaign=campaign)

    def boom(self, session):
        raise ReadCapExceededError("cap atteint")

    with (
        patch("linkedin.db.leads.get_leads_for_qualification",
              return_value=_payload(list(Lead.objects.all()))),
        patch.object(Lead, "get_embedding", boom),
    ):
        candidates = fetch_qualification_candidates(session)

    assert [c.public_identifier for c in candidates] == ["embedde-a"]
