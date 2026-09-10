"""La suite de tests ne parle jamais au hub (fiche conformite 10/09 : rapports
fantomes « NON CONFORME » postes depuis la base de test vide)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.management import call_command

from ekoalu.notifications import hub_events


def test_post_event_sans_jeton_ne_fait_aucune_requete():
    with patch("ekoalu.notifications.hub_events.requests.post") as post,          patch("ekoalu.notifications.hub_events.requests.get") as get:
        assert hub_events.post_event("test", "info", "ne doit pas partir") is False
        assert hub_events.post_proposal("ne doit pas partir", "corps") is False
    post.assert_not_called()
    get.assert_not_called()


@pytest.mark.django_db
def test_daily_conformity_en_test_ne_pousse_rien_au_hub():
    with patch("ekoalu.notifications.hub_events.requests.post") as post:
        call_command("daily_conformity", "--no-send")
    post.assert_not_called()
