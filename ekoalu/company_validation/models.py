"""Modèle ApprovedCompany — file de validation au niveau entreprise."""
from __future__ import annotations

from django.db import models


class CompanyStatus(models.TextChoices):
    APPROVED = "approved", "Approuvée (prospection OK)"
    PENDING = "pending", "En attente validation"
    REJECTED = "rejected", "Refusée (blacklist)"


class CompanySource(models.TextChoices):
    MANUAL = "manual", "Ajoutée manuellement par Richard"
    AI_PROPOSED = "ai_proposed", "Proposée par l'IA"


class ApprovedCompany(models.Model):
    """Statut de validation d'une entreprise pour la prospection."""

    name = models.CharField(max_length=255, unique=True, db_index=True)
    name_normalized = models.CharField(
        max_length=255, db_index=True,
        help_text="Nom en minuscules sans accents — pour matching",
    )
    linkedin_company_url = models.URLField(max_length=500, blank=True)
    status = models.CharField(
        max_length=16, choices=CompanyStatus.choices,
        default=CompanyStatus.PENDING, db_index=True,
    )
    source = models.CharField(
        max_length=16, choices=CompanySource.choices,
        default=CompanySource.MANUAL,
    )
    rationale = models.TextField(
        blank=True,
        help_text="Pourquoi l'IA propose cette entreprise (si source=ai_proposed)",
    )
    notes = models.TextField(blank=True, help_text="Notes Richard")
    leads_count = models.IntegerField(
        default=0,
        help_text="Nombre de prospects identifiés dans cette entreprise",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "ekoalu"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} [{self.status}]"

    def save(self, *args, **kwargs):
        # Auto-normalize name for matching
        self.name_normalized = _normalize_company_name(self.name)
        super().save(*args, **kwargs)

    @classmethod
    def is_approved(cls, company_name: str) -> bool:
        if not company_name:
            return False
        return cls.objects.filter(
            name_normalized=_normalize_company_name(company_name),
            status=CompanyStatus.APPROVED,
        ).exists()

    @classmethod
    def is_rejected(cls, company_name: str) -> bool:
        if not company_name:
            return False
        return cls.objects.filter(
            name_normalized=_normalize_company_name(company_name),
            status=CompanyStatus.REJECTED,
        ).exists()

    @classmethod
    def find_or_create_pending(cls, company_name: str, source: str = CompanySource.AI_PROPOSED) -> "ApprovedCompany":
        """Trouve une entreprise par nom normalisé ou la crée en pending."""
        normalized = _normalize_company_name(company_name)
        obj, _created = cls.objects.get_or_create(
            name_normalized=normalized,
            defaults={
                "name": company_name,
                "source": source,
                "status": CompanyStatus.PENDING,
            },
        )
        return obj


def _normalize_company_name(name: str) -> str:
    """Normalise un nom d'entreprise pour le matching.

    - Minuscules
    - Strip espaces/ponctuation périphérique
    - Retire les suffixes juridiques (SAS, SARL, SA, etc.)
    """
    if not name:
        return ""
    n = name.lower().strip()
    # Retirer accents basiques
    accents = {"é": "e", "è": "e", "ê": "e", "à": "a", "â": "a", "ô": "o", "û": "u", "ç": "c", "î": "i"}
    for a, b in accents.items():
        n = n.replace(a, b)
    # Retirer suffixes juridiques
    for suffix in [" sas", " sarl", " sa", " s.a.s.", " s.a.r.l.", " s.a.", " eurl", " sci"]:
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    # Retirer "entreprise " ou "société " en préfixe
    for prefix in ["entreprise ", "societe ", "ets "]:
        if n.startswith(prefix):
            n = n[len(prefix):]
    return n.strip()
