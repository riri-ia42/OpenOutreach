"""company_validation — file d'attente de validation au niveau entreprise.

Workflow :
1. Daemon trouve prospect, le qualifie via Claude
2. Avant envoi : on regarde l'entreprise du prospect
3. Si entreprise dans ApprovedCompany.APPROVED → poursuite normale (PendingOutbound)
4. Si entreprise dans ApprovedCompany.PENDING ou inconnue → bloqué + créé en attente
5. Si entreprise dans ApprovedCompany.REJECTED → skip définitif

Richard peut :
- Approuver en masse au niveau entreprise (toutes les invitations vers cette entreprise débloquées)
- Refuser une entreprise (toutes les invitations annulées)
- Activer le mode "100% auto" pour skip cette validation
"""
from ekoalu.company_validation.config import is_company_validation_enabled
from ekoalu.company_validation.models import ApprovedCompany, CompanyStatus

__all__ = [
    "ApprovedCompany",
    "CompanyStatus",
    "is_company_validation_enabled",
]
