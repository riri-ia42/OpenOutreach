"""message_validator — valide les messages générés contre les règles EKOALU.

API publique :
- validate_message(text, context) : retourne ValidationResult avec passing + issues
- BANNED_WORDS : liste exposée pour tests
- NICHE_TERMS : liste exposée pour tests
"""
from ekoalu.message_validator.banned_words import (
    BANNED_WORDS,
    contains_banned_word,
    find_banned_words,
)
from ekoalu.message_validator.niche_terms import (
    NICHE_TERMS,
    contains_niche_term,
    find_niche_terms,
)
from ekoalu.message_validator.validator import (
    MessageStep,
    PersonaCategory,
    ValidationContext,
    ValidationResult,
    validate_message,
)

__all__ = [
    "BANNED_WORDS",
    "NICHE_TERMS",
    "MessageStep",
    "PersonaCategory",
    "ValidationContext",
    "ValidationResult",
    "contains_banned_word",
    "contains_niche_term",
    "find_banned_words",
    "find_niche_terms",
    "validate_message",
]
