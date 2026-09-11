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
import re
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
EMAIL_POOL_DAYS_MIN = 3          # jours de réserve avant alerte "manque de leads"


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
    """Évalue les 8 attendus. Retourne {checks, conform, date}."""
    from crm.models import Deal, Lead
    from ekoalu.apify_enrich import service as apify_service
    from ekoalu import conf
    from ekoalu.apify_enrich.models import ApifyUsageDay
    from ekoalu.email_canal.models import EmailLeadData
    from ekoalu.email_canal.pool import cold_mail_candidates
    from ekoalu.email_canal.sender import EMAIL_KINDS
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

    # 1. Enrichissement cookieless (aujourd'hui) — après la tâche 7h30.
    # Depuis le 02/09 la CHAÎNE (Bright Data → Apify → mini-fiche SERP) fait
    # le travail : compter les seuls snapshots Apify rendait le contrôle
    # aveugle (KO du 03/09 alors que Bright Data avait enrichi 40/40).
    # Mesure = vérité terrain : snapshots cookieless réellement posés du jour,
    # toutes sources confondues.
    apify_row = ApifyUsageDay.objects.filter(date=today).first()
    failed = getattr(apify_row, "failed", 0) if apify_row else 0
    by_source = {
        src: Lead.objects.filter(
            profile_snapshot_at__gte=today_start,
            profile_snapshot__source=src,
        ).count()
        for src in ("brightdata", "apify", "serper_snippet")
    }
    enriched_today = sum(by_source.values())
    backlog = len(apify_service.candidate_leads(APIFY_MIN_ENRICHED))
    expected_min = min(APIFY_MIN_ENRICHED, backlog)
    detail_sources = ", ".join(f"{src} {n}" for src, n in by_source.items() if n)
    if not _is_working_day(today):
        checks.append(_check("Enrichissement", True, "week-end", "-", "", skipped=True))
    elif enriched_today == 0 and failed == 0 and backlog > 0:
        checks.append(_check(
            "Enrichissement", False, "0 tentative (backlog non vide)",
            "la tâche 7h30 a tourné",
            "Vérifier la tâche planifiée EKOALU-Apify-Enrich (Task Scheduler), "
            "les kill-switches EKOALU_BRIGHTDATA_ENRICH / EKOALU_APIFY_ENRICH "
            "et data/apify_enrich.log.",
        ))
    else:
        checks.append(_check(
            "Enrichissement", enriched_today >= expected_min,
            f"{enriched_today} profil(s) enrichi(s) ({detail_sources or 'aucune source'}), "
            f"{failed} échec(s) Apify",
            f">= {expected_min} via la chaîne cookieless",
            "Vérifier data/apify_enrich.log, le quota Bright Data "
            "(BrightdataUsageMonth, 4500/mois) et le compte Apify en secours. "
            "Dernier recours assumé : repli Voyager (lectures compte LinkedIn).",
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

    # 5. Envois — la file approved DRAINE-t-elle ? Depuis les validations en
    # masse (31/08 : 141 approuvés d'un coup = ~3 jours de quota), un message
    # qui attend > 24h est NORMAL. Le vrai signal de panne : il reste des
    # approuvés ET le dernier jour ouvré a envoyé bien moins que le quota.
    stuck_cutoff = now - timedelta(hours=APPROVED_STUCK_HOURS)
    stuck = PendingOutbound.objects.filter(
        status=OutboundStatus.APPROVED, approved_at__lt=stuck_cutoff,
    ).count()
    from ekoalu.email_canal.quota import cold_mail_quota_for
    prev_day = now.date() - timedelta(days=1)
    while cold_mail_quota_for(prev_day) == 0:  # remonte au dernier jour actif
        prev_day -= timedelta(days=1)
    quota_prev = cold_mail_quota_for(prev_day)
    sent_prev = PendingOutbound.objects.filter(
        status=OutboundStatus.SENT,
        sent_at__date=prev_day,
    ).count()
    draining = sent_prev >= max(1, int(quota_prev * 0.5))
    checks.append(_check(
        "Envois", stuck == 0 or draining,
        f"{stuck} approuvé(s) > {APPROVED_STUCK_HOURS}h — dernier jour actif "
        f"({prev_day}) : {sent_prev} envoyé(s) / quota {quota_prev}",
        "file qui draine (>= 50 % du quota) ou 0 en attente",
        "Vérifier le daemon (drain de la file approved), les caps quotidiens "
        "(EKOALU_DAILY_INVITE_CAP / MESSAGE_CAP) et la session LinkedIn "
        "(auth_watch, checkpoint).",
    ))

    # 6. Backlog de tâches en retard — HORS connects (fix 03/09) : le reconcile
    # sème volontairement 1 connect par campagne active (162 campagnes) et le
    # quota n'en sert que 12/j → ~160 connects « en retard » est l'état NOMINAL
    # du système, pas un engorgement (le KO tombait tous les jours pour rien).
    # Le vrai signal de santé = relances (follow_up) et sondes (check_pending)
    # en retard ; les connects restent affichés en information.
    overdue_qs = Task.objects.filter(
        status=Task.Status.PENDING, scheduled_at__lt=today_start,
    )
    overdue_connects = overdue_qs.filter(task_type=Task.TaskType.CONNECT).count()
    overdue_real = overdue_qs.exclude(task_type=Task.TaskType.CONNECT).count()
    checks.append(_check(
        "Backlog tâches", overdue_real < OVERDUE_TASKS_MAX,
        f"{overdue_real} relances/sondes en retard "
        f"(+ {overdue_connects} connects semés, nominal au quota 12/j)",
        f"< {OVERDUE_TASKS_MAX} hors connects",
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
    # Seuil = EMAIL_POOL_DAYS_MIN jours de quota. Alerter à 1 jour serait trop
    # tard : la réalimentation demande une action (import DECP, enrichissement
    # SIRENE) qui ne se fait pas dans la matinée. Richard veut être prévenu
    # « dès que ça manque », donc avec de la marge (décision 28/07).
    quota_jour = conf.DAILY_COLD_MAIL_TARGET
    pool_min = quota_jour * EMAIL_POOL_DAYS_MIN
    jours_restants = len(pool) / quota_jour if quota_jour else 0
    checks.append(_check(
        "Canal email", len(pool) >= pool_min,
        f"vivier {len(pool)} lead(s) (~{jours_restants:.1f} jour(s) ouvré(s) "
        f"à {quota_jour}/j), {generated_yesterday} cold mail(s) générés hier",
        f">= {pool_min} leads ({EMAIL_POOL_DAYS_MIN} jours de réserve)",
        "Vivier bientôt à sec — réalimenter, par ordre de préférence : "
        "(1) `manage.py import_decp_cibles --dry-run` puis sans --dry-run "
        "(marchés publics attribués, régénéré chaque dimanche par BDD PROSPECT) ; "
        "(2) `manage.py import_bdd_prospect --source "
        "\"../../BDD PROSPECT/enrichis-sirene.json\" --priority P1P2` — ATTENTION, "
        "seul enrichis-sirene.json porte le code NAF, contacts-propres.json ne "
        "l'a pas (100 % de rejets naf_not_target) ; (3) si les deux sont épuisés, "
        "enrichissement NAF via l'API SIRENE des ~36 700 contacts de "
        "contacts-propres.json qui ont un SIREN (chantier de fond, décision "
        "Richard 28/07 : à lancer dès que les leads manquent).",
    ))

    # 8. Identité des messages en file — le mail salue-t-il la bonne personne et
    # ne nomme-t-il pas une société que l'adresse contredit ? (capture Richard
    # 11/09 : « incohérence entre les noms affichés, les mails et les sociétés »).
    # Les gardes sont dans les générateurs ; ce contrôle vérifie leur effet réel.
    from ekoalu.management.commands.audit_identites import cites_company
    from ekoalu.email_generator.salutation import (
        company_confirmed_by_email, dirigeant_for_salutation,
    )

    queued = list(PendingOutbound.objects.filter(
        kind__in=EMAIL_KINDS, status__in=[OutboundStatus.PENDING, OutboundStatus.APPROVED]))
    q_ids = [po.prospect_public_id for po in queued]
    q_mails = dict(Lead.objects.filter(public_identifier__in=q_ids)
                   .values_list("public_identifier", "contact_email"))
    q_infos = {p: (d, e) for p, d, e in EmailLeadData.objects
               .filter(lead__public_identifier__in=q_ids)
               .values_list("lead__public_identifier", "dirigeant", "entreprise")}
    incoherents = []
    for po in queued:
        dirigeant, entreprise = q_infos.get(po.prospect_public_id, ("", ""))
        email = q_mails.get(po.prospect_public_id) or ""
        body = po.content_to_send
        first = body.splitlines()[0] if body else ""
        salutation_ko = bool(re.match(r"^\s*Bonjour\s+(?!,)", first)) and not dirigeant_for_salutation(
            dirigeant, email, po.prospect_company or entreprise)
        societe_ko = bool(entreprise) and cites_company(body, entreprise)             and not company_confirmed_by_email(entreprise, email)
        if salutation_ko or societe_ko:
            incoherents.append(po.pk)
    checks.append(_check(
        "Identité des messages", not incoherents,
        f"{len(incoherents)} message(s) incohérent(s) sur {len(queued)} en file"
        + (f" : {incoherents[:10]}" if incoherents else ""),
        "0 sur la file",
        "Un message salue la mauvaise personne ou nomme une société que "
        "l'adresse contredit. Détail : `manage.py audit_identites` ; "
        "réécriture : `manage.py audit_identites --regenerate` (1 appel Claude "
        "par message). Si seule la salutation est en cause, `manage.py "
        "fix_salutations` corrige sans appel API.",
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

        # Hub-ekoalu (§14ter, capture Richard 26/08) : le verdict et les
        # corrections proposées doivent être VISIBLES dans le hub — d'autant
        # que le mail de report peut être suspendu par l'interrupteur.
        from ekoalu.notifications.hub_events import post_event, post_proposal
        if report["conform"]:
            post_event("conformite", "info", f"Conformité {report['date']:%d/%m} : CONFORME")
        else:
            failed = [c["name"] for c in report["checks"] if not c["ok"]]
            post_event(
                "conformite", "warn",
                f"Conformité {report['date']:%d/%m} : NON CONFORME ({', '.join(failed)})",
                {"rapport": text},
            )
            post_proposal(
                "Conformité NON CONFORME — corrections proposées (à traiter)",
                "## Constat\nLe contrôle quotidien est NON CONFORME. Corrections proposées "
                "par le contrôle (détail du jour dans le fil d'événements et "
                "`data/conformity_last.md`) :\n\n"
                f"```\n{text}\n```\n\n"
                "## Proposition\nTraiter les points KO ci-dessus (une session Claude Code "
                "sur prospection-ia), puis rejouer `manage.py daily_conformity --no-send` "
                "pour vérifier le retour au vert.",
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
