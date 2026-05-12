"""Dashboard EKOALU — vue d'ensemble live pour piloter la prospection.

URL : /ekoalu/
"""
from __future__ import annotations

from collections import defaultdict

from django.contrib import messages as django_messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ekoalu import conf
from ekoalu.inbox_assist.models import CorrectionExample, PendingReply
from ekoalu.outbound_validation.config import get_approval_mode
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound
from ekoalu.personas import PERSONAS


@staff_member_required
def dashboard(request):
    """Vue dashboard principal."""
    from chat.models import ChatMessage
    from crm.models import Deal, Lead
    from linkedin.models import Campaign, LinkedInProfile, SiteConfig, Task

    # ---- Infrastructure ----
    profile = LinkedInProfile.objects.filter(active=True).first()
    site_cfg = SiteConfig.load()

    # ---- Campaigns EKOALU avec stats ----
    campaigns_data = []
    for campaign in Campaign.objects.filter(name__startswith="EKOALU - ").order_by("pk"):
        # Identifier le persona depuis le label de la campagne
        persona_slug = None
        for p in PERSONAS.values():
            if p.label in campaign.name:
                persona_slug = p.slug
                break

        deals_by_state = (
            Deal.objects.filter(campaign=campaign)
            .values("state")
            .annotate(n=Count("id"))
        )
        state_counts = {d["state"]: d["n"] for d in deals_by_state}

        campaigns_data.append({
            "campaign": campaign,
            "persona_slug": persona_slug,
            "states": {
                "qualified": state_counts.get("Qualified", 0),
                "ready_to_connect": state_counts.get("Ready_to_connect", 0),
                "pending": state_counts.get("Pending", 0),
                "connected": state_counts.get("Connected", 0),
                "completed": state_counts.get("Completed", 0),
                "failed": state_counts.get("Failed", 0),
            },
            "total_leads": sum(state_counts.values()),
        })

    # ---- KPI globaux ----
    all_deals = Deal.objects.filter(campaign__name__startswith="EKOALU - ")
    total_qualified = all_deals.filter(state="Qualified").count()
    total_ready = all_deals.filter(state="Ready_to_connect").count()
    total_pending = all_deals.filter(state="Pending").count()
    total_connected = all_deals.filter(state__in=["Connected", "Completed"]).count()
    total_failed = all_deals.filter(state="Failed").count()
    total_disqualified = all_deals.filter(state="Failed", outcome="wrong_fit").count()
    total_replied = ChatMessage.objects.filter(
        owner__isnull=True,  # message inbound (du prospect)
    ).count() if hasattr(ChatMessage, "owner") else 0

    # Taux d acceptation (Connected / Pending+Connected+Failed_with_no_response)
    total_invited = total_pending + total_connected + all_deals.filter(
        state="Failed", outcome="unresponsive",
    ).count()
    accept_rate = (
        round(total_connected / total_invited * 100, 1)
        if total_invited > 0 else None
    )

    # ---- Pending replies (inbox_assist) ----
    pending_replies = PendingReply.objects.filter(
        status=PendingReply.Status.PENDING,
    ).order_by("-created_at")[:5]
    pending_count = PendingReply.objects.filter(
        status=PendingReply.Status.PENDING,
    ).count()

    # ---- Pending outbound messages (validation avant envoi) ----
    pending_outbound = PendingOutbound.objects.filter(
        status=OutboundStatus.PENDING,
    ).order_by("-created_at")[:5]
    pending_outbound_count = PendingOutbound.objects.filter(
        status=OutboundStatus.PENDING,
    ).count()
    approved_outbound_count = PendingOutbound.objects.filter(
        status=OutboundStatus.APPROVED,
    ).count()

    # ---- Tasks en queue ----
    tasks_pending = Task.objects.filter(status="pending").count()
    tasks_running = Task.objects.filter(status="running").count()
    next_task = (
        Task.objects.filter(status="pending")
        .order_by("scheduled_at")
        .first()
    )

    # ---- Apprentissage inbox_assist ----
    corrections_count = CorrectionExample.objects.count()
    corrections_in_use = CorrectionExample.objects.filter(used_in_prompt=True).count()
    avg_similarity = None
    if corrections_count > 0:
        from django.db.models import Avg
        avg_similarity = CorrectionExample.objects.aggregate(
            avg=Avg("similarity_ratio"),
        )["avg"]
        avg_similarity = round(avg_similarity * 100, 1) if avg_similarity else None

    context = {
        "now": timezone.localtime(),
        "profile": profile,
        "site_cfg": site_cfg,
        "campaigns_data": campaigns_data,
        "kpis": {
            "total_qualified": total_qualified,
            "total_ready": total_ready,
            "total_pending": total_pending,
            "total_connected": total_connected,
            "total_failed": total_failed,
            "total_disqualified": total_disqualified,
            "accept_rate": accept_rate,
            "total_invited": total_invited,
        },
        "pending_replies": pending_replies,
        "pending_count": pending_count,
        "pending_outbound": pending_outbound,
        "pending_outbound_count": pending_outbound_count,
        "approved_outbound_count": approved_outbound_count,
        "approval_mode": get_approval_mode().value,
        "tasks": {
            "pending": tasks_pending,
            "running": tasks_running,
            "next_at": next_task.scheduled_at if next_task else None,
        },
        "learning": {
            "total": corrections_count,
            "in_use": corrections_in_use,
            "avg_similarity": avg_similarity,
        },
        "conf": {
            "active_windows": conf.ACTIVE_WINDOWS,
            "weekly_target": conf.WEEKLY_INVITE_TARGET,
            "weekly_cap": conf.WEEKLY_INVITE_HARD_CAP,
            "daily_cap": conf.DAILY_INVITE_CAP,
            "booking_url": conf.CALENDAR_BOOKING_URL,
        },
    }
    return render(request, "ekoalu/dashboard.html", context)


@staff_member_required
def leads_add(request):
    """Formulaire d'ajout de prospects (seeds) à une Campaign."""
    from linkedin.models import Campaign
    from linkedin.setup.seeds import create_seed_leads, parse_seed_urls

    ekoalu_campaigns = Campaign.objects.filter(name__startswith="EKOALU - ").order_by("pk")

    if request.method == "POST":
        campaign_id = request.POST.get("campaign_id", "")
        urls_text = request.POST.get("urls", "").strip()

        if not campaign_id:
            django_messages.error(request, "Choisis une Campaign cible.")
        elif not urls_text:
            django_messages.error(request, "Colle au moins une URL LinkedIn.")
        else:
            try:
                campaign = Campaign.objects.get(pk=int(campaign_id))
            except (ValueError, Campaign.DoesNotExist):
                django_messages.error(request, "Campaign introuvable.")
                return redirect("ekoalu:leads_add")

            public_ids = parse_seed_urls(urls_text)
            if not public_ids:
                django_messages.warning(
                    request,
                    "Aucune URL LinkedIn valide trouvée. Format attendu : "
                    "https://www.linkedin.com/in/<slug>/",
                )
            else:
                created = create_seed_leads(campaign, public_ids)
                skipped = len(public_ids) - created
                msg = f"{created} prospect(s) ajouté(s) à \"{campaign.name}\""
                if skipped:
                    msg += f" ({skipped} déjà existant(s), ignoré(s))"
                django_messages.success(request, msg)
                return redirect("ekoalu:campaign_detail", pk=campaign.pk)

    return render(request, "ekoalu/leads_add.html", {
        "campaigns": ekoalu_campaigns,
    })


@staff_member_required
def campaigns_list(request):
    """Liste des Campaigns EKOALU avec stats live."""
    from crm.models import Deal
    from linkedin.models import Campaign

    only_ekoalu = request.GET.get("scope", "ekoalu") == "ekoalu"
    queryset = Campaign.objects.all()
    if only_ekoalu:
        queryset = queryset.filter(name__startswith="EKOALU - ")
    queryset = queryset.order_by("pk")

    campaigns_data = []
    for campaign in queryset:
        deals = Deal.objects.filter(campaign=campaign)
        state_counts = {}
        for d in deals.values("state").annotate(n=Count("id")):
            state_counts[d["state"]] = d["n"]

        # Identifier persona
        persona_slug = None
        for p in PERSONAS.values():
            if p.label in campaign.name:
                persona_slug = p.slug
                break

        # Compter PendingOutbound liés
        pending_out = PendingOutbound.objects.filter(
            campaign_id=campaign.pk,
            status=OutboundStatus.PENDING,
        ).count()
        approved_out = PendingOutbound.objects.filter(
            campaign_id=campaign.pk,
            status=OutboundStatus.APPROVED,
        ).count()

        campaigns_data.append({
            "campaign": campaign,
            "persona_slug": persona_slug,
            "states": {
                "qualified": state_counts.get("Qualified", 0),
                "ready": state_counts.get("Ready_to_connect", 0),
                "pending": state_counts.get("Pending", 0),
                "connected": state_counts.get("Connected", 0),
                "completed": state_counts.get("Completed", 0),
                "failed": state_counts.get("Failed", 0),
            },
            "total": sum(state_counts.values()),
            "pending_out": pending_out,
            "approved_out": approved_out,
            "is_active": campaign.action_fraction > 0 and not campaign.is_freemium,
        })

    return render(request, "ekoalu/campaigns_list.html", {
        "campaigns_data": campaigns_data,
        "only_ekoalu": only_ekoalu,
        "now": timezone.localtime(),
    })


@staff_member_required
def campaign_detail(request, pk: int):
    """Détail d'une Campaign : édition params + liste prospects + actions."""
    from crm.models import Deal
    from linkedin.models import Campaign

    campaign = get_object_or_404(Campaign, pk=pk)

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "save":
            campaign.product_docs = request.POST.get("product_docs", "").strip()
            campaign.campaign_objective = request.POST.get("campaign_objective", "").strip()
            campaign.booking_link = request.POST.get("booking_link", "").strip()
            campaign.save()
            django_messages.success(request, "Campaign mise à jour.")
            return redirect("ekoalu:campaign_detail", pk=pk)

        elif action == "pause":
            campaign.action_fraction = 0.0
            campaign.save()
            django_messages.warning(request, "Campaign mise en pause.")
            return redirect("ekoalu:campaign_detail", pk=pk)

        elif action == "resume":
            campaign.action_fraction = 1.0
            campaign.save()
            django_messages.success(request, "Campaign réactivée.")
            return redirect("ekoalu:campaign_detail", pk=pk)

    # Stats
    deals = Deal.objects.filter(campaign=campaign).select_related("lead").order_by("-creation_date")
    state_counts = {}
    for d in deals.values("state").annotate(n=Count("id")):
        state_counts[d["state"]] = d["n"]

    # Persona
    persona = None
    for p in PERSONAS.values():
        if p.label in campaign.name:
            persona = p
            break

    # Recent deals (10 derniers)
    recent_deals = deals[:20]

    # Pending outbound
    pending_outbound_list = PendingOutbound.objects.filter(
        campaign_id=campaign.pk,
    ).order_by("-created_at")[:10]

    return render(request, "ekoalu/campaign_detail.html", {
        "campaign": campaign,
        "persona": persona,
        "state_counts": state_counts,
        "total_leads": sum(state_counts.values()),
        "recent_deals": recent_deals,
        "pending_outbound_list": pending_outbound_list,
        "is_active": campaign.action_fraction > 0 and not campaign.is_freemium,
    })


@staff_member_required
def outbound_list(request):
    """Liste des messages sortants à valider."""
    status_filter = request.GET.get("status", "pending")
    kind_filter = request.GET.get("kind", "")

    queryset = PendingOutbound.objects.all().order_by("-created_at")
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    if kind_filter:
        queryset = queryset.filter(kind=kind_filter)

    counts = {
        "pending": PendingOutbound.objects.filter(status=OutboundStatus.PENDING).count(),
        "approved": PendingOutbound.objects.filter(status=OutboundStatus.APPROVED).count(),
        "sent": PendingOutbound.objects.filter(status=OutboundStatus.SENT).count(),
        "rejected": PendingOutbound.objects.filter(status=OutboundStatus.REJECTED).count(),
    }

    context = {
        "outbound_list": queryset[:100],
        "counts": counts,
        "status_filter": status_filter,
        "kind_filter": kind_filter,
        "approval_mode": get_approval_mode().value,
        "now": timezone.localtime(),
    }
    return render(request, "ekoalu/outbound_list.html", context)


@staff_member_required
def outbound_detail(request, pk: int):
    """Détail d'un message sortant + édition + actions."""
    outbound = get_object_or_404(PendingOutbound, pk=pk)

    if request.method == "POST":
        action = request.POST.get("action", "")
        final_content = request.POST.get("final_content", "").strip()

        if action == "approve":
            outbound.final_content = final_content
            outbound.status = OutboundStatus.APPROVED
            outbound.approved_at = timezone.now()
            outbound.save()
            django_messages.success(request, "Message approuvé. Il sera envoyé au prochain cycle.")
            return redirect("ekoalu:outbound_list")

        elif action == "reject":
            outbound.status = OutboundStatus.REJECTED
            outbound.rejection_reason = request.POST.get("rejection_reason", "")
            outbound.save()
            django_messages.warning(request, "Message refusé.")
            return redirect("ekoalu:outbound_list")

        elif action == "save_draft":
            outbound.final_content = final_content
            outbound.save()
            django_messages.info(request, "Brouillon sauvegardé (statut inchangé).")
            return redirect("ekoalu:outbound_detail", pk=pk)

        elif action == "mark_sent":
            outbound.status = OutboundStatus.SENT
            outbound.sent_at = timezone.now()
            outbound.save()
            django_messages.success(request, "Marqué comme envoyé manuellement.")
            return redirect("ekoalu:outbound_list")

    context = {
        "outbound": outbound,
        "linkedin_url": f"https://www.linkedin.com/in/{outbound.prospect_public_id}/",
        "is_pending": outbound.status == OutboundStatus.PENDING,
        "is_approved": outbound.status == OutboundStatus.APPROVED,
    }
    return render(request, "ekoalu/outbound_detail.html", context)
