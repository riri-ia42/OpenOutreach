"""Idempotence cold-mail : FAILED/EXPIRED bloquent la regeneration (revue 17/06 P2-1).

Sans FAILED/EXPIRED dans les statuts bloquants, un cold mail dont l'envoi a
echoue serait regenere a chaque passe → doublon en file de validation.
"""
from __future__ import annotations

from ekoalu.management.commands.generate_cold_emails import _BLOCKING_STATUSES
from ekoalu.outbound_validation.models import OutboundStatus


def test_failed_et_expired_bloquent_la_regeneration():
    assert OutboundStatus.FAILED in _BLOCKING_STATUSES
    assert OutboundStatus.EXPIRED in _BLOCKING_STATUSES


def test_tous_les_statuts_non_renvoyables_sont_bloquants():
    """Seul un lead SANS aucun cold mail doit pouvoir en recevoir un nouveau :
    tout statut existant (ouvert, terminal ou echoue) bloque la regeneration."""
    for status in OutboundStatus:
        assert status in _BLOCKING_STATUSES, f"{status} devrait bloquer la regen cold-mail"
