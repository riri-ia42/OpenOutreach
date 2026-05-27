"""Tests fiabilité de inbox_poller + reply_generator + poll_email_replies."""
from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from ekoalu.email_canal.inbox_poller import poll_inbox, process_message
from ekoalu.email_generator.models import ColdEmailDraft
from ekoalu.email_generator.reply_generator import generate_email_reply
from ekoalu.inbox_assist.intent_classifier import Intent
from ekoalu.inbox_assist.models import PendingReply

pytestmark = pytest.mark.django_db


# --- Builders ---------------------------------------------------------------


@pytest.fixture
def make_lead_email(db):
    """Builder Lead + EmailLeadData pour matcher contre l'inbox."""
    from crm.models import Lead
    from ekoalu.email_canal.models import EmailLeadData

    def _build(*, email="dirigeant@acme.fr", siren="100000001",
               entreprise="ACME BAT", dirigeant="JEAN DUPONT"):
        lead = Lead.objects.create(
            linkedin_url=f"https://bdd-prospect.local/siren/{siren}",
            public_identifier=f"bdd-prospect-{siren}",
            contact_email=email,
            contact_email_source="bdd_prospect",
        )
        EmailLeadData.objects.create(
            lead=lead, source="bdd_prospect", siren=siren,
            entreprise=entreprise, dirigeant=dirigeant, code_naf="41.20B",
        )
        return lead
    return _build


def _msg(*, id="msg-1", from_email="dirigeant@acme.fr",
         subject="Re: Coupe-feu", body="Bonjour, intéressé."):
    return {
        "id": id, "subject": subject,
        "from_email": from_email.lower(), "from_name": "X",
        "received_at": "2026-05-27T09:00:00Z",
        "body_text": body, "body_html": "", "is_read": False,
    }


@pytest.fixture
def fake_reply():
    return ColdEmailDraft(
        subject="Re: Coupe-feu EI60",
        body="Bonjour M. Dupont,\n\n15 min visio ?\n\nRichard",
        model_used="claude-sonnet-4-6",
    )


# --- process_message --------------------------------------------------------


class TestProcessMessage:
    def test_match_lead_et_cree_pendingreply(self, make_lead_email, monkeypatch, fake_reply):
        lead = make_lead_email(email="dirigeant@acme.fr")
        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.generate_email_reply",
            lambda **kw: fake_reply,
        )
        result = process_message(_msg(from_email="dirigeant@acme.fr"))
        assert result == "draft_created"
        pr = PendingReply.objects.get(inbound_message_id="msg-1")
        assert pr.channel == PendingReply.CHANNEL_EMAIL
        assert pr.prospect_public_id == lead.public_identifier
        assert pr.sender_email == "dirigeant@acme.fr"
        assert pr.inbound_subject == "Re: Coupe-feu"
        assert pr.ai_draft == fake_reply.body
        assert pr.status == PendingReply.Status.PENDING

    def test_idempotence_meme_message_id(self, make_lead_email, monkeypatch, fake_reply):
        make_lead_email(email="dirigeant@acme.fr")
        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.generate_email_reply",
            lambda **kw: fake_reply,
        )
        r1 = process_message(_msg(id="msg-7"))
        r2 = process_message(_msg(id="msg-7"))
        assert r1 == "draft_created"
        assert r2 == "already_seen"
        assert PendingReply.objects.filter(inbound_message_id="msg-7").count() == 1

    def test_pas_de_lead_match(self, db, monkeypatch, fake_reply):
        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.generate_email_reply",
            lambda **kw: pytest.fail("ne doit pas être appelé sans lead"),
        )
        result = process_message(_msg(from_email="inconnu@xyz.com"))
        assert result == "no_lead_match"
        assert PendingReply.objects.count() == 0

    def test_match_case_insensitive(self, make_lead_email, monkeypatch, fake_reply):
        make_lead_email(email="dirigeant@acme.fr")
        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.generate_email_reply",
            lambda **kw: fake_reply,
        )
        result = process_message(_msg(from_email="Dirigeant@ACME.fr"))
        assert result == "draft_created"

    def test_intent_classifie_dans_pending(self, make_lead_email, monkeypatch, fake_reply):
        make_lead_email(email="dirigeant@acme.fr")
        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.generate_email_reply",
            lambda **kw: fake_reply,
        )
        # Message qui matche les patterns RDV (cf intent_classifier)
        process_message(_msg(
            body="Bonjour, pourriez-vous me proposer un rendez-vous ?",
        ))
        pr = PendingReply.objects.first()
        assert pr.intent == Intent.RDV_REQUEST.value

    def test_intent_opt_out_persiste(self, make_lead_email, monkeypatch, fake_reply):
        make_lead_email(email="dirigeant@acme.fr")
        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.generate_email_reply",
            lambda **kw: fake_reply,
        )
        process_message(_msg(body="merci de me désabonner, je ne suis pas intéressé."))
        pr = PendingReply.objects.first()
        assert pr.intent == Intent.OPT_OUT.value

    def test_no_claude_mode_skeleton(self, make_lead_email, monkeypatch):
        make_lead_email(email="dirigeant@acme.fr")
        # Si generate_draft=False, on ne doit PAS appeler le générateur
        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.generate_email_reply",
            lambda **kw: pytest.fail("ne doit pas être appelé"),
        )
        result = process_message(_msg(), generate_draft=False)
        assert result == "draft_created"
        pr = PendingReply.objects.first()
        assert pr.ai_draft == ""

    def test_generation_echec_persiste_quand_meme(self, make_lead_email, monkeypatch):
        make_lead_email(email="dirigeant@acme.fr")
        empty_draft = ColdEmailDraft(subject="", body="")
        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.generate_email_reply",
            lambda **kw: empty_draft,
        )
        result = process_message(_msg(id="msg-fail"))
        assert result == "draft_failed"
        # On a quand même un PendingReply (pour ne pas perdre le mail) avec body vide
        pr = PendingReply.objects.get(inbound_message_id="msg-fail")
        assert pr.ai_draft == ""

    def test_msg_sans_id_ignore(self, db, monkeypatch):
        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.generate_email_reply",
            lambda **kw: pytest.fail("ne doit pas être appelé"),
        )
        result = process_message({"id": "", "from_email": "x@y.fr",
                                  "subject": "", "body_text": ""})
        assert result == "no_lead_match"


# --- poll_inbox --------------------------------------------------------------


class TestPollInbox:
    def test_compte_les_stats(self, make_lead_email, monkeypatch, fake_reply):
        make_lead_email(email="a@x.fr", siren="1")
        make_lead_email(email="b@x.fr", siren="2")

        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.list_inbox_messages",
            lambda **kw: [
                _msg(id="m1", from_email="a@x.fr"),
                _msg(id="m2", from_email="b@x.fr"),
                _msg(id="m3", from_email="inconnu@z.fr"),
            ],
        )
        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.generate_email_reply",
            lambda **kw: fake_reply,
        )
        stats = poll_inbox(since_iso_utc="2026-05-27T00:00:00Z")
        assert stats.fetched == 3
        assert stats.drafts_created == 2
        assert stats.no_lead_match == 1
        assert stats.already_seen == 0

    def test_idempotence_sur_relance(self, make_lead_email, monkeypatch, fake_reply):
        make_lead_email(email="a@x.fr", siren="1")
        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.list_inbox_messages",
            lambda **kw: [_msg(id="dup", from_email="a@x.fr")],
        )
        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.generate_email_reply",
            lambda **kw: fake_reply,
        )
        s1 = poll_inbox(since_iso_utc="2026-05-27T00:00:00Z")
        s2 = poll_inbox(since_iso_utc="2026-05-27T00:00:00Z")
        assert s1.drafts_created == 1
        assert s2.already_seen == 1
        assert s2.drafts_created == 0
        assert PendingReply.objects.filter(inbound_message_id="dup").count() == 1


# --- reply_generator parsing & prompt ---------------------------------------


class TestReplyGenerator:
    def test_pas_de_client_retour_vide(self, monkeypatch):
        monkeypatch.setattr(
            "ekoalu.email_generator.reply_generator._get_anthropic_client",
            lambda: None,
        )
        d = generate_email_reply(
            intent=Intent.RDV_REQUEST,
            inbound_subject="Re: x",
            inbound_message="ok pour 15min",
        )
        assert not d.is_valid()

    def test_re_prefix_normalise(self, monkeypatch):
        """Si Claude renvoie un sujet sans 'Re:', on le préfixe nous-mêmes."""
        class _FakeContent:
            def __init__(self, text):
                self.text = text

        class _FakeResp:
            def __init__(self, text):
                self.content = [_FakeContent(text)]

        class _FakeClient:
            def __init__(self):
                self.messages = self
            def create(self, **kw):
                return _FakeResp(
                    "<sujet>Visio 15 min</sujet><corps>Bonjour, ok.\nRichard</corps>"
                )

        monkeypatch.setattr(
            "ekoalu.email_generator.reply_generator._get_anthropic_client",
            lambda: _FakeClient(),
        )
        d = generate_email_reply(
            intent=Intent.RDV_REQUEST,
            inbound_subject="Coupe-feu",
            inbound_message="ok pour 15min",
        )
        assert d.subject.startswith("Re: ")

    def test_re_prefix_pas_double_si_deja_present(self, monkeypatch):
        class _FakeContent:
            def __init__(self, text):
                self.text = text

        class _FakeResp:
            def __init__(self, text):
                self.content = [_FakeContent(text)]

        class _FakeClient:
            def __init__(self):
                self.messages = self
            def create(self, **kw):
                return _FakeResp(
                    "<sujet>Re: Coupe-feu</sujet><corps>Ok.</corps>"
                )

        monkeypatch.setattr(
            "ekoalu.email_generator.reply_generator._get_anthropic_client",
            lambda: _FakeClient(),
        )
        d = generate_email_reply(
            intent=Intent.OFF_TOPIC,
            inbound_subject="Coupe-feu", inbound_message="bien noté",
        )
        assert d.subject == "Re: Coupe-feu"  # pas "Re: Re: ..."


# --- Management command poll_email_replies ----------------------------------


class TestPollEmailRepliesCommand:
    def test_dry_run_ne_cree_rien(self, monkeypatch):
        monkeypatch.setattr(
            "ekoalu.notifications.graph_mailer.list_inbox_messages",
            lambda **kw: [_msg()],
        )
        call_command("poll_email_replies", dry_run=True, force_since="2026-01-01T00:00:00Z",
                     stdout=StringIO())
        assert PendingReply.objects.count() == 0

    def test_run_complet_cree_pending_reply(self, make_lead_email, monkeypatch, fake_reply):
        make_lead_email(email="a@x.fr", siren="1")
        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.list_inbox_messages",
            lambda **kw: [_msg(id="m-cmd", from_email="a@x.fr")],
        )
        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.generate_email_reply",
            lambda **kw: fake_reply,
        )
        call_command("poll_email_replies", force_since="2026-01-01T00:00:00Z",
                     stdout=StringIO())
        assert PendingReply.objects.filter(inbound_message_id="m-cmd").count() == 1

    def test_no_claude_flag_skip_generation(self, make_lead_email, monkeypatch):
        make_lead_email(email="a@x.fr", siren="1")
        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.list_inbox_messages",
            lambda **kw: [_msg(id="m-skel", from_email="a@x.fr")],
        )
        # Doit NE PAS appeler le générateur
        monkeypatch.setattr(
            "ekoalu.email_canal.inbox_poller.generate_email_reply",
            lambda **kw: pytest.fail("ne doit pas être appelé en --no-claude"),
        )
        call_command("poll_email_replies", no_claude=True,
                     force_since="2026-01-01T00:00:00Z", stdout=StringIO())
        pr = PendingReply.objects.get(inbound_message_id="m-skel")
        assert pr.ai_draft == ""
