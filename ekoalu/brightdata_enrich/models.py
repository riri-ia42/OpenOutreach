"""Compteur de consommation Bright Data par MOIS calendaire.

Le free tier Bright Data est mensuel (5 000 enregistrements, remis à zéro le
1er) — contrairement à Apify (quota par jour). On borne notre consommation
sous le quota gratuit avec une marge (défaut 4 500) : au-delà, Bright Data
répond False à ``brightdata_ready()`` et la chaîne passe au fournisseur
suivant. Ne JAMAIS dépasser le gratuit sans décision explicite de Richard
(la carte enregistrée serait débitée).
"""
from __future__ import annotations

from django.db import models


class BrightdataUsageMonth(models.Model):
    month = models.CharField(max_length=7, unique=True)  # "2026-09"
    count = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = "ekoalu"
        verbose_name = "Consommation Bright Data (mois)"
        verbose_name_plural = "Consommations Bright Data (mois)"

    def __str__(self):
        return f"{self.month}: {self.count} collectés, {self.failed} échecs"
