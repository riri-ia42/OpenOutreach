"""personas — définitions des 8 personas EKOALU.

API publique :
- PERSONAS : dict des 8 personas
- get_persona(slug) : retourne la dataclass
- list_personas_by_priority() : liste ordonnée
"""
from ekoalu.personas.definitions import (
    PERSONAS,
    Persona,
    PersonaCategory,
    get_persona,
    list_personas_by_priority,
)

__all__ = [
    "PERSONAS",
    "Persona",
    "PersonaCategory",
    "get_persona",
    "list_personas_by_priority",
]
