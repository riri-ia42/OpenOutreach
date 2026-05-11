"""human_scheduler — humanisation du scheduler OpenOutreach.

API publique :
- is_action_allowed_now() : bool — est-on dans une plage active ?
- compute_human_delay(base_delay) : float — ajuste un délai selon contraintes humaines
- next_active_slot() : datetime — prochain créneau disponible
"""
from ekoalu.human_scheduler.scheduler import (
    compute_human_delay,
    is_action_allowed_now,
    next_active_slot,
)

__all__ = [
    "compute_human_delay",
    "is_action_allowed_now",
    "next_active_slot",
]
