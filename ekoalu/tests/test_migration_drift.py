"""Garde anti-décalage code / base (fiche hub 11/09).

27 tâches LinkedIn perdues en deux jours : le daemon tournait depuis le 09/09
09h01, la migration 0030 a été appliquée à 16h51 le même jour, et Django fige
les classes de modèle à l'import. Les INSERT du process omettaient la colonne
ajoutée depuis, SQLite les refusait, chaque refus perdait une invitation.
"""
from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from ekoalu import migration_drift

pytestmark = pytest.mark.django_db


class TestDetection:
    def test_migration_posterieure_au_demarrage_est_un_drift(self, monkeypatch):
        applique = timezone.now()
        demarrage = applique - dt.timedelta(hours=8)
        monkeypatch.setattr(migration_drift, "last_migration_applied_at",
                            lambda: applique)
        assert migration_drift.drift_detecte(demarrage) == applique

    def test_migration_anterieure_ne_declenche_rien(self, monkeypatch):
        applique = timezone.now() - dt.timedelta(days=2)
        monkeypatch.setattr(migration_drift, "last_migration_applied_at",
                            lambda: applique)
        assert migration_drift.drift_detecte(timezone.now()) is None

    def test_base_sans_migration_ne_declenche_rien(self, monkeypatch):
        monkeypatch.setattr(migration_drift, "last_migration_applied_at", lambda: None)
        assert migration_drift.drift_detecte(timezone.now()) is None

    def test_kill_switch(self, monkeypatch):
        applique = timezone.now()
        monkeypatch.setattr(migration_drift, "last_migration_applied_at",
                            lambda: applique)
        monkeypatch.setenv(migration_drift.ENV_KILL_SWITCH, "0")
        assert migration_drift.drift_detecte(applique - dt.timedelta(hours=1)) is None


class TestLectureDeLaBase:
    def test_lit_un_horodatage_reel(self):
        """La suite de tests applique les migrations : il y en a forcément une."""
        applique = migration_drift.last_migration_applied_at()
        assert applique is not None
        assert timezone.is_aware(applique)

    def test_un_horodatage_illisible_ne_casse_pas_le_daemon(self, monkeypatch):
        class _Cur:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, *a): return None
            def fetchone(self): return ("pas une date",)

        monkeypatch.setattr(migration_drift, "_aware", lambda v: v)
        import django.db
        monkeypatch.setattr(django.db.connection, "cursor", lambda: _Cur())
        assert migration_drift.last_migration_applied_at() is None


class TestSignalement:
    def test_le_signalement_laisse_une_trace_au_hub(self, monkeypatch):
        """La revue de nuit du hub doit pouvoir constater l'arrêt."""
        from unittest.mock import patch

        applique = timezone.now()
        with patch("ekoalu.notifications.hub_events.post_event") as event:
            migration_drift.signaler(applique, applique - dt.timedelta(hours=8))
        assert event.call_count == 1
        assert event.call_args.args[1] == "error"
        assert event.call_args.args[3]["status"] == "drift"
