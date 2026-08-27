"""Tests sorties de prospection (boutons + registre + garde import — Richard 27/08)."""
from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from crm.models import Lead
from ekoalu.email_canal.models import EmailLeadData
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound
from ekoalu.sorties import service
from ekoalu.sorties.models import ProspectionSortie


@pytest.fixture
def client_logged(db):
    User = get_user_model()
    User.objects.create_user(username="testadmin", password="testpwd123", is_staff=True)
    c = Client()
    c.login(username="testadmin", password="testpwd123")
    return c


@pytest.fixture(autouse=True)
def _export_to_tmp(tmp_path, monkeypatch):
    """L'export partagé écrit dans un fichier temporaire pendant les tests."""
    target = tmp_path / "sorties_prospection.json"
    monkeypatch.setenv("EKOALU_SORTIES_EXPORT_PATH", str(target))
    service.invalidate_cache()
    yield target
    service.invalidate_cache()


def _lead(public_id="bdd-prospect-350246039", siren="350246039",
          entreprise="SOFIPRE", dirigeant="Jean Clavagnier", email=None):
    lead = Lead.objects.create(
        linkedin_url=f"https://bdd-prospect.local/siren/{public_id}",
        public_identifier=public_id,
        contact_email=email or f"{public_id}@exemple.fr",
    )
    EmailLeadData.objects.create(
        lead=lead, source=EmailLeadData.SOURCE_BDD_PROSPECT,
        siren=siren, entreprise=entreprise, dirigeant=dirigeant,
    )
    return lead


@pytest.mark.django_db
class TestSortirProspect:
    def test_cascade_et_registre(self, _export_to_tmp):
        lead = _lead()
        po = PendingOutbound.objects.create(
            prospect_public_id=lead.public_identifier,
            kind=OutboundKind.EMAIL_COLD, ai_draft="brouillon",
        )
        label = service.sortir_prospect(lead.public_identifier)
        lead.refresh_from_db()
        po.refresh_from_db()
        assert lead.disqualified is True
        assert po.status == OutboundStatus.REJECTED
        assert label == "Jean Clavagnier"
        entry = ProspectionSortie.objects.get(kind="person",
                                              public_identifier=lead.public_identifier)
        assert entry.siren == "350246039"
        assert entry.company_name == "SOFIPRE"
        data = json.loads(_export_to_tmp.read_text(encoding="utf-8"))
        assert len(data["sorties"]) == 1

    def test_idempotent(self):
        lead = _lead()
        service.sortir_prospect(lead.public_identifier)
        service.sortir_prospect(lead.public_identifier)
        assert ProspectionSortie.objects.filter(
            public_identifier=lead.public_identifier).count() == 1


@pytest.mark.django_db
class TestSortirSociete:
    def test_toutes_les_personnes_du_siren(self):
        l1 = _lead(public_id="bdd-prospect-111", siren="111222333")
        l2 = _lead(public_id="bdd-prospect-111-i2", siren="111222333",
                   dirigeant="Marie Perret", email="m.perret@exemple.fr")
        autre = _lead(public_id="bdd-prospect-999", siren="999888777",
                      entreprise="AUTRE SOCIETE")
        n, label, slugs = service.sortir_societe(siren="111222333", company_name="SOFIPRE")
        assert slugs == ["bdd-prospect-111", "bdd-prospect-111-i2"]
        for lead in (l1, l2, autre):
            lead.refresh_from_db()
        assert n == 2
        assert label == "SOFIPRE"
        assert l1.disqualified and l2.disqualified
        assert autre.disqualified is False
        assert service.is_company_excluded("111222333") is True
        assert service.is_company_excluded("999888777") is False

    def test_rattachement_par_nom_pendingoutbound(self):
        # Lead LinkedIn (pas d'EmailLeadData) rattaché via prospect_company
        lead = Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/paul-durand",
            public_identifier="paul-durand",
        )
        PendingOutbound.objects.create(
            prospect_public_id="paul-durand",
            prospect_company="Metallerie Durand",
            kind=OutboundKind.INVITATION, ai_draft="x",
        )
        n, _, _ = service.sortir_societe(company_name="Metallerie Durand")
        lead.refresh_from_db()
        assert n == 1
        assert lead.disqualified is True

    def test_sans_siren_ni_nom(self):
        assert service.sortir_societe() == (0, "", [])


@pytest.mark.django_db
class TestGardeImport:
    def test_excluded_sirens_cache_invalidation(self):
        assert service.is_company_excluded("424242424") is False
        ProspectionSortie.objects.create(
            kind=ProspectionSortie.Kind.COMPANY, siren="424242424", label="X",
        )
        service.invalidate_cache()
        assert service.is_company_excluded("424242424") is True

    def test_siren_vide_jamais_exclu(self):
        assert service.is_company_excluded("") is False


@pytest.mark.django_db
class TestVuesSorties:
    def test_bouton_sortir_prospect(self, client_logged):
        lead = _lead()
        po = PendingOutbound.objects.create(
            prospect_public_id=lead.public_identifier,
            kind=OutboundKind.EMAIL_COLD, ai_draft="brouillon",
        )
        r = client_logged.post(f"/ekoalu/messages/{po.pk}/sortir-prospect/")
        assert r.status_code in (302, 303)
        lead.refresh_from_db()
        assert lead.disqualified is True
        assert ProspectionSortie.objects.filter(kind="person").count() == 1

    def test_bouton_sortir_societe(self, client_logged):
        lead = _lead(siren="555666777")
        po = PendingOutbound.objects.create(
            prospect_public_id=lead.public_identifier,
            kind=OutboundKind.EMAIL_COLD, ai_draft="brouillon",
        )
        r = client_logged.post(f"/ekoalu/messages/{po.pk}/sortir-societe/")
        assert r.status_code in (302, 303)
        lead.refresh_from_db()
        assert lead.disqualified is True
        assert service.is_company_excluded("555666777") is True

    def test_sortir_societe_ajax_renvoie_les_slugs(self, client_logged):
        # AJAX : l'UI retire les lignes de TOUS les contacts sans recharger
        # (le rechargement effaçait les cases cochées de Richard).
        _lead(public_id="bdd-prospect-777", siren="777777777")
        _lead(public_id="bdd-prospect-777-i2", siren="777777777",
              dirigeant="Autre Contact", email="autre@exemple.fr")
        po = PendingOutbound.objects.create(
            prospect_public_id="bdd-prospect-777",
            kind=OutboundKind.EMAIL_COLD, ai_draft="brouillon",
        )
        r = client_logged.post(
            f"/ekoalu/messages/{po.pk}/sortir-societe/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["n"] == 2
        assert set(data["removed"]) == {"bdd-prospect-777", "bdd-prospect-777-i2"}

    def test_sortir_prospect_ajax(self, client_logged):
        lead = _lead()
        po = PendingOutbound.objects.create(
            prospect_public_id=lead.public_identifier,
            kind=OutboundKind.EMAIL_COLD, ai_draft="brouillon",
        )
        r = client_logged.post(
            f"/ekoalu/messages/{po.pk}/sortir-prospect/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["removed"] == [lead.public_identifier]

    def test_page_sorties_et_retrait(self, client_logged):
        entry = ProspectionSortie.objects.create(
            kind=ProspectionSortie.Kind.COMPANY, siren="123123123", label="ACME",
        )
        r = client_logged.get("/ekoalu/sorties/")
        assert r.status_code == 200
        assert "ACME" in r.content.decode()
        r = client_logged.post("/ekoalu/sorties/", data={"action": "delete", "pk": entry.pk})
        assert r.status_code in (302, 303)
        assert ProspectionSortie.objects.count() == 0
