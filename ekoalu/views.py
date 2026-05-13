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

    # ---- Activité récente daemon ----
    from linkedin.models import Task as DaemonTask
    last_completed = (
        DaemonTask.objects.filter(status="completed")
        .order_by("-completed_at").first()
    )
    last_failed = (
        DaemonTask.objects.filter(status="failed")
        .order_by("-completed_at").first()
    )

    # Tasks dans les 24h
    from datetime import timedelta
    since = timezone.now() - timedelta(hours=24)
    tasks_24h = {
        "completed": DaemonTask.objects.filter(status="completed", completed_at__gte=since).count(),
        "failed": DaemonTask.objects.filter(status="failed", completed_at__gte=since).count(),
        "by_type": list(
            DaemonTask.objects.filter(completed_at__gte=since)
            .values("task_type")
            .annotate(n=Count("id"))
        ),
    }

    # Entreprises pending
    from ekoalu.company_validation.models import ApprovedCompany, CompanyStatus
    companies_pending_count = ApprovedCompany.objects.filter(
        status=CompanyStatus.PENDING,
    ).count()

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
            "last_completed": last_completed,
            "last_failed": last_failed,
            "tasks_24h": tasks_24h,
        },
        "companies_pending_count": companies_pending_count,
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
def lead_detail(request, slug: str):
    """Detail d'un prospect : profil + scoring + timeline + actions (incl. G1+G2)."""
    from chat.models import ChatMessage
    from crm.models import Deal, Lead
    from django.contrib.contenttypes.models import ContentType
    from linkedin.models import Campaign, LinkedInProfile

    lead = get_object_or_404(Lead, public_identifier=slug)
    profile = LinkedInProfile.objects.filter(active=True).first()
    active_user = profile.user if profile else None

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "disqualify":
            lead.disqualified = True
            lead.save()
            django_messages.warning(request, "Prospect disqualifié (exclusion permanente).")
            return redirect("ekoalu:lead_detail", slug=slug)
        elif action == "requalify":
            lead.disqualified = False
            lead.save()
            django_messages.success(request, "Prospect requalifié.")
            return redirect("ekoalu:lead_detail", slug=slug)
        elif action == "reassign":
            # G1 : creer un Deal Qualified sur une campagne existante
            target_id = request.POST.get("target_campaign_id")
            target = Campaign.objects.filter(pk=target_id).first()
            if not target:
                django_messages.error(request, "Campagne cible introuvable.")
            elif Deal.objects.filter(lead=lead, campaign=target).exists():
                django_messages.warning(
                    request, f"Ce prospect a deja un Deal dans « {target.name} ».",
                )
            else:
                Deal.objects.create(
                    lead=lead, campaign=target, state="Qualified",
                    reason="Reaffecte manuellement par Richard depuis la fiche prospect.",
                )
                django_messages.success(
                    request,
                    f"Prospect réaffecté sur « {target.name} » (Deal Qualified créé).",
                )
            return redirect("ekoalu:lead_detail", slug=slug)
        elif action == "create_campaign_and_assign":
            # G2 : creer une nouvelle Campaign et y rattacher ce prospect
            name = (request.POST.get("name") or "").strip()
            objective = (request.POST.get("objective") or "").strip()
            product_docs = (request.POST.get("product_docs") or "").strip()
            if not (name and objective and product_docs):
                django_messages.error(request, "Tous les champs sont requis pour creer une campagne.")
                return redirect("ekoalu:lead_detail", slug=slug)
            full_name = name if name.startswith("EKOALU - ") else f"EKOALU - {name}"
            campaign, created = Campaign.objects.get_or_create(
                name=full_name,
                defaults={
                    "campaign_objective": objective,
                    "product_docs": product_docs,
                    "booking_link": conf.CALENDAR_BOOKING_URL,
                    "action_fraction": 1.0,
                    "is_freemium": False,
                },
            )
            if active_user:
                campaign.users.add(active_user)
            if not created:
                django_messages.warning(request, f"La campagne « {full_name} » existe déjà — réutilisée.")
            else:
                django_messages.success(request, f"Campagne « {full_name} » créée et activée.")
            if not Deal.objects.filter(lead=lead, campaign=campaign).exists():
                Deal.objects.create(
                    lead=lead, campaign=campaign, state="Qualified",
                    reason="Premier prospect de cette nouvelle campagne (créée depuis sa fiche).",
                )
                django_messages.success(request, "Prospect rattaché à cette nouvelle campagne.")
            return redirect("ekoalu:lead_detail", slug=slug)

    deals = Deal.objects.filter(lead=lead).select_related("campaign").order_by("-creation_date")

    # Conversations (ChatMessage via GenericForeignKey)
    lead_ct = ContentType.objects.get_for_model(Lead)
    chat_messages = ChatMessage.objects.filter(
        content_type=lead_ct, object_id=lead.pk,
    ).order_by("creation_date")[:50]

    # Outbound liés
    pending_outbound = PendingOutbound.objects.filter(
        prospect_public_id=slug,
    ).order_by("-created_at")[:20]

    # Compose la timeline
    timeline_events = []
    timeline_events.append({
        "kind": "lead_created",
        "label": "Prospect créé",
        "date": lead.creation_date,
    })
    for d in deals:
        timeline_events.append({
            "kind": "deal_state",
            "label": f"{d.campaign.name} → {d.state}",
            "date": d.update_date or d.creation_date,
            "deal": d,
        })
    for po in pending_outbound:
        timeline_events.append({
            "kind": "outbound",
            "label": f"{po.get_kind_display()} ({po.get_status_display()})",
            "date": po.created_at,
            "outbound": po,
        })
    for cm in chat_messages:
        timeline_events.append({
            "kind": "chat",
            "label": ("→ envoyé" if cm.is_outgoing else "← reçu"),
            "date": cm.creation_date,
            "chat": cm,
        })
    timeline_events.sort(key=lambda e: e["date"] or timezone.now(), reverse=True)

    # Campagnes disponibles pour reaffectation (toutes EKOALU - sauf celles ou le lead a deja un Deal)
    existing_campaign_ids = set(deals.values_list("campaign_id", flat=True))
    available_campaigns = Campaign.objects.filter(
        name__startswith="EKOALU - ",
    ).exclude(pk__in=existing_campaign_ids).order_by("name")

    return render(request, "ekoalu/lead_detail.html", {
        "lead": lead,
        "linkedin_url": f"https://www.linkedin.com/in/{slug}/",
        "deals": deals,
        "chat_messages": chat_messages,
        "pending_outbound": pending_outbound,
        "timeline_events": timeline_events[:30],
        "has_embedding": lead.embedding is not None,
        "available_campaigns": available_campaigns,
    })


@staff_member_required
def companies_list(request):
    """Vue agrégée par société (basée sur Deal.profile_summary)."""
    from crm.models import Deal

    # Extrait le nom de société depuis profile_summary (mem0 facts) si présent
    companies: dict[str, dict] = {}
    deals = Deal.objects.select_related("lead", "campaign").exclude(profile_summary=None)
    for deal in deals:
        company = _extract_company_from_summary(deal.profile_summary)
        if not company:
            continue
        key = company.lower().strip()
        if key not in companies:
            companies[key] = {
                "name": company,
                "deals": [],
                "states": {},
            }
        companies[key]["deals"].append(deal)
        companies[key]["states"][deal.state] = companies[key]["states"].get(deal.state, 0) + 1

    # Tri par nb de prospects desc
    companies_sorted = sorted(
        companies.values(), key=lambda c: len(c["deals"]), reverse=True,
    )

    return render(request, "ekoalu/companies_list.html", {
        "companies": companies_sorted,
        "total_companies": len(companies_sorted),
    })


def _extract_company_from_summary(profile_summary):
    """Extrait le nom de société depuis profile_summary (liste de facts mem0)."""
    if not profile_summary or not isinstance(profile_summary, list):
        return None
    for fact in profile_summary:
        if not isinstance(fact, dict):
            continue
        text = fact.get("memory") or fact.get("text") or fact.get("fact") or ""
        text_lower = text.lower()
        # Heuristique simple : facts qui contiennent "company:", "works at", "entreprise"
        if any(kw in text_lower for kw in ["company:", "works at ", "entreprise :", "société :"]):
            # Extraction du nom après le marqueur
            for sep in [":", " at ", "chez "]:
                if sep in text_lower:
                    return text.split(sep, 1)[1].strip().rstrip(".,;").split("\n")[0]
    return None


@staff_member_required
def inbox(request):
    """Vue inbox : conversations en cours + brouillons de réponse."""
    from chat.models import ChatMessage
    from crm.models import Lead
    from django.contrib.contenttypes.models import ContentType

    # Brouillons de réponse en attente (PendingReply)
    pending_replies = PendingReply.objects.filter(
        status=PendingReply.Status.PENDING,
    ).order_by("-created_at")[:20]

    # Conversations actives : derniers messages reçus (is_outgoing=False)
    lead_ct = ContentType.objects.get_for_model(Lead)
    recent_inbound = (
        ChatMessage.objects
        .filter(content_type=lead_ct, is_outgoing=False)
        .select_related()
        .order_by("-creation_date")[:30]
    )

    # Regroupe par lead pour avoir des "conversations"
    conversations = {}
    for msg in recent_inbound:
        lead_pk = msg.object_id
        if lead_pk not in conversations:
            try:
                lead = Lead.objects.get(pk=lead_pk)
            except Lead.DoesNotExist:
                continue
            conversations[lead_pk] = {
                "lead": lead,
                "last_message": msg,
                "messages_count": 0,
            }
        conversations[lead_pk]["messages_count"] += 1

    conversations_list = list(conversations.values())

    return render(request, "ekoalu/inbox.html", {
        "pending_replies": pending_replies,
        "pending_count": pending_replies.count(),
        "conversations": conversations_list,
    })


@staff_member_required
def companies_validation(request):
    """Liste des entreprises avec leur statut de validation + actions."""
    from ekoalu.company_validation.config import is_company_validation_enabled
    from ekoalu.company_validation.models import ApprovedCompany, CompanySource, CompanyStatus

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "add_manual":
            name = request.POST.get("name", "").strip()
            url = request.POST.get("linkedin_company_url", "").strip()
            status = request.POST.get("status", CompanyStatus.APPROVED)
            if name:
                obj, created = ApprovedCompany.objects.update_or_create(
                    name_normalized=ApprovedCompany.objects.model.objects.none().model._meta.get_field("name_normalized"),
                    name=name,
                    defaults={
                        "linkedin_company_url": url,
                        "source": CompanySource.MANUAL,
                        "status": status,
                        "decided_at": timezone.now() if status != CompanyStatus.PENDING else None,
                    },
                ) if False else (None, False)  # we'll re-do simpler below
                # Simpler create
                from ekoalu.company_validation.models import _normalize_company_name
                normalized = _normalize_company_name(name)
                obj, created = ApprovedCompany.objects.get_or_create(
                    name_normalized=normalized,
                    defaults={
                        "name": name,
                        "linkedin_company_url": url,
                        "source": CompanySource.MANUAL,
                        "status": status,
                        "decided_at": timezone.now() if status != CompanyStatus.PENDING else None,
                    },
                )
                if not created:
                    obj.status = status
                    obj.linkedin_company_url = url or obj.linkedin_company_url
                    if status != CompanyStatus.PENDING:
                        obj.decided_at = timezone.now()
                    obj.save()
                django_messages.success(request, f"'{name}' enregistrée comme {obj.get_status_display()}.")
            return redirect("ekoalu:companies_validation")

        elif action == "suggest_ai":
            from ekoalu.company_validation.suggester import (
                import_suggestions_into_db,
                suggest_companies,
            )
            try:
                n = int(request.POST.get("nb_suggestions", "10"))
            except ValueError:
                n = 10
            n = min(max(n, 1), 25)  # cap [1, 25]
            focus = request.POST.get("focus", "").strip()

            suggestions = suggest_companies(n=n, focus=focus)
            if not suggestions:
                django_messages.error(
                    request,
                    "Échec génération suggestions (clé API ? prompt ? erreur réseau ?). "
                    "Voir logs serveur.",
                )
            else:
                stats = import_suggestions_into_db(suggestions)
                django_messages.success(
                    request,
                    f"{stats['created']} entreprise(s) suggérée(s) ajoutée(s) en attente. "
                    f"{stats['skipped_existing']} déjà existante(s)."
                )
            return redirect("ekoalu:companies_validation")

        elif action in ("approve", "reject", "set_pending"):
            company_id = request.POST.get("company_id", "")
            if company_id:
                try:
                    obj = ApprovedCompany.objects.get(pk=int(company_id))
                except (ValueError, ApprovedCompany.DoesNotExist):
                    django_messages.error(request, "Entreprise introuvable.")
                    return redirect("ekoalu:companies_validation")

                new_status = {
                    "approve": CompanyStatus.APPROVED,
                    "reject": CompanyStatus.REJECTED,
                    "set_pending": CompanyStatus.PENDING,
                }[action]
                obj.status = new_status
                obj.decided_at = timezone.now() if new_status != CompanyStatus.PENDING else None
                obj.save()

                # Si on approuve : débloquer les PendingOutbound liés à cette entreprise
                if new_status == CompanyStatus.APPROVED:
                    from ekoalu.company_validation.models import _normalize_company_name
                    n = _normalize_company_name(obj.name)
                    # Match approximatif sur prospect_company
                    blocked = PendingOutbound.objects.filter(
                        status=OutboundStatus.BLOCKED_COMPANY,
                    )
                    debloque = 0
                    for po in blocked:
                        if _normalize_company_name(po.prospect_company or "") == n:
                            po.status = OutboundStatus.PENDING
                            po.save()
                            debloque += 1
                    django_messages.success(
                        request,
                        f"'{obj.name}' approuvée. {debloque} message(s) débloqué(s)."
                    )
                # Si on refuse : marquer comme REJECTED tous les pending de cette société
                elif new_status == CompanyStatus.REJECTED:
                    from ekoalu.company_validation.models import _normalize_company_name
                    n = _normalize_company_name(obj.name)
                    bloqued = 0
                    for po in PendingOutbound.objects.filter(
                        status__in=[OutboundStatus.PENDING, OutboundStatus.BLOCKED_COMPANY],
                    ):
                        if _normalize_company_name(po.prospect_company or "") == n:
                            po.status = OutboundStatus.REJECTED
                            po.rejection_reason = f"Entreprise refusée : {obj.name}"
                            po.save()
                            bloqued += 1
                    django_messages.warning(
                        request,
                        f"'{obj.name}' refusée. {bloqued} message(s) annulé(s)."
                    )
                else:
                    django_messages.info(request, f"'{obj.name}' remise en attente.")
            return redirect("ekoalu:companies_validation")

    # Liste par statut
    approved = ApprovedCompany.objects.filter(status=CompanyStatus.APPROVED).order_by("name")
    pending = ApprovedCompany.objects.filter(status=CompanyStatus.PENDING).order_by("-created_at")
    rejected = ApprovedCompany.objects.filter(status=CompanyStatus.REJECTED).order_by("name")

    # Stats PendingOutbound bloqués
    nb_blocked_by_company = PendingOutbound.objects.filter(
        status=OutboundStatus.BLOCKED_COMPANY,
    ).count()

    return render(request, "ekoalu/companies_validation.html", {
        "approved": approved,
        "pending": pending,
        "rejected": rejected,
        "nb_blocked": nb_blocked_by_company,
        "validation_enabled": is_company_validation_enabled(),
        "status_choices": CompanyStatus.choices,
    })


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
    """Liste des Campaigns EKOALU avec stats + actions activate/deactivate/create."""
    from crm.models import Deal
    from linkedin.models import Campaign, LinkedInProfile

    profile = LinkedInProfile.objects.filter(active=True).first()
    active_user = profile.user if profile else None

    # POST : actions de gestion
    if request.method == "POST" and active_user:
        action = request.POST.get("action", "")
        if action in ("activate", "deactivate"):
            cid = request.POST.get("campaign_id")
            campaign = Campaign.objects.filter(pk=cid).first()
            if campaign:
                if action == "activate":
                    campaign.users.add(active_user)
                    django_messages.success(request, f"Campagne « {campaign.name} » activée.")
                else:
                    campaign.users.remove(active_user)
                    django_messages.success(request, f"Campagne « {campaign.name} » mise en pause.")
            return redirect("ekoalu:campaigns_list")
        if action == "create":
            name = (request.POST.get("name") or "").strip()
            objective = (request.POST.get("objective") or "").strip()
            product_docs = (request.POST.get("product_docs") or "").strip()
            if not (name and objective and product_docs):
                django_messages.error(request, "Tous les champs sont requis.")
                return redirect("ekoalu:campaigns_list")
            full_name = name if name.startswith("EKOALU - ") else f"EKOALU - {name}"
            campaign, created = Campaign.objects.get_or_create(
                name=full_name,
                defaults={
                    "campaign_objective": objective,
                    "product_docs": product_docs,
                    "booking_link": conf.CALENDAR_BOOKING_URL,
                    "action_fraction": 1.0,
                    "is_freemium": False,
                },
            )
            if not created:
                django_messages.warning(request, f"Une campagne « {full_name} » existe déjà.")
            else:
                campaign.users.add(active_user)
                django_messages.success(request, f"Campagne « {full_name} » créée et activée.")
            return redirect("ekoalu:campaigns_list")

    only_ekoalu = request.GET.get("scope", "ekoalu") == "ekoalu"
    queryset = Campaign.objects.all()
    if only_ekoalu:
        queryset = queryset.filter(name__startswith="EKOALU - ")
    queryset = queryset.order_by("pk")

    active_campaign_ids = set()
    if active_user:
        active_campaign_ids = set(
            active_user.campaigns.values_list("pk", flat=True),
        )

    campaigns_data = []
    for campaign in queryset:
        deals = Deal.objects.filter(campaign=campaign)
        state_counts = {}
        for d in deals.values("state").annotate(n=Count("id")):
            state_counts[d["state"]] = d["n"]

        persona_slug = None
        for p in PERSONAS.values():
            if p.label in campaign.name:
                persona_slug = p.slug
                break

        pending_out = PendingOutbound.objects.filter(
            campaign_id=campaign.pk,
            status=OutboundStatus.PENDING,
        ).count()
        approved_out = PendingOutbound.objects.filter(
            campaign_id=campaign.pk,
            status=OutboundStatus.APPROVED,
        ).count()

        # G3 : metriques d'efficacite
        qualified = state_counts.get("Qualified", 0)
        ready = state_counts.get("Ready_to_connect", 0)
        pending = state_counts.get("Pending", 0)
        connected = state_counts.get("Connected", 0) + state_counts.get("Completed", 0)
        failed = state_counts.get("Failed", 0)
        unresponsive = Deal.objects.filter(
            campaign=campaign, state="Failed", outcome="unresponsive",
        ).count()
        wrong_fit = Deal.objects.filter(
            campaign=campaign, state="Failed", outcome="wrong_fit",
        ).count()
        invited_total = pending + connected + unresponsive
        accept_rate = (
            round(connected / invited_total * 100, 1)
            if invited_total > 0 else None
        )
        disqualif_rate = (
            round(wrong_fit / (qualified + failed) * 100, 1)
            if (qualified + failed) > 0 else None
        )

        campaigns_data.append({
            "campaign": campaign,
            "persona_slug": persona_slug,
            "states": {
                "qualified": qualified,
                "ready": ready,
                "pending": pending,
                "connected": state_counts.get("Connected", 0),
                "completed": state_counts.get("Completed", 0),
                "failed": failed,
            },
            "total": sum(state_counts.values()),
            "pending_out": pending_out,
            "approved_out": approved_out,
            "is_active": campaign.pk in active_campaign_ids and not campaign.is_freemium,
            "metrics": {
                "invited_total": invited_total,
                "accept_rate": accept_rate,
                "disqualif_rate": disqualif_rate,
            },
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

            # Si Richard a edite (final != draft), creer CorrectionExample pour apprentissage
            if final_content.strip() and final_content.strip() != outbound.ai_draft.strip():
                try:
                    from ekoalu.inbox_assist.models import CorrectionExample, PendingReply
                    # Creer un PendingReply minimal qui sert de container
                    pr = PendingReply.objects.create(
                        prospect_public_id=outbound.prospect_public_id,
                        campaign_id=outbound.campaign_id,
                        inbound_message="(invitation outbound)",
                        ai_draft=outbound.ai_draft,
                        final_sent=final_content.strip(),
                        status=PendingReply.Status.SENT,
                        sent_at=timezone.now(),
                    )
                    CorrectionExample.from_pending(pr, persona_slug="invitation")
                except Exception as e:
                    import logging
                    logging.exception("CorrectionExample creation failed: %s", e)

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


# ---- A : drill-down depuis le dashboard --------------------------------

_STATE_FILTER_MAP = {
    "qualified":    {"states": ["Qualified"],                     "title": "Qualifiés"},
    "ready":        {"states": ["Ready_to_connect"],              "title": "Prêts à inviter"},
    "pending":      {"states": ["Pending"],                       "title": "Invités (en attente)"},
    "connected":    {"states": ["Connected", "Completed"],        "title": "Connectés"},
    "disqualified": {"states": ["Failed"],                        "title": "Disqualifiés / Échec"},
}


@staff_member_required
def deals_filtered(request):
    """Liste Deals filtres par etat + actions requalify/confirm_reject sur disqualifies (F)."""
    from crm.models import Deal
    from ekoalu.qualification_feedback.models import QualificationFeedback

    if request.method == "POST":
        from linkedin.models import Campaign

        action = request.POST.get("action", "")
        deal_id = request.POST.get("deal_id")
        explanation = (request.POST.get("explanation") or "").strip()
        deal = Deal.objects.filter(pk=deal_id).select_related("lead", "campaign").first()
        if not deal:
            django_messages.error(request, "Deal introuvable.")
            return redirect(request.path + "?" + request.GET.urlencode())

        # Explanation : obligatoire pour requalify (Claude doit comprendre),
        # optionnelle pour confirm_reject (Claude a raison, OK rapide).
        if action == "requalify" and not explanation:
            django_messages.error(request, "Une explication est obligatoire pour requalifier (apprentissage Claude).")
            return redirect(request.path + "?" + request.GET.urlencode())

        if action == "requalify":
            target_id = request.POST.get("target_campaign_id") or str(deal.campaign_id)
            target = Campaign.objects.filter(pk=target_id).first()
            QualificationFeedback.objects.create(
                prospect_public_id=deal.lead.public_identifier,
                campaign_id=target.pk if target else deal.campaign_id,
                campaign_name=target.name if target else (deal.campaign.name if deal.campaign else ""),
                claude_reason=deal.reason or "",
                richard_explanation=explanation,
                kind=QualificationFeedback.Kind.REQUALIFY,
            )
            if target and target.pk != deal.campaign_id:
                # Reaffectation : on cree un Deal Qualified sur la campagne cible,
                # on laisse le Failed historique sur l'ancienne.
                existing = Deal.objects.filter(lead=deal.lead, campaign=target).first()
                if existing and existing.state != "Failed":
                    django_messages.warning(
                        request,
                        f"Le prospect a deja un Deal {existing.state} dans « {target.name} » — pas de double creation.",
                    )
                else:
                    if existing:
                        existing.state = "Qualified"
                        existing.outcome = ""
                        existing.reason = f"Reaffecte depuis « {deal.campaign.name} » : {explanation[:300]}"
                        existing.save()
                    else:
                        Deal.objects.create(
                            lead=deal.lead, campaign=target, state="Qualified",
                            reason=f"Reaffecte depuis « {deal.campaign.name} » : {explanation[:300]}",
                        )
                    django_messages.success(
                        request,
                        f"{deal.lead.public_identifier} reaffecte sur « {target.name} » en Qualified. "
                        "Claude apprendra de cette correction.",
                    )
            else:
                # Meme campagne : on requalifie le Deal courant.
                deal.state = "Qualified"
                deal.outcome = ""
                deal.save()
                django_messages.success(
                    request,
                    f"{deal.lead.public_identifier} remis en file Qualified sur la meme campagne. "
                    "Claude apprendra de cette correction.",
                )
        elif action == "confirm_reject":
            QualificationFeedback.objects.create(
                prospect_public_id=deal.lead.public_identifier,
                campaign_id=deal.campaign_id,
                campaign_name=deal.campaign.name if deal.campaign else "",
                claude_reason=deal.reason or "",
                richard_explanation=explanation,
                kind=QualificationFeedback.Kind.CONFIRM_REJECT,
            )
            django_messages.success(
                request,
                "Decision Claude confirmee, feedback enregistre pour apprentissage.",
            )
        return redirect(request.path + "?" + request.GET.urlencode())

    state = request.GET.get("state", "qualified")
    cfg = _STATE_FILTER_MAP.get(state, _STATE_FILTER_MAP["qualified"])

    qs = (
        Deal.objects.filter(
            campaign__name__startswith="EKOALU - ",
            state__in=cfg["states"],
        )
        .select_related("lead", "campaign")
        .order_by("-update_date")
    )

    feedback_slugs = set()
    handled_elsewhere_slugs = set()
    all_ekoalu_campaigns = []
    nb_reviewed = 0
    show_reviewed = request.GET.get("reviewed") == "1"
    if state == "disqualified":
        feedback_slugs = set(
            QualificationFeedback.objects
            .filter(prospect_public_id__in=qs.values_list("lead__public_identifier", flat=True))
            .values_list("prospect_public_id", flat=True)
        )
        # Prospects deja repris ailleurs : ont un Deal actif (non-Failed) dans
        # une autre campagne EKOALU → reaffectes via la fiche lead, plus a traiter.
        handled_elsewhere_slugs = set(
            Deal.objects
            .filter(
                lead__public_identifier__in=qs.values_list("lead__public_identifier", flat=True),
                campaign__name__startswith="EKOALU - ",
            )
            .exclude(state="Failed")
            .values_list("lead__public_identifier", flat=True)
        )
        reviewed_set = feedback_slugs | handled_elsewhere_slugs
        nb_reviewed = len(reviewed_set)
        # Par defaut on cache les traites pour focus sur ceux qui restent
        if not show_reviewed and reviewed_set:
            qs = qs.exclude(lead__public_identifier__in=reviewed_set)
        from linkedin.models import Campaign
        all_ekoalu_campaigns = list(
            Campaign.objects.filter(name__startswith="EKOALU - ").order_by("name")
        )

    context = {
        "state_filter": state,
        "title": cfg["title"],
        "deals": qs[:200],
        "total": qs.count(),
        "state_filter_map": _STATE_FILTER_MAP,
        "already_feedback_slugs": feedback_slugs,
        "handled_elsewhere_slugs": handled_elsewhere_slugs,
        "all_ekoalu_campaigns": all_ekoalu_campaigns,
        "nb_reviewed": nb_reviewed,
        "show_reviewed": show_reviewed,
    }
    return render(request, "ekoalu/deals_filtered.html", context)


# ---- D : page detail consommation Claude API --------------------------

@staff_member_required
def usage(request):
    """Page detail consommation Claude API (tokens + cout)."""
    from datetime import datetime, time as dtime, timedelta

    from django.db.models import Count, Sum

    from ekoalu.llm_usage.models import ClaudeUsageLog

    now = timezone.localtime()
    start_of_day = timezone.make_aware(
        datetime.combine(now.date(), dtime.min),
        timezone.get_current_timezone(),
    )
    start_of_month = timezone.make_aware(
        datetime.combine(now.date().replace(day=1), dtime.min),
        timezone.get_current_timezone(),
    )
    last_30 = now - timedelta(days=30)

    today_rows = (
        ClaudeUsageLog.objects.filter(timestamp__gte=start_of_day)
        .values("model", "context")
        .annotate(
            n=Count("id"),
            in_t=Sum("input_tokens"),
            out_t=Sum("output_tokens"),
            cost=Sum("cost_usd"),
        )
        .order_by("-cost")
    )
    month_rows = (
        ClaudeUsageLog.objects.filter(timestamp__gte=start_of_month)
        .values("model", "context")
        .annotate(
            n=Count("id"),
            in_t=Sum("input_tokens"),
            out_t=Sum("output_tokens"),
            cost=Sum("cost_usd"),
        )
        .order_by("-cost")
    )

    daily = (
        ClaudeUsageLog.objects.filter(timestamp__gte=last_30)
        .extra(select={"day": "DATE(timestamp)"})
        .values("day")
        .annotate(
            n=Count("id"),
            in_t=Sum("input_tokens"),
            out_t=Sum("output_tokens"),
            cost=Sum("cost_usd"),
        )
        .order_by("-day")
    )

    total_all = ClaudeUsageLog.objects.aggregate(
        s=Sum("cost_usd"), n=Count("id"),
    )
    recent = ClaudeUsageLog.objects.order_by("-timestamp")[:30]

    return render(request, "ekoalu/usage.html", {
        "today_rows": list(today_rows),
        "month_rows": list(month_rows),
        "daily": list(daily),
        "total_all": total_all,
        "recent": recent,
    })
