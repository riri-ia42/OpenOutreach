"""Vivier du canal email : leads éligibles à un cold mail.

Source unique de vérité partagée par `generate_cold_emails` (qui génère) et
`daily_conformity` (qui surveille le niveau de carburant). Sans ce partage, le
contrôle quotidien pouvait rester CONFORME pendant que le vivier était à sec —
c'est exactement ce qui s'est produit du 19/06 au 27/07 (0 cold mail généré
pendant 5 semaines, non détecté).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

if TYPE_CHECKING:  # pragma: no cover
    from crm.models import Lead

# Statuts qui bloquent une nouvelle génération : cold mail "en cours", déjà
# envoyé, OU refusé. Un refus est définitif (cf. lead_exclusion) : on ne
# regénère jamais un cold mail pour un prospect déjà refusé. FAILED/EXPIRED
# bloquent aussi (P2-1) ; le retry légitime passe par triage_failed_outbound.
BLOCKING_STATUSES = (
    OutboundStatus.PENDING,
    OutboundStatus.APPROVED,
    OutboundStatus.SENDING,
    OutboundStatus.SENT,
    OutboundStatus.BLOCKED_COMPANY,
    OutboundStatus.REJECTED,
    OutboundStatus.FAILED,
    OutboundStatus.EXPIRED,
)


def cold_mail_candidates(dpt: str = "", source: str = "") -> tuple[list["Lead"], int]:
    """Leads éligibles à un cold mail, dans l'ordre de la file.

    Retourne `(candidats, nb_skippés_exclusion_partagée)`.
    """
    from crm.models import Lead

    from ekoalu.shared_exclusions import excluded_emails

    leads_qs = (
        Lead.objects
        .filter(
            contact_email__isnull=False,
            unsubscribed_at__isnull=True,
            email_bounced_at__isnull=True,
            disqualified=False,
        )
        .exclude(contact_email="")
        .filter(email_data__isnull=False)
    )
    if dpt:
        leads_qs = leads_qs.filter(email_data__dpt=dpt)
    if source:
        leads_qs = leads_qs.filter(email_data__source=source)

    blocked_public_ids = set(
        PendingOutbound.objects
        .filter(kind=OutboundKind.EMAIL_COLD, status__in=BLOCKING_STATUSES)
        .values_list("prospect_public_id", flat=True)
    )
    shared_excluded = excluded_emails()

    skipped_excluded = 0
    candidates: list[Lead] = []
    for lead in leads_qs.select_related("email_data"):
        if lead.public_identifier in blocked_public_ids:
            continue
        if (lead.contact_email or "").strip().lower() in shared_excluded:
            skipped_excluded += 1
            continue
        candidates.append(lead)
    # Cibles prioritaires DECP en tête (décision Richard 2026-07-28) : poseurs
    # non-fabricants qui viennent de gagner un lot — fenêtre commerciale courte.
    # Tri stable : l'ordre FIFO est conservé à l'intérieur de chaque groupe.
    candidates.sort(key=_not_priority)
    return candidates, skipped_excluded


def _not_priority(lead: "Lead") -> bool:
    """False (= tête de file) pour une cible prioritaire DECP."""
    data = getattr(lead, "email_data", None)
    if data is None or data.source != "decp":
        return True
    return not bool((data.raw_json or {}).get("cible_prioritaire"))
