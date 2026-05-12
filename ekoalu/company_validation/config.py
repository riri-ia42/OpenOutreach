"""Configuration mode de validation des entreprises."""
from __future__ import annotations

import os


def is_company_validation_enabled() -> bool:
    """True si la validation entreprise est active (defaut).

    Désactivable via env var EKOALU_COMPANY_VALIDATION=off pour passer en
    mode 100% auto (skip validation entreprise).
    """
    raw = os.environ.get("EKOALU_COMPANY_VALIDATION", "").strip().lower()
    if raw == "off":
        return False
    return True
