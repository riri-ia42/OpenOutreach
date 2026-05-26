"""Modèle PendingOutbound — queue de validation des messages sortants."""
from __future__ import annotations

from django.db import models


class OutboundKind(models.TextChoices):
    INVITATION = "invitation", "Invitation LinkedIn"
    FOLLOW_UP = "follow_up", "Message follow-up LinkedIn"
    REPLY = "reply", "Réponse à un prospect"
    EMAIL_COLD = "email_cold", "Cold mail (1er contact)"
    EMAIL_FOLLOW_UP = "email_follow_up", "Email follow-up"


class OutboundStatus(models.TextChoices):
    PENDING = "pending", "En attente validation"
    APPROVED = "approved", "Approuvé (à envoyer)"
    SENT = "sent", "Envoyé"
    REJECTED = "rejected", "Refusé"
    EXPIRED = "expired", "Expiré"
    FAILED = "failed", "Échec envoi"
    BLOCKED_COMPANY = "blocked_company", "Bloqué (entreprise non validée)"


class PendingOutbound(models.Model):
    """Message en attente de validation Richard avant envoi LinkedIn."""

    # Identification
    prospect_public_id = models.CharField(max_length=128, db_index=True)
    prospect_urn = models.CharField(max_length=128, blank=True)
    prospect_company = models.CharField(
        max_length=255, blank=True, db_index=True,
        help_text="Nom de l'entreprise extrait du profil (pour validation entreprise)",
    )
    campaign_id = models.IntegerField(null=True, blank=True, db_index=True)
    campaign_name = models.CharField(max_length=255, blank=True)

    # Message
    kind = models.CharField(max_length=16, choices=OutboundKind.choices, db_index=True)
    ai_draft = models.TextField(help_text="Message généré par l'IA (Claude)")
    final_content = models.TextField(
        blank=True,
        help_text="Contenu final édité par Richard (vide = utilise ai_draft)",
    )

    # Workflow
    status = models.CharField(
        max_length=16,
        choices=OutboundStatus.choices,
        default=OutboundStatus.PENDING,
        db_index=True,
    )
    rejection_reason = models.TextField(blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        app_label = "ekoalu"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["kind", "status"]),
        ]

    def __str__(self) -> str:
        return f"PendingOutbound({self.kind}, {self.prospect_public_id}, {self.status})"

    @property
    def content_to_send(self) -> str:
        """Texte qui sera réellement envoyé : final_content si édité, sinon ai_draft."""
        return self.final_content.strip() if self.final_content.strip() else self.ai_draft
