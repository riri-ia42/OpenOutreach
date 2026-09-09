"""Tests du helper dedup (hub #253) : statuts ouverts vs clos, filtre kind."""
from __future__ import annotations

import pytest

from ekoalu.outbound_validation.dedup import has_open_invitation, public_ids_with_open_outbound
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound


def _po(pid, kind=OutboundKind.INVITATION, status=OutboundStatus.PENDING):
    return PendingOutbound.objects.create(prospect_public_id=pid, kind=kind, status=status, ai_draft="")


@pytest.mark.django_db
class TestOpenOutbound:
    def test_open_statuses_detected(self):
        _po("a", status=OutboundStatus.PENDING)
        _po("b", status=OutboundStatus.APPROVED)
        _po("c", status=OutboundStatus.BLOCKED_COMPANY)
        assert public_ids_with_open_outbound(["a", "b", "c", "d"], OutboundKind.INVITATION) == {"a", "b", "c"}

    def test_closed_statuses_ignored(self):
        for st in (OutboundStatus.SENT, OutboundStatus.REJECTED, OutboundStatus.EXPIRED,
                   OutboundStatus.FAILED, OutboundStatus.SENDING):
            _po("x", status=st)
        assert not has_open_invitation("x")

    def test_kind_filter(self):
        _po("a", kind=OutboundKind.FOLLOW_UP)
        assert not has_open_invitation("a")
        assert public_ids_with_open_outbound(["a"], OutboundKind.FOLLOW_UP) == {"a"}

    def test_empty_input(self):
        assert public_ids_with_open_outbound([], OutboundKind.INVITATION) == set()
        assert public_ids_with_open_outbound(["", None], OutboundKind.INVITATION) == set()
