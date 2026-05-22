"""Persistance des incidents et actions du doctor.

Un Incident = une session de diagnostic (1 healthcheck KO -> 1 appel Claude ->
mail advisory). Une Action = une suggestion concrete extraite du diagnostic
(en V0 : juste loggee, pas executee).
"""
from __future__ import annotations

from django.db import models


class DoctorIncident(models.Model):
    """Une session de diagnostic du doctor.

    signature = hash stable du diagnostic Claude (status + cause principale).
    Permet de detecter les incidents recurrents pour escalader plutot que
    relancer le doctor en boucle.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Ouvert (en cours)"
        ADVISORY_SENT = "advisory_sent", "Mail advisory envoye"
        AUTO_RESOLVED = "auto_resolved", "Resolu automatiquement"
        ESCALATED = "escalated", "Escalade humaine"
        FAILED = "failed", "Echec diagnostic"

    started_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)

    trigger_health_status = models.CharField(max_length=40, blank=True)
    signature = models.CharField(max_length=64, db_index=True, blank=True)

    diagnosis = models.TextField(blank=True)
    confidence = models.FloatField(default=0.0)
    actions_proposed = models.JSONField(default=list, blank=True)

    cost_usd = models.DecimalField(max_digits=8, decimal_places=5, default=0)
    mail_sent_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"Incident#{self.pk} {self.status} {self.trigger_health_status}"


class DoctorAction(models.Model):
    """Une action proposee (V0) ou executee (V1+) sur un incident."""

    class Mode(models.TextChoices):
        PROPOSED = "proposed", "Propose (advisory)"
        EXECUTED = "executed", "Execute automatiquement"
        SKIPPED = "skipped", "Skip (hors whitelist / preconditions)"

    incident = models.ForeignKey(
        DoctorIncident, on_delete=models.CASCADE, related_name="actions",
    )
    action_type = models.CharField(max_length=50)
    payload = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)

    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.PROPOSED)
    created_at = models.DateTimeField(auto_now_add=True)
    executed_at = models.DateTimeField(null=True, blank=True)
    success = models.BooleanField(default=False)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["incident", "created_at"]

    def __str__(self) -> str:
        return f"{self.action_type} on Incident#{self.incident_id}"
