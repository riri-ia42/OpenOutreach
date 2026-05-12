"""outbound_validation — file d attente de validation des messages sortants.

Quand le daemon OpenOutreach veut envoyer un message (invitation, follow-up,
reply), il passe AVANT par cette file. Richard valide depuis l UI Django.

API :
- PendingOutbound : modele Django (queue de messages a valider)
- ApprovalMode : enum (auto_send / require_approval)
- get_approval_mode() : mode courant (env var EKOALU_APPROVAL_MODE)
"""
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound
from ekoalu.outbound_validation.config import ApprovalMode, get_approval_mode

__all__ = [
    "ApprovalMode",
    "OutboundKind",
    "OutboundStatus",
    "PendingOutbound",
    "get_approval_mode",
]
