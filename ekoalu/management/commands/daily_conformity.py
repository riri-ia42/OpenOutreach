"""Test + analyse de conformité quotidienne du pipeline (demande Richard 15/07).

Chaque matin (tâche planifiée EKOALU-Conformity-Check, après la rotation
Serper 7h et l'enrichissement Apify 7h30), on vérifie que la chaîne complète
tourne CONFORMÉMENT AUX ATTENDUS ; chaque non-conformité vient avec une
CORRECTION PROPOSÉE. Résultat : mail à Richard + data/conformity_last.md
(relu par Claude au prochain démarrage de session).

Attendus vérifiés (100 % lecture seule, aucun appel réseau payant) :
1. Apify (aujourd'hui)   — tentatives > 0 et taux de réussite >= 70 %
2. Sourcing (aujourd'hui) — >= 15 leads découverts (cible rotation : 30)
3. Connects (hier ouvré)  — >= 1 servie s'il y avait des connect dues
4. Qualification (hier)   — >= 1 deal créé s'il y avait des candidats embeddés
5. Envois                 — aucun message approuvé bloqué depuis > 24 h
6. Relances               — backlog de tâches en retard < 150
7. Canal email            — vivier de cold mails >= 1 jour de génération
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone as dj_tz

logger = logging.getLogger(__name__)

# Seuils des attendus (constantes lisibles, pas de sur-ingénierie env)
APIFY_MIN_ENRICHED = 8           # limite free-tier apimaestro 10/j, marge not-found
SOURCING_MIN_LEADS = 15          # cible rotation = 30, alerte sous la moitié
OVERDUE_TASKS_MAX = 150
APPROVED_STUCK_HOURS = 24
EMAIL_POOL_MIN = 25              # = 1 jour de génération (ColdLimit du pipeline)


def _is_working_day(d) -> bool:
    return d.weekday() < 5  # lun-ven


def _day_bounds(d):
    tz = dj_tz.get_current_timezone()
    start = dj_tz.make_aware(datetime.combine(d, datetime.min.time()), tz)
    return start, start + timedelta(days=1)


def _check(name: str, ok: bool, measured: str, expected: str,
           correction: str, skipped: bool = False) -> dict:
    return {
        "name": name, "ok": ok, "measured": measured, "expected": expected,
        "correction": None if ok else correction, "skipped": skipped,
    }


def build_conformity_report(today=None) -> dict:
    """Évalue les 6 attendus. Retourne {checks, conform, date}."""
    from crm.models import Deal, Lead
    from ekoalu.apify_enrich import service as apify_service
    from ekoalu.apify_enrich.models import ApifyUsageDay
    from ekoalu.email_canal.pool import cold_mail_candidates
    from ekoalu.human_scheduler.budget import is_day_off
    from ekoalu.lead_routing.models import LeadDiscovery
    from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound
    from linkedin.models import Task

    now = dj_tz.localtime()
    today = today or now.date()
    yesterday = today - timedelta(days=1)
    today_start, _ = _day_bounds(today)
    y_start, y_end = _day_bounds(yesterday)
    checks: list[dict] = []

    # 1. Apify (aujourd'hui) — après la tâche 7h30. Mesure = vérité terrain
    # (snapshots source=apify réellement posés), pas le compteur (le
    # disjoncteur free-tier le sature volontairement après la limite 10/j).
    apify_row = ApifyUsageDay.objects.filter(date=today).first()
    failed = getattr(apify_row, "failed", 0) if apify_row else 0
    enriched_today = Lead.objects.filter(
        profile_snapshot_at__gte=today_start,
        profile_snapshot__source="apify",
    ).count()
    backlog = len(apify_service.candidate_leads(APIFY_MIN_ENRICHED))
    expected_min = min(APIFY_MIN_ENRICHED, backlog)
    if not _is_working_day(today):
        checks.append(_check("Apify", True, "week-end", "-", "", skipped=True))
    elif enriched_today == 0 and failed == 0 and backlog > 0:
        checks.append(_check(
            "Apify", False, "0 tentative (backlog non vide)",
            "la tâche 7h30 a tourné",
            "Vérifier la tâche planifiée EKOALU-Apify-Enrich (Task Scheduler), "
            "le kill-switch EKOALU_APIFY_ENRICH et data/apify_enrich.log.",
        ))
    else:
        checks.append(_check(
            "Apify", enriched_today >= expected_min,
            f"{enriched_today} profil(s) enrichi(s), {failed} échec(s)",
            f">= {expected_min} (limite free-tier apimaestro : 10/j)",
            "Vérifier data/apify_enrich.log et le compte Apify (crédit 5 $/mois). "
            "Si la limite free-tier bride (10 profils/j), options : plan Apify "
            "Starter 29 $/mois, autre actor via EKOALU_APIFY_ACTOR, ou assumer "
            "le repli Voyager (lectures sur le compte LinkedIn).",
        ))

    # 2. Sourcing Serper (aujourd'hui) — après la rotation 7h
    sourced = (
        LeadDiscovery.objects.filter(created_at__gte=today_start)
        .values("lead_id").distinct().count()
    )
    if not _is_working_day(today):
        checks.append(_check("Sourcing", True, "week-end", "-", "", skipped=True))
    else:
        checks.append(_check(
            "Sourcing", sourced >= SOURCING_MIN_LEADS,
            f"{sourced} leads découverts",
            f">= {SOURCING_MIN_LEADS} (cible rotation : 30)",
            "Vérifier data/serper_rotation.log (tâche 7h), les crédits Serper, "
            "et les campagnes épuisées (`manage.py source_via_google_rotate "
            "--reset-exhausted` le cas échéant).",
        ))

    # 3. Connects servies hier (la qualification tourne DANS handle_connect)
    engagement_skipped = not _is_working_day(yesterday) or is_day_off(yesterday)
    connects_done = Task.objects.filter(
        task_type=Task.TaskType.CONNECT,
        completed_at__gte=y_start, completed_at__lt=y_end,
    ).count()
    connects_due = Task.objects.filter(
        task_type=Task.TaskType.CONNECT,
        status=Task.Status.PENDING, scheduled_at__lt=y_end,
    ).count()
    if engagement_skipped:
        checks.append(_check("Connects", True, "hier = jour off", "-", "", skipped=True))
    elif connects_done == 0 and connects_due > 0:
        checks.append(_check(
            "Connects", False,
            f"0 servie hier ({connects_due} dues en file)",
            ">= 1 connect servie (quota : 12/j)",
            "Famine connect (cf. 08-13/07) : vérifier EKOALU_DAILY_CONNECT_QUOTA, "
            "le volume de re-checks follow_up et que le daemon tourne "
            "(eko logs / data/daemon.log).",
        ))
    else:
        checks.append(_check(
            "Connects", True, f"{connects_done} servies hier", ">= 1 si dues", "",
        ))

    # 4. Qualification hier (deals créés)
    deals_created = Deal.objects.filter(
        creation_date__gte=y_start, creation_date__lt=y_end,
    ).count()
    qualif_backlog = Lead.objects.filter(
        embedding__isnull=False, disqualified=False, deal__isnull=True,
        discoveries__campaign__active=True,
    ).distinct().count()
    if engagement_skipped:
        checks.append(_check("Qualification", True, "hier = jour off", "-", "", skipped=True))
    elif deals_created == 0 and qualif_backlog > 0:
        checks.append(_check(
            "Qualification", False,
            f"0 deal créé hier ({qualif_backlog} candidats embeddés en attente)",
            ">= 1 deal / jour ouvré",
            "La qualification tourne dans handle_connect : vérifier le point "
            "Connects ci-dessus, le budget Claude (budget_guard 4 $/j) et les "
            "kill-switches (DAEMON_DISABLE_QUALIFIER, scoped_qualification).",
        ))
    else:
        checks.append(_check(
            "Qualification", True, f"{deals_created} deals créés hier",
            ">= 1 si candidats", "",
        ))

    # 5. Envois — messages approuvés bloqués depuis > 24h
    stuck_cutoff = now - timedelta(hours=APPROVED_STUCK_HOURS)
    stuck = PendingOutbound.objects.filter(
        status=OutboundStatus.APPROVED, approved_at__lt=stuck_cutoff,
    ).count()
    checks.append(_check(
        "Envois", stuck == 0,
        f"{stuck} message(s) approuvé(s) bloqué(s) > {APPROVED_STUCK_HOURS}h",
        "0 bloqué",
        "Vérifier le daemon (drain de la file approved), les caps quotidiens "
        "(EKOALU_DAILY_INVITE_CAP / MESSAGE_CAP) et la session LinkedIn "
        "(auth_watch, checkpoint).",
    ))

    # 6. Backlog de tâches en retard
    overdue = Task.objects.filter(
        status=Task.Status.PENDING, scheduled_at__lt=today_start,
    ).count()
    checks.append(_check(
        "Backlog tâches", overdue < OVERDUE_TASKS_MAX,
        f"{overdue} tâches en retard",
        f"< {OVERDUE_TASKS_MAX}",
        "File saturée : vider la file de validation (relances en attente), "
        "vérifier le débit du daemon et les caps ; voir analyse_semaine pour "
        "le détail par type.",
    ))

    # 7. Canal email — niveau du vivier de cold mails. Le pipeline du matin ne
    # fait que PUISER dedans : rien ne le réalimente automatiquement. À sec, il
    # tourne en "0 candidat" sans lever d'alerte (panne silencieuse 19/06→27/07).
    pool, _ = cold_mail_candidates()
    generated_yesterday = PendingOutbound.objects.filter(
        kind=OutboundKind.EMAIL_COLD,
        created_at__gte=y_start, created_at__lt=y_end,
    ).count()
    checks.append(_check(
        "Canal email", len(pool) >= EMAIL_POOL_MIN,
        f"vivier {len(pool)} lead(s), {generated_yesterday} cold mail(s) générés hier",
        f">= {EMAIL_POOL_MIN} leads en vivier (1 jour de génération)",
        "Vivier à sec : réalimenter depuis BDD PROSPECT — `manage.py "
        "import_bdd_prospect --source \"../../BDD PROSPECT/enrichis-sirene.json\" "
        "--priority P1P2 --dry-run` puis sans --dry-run. ATTENTION : seul "
        "enrichis-sirene.json porte le code NAF (contacts-propres.json ne l'a "
        "pas → 100 % de rejets naf_not_target).",
    ))

    conform = all(c["ok"] for c in checks)
    return {"date": today, "checks": checks, "conform": conform}


def render_text(report: dict) -> str:
    lines = [f"Conformité pipeline — {report['date']:%Y-%m-%d}", ""]
    for c in report["checks"]:
        flag = "SKIP" if c["skipped"] else ("OK  " if c["ok"] else "KO  ")
        lines.append(f"[{flag}] {c['name']}: {c['measured']} (attendu : {c['expected']})")
        if c["correction"]:
            lines.append(f"       >> CORRECTION PROPOSÉE : {c['correction']}")
    lines.append("")
    lines.append("VERDICT : " + ("CONFORME" if report["conform"] else "NON CONFORME"))
    return "\n".join(lines)


def render_html(report: dict) -> str:
    rows = []
    for c in report["checks"]:
        color = "#9ca3af" if c["skipped"] else ("#16a34a" if c["ok"] else "#dc2626")
        flag = "—" if c["skipped"] else ("✔" if c["ok"] else "✘")
        corr = (
            f"<div style='color:#b45309;font-size:12px;margin-top:2px'>"
            f"<b>Correction proposée :</b> {c['correction']}</div>"
            if c["correction"] else ""
        )
        rows.append(
            f"<tr><td style='padding:6px 10px;color:{color};font-weight:bold'>{flag}</td>"
            f"<td style='padding:6px 10px;font-weight:600'>{c['name']}</td>"
            f"<td style='padding:6px 10px'>{c['measured']}"
            f"<div style='color:#6b7280;font-size:12px'>attendu : {c['expected']}</div>"
            f"{corr}</td></tr>"
        )
    verdict = (
        "<span style='color:#16a34a'>CONFORME</span>" if report["conform"]
        else "<span style='color:#dc2626'>NON CONFORME — corrections proposées ci-dessous</span>"
    )
    return (
        f"<div style='font-family:Segoe UI,Arial,sans-serif;max-width:640px'>"
        f"<h2 style='margin:0 0 4px'>Conformité pipeline — {report['date']:%d/%m/%Y}</h2>"
        f"<p style='margin:0 0 12px;font-size:15px'><b>{verdict}</b></p>"
        f"<table style='border-collapse:collapse;font-size:14px'>{''.join(rows)}</table>"
        f"<p style='color:#6b7280;font-size:12px;margin-top:12px'>"
        f"Test quotidien automatique (EKOALU-Conformity-Check). "
        f"Dashboard : http://ekoalu-prospection:3210/ekoalu/</p></div>"
    )


class Command(BaseCommand):
    help = "Teste la conformité quotidienne du pipeline et propose des corrections."

    def add_arguments(self, parser):
        parser.add_argument("--no-send", action="store_true",
                            help="Affiche le rapport sans envoyer le mail")

    def handle(self, *args, **opts):
        report = build_conformity_report()
        text = render_text(report)
        self.stdout.write(text)

        data_dir = Path(settings.ROOT_DIR) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "conformity_last.md").write_text(
            f"# Conformité pipeline — {report['date']:%Y-%m-%d}\n\n"
            f"_Généré par daily_conformity (test quotidien). Si NON CONFORME,"
            f" proposer les corrections à Richard au prochain démarrage._\n\n"
            f"```\n{text}\n```\n",
            encoding="utf-8",
        )

        if opts["no_send"]:
            return
        from ekoalu.notifications.graph_mailer import is_configured, send_mail
        if not is_configured():
            logger.warning("Graph mailer non configuré — rapport non envoyé")
            return
        flag = "✅ CONFORME" if report["conform"] else "❌ NON CONFORME"
        send_mail(
            subject=f"[Prospection] Conformité {report['date']:%d/%m} — {flag}",
            html_body=render_html(report),
            text_body=text,
        )
        self.stdout.write(self.style.SUCCESS("Mail de conformité envoyé."))
