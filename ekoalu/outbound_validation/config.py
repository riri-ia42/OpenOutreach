"""Configuration mode de validation des messages sortants."""
from __future__ import annotations

import enum
import os


class ApprovalMode(str, enum.Enum):
    AUTO_SEND = "auto_send"            # daemon envoie tout direct (legacy)
    REQUIRE_APPROVAL = "require_approval"  # tout passe par PendingOutbound (default EKOALU)


def get_approval_mode() -> ApprovalMode:
    """Mode courant lu depuis env var EKOALU_APPROVAL_MODE.

    Default: REQUIRE_APPROVAL (sécurité maximale, pas d envoi sans validation).
    """
    raw = os.environ.get("EKOALU_APPROVAL_MODE", "").strip().lower()
    if raw == "auto_send":
        return ApprovalMode.AUTO_SEND
    return ApprovalMode.REQUIRE_APPROVAL


def is_approval_required() -> bool:
    """True si on doit créer PendingOutbound au lieu d envoyer direct."""
    return get_approval_mode() == ApprovalMode.REQUIRE_APPROVAL
