"""Tests des vues EKOALU (smoke tests d'accès + contenu)."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse


@pytest.fixture
def staff_user(db):
    User = get_user_model()
    user = User.objects.create_user(
        username="testadmin", password="testpwd123", is_staff=True,
    )
    return user


@pytest.fixture
def client_logged(staff_user):
    c = Client()
    c.login(username="testadmin", password="testpwd123")
    return c


@pytest.mark.django_db
class TestDashboardView:
    def test_anonymous_redirect(self):
        c = Client()
        r = c.get(reverse("ekoalu:dashboard"))
        assert r.status_code in (302, 301)  # redirect login

    def test_logged_user_can_access(self, client_logged):
        r = client_logged.get(reverse("ekoalu:dashboard"))
        assert r.status_code == 200
        assert b"EKOALU" in r.content
        assert b"Prospection" in r.content


@pytest.mark.django_db
class TestCampaignsViews:
    def test_campaigns_list_accessible(self, client_logged):
        r = client_logged.get(reverse("ekoalu:campaigns_list"))
        assert r.status_code == 200

    def test_campaign_detail_404_si_inexistant(self, client_logged):
        r = client_logged.get(reverse("ekoalu:campaign_detail", args=[99999]))
        assert r.status_code == 404

    def test_campaign_detail_accessible_si_existant(self, client_logged):
        from linkedin.models import Campaign
        camp = Campaign.objects.create(name="Test - campaign for view")
        r = client_logged.get(reverse("ekoalu:campaign_detail", args=[camp.pk]))
        assert r.status_code == 200
        assert b"Test - campaign for view" in r.content

    def test_pause_campaign(self, client_logged):
        from linkedin.models import Campaign
        camp = Campaign.objects.create(name="Test pause", action_fraction=1.0)
        r = client_logged.post(
            reverse("ekoalu:campaign_detail", args=[camp.pk]),
            data={"action": "pause"},
        )
        assert r.status_code in (302, 303)
        camp.refresh_from_db()
        assert camp.action_fraction == 0.0

    def test_resume_campaign(self, client_logged):
        from linkedin.models import Campaign
        camp = Campaign.objects.create(name="Test resume", action_fraction=0.0)
        r = client_logged.post(
            reverse("ekoalu:campaign_detail", args=[camp.pk]),
            data={"action": "resume"},
        )
        assert r.status_code in (302, 303)
        camp.refresh_from_db()
        assert camp.action_fraction == 1.0

    def test_save_campaign_params(self, client_logged):
        from linkedin.models import Campaign
        camp = Campaign.objects.create(name="Test save")
        r = client_logged.post(
            reverse("ekoalu:campaign_detail", args=[camp.pk]),
            data={
                "action": "save",
                "product_docs": "Nouveau descriptif produit",
                "campaign_objective": "Nouvel objectif",
                "booking_link": "https://example.com/book",
            },
        )
        assert r.status_code in (302, 303)
        camp.refresh_from_db()
        assert camp.product_docs == "Nouveau descriptif produit"
        assert camp.campaign_objective == "Nouvel objectif"
        assert camp.booking_link == "https://example.com/book"


@pytest.mark.django_db
class TestOutboundViews:
    def test_outbound_list_accessible(self, client_logged):
        r = client_logged.get(reverse("ekoalu:outbound_list"))
        assert r.status_code == 200

    def test_outbound_detail_404(self, client_logged):
        r = client_logged.get(reverse("ekoalu:outbound_detail", args=[99999]))
        assert r.status_code == 404

    def test_outbound_approve(self, client_logged):
        from ekoalu.outbound_validation.models import (
            OutboundKind,
            OutboundStatus,
            PendingOutbound,
        )
        po = PendingOutbound.objects.create(
            prospect_public_id="test",
            kind=OutboundKind.INVITATION,
            ai_draft="draft",
        )
        r = client_logged.post(
            reverse("ekoalu:outbound_detail", args=[po.pk]),
            data={"action": "approve", "final_content": ""},
        )
        assert r.status_code in (302, 303)
        po.refresh_from_db()
        assert po.status == OutboundStatus.APPROVED
        assert po.approved_at is not None

    def test_outbound_reject(self, client_logged):
        from ekoalu.outbound_validation.models import (
            OutboundKind,
            OutboundStatus,
            PendingOutbound,
        )
        po = PendingOutbound.objects.create(
            prospect_public_id="test",
            kind=OutboundKind.INVITATION,
            ai_draft="draft",
        )
        r = client_logged.post(
            reverse("ekoalu:outbound_detail", args=[po.pk]),
            data={
                "action": "reject",
                "rejection_reason": "pas pertinent",
            },
        )
        assert r.status_code in (302, 303)
        po.refresh_from_db()
        assert po.status == OutboundStatus.REJECTED
        assert po.rejection_reason == "pas pertinent"


@pytest.mark.django_db
class TestLeadsAddView:
    def test_leads_add_get(self, client_logged):
        r = client_logged.get(reverse("ekoalu:leads_add"))
        assert r.status_code == 200
        assert b"prospects" in r.content.lower()

    def test_leads_add_missing_campaign(self, client_logged):
        r = client_logged.post(
            reverse("ekoalu:leads_add"),
            data={"urls": "https://www.linkedin.com/in/test/"},
        )
        assert r.status_code == 200  # re-render avec error
        assert b"Choisis" in r.content

    def test_leads_add_missing_urls(self, client_logged):
        from linkedin.models import Campaign
        camp = Campaign.objects.create(name="EKOALU - test")
        r = client_logged.post(
            reverse("ekoalu:leads_add"),
            data={"campaign_id": camp.pk, "urls": ""},
        )
        assert r.status_code == 200

    def test_leads_add_success_creates_lead(self, client_logged):
        from linkedin.models import Campaign
        from crm.models import Lead, Deal
        camp = Campaign.objects.create(name="EKOALU - test add")
        urls = "https://www.linkedin.com/in/test-prospect-add-1/\nhttps://www.linkedin.com/in/test-prospect-add-2/"
        r = client_logged.post(
            reverse("ekoalu:leads_add"),
            data={"campaign_id": camp.pk, "urls": urls},
        )
        assert r.status_code in (302, 303)
        assert Lead.objects.filter(public_identifier="test-prospect-add-1").exists()
        assert Lead.objects.filter(public_identifier="test-prospect-add-2").exists()
        assert Deal.objects.filter(campaign=camp).count() == 2

    def test_leads_add_idempotent(self, client_logged):
        from linkedin.models import Campaign
        from crm.models import Lead, Deal
        camp = Campaign.objects.create(name="EKOALU - test idem")
        url = "https://www.linkedin.com/in/test-idem-prospect/"
        # 1ère fois
        client_logged.post(reverse("ekoalu:leads_add"),
                           data={"campaign_id": camp.pk, "urls": url})
        # 2e fois (doit pas dupliquer)
        client_logged.post(reverse("ekoalu:leads_add"),
                           data={"campaign_id": camp.pk, "urls": url})
        assert Deal.objects.filter(campaign=camp).count() == 1


@pytest.mark.django_db
class TestLeadDetailView:
    def test_lead_detail_404(self, client_logged):
        r = client_logged.get(reverse("ekoalu:lead_detail", args=["inexistant-slug"]))
        assert r.status_code == 404

    def test_lead_detail_accessible(self, client_logged):
        from crm.models import Lead
        Lead.objects.create(
            public_identifier="test-detail-slug",
            linkedin_url="https://www.linkedin.com/in/test-detail-slug/",
        )
        r = client_logged.get(reverse("ekoalu:lead_detail", args=["test-detail-slug"]))
        assert r.status_code == 200
        assert b"test-detail-slug" in r.content

    def test_lead_detail_disqualify(self, client_logged):
        from crm.models import Lead
        Lead.objects.create(
            public_identifier="test-disq",
            linkedin_url="https://www.linkedin.com/in/test-disq/",
        )
        r = client_logged.post(
            reverse("ekoalu:lead_detail", args=["test-disq"]),
            data={"action": "disqualify"},
        )
        assert r.status_code in (302, 303)
        lead = Lead.objects.get(public_identifier="test-disq")
        assert lead.disqualified is True

    def test_lead_detail_requalify(self, client_logged):
        from crm.models import Lead
        Lead.objects.create(
            public_identifier="test-req",
            linkedin_url="https://www.linkedin.com/in/test-req/",
            disqualified=True,
        )
        r = client_logged.post(
            reverse("ekoalu:lead_detail", args=["test-req"]),
            data={"action": "requalify"},
        )
        assert r.status_code in (302, 303)
        lead = Lead.objects.get(public_identifier="test-req")
        assert lead.disqualified is False


@pytest.mark.django_db
class TestCompaniesView:
    def test_companies_list_accessible_vide(self, client_logged):
        r = client_logged.get(reverse("ekoalu:companies_list"))
        assert r.status_code == 200

    def test_companies_list_avec_deals(self, client_logged):
        from crm.models import Lead, Deal
        from linkedin.models import Campaign
        lead = Lead.objects.create(
            public_identifier="companies-test",
            linkedin_url="https://www.linkedin.com/in/companies-test/",
        )
        camp = Campaign.objects.create(name="EKOALU - test companies")
        Deal.objects.create(
            lead=lead,
            campaign=camp,
            state="Qualified",
            profile_summary=[
                {"memory": "Company: BTP Lyon SAS"},
                {"memory": "Works in tertiary construction"},
            ],
        )
        r = client_logged.get(reverse("ekoalu:companies_list"))
        assert r.status_code == 200
        assert b"BTP Lyon SAS" in r.content


@pytest.mark.django_db
class TestInboxView:
    def test_inbox_accessible(self, client_logged):
        r = client_logged.get(reverse("ekoalu:inbox"))
        assert r.status_code == 200
        assert b"Inbox" in r.content


@pytest.mark.django_db
class TestOutboundViewsExtra:
    def test_outbound_edit_then_approve(self, client_logged):
        from ekoalu.outbound_validation.models import (
            OutboundKind,
            OutboundStatus,
            PendingOutbound,
        )
        po = PendingOutbound.objects.create(
            prospect_public_id="test",
            kind=OutboundKind.INVITATION,
            ai_draft="draft IA",
        )
        r = client_logged.post(
            reverse("ekoalu:outbound_detail", args=[po.pk]),
            data={
                "action": "approve",
                "final_content": "Version éditée par Richard",
            },
        )
        assert r.status_code in (302, 303)
        po.refresh_from_db()
        assert po.status == OutboundStatus.APPROVED
        assert po.final_content == "Version éditée par Richard"
        assert po.content_to_send == "Version éditée par Richard"
