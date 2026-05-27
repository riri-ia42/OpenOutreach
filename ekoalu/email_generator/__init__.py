"""Génération de cold mails EKOALU via Claude.

Public API :
- `generate_cold_email(lead) -> ColdEmailDraft` : génère subject + body
- `ColdEmailDraft` : dataclass de sortie
"""
from ekoalu.email_generator.generator import generate_cold_email, has_niche_mention
from ekoalu.email_generator.models import ColdEmailDraft
from ekoalu.email_generator.reply_generator import generate_email_reply

__all__ = ["generate_cold_email", "generate_email_reply",
           "has_niche_mention", "ColdEmailDraft"]
