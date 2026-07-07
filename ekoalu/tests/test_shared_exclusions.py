"""LOT D : liste d'exclusion partagée _partage/exclusions.json.

Le CLAUDE.md projet promet qu'elle est lue avant tout envoi — ces tests
verrouillent les 3 consommateurs : sender email, generate_cold_emails,
import_mailjet_hot_leads. + tolérance fichier absent/corrompu + lowercase.
"""
from __future__ import annotations

import json

import pytest
from django.core.management import call_command

from ekoalu import shared_exclusions
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound


def _write_exclusions(tmp_path, monkeypatch, emails: list[str]):
    p = tmp_path / "exclusions.json"
    payload = {
        "updated_at": "2026-07-07",
        "exclusions": [
            {"email": e, "reason": "hard_bounce", "source": "mailjet:1", "added_at": "2026-07-07"}
            for e in emails
        ],
    }
    p.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv(shared_exclusions.ENV_VAR, str(p))
    shared_exclusions._cache = None
    return p


# --- module ------------------------------------------------------------------


class TestModule:
    def test_fichier_absent_liste_vide_sans_crash(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setenv(shared_exclusions.ENV_VAR, str(tmp_path / "absent.json"))
        shared_exclusions._cache = None
        with caplog.at_level("WARNING"):
            assert shared_exclusions.excluded_emails(refresh=True) == frozenset()
        assert "introuvable" in caplog.text

    def test_fichier_corrompu_liste_vide_sans_crash(self, tmp_path, monkeypatch, caplog):
        p = tmp_path / "exclusions.json"
        p.write_text("{pas du json", encoding="utf-8")
        monkeypatch.setenv(shared_exclusions.ENV_VAR, str(p))
        shared_exclusions._cache = None
        with caplog.at_level("WARNING"):
            assert shared_exclusions.excluded_emails(refresh=True) == frozenset()
        assert "illisible" in caplog.text

    def test_comparaison_lowercase(self, tmp_path, monkeypatch):
        _write_exclusions(tmp_path, monkeypatch, ["Jean.DUPONT@Acme.FR"])
        assert shared_exclusions.is_excluded("jean.dupont@acme.fr")
        assert shared_exclusions.is_excluded("JEAN.DUPONT@ACME.FR")
        assert not shared_exclusions.is_excluded("autre@acme.fr")
        assert not shared_exclusions.is_excluded("")
        assert not shared_exclusions.is_excluded(None)

    def test_cache_ttl_puis_refresh(self, tmp_path, monkeypatch):
        p = _write_exclusions(tmp_path, monkeypatch, ["a@b.fr"])
        assert shared_exclusions.is_excluded("a@b.fr")
        # Le fichier change : le cache TTL sert encore l'ancien contenu…
        p.write_text(json.dumps({"exclusions": []}), encoding="utf-8")
        assert shared_exclusions.is_excluded("a@b.fr")
        # …mais refresh force la relecture.
        assert shared_exclusions.excluded_emails(refresh=True) == frozenset()


# --- consommateur (a) : sender email -----------------------------------------


@pytest.mark.django_db
class TestSenderBlocksExcluded:
    def _lead_po(self, email="cible@acme.fr"):
        from crm.models import Lead
        lead = Lead.objects.create(
            linkedin_url="https://bdd-prospect.local/siren/111222333",
            public_identifier="bdd-prospect-111222333",
            contact_email=email,
        )
        po = PendingOutbound.objects.create(
            prospect_public_id=lead.public_identifier, prospect_company="ACME",
            kind=OutboundKind.EMAIL_COLD, subject="Coupe-feu EI60",
            ai_draft="Bonjour", status=OutboundStatus.APPROVED,
        )
        return lead, po

    def test_envoi_bloque_si_email_exclu(self, tmp_path, monkeypatch):
        from ekoalu.email_canal.sender import send_cold_email
        _write_exclusions(tmp_path, monkeypatch, ["CIBLE@acme.fr"])
        lead, po = self._lead_po()
        called = []
        monkeypatch.setattr("ekoalu.email_canal.sender.send_mail",
                            lambda **kw: called.append(kw))
        success, err = send_cold_email(po)
        assert success is False
        assert called == []
        assert "bloqué" in err

    def test_envoi_bloque_si_lead_disqualifie(self, tmp_path, monkeypatch):
        """Symétrie canal LinkedIn : refus Richard APRÈS approbation = pas d'envoi."""
        from ekoalu.email_canal.sender import send_cold_email
        lead, po = self._lead_po()
        lead.disqualified = True
        lead.save(update_fields=["disqualified"])
        called = []
        monkeypatch.setattr("ekoalu.email_canal.sender.send_mail",
                            lambda **kw: called.append(kw))
        success, _err = send_cold_email(po)
        assert success is False
        assert called == []

    def test_envoi_passe_si_non_exclu(self, tmp_path, monkeypatch):
        from ekoalu.email_canal.sender import send_cold_email
        _write_exclusions(tmp_path, monkeypatch, ["autre@x.fr"])
        lead, po = self._lead_po()
        called = []
        monkeypatch.setattr("ekoalu.email_canal.sender.send_mail",
                            lambda **kw: called.append(kw))
        success, err = send_cold_email(po)
        assert success is True, err
        assert len(called) == 1


# --- consommateur (b) : generate_cold_emails ----------------------------------


@pytest.mark.django_db
class TestGenerateSkipsExcluded:
    def _make_email_lead(self, email, siren):
        from crm.models import Lead
        from ekoalu.email_canal.models import EmailLeadData
        lead = Lead.objects.create(
            linkedin_url=f"https://bdd-prospect.local/siren/{siren}",
            public_identifier=f"bdd-prospect-{siren}",
            contact_email=email,
        )
        EmailLeadData.objects.create(
            lead=lead, source="bdd_prospect", siren=siren,
            entreprise="ACME", code_naf="41.20B",
        )
        return lead

    def test_generation_skip_les_exclus(self, tmp_path, monkeypatch):
        _write_exclusions(tmp_path, monkeypatch, ["Exclu@acme.fr"])
        self._make_email_lead("exclu@acme.fr", "111000111")
        ok = self._make_email_lead("ok@acme.fr", "222000222")

        generated = []

        class _Draft:
            subject = "Coupe-feu EI60 à Chasselay"
            body = "On fabrique du coupe-feu EI60."
            variant_used = "v1"
            def is_valid(self):
                return True

        def _fake_generate(**kwargs):
            generated.append(kwargs)
            return _Draft()

        monkeypatch.setattr(
            "ekoalu.management.commands.generate_cold_emails.generate_cold_email",
            _fake_generate)
        monkeypatch.setattr(
            "ekoalu.management.commands.generate_cold_emails.has_niche_mention",
            lambda body: True)

        call_command("generate_cold_emails", "--limit", "10")

        assert len(generated) == 1
        pos = PendingOutbound.objects.filter(kind=OutboundKind.EMAIL_COLD)
        assert pos.count() == 1
        assert pos.first().prospect_public_id == ok.public_identifier


# --- consommateur (c) : import_mailjet_hot_leads ------------------------------


@pytest.mark.django_db
class TestImportSkipsExcluded:
    def _deposit(self, tmp_path, rows):
        p = tmp_path / "mailjet_hot_leads.json"
        p.write_text(json.dumps(rows), encoding="utf-8")
        return p

    def test_import_skip_les_exclus(self, tmp_path, monkeypatch):
        from crm.models import Lead
        _write_exclusions(tmp_path, monkeypatch, ["bounce@acme.fr"])
        src = self._deposit(tmp_path, [
            {"email": "Bounce@ACME.fr", "event_type": "click", "siren": "333000333"},
            {"email": "ouvre@acme.fr", "event_type": "open", "siren": "444000444"},
        ])
        call_command("import_mailjet_hot_leads", "--source", str(src))
        emails = set(Lead.objects.values_list("contact_email", flat=True))
        assert emails == {"ouvre@acme.fr"}

    def test_import_dedup_insensible_a_la_casse(self, tmp_path):
        """P3 LOT D : un lead déjà en DB avec une casse différente n'est PAS réimporté."""
        from crm.models import Lead
        Lead.objects.create(
            linkedin_url="https://mailjet-hot.local/existant",
            public_identifier="mailjet-hot-existant",
            contact_email="Jean.Dupont@Acme.FR",
        )
        src = self._deposit(tmp_path, [
            {"email": "jean.dupont@acme.fr", "event_type": "open"},
        ])
        call_command("import_mailjet_hot_leads", "--source", str(src))
        assert Lead.objects.count() == 1  # pas de doublon créé
