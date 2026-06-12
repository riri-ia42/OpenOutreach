"""Tests du snapshot de fiche LinkedIn (Lead.profile_snapshot).

Décision Richard 12/06 : « on a toutes les infos, on peut juger » — la fiche
est stockée à la 1re lecture, les accès suivants (verdict LLM, résumé
follow-up) ne relisent JAMAIS LinkedIn.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone


@pytest.fixture
def lead(db):
    from crm.models import Lead
    return Lead.objects.create(
        linkedin_url="https://www.linkedin.com/in/snap-test",
        public_identifier="snap-test",
    )


PROFILE = {"public_identifier": "snap-test", "headline": "Conducteur de travaux",
           "urn": "urn:li:fsd_profile:SNAP1"}


def _mock_api(profile=PROFILE):
    api = MagicMock()
    api.get_profile.return_value = (profile, {"raw": True})
    return api


@pytest.mark.django_db
class TestProfileSnapshot:
    def test_premiere_lecture_scrape_et_stocke(self, lead):
        api = _mock_api()
        with patch("linkedin.api.client.PlaywrightLinkedinAPI", return_value=api):
            out = lead.get_profile(session=MagicMock())
        assert out["headline"] == "Conducteur de travaux"
        lead.refresh_from_db()
        assert lead.profile_snapshot["headline"] == "Conducteur de travaux"
        assert lead.profile_snapshot_at is not None
        api.get_profile.assert_called_once()

    def test_lectures_suivantes_servies_depuis_la_db(self, lead):
        lead.profile_snapshot = dict(PROFILE)
        lead.profile_snapshot_at = timezone.now()
        lead.save()
        api = _mock_api()
        with patch("linkedin.api.client.PlaywrightLinkedinAPI", return_value=api):
            out = lead.get_profile(session=MagicMock())
        assert out["headline"] == "Conducteur de travaux"
        api.get_profile.assert_not_called()  # ZERO lecture LinkedIn

    def test_refresh_force_une_relecture(self, lead):
        lead.profile_snapshot = dict(PROFILE)
        lead.profile_snapshot_at = timezone.now()
        lead.save()
        api = _mock_api()
        with patch("linkedin.api.client.PlaywrightLinkedinAPI", return_value=api):
            lead.get_profile(session=MagicMock(), refresh=True)
        api.get_profile.assert_called_once()

    def test_snapshot_perime_est_relu(self, lead):
        lead.profile_snapshot = dict(PROFILE)
        lead.profile_snapshot_at = timezone.now() - timedelta(days=45)
        lead.save()
        api = _mock_api()
        with patch("linkedin.api.client.PlaywrightLinkedinAPI", return_value=api):
            lead.get_profile(session=MagicMock())
        api.get_profile.assert_called_once()

    def test_create_enriched_lead_stocke_le_snapshot(self, db):
        from crm.models import Lead
        from linkedin.db.leads import create_enriched_lead

        pk = create_enriched_lead(
            session=MagicMock(),
            url="https://www.linkedin.com/in/enrich-snap",
            profile={"public_identifier": "enrich-snap", "headline": "Métreur",
                     "urn": "urn:li:fsd_profile:ENR1"},
        )
        lead = Lead.objects.get(pk=pk)
        assert lead.profile_snapshot["headline"] == "Métreur"
        assert lead.profile_snapshot_at is not None

    def test_verdict_llm_n_utilise_pas_linkedin_si_snapshot(self, lead):
        """_fetch_profile_text (le texte donné au juge Claude) lit le snapshot."""
        from linkedin.pipeline.qualify import _fetch_profile_text

        lead.profile_snapshot = dict(PROFILE)
        lead.profile_snapshot_at = timezone.now()
        lead.save()
        api = _mock_api()
        with patch("linkedin.api.client.PlaywrightLinkedinAPI", return_value=api):
            text = _fetch_profile_text(MagicMock(), lead.pk, lead.public_identifier)
        assert text and "conducteur de travaux" in text.lower()
        api.get_profile.assert_not_called()
