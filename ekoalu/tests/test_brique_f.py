"""Tests fiabilité de la brique F : auto-désabonnement OPT_OUT, view approve, recap email."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from ekoalu.email_canal.inbox_poller import process_message
from ekoalu.email_generator.models import ColdEmailDraft
from ekoalu.inbox_assist.intent_classifier import Intent
from ekoalu.inbox_assist.models import PendingReply
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

pytestmark = pytest.mark.django_db


# --- Helpers ----------------------------------------------------------------


@pytest.fixture
def staff_user(db):
    User = get_user_model()
    return User.objects.create_user(
        username="richard_staff", email="r@ekoalu.com",
        password="x", is_staff=True,
    )


@pytest.fixture
def staff_client(staff_user):
    c = Client()
    c.force_login(staff_user)
    return c


@pytest.fixture
def make_lead_email(db):
    from crm.models import Lead
    from ekoalu.email_canal.models import EmailLeadData

    def _build(*, email="d@acme.fr", siren="900000001", unsubscribed=False):
        lead = Lead.objects.create(
            linkedin_url=f"https://bdd-prospect.local/siren/{siren}",
            public_identifier=f"bdd-prospect-{siren}",
            contact_email=email,
            contact_email_source="bdd_prospect",
            unsubscribed_at=timezone.now() if unsubscribed else None,
        )
        EmailLeadData.objects.create(
            lead=lead, source="bdd_prospect", siren=siren,
            entreprise="ACME", dirigeant="X", code_naf="41.20B",
        )
        return lead
    return _build


def _msg(*, id="m-f", from_email="d@acme.fr", subject="Re: X",
         body="merci de me désabonner, pas intéressé"):
    return {
        "id": id, "subject": subject,
        "from_email": from_email.lower(), "from_name": "X",
        "received_at": "2026-05-27T09:00:00Z",
        "body_text": body, "body_html": "", "is_read": False,
    }


# === F1 : Auto-désabonnement OPT_OUT ========================================


class TestAutoUnsubscribeOnOptOut:
    def test_opt_out_set_unsubscribed_at(self, make_lead_email, monkeypatch):
        lead = make_lead_email(email="d@acme.fr")
        assert lead.unsubscribed_at is None

        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.generate_email_reply",
            lambda **kw: ColdEmailDraft(subject="Re: X",
                                        body="C'est noté.\nCordialement"),
        )
        process_message(_msg(body="merci de me désabonner, je suis pas intéressé"))

        lead.refresh_from_db()
        assert lead.unsubscribed_at is not None

    def test_opt_out_pending_reply_quand_meme_cree(self, make_lead_email, monkeypatch):
        """Même si on désinscrit, on crée le PR pour que Richard envoie la confirmation."""
        make_lead_email(email="d@acme.fr")
        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.generate_email_reply",
            lambda **kw: ColdEmailDraft(subject="Re: X", body="OK noté."),
        )
        process_message(_msg(body="merci de me désabonner"))
        pr = PendingReply.objects.first()
        assert pr is not None
        assert pr.intent == Intent.OPT_OUT.value

    def test_non_opt_out_ne_touche_pas_unsubscribed_at(self, make_lead_email, monkeypatch):
        lead = make_lead_email(email="d@acme.fr")
        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.generate_email_reply",
            lambda **kw: ColdEmailDraft(subject="Re: X", body="OK 15 min visio.\nRichard"),
        )
        process_message(_msg(body="ok pour un rendez-vous visio rapide"))
        lead.refresh_from_db()
        assert lead.unsubscribed_at is None

    def test_opt_out_deja_unsubscribed_ne_double_pas(self, make_lead_email, monkeypatch):
        """Si lead déjà désinscrit, on ne touche pas à la date (préserve l'original)."""
        lead = make_lead_email(email="d@acme.fr", unsubscribed=True)
        original_unsub_at = lead.unsubscribed_at
        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.generate_email_reply",
            lambda **kw: ColdEmailDraft(subject="Re: X", body="OK noté."),
        )
        process_message(_msg(body="merci de me désinscrire"))
        lead.refresh_from_db()
        assert lead.unsubscribed_at == original_unsub_at  # inchangé


# === F2 : Vue email_reply_action ============================================


def _make_pr_email(status=PendingReply.Status.PENDING, **kw):
    defaults = dict(
        prospect_public_id="bdd-prospect-1",
        channel=PendingReply.CHANNEL_EMAIL,
        inbound_message_id="g-1",
        sender_email="x@y.fr",
        inbound_subject="Re: x",
        inbound_message="reçu",
        intent="rdv_request",
        ai_draft="Bonjour, OK pour 15min visio.\nRichard",
        final_sent="",
        status=status,
    )
    defaults.update(kw)
    return PendingReply.objects.create(**defaults)


class TestEmailReplyActionView:
    def test_approve_avec_final_sent(self, staff_client):
        pr = _make_pr_email()
        url = reverse("ekoalu:email_reply_action", args=[pr.pk])
        resp = staff_client.post(url, {
            "action": "approve",
            "final_sent": "Bonjour, OK pour vendredi 14h en visio.\nR.",
        })
        assert resp.status_code == 302
        pr.refresh_from_db()
        assert pr.status == PendingReply.Status.APPROVED
        assert "vendredi 14h" in pr.final_sent

    def test_approve_sans_final_sent_garde_draft(self, staff_client):
        """Si Richard approuve sans éditer, final_sent reste vide
        et reply_sender prendra ai_draft."""
        pr = _make_pr_email()
        url = reverse("ekoalu:email_reply_action", args=[pr.pk])
        resp = staff_client.post(url, {"action": "approve", "final_sent": ""})
        assert resp.status_code == 302
        pr.refresh_from_db()
        assert pr.status == PendingReply.Status.APPROVED
        assert pr.final_sent == ""

    def test_discard(self, staff_client):
        pr = _make_pr_email()
        url = reverse("ekoalu:email_reply_action", args=[pr.pk])
        resp = staff_client.post(url, {"action": "discard"})
        assert resp.status_code == 302
        pr.refresh_from_db()
        assert pr.status == PendingReply.Status.DISCARDED

    def test_save_draft_garde_status_pending(self, staff_client):
        pr = _make_pr_email()
        url = reverse("ekoalu:email_reply_action", args=[pr.pk])
        resp = staff_client.post(url, {
            "action": "save_draft",
            "final_sent": "version intermediaire",
        })
        assert resp.status_code == 302
        pr.refresh_from_db()
        assert pr.status == PendingReply.Status.PENDING  # inchangé
        assert pr.final_sent == "version intermediaire"

    def test_action_inconnue_ne_change_rien(self, staff_client):
        pr = _make_pr_email()
        url = reverse("ekoalu:email_reply_action", args=[pr.pk])
        resp = staff_client.post(url, {"action": "wat"})
        assert resp.status_code == 302
        pr.refresh_from_db()
        assert pr.status == PendingReply.Status.PENDING

    def test_pr_linkedin_refuse(self, staff_client):
        pr = _make_pr_email(channel=PendingReply.CHANNEL_LINKEDIN,
                            inbound_message_id="li-1")
        url = reverse("ekoalu:email_reply_action", args=[pr.pk])
        resp = staff_client.post(url, {"action": "approve"})
        assert resp.status_code == 302
        pr.refresh_from_db()
        assert pr.status == PendingReply.Status.PENDING  # intact

    def test_get_refuse(self, staff_client):
        pr = _make_pr_email()
        url = reverse("ekoalu:email_reply_action", args=[pr.pk])
        resp = staff_client.get(url)
        assert resp.status_code == 405  # require_POST

    def test_anonymous_refuse(self):
        pr = _make_pr_email()
        c = Client()
        url = reverse("ekoalu:email_reply_action", args=[pr.pk])
        resp = c.post(url, {"action": "approve"})
        # Redirect vers login admin
        assert resp.status_code == 302
        pr.refresh_from_db()
        assert pr.status == PendingReply.Status.PENDING


# === F3 : Stats canal email dans daily_recap ================================


class TestDailyRecapEmailStats:
    def test_email_cold_sent_compte_les_envoyes(self, db):
        from ekoalu.management.commands.daily_recap import compute_stats
        now = timezone.now()
        PendingOutbound.objects.create(
            prospect_public_id="x1", kind=OutboundKind.EMAIL_COLD,
            ai_draft="x", subject="s", status=OutboundStatus.SENT, sent_at=now,
        )
        PendingOutbound.objects.create(
            prospect_public_id="x2", kind=OutboundKind.EMAIL_COLD,
            ai_draft="x", subject="s", status=OutboundStatus.PENDING,
        )
        PendingOutbound.objects.create(
            prospect_public_id="x3", kind=OutboundKind.INVITATION,
            ai_draft="x", status=OutboundStatus.SENT, sent_at=now,
        )
        stats = compute_stats(date.today())
        assert stats.email_cold_sent == 1  # seulement le SENT email_cold
        assert stats.invitations_sent == 1  # invitation séparée

    def test_email_replies_compteurs(self, db):
        from ekoalu.management.commands.daily_recap import compute_stats
        now = timezone.now()
        _make_pr_email(status=PendingReply.Status.PENDING)
        _make_pr_email(status=PendingReply.Status.PENDING, inbound_message_id="g-2")
        _make_pr_email(status=PendingReply.Status.SENT, sent_at=now, inbound_message_id="g-3")
        _make_pr_email(status=PendingReply.Status.APPROVED, inbound_message_id="g-4")
        # PR LinkedIn ne doit PAS compter
        _make_pr_email(channel=PendingReply.CHANNEL_LINKEDIN, inbound_message_id="li-x")

        stats = compute_stats(date.today())
        assert stats.email_replies_received == 4  # tous les PR email créés aujourd'hui
        assert stats.email_replies_pending == 2  # PENDING uniquement
        assert stats.email_replies_sent == 1  # SENT uniquement

    def test_email_unsubscribed_compte_les_desinscrits_email(self, db):
        from crm.models import Lead
        from ekoalu.management.commands.daily_recap import compute_stats

        now = timezone.now()
        Lead.objects.create(
            linkedin_url="https://bdd-prospect.local/siren/u1",
            public_identifier="bdd-prospect-u1",
            contact_email="u1@x.fr", contact_email_source="bdd_prospect",
            unsubscribed_at=now,
        )
        Lead.objects.create(
            linkedin_url="https://bdd-prospect.local/siren/u2",
            public_identifier="bdd-prospect-u2",
            contact_email="u2@x.fr", contact_email_source="bdd_prospect",
            unsubscribed_at=now,
        )
        # Lead sans email_contact ne doit PAS compter (pas une "désinscription canal email")
        Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/li1",
            public_identifier="li1",
            unsubscribed_at=now,
        )
        # Lead désinscrit hier ne doit PAS compter
        old_lead = Lead.objects.create(
            linkedin_url="https://bdd-prospect.local/siren/old",
            public_identifier="bdd-prospect-old",
            contact_email="old@x.fr",
            unsubscribed_at=now - timedelta(days=2),
        )

        stats = compute_stats(date.today())
        assert stats.email_unsubscribed == 2
