"""Tests brique H : A/B testing prompts cold mail."""
from __future__ import annotations

from datetime import date
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from ekoalu.email_generator.generator import generate_cold_email
from ekoalu.email_generator.models import ColdEmailDraft
from ekoalu.email_generator.prompts import (
    DEFAULT_VARIANT,
    PROMPT_VARIANTS,
    pick_variant,
    render_system_prompt,
)
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

pytestmark = pytest.mark.django_db


# --- pick_variant -----------------------------------------------------------


class TestPickVariant:
    def test_renvoie_un_id_du_registry(self):
        for _ in range(20):
            v = pick_variant()
            assert v in PROMPT_VARIANTS

    def test_avec_registry_custom(self):
        custom = {"a": ("template a", 1.0), "b": ("template b", 1.0)}
        for _ in range(20):
            assert pick_variant(custom) in ("a", "b")

    def test_distribution_equitable_sur_n_tirages(self):
        """Avec 2 variantes poids égaux, sur 500 tirages chaque doit dépasser 150."""
        counts = {"v1": 0, "v2": 0}
        for _ in range(500):
            v = pick_variant()
            counts[v] = counts.get(v, 0) + 1
        # tolérance large : chaque variante doit avoir au moins 150 / 500 (30%)
        assert counts["v1"] >= 150
        assert counts["v2"] >= 150

    def test_poids_non_egaux_respecte_la_dominance(self):
        custom = {"x": ("tpl x", 10.0), "y": ("tpl y", 1.0)}
        counts = {"x": 0, "y": 0}
        for _ in range(500):
            counts[pick_variant(custom)] += 1
        # x doit être très majoritaire (au moins 80%)
        assert counts["x"] >= 400

    def test_registry_vide_fallback(self):
        assert pick_variant({}) == DEFAULT_VARIANT


# --- render_system_prompt --------------------------------------------------


class TestRenderSystemPrompt:
    def test_v1_et_v2_different(self):
        p1 = render_system_prompt("v1")
        p2 = render_system_prompt("v2")
        assert p1 != p2
        # v2 doit contenir un marker spécifique (preuves chiffrées)
        assert "preuves chiffrées" in p2 or "atelier intégré 20 personnes" in p2

    def test_variante_inconnue_fallback_default(self):
        p_unknown = render_system_prompt("wat")
        p_default = render_system_prompt(DEFAULT_VARIANT)
        assert p_unknown == p_default

    def test_signature_dans_les_2(self):
        from ekoalu import conf
        assert conf.SIGNATURE_NAME in render_system_prompt("v1")
        assert conf.SIGNATURE_NAME in render_system_prompt("v2")


# --- generate_cold_email propage variant -----------------------------------


class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeResp:
    def __init__(self, text):
        self.content = [_FakeContent(text)]


class _FakeClient:
    def __init__(self, capture: dict | None = None):
        self.capture = capture if capture is not None else {}
        self.messages = self

    def create(self, **kw):
        self.capture["system"] = kw["system"]
        return _FakeResp(
            "<sujet>Re: x</sujet><corps>Bonjour. coupe-feu EI60.\nRichard</corps>"
        )


class TestGenerateColdEmailVariant:
    def test_variant_explicite_propage(self, monkeypatch):
        cap = {}
        monkeypatch.setattr(
            "ekoalu.email_generator.generator._get_anthropic_client",
            lambda: _FakeClient(cap),
        )
        d = generate_cold_email(
            entreprise="X", dirigeant="Y", code_naf="41.20B",
            activite="", ville="", dpt="", effectif_min=10, effectif_max=20,
            variant="v2",
        )
        assert d.variant_used == "v2"
        # Le system envoyé à Claude doit être bien la v2
        assert "preuves chiffrées" in cap["system"] or "atelier intégré 20" in cap["system"]

    def test_variant_par_defaut_tire_au_sort(self, monkeypatch):
        monkeypatch.setattr(
            "ekoalu.email_generator.generator._get_anthropic_client",
            lambda: _FakeClient(),
        )
        d = generate_cold_email(
            entreprise="X", dirigeant="Y", code_naf="41.20B",
            activite="", ville="", dpt="", effectif_min=10, effectif_max=20,
        )
        # Une variante valide doit être assignée
        assert d.variant_used in PROMPT_VARIANTS

    def test_variant_persiste_meme_en_cas_d_echec(self, monkeypatch):
        """Si pas de client, le draft sort vide mais variant_used reste rempli."""
        monkeypatch.setattr(
            "ekoalu.email_generator.generator._get_anthropic_client",
            lambda: None,
        )
        d = generate_cold_email(
            entreprise="X", dirigeant="Y", code_naf="41.20B",
            activite="", ville="", dpt="", effectif_min=10, effectif_max=20,
            variant="v2",
        )
        assert d.variant_used == "v2"
        assert not d.is_valid()


# --- Management command tag prompt_variant sur PendingOutbound -------------


@pytest.fixture
def make_lead(db):
    from crm.models import Lead
    from ekoalu.email_canal.models import EmailLeadData

    def _build(*, siren="h00001", email="h@x.fr"):
        lead = Lead.objects.create(
            linkedin_url=f"https://bdd-prospect.local/siren/{siren}",
            public_identifier=f"bdd-prospect-{siren}",
            contact_email=email,
            contact_email_source="bdd_prospect",
        )
        EmailLeadData.objects.create(
            lead=lead, source="bdd_prospect", siren=siren,
            entreprise="ACME", dirigeant="X", code_naf="41.20B",
        )
        return lead
    return _build


class TestCommandTagsVariant:
    def test_pendingoutbound_a_un_prompt_variant_apres_generation(
        self, make_lead, monkeypatch,
    ):
        make_lead()
        draft = ColdEmailDraft(
            subject="Coupe-feu",
            body="Bonjour M. Dupont,\n\ncoupe-feu EI60.\n\nRichard",
            variant_used="v2",
        )
        monkeypatch.setattr(
            "ekoalu.management.commands.generate_cold_emails.generate_cold_email",
            lambda **kw: draft,
        )
        call_command("generate_cold_emails", limit=10, stdout=StringIO())
        po = PendingOutbound.objects.get(kind=OutboundKind.EMAIL_COLD)
        assert po.prompt_variant == "v2"


# --- Daily recap breakdown par variante ------------------------------------


class TestDailyRecapAbBreakdown:
    def test_breakdown_par_variante(self, db):
        from ekoalu.management.commands.daily_recap import compute_stats

        now = timezone.now()
        for _ in range(3):
            PendingOutbound.objects.create(
                prospect_public_id=f"x{_}", kind=OutboundKind.EMAIL_COLD,
                ai_draft="x", subject="s", status=OutboundStatus.SENT, sent_at=now,
                prompt_variant="v1",
            )
        for _ in range(2):
            PendingOutbound.objects.create(
                prospect_public_id=f"y{_}", kind=OutboundKind.EMAIL_COLD,
                ai_draft="x", subject="s", status=OutboundStatus.SENT, sent_at=now,
                prompt_variant="v2",
            )
        # Un PO sans variante (ancien) ne doit pas casser le compteur
        PendingOutbound.objects.create(
            prospect_public_id="z0", kind=OutboundKind.EMAIL_COLD,
            ai_draft="x", subject="s", status=OutboundStatus.SENT, sent_at=now,
            prompt_variant="",
        )

        stats = compute_stats(date.today())
        assert stats.email_cold_by_variant == {"v1": 3, "v2": 2}
