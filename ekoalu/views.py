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
from ekoalu.dedup.consolidator import SHADOW_OUTCOMES
from ekoalu.inbox_assist.models import CorrectionExample, PendingReply
from ekoalu.outbound_validation.config import get_approval_mode
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound
from ekoalu.personas import PERSONAS


def _build_prospect_card(slug: str, deal=None, company_hint: str = "") -> dict:
    """Fiche prospect complète (demande Richard 12/06) — partagée entre le
    détail message et la fiche prospect.

    Combine resolve_prospect_display (heuristiques LinkedIn) avec les données
    structurées EmailLeadData (leads BDD PROSPECT) : nom du dirigeant, email,
    ville, NAF, effectif, SIREN, source.
    """
    from crm.models import Lead
    from ekoalu.prospect_display import resolve_prospect_display

    display = resolve_prospect_display(slug, deal=deal, company_hint=company_hint)
    lead = Lead.objects.filter(public_identifier=slug).select_related("email_data").first()
    email_data = getattr(lead, "email_data", None) if lead else None
    if email_data:
        if email_data.dirigeant:
            display["name"] = email_data.dirigeant
        display["company"] = email_data.entreprise or display.get("company", "")
        loc_bits = [b for b in (email_data.ville, email_data.cp) if b]
        if loc_bits:
            display["location"] = " ".join(loc_bits) + (
                f" ({email_data.dpt})" if email_data.dpt else ""
            )
        if not display.get("job_title"):
            display["job_title"] = "Dirigeant" if email_data.dirigeant else ""

    facts = {
        "email": getattr(lead, "contact_email", "") or "",
        "email_bounced": bool(lead and lead.email_bounced_at),
        "unsubscribed": bool(lead and lead.unsubscribed_at),
        "activite": getattr(email_data, "activite", "") or "",
        "code_naf": getattr(email_data, "code_naf", "") or "",
        "siren": getattr(email_data, "siren", "") or "",
        "effectif": (
            f"{email_data.effectif_min}-{email_data.effectif_max} salariés"
            if email_data and email_data.effectif_max else ""
        ),
        "source": (
            "BDD PROSPECT" if (email_data and email_data.source == "bdd_prospect")
            else ("Saisie manuelle" if email_data and email_data.source == "manual" else "LinkedIn")
        ),
    }
    # Vrai lien LinkedIn vs slug synthétique (bdd-prospect- = BDD PROSPECT,
    # mailjet-hot- = leads Mailjet, URL en *.local). On n'affiche le lien que
    # s'il pointe vers un vrai profil linkedin.com/in/ (demande Richard 15/06,
    # capture #472 montrait un faux lien LinkedIn sur un lead mail-only Mailjet).
    synthetic = slug.startswith("bdd-prospect-") or slug.startswith("mailjet-hot-")
    real_url = (getattr(lead, "linkedin_url", "") or "") if lead else ""
    has_real_linkedin = "linkedin.com/in/" in real_url
    if has_real_linkedin:
        linkedin_url = real_url
    elif not synthetic:
        linkedin_url = f"https://www.linkedin.com/in/{slug}/"
    else:
        linkedin_url = ""
    return {
        "display": display,
        "facts": facts,
        "lead": lead,
        "is_mail_only": synthetic and not has_real_linkedin,
        "linkedin_url": linkedin_url,
    }


def _campaign_funnel(campaign) -> dict:
    """Funnel commercial cohérent d'une campagne — partagé dashboard + page campagnes.

    Lecture responsable ventes, et arithmétique vérifiable :
    total = a_contacter + invites + connectes + hors_cible + echecs.

    - a_contacter : Qualified + Ready_to_connect (stock en file)
    - invites     : Pending (invitation envoyée, en attente de réponse)
    - connectes   : Connected + Completed (invitation ACCEPTÉE — même définition
                    que le taux d'acceptation, qui était calculé sur Connected+Completed
                    alors que la colonne n'affichait que Connected)
    - hors_cible  : Failed/wrong_fit (rejet qualifier — jamais contactés)
    - echecs      : Failed restants (sans réponse, erreurs d'envoi…)
    - accept_rate : connectes / (invites + connectes + sans-réponse)
    """
    from crm.models import Deal

    rows = (
        Deal.objects.filter(campaign=campaign)
        .exclude(outcome__in=SHADOW_OUTCOMES)
        .values("state", "outcome")
        .annotate(n=Count("id"))
    )
    by_state: dict[str, int] = defaultdict(int)
    wrong_fit = 0
    unresponsive = 0
    for r in rows:
        by_state[r["state"]] += r["n"]
        if r["state"] == "Failed" and r["outcome"] == "wrong_fit":
            wrong_fit += r["n"]
        if r["state"] == "Failed" and r["outcome"] == "unresponsive":
            unresponsive += r["n"]

    a_contacter = by_state["Qualified"] + by_state["Ready_to_connect"]
    invites = by_state["Pending"]
    connectes = by_state["Connected"] + by_state["Completed"]
    echecs = by_state["Failed"] - wrong_fit
    total = sum(by_state.values())

    invited_total = invites + connectes + unresponsive
    accept_rate = (
        round(connectes / invited_total * 100, 1) if invited_total > 0 else None
    )

    return {
        "a_contacter": a_contacter,
        "invites": invites,
        "connectes": connectes,
        "hors_cible": wrong_fit,
        "echecs": echecs,
        "total": total,
        "invited_total": invited_total,
        "accept_rate": accept_rate,
    }


def _search_efficiency_data(days: int = 10) -> dict:
    """Efficacité du tri sur `days` jours : fiches lues vs profils sélectionnés.

    - lectures : ProfileReadDay (compteur read_guard, = « recherches » au sens
      fiches LinkedIn consultées)
    - sélectionnés : Deals créés ce jour-là par le qualifier en Qualified
      (state != Failed, shadows exclus)
    - rejetés : Deals créés ce jour-là en Failed/wrong_fit
    - taux : sélectionnés / lectures du jour

    Fenêtre plancher au 10/06/2026 : le compteur de lectures (read_guard)
    n'existe que depuis cette date — avant, lectures=0 et des centaines de
    rejets/jour (ère pré-scoping) écrasaient l'échelle et faussaient le taux
    (demande Richard 12/06).
    """
    import json
    from datetime import date, timedelta

    from crm.models import Deal
    from ekoalu.read_guard.models import ProfileReadDay

    floor = date(2026, 6, 10)
    today = timezone.localdate()
    day_list = [
        d for d in (today - timedelta(days=i) for i in range(days - 1, -1, -1))
        if d >= floor
    ] or [today]
    start = timezone.make_aware(
        timezone.datetime.combine(day_list[0], timezone.datetime.min.time()),
    )

    # Décomposition de TOUTES les interactions sur une fiche LinkedIn (demande
    # Richard 15/06 : « compter tout », car LinkedIn voit aussi les visites de
    # page, pas seulement nos fetches API). 4 catégories :
    #   - selection : fetch Voyager d'enrichissement/verdict d'un NOUVEAU candidat
    #   - follow_up : fetch Voyager de relance (lead déjà connecté)
    #   - degré     : fetch get_connection_degree (contrôle avant invitation)
    #   - visite    : navigation réelle sur la page /in/ (visit_profile) — vue
    #                 que LinkedIn enregistre, jusqu'ici NON comptée
    # DÉNOMINATEUR du taux d'efficacité = "selection" seulement.
    # Jours hérités (avant le 15/06, pas de ventilation) → tout get_profile = sélection.
    reads_sel_by_day: dict = {}
    reads_fu_by_day: dict = {}
    reads_degree_by_day: dict = {}
    reads_visit_by_day: dict = {}
    total_by_day: dict = {}
    for r in ProfileReadDay.objects.filter(date__gte=day_list[0]):
        src = r.sources or {}
        degree = src.get("get_connection_degree", 0)
        visit = src.get("visit_profile", 0)
        has_usage = ("selection" in src) or ("follow_up" in src)
        if has_usage:
            sel = src.get("selection", 0) + src.get("get_profile", 0)
            fu = src.get("follow_up", 0)
        else:
            # héritage : tout sauf degré/visite = sélection
            sel = max(r.count - degree - visit, 0)
            fu = 0
        reads_sel_by_day[r.date] = sel
        reads_fu_by_day[r.date] = fu
        reads_degree_by_day[r.date] = degree
        reads_visit_by_day[r.date] = visit
        total_by_day[r.date] = r.count  # compteur atomique = total interactions

    selected_by_day: dict = defaultdict(int)
    rejected_by_day: dict = defaultdict(int)
    rows = (
        Deal.objects.filter(creation_date__gte=start)
        .exclude(outcome__in=SHADOW_OUTCOMES)
        .values_list("creation_date", "state", "outcome")
    )
    for created, state, outcome in rows:
        d = timezone.localtime(created).date()
        if state == "Failed" and outcome == "wrong_fit":
            rejected_by_day[d] += 1
        elif state != "Failed":
            selected_by_day[d] += 1

    reads_sel = [reads_sel_by_day.get(d, 0) for d in day_list]
    reads_fu = [reads_fu_by_day.get(d, 0) for d in day_list]
    reads_degree = [reads_degree_by_day.get(d, 0) for d in day_list]
    reads_visit = [reads_visit_by_day.get(d, 0) for d in day_list]
    # Total interactions = compteur atomique du jour (fallback = somme des 4 cats)
    interactions = [
        total_by_day.get(d, s + f + dg + v)
        for d, s, f, dg, v in zip(day_list, reads_sel, reads_fu, reads_degree, reads_visit)
    ]
    selected = [selected_by_day.get(d, 0) for d in day_list]
    rejected = [rejected_by_day.get(d, 0) for d in day_list]
    # Taux d'EFFICACITÉ = sélectionnés / lectures de SÉLECTION (pas le total).
    rates = [
        round(s / r * 100, 1) if r else None
        for s, r in zip(selected, reads_sel)
    ]
    # Part RECHERCHE-SÉLECTION = lectures sélection / total interactions (quelle
    # fraction de l'activité LinkedIn sert vraiment à chercher des candidats).
    research_share = [
        round(rs / it * 100, 1) if it else None
        for rs, it in zip(reads_sel, interactions)
    ]

    total_interactions = sum(interactions)
    total_sel_reads = sum(reads_sel)
    total_fu_reads = sum(reads_fu)
    total_degree = sum(reads_degree)
    total_visit = sum(reads_visit)
    total_selected = sum(selected)

    # Ventilation des lectures du jour par usage (réponse à « où sont les
    # manquants ? » : lectures techniques ≠ verdicts de tri).
    today_row = ProfileReadDay.objects.filter(date=today).first()
    src = (today_row.sources or {}) if today_row else {}
    _SRC_LABELS = {
        "selection": "sélection de candidats (fetch enrichissement/tri)",
        "follow_up": "relances (leads déjà connectés)",
        "visit_profile": "visites de page (avant invitation / inspection)",
        "get_profile": "fiches non catégorisées",
        "get_connection_degree": "contrôles de degré (avant invitation)",
        "autre": "autres",
    }
    today_sources = [
        (_SRC_LABELS.get(k, k), v) for k, v in sorted(src.items(), key=lambda x: -x[1])
    ]

    return {
        "labels": json.dumps([d.strftime("%d/%m") for d in day_list]),
        "interactions": json.dumps(interactions),
        "reads_selection": json.dumps(reads_sel),
        "reads_followup": json.dumps(reads_fu),
        "reads_degree": json.dumps(reads_degree),
        "reads_visit": json.dumps(reads_visit),
        "selected": json.dumps(selected),
        "rejected": json.dumps(rejected),
        "rates": json.dumps(rates),
        "research_share": json.dumps(research_share),
        "totals": {
            "interactions": total_interactions,
            "selection_reads": total_sel_reads,
            "followup_reads": total_fu_reads,
            "degree_reads": total_degree,
            "visit_reads": total_visit,
            "selected": total_selected,
            "rejected": sum(rejected),
            # Taux d'efficacité = sélectionnés / lectures de sélection
            "rate": round(total_selected / total_sel_reads * 100, 1) if total_sel_reads else None,
            # Part recherche-sélection = lectures sélection / total interactions
            "research_share": round(total_sel_reads / total_interactions * 100, 1) if total_interactions else None,
            "today_rate": rates[-1] if rates else None,
            "today_research_share": research_share[-1] if research_share else None,
            "since": day_list[0].strftime("%d/%m"),
            "today_interactions": interactions[-1] if interactions else 0,
            "today_selection_reads": reads_sel[-1] if reads_sel else 0,
            "today_verdicts": (selected[-1] + rejected[-1]) if selected else 0,
        },
        "today_sources": today_sources,
    }


def _activity_chart_data(days: int = 10) -> dict:
    """Séries jour par jour pour le graphique d'activité du dashboard.

    4 séries sur `days` jours glissants (aujourd'hui inclus) :
    emails envoyés, invitations LinkedIn envoyées, messages LinkedIn envoyés
    (follow-up + reply), invitations acceptées (Deal.connected_at).
    """
    import json
    from datetime import timedelta

    from crm.models import Deal

    today = timezone.localdate()
    day_list = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    start = timezone.make_aware(
        timezone.datetime.combine(day_list[0], timezone.datetime.min.time()),
    )

    def _series_by_day(qs, dt_field: str) -> dict:
        counts = defaultdict(int)
        for dt in qs.values_list(dt_field, flat=True):
            counts[timezone.localtime(dt).date()] += 1
        return counts

    sent = PendingOutbound.objects.filter(
        status=OutboundStatus.SENT, sent_at__gte=start,
    )
    emails = _series_by_day(
        sent.filter(kind__in=[OutboundKind.EMAIL_COLD, OutboundKind.EMAIL_FOLLOW_UP]),
        "sent_at",
    )
    invitations = _series_by_day(sent.filter(kind=OutboundKind.INVITATION), "sent_at")
    lk_messages = _series_by_day(
        sent.filter(kind__in=[OutboundKind.FOLLOW_UP, OutboundKind.REPLY]), "sent_at",
    )
    accepted = _series_by_day(
        Deal.objects.filter(connected_at__gte=start)
        .exclude(outcome__in=SHADOW_OUTCOMES),
        "connected_at",
    )

    return {
        "labels": json.dumps([d.strftime("%d/%m") for d in day_list]),
        "emails": json.dumps([emails.get(d, 0) for d in day_list]),
        "invitations": json.dumps([invitations.get(d, 0) for d in day_list]),
        "lk_messages": json.dumps([lk_messages.get(d, 0) for d in day_list]),
        "accepted": json.dumps([accepted.get(d, 0) for d in day_list]),
        "totals": {
            "emails": sum(emails.values()),
            "invitations": sum(invitations.values()),
            "lk_messages": sum(lk_messages.values()),
            "accepted": sum(accepted.values()),
        },
    }


@staff_member_required
def dashboard(request):
    """Vue dashboard principal."""
    from chat.models import ChatMessage
    from crm.models import Deal, Lead
    from linkedin.models import Campaign, LinkedInProfile, SiteConfig, Task

    # ---- Infrastructure ----
    profile = LinkedInProfile.objects.filter(active=True).first()
    site_cfg = SiteConfig.load()

    # ---- Campaigns EKOALU avec stats (funnel partagé avec la page campagnes) ----
    campaigns_data = []
    for campaign in Campaign.objects.filter(name__startswith="EKOALU - ").order_by("pk"):
        # Identifier le persona depuis le label de la campagne
        persona_slug = None
        for p in PERSONAS.values():
            if p.label in campaign.name:
                persona_slug = p.slug
                break

        funnel = _campaign_funnel(campaign)
        # Tableau dashboard = vue synthèse : on masque les campagnes vides
        # (42 campagnes ABM dont beaucoup à 0 → bruit pur).
        if funnel["total"] == 0:
            continue

        campaigns_data.append({
            "campaign": campaign,
            "persona_slug": persona_slug,
            "funnel": funnel,
        })

    # Campagnes les plus actives en premier (connectés, puis invités, puis stock)
    campaigns_data.sort(
        key=lambda c: (
            c["funnel"]["connectes"], c["funnel"]["invites"], c["funnel"]["a_contacter"],
        ),
        reverse=True,
    )

    # ---- KPI globaux ----
    # Exclusion globale des Deals shadows (duplicate_campaign / pre_existing_relation)
    # → ils existent en base pour audit mais n'apparaissent jamais dans les compteurs.
    all_deals = (
        Deal.objects.filter(campaign__name__startswith="EKOALU - ")
        .exclude(outcome__in=SHADOW_OUTCOMES)
    )
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
    failed_outbound_count = PendingOutbound.objects.filter(
        status=OutboundStatus.FAILED,
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

    # ---- Graphiques 10 derniers jours (activité + efficacité du tri) ----
    activity_chart = _activity_chart_data(days=10)
    efficiency_chart = _search_efficiency_data(days=10)

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
        "failed_outbound_count": failed_outbound_count,
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
        "activity_chart": activity_chart,
        "efficiency_chart": efficiency_chart,
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
        elif action == "unsubscribe_email":
            # Désinscription RGPD canal email — empêche tout envoi futur
            # (cold mail et reply). Conserve toute la trace en DB pour audit.
            if lead.unsubscribed_at is None:
                lead.unsubscribed_at = timezone.now()
                lead.save(update_fields=["unsubscribed_at"])
                django_messages.warning(
                    request,
                    "Lead désinscrit du canal email (RGPD). Aucun envoi futur.",
                )
            else:
                django_messages.info(
                    request,
                    f"Lead déjà désinscrit depuis {lead.unsubscribed_at:%d/%m/%Y %H:%M}.",
                )
            return redirect("ekoalu:lead_detail", slug=slug)
        elif action == "resubscribe_email":
            # Annule la désinscription (cas erreur Richard / changement d'avis prospect)
            if lead.unsubscribed_at is not None:
                lead.unsubscribed_at = None
                lead.save(update_fields=["unsubscribed_at"])
                django_messages.success(
                    request, "Lead réabonné au canal email (envois autorisés).",
                )
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

    primary_deal = deals.first() if deals else None
    card = _build_prospect_card(slug, deal=primary_deal)

    # Lien retour vers le message en attente de validation (demande Richard
    # 12/06 : « pouvoir revenir en arrière, je viens du mail cold à valider »)
    po_to_validate = (
        PendingOutbound.objects.filter(
            prospect_public_id=slug, status=OutboundStatus.PENDING,
        ).order_by("-created_at").first()
    )

    return render(request, "ekoalu/lead_detail.html", {
        "lead": lead,
        "linkedin_url": card["linkedin_url"],
        "deals": deals,
        "chat_messages": chat_messages,
        "pending_outbound": pending_outbound,
        "timeline_events": timeline_events[:30],
        "has_embedding": lead.embedding is not None,
        "available_campaigns": available_campaigns,
        "prospect_display": card["display"],
        "prospect_facts": card["facts"],
        "is_mail_only": card["is_mail_only"],
        "po_to_validate": po_to_validate,
    })


def _parse_bulk_ids(request) -> list[int]:
    """Recupere et parse selected_ids depuis le POST (CSV ou liste)."""
    raw = request.POST.get("selected_ids", "") or ""
    parts = [p.strip() for p in raw.replace(";", ",").split(",")]
    out = []
    for p in parts:
        if not p:
            continue
        try:
            out.append(int(p))
        except ValueError:
            continue
    return out


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

    # Pipeline canal email : APPROVED + FAILED (en plus des PENDING ci-dessus)
    email_in_flight = PendingReply.objects.filter(
        channel=PendingReply.CHANNEL_EMAIL,
        status__in=[PendingReply.Status.APPROVED, PendingReply.Status.FAILED],
    ).order_by("-created_at")[:10]

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
        "email_in_flight": email_in_flight,
        "conversations": conversations_list,
    })


@staff_member_required
@require_POST
def email_reply_action(request, pk: int):
    """Actions sur un PendingReply EMAIL : approve / discard / save_draft.

    Restreint à `channel=email` — pour les PR LinkedIn, Richard utilise
    encore l'admin Django (envoi manuel hors pipeline).
    """
    pr = get_object_or_404(PendingReply, pk=pk)
    if pr.channel != PendingReply.CHANNEL_EMAIL:
        django_messages.error(request, "Action réservée aux brouillons email.")
        return redirect("ekoalu:inbox")

    action = request.POST.get("action", "")
    final_sent = request.POST.get("final_sent", "").strip()

    if action == "approve":
        pr.final_sent = final_sent  # peut être vide → reply_sender prendra ai_draft
        pr.status = PendingReply.Status.APPROVED
        pr.error_message = ""
        pr.save(update_fields=["final_sent", "status", "error_message"])

        # Apprentissage : si Richard a édité (final != draft), capture l'exemple
        # pour few-shot dans les prochaines générations (groupé par intent).
        learn_note = request.POST.get("learn_note", "").strip()
        if final_sent.strip() and final_sent.strip() != pr.ai_draft.strip():
            try:
                CorrectionExample.from_pending(
                    pr,
                    persona_slug=f"email_reply_{pr.intent}",
                    explanation=learn_note,
                )
            except Exception:  # noqa: BLE001 — apprentissage best-effort
                import logging
                logging.exception("CorrectionExample (email reply) creation failed")

        django_messages.success(
            request,
            f"Réponse #{pr.pk} approuvée — sera envoyée au prochain "
            f"`send_approved_email_replies`.",
        )
    elif action == "discard":
        pr.status = PendingReply.Status.DISCARDED
        pr.save(update_fields=["status"])
        django_messages.warning(request, f"Réponse #{pr.pk} abandonnée.")
    elif action == "save_draft":
        pr.final_sent = final_sent
        pr.save(update_fields=["final_sent"])
        django_messages.info(request, f"Brouillon #{pr.pk} sauvegardé.")
    else:
        django_messages.error(request, f"Action inconnue : {action!r}.")

    return redirect("ekoalu:inbox")


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

        elif action in ("bulk_approve_companies", "bulk_reject_companies"):
            from ekoalu.company_validation.models import _normalize_company_name
            ids = _parse_bulk_ids(request)
            if not ids:
                django_messages.warning(request, "Aucune entreprise sélectionnée.")
                return redirect("ekoalu:companies_validation")
            new_status = (
                CompanyStatus.APPROVED if action == "bulk_approve_companies"
                else CompanyStatus.REJECTED
            )
            companies = ApprovedCompany.objects.filter(pk__in=ids)
            n_companies = 0
            n_unblocked = 0
            for c in companies:
                c.status = new_status
                c.decided_at = timezone.now()
                c.save()
                n_companies += 1
                normalized = _normalize_company_name(c.name)
                if new_status == CompanyStatus.APPROVED:
                    blocked = PendingOutbound.objects.filter(status=OutboundStatus.BLOCKED_COMPANY)
                    for po in blocked:
                        if _normalize_company_name(po.prospect_company or "") == normalized:
                            po.status = OutboundStatus.PENDING
                            po.save()
                            n_unblocked += 1
                else:
                    for po in PendingOutbound.objects.filter(
                        status__in=[OutboundStatus.PENDING, OutboundStatus.BLOCKED_COMPANY],
                    ):
                        if _normalize_company_name(po.prospect_company or "") == normalized:
                            po.status = OutboundStatus.REJECTED
                            po.rejection_reason = f"Entreprise refusée (masse) : {c.name}"
                            po.save()
            if new_status == CompanyStatus.APPROVED:
                django_messages.success(
                    request,
                    f"✓ {n_companies} entreprise(s) approuvée(s). "
                    f"{n_unblocked} message(s) débloqué(s).",
                )
            else:
                django_messages.warning(request, f"✗ {n_companies} entreprise(s) refusée(s).")
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
                    "Échec génération suggestions Claude (JSON invalide / réponse vide / erreur API). "
                    "Voir logs serveur — possible cause : reponse tronquee à max_tokens. "
                    "Ré-essaye avec un nombre plus petit.",
                )
            else:
                stats = import_suggestions_into_db(suggestions)
                if stats["created"] == 0 and stats["skipped_existing"] > 0:
                    django_messages.warning(
                        request,
                        f"Claude a proposé {len(suggestions)} entreprise(s), mais TOUTES "
                        f"étaient déjà en base (approuvée/refusée/pending). "
                        f"Essaye un focus différent (ex: 'Rhône-Alpes hors 69', "
                        f"'designer espace tertiaire', 'BET acoustique') pour explorer de nouveaux secteurs.",
                    )
                else:
                    django_messages.success(
                        request,
                        f"✅ {stats['created']} nouvelle(s) suggestion(s) ajoutée(s) en PENDING — "
                        f"voir la section « ⏳ En attente de validation » ci-dessous. "
                        f"{stats['skipped_existing']} déjà connue(s), ignorée(s).",
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

        elif action == "create_abm":
            # Cree une Campaign ABM ciblee sur l'entreprise approuvee
            from ekoalu.company_validation.abm import AbmCampaignLink
            from linkedin.models import Campaign, LinkedInProfile

            company_id = request.POST.get("company_id")
            company = ApprovedCompany.objects.filter(pk=company_id).first()
            if not company or company.status != CompanyStatus.APPROVED:
                django_messages.error(request, "Entreprise introuvable ou non-approuvee.")
                return redirect("ekoalu:companies_validation")
            if hasattr(company, "abm_campaigns") and company.abm_campaigns.exists():
                existing_link = company.abm_campaigns.first()
                django_messages.warning(
                    request,
                    f"Une campagne ABM existe deja pour {company.name} (#{existing_link.campaign_id}).",
                )
                return redirect("ekoalu:companies_validation")

            full_name = f"EKOALU - ABM - {company.name}"
            campaign, created = Campaign.objects.get_or_create(
                name=full_name,
                defaults={
                    "campaign_objective": (
                        f"Account-Based Marketing sur {company.name}. "
                        f"Identifier tous les decideurs et influenceurs internes "
                        f"(dirigeants, BE, economiste, chefs de chantier, architectes internes, RH...) "
                        f"pour trouver une porte d'entree. Objectif : RDV visio qualifie."
                    ),
                    "product_docs": (
                        "EKOALU = menuiserie aluminium Chasselay (69), specialiste tertiaire "
                        "(coupe-feu EI60/EI120, desenfumage, pare-balles, grandes dim, acoustique Rw>40). "
                        "Atelier integre, multi-gammes (Cortizo, Sepalumic, SAPA, Wicona)."
                    ),
                    "booking_link": conf.CALENDAR_BOOKING_URL,
                    "action_fraction": 1.0,
                    "is_freemium": False,
                },
            )
            AbmCampaignLink.objects.get_or_create(
                campaign=campaign,
                defaults={"target_company": company},
            )
            profile = LinkedInProfile.objects.filter(active=True).first()
            if profile:
                campaign.users.add(profile.user)
            django_messages.success(
                request,
                f"Campagne ABM creee pour {company.name} et activee. "
                "Phase 2 (sourcing LinkedIn cible par entreprise) arrive bientot.",
            )
            return redirect("ekoalu:companies_validation")

    # Liste par statut
    approved = ApprovedCompany.objects.filter(status=CompanyStatus.APPROVED).order_by("name")
    pending = list(
        ApprovedCompany.objects.filter(status=CompanyStatus.PENDING).order_by("-created_at"),
    )
    rejected = ApprovedCompany.objects.filter(status=CompanyStatus.REJECTED).order_by("name")
    # Marque les entreprises creees dans la derniere heure (= session courante)
    from datetime import timedelta
    fresh_cutoff = timezone.now() - timedelta(hours=1)
    for c in pending:
        c.is_fresh = c.created_at >= fresh_cutoff

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

    from ekoalu.company_validation.abm import AbmCampaignLink
    abm_by_campaign = {
        link.campaign_id: link.target_company
        for link in AbmCampaignLink.objects.select_related("target_company").all()
    }

    campaigns_data = []
    for campaign in queryset:
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

        campaigns_data.append({
            "campaign": campaign,
            "persona_slug": persona_slug,
            "abm_company": abm_by_campaign.get(campaign.pk),
            "funnel": _campaign_funnel(campaign),
            "pending_out": pending_out,
            "approved_out": approved_out,
            "is_active": campaign.pk in active_campaign_ids and not campaign.is_freemium,
        })

    # Lecture responsable ventes : actives d'abord, puis celles qui produisent
    # (connectés, invités, stock), les campagnes vides en queue.
    campaigns_data.sort(
        key=lambda c: (
            c["is_active"],
            c["funnel"]["connectes"],
            c["funnel"]["invites"],
            c["funnel"]["a_contacter"],
            c["funnel"]["total"],
        ),
        reverse=True,
    )

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
    """Liste des messages sortants à valider + actions en masse."""
    from crm.models import Deal
    from ekoalu.prospect_display import resolve_prospect_display

    if request.method == "POST":
        bulk_action = request.POST.get("bulk_action", "")
        ids = _parse_bulk_ids(request)
        if not ids:
            django_messages.warning(request, "Aucun message sélectionné.")
            return redirect(request.path + "?" + request.GET.urlencode())
        qs = PendingOutbound.objects.filter(pk__in=ids)
        n = 0
        if bulk_action == "bulk_approve":
            for po in qs.filter(status=OutboundStatus.PENDING):
                po.status = OutboundStatus.APPROVED
                po.approved_at = timezone.now()
                # final_content reste vide -> ai_draft sera envoye tel quel
                po.save()
                n += 1
            django_messages.success(request, f"✓ {n} message(s) approuvé(s) — partira au prochain cycle daemon.")
        elif bulk_action == "bulk_reject":
            reason = request.POST.get("bulk_reason", "").strip() or "(rejet en masse)"
            qs_active = qs.exclude(status__in=[OutboundStatus.SENT, OutboundStatus.REJECTED])
            public_ids = list(qs_active.values_list("prospect_public_id", flat=True).distinct())
            n = qs_active.update(
                status=OutboundStatus.REJECTED,
                rejection_reason=reason,
            )
            n_leads, _ = _disqualify_leads_from_reject(public_ids, reason)
            django_messages.warning(
                request,
                f"✗ {n} message(s) refusé(s) — {n_leads} prospect(s) retiré(s) du pipeline.",
            )
        elif bulk_action == "bulk_mark_sent":
            n = qs.filter(status=OutboundStatus.APPROVED).update(
                status=OutboundStatus.SENT,
                sent_at=timezone.now(),
            )
            django_messages.success(request, f"{n} message(s) marqué(s) envoyés manuellement.")
        elif bulk_action == "bulk_requeue":
            n = qs.filter(status=OutboundStatus.FAILED).update(
                status=OutboundStatus.APPROVED,
                error_message="",
            )
            django_messages.success(request, f"⟲ {n} message(s) remis en file — partiront au prochain cycle daemon.")
        else:
            django_messages.error(request, f"Action en masse inconnue : {bulk_action}")
        return redirect(request.path + "?" + request.GET.urlencode())

    status_filter = request.GET.get("status", "pending")
    kind_filter = request.GET.get("kind", "")

    queryset = PendingOutbound.objects.all().order_by("-created_at")
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    if kind_filter:
        queryset = queryset.filter(kind=kind_filter)

    # Exclusion des PO dont le Deal est shadow (duplicate_campaign / pre_existing_relation)
    # : on ne doit pas faire valider à Richard des messages pour des contacts
    # déjà gérés sur une autre campagne (dedup cross-campagne, decision 02/06/2026).
    if status_filter in ("pending", "approved", ""):
        shadow_q = Q()
        for pid, camp_id in (
            Deal.objects.filter(outcome__in=SHADOW_OUTCOMES)
            .values_list("lead__public_identifier", "campaign_id")
        ):
            shadow_q |= Q(prospect_public_id=pid, campaign_id=camp_id)
        if shadow_q:
            queryset = queryset.exclude(shadow_q)

    counts = {
        "pending": PendingOutbound.objects.filter(status=OutboundStatus.PENDING).count(),
        "approved": PendingOutbound.objects.filter(status=OutboundStatus.APPROVED).count(),
        "sent": PendingOutbound.objects.filter(status=OutboundStatus.SENT).count(),
        "rejected": PendingOutbound.objects.filter(status=OutboundStatus.REJECTED).count(),
        "failed": PendingOutbound.objects.filter(status=OutboundStatus.FAILED).count(),
    }

    items = list(queryset[:100])
    # Enrichissement nom + societe + ville (un lookup Deal par slug/campaign)
    slug_camp = [(o.prospect_public_id, o.campaign_id) for o in items]
    deal_map = {}
    if slug_camp:
        deals = Deal.objects.filter(
            lead__public_identifier__in=[s for s, _ in slug_camp],
        ).select_related("lead", "campaign")
        deal_map = {(d.lead.public_identifier, d.campaign_id): d for d in deals}
    for o in items:
        d = deal_map.get((o.prospect_public_id, o.campaign_id))
        disp = resolve_prospect_display(o.prospect_public_id, deal=d, company_hint=o.prospect_company)
        o.prospect_name = disp["name"]
        o.prospect_company_display = disp["company"]
        o.prospect_location = disp["location"]
        o.prospect_job_title = disp["job_title"]

    context = {
        "outbound_list": items,
        "counts": counts,
        "status_filter": status_filter,
        "kind_filter": kind_filter,
        "approval_mode": get_approval_mode().value,
        "now": timezone.localtime(),
    }
    return render(request, "ekoalu/outbound_list.html", context)


def _disqualify_leads_from_reject(public_ids: list[str], reason: str) -> tuple[int, int]:
    """Refus = exclusion permanente du prospect (toutes campagnes confondues).

    Wrapper historique : délègue à `ekoalu.lead_exclusion.disqualify_leads`
    (source of truth unique, partagée avec l'action Django Admin).
    """
    from ekoalu.lead_exclusion import disqualify_leads
    return disqualify_leads(public_ids, reason)


def _persona_slug_for_outbound(outbound: PendingOutbound) -> str:
    """Infere le persona slug depuis le campaign_name de l'outbound."""
    name = outbound.campaign_name or ""
    if not name:
        return ""
    for p in PERSONAS.values():
        if p.label in name:
            return p.slug
    return ""


def _requeue_invitation_approved(deal) -> PendingOutbound | None:
    """Cree (ou reutilise) un PendingOutbound INVITATION APPROVED pour ce deal.

    Appele apres requalify : Richard a tranche, on saute l'etape ML scoring
    et la validation manuelle. L'invitation part au prochain cycle daemon.

    Idempotent : si un PendingOutbound non-terminal existe deja, on le bascule
    en APPROVED. Aucun doublon.
    """
    from ekoalu.prospect_display import extract_company

    prospect_slug = deal.lead.public_identifier
    company = extract_company(deal.profile_summary) or (deal.reason or "")[:140]
    # Tentative de recuperer une company concrete (le reason fallback est moche
    # mais evite d'envoyer chaine vide quand profile_summary pas encore materialise)
    if "EKOALU" in company or len(company) > 140:
        company = ""

    # Reutilise un PendingOutbound non-terminal si present
    existing = PendingOutbound.objects.filter(
        prospect_public_id=prospect_slug,
        campaign_id=deal.campaign_id,
        kind=OutboundKind.INVITATION,
    ).exclude(status__in=[OutboundStatus.SENT, OutboundStatus.REJECTED]).first()

    if existing:
        existing.status = OutboundStatus.APPROVED
        existing.approved_at = timezone.now()
        existing.rejection_reason = ""
        existing.save()
        return existing

    return PendingOutbound.objects.create(
        prospect_public_id=prospect_slug,
        prospect_company=company,
        campaign_id=deal.campaign_id,
        campaign_name=deal.campaign.name if deal.campaign else "",
        kind=OutboundKind.INVITATION,
        ai_draft="(Invitation LinkedIn sans note)",
        status=OutboundStatus.APPROVED,
        approved_at=timezone.now(),
    )


def _capture_correction_example(
    outbound: PendingOutbound,
    final_text: str,
    explanation: str = "",
    instruction: str = "",
) -> None:
    """Cree un PendingReply + CorrectionExample pour alimenter le few-shot."""
    try:
        from ekoalu.inbox_assist.models import CorrectionExample, PendingReply
        persona_slug = _persona_slug_for_outbound(outbound)
        if not persona_slug:
            persona_slug = outbound.kind  # fallback "invitation" / "follow_up" / "reply"
        pr = PendingReply.objects.create(
            prospect_public_id=outbound.prospect_public_id,
            campaign_id=outbound.campaign_id,
            inbound_message=f"(outbound {outbound.kind})",
            ai_draft=outbound.ai_draft,
            final_sent=final_text,
            status=PendingReply.Status.SENT,
            sent_at=timezone.now(),
        )
        CorrectionExample.from_pending(
            pr,
            persona_slug=persona_slug,
            explanation=explanation,
            instruction=instruction,
        )
    except Exception as e:
        import logging
        logging.exception("CorrectionExample creation failed: %s", e)


def _regenerate_email_draft(outbound: PendingOutbound, instruction: str) -> tuple[bool, str]:
    """Regenere un cold mail / email follow-up via le generateur EMAIL EKOALU.

    (Avant : la regeneration passait par le generateur de DM LinkedIn, qui ne
    connait ni le format sujet/corps ni le lien RDV → hallucinations.)
    """
    from crm.models import Lead
    from ekoalu.email_generator.generator import generate_cold_email

    lead = (
        Lead.objects
        .filter(public_identifier=outbound.prospect_public_id)
        .select_related("email_data")
        .first()
    )
    data = getattr(lead, "email_data", None) if lead else None
    draft = generate_cold_email(
        entreprise=(data.entreprise if data else outbound.prospect_company or ""),
        dirigeant=(data.dirigeant if data else ""),
        code_naf=(data.code_naf if data else ""),
        activite=(data.activite if data else ""),
        ville=(data.ville if data else ""),
        dpt=(data.dpt if data else ""),
        effectif_min=(data.effectif_min if data else 0),
        effectif_max=(data.effectif_max if data else 0),
        variant=outbound.prompt_variant or None,
        instruction=instruction,
    )
    if not draft.is_valid():
        return False, "Le générateur email a renvoyé un draft vide (clé API ? erreur réseau ?)."
    outbound.ai_draft = draft.body
    outbound.subject = draft.subject
    outbound.final_content = ""  # on repart sur le nouveau draft
    outbound.save(update_fields=["ai_draft", "subject", "final_content"])
    return True, ""


def _regenerate_outbound_draft(outbound: PendingOutbound, instruction: str) -> tuple[bool, str]:
    """Regenere le brouillon de l'outbound via le generateur EKOALU.

    Renvoie (success, error_msg).
    Invitations : pas de regeneration (mode sans note). Emails : generateur
    email dedie. FOLLOW_UP / REPLY LinkedIn : generateur DM.
    """
    if outbound.kind == OutboundKind.INVITATION:
        return False, "Régénération désactivée pour les invitations (mode sans note)."
    if outbound.kind in (OutboundKind.EMAIL_COLD, OutboundKind.EMAIL_FOLLOW_UP):
        return _regenerate_email_draft(outbound, instruction)

    from crm.models import Deal
    from ekoalu.follow_up.generator import generate_ekoalu_dm
    from ekoalu.follow_up.models import get_or_create_dm_config
    from ekoalu.follow_up.patch import _format_recent_messages, _is_first_outgoing_dm

    deal = (
        Deal.objects
        .filter(
            lead__public_identifier=outbound.prospect_public_id,
            campaign_id=outbound.campaign_id,
        )
        .select_related("lead", "campaign")
        .first()
    )
    profile_summary = deal.profile_summary if deal else None
    chat_summary = deal.chat_summary if deal else None
    recent_text = _format_recent_messages(deal) if deal else ""
    persona_slug = _persona_slug_for_outbound(outbound)

    include_booking = False
    if deal and deal.campaign:
        dm_cfg = get_or_create_dm_config(deal.campaign)
        include_booking = (
            dm_cfg.include_booking_in_first_dm and _is_first_outgoing_dm(deal)
        )

    new_text = generate_ekoalu_dm(
        public_id=outbound.prospect_public_id,
        profile_summary=profile_summary,
        chat_summary=chat_summary,
        recent_messages_text=recent_text,
        persona_slug=persona_slug,
        include_booking=include_booking,
        instruction=instruction,
    )
    if not new_text:
        return False, "Le générateur Claude a renvoyé un texte vide (clé API ? erreur réseau ?)."
    outbound.ai_draft = new_text
    outbound.final_content = ""  # on repart sur le nouveau draft
    outbound.save(update_fields=["ai_draft", "final_content"])
    return True, ""


def _next_pending_outbound_pk(exclude_pk: int):
    """PK du prochain message EN ATTENTE de validation (triage en rafale,
    demande Richard 15/06 : enchaîner sur la fiche suivante après validation).
    None s'il n'en reste plus. Ordre = plus ancien d'abord (FIFO de la file)."""
    return (
        PendingOutbound.objects
        .filter(status=OutboundStatus.PENDING)
        .exclude(pk=exclude_pk)
        .order_by("created_at", "pk")
        .values_list("pk", flat=True)
        .first()
    )


def _redirect_next_or_list(request, exclude_pk: int):
    """Après une action terminale, va au prochain message à valider ; sinon liste."""
    nxt = _next_pending_outbound_pk(exclude_pk)
    if nxt:
        return redirect("ekoalu:outbound_detail", pk=nxt)
    django_messages.info(request, "Plus aucun message en attente de validation. 🎉")
    return redirect("ekoalu:outbound_list")


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
                learn_note = request.POST.get("learn_note", "").strip()
                _capture_correction_example(
                    outbound,
                    final_text=final_content.strip(),
                    explanation=learn_note,
                )

            django_messages.success(request, "Message approuvé. Il sera envoyé au prochain cycle.")
            return _redirect_next_or_list(request, outbound.pk)

        elif action == "reject":
            rejection_reason = request.POST.get("rejection_reason", "")
            outbound.status = OutboundStatus.REJECTED
            outbound.rejection_reason = rejection_reason
            outbound.save()
            n_leads, _ = _disqualify_leads_from_reject(
                [outbound.prospect_public_id],
                rejection_reason or "(sans raison)",
            )
            msg = "Message refusé."
            if n_leads:
                msg += " Prospect retiré du pipeline (Lead disqualifié)."
            django_messages.warning(request, msg)
            return _redirect_next_or_list(request, outbound.pk)

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
            return _redirect_next_or_list(request, outbound.pk)

        elif action == "regenerate":
            instruction = request.POST.get("regen_instruction", "").strip()
            old_draft = outbound.ai_draft
            success, error = _regenerate_outbound_draft(outbound, instruction)
            if not success:
                django_messages.error(request, error)
                return redirect("ekoalu:outbound_detail", pk=pk)
            # Capture l'echange comme apprentissage : ancienne version -> nouvelle version
            # piloté par la consigne (peut etre vide).
            try:
                from ekoalu.inbox_assist.models import CorrectionExample, PendingReply
                persona_slug = _persona_slug_for_outbound(outbound) or outbound.kind
                pr = PendingReply.objects.create(
                    prospect_public_id=outbound.prospect_public_id,
                    campaign_id=outbound.campaign_id,
                    inbound_message=f"(regenerate {outbound.kind})",
                    ai_draft=old_draft,
                    final_sent=outbound.ai_draft,
                    status=PendingReply.Status.SENT,
                    sent_at=timezone.now(),
                )
                CorrectionExample.from_pending(
                    pr,
                    persona_slug=persona_slug,
                    instruction=instruction,
                )
            except Exception as e:
                import logging
                logging.exception("CorrectionExample (regenerate) creation failed: %s", e)
            if instruction:
                django_messages.success(
                    request,
                    "Brouillon régénéré avec ta consigne. Tu peux encore éditer avant d'approuver.",
                )
            else:
                django_messages.success(
                    request,
                    "Brouillon régénéré (sans consigne). Tu peux encore éditer avant d'approuver.",
                )
            return redirect("ekoalu:outbound_detail", pk=pk)

    from crm.models import Deal
    deal = (
        Deal.objects
        .filter(lead__public_identifier=outbound.prospect_public_id, campaign_id=outbound.campaign_id)
        .select_related("lead", "campaign")
        .first()
    )
    card = _build_prospect_card(
        outbound.prospect_public_id, deal=deal, company_hint=outbound.prospect_company,
    )
    display = card["display"]
    prospect_facts = card["facts"]
    lead = card["lead"]
    is_mail_only = card["is_mail_only"]

    # Apercu signature pour les emails : le bloc coordonnees est appose par
    # le sender a l'envoi — on le montre ici pour que Richard voie le mail
    # COMPLET. Le logo part en cid: inline (invisible hors client mail) ->
    # on le substitue par un data URI base64 pour l'apercu navigateur.
    signature_preview = ""
    if outbound.kind in (OutboundKind.EMAIL_COLD, OutboundKind.EMAIL_FOLLOW_UP):
        import base64

        from ekoalu.email_canal.sender import LOGO_CID, get_logo_bytes, signature_block_html
        signature_preview = signature_block_html(
            formal_first=(outbound.kind == OutboundKind.EMAIL_COLD), with_logo=True,
        )
        logo = get_logo_bytes()
        if logo:
            signature_preview = signature_preview.replace(
                f"cid:{LOGO_CID}",
                "data:image/png;base64," + base64.b64encode(logo).decode("ascii"),
            )

    context = {
        "outbound": outbound,
        "linkedin_url": card["linkedin_url"],
        "is_mail_only": is_mail_only,
        "is_pending": outbound.status == OutboundStatus.PENDING,
        "is_approved": outbound.status == OutboundStatus.APPROVED,
        "can_regenerate": outbound.kind != OutboundKind.INVITATION
            and outbound.status == OutboundStatus.PENDING,
        "prospect_display": display,
        "prospect_facts": prospect_facts,
        "prospect_lead": lead,
        "signature_preview": signature_preview,
    }
    return render(request, "ekoalu/outbound_detail.html", context)


# ---- A : drill-down depuis le dashboard --------------------------------

_STATE_FILTER_MAP = {
    "qualified":    {"states": ["Qualified"],                     "title": "Qualifiés"},
    "ready":        {"states": ["Ready_to_connect"],              "title": "Prêts à inviter"},
    "pending":      {"states": ["Pending"],                       "title": "Invités (en attente)"},
    "connected":    {"states": ["Connected"],                     "title": "Connectés"},
    "completed":    {"states": ["Completed"],                     "title": "Clôturés"},
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

        # ---- Bulk action : confirm_reject sur plusieurs disqualifies a la fois ----
        if action == "bulk_confirm_reject":
            ids = _parse_bulk_ids(request)
            if not ids:
                django_messages.warning(request, "Aucun prospect sélectionné.")
                return redirect(request.path + "?" + request.GET.urlencode())
            deals_qs = Deal.objects.filter(pk__in=ids).select_related("lead", "campaign")
            n = 0
            for d in deals_qs:
                QualificationFeedback.objects.create(
                    prospect_public_id=d.lead.public_identifier,
                    campaign_id=d.campaign_id,
                    campaign_name=d.campaign.name if d.campaign else "",
                    claude_reason=d.reason or "",
                    richard_explanation="(rejet en masse)",
                    kind=QualificationFeedback.Kind.CONFIRM_REJECT,
                )
                n += 1
            django_messages.success(
                request,
                f"✓ Rejet confirmé en masse pour {n} prospect{'s' if n>1 else ''}. "
                "Décisions Claude validées, feedback enregistré pour apprentissage.",
            )
            return redirect(request.path + "?" + request.GET.urlencode())

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
            target_deal = None
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
                        target_deal = existing
                    else:
                        target_deal = Deal.objects.create(
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
                target_deal = deal
                django_messages.success(
                    request,
                    f"{deal.lead.public_identifier} remis en file Qualified sur la meme campagne. "
                    "Claude apprendra de cette correction.",
                )
            # Trigger automatique : on met une invitation directement APPROVED
            # dans la file (Richard a tranche, pas de re-validation manuelle).
            if target_deal is not None:
                try:
                    po = _requeue_invitation_approved(target_deal)
                    if po:
                        django_messages.info(
                            request,
                            f"Invitation LinkedIn programmee (PendingOutbound #{po.pk}, APPROVED) — "
                            "partira au prochain cycle daemon dans la limite du cap journalier.",
                        )
                except Exception as e:
                    import logging
                    logging.exception("Auto-requeue invitation failed: %s", e)
                    django_messages.warning(
                        request,
                        f"Requalif OK mais auto-requeue invitation a echoue : {e}. "
                        "Tu peux la creer manuellement depuis la fiche prospect.",
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
        elif action == "already_connected":
            QualificationFeedback.objects.create(
                prospect_public_id=deal.lead.public_identifier,
                campaign_id=deal.campaign_id,
                campaign_name=deal.campaign.name if deal.campaign else "",
                claude_reason=deal.reason or "",
                richard_explanation=explanation or "Deja une relation de Richard",
                kind=QualificationFeedback.Kind.ALREADY_CONNECTED,
            )
            # Exclusion permanente : evite re-sourcing futur
            deal.lead.disqualified = True
            deal.lead.save(update_fields=["disqualified"])
            django_messages.success(
                request,
                f"{deal.lead.public_identifier} marque comme deja relation - exclu des sourcings futurs.",
            )
        return redirect(request.path + "?" + request.GET.urlencode())

    state = request.GET.get("state", "qualified")
    cfg = _STATE_FILTER_MAP.get(state, _STATE_FILTER_MAP["qualified"])

    qs = (
        Deal.objects.filter(
            campaign__name__startswith="EKOALU - ",
            state__in=cfg["states"],
        )
        .exclude(outcome__in=SHADOW_OUTCOMES)
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

    from ekoalu.prospect_display import analyze_disqualification, resolve_prospect_display
    deals_list = list(qs[:200])
    for d in deals_list:
        disp = resolve_prospect_display(d.lead.public_identifier, deal=d)
        d.prospect_name = disp["name"]
        d.prospect_company_display = disp["company"]
        d.prospect_location = disp["location"]
        d.prospect_job_title = disp["job_title"]
        if state == "disqualified":
            analysis = analyze_disqualification(d.reason or "")
            d.analysis_criteria = analysis["criteria"]
            d.analysis_tldr = analysis["tldr"]

    context = {
        "state_filter": state,
        "title": cfg["title"],
        "deals": deals_list,
        "total": qs.count(),
        "state_filter_map": _STATE_FILTER_MAP,
        "already_feedback_slugs": feedback_slugs,
        "handled_elsewhere_slugs": handled_elsewhere_slugs,
        "all_ekoalu_campaigns": all_ekoalu_campaigns,
        "nb_reviewed": nb_reviewed,
        "show_reviewed": show_reviewed,
    }
    return render(request, "ekoalu/deals_filtered.html", context)


# ---- Arret d'urgence : bouton STOP du dashboard ----------------------

@staff_member_required
@require_POST
def emergency_stop_toggle(request):
    """Pose ou leve l'arret d'urgence (sentinel data/emergency_stop.flag).

    POST action=engage  -> tout le pipeline se met en pause au prochain cycle
    POST action=release -> reprise

    Le daemon LinkedIn et les commandes d'envoi (cold mail / outbound / replies)
    honorent ce sentinel. Cf. ekoalu/emergency_stop.py.
    """
    from ekoalu.emergency_stop import engage, is_stopped, release

    action = request.POST.get("action", "")
    if action == "engage":
        if is_stopped():
            django_messages.info(request, "Arret d'urgence deja actif.")
        else:
            reason = (request.POST.get("reason") or "Stop manuel dashboard").strip()
            actor = getattr(request.user, "username", "") or "staff"
            engage(reason=reason, actor=actor)
            django_messages.error(
                request,
                "⛔ ARRET D'URGENCE ACTIVE. Le daemon et tous les envois sont en "
                "pause au prochain cycle (max ~1 min). Clique « Reprendre » "
                "quand tout est rentre dans l'ordre.",
            )
    elif action == "release":
        if release():
            django_messages.success(
                request,
                "Arret d'urgence leve. Le daemon reprend au prochain cycle "
                "(dans la limite des plages horaires + caps habituels).",
            )
        else:
            django_messages.info(request, "Aucun arret d'urgence actif a lever.")
    else:
        django_messages.warning(request, "Action inconnue (engage|release attendu).")

    return redirect(request.POST.get("next") or "ekoalu:dashboard")


# ---- Budget guard : acquittement manuel par Richard ------------------

@staff_member_required
def budget_resume(request):
    """Supprime le sentinel budget journalier pour reprendre les appels Claude.

    Trigger : mail "[STOP] EKOALU prospection - budget journalier depasse"
    Cf. ekoalu/llm_usage/budget_guard.py.
    """
    from ekoalu.llm_usage.budget_guard import acknowledge, daily_budget_usd

    purged = acknowledge()
    if purged:
        django_messages.success(
            request,
            f"Sentinel budget acquitte. Les appels Claude reprennent. "
            f"Seuil actuel : ${daily_budget_usd():.2f}/jour. Garde un oeil sur /ekoalu/usage/.",
        )
    else:
        django_messages.info(request, "Aucun sentinel budget actif a acquitter.")
    return redirect("ekoalu:usage")


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

    # ─── Reconciliation Admin API (vérité officielle Anthropic) ────────────
    # Compare tracker interne vs Admin API sur 7 jours pour detecter les fuites.
    from datetime import date, timedelta as td
    from ekoalu.llm_usage.models import AnthropicUsageDaily

    last_7d_start = (now - timedelta(days=7)).date()
    today_date = now.date()

    # Admin API par jour
    admin_daily = (
        AnthropicUsageDaily.objects.filter(date__gte=last_7d_start)
        .values("date")
        .annotate(cost=Sum("cost_usd"), in_tok=Sum("input_tokens"), out_tok=Sum("output_tokens"))
        .order_by("-date")
    )
    admin_by_day = {row["date"].isoformat(): row for row in admin_daily}

    # Tracker interne par jour (meme periode)
    tracker_daily = (
        ClaudeUsageLog.objects.filter(timestamp__gte=now - timedelta(days=7))
        .extra(select={"day": "DATE(timestamp)"})
        .values("day")
        .annotate(cost=Sum("cost_usd"), n=Count("id"))
        .order_by("-day")
    )
    tracker_by_day = {row["day"]: row for row in tracker_daily}

    # Reconciliation jour par jour (7 derniers jours)
    reconciliation = []
    for offset in range(7):
        d = today_date - td(days=offset)
        d_iso = d.isoformat()
        admin = admin_by_day.get(d_iso)
        tracker = tracker_by_day.get(d_iso) or tracker_by_day.get(d) or {}
        admin_cost = (admin or {}).get("cost", 0.0) or 0.0
        tracker_cost = tracker.get("cost", 0.0) or 0.0
        gap = admin_cost - tracker_cost
        gap_pct = (gap / admin_cost * 100) if admin_cost else None
        reconciliation.append({
            "date": d,
            "admin_cost": admin_cost,
            "tracker_cost": tracker_cost,
            "gap_usd": gap,
            "gap_pct": gap_pct,
            "tracker_calls": tracker.get("n", 0),
        })

    # Totaux 7j
    admin_7d_total = sum(r["admin_cost"] for r in reconciliation)
    tracker_7d_total = sum(r["tracker_cost"] for r in reconciliation)
    gap_7d_pct = (
        (admin_7d_total - tracker_7d_total) / admin_7d_total * 100
        if admin_7d_total else None
    )

    # État source admin
    admin_api_has_data = AnthropicUsageDaily.objects.exists()
    admin_last_sync = (
        AnthropicUsageDaily.objects.order_by("-synced_at").first()
    )

    return render(request, "ekoalu/usage.html", {
        "today_rows": list(today_rows),
        "month_rows": list(month_rows),
        "daily": list(daily),
        "total_all": total_all,
        "recent": recent,
        # Reconciliation
        "reconciliation": reconciliation,
        "admin_7d_total": admin_7d_total,
        "tracker_7d_total": tracker_7d_total,
        "gap_7d_pct": gap_7d_pct,
        "admin_api_has_data": admin_api_has_data,
        "admin_last_sync": admin_last_sync,
    })


@staff_member_required
def daily_recap_today(request):
    """Redirige vers le recap du jour."""
    today = timezone.localdate().strftime("%Y-%m-%d")
    return redirect("ekoalu:recap_day", day=today)


@staff_member_required
def daily_recap_view(request, day: str):
    """Sert le HTML du recap genere par la commande daily_recap.

    Si pas encore genere, le genere a la volee (sans envoi mail, juste l'HTML).
    """
    from datetime import datetime
    from pathlib import Path
    from django.conf import settings
    from django.http import HttpResponse
    from ekoalu.management.commands.daily_recap import compute_stats, render_html

    try:
        target_date = datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        return HttpResponse("Date invalide (attendu YYYY-MM-DD)", status=400)

    recap_path = Path(settings.ROOT_DIR) / "data" / "recaps" / f"{day}.html"
    if recap_path.exists():
        html = recap_path.read_text(encoding="utf-8")
    else:
        stats = compute_stats(target_date)
        html = render_html(stats)

    return HttpResponse(html, content_type="text/html; charset=utf-8")
