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
class TestCampaignFunnel:
    """Cohérence arithmétique du funnel partagé dashboard + page campagnes."""

    def _make_deal(self, campaign, slug, state, outcome=""):
        from crm.models import Deal, Lead
        lead = Lead.objects.create(
            linkedin_url=f"https://www.linkedin.com/in/{slug}",
            public_identifier=slug,
        )
        return Deal.objects.create(lead=lead, campaign=campaign, state=state, outcome=outcome)

    def test_total_egal_somme_des_colonnes(self):
        from ekoalu.views import _campaign_funnel
        from linkedin.models import Campaign
        camp = Campaign.objects.create(name="EKOALU - Funnel test")
        self._make_deal(camp, "f-qual", "Qualified")
        self._make_deal(camp, "f-ready", "Ready_to_connect")
        self._make_deal(camp, "f-pend", "Pending")
        self._make_deal(camp, "f-conn", "Connected")
        self._make_deal(camp, "f-comp", "Completed", outcome="converted")
        self._make_deal(camp, "f-wf", "Failed", outcome="wrong_fit")
        self._make_deal(camp, "f-unresp", "Failed", outcome="unresponsive")

        f = _campaign_funnel(camp)
        assert f["a_contacter"] == 2
        assert f["invites"] == 1
        assert f["connectes"] == 2  # Connected + Completed
        assert f["hors_cible"] == 1
        assert f["echecs"] == 1
        assert f["total"] == 7
        assert f["total"] == (
            f["a_contacter"] + f["invites"] + f["connectes"]
            + f["hors_cible"] + f["echecs"]
        )

    def test_accept_rate_coherent_avec_colonne_connectes(self):
        from ekoalu.views import _campaign_funnel
        from linkedin.models import Campaign
        camp = Campaign.objects.create(name="EKOALU - Funnel accept")
        self._make_deal(camp, "fa-conn", "Connected")
        self._make_deal(camp, "fa-comp", "Completed", outcome="converted")
        self._make_deal(camp, "fa-pend", "Pending")
        self._make_deal(camp, "fa-unresp", "Failed", outcome="unresponsive")

        f = _campaign_funnel(camp)
        # 2 acceptées / (1 en attente + 2 acceptées + 1 sans réponse) = 50%
        assert f["accept_rate"] == 50.0
        assert f["invited_total"] == 4

    def test_deals_shadow_exclus(self):
        from ekoalu.views import _campaign_funnel
        from linkedin.models import Campaign
        camp = Campaign.objects.create(name="EKOALU - Funnel shadow")
        self._make_deal(camp, "fs-dup", "Completed", outcome="duplicate_campaign")
        self._make_deal(camp, "fs-pre", "Completed", outcome="pre_existing_relation")
        f = _campaign_funnel(camp)
        assert f["total"] == 0

    def test_campagne_vide(self):
        from ekoalu.views import _campaign_funnel
        from linkedin.models import Campaign
        camp = Campaign.objects.create(name="EKOALU - Funnel vide")
        f = _campaign_funnel(camp)
        assert f["total"] == 0
        assert f["accept_rate"] is None


@pytest.mark.django_db
class TestOutboundDetailProspect:
    """Fiche prospect complète dans le détail message (demande Richard 12/06)."""

    def _make_email_po(self):
        from crm.models import Lead
        from ekoalu.email_canal.models import EmailLeadData
        from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

        lead = Lead.objects.create(
            linkedin_url="https://bdd-prospect.local/siren/330465550",
            public_identifier="bdd-prospect-330465550",
            contact_email="m.allouis@faceintec.fr",
        )
        EmailLeadData.objects.create(
            lead=lead, source="bdd_prospect", siren="330465550",
            entreprise="ALLOUIS FACE INTEC", dirigeant="Marc Allouis",
            code_naf="43.32B", activite="Menuiserie métallique et serrurerie",
            cp="69100", dpt="69", ville="Villeurbanne",
            effectif_min=10, effectif_max=19,
        )
        return PendingOutbound.objects.create(
            prospect_public_id=lead.public_identifier,
            kind=OutboundKind.EMAIL_COLD,
            subject="Sujet",
            ai_draft="corps",
            status=OutboundStatus.PENDING,
        )

    def test_fiche_prospect_complete_pour_lead_bdd(self, client_logged):
        po = self._make_email_po()
        r = client_logged.get(reverse("ekoalu:outbound_detail", args=[po.pk]))
        assert r.status_code == 200
        content = r.content.decode()
        assert "Marc Allouis" in content              # nom réel, pas "Bdd Prospect"
        assert "ALLOUIS FACE INTEC" in content        # entreprise
        assert "m.allouis@faceintec.fr" in content    # email
        assert "Villeurbanne" in content              # localisation
        assert "43.32B" in content                    # NAF
        assert "10-19" in content                     # effectif
        assert "330465550" in content                 # siren
        assert "BDD PROSPECT" in content              # source
        assert "Slug LinkedIn" not in content         # plus de faux lien LinkedIn

    def test_logo_visible_dans_apercu_signature(self, client_logged):
        po = self._make_email_po()
        r = client_logged.get(reverse("ekoalu:outbound_detail", args=[po.pk]))
        assert "data:image/png;base64," in r.content.decode()

    # --- Demandes Richard 15/06 (captures) ---

    def _make_real_linkedin_po(self):
        from crm.models import Lead
        from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/jean-dupont-1234/",
            public_identifier="jean-dupont-1234",
        )
        return PendingOutbound.objects.create(
            prospect_public_id=lead.public_identifier,
            kind=OutboundKind.INVITATION, subject="", ai_draft="Bonjour Jean",
            status=OutboundStatus.PENDING,
        )

    def _make_mailjet_hot_po(self):
        """Lead Mailjet hot : URL synthétique *.local, PAS un vrai LinkedIn (capture #472)."""
        from crm.models import Lead
        from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

        lead = Lead.objects.create(
            linkedin_url="https://mailjet-hot.local/a-peyron-blanchet",
            public_identifier="mailjet-hot-a-peyron-blanchet",
            contact_email="a.peyron@blanchet-sa.fr",
        )
        return PendingOutbound.objects.create(
            prospect_public_id=lead.public_identifier,
            kind=OutboundKind.EMAIL_COLD, subject="S", ai_draft="corps",
            status=OutboundStatus.PENDING,
        )

    def test_lien_linkedin_affiche_pour_vrai_profil(self, client_logged):
        po = self._make_real_linkedin_po()
        content = client_logged.get(reverse("ekoalu:outbound_detail", args=[po.pk])).content.decode()
        assert "https://www.linkedin.com/in/jean-dupont-1234/" in content

    def test_pas_de_faux_lien_linkedin_pour_mailjet_hot(self, client_logged):
        """Capture #472 : un lead Mailjet hot (URL .local) ne doit PAS afficher
        de lien LinkedIn (slug synthétique)."""
        po = self._make_mailjet_hot_po()
        content = client_logged.get(reverse("ekoalu:outbound_detail", args=[po.pk])).content.decode()
        assert "mailjet-hot.local" not in content
        assert "linkedin.com/in/mailjet-hot" not in content

    def test_bouton_validation_rapide_present(self, client_logged):
        """Capture #58-07 : bouton de validation juste après le brouillon."""
        po = self._make_email_po()
        content = client_logged.get(reverse("ekoalu:outbound_detail", args=[po.pk])).content.decode()
        assert "Valider ce brouillon tel quel" in content

    def test_validation_rapide_sans_final_content_envoie_le_draft(self, client_logged):
        from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound

        po = self._make_email_po()
        # bouton rapide = approve SANS final_content
        client_logged.post(reverse("ekoalu:outbound_detail", args=[po.pk]), {"action": "approve"})
        po.refresh_from_db()
        assert po.status == OutboundStatus.APPROVED
        assert po.content_to_send == "corps"  # retombe sur ai_draft

    def test_validation_enchaine_sur_le_suivant(self, client_logged):
        """Capture #56-54 : après validation, on va direct au prochain message pending."""
        po1 = self._make_email_po()
        po2 = self._make_mailjet_hot_po()
        r = client_logged.post(
            reverse("ekoalu:outbound_detail", args=[po1.pk]), {"action": "approve"},
        )
        assert r.status_code == 302
        assert r.url == reverse("ekoalu:outbound_detail", args=[po2.pk])

    def test_validation_du_dernier_revient_a_la_liste(self, client_logged):
        po = self._make_email_po()
        r = client_logged.post(
            reverse("ekoalu:outbound_detail", args=[po.pk]), {"action": "approve"},
        )
        assert r.status_code == 302
        assert r.url == reverse("ekoalu:outbound_list")

    def test_fiche_prospect_lead_detail_enrichie_et_lien_retour_message(self, client_logged):
        """La fiche prospect porte les mêmes infos en partie haute + le lien
        vers le message en attente de validation (captures Richard 12/06)."""
        po = self._make_email_po()
        r = client_logged.get(
            reverse("ekoalu:lead_detail", args=[po.prospect_public_id]),
        )
        assert r.status_code == 200
        content = r.content.decode()
        assert "Marc Allouis" in content
        assert "m.allouis@faceintec.fr" in content
        assert "Villeurbanne" in content
        assert "43.32B" in content
        assert "330465550" in content
        assert "BDD PROSPECT" in content
        # bouton retour vers le message pending
        assert f"/ekoalu/messages/{po.pk}/" in content
        assert "à valider" in content
        # pas de faux lien LinkedIn pour un lead mail-only
        assert "linkedin.com/in/bdd-prospect" not in content


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

    def test_outbound_reject_disqualifie_lead_et_stoppe_deals(self, client_logged):
        """Refus = exclusion permanente du prospect (cf. _disqualify_leads_from_reject).

        Sans ca, le daemon regenererait un nouveau PendingOutbound au cycle
        suivant car _has_open_outbound ignore le statut REJECTED.
        """
        from crm.models import Deal, Lead
        from crm.models.deal import Outcome
        from ekoalu.outbound_validation.models import (
            OutboundKind,
            PendingOutbound,
        )
        from linkedin.enums import ProfileState
        from linkedin.models import Campaign

        lead = Lead.objects.create(
            public_identifier="fabien-test",
            linkedin_url="https://www.linkedin.com/in/fabien-test/",
        )
        camp_a = Campaign.objects.create(name="EKOALU - ABM Acial")
        camp_b = Campaign.objects.create(name="EKOALU - ABM Vinci")
        deal_a = Deal.objects.create(lead=lead, campaign=camp_a, state=ProfileState.CONNECTED.value)
        deal_b = Deal.objects.create(lead=lead, campaign=camp_b, state=ProfileState.PENDING.value)
        po = PendingOutbound.objects.create(
            prospect_public_id="fabien-test",
            campaign_id=camp_a.pk,
            kind=OutboundKind.FOLLOW_UP,
            ai_draft="draft",
        )
        r = client_logged.post(
            reverse("ekoalu:outbound_detail", args=[po.pk]),
            data={"action": "reject", "rejection_reason": "pas interessé"},
        )
        assert r.status_code in (302, 303)

        lead.refresh_from_db()
        deal_a.refresh_from_db()
        deal_b.refresh_from_db()
        assert lead.disqualified is True
        assert deal_a.state == ProfileState.FAILED.value
        assert deal_a.outcome == Outcome.NOT_INTERESTED.value
        assert "Refus Richard" in deal_a.reason
        # Deal cross-campagne stoppé aussi (sinon ABM Vinci continuerait à relancer)
        assert deal_b.state == ProfileState.FAILED.value
        assert deal_b.outcome == Outcome.NOT_INTERESTED.value

    def test_outbound_bulk_reject_disqualifie_leads(self, client_logged):
        from crm.models import Lead
        from ekoalu.outbound_validation.models import (
            OutboundKind,
            OutboundStatus,
            PendingOutbound,
        )

        lead1 = Lead.objects.create(public_identifier="alice-bulk",
                                    linkedin_url="https://www.linkedin.com/in/alice-bulk/")
        lead2 = Lead.objects.create(public_identifier="bob-bulk",
                                    linkedin_url="https://www.linkedin.com/in/bob-bulk/")
        po1 = PendingOutbound.objects.create(
            prospect_public_id="alice-bulk", kind=OutboundKind.INVITATION, ai_draft="d1",
        )
        po2 = PendingOutbound.objects.create(
            prospect_public_id="bob-bulk", kind=OutboundKind.FOLLOW_UP, ai_draft="d2",
        )
        r = client_logged.post(
            reverse("ekoalu:outbound_list"),
            data={
                "bulk_action": "bulk_reject",
                "bulk_reason": "test masse",
                "selected_ids": f"{po1.pk},{po2.pk}",
            },
        )
        assert r.status_code in (302, 303)
        po1.refresh_from_db()
        po2.refresh_from_db()
        lead1.refresh_from_db()
        lead2.refresh_from_db()
        assert po1.status == OutboundStatus.REJECTED
        assert po2.status == OutboundStatus.REJECTED
        assert lead1.disqualified is True
        assert lead2.disqualified is True


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
