"""Tests du chemin Apify-first dans _embed_urlonly_leads (cablage 07/07).

Quand Apify est pret (token + kill-switch + plafond), l'embed des leads
URL-only ne fait AUCUNE lecture Voyager (get_profile/get_embedding jamais
appeles -> cap read_guard intact). Repli Voyager sur echec Apify ;
comportement d'origine si Apify non configure.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from ekoalu.apify_enrich import client
from linkedin.pipeline.qualify import fetch_qualification_candidates


@pytest.fixture
def campaign(db):
    from linkedin.models import Campaign

    return Campaign.objects.create(name="EKOALU - ABM - ApifyFirst")


@pytest.fixture(autouse=True)
def _no_pacing_sleep():
    with patch("linkedin.pipeline.qualify.time.sleep"):
        yield


@pytest.fixture(autouse=True)
def _embed_mock():
    with patch("linkedin.ml.embeddings.embed_text",
               return_value=np.ones(384, dtype=np.float32)):
        yield


def _mk_lead(pid: str):
    from crm.models import Lead

    return Lead.objects.create(
        public_identifier=pid,
        linkedin_url=f"https://www.linkedin.com/in/{pid}/",
    )


def _payload(leads) -> list[dict]:
    return [
        {"lead_id": ld.pk, "public_identifier": ld.public_identifier,
         "url": ld.linkedin_url, "meta": {}}
        for ld in leads
    ]


def _actor_item(pid: str) -> dict:
    return {
        "linkedinUrl": f"https://www.linkedin.com/in/{pid}/",
        "publicIdentifier": pid,
        "firstName": "Jean",
        "lastName": pid,
        "headline": "Gerant metallerie",
    }


def _fake_get_embedding(self, session):
    emb = np.ones(384, dtype=np.float32)
    self.embedding = emb.tobytes()
    self.save(update_fields=["embedding"])
    return emb


@pytest.mark.django_db
def test_apify_first_zero_lecture_voyager(monkeypatch, campaign):
    """Apify configure : embed via Apify, AUCUN appel get_profile/get_embedding
    (le patch read_guard n'est jamais traverse, cap lectures intact)."""
    from crm.models import Lead

    monkeypatch.setenv("EKOALU_APIFY_TOKEN", "tok-123")
    urlonly = [_mk_lead(f"serper-{i}") for i in range(2)]
    session = SimpleNamespace(campaign=campaign)

    with (
        patch("linkedin.db.leads.get_leads_for_qualification",
              return_value=_payload(urlonly)),
        patch.object(client, "run_profile_scraper",
                     side_effect=lambda urls: [
                         _actor_item(u.rstrip("/").rsplit("/", 1)[-1])
                         for u in urls]),
        patch.object(Lead, "get_embedding") as voyager_embed,
        patch.object(Lead, "get_profile") as voyager_profile,
    ):
        candidates = fetch_qualification_candidates(session)

    voyager_embed.assert_not_called()
    voyager_profile.assert_not_called()
    pids = {c.public_identifier for c in candidates}
    assert pids == {"serper-0", "serper-1"}
    for lead in urlonly:
        lead.refresh_from_db()
        assert lead.profile_snapshot["source"] == "apify"
        assert lead.embedding is not None


@pytest.mark.django_db
def test_repli_voyager_sur_echec_apify(monkeypatch, campaign):
    """Echec Apify (reseau/acteur) : le lead est embedde par le chemin
    Voyager historique, le cycle continue."""
    from crm.models import Lead

    monkeypatch.setenv("EKOALU_APIFY_TOKEN", "tok-123")
    lead = _mk_lead("serper-ko")
    session = SimpleNamespace(campaign=campaign)

    with (
        patch("linkedin.db.leads.get_leads_for_qualification",
              return_value=_payload([lead])),
        patch.object(client, "run_profile_scraper",
                     side_effect=RuntimeError("Apify HTTP 500")),
        patch.object(Lead, "get_embedding", _fake_get_embedding),
    ):
        candidates = fetch_qualification_candidates(session)

    assert [c.public_identifier for c in candidates] == ["serper-ko"]
    lead.refresh_from_db()
    assert lead.embedding is not None       # pose par le repli Voyager
    assert lead.profile_snapshot is None    # Apify n'a rien ecrit


@pytest.mark.django_db
def test_comportement_origine_si_apify_non_configure(campaign):
    """Sans token : chemin Voyager historique, AUCUN appel Apify."""
    from crm.models import Lead

    lead = _mk_lead("serper-a")
    session = SimpleNamespace(campaign=campaign)

    with (
        patch("linkedin.db.leads.get_leads_for_qualification",
              return_value=_payload([lead])),
        patch.object(client, "run_profile_scraper") as apify_run,
        patch.object(Lead, "get_embedding", _fake_get_embedding),
    ):
        candidates = fetch_qualification_candidates(session)

    apify_run.assert_not_called()
    assert [c.public_identifier for c in candidates] == ["serper-a"]


@pytest.mark.django_db
def test_plafond_apify_atteint_repli_voyager(monkeypatch, campaign):
    """Plafond quotidien Apify consomme : repli Voyager sans appel API."""
    from crm.models import Lead
    from ekoalu.apify_enrich import service

    monkeypatch.setenv("EKOALU_APIFY_TOKEN", "tok-123")
    monkeypatch.setenv("EKOALU_APIFY_DAILY_CAP", "1")
    service.record_usage(1)   # plafond du jour deja consomme
    lead = _mk_lead("serper-cap")
    session = SimpleNamespace(campaign=campaign)

    with (
        patch("linkedin.db.leads.get_leads_for_qualification",
              return_value=_payload([lead])),
        patch.object(client, "run_profile_scraper") as apify_run,
        patch.object(Lead, "get_embedding", _fake_get_embedding),
    ):
        candidates = fetch_qualification_candidates(session)

    apify_run.assert_not_called()
    assert [c.public_identifier for c in candidates] == ["serper-cap"]


@pytest.mark.django_db
def test_kill_switch_apify_repli_voyager(monkeypatch, campaign):
    """EKOALU_APIFY_ENRICH=0 : chemin Voyager, aucun appel Apify."""
    from crm.models import Lead

    monkeypatch.setenv("EKOALU_APIFY_TOKEN", "tok-123")
    monkeypatch.setenv("EKOALU_APIFY_ENRICH", "0")
    lead = _mk_lead("serper-off")
    session = SimpleNamespace(campaign=campaign)

    with (
        patch("linkedin.db.leads.get_leads_for_qualification",
              return_value=_payload([lead])),
        patch.object(client, "run_profile_scraper") as apify_run,
        patch.object(Lead, "get_embedding", _fake_get_embedding),
    ):
        candidates = fetch_qualification_candidates(session)

    apify_run.assert_not_called()
    assert [c.public_identifier for c in candidates] == ["serper-off"]
