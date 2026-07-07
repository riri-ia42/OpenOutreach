"""Tests LOT E — humanisation des budgets quotidiens (ekoalu/human_scheduler/budget).

Constat audit 07/07 : saturation 150/150 lectures TOUS les jours (samedi
compris a 100 %), poids hebdo jamais appliques en facteur, volume pile au cap
= signature reguliere (le checkpoint du 06/06 a ete cause par les lectures).

NB : le conftest neutralise budget.* par defaut (setattr sur le module) ; ici
on importe les fonctions REELLES directement (liees a l'import du module de
test, avant le monkeypatch), et on re-patche explicitement pour les tests
d'integration guard/sender.
"""
from __future__ import annotations

import datetime as dt

import pytest

# Imports directs = fonctions reelles (le conftest ne patche que l'attribut
# de module, pas ces references locales).
from ekoalu.human_scheduler.budget import (
    JITTER_MAX,
    JITTER_MIN,
    daily_jitter_factor,
    daily_weight_factor,
    days_off_for_month,
    is_day_off,
)

SATURDAY = dt.date(2026, 7, 11)   # samedi
MONDAY = dt.date(2026, 7, 6)      # lundi
SUNDAY = dt.date(2026, 7, 12)     # dimanche


class TestDailyWeightFactor:
    def test_samedi_20_pourcent(self):
        assert daily_weight_factor(SATURDAY) == 0.2

    def test_lundi_100_pourcent(self):
        assert daily_weight_factor(MONDAY) == 1.0

    def test_dimanche_zero(self):
        assert daily_weight_factor(SUNDAY) == 0.0

    def test_vendredi_70_pourcent(self):
        assert daily_weight_factor(dt.date(2026, 7, 10)) == 0.7


class TestJoursOffAleatoires:
    """Point 2 audit : RANDOM_DAYS_OFF_PER_MONTH n'etait jamais reference."""

    def test_tirage_deterministe_par_mois(self):
        assert days_off_for_month(2026, 9) == days_off_for_month(2026, 9)

    def test_1_a_2_jours_ouvres_jamais_consecutifs(self):
        for year, month in [(2026, m) for m in range(1, 13)]:
            offs = days_off_for_month(year, month)
            assert 1 <= len(offs) <= 2, f"{year}-{month}: {offs}"
            for d in offs:
                assert d.weekday() < 5, f"{d} n'est pas un jour ouvre"
            if len(offs) == 2:
                assert abs((offs[1] - offs[0]).days) >= 2, f"consecutifs: {offs}"

    def test_mois_differents_tirages_differents(self):
        """Sur 24 mois, au moins 2 tirages distincts (pas un tirage fige)."""
        draws = {
            days_off_for_month(y, m)
            for y in (2026, 2027) for m in range(1, 13)
        }
        assert len(draws) > 1

    def test_is_day_off_vrai_pour_un_jour_tire(self, monkeypatch):
        monkeypatch.setenv("EKOALU_RANDOM_DAYS_OFF", "1")
        off = days_off_for_month(2026, 9)[0]
        assert is_day_off(off)

    def test_is_day_off_faux_hors_tirage(self, monkeypatch):
        monkeypatch.setenv("EKOALU_RANDOM_DAYS_OFF", "1")
        offs = set(days_off_for_month(2026, 9))
        d = dt.date(2026, 9, 1)
        while d in offs:
            d += dt.timedelta(days=1)
        assert not is_day_off(d)

    def test_kill_switch_env(self, monkeypatch):
        monkeypatch.setenv("EKOALU_RANDOM_DAYS_OFF", "0")
        off = days_off_for_month(2026, 9)[0]
        assert not is_day_off(off)


class TestJourOffBloqueLinkedInPasEmail:
    """Un jour off = hors plage pour LinkedIn ; email + manuel restent actifs."""

    def _day_off_at_10h(self, monkeypatch) -> dt.datetime:
        monkeypatch.setenv("EKOALU_RANDOM_DAYS_OFF", "1")
        off = days_off_for_month(2026, 9)[0]
        return dt.datetime(off.year, off.month, off.day, 10, 0,
                           tzinfo=dt.timezone.utc)

    def test_linkedin_refuse_le_jour_off(self, monkeypatch):
        from ekoalu.human_scheduler.scheduler import is_action_allowed_now
        now = self._day_off_at_10h(monkeypatch)
        assert not is_action_allowed_now(now)

    def test_email_passe_le_jour_off(self, monkeypatch):
        from ekoalu.human_scheduler.scheduler import is_action_allowed_now
        now = self._day_off_at_10h(monkeypatch)
        assert is_action_allowed_now(now, channel="email")

    def test_prochain_creneau_saute_le_jour_off(self, monkeypatch):
        from ekoalu.human_scheduler.windows import next_active_window_start
        now = self._day_off_at_10h(monkeypatch).replace(hour=6)
        nxt = next_active_window_start(now)
        assert nxt.date() != now.date()  # le creneau du jour off est saute

    def test_log_au_premier_refus_seulement(self, monkeypatch, caplog):
        import logging

        from ekoalu.human_scheduler import scheduler
        monkeypatch.setattr(scheduler, "_day_off_logged", None)
        now = self._day_off_at_10h(monkeypatch)
        with caplog.at_level(logging.INFO, logger="ekoalu.human_scheduler.scheduler"):
            scheduler.is_action_allowed_now(now)
            scheduler.is_action_allowed_now(now)
        hits = [r for r in caplog.records if "jour off" in r.getMessage().lower()]
        assert len(hits) == 1


class TestJitterJournalier:
    """Point 3 audit : casser la signature « volume pile au cap chaque jour »."""

    def test_borne_085_a_100_sur_60_jours(self):
        d = dt.date(2026, 7, 1)
        for i in range(60):
            f = daily_jitter_factor(d + dt.timedelta(days=i))
            assert JITTER_MIN <= f <= JITTER_MAX

    def test_deterministe_par_date(self):
        d = dt.date(2026, 7, 8)
        assert daily_jitter_factor(d) == daily_jitter_factor(d)

    def test_varie_d_un_jour_a_l_autre(self):
        d = dt.date(2026, 7, 1)
        values = {daily_jitter_factor(d + dt.timedelta(days=i)) for i in range(30)}
        assert len(values) > 10  # pas un facteur fige


@pytest.mark.django_db
class TestCapLecturesPondere:
    """Le cap lectures effectif = nominal x poids jour (point 1 audit)."""

    def _force_weight(self, monkeypatch, weight: float):
        from ekoalu.human_scheduler import budget
        monkeypatch.setattr(budget, "daily_weight_factor", lambda d=None: weight)

    def test_samedi_cap_a_20_pourcent_du_nominal(self, monkeypatch):
        monkeypatch.delenv("EKOALU_PROFILE_READS_CAP_RAMP", raising=False)
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "80")
        self._force_weight(monkeypatch, 0.2)
        from ekoalu.read_guard.guard import daily_reads_cap
        assert daily_reads_cap() == 16

    def test_dimanche_cap_zero(self, monkeypatch):
        monkeypatch.delenv("EKOALU_PROFILE_READS_CAP_RAMP", raising=False)
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "80")
        self._force_weight(monkeypatch, 0.0)
        from ekoalu.read_guard.guard import daily_reads_cap, is_cap_reached
        assert daily_reads_cap() == 0
        assert is_cap_reached()

    def test_jour_plein_cap_nominal(self, monkeypatch):
        monkeypatch.delenv("EKOALU_PROFILE_READS_CAP_RAMP", raising=False)
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "80")
        self._force_weight(monkeypatch, 1.0)
        from ekoalu.read_guard.guard import daily_reads_cap
        assert daily_reads_cap() == 80

    def test_empilement_poids_x_jitter(self, monkeypatch):
        """Cap effectif = nominal x poids hebdo x jitter (points 1+3 audit)."""
        monkeypatch.delenv("EKOALU_PROFILE_READS_CAP_RAMP", raising=False)
        monkeypatch.setenv("EKOALU_DAILY_PROFILE_READS_CAP", "80")
        from ekoalu.human_scheduler import budget
        monkeypatch.setattr(budget, "daily_weight_factor", lambda d=None: 0.2)
        monkeypatch.setattr(budget, "daily_jitter_factor", lambda d=None: 0.9)
        from ekoalu.read_guard.guard import daily_reads_cap
        assert daily_reads_cap() == round(80 * 0.2 * 0.9)  # = 14


@pytest.mark.django_db
class TestCapsEnvoisPonderes:
    """Les caps quotidiens invitations/messages sont modules pareil (point 1)."""

    def _seed_sent(self, kind, n: int):
        from datetime import timedelta

        from django.utils import timezone

        from ekoalu.outbound_validation import (
            OutboundKind, OutboundStatus, PendingOutbound,
        )
        sent_at = timezone.now() - timedelta(hours=2)
        PendingOutbound.objects.bulk_create([
            PendingOutbound(
                prospect_public_id=f"sent-{kind}-{i}",
                kind=kind,
                ai_draft="x",
                status=OutboundStatus.SENT,
                sent_at=sent_at,
            )
            for i in range(n)
        ])
        return OutboundKind, OutboundStatus, PendingOutbound

    def _run(self, monkeypatch, weight: float):
        from unittest.mock import MagicMock, patch

        from ekoalu.human_scheduler import budget
        from ekoalu.outbound_validation.sender import process_approved_queue

        monkeypatch.setattr(budget, "daily_weight_factor", lambda d=None: weight)
        with patch(
            "ekoalu.outbound_validation.sender.is_action_allowed_now",
            return_value=True,
        ), patch(
            "ekoalu.read_guard.guard.is_cap_reached", return_value=False,
        ):
            return process_approved_queue(session=MagicMock(), dry_run=True)

    def test_samedi_invitations_bloquees_bien_avant_le_cap_nominal(
        self, monkeypatch,
    ):
        """Cap nominal 8/j, samedi (0.2) -> 2 effectives : 2 envoyees = bloque."""
        from django.utils import timezone

        from ekoalu import conf
        monkeypatch.setattr(conf, "DAILY_INVITE_CAP", 8)
        OutboundKind, OutboundStatus, PendingOutbound = self._seed_sent(
            "invitation", 2,
        )
        appr = PendingOutbound.objects.create(
            prospect_public_id="appr-inv",
            kind=OutboundKind.INVITATION,
            ai_draft="x",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now(),
        )

        stats = self._run(monkeypatch, weight=0.2)

        assert stats["sent"] == 0
        appr.refresh_from_db()
        assert appr.status == OutboundStatus.APPROVED  # pas envoyee

    def test_samedi_messages_bloques_a_20_pourcent(self, monkeypatch):
        """Cap nominal 15/j, samedi (0.2) -> 3 effectifs : 3 envoyes = bloque."""
        from django.utils import timezone

        from ekoalu import conf
        monkeypatch.setattr(conf, "DAILY_MESSAGE_CAP", 15)
        OutboundKind, OutboundStatus, PendingOutbound = self._seed_sent(
            "follow_up", 3,
        )
        appr = PendingOutbound.objects.create(
            prospect_public_id="appr-msg",
            kind=OutboundKind.FOLLOW_UP,
            ai_draft="x",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now(),
        )

        stats = self._run(monkeypatch, weight=0.2)

        assert stats["sent"] == 0
        appr.refresh_from_db()
        assert appr.status == OutboundStatus.APPROVED

    def test_jour_plein_rien_ne_change(self, monkeypatch):
        """Poids 1.0 : 2 invitations envoyees sur cap 8 -> la file continue."""
        from django.utils import timezone

        from ekoalu import conf
        monkeypatch.setattr(conf, "DAILY_INVITE_CAP", 8)
        OutboundKind, OutboundStatus, PendingOutbound = self._seed_sent(
            "invitation", 2,
        )
        PendingOutbound.objects.create(
            prospect_public_id="appr-inv2",
            kind=OutboundKind.INVITATION,
            ai_draft="x",
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now(),
        )

        stats = self._run(monkeypatch, weight=1.0)

        assert stats["processed"] == 1  # dry_run : aurait envoye
