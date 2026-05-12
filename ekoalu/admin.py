"""Django Admin enrichi pour les modeles EKOALU.

Expose PendingReply et CorrectionExample avec des vues ergonomiques :
- PendingReply : preview brouillon + final + bouton "envoyer"
- CorrectionExample : visualisation diff + similarity ratio
"""
from __future__ import annotations

from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html

from ekoalu.inbox_assist.models import CorrectionExample, PendingReply
from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound


@admin.register(PendingReply)
class PendingReplyAdmin(admin.ModelAdmin):
    list_display = (
        "prospect_public_id",
        "intent_badge",
        "status_badge",
        "preview_inbound",
        "preview_draft",
        "created_at_display",
    )
    list_filter = ("status", "intent", "created_at")
    search_fields = ("prospect_public_id", "inbound_message", "ai_draft", "final_sent")
    readonly_fields = ("created_at", "sent_at")
    fieldsets = (
        ("Prospect", {
            "fields": ("prospect_public_id", "campaign_id", "intent"),
        }),
        ("Message entrant", {
            "fields": ("inbound_message",),
        }),
        ("Brouillon IA + envoi final", {
            "fields": ("ai_draft", "final_sent"),
            "description": (
                "Editer final_sent puis passer status=sent. Le delta entre "
                "ai_draft et final_sent sera enregistré comme CorrectionExample "
                "pour ameliorer les futurs brouillons."
            ),
        }),
        ("Etat", {
            "fields": ("status", "created_at", "sent_at"),
        }),
    )
    actions = ["accepter_brouillon_tel_quel", "marquer_envoye"]

    def intent_badge(self, obj):
        colors = {
            "rdv_request": "#28a745",         # vert
            "technical_question": "#007bff",  # bleu
            "objection": "#ffc107",            # jaune
            "off_topic": "#6c757d",            # gris
            "opt_out": "#dc3545",              # rouge
        }
        color = colors.get(obj.intent, "#6c757d")
        return format_html(
            '<span style="background:{};color:white;padding:2px 8px;border-radius:3px;font-size:11px">{}</span>',
            color, obj.intent,
        )
    intent_badge.short_description = "Intent"

    def status_badge(self, obj):
        colors = {"pending": "#ffc107", "sent": "#28a745", "discarded": "#6c757d"}
        color = colors.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background:{};color:white;padding:2px 8px;border-radius:3px">{}</span>',
            color, obj.status,
        )
    status_badge.short_description = "Statut"

    def preview_inbound(self, obj):
        return (obj.inbound_message or "")[:60] + ("..." if len(obj.inbound_message or "") > 60 else "")
    preview_inbound.short_description = "Message recu"

    def preview_draft(self, obj):
        return (obj.ai_draft or "")[:60] + ("..." if len(obj.ai_draft or "") > 60 else "")
    preview_draft.short_description = "Brouillon IA"

    def created_at_display(self, obj):
        return obj.created_at.strftime("%Y-%m-%d %H:%M") if obj.created_at else "-"
    created_at_display.short_description = "Cree"

    @admin.action(description="Accepter le brouillon tel quel (final_sent = ai_draft)")
    def accepter_brouillon_tel_quel(self, request, queryset):
        updated = 0
        for pr in queryset.filter(status=PendingReply.Status.PENDING):
            pr.final_sent = pr.ai_draft
            pr.save()
            updated += 1
        self.message_user(
            request, f"{updated} brouillon(s) acceptes (final_sent = ai_draft).",
            messages.SUCCESS,
        )

    @admin.action(description="Marquer comme envoye (et creer CorrectionExample)")
    def marquer_envoye(self, request, queryset):
        sent = 0
        for pr in queryset.filter(status=PendingReply.Status.PENDING):
            if not pr.final_sent:
                pr.final_sent = pr.ai_draft
            pr.status = PendingReply.Status.SENT
            pr.sent_at = timezone.now()
            pr.save()
            CorrectionExample.from_pending(pr, persona_slug="manual")
            sent += 1
        self.message_user(
            request, f"{sent} message(s) marques comme envoyes + CorrectionExample crees.",
            messages.SUCCESS,
        )


@admin.register(CorrectionExample)
class CorrectionExampleAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "persona_slug",
        "similarity_display",
        "used_in_prompt",
        "created_at",
    )
    list_filter = ("persona_slug", "used_in_prompt", "created_at")
    readonly_fields = (
        "pending_reply",
        "persona_slug",
        "similarity_ratio",
        "diff_lines",
        "created_at",
        "used_in_prompt",
    )

    def similarity_display(self, obj):
        pct = obj.similarity_ratio * 100
        color = "#28a745" if pct > 80 else "#ffc107" if pct > 50 else "#dc3545"
        return format_html(
            '<span style="color:{};font-weight:bold">{}%</span>',
            color, f"{pct:.0f}",
        )
    similarity_display.short_description = "Similarite brouillon/final"


@admin.register(PendingOutbound)
class PendingOutboundAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "kind_badge",
        "status_badge",
        "prospect_public_id",
        "campaign_name",
        "preview_content",
        "created_at_display",
    )
    list_filter = ("status", "kind", "created_at")
    search_fields = ("prospect_public_id", "campaign_name", "ai_draft", "final_content")
    readonly_fields = ("created_at", "approved_at", "sent_at", "error_message")
    fieldsets = (
        ("Prospect", {
            "fields": ("prospect_public_id", "prospect_urn", "campaign_id", "campaign_name"),
        }),
        ("Message", {
            "fields": ("kind", "ai_draft", "final_content"),
            "description": (
                "Si final_content est rempli, c'est ce qui sera envoyé. "
                "Sinon ai_draft est utilisé."
            ),
        }),
        ("Workflow", {
            "fields": ("status", "rejection_reason"),
        }),
        ("Logs", {
            "fields": ("created_at", "approved_at", "sent_at", "error_message"),
        }),
    )
    actions = ["approuver", "rejeter", "marquer_envoye"]

    def kind_badge(self, obj):
        colors = {
            "invitation": "#007bff",
            "follow_up": "#28a745",
            "reply": "#6f42c1",
        }
        color = colors.get(obj.kind, "#6c757d")
        return format_html(
            '<span style="background:{};color:white;padding:2px 8px;border-radius:3px;font-size:11px">{}</span>',
            color, obj.kind,
        )
    kind_badge.short_description = "Type"

    def status_badge(self, obj):
        colors = {
            "pending": "#ffc107",
            "approved": "#17a2b8",
            "sent": "#28a745",
            "rejected": "#6c757d",
            "expired": "#6c757d",
            "failed": "#dc3545",
        }
        color = colors.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background:{};color:white;padding:2px 8px;border-radius:3px">{}</span>',
            color, obj.status,
        )
    status_badge.short_description = "Statut"

    def preview_content(self, obj):
        content = obj.content_to_send
        return (content or "")[:80] + ("..." if len(content or "") > 80 else "")
    preview_content.short_description = "Aperçu"

    def created_at_display(self, obj):
        return obj.created_at.strftime("%Y-%m-%d %H:%M") if obj.created_at else "-"
    created_at_display.short_description = "Créé"

    @admin.action(description="Approuver (marquer 'approved' = à envoyer)")
    def approuver(self, request, queryset):
        from django.utils import timezone
        updated = queryset.filter(status=OutboundStatus.PENDING).update(
            status=OutboundStatus.APPROVED,
            approved_at=timezone.now(),
        )
        self.message_user(request, f"{updated} message(s) approuvé(s).", messages.SUCCESS)

    @admin.action(description="Refuser")
    def rejeter(self, request, queryset):
        updated = queryset.filter(status=OutboundStatus.PENDING).update(
            status=OutboundStatus.REJECTED,
        )
        self.message_user(request, f"{updated} message(s) refusé(s).", messages.WARNING)

    @admin.action(description="Marquer comme envoyé manuellement")
    def marquer_envoye(self, request, queryset):
        from django.utils import timezone
        updated = queryset.filter(
            status__in=[OutboundStatus.PENDING, OutboundStatus.APPROVED],
        ).update(
            status=OutboundStatus.SENT,
            sent_at=timezone.now(),
        )
        self.message_user(request, f"{updated} message(s) marqué(s) envoyé.", messages.SUCCESS)
