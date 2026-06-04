"""Modele LeadDiscovery : rattache un Lead a la campagne qui l'a trouve."""
from __future__ import annotations

from django.db import models


class LeadDiscovery(models.Model):
    """Trace : telle campagne a decouvert tel profil au sourcing.

    Le chainon manquant qui permet de scoper la qualification. Un meme profil
    PEUT etre decouvert par plusieurs campagnes (recherches qui se recoupent),
    d'ou le unique_together (lead, campaign) plutot qu'un OneToOne.
    """

    lead = models.ForeignKey(
        "crm.Lead",
        on_delete=models.CASCADE,
        related_name="discoveries",
    )
    campaign = models.ForeignKey(
        "linkedin.Campaign",
        on_delete=models.CASCADE,
        related_name="discovered_leads",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "ekoalu"
        unique_together = [("lead", "campaign")]
        indexes = [
            models.Index(fields=["campaign", "lead"]),
        ]

    def __str__(self) -> str:
        return f"LeadDiscovery(lead={self.lead_id}, campaign={self.campaign_id})"
