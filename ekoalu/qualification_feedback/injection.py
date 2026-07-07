"""Injection des feedbacks Richard (requalify / confirm_reject) dans le prompt
qualifier (audit 07/07 : 171 QualificationFeedback jamais reinjectes).

Le bloc part dans le message USER (jamais dans le system : il est mis en cache
Anthropic et doit rester identique pour toutes les campagnes).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

FEEDBACK_LIMIT = 5


def qualification_feedback_block(campaign_id: int | None = None,
                                 limit: int = FEEDBACK_LIMIT) -> str:
    """Bloc des N derniers feedbacks pertinents (meme campagne d'abord, sinon globaux).

    Seuls REQUALIFY / CONFIRM_REJECT sont pertinents pour calibrer le verdict
    (ALREADY_CONNECTED ne dit rien de la pertinence du profil).
    Marque used_in_prompt=True sur les feedbacks injectes.
    """
    from ekoalu.qualification_feedback.models import QualificationFeedback as QF

    base = (
        QF.objects
        .filter(kind__in=[QF.Kind.REQUALIFY, QF.Kind.CONFIRM_REJECT])
        .order_by("-created_at", "-pk")
    )
    rows: list = []
    if campaign_id:
        rows = list(base.filter(campaign_id=campaign_id)[:limit])
    if len(rows) < limit:
        extra = base.exclude(pk__in=[r.pk for r in rows])[: limit - len(rows)]
        rows += list(extra)
    if not rows:
        return ""

    lines = [
        "## Recent human feedback on past qualification decisions "
        "(from Richard, the CEO — align your judgement with these)",
    ]
    for fb in rows:
        if fb.kind == QF.Kind.REQUALIFY:
            verdict = "Richard REVERSED the rejection (the profile IS relevant)"
        else:
            verdict = "Richard CONFIRMED the rejection"
        claude_said = (fb.claude_reason or "(no reason recorded)")[:200]
        lines.append(
            f"- Claude said: {claude_said} -> {verdict}. "
            f"Richard's explanation: {fb.richard_explanation[:300]}"
        )
    QF.objects.filter(pk__in=[r.pk for r in rows]).update(used_in_prompt=True)
    return "\n".join(lines) + "\n\n"
