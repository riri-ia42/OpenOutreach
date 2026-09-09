"""Règle d'arrêt de l'A/B des prompts cold mail (fiche hub #139, 09/09/2026).

L'A/B v1/v2 tournait à 50/50 depuis juin sans jamais conclure. Règle : dès que
deux variantes actives ont chacune >= MIN_SENT envois et que l'écart de taux
de réponse est >= MIN_GAP_POINTS, la meilleure gagne. Le récap du soir appelle
`evaluate_ab` et persiste le verdict dans `data/prompt_variants/ab_winner.json`
(lu par `prompts.py` pour mettre le poids des perdantes à 0). Idempotent : un
vainqueur déjà écrit n'est pas réécrit (Richard peut relancer un A/B en
supprimant le fichier ou en posant une v3 dans active.json).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_SENT = 100
MIN_GAP_POINTS = 2.0


def variants_dir() -> Path:
    from django.conf import settings

    base = Path(getattr(settings, "BASE_DIR", ".")) / "data" / "prompt_variants"
    custom = os.environ.get("EKOALU_PROMPT_VARIANTS_DIR", "").strip()
    return Path(custom) if custom else base


def winner_path() -> Path:
    return variants_dir() / "ab_winner.json"


@dataclass(frozen=True)
class AbVerdict:
    winner: str
    loser: str
    winner_rate: float
    loser_rate: float
    winner_sent: int
    loser_sent: int

    def as_dict(self) -> dict:
        return {
            "winner": self.winner, "loser": self.loser,
            "winner_rate": round(self.winner_rate, 4), "loser_rate": round(self.loser_rate, 4),
            "winner_sent": self.winner_sent, "loser_sent": self.loser_sent,
            "decided_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }


def evaluate_ab(sent: dict[str, int], replies: dict[str, int],
                active: set[str] | None = None) -> AbVerdict | None:
    """Renvoie le verdict si la règle d'arrêt est satisfaite, sinon None.

    `active` restreint aux variantes encore en lice (poids > 0) ; sans lui,
    toutes les variantes ayant des envois sont comparées.
    """
    ids = [v for v in sent if (active is None or v in active) and sent[v] >= MIN_SENT]
    if len(ids) < 2:
        return None
    rates = {v: replies.get(v, 0) / sent[v] for v in ids}
    best = max(ids, key=lambda v: rates[v])
    rest = [v for v in ids if v != best]
    second = max(rest, key=lambda v: rates[v])
    if (rates[best] - rates[second]) * 100.0 < MIN_GAP_POINTS:
        return None
    return AbVerdict(best, second, rates[best], rates[second], sent[best], sent[second])


def read_winner() -> str | None:
    try:
        data = json.loads(winner_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return data.get("winner") or None


def record_winner(verdict: AbVerdict) -> bool:
    """Écrit le verdict ; False si un vainqueur est déjà enregistré."""
    if read_winner():
        return False
    path = winner_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(verdict.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("A/B cold mail conclu : %s bat %s (%.1f %% vs %.1f %%)",
                verdict.winner, verdict.loser, verdict.winner_rate * 100, verdict.loser_rate * 100)
    return True
