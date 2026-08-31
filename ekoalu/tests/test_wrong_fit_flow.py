"""Tests câblage réponses hors-cible (capture Richard 31/08).

Cas réel : Parquetsol répond « Nous faisons exclusivement des revêtements de
sols » — classé rdv_request parce que le fil cité (notre cold mail, « mon
agenda en ligne ») était analysé aussi.
"""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from crm.models import Lead
from ekoalu.inbox_assist.intent_classifier import Intent, classify_intent, strip_quoted_reply
from ekoalu.inbox_assist.models import PendingReply
from ekoalu.sorties.models import ProspectionSortie

PARQUETSOL = (
    "Bonjour, Nous faisons exclusivement des revêtements de sols. Bien cordialement, "
    "De : Richard GROS (EKOALU) <richard@ekoalu.com> Envoyé : mardi 25 août 2026 20:15 "
    "À : Christophe Gros Objet : menuiseries intérieures tertiaires — "
    "Si le sujet vous parle, 15-30 min en visio suffisent — mon agenda en ligne : https://..."
)


class TestStripQuotedReply:
    def test_coupe_au_marqueur_de(self):
        assert "agenda" not in strip_quoted_reply(PARQUETSOL)
        assert "revêtements de sols" in strip_quoted_reply(PARQUETSOL)

    def test_marqueurs_varies(self):
        for marker in ("From: x@y.z", "Le 25 août 2026 à 10:00, Jean a écrit :",
                       "-- Message d'origine --", "Envoyé : mardi"):
            txt = f"Ma réponse courte.\n{marker}\nvieux contenu rdv agenda"
            assert "agenda" not in strip_quoted_reply(txt)

    def test_transfert_sans_texte_garde_original(self):
        txt = "De : quelqu'un\nContenu transféré"
        assert strip_quoted_reply(txt) == txt


class TestClassifyWrongFit:
    def test_cas_parquetsol(self):
        # LE bug du 31/08 : ne doit plus être rdv_request
        assert classify_intent(PARQUETSOL) == Intent.WRONG_FIT

    def test_variantes_hors_cible(self):
        assert classify_intent("Nous ne fabriquons pas de menuiseries.") == Intent.WRONG_FIT
        assert classify_intent("Ce n'est pas notre activité.") == Intent.WRONG_FIT
        assert classify_intent("Nous ne sommes pas concernés par ce sujet.") == Intent.WRONG_FIT

    def test_opt_out_prime_sur_wrong_fit(self):
        assert classify_intent(
            "Nous faisons exclusivement du carrelage, merci de ne plus me contacter."
        ) == Intent.OPT_OUT

    def test_rdv_reste_rdv(self):
        assert classify_intent("OK pour un rendez-vous la semaine prochaine.") == Intent.RDV_REQUEST


@pytest.fixture
def client_logged(db):
    User = get_user_model()
    User.objects.create_user(username="testadmin", password="testpwd123", is_staff=True)
    c = Client()
    c.login(username="testadmin", password="testpwd123")
    return c


@pytest.mark.django_db
class TestSortirProspectALApprobation:
    def _pending_reply(self, intent="wrong_fit"):
        lead = Lead.objects.create(
            linkedin_url="https://bdd-prospect.local/siren/316343508/i1",
            public_identifier="bdd-prospect-316343508-i1",
            contact_email="christophe.gros@parquetsol.fr",
        )
        pr = PendingReply.objects.create(
            prospect_public_id=lead.public_identifier,
            campaign_id=0, channel=PendingReply.CHANNEL_EMAIL,
            inbound_message="Nous faisons exclusivement des revêtements de sols.",
            ai_draft="Compris — je ne poursuis pas dans ce sens.",
            intent=intent, status=PendingReply.Status.PENDING,
        )
        return lead, pr

    def test_approve_avec_sortie(self, client_logged):
        lead, pr = self._pending_reply()
        r = client_logged.post(
            reverse("ekoalu:email_reply_action", args=[pr.pk]),
            data={"action": "approve", "final_sent": "", "sortir_prospect": "1"},
        )
        assert r.status_code in (302, 303)
        pr.refresh_from_db()
        lead.refresh_from_db()
        assert pr.status == PendingReply.Status.APPROVED  # la réponse PARTIRA
        assert lead.disqualified is True
        assert ProspectionSortie.objects.filter(
            public_identifier=lead.public_identifier).exists()

    def test_approve_sans_sortie(self, client_logged):
        lead, pr = self._pending_reply(intent="rdv_request")
        client_logged.post(
            reverse("ekoalu:email_reply_action", args=[pr.pk]),
            data={"action": "approve", "final_sent": ""},
        )
        lead.refresh_from_db()
        assert lead.disqualified is False
        assert ProspectionSortie.objects.count() == 0

    def test_case_precochee_dans_inbox(self, client_logged):
        _lead, _pr = self._pending_reply(intent="wrong_fit")
        html = client_logged.get(reverse("ekoalu:inbox")).content.decode()
        assert 'name="sortir_prospect"' in html
        assert "checked" in html
