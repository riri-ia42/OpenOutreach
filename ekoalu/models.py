"""Modèles Django agrégés de l app ekoalu.

Re-exporte les modèles des sous-modules pour que Django les détecte.
"""
from ekoalu.inbox_assist.models import CorrectionExample, PendingReply
from ekoalu.outbound_validation.models import PendingOutbound

__all__ = ["PendingReply", "CorrectionExample", "PendingOutbound"]
