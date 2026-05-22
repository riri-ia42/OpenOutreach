"""Modèles Django agrégés de l app ekoalu.

Re-exporte les modèles des sous-modules pour que Django les détecte.
"""
from ekoalu.company_validation.abm import AbmCampaignLink
from ekoalu.company_validation.models import ApprovedCompany
from ekoalu.doctor.models import DoctorAction, DoctorIncident
from ekoalu.follow_up.models import CampaignDmConfig
from ekoalu.inbox_assist.models import CorrectionExample, PendingReply
from ekoalu.llm_usage.models import ClaudeUsageLog
from ekoalu.outbound_validation.models import PendingOutbound
from ekoalu.qualification_feedback.models import QualificationFeedback

__all__ = [
    "PendingReply",
    "CorrectionExample",
    "PendingOutbound",
    "ApprovedCompany",
    "AbmCampaignLink",
    "CampaignDmConfig",
    "ClaudeUsageLog",
    "QualificationFeedback",
    "DoctorIncident",
    "DoctorAction",
]
