"""Tests fiabilité du module company_validation."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ekoalu.company_validation.config import is_company_validation_enabled
from ekoalu.company_validation.models import (
    ApprovedCompany,
    CompanyStatus,
    _normalize_company_name,
)


class TestNormalize:
    def test_lowercase(self):
        assert _normalize_company_name("BONGLET") == "bonglet"

    def test_strip_sas(self):
        assert _normalize_company_name("Mattana SAS") == "mattana"

    def test_strip_sarl(self):
        assert _normalize_company_name("X SARL") == "x"

    def test_strip_entreprise_prefix(self):
        assert _normalize_company_name("Entreprise Bonglet") == "bonglet"

    def test_accents(self):
        assert _normalize_company_name("Société") == "societe"

    def test_empty(self):
        assert _normalize_company_name("") == ""
        assert _normalize_company_name(None or "") == ""


class TestConfig:
    def test_default_enabled(self, monkeypatch):
        monkeypatch.delenv("EKOALU_COMPANY_VALIDATION", raising=False)
        assert is_company_validation_enabled() is True

    def test_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("EKOALU_COMPANY_VALIDATION", "off")
        assert is_company_validation_enabled() is False

    def test_other_values_enabled(self, monkeypatch):
        monkeypatch.setenv("EKOALU_COMPANY_VALIDATION", "on")
        assert is_company_validation_enabled() is True


@pytest.mark.django_db
class TestApprovedCompanyModel:
    def test_is_approved_match(self):
        ApprovedCompany.objects.create(name="Bonglet", status=CompanyStatus.APPROVED)
        assert ApprovedCompany.is_approved("Bonglet") is True
        assert ApprovedCompany.is_approved("bonglet") is True
        assert ApprovedCompany.is_approved("BONGLET SAS") is True

    def test_is_approved_no_match(self):
        assert ApprovedCompany.is_approved("Unknown") is False
        assert ApprovedCompany.is_approved("") is False

    def test_is_rejected(self):
        ApprovedCompany.objects.create(name="BadCorp", status=CompanyStatus.REJECTED)
        assert ApprovedCompany.is_rejected("BadCorp") is True
        assert ApprovedCompany.is_rejected("badcorp SAS") is True

    def test_pending_not_approved(self):
        ApprovedCompany.objects.create(name="WaitMe", status=CompanyStatus.PENDING)
        assert ApprovedCompany.is_approved("WaitMe") is False
        assert ApprovedCompany.is_rejected("WaitMe") is False

    def test_find_or_create_pending_new(self):
        obj = ApprovedCompany.find_or_create_pending("NewCo")
        assert obj.status == CompanyStatus.PENDING
        assert obj.name == "NewCo"

    def test_find_or_create_pending_idempotent(self):
        ApprovedCompany.find_or_create_pending("DupeCo")
        ApprovedCompany.find_or_create_pending("dupeco")
        ApprovedCompany.find_or_create_pending("DUPECO SAS")
        # Tous matchent sur le même nom normalisé
        assert ApprovedCompany.objects.filter(name_normalized="dupeco").count() == 1


@pytest.mark.django_db
class TestPatchAvecValidationEntreprise:
    def setup_method(self):
        from ekoalu.outbound_validation import patch as patch_module
        patch_module._PATCH_APPLIED = False

    def test_company_approuvee_passe_en_pending(self, monkeypatch):
        monkeypatch.setenv("EKOALU_APPROVAL_MODE", "require_approval")
        monkeypatch.delenv("EKOALU_COMPANY_VALIDATION", raising=False)

        ApprovedCompany.objects.create(name="GoodCorp", status=CompanyStatus.APPROVED)

        from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound
        from ekoalu.outbound_validation.patch import apply_outbound_validation_patch
        apply_outbound_validation_patch()

        from linkedin.actions import connect as connect_module

        session = MagicMock()
        campaign_mock = MagicMock()
        campaign_mock.pk = 1
        campaign_mock.name = "EKOALU - Test"
        session.campaign = campaign_mock
        profile = {
            "public_identifier": "test-good",
            "urn": "urn:li:test",
            "company": "GoodCorp",
        }
        connect_module.send_connection_request(session, profile)

        po = PendingOutbound.objects.filter(prospect_public_id="test-good").first()
        assert po is not None
        assert po.status == OutboundStatus.PENDING
        assert po.prospect_company == "GoodCorp"

    def test_company_inconnue_bloque_message(self, monkeypatch):
        monkeypatch.setenv("EKOALU_APPROVAL_MODE", "require_approval")
        monkeypatch.delenv("EKOALU_COMPANY_VALIDATION", raising=False)

        from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound
        from ekoalu.outbound_validation.patch import apply_outbound_validation_patch
        apply_outbound_validation_patch()

        from linkedin.actions import connect as connect_module

        session = MagicMock()
        campaign_mock = MagicMock()
        campaign_mock.pk = 2
        campaign_mock.name = "EKOALU - Test2"
        session.campaign = campaign_mock
        profile = {
            "public_identifier": "test-unknown",
            "urn": "urn:li:test2",
            "company": "UnknownCo",
        }
        connect_module.send_connection_request(session, profile)

        po = PendingOutbound.objects.filter(prospect_public_id="test-unknown").first()
        assert po is not None
        assert po.status == OutboundStatus.BLOCKED_COMPANY

        # ApprovedCompany pending crée automatiquement
        ac = ApprovedCompany.objects.filter(name_normalized="unknownco").first()
        assert ac is not None
        assert ac.status == CompanyStatus.PENDING

    def test_company_rejected_skip(self, monkeypatch):
        monkeypatch.setenv("EKOALU_APPROVAL_MODE", "require_approval")
        monkeypatch.delenv("EKOALU_COMPANY_VALIDATION", raising=False)

        ApprovedCompany.objects.create(name="BadCorp", status=CompanyStatus.REJECTED)

        from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound
        from ekoalu.outbound_validation.patch import apply_outbound_validation_patch
        apply_outbound_validation_patch()

        from linkedin.actions import connect as connect_module

        session = MagicMock()
        campaign_mock = MagicMock()
        campaign_mock.pk = 3
        campaign_mock.name = "EKOALU - Test3"
        session.campaign = campaign_mock
        profile = {
            "public_identifier": "test-bad",
            "urn": "urn:li:test3",
            "company": "BadCorp",
        }
        connect_module.send_connection_request(session, profile)

        po = PendingOutbound.objects.filter(prospect_public_id="test-bad").first()
        assert po is not None
        assert po.status == OutboundStatus.REJECTED
        assert "BadCorp" in po.rejection_reason

    def test_validation_disabled_bypass(self, monkeypatch):
        """Quand EKOALU_COMPANY_VALIDATION=off, on bypass la validation entreprise."""
        monkeypatch.setenv("EKOALU_APPROVAL_MODE", "require_approval")
        monkeypatch.setenv("EKOALU_COMPANY_VALIDATION", "off")

        from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound
        from ekoalu.outbound_validation.patch import apply_outbound_validation_patch
        apply_outbound_validation_patch()

        from linkedin.actions import connect as connect_module

        session = MagicMock()
        campaign_mock = MagicMock()
        campaign_mock.pk = 4
        campaign_mock.name = "EKOALU - Test4"
        session.campaign = campaign_mock
        profile = {
            "public_identifier": "test-bypass",
            "urn": "urn:li:test4",
            "company": "AnyCorp",
        }
        connect_module.send_connection_request(session, profile)

        po = PendingOutbound.objects.filter(prospect_public_id="test-bypass").first()
        assert po is not None
        # En mode off, le message va en PENDING direct (pas de blocage entreprise)
        assert po.status == OutboundStatus.PENDING
