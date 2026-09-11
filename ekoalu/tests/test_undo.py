"""Bouton « Annuler la dernière action » (capture Richard 11/09).

La demande de confirmation avant chaque sortie de prospect a été retirée : on
confirmait sans lire. Le garde-fou est désormais le retour arrière, donc il doit
rendre EXACTEMENT l'état d'avant, y compris les messages en file, les deals et
les tâches que la cascade de disqualification avait fermés.
"""
from __future__ import annotations

import pytest

from crm.models import Lead
from ekoalu.email_canal.models import EmailLeadData
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound
from ekoalu.undo import service as undo
from ekoalu.undo.models import UndoEntry


def _lead(pid, email="a@metal.fr", entreprise="METAL SA", siren="000000001"):
    lead = Lead.objects.create(linkedin_url=f"https://bdd-prospect.local/siren/{pid}",
                               public_identifier=pid, contact_email=email)
    EmailLeadData.objects.create(lead=lead, source="bdd_prospect", siren=siren,
                                 dirigeant="Jean Dupont", entreprise=entreprise)
    return lead


def _pending(pid, status=OutboundStatus.PENDING):
    return PendingOutbound.objects.create(
        prospect_public_id=pid, kind=OutboundKind.EMAIL_COLD, status=status,
        subject="Objet", ai_draft="Bonjour, corps du message.")


@pytest.mark.django_db
class TestUndoSortieProspect:
    def test_la_sortie_est_annulable_et_rend_l_etat_d_avant(self, tmp_path, monkeypatch):
        from ekoalu.sorties import service as sorties

        monkeypatch.setenv("EKOALU_SORTIES_EXPORT_PATH", str(tmp_path / "sorties.json"))
        lead = _lead("bdd-prospect-1")
        po = _pending("bdd-prospect-1")

        sorties.sortir_prospect("bdd-prospect-1")
        lead.refresh_from_db(); po.refresh_from_db()
        assert lead.disqualified is True
        assert po.status == OutboundStatus.REJECTED

        ok, msg = undo.undo_last()
        assert ok, msg
        lead.refresh_from_db(); po.refresh_from_db()
        assert lead.disqualified is False
        assert po.status == OutboundStatus.PENDING
        # le registre des sorties ne garde pas de trace de l'action annulée
        from ekoalu.sorties.models import ProspectionSortie
        assert not ProspectionSortie.objects.filter(public_identifier="bdd-prospect-1").exists()

    def test_on_n_annule_pas_deux_fois(self, tmp_path, monkeypatch):
        from ekoalu.sorties import service as sorties

        monkeypatch.setenv("EKOALU_SORTIES_EXPORT_PATH", str(tmp_path / "sorties.json"))
        _lead("bdd-prospect-1")
        sorties.sortir_prospect("bdd-prospect-1")
        assert undo.undo_last()[0] is True
        ok, msg = undo.undo_last()
        assert ok is False and "Aucune action" in msg

    def test_un_lead_deja_disqualifie_avant_le_reste(self, tmp_path, monkeypatch):
        """Annuler une sortie de société ne ressuscite pas un lead qui était
        déjà disqualifié pour une autre raison."""
        from ekoalu.sorties import service as sorties

        monkeypatch.setenv("EKOALU_SORTIES_EXPORT_PATH", str(tmp_path / "sorties.json"))
        actif = _lead("bdd-prospect-1", entreprise="METAL SA")
        deja = _lead("bdd-prospect-2", email="b@metal.fr", entreprise="METAL SA")
        deja.disqualified = True
        deja.save()

        sorties.sortir_societe(siren="000000001", company_name="METAL SA")
        undo.undo_last()

        actif.refresh_from_db(); deja.refresh_from_db()
        assert actif.disqualified is False      # remis en prospection
        assert deja.disqualified is True        # n'a jamais été concerné


@pytest.mark.django_db
class TestUndoValidation:
    def test_une_validation_se_defait(self):
        _lead("bdd-prospect-1")
        po = _pending("bdd-prospect-1")
        before = undo.snapshot_outbounds([po.pk])
        po.status = OutboundStatus.APPROVED
        po.save()
        undo.record(UndoEntry.Kind.APPROVE, "Validation de 1 message(s)", {"outbounds": before})

        assert undo.undo_last()[0] is True
        po.refresh_from_db()
        assert po.status == OutboundStatus.PENDING

    def test_un_message_deja_parti_n_est_jamais_repris(self):
        """Entre l'action et l'annulation, le daemon a pu envoyer : on ne
        remet pas en file un message déjà sorti."""
        _lead("bdd-prospect-1")
        po = _pending("bdd-prospect-1")
        before = undo.snapshot_outbounds([po.pk])
        po.status = OutboundStatus.APPROVED
        po.save()
        undo.record(UndoEntry.Kind.APPROVE, "Validation de 1 message(s)", {"outbounds": before})

        po.status = OutboundStatus.SENT      # parti entre-temps
        po.save()
        undo.undo_last()
        po.refresh_from_db()
        assert po.status == OutboundStatus.SENT


@pytest.mark.django_db
class TestUndoParLaVue:
    """Le bouton de la file : POST bulk_action=undo_last, sans sélection."""

    def _staff(self, client):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(
            username="staff-undo", password="x", is_staff=True, is_superuser=True)
        client.force_login(user)
        return user

    def test_le_bouton_annule_la_validation(self, client):
        self._staff(client)
        _lead("bdd-prospect-1")
        po = _pending("bdd-prospect-1")

        r = client.post("/ekoalu/messages/?status=pending",
                        {"bulk_action": "bulk_approve", "selected_ids": str(po.pk)})
        assert r.status_code == 302
        po.refresh_from_db()
        assert po.status == OutboundStatus.APPROVED

        r = client.post("/ekoalu/messages/?status=pending", {"bulk_action": "undo_last"})
        assert r.status_code == 302
        po.refresh_from_db()
        assert po.status == OutboundStatus.PENDING

    def test_sans_rien_a_annuler_le_bouton_ne_casse_pas(self, client):
        self._staff(client)
        r = client.post("/ekoalu/messages/?status=pending", {"bulk_action": "undo_last"})
        assert r.status_code == 302
        assert not UndoEntry.objects.exists()

    def test_le_refus_en_masse_se_defait(self, client):
        self._staff(client)
        lead = _lead("bdd-prospect-1")
        po = _pending("bdd-prospect-1")

        client.post("/ekoalu/messages/?status=pending",
                    {"bulk_action": "bulk_reject", "selected_ids": str(po.pk),
                     "bulk_reason": "test"})
        lead.refresh_from_db(); po.refresh_from_db()
        assert lead.disqualified is True and po.status == OutboundStatus.REJECTED

        client.post("/ekoalu/messages/?status=pending", {"bulk_action": "undo_last"})
        lead.refresh_from_db(); po.refresh_from_db()
        assert lead.disqualified is False
        assert po.status == OutboundStatus.PENDING
