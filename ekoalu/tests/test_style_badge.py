"""Tests capture Richard 03/09 : badge « mot banni » dans la liste de validation.

Le garde-fou de style laisse passer (par design) un message encore fautif après
une régénération — pour arbitrage humain. Mais l'UI ne montrait pas la
violation : « synergies » a été approuvé sans être vu (message Garrido 02/09).
"""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

pytestmark = pytest.mark.django_db


@pytest.fixture
def client_staff(db):
    User = get_user_model()
    User.objects.create_user(username="badge", password="p", is_staff=True)
    c = Client()
    c.login(username="badge", password="p")
    return c


class TestBadgeMotBanni:
    def test_violation_affichee_sur_pending(self, client_staff):
        PendingOutbound.objects.create(
            prospect_public_id="badge-test-1",
            kind=OutboundKind.FOLLOW_UP,
            ai_draft="Identifions des synergies potentielles ensemble.",
            status=OutboundStatus.PENDING,
        )
        r = client_staff.get(reverse("ekoalu:outbound_list") + "?status=pending")
        content = r.content.decode()
        assert "mot banni" in content
        assert "synergies" in content

    def test_message_propre_sans_badge(self, client_staff):
        PendingOutbound.objects.create(
            prospect_public_id="badge-test-2",
            kind=OutboundKind.FOLLOW_UP,
            ai_draft="Vos chantiers intègrent-ils du coupe-feu EI30 ?",
            status=OutboundStatus.PENDING,
        )
        r = client_staff.get(reverse("ekoalu:outbound_list") + "?status=pending")
        assert "mot banni" not in r.content.decode()

    def test_final_content_edite_prime_sur_ai_draft(self, client_staff):
        """Si Richard a édité le message, on contrôle SA version, pas le brouillon."""
        PendingOutbound.objects.create(
            prospect_public_id="badge-test-3",
            kind=OutboundKind.FOLLOW_UP,
            ai_draft="Message propre d'origine.",
            final_content="Version éditée avec du win-win dedans.",
            status=OutboundStatus.APPROVED,
        )
        r = client_staff.get(reverse("ekoalu:outbound_list") + "?status=approved")
        content = r.content.decode()
        assert "mot banni" in content
        assert "win-win" in content

    def test_pas_de_scan_sur_les_envoyes(self, client_staff):
        """Les onglets sent/rejected ne scannent pas (rien d'actionnable)."""
        PendingOutbound.objects.create(
            prospect_public_id="badge-test-4",
            kind=OutboundKind.FOLLOW_UP,
            ai_draft="Des synergies partout.",
            status=OutboundStatus.SENT,
        )
        r = client_staff.get(reverse("ekoalu:outbound_list") + "?status=sent")
        assert "mot banni" not in r.content.decode()
