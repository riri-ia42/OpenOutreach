"""Tests fiabilité du sender cold mail + management command send_approved_emails."""
from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from ekoalu.email_canal.sender import (
    build_html_email,
    send_cold_email,
    text_body_to_html,
)
from ekoalu.notifications.graph_mailer import GraphSendError
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound


# --- text_body_to_html -------------------------------------------------------


class TestTextBodyToHtml:
    def test_paragraphes_separes_par_blank_line(self):
        body = "Bonjour M. Dupont,\n\nMessage corps.\n\nCordialement"
        out = text_body_to_html(body)
        assert out.count("<p ") == 3

    def test_line_breaks_simples_deviennent_br(self):
        body = "Ligne1\nLigne2\n\nPara2"
        out = text_body_to_html(body)
        assert "Ligne1<br>Ligne2" in out
        assert "Para2" in out

    def test_html_escape(self):
        body = "Vous avez <script>alert(1)</script> & ça."
        out = text_body_to_html(body)
        assert "&lt;script&gt;" in out
        assert "&amp;" in out
        assert "<script>" not in out  # neutralisé

    def test_strip_ligne_vide_finale(self):
        body = "Bonjour,\n\n\n\n"
        out = text_body_to_html(body)
        assert out.count("<p ") == 1


class TestBuildHtmlEmail:
    def test_footer_desinscription_inclus(self):
        out = build_html_email("Bonjour, corps.")
        assert "stop" in out.lower()
        assert "exclusion" in out.lower() or "ne plus recevoir" in out.lower()

    def test_body_vide_ne_casse_pas(self):
        out = build_html_email("")
        assert "stop" in out.lower()


# --- send_cold_email ---------------------------------------------------------


@pytest.fixture
def make_lead_with_po(db):
    """Builder Lead + EmailLeadData + PendingOutbound approved (kind=email_cold)."""
    from crm.models import Lead
    from ekoalu.email_canal.models import EmailLeadData

    def _build(*, email="dirigeant@acme.fr", subject="Coupe-feu EI60 pour vos projets",
               body="Bonjour M. Dupont,\n\nCoupe-feu EI60.\n\nCordialement",
               unsubscribed=False, status=OutboundStatus.APPROVED,
               kind=OutboundKind.EMAIL_COLD, siren="555000111"):
        lead = Lead.objects.create(
            linkedin_url=f"https://bdd-prospect.local/siren/{siren}",
            public_identifier=f"bdd-prospect-{siren}",
            contact_email=email,
            contact_email_source="bdd_prospect",
            unsubscribed_at=timezone.now() if unsubscribed else None,
        )
        EmailLeadData.objects.create(
            lead=lead, source="bdd_prospect", siren=siren,
            entreprise="ACME", code_naf="41.20B",
        )
        po = PendingOutbound.objects.create(
            prospect_public_id=lead.public_identifier,
            prospect_company="ACME",
            kind=kind, subject=subject, ai_draft=body,
            status=status,
        )
        return lead, po
    return _build


class TestSendColdEmailSuccess:
    def test_envoi_reussi_appelle_send_mail(self, make_lead_with_po, monkeypatch):
        lead, po = make_lead_with_po()
        captured = {}

        def _mock_send(*, subject, html_body, to):
            captured["subject"] = subject
            captured["html_body"] = html_body
            captured["to"] = to

        monkeypatch.setattr("ekoalu.email_canal.sender.send_mail", _mock_send)
        success, err = send_cold_email(po)
        assert success is True
        assert err == ""
        assert captured["to"] == "dirigeant@acme.fr"
        assert captured["subject"] == "Coupe-feu EI60 pour vos projets"
        assert "Coupe-feu EI60" in captured["html_body"]
        assert "<p " in captured["html_body"]  # HTML conversion


class TestSendColdEmailFailures:
    def test_lead_introuvable(self, db, monkeypatch):
        monkeypatch.setattr("ekoalu.email_canal.sender.send_mail",
                            lambda **kw: pytest.fail("ne doit pas être appelé"))
        po = PendingOutbound.objects.create(
            prospect_public_id="bdd-prospect-zzz999",  # inexistant
            kind=OutboundKind.EMAIL_COLD, subject="x", ai_draft="y",
            status=OutboundStatus.APPROVED,
        )
        success, err = send_cold_email(po)
        assert not success
        assert "destinataire" in err.lower()

    def test_lead_unsubscribed(self, make_lead_with_po, monkeypatch):
        lead, po = make_lead_with_po(unsubscribed=True)
        monkeypatch.setattr("ekoalu.email_canal.sender.send_mail",
                            lambda **kw: pytest.fail("ne doit pas être appelé"))
        success, err = send_cold_email(po)
        assert not success
        assert "destinataire" in err.lower()

    def test_kind_non_email_refuse(self, make_lead_with_po, monkeypatch):
        lead, po = make_lead_with_po(kind=OutboundKind.INVITATION)
        monkeypatch.setattr("ekoalu.email_canal.sender.send_mail",
                            lambda **kw: pytest.fail("ne doit pas être appelé"))
        success, err = send_cold_email(po)
        assert not success
        assert "kind" in err.lower()

    def test_subject_vide(self, make_lead_with_po, monkeypatch):
        lead, po = make_lead_with_po(subject="")
        monkeypatch.setattr("ekoalu.email_canal.sender.send_mail",
                            lambda **kw: pytest.fail("ne doit pas être appelé"))
        success, err = send_cold_email(po)
        assert not success
        assert "subject" in err.lower()

    def test_graph_send_error_capturee(self, make_lead_with_po, monkeypatch):
        lead, po = make_lead_with_po()

        def _raise(**kw):
            raise GraphSendError("sendMail 503: Service unavailable")

        monkeypatch.setattr("ekoalu.email_canal.sender.send_mail", _raise)
        success, err = send_cold_email(po)
        assert not success
        assert "graph_send" in err
        assert "503" in err


# --- Management command send_approved_emails --------------------------------


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Désactive time.sleep dans la commande pour ne pas attendre 90s entre tests."""
    monkeypatch.setattr("ekoalu.management.commands.send_approved_emails.time.sleep",
                        lambda _s: None)


class TestSendApprovedEmailsCommand:
    def test_dry_run_ne_change_pas_le_statut(self, make_lead_with_po, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_emails.send_cold_email",
            lambda po: called.__setitem__("n", called["n"] + 1) or (True, ""),
        )
        _, po = make_lead_with_po()
        call_command("send_approved_emails", dry_run=True, ignore_schedule=True,
                     stdout=StringIO())
        po.refresh_from_db()
        assert po.status == OutboundStatus.APPROVED  # inchangé
        assert called["n"] == 0  # sender pas appelé

    def test_succes_passe_a_sent(self, make_lead_with_po, monkeypatch):
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_emails.send_cold_email",
            lambda po: (True, ""),
        )
        _, po = make_lead_with_po()
        call_command("send_approved_emails", ignore_schedule=True, stdout=StringIO())
        po.refresh_from_db()
        assert po.status == OutboundStatus.SENT
        assert po.sent_at is not None
        assert po.error_message == ""

    def test_echec_passe_a_failed_avec_message(self, make_lead_with_po, monkeypatch):
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_emails.send_cold_email",
            lambda po: (False, "graph_send: 503 Service unavailable"),
        )
        _, po = make_lead_with_po()
        call_command("send_approved_emails", ignore_schedule=True, stdout=StringIO())
        po.refresh_from_db()
        assert po.status == OutboundStatus.FAILED
        assert "503" in po.error_message

    def test_max_cap_le_nombre_envois(self, make_lead_with_po, monkeypatch):
        for i in range(5):
            make_lead_with_po(siren=f"6000{i:05d}", email=f"x{i}@a.fr")
        called = {"n": 0}
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_emails.send_cold_email",
            lambda po: (called.update(n=called["n"] + 1) or True, ""),
        )
        call_command("send_approved_emails", max=2, ignore_schedule=True,
                     stdout=StringIO())
        sent = PendingOutbound.objects.filter(status=OutboundStatus.SENT).count()
        assert sent == 2
        assert called["n"] == 2

    def test_hors_plage_horaire_skip_tout(self, make_lead_with_po, monkeypatch):
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_emails.is_action_allowed_now",
            lambda: False,
        )
        # send_cold_email ne doit JAMAIS être appelé
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_emails.send_cold_email",
            lambda po: pytest.fail("ne doit pas être appelé hors plage"),
        )
        _, po = make_lead_with_po()
        call_command("send_approved_emails", stdout=StringIO())  # sans --ignore-schedule
        po.refresh_from_db()
        assert po.status == OutboundStatus.APPROVED  # inchangé

    def test_ignore_schedule_bypass_horaire(self, make_lead_with_po, monkeypatch):
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_emails.is_action_allowed_now",
            lambda: False,
        )
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_emails.send_cold_email",
            lambda po: (True, ""),
        )
        _, po = make_lead_with_po()
        call_command("send_approved_emails", ignore_schedule=True, stdout=StringIO())
        po.refresh_from_db()
        assert po.status == OutboundStatus.SENT

    def test_seuls_kinds_email_sont_traites(self, make_lead_with_po, monkeypatch):
        """Un INVITATION approved ne doit pas être envoyé par cette commande."""
        called = {"n": 0}
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_emails.send_cold_email",
            lambda po: (called.update(n=called["n"] + 1) or True, ""),
        )
        _, po_invite = make_lead_with_po(kind=OutboundKind.INVITATION,
                                         siren="700000001", email="i@a.fr")
        _, po_email = make_lead_with_po(kind=OutboundKind.EMAIL_COLD,
                                        siren="700000002", email="e@a.fr")
        call_command("send_approved_emails", ignore_schedule=True, stdout=StringIO())
        po_invite.refresh_from_db()
        po_email.refresh_from_db()
        assert po_invite.status == OutboundStatus.APPROVED  # intact
        assert po_email.status == OutboundStatus.SENT
        assert called["n"] == 1

    def test_seuls_status_approved_sont_traites(self, make_lead_with_po, monkeypatch):
        monkeypatch.setattr(
            "ekoalu.management.commands.send_approved_emails.send_cold_email",
            lambda po: (True, ""),
        )
        _, po_pending = make_lead_with_po(status=OutboundStatus.PENDING,
                                          siren="800000001", email="p@a.fr")
        _, po_sent_avant = make_lead_with_po(status=OutboundStatus.SENT,
                                             siren="800000002", email="s@a.fr")
        call_command("send_approved_emails", ignore_schedule=True, stdout=StringIO())
        po_pending.refresh_from_db()
        po_sent_avant.refresh_from_db()
        assert po_pending.status == OutboundStatus.PENDING  # intact
        assert po_sent_avant.status == OutboundStatus.SENT  # intact
