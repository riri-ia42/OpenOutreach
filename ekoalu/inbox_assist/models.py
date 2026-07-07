"""Modèles Django pour inbox_assist : PendingReply + CorrectionExample.

V1 minimal — pas encore intégré au follow-up agent OpenOutreach.
L'intégration agent sera Phase 3.5 / V2.
"""
from __future__ import annotations

import difflib

from django.db import models

from ekoalu.inbox_assist.intent_classifier import Intent


class PendingReply(models.Model):
    """Brouillon de réponse en attente de validation Richard.

    Supporte 2 canaux : LinkedIn (depuis V1) et Email (depuis brique D 27/05/2026).
    Pour le canal email :
    - `inbound_message_id` = ID Graph du mail original (idempotence sur poll)
    - `sender_email` = adresse de l'expéditeur (matché au Lead.contact_email)
    - `inbound_subject` = sujet du mail entrant (pour reply "Re: ...")
    """

    CHANNEL_LINKEDIN = "linkedin"
    CHANNEL_EMAIL = "email"
    CHANNEL_CHOICES = [(CHANNEL_LINKEDIN, "LinkedIn"), (CHANNEL_EMAIL, "Email")]

    class Status(models.TextChoices):
        PENDING = "pending", "En attente validation"
        APPROVED = "approved", "Approuvé (à envoyer)"
        SENT = "sent", "Envoyé"
        FAILED = "failed", "Échec envoi"
        DISCARDED = "discarded", "Abandonné"

    # Note : on ne FK pas directement à Lead/Deal de crm pour découpler
    # (lazy reference par identifiant LinkedIn)
    prospect_public_id = models.CharField(max_length=128, db_index=True)
    campaign_id = models.IntegerField(null=True, blank=True, db_index=True)

    # Canal de la conversation
    channel = models.CharField(
        max_length=16, choices=CHANNEL_CHOICES,
        default=CHANNEL_LINKEDIN, db_index=True,
    )
    # Spécifique email — vide si channel=linkedin
    inbound_message_id = models.CharField(
        max_length=200, blank=True, db_index=True,
        help_text="ID Graph du message email entrant (idempotence sur poll)",
    )
    inbound_subject = models.CharField(max_length=300, blank=True)
    sender_email = models.CharField(max_length=255, blank=True, db_index=True)
    cold_variant = models.CharField(
        max_length=32, blank=True, db_index=True,
        help_text="Variante de prompt du cold mail auquel ce prospect répond"
                  " (brique K : conversion A/B par variante)",
    )

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

    error_message = models.TextField(
        blank=True, default="",
        help_text="Détail erreur si status=FAILED",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "ekoalu"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["channel", "status"]),
        ]

    def __str__(self) -> str:
        return f"PendingReply({self.channel}, {self.prospect_public_id}, {self.intent}, {self.status})"


class CorrectionExample(models.Model):
    """Snapshot d un feedback Richard pour alimenter le few-shot d apprentissage.

    Trois variantes (champ `kind`) :
    - TEXT_CORRECTION : Richard a edite le brouillon (final_sent != ai_draft)
    - INSTRUCTION_ONLY : Richard a regenere via une consigne sans corriger le texte
    - BOTH : Richard a a la fois donne une consigne ET edite le brouillon

    Cree automatiquement par :
    - outbound_detail view a l approbation (TEXT_CORRECTION ou BOTH)
    - outbound_detail view a la regeneration (INSTRUCTION_ONLY)
    - outbound_detail view au refus avec motif exploitable (REJECTION)
    """

    class Kind(models.TextChoices):
        TEXT_CORRECTION = "text_correction", "Correction texte"
        INSTRUCTION_ONLY = "instruction_only", "Consigne seule"
        BOTH = "both", "Correction + consigne"
        REJECTION = "rejection", "Refus avec motif"

    class Channel(models.TextChoices):
        LINKEDIN_DM = "linkedin_dm", "DM LinkedIn"
        EMAIL_COLD = "email_cold", "Cold email"
        EMAIL_REPLY = "email_reply", "Réponse email"

    pending_reply = models.OneToOneField(
        PendingReply,
        on_delete=models.CASCADE,
        related_name="correction",
    )
    persona_slug = models.CharField(max_length=64, db_index=True)
    channel = models.CharField(
        max_length=16,
        choices=Channel.choices,
        default=Channel.LINKEDIN_DM,
        db_index=True,
        help_text="Canal du message corrigé — la génération filtre TOUJOURS par canal",
    )
    kind = models.CharField(
        max_length=20,
        choices=Kind.choices,
        default=Kind.TEXT_CORRECTION,
        db_index=True,
    )
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
    instruction = models.TextField(
        blank=True, default="",
        help_text="Consigne textuelle donnee par Richard avant la regeneration (kind=instruction_only|both)",
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
        return f"CorrectionExample({self.persona_slug}, {self.kind}, sim={self.similarity_ratio:.2f})"

    @classmethod
    def compute_similarity_ratio(cls, draft: str, final: str) -> float:
        """SequenceMatcher.ratio() — 0=tout réécrit, 1=identique."""
        if not draft and not final:
            return 1.0
        return difflib.SequenceMatcher(None, draft, final).ratio()

    @classmethod
    def derive_channel(cls, persona_slug: str = "", pending_channel: str = "") -> str:
        """Deduit le canal d un exemple : slug explicite > canal du PendingReply."""
        if persona_slug.startswith("email_reply"):
            return cls.Channel.EMAIL_REPLY
        if persona_slug.startswith("email"):
            return cls.Channel.EMAIL_COLD
        if pending_channel == PendingReply.CHANNEL_EMAIL:
            return cls.Channel.EMAIL_REPLY
        return cls.Channel.LINKEDIN_DM

    @classmethod
    def from_pending(
        cls,
        pending: PendingReply,
        persona_slug: str = "",
        explanation: str = "",
        instruction: str = "",
        channel: str = "",
    ) -> CorrectionExample:
        """Crée un CorrectionExample a partir d un PendingReply envoye.

        Le kind est inferre :
        - INSTRUCTION_ONLY si consigne presente et texte non modifie (ratio >= 0.99)
        - BOTH si consigne presente et texte modifie
        - TEXT_CORRECTION si pas de consigne
        `channel` vide → deduit du slug puis du canal du PendingReply.
        """
        ratio = cls.compute_similarity_ratio(pending.ai_draft, pending.final_sent)
        diff = list(
            difflib.unified_diff(
                pending.ai_draft.splitlines(),
                pending.final_sent.splitlines(),
                lineterm="",
                n=2,
            )
        )
        if instruction.strip():
            kind = cls.Kind.INSTRUCTION_ONLY if ratio >= 0.99 else cls.Kind.BOTH
        else:
            kind = cls.Kind.TEXT_CORRECTION
        return cls.objects.create(
            pending_reply=pending,
            persona_slug=persona_slug,
            channel=channel or cls.derive_channel(persona_slug, pending.channel),
            kind=kind,
            similarity_ratio=ratio,
            diff_lines=diff,
            explanation=explanation,
            instruction=instruction.strip(),
        )
