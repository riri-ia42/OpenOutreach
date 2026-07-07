# tests/conftest.py
from unittest.mock import patch

import numpy as np
import pytest

from linkedin.management.setup_crm import setup_crm
from tests.factories import UserFactory


@pytest.fixture(autouse=True)
def _ensure_crm_data(db):
    """
    Ensure CRM bootstrap data exists before every test.
    Uses `db` fixture (not transactional_db) for compatibility.
    Since transaction=True tests rollback, we re-create data each time.
    """
    setup_crm()


@pytest.fixture(autouse=True)
def _mock_embeddings(request):
    """Stub fastembed so tests don't need the ONNX model."""
    if "no_embed_mock" in request.keywords:
        yield
    else:
        with patch("linkedin.ml.embeddings.embed_text", return_value=np.ones(384)):
            yield


@pytest.fixture(autouse=True)
def _no_random_days_off(monkeypatch):
    """Desactive les jours off aleatoires LinkedIn (LOT E) pendant les tests.

    La creation de Task passe par compute_human_delay (patch human_scheduler) :
    un jour off tire pour aujourd'hui decalerait scheduled_at d'un jour et
    rendrait la suite non deterministe 1-2 jours par mois."""
    monkeypatch.setenv("EKOALU_RANDOM_DAYS_OFF", "0")


@pytest.fixture(autouse=True)
def _ignore_qualifier_kill_switch():
    """Empeche le sentinel data/qualifier_disabled.flag (kill-switch ingestion
    pose en prod par Richard) de bloquer les tests qui exercent
    run_qualification."""
    with patch("linkedin.pipeline.qualify._qualifier_disabled", return_value=False):
        yield


class FakeAccountSession:
    """Minimal stand-in for AccountSession — exposes django_user + campaign."""

    def __init__(self, django_user, linkedin_profile, campaign):
        self.django_user = django_user
        self.linkedin_profile = linkedin_profile
        self.campaign = campaign

    @property
    def campaigns(self):
        from linkedin.models import Campaign
        return Campaign.objects.filter(users=self.django_user)

    def ensure_browser(self):
        pass


@pytest.fixture
def fake_session(db):
    """An AccountSession-like object backed by the Django test DB."""
    from linkedin.models import Campaign, LinkedInProfile

    user = UserFactory(username="testuser")

    campaign = Campaign.objects.first()
    if campaign is None:
        campaign = Campaign.objects.create(name="LinkedIn Outreach")
    campaign.users.add(user)

    linkedin_profile, _ = LinkedInProfile.objects.get_or_create(
        user=user,
        defaults={
            "linkedin_username": "testuser@example.com",
            "linkedin_password": "testpass",
        },
    )

    return FakeAccountSession(django_user=user, linkedin_profile=linkedin_profile, campaign=campaign)
