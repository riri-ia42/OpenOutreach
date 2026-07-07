"""human_scheduler — humanisation du scheduler OpenOutreach.

API publique :
- is_action_allowed_now() : bool — est-on dans une plage active ?
  (channel="email" pour ignorer les jours off aléatoires LinkedIn)
- compute_human_delay(base_delay) : float — ajuste un délai selon contraintes humaines
- next_active_slot() : datetime — prochain créneau disponible
- is_day_off(date) : bool — jour off aléatoire LinkedIn (LOT E)
- daily_weight_factor(date) : float — poids hebdo appliqué aux caps quotidiens
"""
from ekoalu.human_scheduler.budget import daily_weight_factor, is_day_off
from ekoalu.human_scheduler.scheduler import (
    compute_human_delay,
    is_action_allowed_now,
    next_active_slot,
)

__all__ = [
    "compute_human_delay",
    "daily_weight_factor",
    "is_action_allowed_now",
    "is_day_off",
    "next_active_slot",
]
