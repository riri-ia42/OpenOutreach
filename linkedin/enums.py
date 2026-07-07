from __future__ import annotations

from enum import StrEnum


class ProfileState(StrEnum):
    QUALIFIED = "Qualified"
    READY_TO_CONNECT = "Ready to Connect"
    PENDING = "Pending"
    CONNECTED = "Connected"
    COMPLETED = "Completed"
    FAILED = "Failed"


class _InterceptedForValidation:
    """Sentinelle « intercepté pour validation » (EKOALU outbound_validation).

    Retournée par les fonctions d'envoi patchées (send_connection_request,
    send_raw_message) quand l'invitation / le message est capturé en file
    PendingOutbound au lieu d'être envoyé : c'est le résultat NORMAL du mode
    require_approval, pas un échec d'envoi.

    Falsy à dessein : un appelant qui ne teste que la véracité y voit
    « pas envoyé » (donc aucun record_action) ; les handlers testent
    l'identité (``result is INTERCEPTED``) pour NE PAS traiter ce cas comme
    un échec (pas de démotion du Deal, pas de disqualification).
    """

    __slots__ = ()

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "INTERCEPTED"


INTERCEPTED = _InterceptedForValidation()
