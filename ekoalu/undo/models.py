"""Journal d'annulation des actions de la file de validation."""
from __future__ import annotations

from django.db import models


class UndoEntry(models.Model):
    """Une action annulable, avec l'état PRÉCÉDENT des lignes qu'elle a touchées.

    Capture Richard 11/09 : la demande de confirmation avant chaque sortie de
    prospect ralentissait le tri sans rien empêcher (on confirme sans lire).
    Elle est remplacée par un retour arrière : on agit vite, on répare si on
    s'est trompé. Le journal ne sert QUE ça — ce n'est pas un historique
    d'audit, les entrées annulées et les anciennes sont purgées.
    """

    class Kind(models.TextChoices):
        SORTIR_PROSPECT = "sortir_prospect", "Sortie d'un prospect"
        SORTIR_SOCIETE = "sortir_societe", "Sortie d'une société"
        APPROVE = "approve", "Validation"
        REJECT = "reject", "Refus"
        REQUEUE = "requeue", "Remise en file"
        MARK_SENT = "mark_sent", "Marqué envoyé"

    kind = models.CharField(max_length=32, choices=Kind.choices, db_index=True)
    # Texte montré sur le bouton : « Sortie de Stephanie Lopitaux », « Validation de 12 messages »
    label = models.CharField(max_length=255)
    # État précédent, forme dépendante du kind (cf. ekoalu/undo/service.py).
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    undone_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "ekoalu"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"UndoEntry({self.kind}, {self.label})"
