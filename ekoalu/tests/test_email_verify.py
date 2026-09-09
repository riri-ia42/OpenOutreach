"""Vérification d'existence d'une adresse avant l'envoi (fiche hub validée 2026-09-09).

Aucun réseau : DNS et SMTP sont simulés. Le contrat testé est celui qui protège
la réputation d'ekoalu.com sans jamais bloquer un vrai prospect dans le doute.
"""
from __future__ import annotations

import json

import pytest

from ekoalu.email_canal import verify
from ekoalu.email_canal.sender import send_cold_email
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound


class _FakeSmtp:
    """Serveur SMTP simulé : `rcpt_codes` = {adresse: (code, message)}, défaut pour l'aléatoire."""

    def __init__(self, rcpt_codes, default=(250, b"ok"), fail_connect=False):
        self._codes = rcpt_codes
        self._default = default
        self._fail = fail_connect

    def __call__(self, host, port, timeout):
        if self._fail:
            raise OSError("connexion refusée")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self, name=""):
        return 250, b"ok"

    def has_extn(self, name):
        return False

    def mail(self, sender):
        return 250, b"ok"

    def rcpt(self, addr):
        return self._codes.get(addr, self._default)


MX = ["mx1.acme.fr", "mx2.acme.fr"]


class TestSmtpProbe:
    def test_accepte_et_aleatoire_refuse_devient_ok(self):
        smtp = _FakeSmtp({"jean@acme.fr": (250, b"ok")}, default=(550, b"5.1.1 user unknown"))
        assert verify.smtp_probe("jean@acme.fr", MX, smtp_factory=smtp)[0] == "ok"

    def test_catch_all_est_invérifiable(self):
        smtp = _FakeSmtp({}, default=(250, b"ok"))
        verdict, detail = verify.smtp_probe("jean@acme.fr", MX, smtp_factory=smtp)
        assert verdict == "unknown"
        assert "catch-all" in detail

    def test_refus_cible_mais_aleatoire_accepte_est_invalide(self):
        smtp = _FakeSmtp({"jean@acme.fr": (550, b"5.1.1 no such user")}, default=(250, b"ok"))
        assert verify.smtp_probe("jean@acme.fr", MX, smtp_factory=smtp)[0] == "invalid"

    def test_double_refus_utilisateur_inconnu_est_invalide(self):
        smtp = _FakeSmtp({}, default=(550, b"5.1.1 <x>: Recipient address rejected: User unknown"))
        assert verify.smtp_probe("jean@acme.fr", MX, smtp_factory=smtp)[0] == "invalid"

    def test_double_refus_politique_reste_inconnu(self):
        # Une IP bloquée ou un relais refusé refuse TOUT : ce n'est pas l'adresse qui est morte.
        smtp = _FakeSmtp({}, default=(550, b"5.7.1 Service unavailable, client blocked"))
        assert verify.smtp_probe("jean@acme.fr", MX, smtp_factory=smtp)[0] == "unknown"

    def test_greylisting_reste_inconnu(self):
        smtp = _FakeSmtp({"jean@acme.fr": (451, b"4.7.1 try again later")})
        assert verify.smtp_probe("jean@acme.fr", MX, smtp_factory=smtp)[0] == "unknown"

    def test_mx_injoignable_reste_inconnu(self):
        smtp = _FakeSmtp({}, fail_connect=True)
        assert verify.smtp_probe("jean@acme.fr", MX, smtp_factory=smtp)[0] == "unknown"


class TestVerifyAddress:
    @pytest.fixture(autouse=True)
    def _enabled(self, monkeypatch):
        monkeypatch.setenv(verify.ENV_VAR, "1")
        verify._mx_cache.clear()

    def test_kill_switch(self, monkeypatch):
        monkeypatch.setenv(verify.ENV_VAR, "0")
        assert verify.verify_address("x@acme.fr", "decp_influence")[0] == "skipped"

    def test_sans_mx_ni_a_bloque(self, monkeypatch):
        monkeypatch.setattr(verify, "resolve_mx", lambda d: ("none", []))
        assert verify.verify_address("x@acme.fr", "bdd_prospect")[0] == "no_mx"

    def test_mx_de_parking_bloque_sans_sonde(self, monkeypatch):
        # copas.com (4 contacts en base) : park-mx.above.com accepte tout puis jette.
        monkeypatch.setattr(verify, "resolve_mx", lambda d: ("ok", ["park-mx.above.com"]))
        monkeypatch.setattr(verify, "smtp_probe", lambda *a, **k: pytest.fail("sonde inutile"))
        verdict, detail = verify.verify_address("contact@copas.com", "decp")
        assert verdict == "parked"
        assert "parking" in detail

    def test_dns_en_panne_ne_bloque_pas(self, monkeypatch):
        monkeypatch.setattr(verify, "resolve_mx", lambda d: ("unknown", []))
        assert verify.verify_address("x@acme.fr", "decp_influence")[0] == "unknown"

    def test_source_non_deduite_pas_de_sonde(self, monkeypatch):
        monkeypatch.setattr(verify, "resolve_mx", lambda d: ("ok", MX))
        monkeypatch.setattr(verify, "smtp_probe", lambda *a, **k: pytest.fail("sonde interdite"))
        assert verify.verify_address("x@acme.fr", "bdd_prospect")[0] == "ok"
        assert verify.verify_address("x@acme.fr", "mailjet_hot")[0] == "ok"

    def test_source_deduite_est_sondee(self, monkeypatch):
        monkeypatch.setattr(verify, "resolve_mx", lambda d: ("ok", MX))
        monkeypatch.setattr(verify, "smtp_probe", lambda email, hosts: ("invalid", "550"))
        assert verify.verify_address("X@Acme.fr", "decp_influence")[0] == "invalid"
        assert verify.verify_address("x@acme.fr", "decp")[0] == "invalid"


# --- intégration sender ------------------------------------------------------


@pytest.fixture
def lead_and_po(db):
    from crm.models import Lead

    lead = Lead.objects.create(
        linkedin_url="https://www.linkedin.com/in/jean-dupont-verif/",
        public_identifier="jean-dupont-verif",
        contact_email="jean.dupont@acme-inexistante.fr",
        contact_email_source="decp_influence",
    )
    po = PendingOutbound.objects.create(
        prospect_public_id=lead.public_identifier, prospect_company="ACME",
        kind=OutboundKind.EMAIL_COLD, subject="Coupe-feu EI60", ai_draft="Bonjour,\n\nCorps.",
        status=OutboundStatus.APPROVED,
    )
    return lead, po


@pytest.fixture
def partage(tmp_path, monkeypatch):
    from ekoalu import shared_exclusions

    excl = tmp_path / "exclusions.json"
    drop = tmp_path / "retour-mail-corrections.json"
    monkeypatch.setenv(shared_exclusions.ENV_VAR, str(excl))
    monkeypatch.setenv("RETOUR_MAIL_DROP_FILE", str(drop))
    shared_exclusions._cache = None
    return excl, drop


class TestSenderBloqueAvantEnvoi:
    def test_adresse_inexistante_bloquee_bouncee_exclue_deposee(self, lead_and_po, partage, monkeypatch):
        lead, po = lead_and_po
        excl, drop = partage
        monkeypatch.setenv(verify.ENV_VAR, "1")
        monkeypatch.setattr(verify, "verify_address", lambda email, source: ("invalid", "mx1: RCPT 550"))
        monkeypatch.setattr("ekoalu.email_canal.sender.send_mail",
                            lambda **kw: pytest.fail("aucun mail ne doit partir"))

        success, err = send_cold_email(po)
        assert success is False
        assert "inexistante" in err

        lead.refresh_from_db()
        assert lead.email_bounced_at is not None
        ex = json.loads(excl.read_text(encoding="utf-8"))["exclusions"]
        assert ex[0]["email"] == "jean.dupont@acme-inexistante.fr"
        assert ex[0]["reason"] == "verify_invalid"
        corr = json.loads(drop.read_text(encoding="utf-8"))["corrections"]
        assert corr[0]["type"] == "hard_bounce"
        assert corr[0]["imported_by"] == ["prospection-ia"]
        assert corr[0]["provenance"].startswith("prospection-ia:verify:invalid")

        # Deuxième passage : rien n'est dupliqué dans les fichiers partagés.
        send_cold_email(po)
        assert len(json.loads(excl.read_text(encoding="utf-8"))["exclusions"]) == 1
        assert len(json.loads(drop.read_text(encoding="utf-8"))["corrections"]) == 1

    def test_verdict_incertain_laisse_partir(self, lead_and_po, partage, monkeypatch):
        lead, po = lead_and_po
        monkeypatch.setenv(verify.ENV_VAR, "1")
        monkeypatch.setattr(verify, "verify_address", lambda email, source: ("unknown", "catch-all"))
        sent = {}
        monkeypatch.setattr("ekoalu.email_canal.sender.send_mail", lambda **kw: sent.update(kw))
        success, _ = send_cold_email(po)
        assert success is True
        assert sent["to"] == "jean.dupont@acme-inexistante.fr"
        lead.refresh_from_db()
        assert lead.email_bounced_at is None

    def test_follow_up_jamais_sonde(self, lead_and_po, partage, monkeypatch):
        lead, po = lead_and_po
        po.kind = OutboundKind.EMAIL_FOLLOW_UP
        po.save(update_fields=["kind"])
        monkeypatch.setenv(verify.ENV_VAR, "1")
        monkeypatch.setattr(verify, "verify_address", lambda *a: pytest.fail("pas de sonde sur un follow-up"))
        monkeypatch.setattr("ekoalu.email_canal.sender.send_mail", lambda **kw: None)
        assert send_cold_email(po)[0] is True
