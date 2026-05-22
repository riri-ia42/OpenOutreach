"""Pipeline ekoalu-doctor : detecte + diagnostique + advisory (V0 only).

Usage :
    python manage.py ekoalu_doctor
        # mode normal : collecte contexte, appelle Claude, mail Richard si KO
        # detecte
    python manage.py ekoalu_doctor --force
        # bypass la verif "rien d'anormal", force un diagnostic meme si HEALTHY
    python manage.py ekoalu_doctor --dry-run
        # collecte + diagnostic mais N'ENVOIE PAS de mail (test local)

Conditions de declenchement (sans --force) :
    health.status not in ("HEALTHY", "OUT_OF_HOURS", "DAEMON_DISABLED")
    OU anthropic_24h.total_cost_usd > 5.0 (cap nominal ~1$/jour)

Cap budget mensuel doctor : 30 USD. Si depasse -> mail simple "doctor disabled
budget cap" sans appel Claude.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

logger = logging.getLogger(__name__)

ABNORMAL_STATUSES = {
    "ZOMBIE_ASYNCIO",
    "ZOMBIE_NO_PROGRESS",
    "UNKNOWN",
}
COST_RUNAWAY_THRESHOLD_USD_24H = 5.0
DOCTOR_MONTHLY_BUDGET_USD = 30.0


def _doctor_monthly_cost() -> float:
    """Conso accumulee par le doctor sur les 30 derniers jours."""
    from django.apps import apps
    ClaudeUsageLog = apps.get_model("ekoalu", "ClaudeUsageLog")
    since = timezone.now() - timedelta(days=30)
    total = ClaudeUsageLog.objects.filter(
        timestamp__gte=since, context="ekoalu_doctor",
    ).aggregate(t=Sum("cost_usd"))["t"] or 0
    return float(total)


def _is_abnormal(context: dict) -> tuple[bool, str]:
    """True + raison si l'etat justifie un diagnostic doctor."""
    health = context.get("health") or {}
    status = health.get("status", "")
    if status in ABNORMAL_STATUSES:
        return True, f"HEALTH.status={status}"
    cost24h = (context.get("anthropic_24h") or {}).get("total_cost_usd", 0.0)
    if cost24h > COST_RUNAWAY_THRESHOLD_USD_24H:
        return True, f"cost_24h={cost24h:.2f} USD > {COST_RUNAWAY_THRESHOLD_USD_24H}"
    return False, ""


class Command(BaseCommand):
    help = "Doctor: detecte une anomalie, demande un diagnostic a Claude, envoie un mail advisory."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force", action="store_true",
            help="Force le diagnostic meme si HEALTHY",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Tout sauf l'envoi du mail",
        )

    def handle(self, *args, **opts):
        from django.apps import apps

        from ekoalu.doctor.context_collector import collect_context
        from ekoalu.doctor.diagnose import DiagnosisError, diagnose, filter_actions
        from ekoalu.doctor.mail import send_advisory
        from ekoalu.doctor.redact import redact_dict

        DoctorIncident = apps.get_model("ekoalu", "DoctorIncident")
        DoctorAction = apps.get_model("ekoalu", "DoctorAction")

        # 1. Cap budget mensuel -- gate avant tout appel Claude
        monthly = _doctor_monthly_cost()
        if monthly >= DOCTOR_MONTHLY_BUDGET_USD:
            self.stdout.write(self.style.WARNING(
                f"Doctor disabled: budget mensuel atteint ({monthly:.2f} USD / "
                f"{DOCTOR_MONTHLY_BUDGET_USD} USD). Reset le mois prochain.",
            ))
            return

        # 2. Collecte contexte
        context = collect_context()
        abnormal, reason = _is_abnormal(context)

        if not abnormal and not opts["force"]:
            self.stdout.write(self.style.SUCCESS(
                f"Etat OK ({context.get('health', {}).get('status', '?')}). Rien a faire.",
            ))
            return

        self.stdout.write(self.style.WARNING(f"Anomalie detectee: {reason or 'force'}"))

        # 3. Cree l'incident en DB
        incident = DoctorIncident.objects.create(
            trigger_health_status=context.get("health", {}).get("status", "")[:40],
            status=DoctorIncident.Status.OPEN,
        )
        self.stdout.write(f"Incident #{incident.pk} ouvert.")

        # 4. Appel Claude diagnostic (avec redaction prealable du contexte)
        try:
            safe_context = redact_dict(context)
            diagnosis, cost = diagnose(safe_context)
        except DiagnosisError as exc:
            incident.status = DoctorIncident.Status.FAILED
            incident.error_message = str(exc)[:1000]
            incident.closed_at = timezone.now()
            incident.save()
            self.stdout.write(self.style.ERROR(f"Diagnostic Claude echoue: {exc}"))
            raise

        # 5. Filtre les actions hors whitelist
        ok_actions, rejected = filter_actions(diagnosis.get("actions", []))
        if rejected:
            logger.warning("Doctor: %d action(s) hors whitelist rejetee(s): %s",
                           len(rejected), [a.get("action_type") for a in rejected])

        # 6. Persiste diagnostic + actions
        incident.diagnosis = (diagnosis.get("diagnosis") or "")[:5000]
        incident.signature = (diagnosis.get("signature") or "")[:64]
        incident.confidence = diagnosis.get("confidence", 0.0)
        incident.actions_proposed = ok_actions
        incident.cost_usd = round(cost, 5)
        incident.save()

        for action in ok_actions:
            DoctorAction.objects.create(
                incident=incident,
                action_type=action.get("action_type", "")[:50],
                payload=action.get("payload") or {},
                reason=(action.get("reason") or "")[:500],
                mode=DoctorAction.Mode.PROPOSED,  # V0 = advisory only
            )
        for action in rejected:
            DoctorAction.objects.create(
                incident=incident,
                action_type=str(action.get("action_type", "unknown"))[:50],
                payload=action.get("payload") or {},
                reason=action.get("reason", "")[:500],
                mode=DoctorAction.Mode.SKIPPED,
            )

        self.stdout.write(
            f"Diagnostic: {diagnosis.get('root_cause', '')[:80]} "
            f"(confidence={diagnosis['confidence']:.2f}, cost={cost:.4f} USD)",
        )

        # 7. Envoi mail advisory (V0 : toujours, pas d'execution auto)
        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry-run: mail NON envoye."))
            incident.status = DoctorIncident.Status.OPEN
        else:
            try:
                send_advisory(incident, diagnosis)
                incident.mail_sent_at = timezone.now()
                incident.status = DoctorIncident.Status.ADVISORY_SENT
                self.stdout.write(self.style.SUCCESS(
                    f"Mail advisory envoye (incident #{incident.pk})",
                ))
            except Exception as exc:
                incident.error_message = f"Mail failed: {exc}"[:1000]
                incident.status = DoctorIncident.Status.FAILED
                self.stdout.write(self.style.ERROR(f"Envoi mail echoue: {exc}"))

        incident.closed_at = timezone.now()
        incident.save()
