"""Verdict fabricant / revendeur d'une société, clé = SIREN.

Clé SIREN et non Lead : le verdict porte sur l'ENTREPRISE, il reste valable si
le même SIREN réapparaît par une autre source (DECP, BDD PROSPECT, Mailjet).
"""
from __future__ import annotations

from django.db import models


class FabricantVerdict(models.Model):
    """Résultat de l'analyse du site web d'une société."""

    FABRICANT = "fabricant"
    REVENDEUR = "revendeur_poseur"
    INDETERMINE = "indetermine"
    VERDICT_CHOICES = [
        (FABRICANT, "Fabricant (atelier propre)"),
        (REVENDEUR, "Revendeur / poseur"),
        (INDETERMINE, "Indéterminé"),
    ]

    CONFIANCE_CHOICES = [("haute", "Haute"), ("moyenne", "Moyenne"), ("basse", "Basse")]

    siren = models.CharField(max_length=20, unique=True, db_index=True)
    entreprise = models.CharField(max_length=255, blank=True)
    code_naf = models.CharField(max_length=10, blank=True)
    domain = models.CharField(max_length=255, blank=True)
    url = models.URLField(max_length=500, blank=True)

    verdict = models.CharField(max_length=32, choices=VERDICT_CHOICES, db_index=True)
    confiance = models.CharField(max_length=16, choices=CONFIANCE_CHOICES, default="basse")
    materiaux = models.JSONField(default=list, blank=True)
    indices_fabrication = models.JSONField(default=list, blank=True)
    indices_negoce = models.JSONField(default=list, blank=True)
    marques_produits_finis = models.JSONField(default=list, blank=True)
    justification = models.TextField(blank=True)

    # Traçabilité : quel modèle a tranché, et si Haiku a dû être escaladé
    model_used = models.CharField(max_length=64, blank=True)
    escalated = models.BooleanField(default=False)
    fetch_error = models.CharField(max_length=128, blank=True)
    pages_fetched = models.JSONField(default=list, blank=True)

    checked_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Verdict fabricant"
        verbose_name_plural = "Verdicts fabricant"
        ordering = ["-checked_at"]

    def __str__(self) -> str:
        return f"{self.entreprise or self.siren} — {self.verdict} ({self.confiance})"

    @property
    def is_fabricant(self) -> bool:
        """Fabricant avéré : seul un verdict non-basse confiance compte."""
        return self.verdict == self.FABRICANT and self.confiance != "basse"
