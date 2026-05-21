"""Genere et envoie le recap quotidien de l'activite prospection.

Envoi SMTP si EMAIL_HOST configure dans le settings, sinon dump du HTML dans
data/recaps/YYYY-MM-DD.html. Toujours loggue le resultat.

Usage : python manage.py daily_recap [--date YYYY-MM-DD]
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.db.models import Count, Sum
from django.utils import timezone as dj_tz

from crm.models import Deal, Lead
from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound
from linkedin.models import Campaign, Task

try:
    from ekoalu.llm_usage.models import ClaudeUsageLog
except ImportError:
    ClaudeUsageLog = None  # type: ignore

logger = logging.getLogger(__name__)


@dataclass
class DailyStats:
    day: date
    leads_total: int
    leads_qualified: int
    leads_disqualified: int
    deals_created: int
    invitations_sent: int
    follow_ups_sent: int
    messages_pending_validation: int
    tasks_completed: int
    tasks_failed: int
    accept_rate_today: float | None
    claude_cost_usd: float
    by_campaign: list[dict]
    recent_activity: list[str]


def compute_stats(day: date) -> DailyStats:
    tz = dj_tz.get_current_timezone()
    day_start = dj_tz.make_aware(datetime.combine(day, datetime.min.time()), tz)
    day_end = day_start + timedelta(days=1)

    leads_today = Lead.objects.filter(creation_date__gte=day_start, creation_date__lt=day_end)
    leads_total = leads_today.count()
    leads_qualified = leads_today.filter(deal__state="Qualified").distinct().count()
    leads_disqualified = leads_today.filter(disqualified=True).count()

    deals_today = Deal.objects.filter(creation_date__gte=day_start, creation_date__lt=day_end)
    deals_created = deals_today.count()

    outbound_today = PendingOutbound.objects.filter(
        sent_at__gte=day_start, sent_at__lt=day_end, status=OutboundStatus.SENT
    )
    invitations_sent = outbound_today.filter(kind="invitation").count()
    follow_ups_sent = outbound_today.filter(kind="follow_up").count()
    messages_pending_validation = PendingOutbound.objects.filter(status=OutboundStatus.PENDING).count()

    tasks_today_completed = Task.objects.filter(
        completed_at__gte=day_start, completed_at__lt=day_end, status="completed"
    ).count()
    tasks_today_failed = Task.objects.filter(
        completed_at__gte=day_start, completed_at__lt=day_end, status="failed"
    ).count()

    invits_today = outbound_today.filter(kind="invitation").count()
    connected_today = Deal.objects.filter(
        update_date__gte=day_start, update_date__lt=day_end, state="Connected"
    ).count()
    accept_rate = round(100.0 * connected_today / invits_today, 1) if invits_today > 0 else None

    cost_today = 0.0
    if ClaudeUsageLog is not None:
        cost_today = float(
            ClaudeUsageLog.objects.filter(
                timestamp__gte=day_start, timestamp__lt=day_end
            ).aggregate(s=Sum("cost_usd"))["s"]
            or 0.0
        )

    by_campaign = []
    for c in Campaign.objects.all().order_by("name"):
        ld = Lead.objects.filter(
            deal__campaign=c, creation_date__gte=day_start, creation_date__lt=day_end
        ).distinct().count()
        sent = outbound_today.filter(campaign_id=c.pk).count()
        if ld == 0 and sent == 0:
            continue
        by_campaign.append(
            {
                "name": c.name.replace("EKOALU - ", ""),
                "leads": ld,
                "sent": sent,
            }
        )

    recent_activity: list[str] = []
    for t in Task.objects.filter(
        completed_at__gte=day_start, completed_at__lt=day_end, status="completed"
    ).order_by("-completed_at")[:5]:
        recent_activity.append(f"{t.completed_at.astimezone(tz):%H:%M} - {t.task_type}")

    return DailyStats(
        day=day,
        leads_total=leads_total,
        leads_qualified=leads_qualified,
        leads_disqualified=leads_disqualified,
        deals_created=deals_created,
        invitations_sent=invitations_sent,
        follow_ups_sent=follow_ups_sent,
        messages_pending_validation=messages_pending_validation,
        tasks_completed=tasks_today_completed,
        tasks_failed=tasks_today_failed,
        accept_rate_today=accept_rate,
        claude_cost_usd=cost_today,
        by_campaign=by_campaign,
        recent_activity=recent_activity,
    )


def render_html(s: DailyStats) -> str:
    day_str = s.day.strftime("%A %d %B %Y").replace("January", "janvier").replace(
        "February", "fevrier").replace("March", "mars").replace("April", "avril").replace(
        "May", "mai").replace("June", "juin").replace("July", "juillet").replace(
        "August", "aout").replace("September", "septembre").replace(
        "October", "octobre").replace("November", "novembre").replace("December", "decembre")

    accept = f"{s.accept_rate_today}%" if s.accept_rate_today is not None else "n/a"
    health = "OK"
    health_color = "#16a34a"
    if s.tasks_failed > 0 and s.tasks_completed == 0:
        health = "ALERTE - aucune tache reussie, daemon possiblement KO"
        health_color = "#dc2626"
    elif s.tasks_failed > s.tasks_completed * 2:
        health = "DEGRADE - beaucoup d'echecs"
        health_color = "#ea580c"

    rows_campaign = "\n".join(
        f"<tr><td>{c['name']}</td><td style='text-align:right'>{c['leads']}</td>"
        f"<td style='text-align:right'>{c['sent']}</td></tr>"
        for c in s.by_campaign
    ) or "<tr><td colspan='3' style='color:#6b7280'>(aucune activite par campagne)</td></tr>"

    recent = "<br>".join(s.recent_activity) or "(aucune action terminee)"

    return f"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><title>Recap EKOALU {s.day}</title></head>
<body style="font-family: -apple-system, Segoe UI, sans-serif; max-width: 700px; margin: 0 auto; padding: 20px; color: #111827;">
<h1 style="border-bottom: 2px solid #3b82f6; padding-bottom: 8px;">Recap prospection EKOALU</h1>
<p style="color: #6b7280; margin-top: 0;">{day_str}</p>

<div style="background: {health_color}; color: white; padding: 12px 16px; border-radius: 8px; margin: 16px 0;">
  <strong>Etat outil :</strong> {health}
</div>

<h2 style="color: #1f2937;">Activite du jour</h2>
<table style="width: 100%; border-collapse: collapse; margin: 12px 0;">
  <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb;">Nouveaux prospects scrapes</td>
      <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; text-align: right; font-weight: bold;">{s.leads_total}</td></tr>
  <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb;">&nbsp;&nbsp;dont qualifies par Claude</td>
      <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; text-align: right; color: #16a34a;">{s.leads_qualified}</td></tr>
  <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb;">&nbsp;&nbsp;dont disqualifies</td>
      <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; text-align: right; color: #6b7280;">{s.leads_disqualified}</td></tr>
  <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb;">Deals crees / requalifies</td>
      <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; text-align: right;">{s.deals_created}</td></tr>
  <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb;"><strong>Invitations envoyees</strong></td>
      <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; text-align: right; font-weight: bold; color: #2563eb;">{s.invitations_sent}</td></tr>
  <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb;">Follow-up envoyes</td>
      <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; text-align: right;">{s.follow_ups_sent}</td></tr>
  <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb;">Messages en attente de validation</td>
      <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; text-align: right; color: #ea580c;">{s.messages_pending_validation}</td></tr>
  <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb;">Taux acceptation (du jour)</td>
      <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; text-align: right;">{accept}</td></tr>
  <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb;">Cout Claude API du jour</td>
      <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; text-align: right;">{s.claude_cost_usd:.3f} $</td></tr>
  <tr><td style="padding: 8px; border-bottom: 1px solid #e5e7eb;">Tasks daemon completed / failed</td>
      <td style="padding: 8px; border-bottom: 1px solid #e5e7eb; text-align: right;">
        <span style="color: #16a34a">{s.tasks_completed}</span> /
        <span style="color: #dc2626">{s.tasks_failed}</span>
      </td></tr>
</table>

<h2 style="color: #1f2937;">Par campagne</h2>
<table style="width: 100%; border-collapse: collapse; margin: 12px 0;">
  <thead><tr style="background: #f3f4f6;">
    <th style="padding: 8px; text-align: left;">Campagne</th>
    <th style="padding: 8px; text-align: right;">Leads</th>
    <th style="padding: 8px; text-align: right;">Envois</th>
  </tr></thead>
  <tbody>{rows_campaign}</tbody>
</table>

<h2 style="color: #1f2937;">5 dernieres actions completees</h2>
<p style="background: #f9fafb; padding: 12px; border-radius: 8px; font-family: monospace; font-size: 13px;">{recent}</p>

<hr style="border: none; border-top: 1px solid #e5e7eb; margin: 24px 0;">
<p style="color: #6b7280; font-size: 12px;">
Dashboard complet : <a href="http://ekoalu-prospection:3210/ekoalu/">http://ekoalu-prospection:3210/ekoalu/</a><br>
A valider : <a href="http://ekoalu-prospection:3210/ekoalu/messages/?status=pending">messages en attente</a>
&middot; <a href="http://ekoalu-prospection:3210/ekoalu/companies-validation/">entreprises a approuver</a>
</p>
</body></html>"""


def render_text(s: DailyStats) -> str:
    accept = f"{s.accept_rate_today}%" if s.accept_rate_today is not None else "n/a"
    lines = [
        f"Recap prospection EKOALU - {s.day}",
        "",
        f"Nouveaux prospects   : {s.leads_total} (qualifies {s.leads_qualified}, disqualifies {s.leads_disqualified})",
        f"Deals crees          : {s.deals_created}",
        f"Invitations envoyees : {s.invitations_sent}",
        f"Follow-up envoyes    : {s.follow_ups_sent}",
        f"En attente validation: {s.messages_pending_validation}",
        f"Taux acceptation     : {accept}",
        f"Cout Claude          : {s.claude_cost_usd:.3f} $",
        f"Tasks completed/failed: {s.tasks_completed} / {s.tasks_failed}",
        "",
        "Dashboard : http://ekoalu-prospection:3210/ekoalu/",
    ]
    return "\n".join(lines)


class Command(BaseCommand):
    help = "Genere et envoie le recap quotidien de l'activite prospection."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            help="Date du recap au format YYYY-MM-DD (defaut: aujourd'hui)",
        )

    def handle(self, *args, **opts):
        if opts["date"]:
            day = datetime.strptime(opts["date"], "%Y-%m-%d").date()
        else:
            day = dj_tz.localdate()

        stats = compute_stats(day)
        html = render_html(stats)
        text = render_text(stats)

        # Dump fichier (always)
        recaps_dir = Path(settings.ROOT_DIR) / "data" / "recaps"
        recaps_dir.mkdir(parents=True, exist_ok=True)
        recap_path = recaps_dir / f"{day:%Y-%m-%d}.html"
        recap_path.write_text(html, encoding="utf-8")
        self.stdout.write(f"Recap HTML dump: {recap_path}")
        logger.info("Recap %s ecrit dans %s", day, recap_path)

        # Tentative envoi via Microsoft Graph (préféré — partagé avec mail-assistant)
        sent = False
        recipient = settings.RECAP_RECIPIENT
        subject = f"EKOALU prospection - recap {day:%d/%m/%Y} ({stats.invitations_sent} envois, {stats.leads_total} leads)"

        try:
            from ekoalu.notifications.graph_mailer import send_mail, is_configured
            if is_configured():
                send_mail(subject=subject, html_body=html, to=recipient)
                self.stdout.write(self.style.SUCCESS(f"Envoye via Graph a {recipient}"))
                logger.info("Recap %s envoye via Graph a %s", day, recipient)
                sent = True
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"Envoi Graph echoue : {exc}"))
            logger.warning("Recap %s : envoi Graph echoue : %s", day, exc)

        # Fallback SMTP si Graph KO et SMTP configure
        if not sent and settings.EMAIL_HOST and settings.EMAIL_HOST_USER:
            try:
                msg = EmailMultiAlternatives(
                    subject=subject,
                    body=text,
                    from_email=settings.RECAP_FROM,
                    to=[recipient],
                )
                msg.attach_alternative(html, "text/html")
                msg.send(fail_silently=False)
                self.stdout.write(self.style.SUCCESS(f"Envoye via SMTP a {recipient}"))
                logger.info("Recap %s envoye via SMTP a %s", day, recipient)
                sent = True
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"Envoi SMTP echoue : {exc}"))
                logger.warning("Recap %s : envoi SMTP echoue : %s", day, exc)

        if not sent:
            self.stdout.write(
                "Aucun canal d'envoi configure -- recap dispo uniquement dans data/recaps/. "
                "Pour activer : remplir GRAPH_* dans .env.production."
            )
