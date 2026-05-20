"""Modeles persistants du module follow_up EKOALU."""
from __future__ import annotations

from django.db import models


class CampaignDmConfig(models.Model):
    """Reglages specifiques EKOALU pour la generation de DM follow-up.

    Une ligne par Campaign EKOALU. Cree a la volee si absente.
    """

    campaign = models.OneToOneField(
        "linkedin.Campaign",
        on_delete=models.CASCADE,
        related_name="ekoalu_dm_config",
    )
    include_booking_in_first_dm = models.BooleanField(
        default=False,
        help_text="Si vrai, le 1er DM post-acceptation inclut le lien Outlook Bookings.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "ekoalu"
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return (
            f"CampaignDmConfig(campaign={self.campaign_id}, "
            f"booking_in_first={self.include_booking_in_first_dm})"
        )


def get_or_create_dm_config(campaign) -> "CampaignDmConfig":
    """Recupere (ou cree) la config DM pour une Campaign."""
    obj, _ = CampaignDmConfig.objects.get_or_create(campaign=campaign)
    return obj
