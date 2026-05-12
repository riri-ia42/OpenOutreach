"""Modèles Django pour inbox_assist : PendingReply + CorrectionExample.

V1 minimal — pas encore intégré au follow-up agent OpenOutreach.
L'intégration agent sera Phase 3.5 / V2.
"""
from __future__ import annotations

import difflib

from django.db import models

from ekoalu.inbox_assist.intent_classifier import Intent


class PendingReply(models.Model):
    """Brouillon de réponse en attente de validation Richard."""

    class Status(models.TextChoices):
        PENDING = "pending", "En attente validation"
        SENT = "sent", "Envoyé"
        DISCARDED = "discarded", "Abandonné"

    # Note : on ne FK pas directement à Lead/Deal de crm pour découpler
    # (lazy reference par identifiant LinkedIn)
    prospect_public_id = models.CharField(max_length=128, db_index=True)
    campaign_id = models.IntegerField(null=True, blank=True, db_index=True)

    inbound_message = models.TextField(help_text="Message du prospect")
    intent = models.CharField(
        max_length=32,
        choices=[(i.value, i.name) for i in Intent],
        default=Intent.OFF_TOPIC.value,
    )

    ai_draft = models.TextField(help_text="Brouillon généré par Claude")
    final_sent = models.TextField(
        blank=True,
        help_text="Texte final envoyé (après édition Richard)",
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "ekoalu"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"PendingReply({self.prospect_public_id}, {self.intent}, {self.status})"


class CorrectionExample(models.Model):
    """Snapshot d une correction Richard pour alimenter le few-shot d apprentissage.

    Créé automatiquement quand PendingReply.status passe à SENT et que
    final_sent != ai_draft.
    """

    pending_reply = models.OneToOneField(
        PendingReply,
        on_delete=models.CASCADE,
        related_name="correction",
    )
    persona_slug = models.CharField(max_length=64, db_index=True)
    similarity_ratio = models.FloatField(
        help_text="0.0 = totalement réécrit, 1.0 = aucune correction",
    )
    diff_lines = models.JSONField(
        default=list,
        help_text="Liste de lignes diff unifiées (debug)",
    )
    explanation = models.CharField(
        max_length=300, blank=True, default="",
        help_text="Note Richard expliquant pourquoi cette correction (aide apprentissage Claude)",
    )
    used_in_prompt = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True quand l exemple a été injecté en few-shot",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "ekoalu"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"CorrectionExample({self.persona_slug}, sim={self.similarity_ratio:.2f})"

    @classmethod
    def compute_similarity_ratio(cls, draft: str, final: str) -> float:
        """SequenceMatcher.ratio() — 0=tout réécrit, 1=identique."""
        if not draft and not final:
            return 1.0
        return difflib.SequenceMatcher(None, draft, final).ratio()

    @classmethod
    def from_pending(
        cls,
        pending: PendingReply,
        persona_slug: str = "",
        explanation: str = "",
    ) -> CorrectionExample:
        """Crée un CorrectionExample à partir d un PendingReply envoyé."""
        ratio = cls.compute_similarity_ratio(pending.ai_draft, pending.final_sent)
        diff = list(
            difflib.unified_diff(
                pending.ai_draft.splitlines(),
                pending.final_sent.splitlines(),
                lineterm="",
                n=2,
            )
        )
        return cls.objects.create(
            pending_reply=pending,
            persona_slug=persona_slug,
            similarity_ratio=ratio,
            diff_lines=diff,
            explanation=explanation,
        )
