"""Compteur journalier des profils envoyes a Apify (plafond de depense)."""
from __future__ import annotations

from django.db import models


class ApifyUsageDay(models.Model):
    """Une ligne par jour : nombre de profils envoyes a l'acteur Apify.

    Patron de ProfileReadDay (read_guard) mais compteur SEPARE et etanche :
    un fetch Apify est cookieless, il ne touche PAS le compte LinkedIn de
    Richard et ne compte JAMAIS dans le cap lectures. Ce plafond borne la
    DEPENSE Apify (env ``EKOALU_APIFY_DAILY_CAP``, defaut 40/j ~ 0,16 $/j).
    """

    date = models.DateField(unique=True)
    count = models.PositiveIntegerField(default=0)
    # 15/07 : tentatives en ECHEC du jour. Les echecs sont REMBOURSES de
    # ``count`` (un actor en panne — ex. limite 20 runs du plan Apify Free —
    # ne doit ni consommer le plafond pour rien, ni masquer la panne).
    # ``count`` = reussites effectives + tentatives en cours.
    failed = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = "ekoalu"
        verbose_name = "Profils Apify / jour"
        verbose_name_plural = "Profils Apify / jour"

    def __str__(self) -> str:
        return f"{self.date} : {self.count} profils Apify"
