"""Tests brique I (désinscription canal email depuis lead_detail) + J (apprentissage replies)."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from ekoalu.email_generator.models import ColdEmailDraft
from ekoalu.email_generator.reply_generator import (
    _build_few_shot_for_intent,
    generate_email_reply,
)
from ekoalu.inbox_assist.intent_classifier import Intent
from ekoalu.inbox_assist.models import CorrectionExample, PendingReply

pytestmark = pytest.mark.django_db


# --- Helpers ----------------------------------------------------------------


@pytest.fixture
def staff_user(db):
    User = get_user_model()
    return User.objects.create_user(
        username="richard_ij", email="r@ekoalu.com", password="x", is_staff=True,
    )


@pytest.fixture
def staff_client(staff_user):
    c = Client()
    c.force_login(staff_user)
    return c


@pytest.fixture
def make_lead(db):
    from crm.models import Lead
    from ekoalu.email_canal.models import EmailLeadData

    def _build(*, public_id="bdd-prospect-i01", email="d@acme.fr",
               unsubscribed=False):
        lead = Lead.objects.create(
            linkedin_url=f"https://bdd-prospect.local/siren/{public_id}",
            public_identifier=public_id,
            contact_email=email,
            contact_email_source="bdd_prospect",
            unsubscribed_at=timezone.now() if unsubscribed else None,
        )
        EmailLeadData.objects.create(
            lead=lead, source="bdd_prospect", siren=public_id,
            entreprise="ACME", dirigeant="X", code_naf="41.20B",
        )
        return lead
    return _build


def _make_pr(**kw):
    defaults = dict(
        prospect_public_id="bdd-prospect-i01",
        channel=PendingReply.CHANNEL_EMAIL,
        inbound_message_id="g-i01",
        sender_email="d@acme.fr",
        inbound_subject="Re: x",
        inbound_message="message reçu",
        intent="rdv_request",
        ai_draft="Bonjour, OK pour visio.\nRichard",
        final_sent="",
        status=PendingReply.Status.PENDING,
    )
    defaults.update(kw)
    return PendingReply.objects.create(**defaults)


# === Brique I : désinscription canal email depuis lead_detail =================


class TestUnsubscribeFromLeadDetail:
    def test_unsubscribe_email_set_unsubscribed_at(self, staff_client, make_lead):
        lead = make_lead(public_id="bdd-prospect-i02")
        assert lead.unsubscribed_at is None
        url = reverse("ekoalu:lead_detail", args=[lead.public_identifier])
        resp = staff_client.post(url, {"action": "unsubscribe_email"})
        assert resp.status_code == 302
        lead.refresh_from_db()
        assert lead.unsubscribed_at is not None

    def test_unsubscribe_email_idempotent_si_deja_unsub(self, staff_client, make_lead):
        lead = make_lead(public_id="bdd-prospect-i03", unsubscribed=True)
        original = lead.unsubscribed_at
        url = reverse("ekoalu:lead_detail", args=[lead.public_identifier])
        staff_client.post(url, {"action": "unsubscribe_email"})
        lead.refresh_from_db()
        # La date originale est préservée (pas écrasée)
        assert lead.unsubscribed_at == original

    def test_resubscribe_email_clear_unsubscribed_at(self, staff_client, make_lead):
        lead = make_lead(public_id="bdd-prospect-i04", unsubscribed=True)
        url = reverse("ekoalu:lead_detail", args=[lead.public_identifier])
        resp = staff_client.post(url, {"action": "resubscribe_email"})
        assert resp.status_code == 302
        lead.refresh_from_db()
        assert lead.unsubscribed_at is None

    def test_unsubscribe_visible_dans_lead_detail_get(self, staff_client, make_lead):
        lead = make_lead(public_id="bdd-prospect-i05")
        url = reverse("ekoalu:lead_detail", args=[lead.public_identifier])
        resp = staff_client.get(url)
        assert resp.status_code == 200
        # Le bouton désinscrire est présent puisque contact_email + pas désinscrit
        assert b"unsubscribe_email" in resp.content
        # Pas le bouton réabonner
        assert b"resubscribe_email" not in resp.content

    def test_resubscribe_visible_si_deja_unsub(self, staff_client, make_lead):
        lead = make_lead(public_id="bdd-prospect-i06", unsubscribed=True)
        url = reverse("ekoalu:lead_detail", args=[lead.public_identifier])
        resp = staff_client.get(url)
        assert resp.status_code == 200
        assert b"resubscribe_email" in resp.content
        assert b"unsubscribe_email" not in resp.content


# === Brique J : apprentissage CorrectionExample sur replies email ============


class TestCorrectionExampleOnApprove:
    def test_approve_avec_final_sent_edite_cree_correction(self, staff_client):
        pr = _make_pr(ai_draft="brouillon initial IA", final_sent="")
        url = reverse("ekoalu:email_reply_action", args=[pr.pk])
        staff_client.post(url, {
            "action": "approve",
            "final_sent": "Version retravaillée par Richard.",
            "learn_note": "Trop ampoulé, simplifié",
        })
        ce = CorrectionExample.objects.filter(pending_reply=pr).first()
        assert ce is not None
        assert ce.persona_slug == "email_reply_rdv_request"
        assert ce.explanation == "Trop ampoulé, simplifié"
        # similarity_ratio doit être < 1 (le texte a vraiment changé)
        assert ce.similarity_ratio < 0.9

    def test_approve_sans_final_sent_ne_cree_pas_correction(self, staff_client):
        pr = _make_pr(ai_draft="brouillon IA")
        url = reverse("ekoalu:email_reply_action", args=[pr.pk])
        staff_client.post(url, {"action": "approve", "final_sent": ""})
        assert CorrectionExample.objects.filter(pending_reply=pr).count() == 0

    def test_approve_final_sent_identique_ne_cree_pas_correction(self, staff_client):
        pr = _make_pr(ai_draft="exactement la même chose")
        url = reverse("ekoalu:email_reply_action", args=[pr.pk])
        staff_client.post(url, {
            "action": "approve",
            "final_sent": "exactement la même chose",
        })
        assert CorrectionExample.objects.filter(pending_reply=pr).count() == 0

    def test_persona_slug_depend_de_intent(self, staff_client):
        for intent_value in ("rdv_request", "technical_question", "objection",
                             "opt_out", "off_topic"):
            pr = _make_pr(intent=intent_value,
                          inbound_message_id=f"g-{intent_value}")
            url = reverse("ekoalu:email_reply_action", args=[pr.pk])
            staff_client.post(url, {
                "action": "approve",
                "final_sent": f"version richard pour {intent_value}",
            })
            ce = CorrectionExample.objects.get(pending_reply=pr)
            assert ce.persona_slug == f"email_reply_{intent_value}"


class TestBuildFewShotForIntent:
    def test_vide_si_aucun_exemple(self, db):
        out = _build_few_shot_for_intent(Intent.RDV_REQUEST)
        assert out == ""

    def test_inclut_exemples_du_bon_intent(self, db):
        pr = _make_pr(intent="rdv_request",
                      ai_draft="brouillon IA initial",
                      final_sent="version Richard améliorée",
                      inbound_message="quand peut-on se voir ?",
                      inbound_message_id="g-fs1")
        CorrectionExample.objects.create(
            pending_reply=pr,
            persona_slug="email_reply_rdv_request",
            kind=CorrectionExample.Kind.TEXT_CORRECTION,
            similarity_ratio=0.4,
            diff_lines=[],
            explanation="simplifié",
        )
        out = _build_few_shot_for_intent(Intent.RDV_REQUEST)
        assert "Richard améliorée" in out
        assert "brouillon IA initial" in out
        assert "quand peut-on se voir" in out
        assert "simplifié" in out

    def test_filtre_par_intent(self, db):
        """Un CorrectionExample pour `objection` ne doit pas remonter pour `rdv_request`."""
        pr = _make_pr(intent="objection",
                      ai_draft="x", final_sent="y",
                      inbound_message_id="g-fs2")
        CorrectionExample.objects.create(
            pending_reply=pr,
            persona_slug="email_reply_objection",
            kind=CorrectionExample.Kind.TEXT_CORRECTION,
            similarity_ratio=0.5, diff_lines=[],
        )
        out = _build_few_shot_for_intent(Intent.RDV_REQUEST)
        assert out == ""  # rien pour l'intent demandé

    def test_limit_respect(self, db):
        for i in range(10):
            pr = _make_pr(intent="rdv_request",
                          inbound_message=f"msg {i}",
                          inbound_message_id=f"g-lim{i}",
                          ai_draft=f"brouillon {i}",
                          final_sent=f"final {i}")
            CorrectionExample.objects.create(
                pending_reply=pr,
                persona_slug="email_reply_rdv_request",
                kind=CorrectionExample.Kind.TEXT_CORRECTION,
                similarity_ratio=0.5, diff_lines=[],
            )
        out = _build_few_shot_for_intent(Intent.RDV_REQUEST, limit=3)
        # 3 exemples présents max
        assert out.count("Brouillon IA :") == 3


class TestGenerateEmailReplyUsesFewShot:
    def test_few_shot_injecte_dans_system_prompt(self, db, monkeypatch):
        # Crée un exemple
        pr = _make_pr(intent="rdv_request",
                      ai_draft="brouillon banal",
                      final_sent="REPONSE_RICHARD_UNIQUE_TOKEN",
                      inbound_message_id="g-inject")
        CorrectionExample.objects.create(
            pending_reply=pr,
            persona_slug="email_reply_rdv_request",
            kind=CorrectionExample.Kind.TEXT_CORRECTION,
            similarity_ratio=0.4, diff_lines=[],
        )

        captured = {}

        class _FakeContent:
            text = "<sujet>Re: x</sujet><corps>ok</corps>"

        class _FakeResp:
            content = [_FakeContent()]

        class _FakeClient:
            def __init__(self):
                self.messages = self

            def create(self, **kw):
                captured["system"] = kw["system"]
                return _FakeResp()

        monkeypatch.setattr(
            "ekoalu.email_generator.reply_generator._get_anthropic_client",
            lambda: _FakeClient(),
        )
        generate_email_reply(
            intent=Intent.RDV_REQUEST,
            inbound_subject="x",
            inbound_message="quand on se voit ?",
        )
        # Le system prompt doit contenir la version de Richard
        assert "REPONSE_RICHARD_UNIQUE_TOKEN" in captured["system"]
