"""Feedback Richard sur la qualification Claude : remettre un prospect dans la
boucle apres une eviction, en expliquant pourquoi pour aider l'apprentissage.
"""
from __future__ import annotations

from django.db import models


class QualificationFeedback(models.Model):
    """Une correction Richard sur la decision Claude de disqualifier.

    Cree quand Richard remet un Deal en Qualified depuis la liste des
    disqualifies, avec une explication courte.
    """

    class Kind(models.TextChoices):
        REQUALIFY = "requalify", "Remettre en file (Claude s'est trompe)"
        CONFIRM_REJECT = "confirm_reject", "Confirmer le rejet (Claude a raison)"

    prospect_public_id = models.CharField(max_length=128, db_index=True)
    campaign_id = models.IntegerField(null=True, blank=True, db_index=True)
    campaign_name = models.CharField(max_length=200, blank=True, default="")

    claude_reason = models.TextField(
        blank=True, default="",
        help_text="Raison originale Claude qui a fait disqualifier ce prospect",
    )
    richard_explanation = models.CharField(
        max_length=500,
        help_text="Pourquoi Richard contredit / confirme la decision Claude",
    )
    kind = models.CharField(
        max_length=20, choices=Kind.choices, default=Kind.REQUALIFY,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    used_in_prompt = models.BooleanField(
        default=False, db_index=True,
        help_text="True quand l'exemple a ete injecte en few-shot dans le prompt qualifier",
    )

    class Meta:
        app_label = "ekoalu"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["kind", "-created_at"]),
            models.Index(fields=["campaign_id", "kind"]),
        ]

    def __str__(self) -> str:
        return (
            f"QualificationFeedback({self.prospect_public_id}, "
            f"{self.kind}, '{self.richard_explanation[:40]}...')"
        )
