# tests/test_ready_pool.py
import pytest
from unittest.mock import patch

import numpy as np

from linkedin.db.deals import set_profile_state
from linkedin.db.leads import create_enriched_lead, promote_lead_to_deal
from linkedin.ml.qualifier import BayesianQualifier
from linkedin.enums import ProfileState
from linkedin.pipeline.ready_pool import promote_to_ready, find_ready_candidate


SAMPLE_PROFILE = {
    "first_name": "Alice",
    "last_name": "Smith",
    "headline": "Engineer",
    "positions": [{"company_name": "Acme"}],
}


def _make_qualified(session, public_id="alice"):
    url = f"https://www.linkedin.com/in/{public_id}/"
    create_enriched_lead(session, url, SAMPLE_PROFILE)
    promote_lead_to_deal(session, public_id)


@pytest.mark.django_db
class TestPromoteToReady:
    @pytest.fixture(autouse=True)
    def _db(self, db):
        pass

    def test_promotes_above_threshold(self, fake_session):
        _make_qualified(fake_session, "alice")
        _make_qualified(fake_session, "bob")

        scorer = BayesianQualifier(seed=42)

        with patch(
            "crm.models.lead.Lead.get_embedding",
            return_value=np.ones(384),
        ), patch.object(
            scorer, "predict_probs", return_value=np.array([0.95, 0.80]),
        ):
            count = promote_to_ready(fake_session, scorer, threshold=0.9)

        assert count == 1

        from crm.models import Deal
        alice_deal = Deal.objects.get(lead__linkedin_url="https://www.linkedin.com/in/alice/")
        bob_deal = Deal.objects.get(lead__linkedin_url="https://www.linkedin.com/in/bob/")
        assert alice_deal.state == ProfileState.READY_TO_CONNECT
        assert bob_deal.state == ProfileState.QUALIFIED

    def test_returns_zero_on_cold_start(self, fake_session):
        _make_qualified(fake_session)

        scorer = BayesianQualifier(seed=42)

        with patch(
            "crm.models.lead.Lead.get_embedding",
            return_value=np.ones(384),
        ), patch.object(
            scorer, "predict_probs", return_value=None,
        ):
            assert promote_to_ready(fake_session, scorer, threshold=0.9) == 0

    def test_returns_zero_on_empty_pool(self, fake_session):
        scorer = BayesianQualifier(seed=42)
        assert promote_to_ready(fake_session, scorer, threshold=0.9) == 0


@pytest.mark.django_db
class TestGetReadyCandidate:
    @pytest.fixture(autouse=True)
    def _db(self, db):
        pass

    def test_returns_none_when_empty(self, fake_session):
        scorer = BayesianQualifier(seed=42)
        assert find_ready_candidate(fake_session, scorer) is None

    def test_returns_top_ranked(self, fake_session):
        _make_qualified(fake_session, "alice")
        set_profile_state(fake_session, "alice", ProfileState.READY_TO_CONNECT.value)

        scorer = BayesianQualifier(seed=42)
        scorer.rank_profiles = lambda profiles, **kw: profiles

        result = find_ready_candidate(fake_session, scorer)
        assert result is not None
        assert result["public_identifier"] == "alice"


def _open_invitation(public_id, status="pending"):
    from ekoalu.outbound_validation.models import OutboundKind, PendingOutbound

    return PendingOutbound.objects.create(
        prospect_public_id=public_id,
        kind=OutboundKind.INVITATION,
        status=status,
        ai_draft="",
    )


@pytest.mark.django_db
class TestOpenInvitationExcluded:
    """Hub #253 (09/09) : un Deal QUALIFIED dont l'invitation attend Richard ne
    doit plus etre re-promu ni ressorti comme candidat (boucle 2 lectures/tour)."""

    @pytest.fixture(autouse=True)
    def _db(self, db):
        pass

    def test_promote_skips_profile_with_pending_invitation(self, fake_session):
        _make_qualified(fake_session, "alice")
        _make_qualified(fake_session, "bob")
        _open_invitation("alice")

        scorer = BayesianQualifier(seed=42)
        with patch(
            "crm.models.lead.Lead.get_embedding", return_value=np.ones(384),
        ), patch.object(scorer, "predict_probs") as probs:
            probs.side_effect = lambda X: np.full(len(X), 0.99)
            count = promote_to_ready(fake_session, scorer, threshold=0.9)

        assert count == 1
        from crm.models import Deal
        alice = Deal.objects.get(lead__linkedin_url="https://www.linkedin.com/in/alice/")
        bob = Deal.objects.get(lead__linkedin_url="https://www.linkedin.com/in/bob/")
        assert alice.state == ProfileState.QUALIFIED
        assert bob.state == ProfileState.READY_TO_CONNECT

    def test_promote_ignores_closed_invitations(self, fake_session):
        _make_qualified(fake_session, "alice")
        _open_invitation("alice", status="sent")
        _open_invitation("alice", status="rejected")

        scorer = BayesianQualifier(seed=42)
        with patch(
            "crm.models.lead.Lead.get_embedding", return_value=np.ones(384),
        ), patch.object(scorer, "predict_probs", return_value=np.array([0.99])):
            assert promote_to_ready(fake_session, scorer, threshold=0.9) == 1

    def test_find_ready_skips_profile_with_approved_invitation(self, fake_session):
        _make_qualified(fake_session, "alice")
        set_profile_state(fake_session, "alice", ProfileState.READY_TO_CONNECT.value)
        _open_invitation("alice", status="approved")

        scorer = BayesianQualifier(seed=42)
        scorer.rank_profiles = lambda profiles, **kw: profiles
        assert find_ready_candidate(fake_session, scorer) is None

    def test_find_ready_keeps_other_profiles(self, fake_session):
        _make_qualified(fake_session, "alice")
        _make_qualified(fake_session, "bob")
        for pid in ("alice", "bob"):
            set_profile_state(fake_session, pid, ProfileState.READY_TO_CONNECT.value)
        _open_invitation("alice")

        scorer = BayesianQualifier(seed=42)
        scorer.rank_profiles = lambda profiles, **kw: profiles
        result = find_ready_candidate(fake_session, scorer)
        assert result is not None
        assert result["public_identifier"] == "bob"
