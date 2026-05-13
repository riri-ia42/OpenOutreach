"""Log des appels Claude API."""
from __future__ import annotations

from django.db import models


class ClaudeUsageLog(models.Model):
    """Un enregistrement par appel Claude API (sync ou async)."""

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    model = models.CharField(max_length=128)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    cache_creation_tokens = models.PositiveIntegerField(default=0)
    cache_read_tokens = models.PositiveIntegerField(default=0)
    cost_usd = models.FloatField(default=0.0)
    context = models.CharField(
        max_length=64, blank=True, db_index=True,
        help_text="Source de l'appel (qualifier, follow_up, invitation, suggester...)",
    )
    duration_ms = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = "ekoalu"
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["timestamp", "model"]),
        ]

    def __str__(self) -> str:
        return (
            f"{self.timestamp:%Y-%m-%d %H:%M} {self.model} "
            f"in={self.input_tokens} out={self.output_tokens} "
            f"${self.cost_usd:.4f}"
        )
