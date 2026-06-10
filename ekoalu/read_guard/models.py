"""Compteur journalier des lectures de profil LinkedIn (Voyager)."""
from __future__ import annotations

from django.db import models


class ProfileReadDay(models.Model):
    """Une ligne par jour : nombre de fiches LinkedIn lues via Voyager.

    Alimente le cap dur lectures/jour (cf. read_guard/guard.py). C'est LE
    volume qui a cause le checkpoint du 06/06 (1200-1760 lectures/j vs
    repere ~80/j compte gratuit) — les caps d'envoi ne suffisent pas.
    """

    date = models.DateField(unique=True)
    count = models.PositiveIntegerField(default=0)
    notified = models.BooleanField(
        default=False,
        help_text="Mail d'alerte cap atteint deja envoye pour ce jour",
    )

    class Meta:
        verbose_name = "Lectures profil / jour"
        verbose_name_plural = "Lectures profil / jour"

    def __str__(self):
        return f"{self.date} : {self.count} lectures"
