"""Tests fiabilité du module outbound_validation."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from ekoalu.outbound_validation import (
    ApprovalMode,
    OutboundKind,
    OutboundStatus,
    PendingOutbound,
    get_approval_mode,
)
from ekoalu.outbound_validation.config import is_approval_required


class TestApprovalMode:
    def test_default_mode_is_require_approval(self, monkeypatch):
        monkeypatch.delenv("EKOALU_APPROVAL_MODE", raising=False)
        assert get_approval_mode() == ApprovalMode.REQUIRE_APPROVAL

    def test_auto_send_explicit(self, monkeypatch):
        monkeypatch.setenv("EKOALU_APPROVAL_MODE", "auto_send")
        assert get_approval_mode() == ApprovalMode.AUTO_SEND

    def test_invalid_value_falls_back_to_require_approval(self, monkeypatch):
        monkeypatch.setenv("EKOALU_APPROVAL_MODE", "invalid_xyz")
        assert get_approval_mode() == ApprovalMode.REQUIRE_APPROVAL

    def test_is_approval_required(self, monkeypatch):
        monkeypatch.setenv("EKOALU_APPROVAL_MODE", "require_approval")
        assert is_approval_required() is True
        monkeypatch.setenv("EKOALU_APPROVAL_MODE", "auto_send")
        assert is_approval_required() is False


@pytest.mark.django_db
class TestPendingOutboundModel:
    def test_create_pending_invitation(self):
        po = PendingOutbound.objects.create(
            prospect_public_id="test-slug",
            kind=OutboundKind.INVITATION,
            ai_draft="Bonjour, je vois que vous travaillez sur le coupe-feu EI60.",
        )
        assert po.status == OutboundStatus.PENDING
        assert po.final_content == ""
        assert po.content_to_send == po.ai_draft

    def test_content_to_send_uses_final_if_edited(self):
        po = PendingOutbound.objects.create(
            prospect_public_id="test-slug",
            kind=OutboundKind.INVITATION,
            ai_draft="Brouillon IA original",
            final_content="Version editee Richard",
        )
        assert po.content_to_send == "Version editee Richard"

    def test_content_to_send_strips_final(self):
        po = PendingOutbound.objects.create(
            prospect_public_id="test-slug",
            kind=OutboundKind.INVITATION,
            ai_draft="brouillon",
            final_content="   ",  # whitespace only → uses draft
        )
        assert po.content_to_send == "brouillon"

    def test_str_representation(self):
        po = PendingOutbound.objects.create(
            prospect_public_id="thierryharo",
            kind=OutboundKind.INVITATION,
            ai_draft="x",
        )
        s = str(po)
        assert "thierryharo" in s
        assert "invitation" in s.lower()


@pytest.mark.django_db
class TestOutboundPatchInvitation:
    """Vérifie que le patch redirige les invitations vers PendingOutbound."""

    def setup_method(self):
        # Reset le patch flag pour pouvoir le ré-appliquer dans les tests
        from ekoalu.outbound_validation import patch as patch_module
        patch_module._PATCH_APPLIED = False

    def test_patch_intercepts_send_connection_request_when_approval_required(
        self, monkeypatch,
    ):
        monkeypatch.setenv("EKOALU_APPROVAL_MODE", "require_approval")

        from ekoalu.outbound_validation.patch import apply_outbound_validation_patch
        apply_outbound_validation_patch()

        from linkedin.actions import connect as connect_module
        from linkedin.enums import ProfileState

        # Fake session + profile
        session = MagicMock()
        session.campaign = MagicMock()
        session.campaign.pk = 42
        session.campaign.name = "EKOALU - Test"
        profile = {"public_identifier": "test-prospect", "urn": "urn:li:fsd_profile:abc"}

        result = connect_module.send_connection_request(session, profile)

        # Doit retourner QUALIFIED (pas PENDING — l'invitation n'est pas envoyée)
        assert result == ProfileState.QUALIFIED

        # Et créer une PendingOutbound
        po = PendingOutbound.objects.filter(prospect_public_id="test-prospect").first()
        assert po is not None
        assert po.kind == OutboundKind.INVITATION
        assert po.status == OutboundStatus.PENDING
        assert po.campaign_id == 42
        assert po.campaign_name == "EKOALU - Test"
        assert po.prospect_urn == "urn:li:fsd_profile:abc"

    def test_patch_bypassed_when_auto_send(self, monkeypatch):
        monkeypatch.setenv("EKOALU_APPROVAL_MODE", "auto_send")

        from ekoalu.outbound_validation.patch import apply_outbound_validation_patch
        apply_outbound_validation_patch()

        # En mode auto_send, le patch doit appeler la fonction originale
        # On vérifie qu'aucune PendingOutbound n'est créée
        from linkedin.actions import connect as connect_module

        session = MagicMock()
        profile = {"public_identifier": "auto-send-test", "urn": "urn:test"}

        # Mock la fonction originale pour éviter d'appeler le vrai LinkedIn
        with patch.object(
            connect_module, "_connect_direct", return_value=False,
        ), patch.object(
            connect_module, "_connect_via_more", return_value=False,
        ), patch.object(
            connect_module, "dump_page_html", return_value=None,
        ):
            connect_module.send_connection_request(session, profile)

        # Aucune PendingOutbound car mode auto_send
        po = PendingOutbound.objects.filter(prospect_public_id="auto-send-test").first()
        assert po is None


@pytest.mark.django_db
class TestOutboundPatchMessage:
    def setup_method(self):
        from ekoalu.outbound_validation import patch as patch_module
        patch_module._PATCH_APPLIED = False

    def test_patch_intercepts_send_raw_message_when_approval_required(
        self, monkeypatch,
    ):
        monkeypatch.setenv("EKOALU_APPROVAL_MODE", "require_approval")

        from ekoalu.outbound_validation.patch import apply_outbound_validation_patch
        apply_outbound_validation_patch()

        from linkedin.actions import message as message_module

        session = MagicMock()
        session.campaign = None
        profile = {"public_identifier": "msg-target", "urn": "urn:li:test"}
        msg = "Hello, je voulais te partager une fiche technique sur EI60."

        result = message_module.send_raw_message(session, profile, msg)

        # Retourne False pour qu'OpenOutreach pense que l'envoi a échoué
        assert result is False

        po = PendingOutbound.objects.filter(prospect_public_id="msg-target").first()
        assert po is not None
        assert po.kind == OutboundKind.FOLLOW_UP
        assert po.ai_draft == msg
        assert po.status == OutboundStatus.PENDING


@pytest.mark.django_db
class TestOutboundIdempotentPatch:
    def test_patch_appliquee_une_seule_fois(self):
        from ekoalu.outbound_validation import patch as patch_module

        # Reset
        patch_module._PATCH_APPLIED = False
        from linkedin.actions import connect as connect_module
        original = connect_module.send_connection_request

        # 1ère application
        patch_module.apply_outbound_validation_patch()
        patched = connect_module.send_connection_request
        assert patched is not original
        assert patch_module._PATCH_APPLIED is True

        # 2e application : ne doit rien changer (pas de double-wrap)
        patch_module.apply_outbound_validation_patch()
        assert connect_module.send_connection_request is patched
