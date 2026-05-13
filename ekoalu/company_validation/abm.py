"""Account-Based Marketing : lier une Campaign OpenOutreach a une entreprise
cible (ApprovedCompany). Quand cette relation existe, la campagne est ABM
(focus sur 1 entreprise, tous angles d'attaque) au lieu d'une campagne
classique par persona.
"""
from __future__ import annotations

from django.db import models


class AbmCampaignLink(models.Model):
    """Lien explicite Campaign <-> ApprovedCompany pour le mode ABM.

    Une seule entreprise par Campaign (OneToOne cote campaign), mais une
    entreprise peut avoir plusieurs campagnes ABM dans le temps (ex
    relancer 6 mois apres une campagne fermee).
    """

    campaign = models.OneToOneField(
        "linkedin.Campaign",
        on_delete=models.CASCADE,
        related_name="abm_link",
    )
    target_company = models.ForeignKey(
        "ekoalu.ApprovedCompany",
        on_delete=models.PROTECT,
        related_name="abm_campaigns",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        app_label = "ekoalu"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"AbmCampaignLink(campaign={self.campaign_id}, company={self.target_company_id})"
