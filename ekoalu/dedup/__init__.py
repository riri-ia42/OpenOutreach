"""Dedup cross-campagne : un Lead = un Deal actif maximum.

Voir consolidator.py pour la logique d'arbitrage + cleanup.
"""
from __future__ import annotations

from ekoalu.dedup.consolidator import (
    DedupReport,
    consolidate_duplicate_deals,
    pick_best_deal,
)

__all__ = ["DedupReport", "consolidate_duplicate_deals", "pick_best_deal"]
