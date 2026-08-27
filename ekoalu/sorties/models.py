"""Registre des sorties de prospection décidées par Richard."""
from __future__ import annotations

from django.db import models


class ProspectionSortie(models.Model):
    """Une personne ou une société sortie volontairement de la prospection.

    Source of truth du « qui ne prospecte-t-on plus » décidé par Richard —
    `Lead.disqualified` reste l'état opérationnel mais mélange rejets LLM,
    unreachable, bounces… Le registre, lui, ne contient que les décisions.
    """

    class Kind(models.TextChoices):
        PERSON = "person", "Personne"
        COMPANY = "company", "Société"

    kind = models.CharField(max_length=16, choices=Kind.choices, db_index=True)
    # Personne : public_identifier du Lead. Société : vide.
    public_identifier = models.CharField(max_length=255, blank=True, default="", db_index=True)
    # Société : siren si connu (leads mail-only). Personne : siren de sa société si connu.
    siren = models.CharField(max_length=20, blank=True, default="", db_index=True)
    # Nom affichable (personne ou société) — pour l'état des sorties.
    label = models.CharField(max_length=255, blank=True, default="")
    company_name = models.CharField(max_length=255, blank=True, default="")
    reason = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "ekoalu"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"ProspectionSortie({self.kind}, {self.label or self.public_identifier or self.siren})"
