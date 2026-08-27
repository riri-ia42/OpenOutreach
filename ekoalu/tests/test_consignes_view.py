"""Tests fenêtre de gestion des consignes (/ekoalu/consignes/ — Richard 27/08).

Cas déclencheur : la consigne « ne pas se limiter au tertiaire » était rangée
sur linkedin_dm et donc invisible des cold mails — la fenêtre doit permettre
de voir et de CORRIGER le canal d'une consigne.
"""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from ekoalu.inbox_assist.models import CorrectionExample, PendingReply

pytestmark = pytest.mark.django_db


@pytest.fixture
def client_logged(db):
    User = get_user_model()
    User.objects.create_user(username="testadmin", password="testpwd123", is_staff=True)
    c = Client()
    c.login(username="testadmin", password="testpwd123")
    return c


def _consigne(channel=CorrectionExample.Channel.LINKEDIN_DM,
              instruction="ne pas se limiter au tertiaire même si c'est une dominante"):
    pr = PendingReply.objects.create(
        prospect_public_id="x", campaign_id=1, inbound_message="(test)",
        ai_draft="brouillon", final_sent="final", status=PendingReply.Status.SENT,
    )
    return CorrectionExample.objects.create(
        pending_reply=pr, persona_slug="", channel=channel,
        kind=CorrectionExample.Kind.INSTRUCTION_ONLY,
        similarity_ratio=1.0, instruction=instruction,
    )


class TestConsignesView:
    def test_liste_accessible_et_contenu(self, client_logged):
        _consigne()
        r = client_logged.get("/ekoalu/consignes/")
        assert r.status_code == 200
        assert "ne pas se limiter au tertiaire" in r.content.decode()

    def test_filtre_par_canal(self, client_logged):
        _consigne(channel=CorrectionExample.Channel.LINKEDIN_DM, instruction="consigne DM")
        _consigne(channel=CorrectionExample.Channel.EMAIL_COLD, instruction="consigne cold mail")
        r = client_logged.get("/ekoalu/consignes/?channel=email_cold")
        html = r.content.decode()
        assert "consigne cold mail" in html
        assert "consigne DM" not in html

    def test_changement_de_canal(self, client_logged):
        # LE cas vécu : consigne mal rangée -> Richard corrige le canal
        ex = _consigne(channel=CorrectionExample.Channel.LINKEDIN_DM)
        r = client_logged.post("/ekoalu/consignes/", data={
            "action": "update", "pk": ex.pk,
            "instruction": ex.instruction,
            "channel": CorrectionExample.Channel.EMAIL_COLD,
        })
        assert r.status_code in (302, 303)
        ex.refresh_from_db()
        assert ex.channel == CorrectionExample.Channel.EMAIL_COLD

    def test_edition_instruction(self, client_logged):
        ex = _consigne()
        r = client_logged.post("/ekoalu/consignes/", data={
            "action": "update", "pk": ex.pk,
            "instruction": "beaucoup de tertiaire mais pas exclusivement",
            "channel": ex.channel,
        })
        assert r.status_code in (302, 303)
        ex.refresh_from_db()
        assert ex.instruction == "beaucoup de tertiaire mais pas exclusivement"

    def test_suppression(self, client_logged):
        ex = _consigne()
        r = client_logged.post("/ekoalu/consignes/", data={"action": "delete", "pk": ex.pk})
        assert r.status_code in (302, 303)
        assert CorrectionExample.objects.count() == 0

    def test_canal_invalide_ignore(self, client_logged):
        ex = _consigne()
        client_logged.post("/ekoalu/consignes/", data={
            "action": "update", "pk": ex.pk,
            "instruction": ex.instruction, "channel": "canal_bidon",
        })
        ex.refresh_from_db()
        assert ex.channel == CorrectionExample.Channel.LINKEDIN_DM


class TestAffirmationsFaussesBannies:
    """Consigne Richard 27/08 + bible v2.2 : le garde-fou de style refuse
    « exclusivement tertiaire » et toute affirmation de pose."""

    def test_exclusivement_tertiaire_banni(self):
        from ekoalu.message_validator.banned_words import find_banned_words
        assert find_banned_words("EKOALU, orienté exclusivement tertiaire et technique")
        assert find_banned_words("nous travaillons uniquement tertiaire")

    def test_affirmations_pose_bannies(self):
        from ekoalu.message_validator.banned_words import find_banned_words
        assert find_banned_words("fabrication et pose de menuiseries")
        assert find_banned_words("nous assurons la pose sur chantier")

    def test_formulations_correctes_passent(self):
        from ekoalu.message_validator.banned_words import find_banned_words
        assert find_banned_words(
            "EKOALU, très orienté tertiaire — conception, fabrication et livraison"
        ) == []
        assert find_banned_words("le prospect pose une question") == []
