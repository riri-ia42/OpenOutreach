"""LOT D : claim atomique APPROVED→SENDING (anti double envoi email).

Deux créneaux Task Scheduler peuvent se chevaucher (jitter 75 min) : sans
claim, les deux runs sélectionnaient les mêmes APPROVED et envoyaient deux
fois. Le claim UPDATE ... WHERE status=APPROVED (rowcount) garantit qu'un
seul process envoie ; un SENDING périmé (>2h, crash) repasse APPROVED avec
un warning explicite (jamais de renvoi silencieux).
"""
from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from ekoalu.inbox_assist.models import PendingReply
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(
        "ekoalu.management.commands.send_approved_emails.time.sleep", lambda _s: None)
    monkeypatch.setattr(
        "ekoalu.management.commands.send_approved_email_replies.time.sleep", lambda _s: None)


def _make_po(email="cible@acme.fr", status=OutboundStatus.APPROVED, **extra):
    from crm.models import Lead
    lead, _created = Lead.objects.get_or_create(
        public_identifier="bdd-prospect-777000777",
        defaults={
            "linkedin_url": "https://bdd-prospect.local/siren/777000777",
            "contact_email": email,
        },
    )
    return PendingOutbound.objects.create(
        prospect_public_id=lead.public_identifier, prospect_company="ACME",
        kind=OutboundKind.EMAIL_COLD, subject="EI60", ai_draft="Bonjour",
        status=status, **extra,
    )


class TestClaimAtomiqueColdMail:
    def test_claim_concurrent_un_seul_envoi(self, monkeypatch):
        """Simule le chevauchement : le PO passe SENDING (pris par un autre run)
        entre la sélection et l'envoi → 0 appel sender, 0 double envoi."""
        po = _make_po()
        sent = []

        def _steal_then_send(p):
            raise AssertionError("send_cold_email ne doit PAS être appelé")

        # Un "autre process" claime le PO juste après la sélection : on
        # reproduit son effet en le passant SENDING avant le run.
        PendingOutbound.objects.filter(pk=po.pk).update(
            status=OutboundStatus.SENDING, claimed_at=timezone.now())
        # La sélection ne le voit même plus (status != APPROVED)…
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_emails.send_cold_email",
            _steal_then_send)
        call_command("send_approved_emails", ignore_schedule=True, stdout=StringIO())
        po.refresh_from_db()
        assert po.status == OutboundStatus.SENDING
        assert sent == []

    def test_claim_rowcount_le_second_process_perd(self):
        """Primitive de la course : deux claims successifs sur le même PO —
        le premier gagne (rowcount 1), le second perd (rowcount 0)."""
        po = _make_po()
        claimed_1 = PendingOutbound.objects.filter(
            pk=po.pk, status=OutboundStatus.APPROVED,
        ).update(status=OutboundStatus.SENDING, claimed_at=timezone.now())
        claimed_2 = PendingOutbound.objects.filter(
            pk=po.pk, status=OutboundStatus.APPROVED,
        ).update(status=OutboundStatus.SENDING, claimed_at=timezone.now())
        assert claimed_1 == 1
        assert claimed_2 == 0

    def test_envoi_normal_claim_puis_sent(self, monkeypatch):
        po = _make_po()
        seen_status = []

        def _fake_send(p):
            p.refresh_from_db()
            seen_status.append(p.status)
            return True, ""

        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_emails.send_cold_email",
            _fake_send)
        call_command("send_approved_emails", ignore_schedule=True, stdout=StringIO())
        po.refresh_from_db()
        # Pendant l'envoi le PO était bien claimé SENDING, puis passe SENT.
        assert seen_status == [OutboundStatus.SENDING]
        assert po.status == OutboundStatus.SENT
        assert po.claimed_at is not None

    def test_sending_perime_repasse_approved_avec_warning(self, monkeypatch, caplog):
        stale = _make_po(status=OutboundStatus.SENDING,
                         claimed_at=timezone.now() - timedelta(hours=3))
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_emails.send_cold_email",
            lambda p: (True, ""))
        with caplog.at_level("WARNING"):
            call_command("send_approved_emails", ignore_schedule=True, stdout=StringIO())
        assert "SENDING" in caplog.text and "APPROVED" in caplog.text
        stale.refresh_from_db()
        # Récupéré puis renvoyé dans la même passe (avec warning explicite).
        assert stale.status == OutboundStatus.SENT

    def test_sending_recent_pas_touche(self, monkeypatch):
        """Un SENDING < 2h = un autre run est EN TRAIN d'envoyer → on n'y touche pas."""
        fresh = _make_po(status=OutboundStatus.SENDING,
                         claimed_at=timezone.now() - timedelta(minutes=10))
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_emails.send_cold_email",
            lambda p: (True, ""))
        call_command("send_approved_emails", ignore_schedule=True, stdout=StringIO())
        fresh.refresh_from_db()
        assert fresh.status == OutboundStatus.SENDING


class TestClaimAtomiqueReplies:
    def _make_pr(self, status=PendingReply.Status.APPROVED, **extra):
        return PendingReply.objects.create(
            prospect_public_id="bdd-prospect-888000888",
            channel=PendingReply.CHANNEL_EMAIL,
            sender_email="prospect@acme.fr",
            inbound_subject="Re: EI60",
            inbound_message="Intéressé",
            ai_draft="Merci, on peut se voir en visio.",
            status=status, **extra,
        )

    def test_envoi_normal_claim_puis_sent(self, monkeypatch):
        pr = self._make_pr()
        seen_status = []

        def _fake_send(p):
            p.refresh_from_db()
            seen_status.append(p.status)
            return True, ""

        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_email_replies.send_email_reply",
            _fake_send)
        call_command("send_approved_email_replies", ignore_schedule=True,
                     stdout=StringIO())
        pr.refresh_from_db()
        assert seen_status == [PendingReply.Status.SENDING]
        assert pr.status == PendingReply.Status.SENT

    def test_deja_claimee_skip(self, monkeypatch):
        pr = self._make_pr(status=PendingReply.Status.SENDING,
                           claimed_at=timezone.now())
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_email_replies.send_email_reply",
            lambda p: (_ for _ in ()).throw(AssertionError("ne doit pas envoyer")))
        call_command("send_approved_email_replies", ignore_schedule=True,
                     stdout=StringIO())
        pr.refresh_from_db()
        assert pr.status == PendingReply.Status.SENDING

    def test_sending_perimee_repasse_approved(self, monkeypatch, caplog):
        stale = self._make_pr(status=PendingReply.Status.SENDING,
                              claimed_at=timezone.now() - timedelta(hours=3))
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_email_replies.send_email_reply",
            lambda p: (True, ""))
        with caplog.at_level("WARNING"):
            call_command("send_approved_email_replies", ignore_schedule=True,
                         stdout=StringIO())
        assert "SENDING" in caplog.text
        stale.refresh_from_db()
        assert stale.status == PendingReply.Status.SENT
