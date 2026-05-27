"""Tests fiabilité du module email_generator + management command generate_cold_emails."""
from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from ekoalu.email_generator import ColdEmailDraft, generate_cold_email
from ekoalu.email_generator.generator import (
    _ensure_signature,
    has_niche_mention,
    parse_response,
)
from ekoalu.email_generator.prompts import build_user_message, render_system_prompt


# --- parse_response ----------------------------------------------------------


class TestParseResponse:
    def test_parse_complet(self):
        raw = """<sujet>
Coupe-feu EI60 pour vos projets tertiaires
</sujet>

<corps>
Bonjour M. Dupont,

Votre activité…

Cordialement,
Richard
</corps>"""
        d = parse_response(raw)
        assert d.subject == "Coupe-feu EI60 pour vos projets tertiaires"
        assert "Bonjour M. Dupont" in d.body
        assert "Cordialement" in d.body

    def test_parse_vide_si_balises_manquantes(self):
        d = parse_response("juste du texte sans balises")
        assert d.subject == ""
        assert d.body == ""
        assert not d.is_valid()

    def test_parse_vide_si_input_vide(self):
        assert parse_response("").subject == ""
        assert parse_response("").body == ""

    def test_balises_case_insensitive(self):
        raw = "<SUJET>obj</SUJET><CORPS>b</CORPS>"
        d = parse_response(raw)
        assert d.subject == "obj"
        assert d.body == "b"

    def test_strip_espaces_autour(self):
        d = parse_response("<sujet>   hello   </sujet><corps>   world   </corps>")
        assert d.subject == "hello"
        assert d.body == "world"


# --- _ensure_signature -------------------------------------------------------


class TestEnsureSignature:
    def test_signature_deja_presente_inchange(self):
        from ekoalu import conf
        body = f"Bonjour,\n\n{conf.render_signature()}"
        assert _ensure_signature(body) == body

    def test_signature_ajoutee_si_absente(self):
        from ekoalu import conf
        body = "Bonjour, rien d'autre."
        out = _ensure_signature(body)
        assert conf.SIGNATURE_NAME in out
        assert conf.SIGNATURE_EMAIL in out
        assert out.startswith("Bonjour, rien d'autre.")

    def test_body_vide_reste_vide(self):
        assert _ensure_signature("") == ""


# --- has_niche_mention -------------------------------------------------------


class TestHasNicheMention:
    @pytest.mark.parametrize("text", [
        "Nous faisons du coupe-feu EI60.",
        "EI 30 minimum",
        "Désenfumage DENFC norme",
        "Pare-balles BC2",
        "Mur-rideau aluminium",
        "Grandes dimensions 4m",
        "Acoustique Rw>40",
        "Rw 42",
    ])
    def test_detecte_niche(self, text):
        assert has_niche_mention(text)

    @pytest.mark.parametrize("text", [
        "",
        "Bonjour Monsieur, nous fabriquons des fenêtres.",
        "Solutions clé en main pour vos projets.",
    ])
    def test_sans_niche_retourne_false(self, text):
        assert not has_niche_mention(text)


# --- prompts: render & build -------------------------------------------------


class TestPromptRendering:
    def test_system_prompt_inclut_signature_et_booking(self):
        from ekoalu import conf
        sys = render_system_prompt()
        assert conf.SIGNATURE_NAME in sys
        if conf.CALENDAR_BOOKING_URL:
            assert conf.CALENDAR_BOOKING_URL in sys

    def test_system_prompt_contient_mots_bannis_explicites(self):
        sys = render_system_prompt()
        # Sanity : le prompt doit explicitement lister les mots bannis pour Claude
        for forbidden in ["synergies", "leader", "au plaisir d'échanger"]:
            assert forbidden in sys

    def test_user_message_avec_donnees_completes(self):
        msg = build_user_message(
            entreprise="ACME BAT", dirigeant="JEAN DUPONT", code_naf="41.20B",
            activite="ENTREPRISES DE MENUISERIE", ville="LYON", dpt="69",
            effectif_min=20, effectif_max=49,
        )
        assert "ACME BAT" in msg
        assert "JEAN DUPONT" in msg
        assert "41.20B" in msg
        assert "LYON" in msg
        assert "Rhône-Alpes" in msg or "rhône-alpes" in msg.lower()

    def test_user_message_dirigeant_inconnu(self):
        msg = build_user_message(
            entreprise="X", dirigeant="", code_naf="41.20B",
            activite="", ville="", dpt="", effectif_min=0, effectif_max=0,
        )
        assert "inconnu" in msg.lower()

    def test_user_message_dpt_hors_region_pas_de_hint(self):
        msg = build_user_message(
            entreprise="X", dirigeant="Y", code_naf="41.20B",
            activite="", ville="PARIS", dpt="75",
            effectif_min=10, effectif_max=20,
        )
        assert "Rhône-Alpes" not in msg
        assert "proche atelier" not in msg


# --- generate_cold_email : sans API key ⇒ retour vide ------------------------


class TestGenerateColdEmailSansApiKey:
    def test_pas_de_client_retour_vide(self, monkeypatch):
        # Force absence de clé API
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(
            "ekoalu.email_generator.generator._get_anthropic_client",
            lambda: None,
        )
        d = generate_cold_email(
            entreprise="X", dirigeant="Y", code_naf="41.20B",
            activite="", ville="", dpt="", effectif_min=10, effectif_max=20,
        )
        assert not d.is_valid()


# --- Management command : test d'intégration avec generate_cold_email mocké --


@pytest.fixture
def fake_draft():
    """Brouillon Claude factice avec niche obligatoire et signature."""
    from ekoalu import conf
    body = (
        "Bonjour M. Dupont,\n\n"
        "Vous gérez des projets tertiaires en région Rhône-Alpes ?\n\n"
        "Chez EKOALU (Chasselay 69), on fabrique du coupe-feu EI60 et "
        "du désenfumage pour bureaux et ERP.\n\n"
        "15 min en visio si pertinent : https://outlook.office365.com/book/...\n\n"
        f"{conf.render_signature()}"
    )
    return ColdEmailDraft(
        subject="Coupe-feu EI60 pour vos projets tertiaires",
        body=body,
        model_used="claude-sonnet-4-6",
    )


@pytest.fixture
def make_lead(db):
    """Builder pour créer un Lead + EmailLeadData en DB."""
    from crm.models import Lead
    from ekoalu.email_canal.models import EmailLeadData

    def _build(*, siren="111111111", email="x@y.fr", entreprise="X",
               dirigeant="Y", code_naf="41.20B", dpt="69", ville="LYON",
               unsubscribed=False):
        lead = Lead.objects.create(
            linkedin_url=f"https://bdd-prospect.local/siren/{siren}",
            public_identifier=f"bdd-prospect-{siren}",
            contact_email=email,
            contact_email_source="bdd_prospect",
            unsubscribed_at=timezone.now() if unsubscribed else None,
        )
        EmailLeadData.objects.create(
            lead=lead,
            source=EmailLeadData.SOURCE_BDD_PROSPECT,
            siren=siren, entreprise=entreprise, dirigeant=dirigeant,
            code_naf=code_naf, dpt=dpt, ville=ville,
            effectif_min=15, effectif_max=49, activite="",
        )
        return lead
    return _build


class TestGenerateColdEmailsCommand:
    def test_genere_un_cold_mail_et_cree_pendingoutbound(self, make_lead, monkeypatch, fake_draft):
        from ekoalu.outbound_validation.models import OutboundKind, PendingOutbound
        lead = make_lead()
        monkeypatch.setattr(
            "ekoalu.management.commands.generate_cold_emails.generate_cold_email",
            lambda **kw: fake_draft,
        )
        call_command("generate_cold_emails", limit=10, stdout=StringIO())
        po = PendingOutbound.objects.get(prospect_public_id=lead.public_identifier,
                                         kind=OutboundKind.EMAIL_COLD)
        assert po.subject == fake_draft.subject
        assert po.ai_draft == fake_draft.body
        assert po.status == "pending"
        assert po.prospect_company == "X"

    def test_dry_run_ne_cree_aucun_pendingoutbound(self, make_lead, monkeypatch, fake_draft):
        from ekoalu.outbound_validation.models import PendingOutbound
        make_lead()
        monkeypatch.setattr(
            "ekoalu.management.commands.generate_cold_emails.generate_cold_email",
            lambda **kw: fake_draft,
        )
        call_command("generate_cold_emails", dry_run=True, stdout=StringIO())
        assert PendingOutbound.objects.count() == 0

    def test_idempotence_pas_de_double_generation(self, make_lead, monkeypatch, fake_draft):
        from ekoalu.outbound_validation.models import OutboundKind, PendingOutbound
        make_lead()
        called = {"n": 0}

        def _mock(**kw):
            called["n"] += 1
            return fake_draft

        monkeypatch.setattr(
            "ekoalu.management.commands.generate_cold_emails.generate_cold_email",
            _mock,
        )
        call_command("generate_cold_emails", limit=10, stdout=StringIO())
        call_command("generate_cold_emails", limit=10, stdout=StringIO())
        # 1 seul PendingOutbound créé (idempotent) et 1 seul appel Claude
        assert PendingOutbound.objects.filter(kind=OutboundKind.EMAIL_COLD).count() == 1
        assert called["n"] == 1

    def test_exclu_si_unsubscribed(self, make_lead, monkeypatch, fake_draft):
        from ekoalu.outbound_validation.models import PendingOutbound
        make_lead(unsubscribed=True)
        monkeypatch.setattr(
            "ekoalu.management.commands.generate_cold_emails.generate_cold_email",
            lambda **kw: fake_draft,
        )
        call_command("generate_cold_emails", limit=10, stdout=StringIO())
        assert PendingOutbound.objects.count() == 0

    def test_exclu_si_pas_de_niche_dans_draft(self, make_lead, monkeypatch):
        """Si Claude oublie de mentionner une niche → on rejette le draft."""
        from ekoalu.outbound_validation.models import PendingOutbound

        bad = ColdEmailDraft(
            subject="Bonjour",
            body="Bonjour M. Dupont, nous fabriquons des fenêtres.\n\nCordialement",
        )
        make_lead()
        monkeypatch.setattr(
            "ekoalu.management.commands.generate_cold_emails.generate_cold_email",
            lambda **kw: bad,
        )
        call_command("generate_cold_emails", limit=10, stdout=StringIO())
        assert PendingOutbound.objects.count() == 0

    def test_exclu_si_draft_vide(self, make_lead, monkeypatch):
        from ekoalu.outbound_validation.models import PendingOutbound
        make_lead()
        monkeypatch.setattr(
            "ekoalu.management.commands.generate_cold_emails.generate_cold_email",
            lambda **kw: ColdEmailDraft(subject="", body=""),
        )
        call_command("generate_cold_emails", limit=10, stdout=StringIO())
        assert PendingOutbound.objects.count() == 0

    def test_limit_cap_la_generation(self, make_lead, monkeypatch, fake_draft):
        from ekoalu.outbound_validation.models import PendingOutbound
        for i in range(5):
            make_lead(siren=f"100000{i}00", email=f"x{i}@y.fr")
        monkeypatch.setattr(
            "ekoalu.management.commands.generate_cold_emails.generate_cold_email",
            lambda **kw: fake_draft,
        )
        call_command("generate_cold_emails", limit=2, stdout=StringIO())
        assert PendingOutbound.objects.count() == 2

    def test_filtre_dpt(self, make_lead, monkeypatch, fake_draft):
        from ekoalu.outbound_validation.models import PendingOutbound
        make_lead(siren="100000001", email="rhone@a.fr", dpt="69")
        make_lead(siren="100000002", email="paris@a.fr", dpt="75")
        monkeypatch.setattr(
            "ekoalu.management.commands.generate_cold_emails.generate_cold_email",
            lambda **kw: fake_draft,
        )
        call_command("generate_cold_emails", dpt="69", limit=10, stdout=StringIO())
        leads = PendingOutbound.objects.values_list("prospect_public_id", flat=True)
        assert "bdd-prospect-100000001" in leads
        assert "bdd-prospect-100000002" not in leads

    def test_lead_sans_email_data_ignore(self, db, monkeypatch, fake_draft):
        """Un Lead sans EmailLeadData n'est pas candidat."""
        from crm.models import Lead
        from ekoalu.outbound_validation.models import PendingOutbound

        Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/jdupont",
            public_identifier="jdupont",
            contact_email="j@dupont.com",
            contact_email_source="bdd_prospect",
        )
        monkeypatch.setattr(
            "ekoalu.management.commands.generate_cold_emails.generate_cold_email",
            lambda **kw: fake_draft,
        )
        call_command("generate_cold_emails", limit=10, stdout=StringIO())
        assert PendingOutbound.objects.count() == 0
