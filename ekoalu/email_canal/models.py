"""Modèle des données enrichies pour les Lead du canal email.

Le modèle `crm.Lead` upstream reste minimal (linkedin_url, public_identifier,
contact_email, embedding, urn). Les enrichissements spécifiques au canal email
(provenance BDD PROSPECT, NAF, dirigeant, ville, effectif...) sont stockés ici
dans une table 1-1 avec Lead.

Justification : éviter de polluer crm.Lead avec des dizaines de champs
business-specific qui n'ont de sens que dans le contexte EKOALU canal email.
"""
from __future__ import annotations

from django.db import models


class EmailLeadData(models.Model):
    """Enrichissements EKOALU pour un Lead utilisé dans le canal email.

    Source possible : `bdd_prospect` (import depuis BDD PROSPECT EKOALU),
    `manual` (saisie Richard), `enrichment_api` (futur, ex Pappers).
    """

    SOURCE_BDD_PROSPECT = "bdd_prospect"
    SOURCE_MANUAL = "manual"
    SOURCE_ENRICHMENT_API = "enrichment_api"

    lead = models.OneToOneField(
        "crm.Lead",
        on_delete=models.CASCADE,
        related_name="email_data",
    )
    source = models.CharField(max_length=64, db_index=True)

    # Données société
    siren = models.CharField(max_length=20, blank=True, db_index=True)
    entreprise = models.CharField(max_length=255, blank=True)
    dirigeant = models.CharField(max_length=255, blank=True)
    code_naf = models.CharField(max_length=10, blank=True, db_index=True)
    activite = models.CharField(max_length=255, blank=True)

    # Géographie
    cp = models.CharField(max_length=10, blank=True)
    dpt = models.CharField(max_length=4, blank=True, db_index=True)
    ville = models.CharField(max_length=128, blank=True)

    # Effectif (tranche)
    effectif_min = models.IntegerField(default=0)
    effectif_max = models.IntegerField(default=0)

    # Snapshot row source (debug / re-traitement)
    raw_json = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "ekoalu"
        verbose_name = "Lead enrichi (canal email)"
        verbose_name_plural = "Leads enrichis (canal email)"

    def __str__(self) -> str:
        return f"EmailLeadData({self.entreprise or self.lead_id}, {self.code_naf})"
