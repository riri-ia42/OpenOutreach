"""Score de rendement du vivier cold mail (fiche hub #137, 09/09/2026).

Constat : les réponses viennent presque toutes d'un segment (BDD PROSPECT,
43.32B, Rhône) alors que la file FIFO déroulait 2 449 leads Mailjet à 1,6 %.
Ici on mesure, sur les cold mails SENT d'une fenêtre glissante, le taux de
réponse RÉELLE (hors off_topic / désinscription) par (source, NAF, dpt), avec
repli hiérarchique et lissage bayésien pour les petits effectifs :

    (source, naf, dpt) → (source, naf) → (source) → global

Le tri du vivier (pool.py) utilise ce score ; les leads Mailjet, jamais
qualifiés sur l'ICP, restent en queue quoi qu'il arrive (réserve).
Calculé à la demande (une requête par génération), pas de table à maintenir.
Kill-switch : EKOALU_YIELD_RANKING=0 (retour au FIFO + DECP en tête).
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from crm.models import Lead

logger = logging.getLogger(__name__)

WINDOW_DAYS = 90          # fenêtre d'observation des envois
PRIOR_WEIGHT = 20         # poids du parent dans le lissage (k envois fictifs)
GLOBAL_PRIOR = 0.03       # taux a priori quand aucune donnée (repère marché 3 %)
NON_REPLY_INTENTS = frozenset({"off_topic", "unsubscribe", "bounce", "auto_reply"})
RESERVE_SOURCES = frozenset({"mailjet_hot"})  # toujours en queue

Key = tuple[str, str, str]


def yield_ranking_enabled() -> bool:
    return os.environ.get("EKOALU_YIELD_RANKING", "1").lower() in ("1", "true", "yes")


@dataclass
class YieldTable:
    """Envois et réponses par clé, plus les agrégats parents."""

    sent: dict[tuple, int] = field(default_factory=lambda: defaultdict(int))
    replies: dict[tuple, int] = field(default_factory=lambda: defaultdict(int))

    def rate(self, key: tuple) -> float:
        """Taux lissé : (réponses + k × taux parent) / (envois + k)."""
        if len(key) == 0:
            n, r = self.sent[()], self.replies[()]
            return (r + PRIOR_WEIGHT * GLOBAL_PRIOR) / (n + PRIOR_WEIGHT) if n else GLOBAL_PRIOR
        parent = self.rate(key[:-1])
        n, r = self.sent[key], self.replies[key]
        return (r + PRIOR_WEIGHT * parent) / (n + PRIOR_WEIGHT)

    def score(self, source: str, naf: str, dpt: str) -> float:
        return self.rate((source, naf, dpt))


def _segments_of(public_ids: set[str]) -> dict[str, Key]:
    from ekoalu.email_canal.models import EmailLeadData

    rows = EmailLeadData.objects.filter(
        lead__public_identifier__in=public_ids,
    ).values_list("lead__public_identifier", "source", "code_naf", "dpt")
    return {pid: (src or "", naf or "", dpt or "") for pid, src, naf, dpt in rows}


def build_yield_table(days: int = WINDOW_DAYS) -> YieldTable:
    """Agrège les cold mails envoyés sur `days` jours et leurs réponses réelles."""
    from django.utils import timezone

    from ekoalu.inbox_assist.models import PendingReply
    from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

    since = timezone.now() - timedelta(days=days)
    sent_ids = set(
        PendingOutbound.objects
        .filter(kind=OutboundKind.EMAIL_COLD, status=OutboundStatus.SENT, sent_at__gte=since)
        .values_list("prospect_public_id", flat=True)
    )
    table = YieldTable()
    if not sent_ids:
        return table
    replied_ids = set(
        PendingReply.objects
        .filter(channel=PendingReply.CHANNEL_EMAIL, prospect_public_id__in=sent_ids)
        .exclude(intent__in=NON_REPLY_INTENTS)
        .values_list("prospect_public_id", flat=True)
    )
    segments = _segments_of(sent_ids)
    for pid in sent_ids:
        key = segments.get(pid, ("", "", ""))
        replied = pid in replied_ids
        for k in (key, key[:2], key[:1], ()):
            table.sent[k] += 1
            if replied:
                table.replies[k] += 1
    return table


def yield_breakdown(days: int = 30) -> dict[str, list[tuple[str, int, int]]]:
    """Pour le récap : [(libellé, envoyés, réponses)] par source et par NAF."""
    table = build_yield_table(days)
    by_source = sorted(
        ((k[0] or "?", n, table.replies[k]) for k, n in table.sent.items() if len(k) == 1),
        key=lambda t: -t[1],
    )
    by_naf: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for k, n in table.sent.items():
        if len(k) == 2 and k[1]:
            by_naf[k[1]][0] += n
            by_naf[k[1]][1] += table.replies[k]
    return {
        "source": by_source,
        "naf": sorted(((naf, v[0], v[1]) for naf, v in by_naf.items()), key=lambda t: -t[1]),
    }


def rank_key_factory(table: YieldTable):
    """Clé de tri du vivier : DECP prioritaires, puis rendement décroissant,
    réserve Mailjet en queue. Tri stable = FIFO à score égal."""
    from ekoalu.email_canal.pool import _not_priority

    def key(lead: "Lead") -> tuple:
        data = getattr(lead, "email_data", None)
        source = getattr(data, "source", "") or ""
        naf = getattr(data, "code_naf", "") or ""
        dpt = getattr(data, "dpt", "") or ""
        reserve = source in RESERVE_SOURCES
        return (_not_priority(lead), reserve, -table.score(source, naf, dpt))

    return key
