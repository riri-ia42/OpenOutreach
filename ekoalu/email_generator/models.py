"""Dataclasses du générateur cold mail."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ColdEmailDraft:
    """Brouillon généré par Claude pour un cold mail EKOALU."""

    subject: str
    body: str
    model_used: str = ""
    variant_used: str = ""  # id de la variante de prompt (A/B testing, brique H)

    def is_valid(self) -> bool:
        return bool(self.subject.strip()) and bool(self.body.strip())
