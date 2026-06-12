"""État de la rotation Serper par campagne ABM.

Permet de servir TOUTES les campagnes ABM à tour de rôle (décision Richard
12/06) : on sert d'abord la moins récemment servie, et une campagne dont les
requêtes ne ramènent plus AUCUN nouveau profil sur N passages consécutifs est
marquée épuisée (on arrête de dépenser des crédits dessus).
"""
from __future__ import annotations

from django.db import models


class GoogleSourcingState(models.Model):
    campaign = models.OneToOneField(
        "linkedin.Campaign", on_delete=models.CASCADE, related_name="google_sourcing_state",
    )
    last_run_at = models.DateTimeField(null=True, blank=True)
    consecutive_empty_runs = models.PositiveIntegerField(default=0)
    exhausted = models.BooleanField(default=False)
    total_new_leads = models.PositiveIntegerField(default=0)
    total_queries = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = "ekoalu"
        verbose_name = "État sourcing Google (rotation ABM)"
        verbose_name_plural = "États sourcing Google (rotation ABM)"

    def __str__(self):
        flag = "ÉPUISÉE" if self.exhausted else f"{self.consecutive_empty_runs} runs vides"
        return f"{self.campaign.name} [{flag}]"
