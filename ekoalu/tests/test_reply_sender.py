"""Tests fiabilité de reply_sender + send_approved_email_replies."""
from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from ekoalu.email_canal.reply_sender import _resolve_body, send_email_reply
from ekoalu.inbox_assist.models import PendingReply
from ekoalu.notifications.graph_mailer import GraphSendError

pytestmark = pytest.mark.django_db


# --- Builders ---------------------------------------------------------------


def _make_pr(*, channel=PendingReply.CHANNEL_EMAIL, status=PendingReply.Status.APPROVED,
             inbound_message_id="graph-msg-id-123",
             sender_email="dirigeant@acme.fr", inbound_subject="Coupe-feu EI60",
             ai_draft="Bonjour, OK pour 15min visio.\nRichard",
             final_sent=""):
    return PendingReply.objects.create(
        prospect_public_id="bdd-prospect-1",
        channel=channel,
        inbound_message_id=inbound_message_id,
        sender_email=sender_email,
        inbound_subject=inbound_subject,
        inbound_message="Bonjour, intéressé.",
        intent="rdv_request",
        ai_draft=ai_draft,
        final_sent=final_sent,
        status=status,
    )


# --- _resolve_body ----------------------------------------------------------


class TestResolveBody:
    def test_final_sent_prioritaire_si_present(self):
        pr = _make_pr(ai_draft="DRAFT", final_sent="FINAL EDITE")
        assert _resolve_body(pr) == "FINAL EDITE"

    def test_ai_draft_par_defaut(self):
        pr = _make_pr(ai_draft="DRAFT", final_sent="")
        assert _resolve_body(pr) == "DRAFT"

    def test_final_sent_blanc_tombe_sur_draft(self):
        pr = _make_pr(ai_draft="DRAFT", final_sent="    \n  ")
        assert _resolve_body(pr) == "DRAFT"


# --- send_email_reply -------------------------------------------------------


class TestSendEmailReplySuccess:
    def test_appelle_graph_send_reply(self, monkeypatch):
        captured = {}

        def _mock(*, original_message_id, body_html):
            captured["msg_id"] = original_message_id
            captured["html"] = body_html

        monkeypatch.setattr("ekoalu.email_canal.reply_sender.send_reply", _mock)
        pr = _make_pr()
        success, err = send_email_reply(pr)
        assert success is True
        assert err == ""
        assert captured["msg_id"] == "graph-msg-id-123"
        assert "<p " in captured["html"]
        assert "OK pour 15min" in captured["html"]

    def test_envoie_final_sent_si_edite(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "ekoalu.email_canal.reply_sender.send_reply",
            lambda **kw: captured.update(html=kw["body_html"]),
        )
        pr = _make_pr(ai_draft="DRAFT BRUT", final_sent="VERSION RICHARD")
        success, _ = send_email_reply(pr)
        assert success
        assert "VERSION RICHARD" in captured["html"]
        assert "DRAFT BRUT" not in captured["html"]


class TestSendEmailReplyFailures:
    def test_channel_non_email(self, monkeypatch):
        monkeypatch.setattr(
            "ekoalu.email_canal.reply_sender.send_reply",
            lambda **kw: pytest.fail("ne doit pas être appelé"),
        )
        pr = _make_pr(channel=PendingReply.CHANNEL_LINKEDIN)
        success, err = send_email_reply(pr)
        assert not success
        assert "channel" in err.lower()

    def test_inbound_message_id_vide(self, monkeypatch):
        monkeypatch.setattr(
            "ekoalu.email_canal.reply_sender.send_reply",
            lambda **kw: pytest.fail("ne doit pas être appelé"),
        )
        pr = _make_pr(inbound_message_id="")
        success, err = send_email_reply(pr)
        assert not success
        assert "inbound_message_id" in err.lower() or "threadé" in err.lower()

    def test_body_vide(self, monkeypatch):
        monkeypatch.setattr(
            "ekoalu.email_canal.reply_sender.send_reply",
            lambda **kw: pytest.fail("ne doit pas être appelé"),
        )
        pr = _make_pr(ai_draft="", final_sent="")
        success, err = send_email_reply(pr)
        assert not success
        assert "body" in err.lower()

    def test_graph_send_error_capturee(self, monkeypatch):
        def _raise(**kw):
            raise GraphSendError("reply 404: not found")

        monkeypatch.setattr("ekoalu.email_canal.reply_sender.send_reply", _raise)
        pr = _make_pr()
        success, err = send_email_reply(pr)
        assert not success
        assert "graph_send" in err
        assert "404" in err


# --- Management command send_approved_email_replies -------------------------


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(
        "ekoalu.management.commands.send_approved_email_replies.time.sleep",
        lambda _s: None,
    )


class TestSendApprovedEmailRepliesCommand:
    def test_dry_run_ne_change_pas_statut(self, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_email_replies.send_email_reply",
            lambda pr: (called.update(n=called["n"] + 1) or True, ""),
        )
        pr = _make_pr()
        call_command("send_approved_email_replies", dry_run=True, ignore_schedule=True,
                     stdout=StringIO())
        pr.refresh_from_db()
        assert pr.status == PendingReply.Status.APPROVED
        assert called["n"] == 0

    def test_succes_passe_a_sent(self, monkeypatch):
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_email_replies.send_email_reply",
            lambda pr: (True, ""),
        )
        pr = _make_pr()
        call_command("send_approved_email_replies", ignore_schedule=True, stdout=StringIO())
        pr.refresh_from_db()
        assert pr.status == PendingReply.Status.SENT
        assert pr.sent_at is not None
        assert pr.error_message == ""

    def test_echec_passe_a_failed_avec_message(self, monkeypatch):
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_email_replies.send_email_reply",
            lambda pr: (False, "graph_send: 503 Service unavailable"),
        )
        pr = _make_pr()
        call_command("send_approved_email_replies", ignore_schedule=True, stdout=StringIO())
        pr.refresh_from_db()
        assert pr.status == PendingReply.Status.FAILED
        assert "503" in pr.error_message

    def test_max_cap(self, monkeypatch):
        for i in range(4):
            _make_pr(inbound_message_id=f"id-{i}", sender_email=f"u{i}@a.fr")
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_email_replies.send_email_reply",
            lambda pr: (True, ""),
        )
        call_command("send_approved_email_replies", max=2, ignore_schedule=True,
                     stdout=StringIO())
        sent = PendingReply.objects.filter(status=PendingReply.Status.SENT).count()
        assert sent == 2

    def test_hors_plage_horaire_skip(self, monkeypatch):
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_email_replies.is_action_allowed_now",
            lambda: False,
        )
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_email_replies.send_email_reply",
            lambda pr: pytest.fail("ne doit pas être appelé hors plage"),
        )
        pr = _make_pr()
        call_command("send_approved_email_replies", stdout=StringIO())
        pr.refresh_from_db()
        assert pr.status == PendingReply.Status.APPROVED

    def test_seuls_channel_email_traites(self, monkeypatch):
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_email_replies.send_email_reply",
            lambda pr: (True, ""),
        )
        pr_li = _make_pr(channel=PendingReply.CHANNEL_LINKEDIN,
                         inbound_message_id="li-1")
        pr_mail = _make_pr(channel=PendingReply.CHANNEL_EMAIL,
                           inbound_message_id="mail-1", sender_email="x@y.fr")
        call_command("send_approved_email_replies", ignore_schedule=True, stdout=StringIO())
        pr_li.refresh_from_db()
        pr_mail.refresh_from_db()
        assert pr_li.status == PendingReply.Status.APPROVED  # intact
        assert pr_mail.status == PendingReply.Status.SENT

    def test_seuls_status_approved_traites(self, monkeypatch):
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_email_replies.send_email_reply",
            lambda pr: (True, ""),
        )
        pr_pending = _make_pr(status=PendingReply.Status.PENDING, inbound_message_id="p-1")
        pr_sent = _make_pr(status=PendingReply.Status.SENT, inbound_message_id="s-1",
                           sender_email="z@y.fr")
        call_command("send_approved_email_replies", ignore_schedule=True, stdout=StringIO())
        pr_pending.refresh_from_db()
        pr_sent.refresh_from_db()
        assert pr_pending.status == PendingReply.Status.PENDING  # intact
        assert pr_sent.status == PendingReply.Status.SENT  # intact (n'a pas été ré-envoyé)
