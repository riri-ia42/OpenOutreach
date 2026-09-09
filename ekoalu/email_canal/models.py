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
    SOURCE_MAILJET_HOT = "mailjet_hot"  # ouvreurs/cliqueurs campagnes mailing-mailjet
    SOURCE_DECP = "decp"  # titulaires de marchés publics attribués (séance antichambre)
    SOURCE_DECP_INFLUENCE = "decp_influence"  # personnes physiques du groupe d'influence DECP
    SOURCE_REFERRAL = "referral"  # mise en relation par un contact (recommandation)

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
    # Fiche #251 : {"count": n, "last_at": iso, "checked_at": iso, "matched": "email|domain"}
    # posé par le contrôle Outlook avant cold mail ; null = jamais vérifié ou aucun échange.
    relation_existante = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "ekoalu"
        verbose_name = "Lead enrichi (canal email)"
        verbose_name_plural = "Leads enrichis (canal email)"

    def __str__(self) -> str:
        return f"EmailLeadData({self.entreprise or self.lead_id}, {self.code_naf})"


class ProspectRdv(models.Model):
    """Rendez-vous pris par un prospect (Bookings), rattaché à son lead (fiche #252).

    L'entonnoir s'arrêtait à la réponse ; l'objectif fixé en mai est le RDV visio.
    Clé d'idempotence = id de la notification Bookings. `lead` null = RDV non
    rapproché (adresse inconnue de la base) : signalé au récap, à traiter à la main.
    """

    class Status(models.TextChoices):
        PLANNED = "planned", "Planifié"
        HELD = "held", "Tenu"
        CANCELLED = "cancelled", "Annulé"

    lead = models.ForeignKey("crm.Lead", null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="rdvs")
    event_id = models.CharField(max_length=300, unique=True)
    prospect_email = models.CharField(max_length=254, blank=True, db_index=True)
    who = models.CharField(max_length=255, blank=True)
    service = models.CharField(max_length=128, blank=True)
    start = models.DateTimeField(null=True, blank=True)
    end = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PLANNED, db_index=True)
    channel = models.CharField(max_length=16, blank=True, help_text="email | linkedin | autre")
    cold_variant = models.CharField(max_length=32, blank=True)
    matched_by = models.CharField(max_length=16, blank=True, help_text="email | domain | ''")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "ekoalu"
        verbose_name = "RDV prospect"
        verbose_name_plural = "RDV prospects"

    def __str__(self) -> str:
        return f"RDV {self.who or self.prospect_email} {self.start:%d/%m %H:%M}" if self.start else f"RDV {self.who}"

    def refresh_status(self) -> None:
        """planifié → tenu une fois la fin passée (sauf annulation)."""
        from django.utils import timezone as _tz

        if self.status == self.Status.PLANNED and self.end and self.end < _tz.now():
            self.status = self.Status.HELD
            self.save(update_fields=["status"])

