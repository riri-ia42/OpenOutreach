"""État de la rotation Serper par campagne ABM.

Permet de servir TOUTES les campagnes ABM à tour de rôle (décision Richard
12/06) : on sert d'abord la moins récemment servie, et une campagne dont les
requêtes ne ramènent plus AUCUN nouveau profil sur N passages consécutifs est
marquée épuisée (on arrête de dépenser des crédits dessus).
"""
from __future__ import annotations

from django.db import models


class SerpMeta(models.Model):
    """Title + snippet Google du profil, capturés au sourcing Serper.

    Décision Richard 02/09 (« il faut être malin ») : le titre SERP d'un profil
    LinkedIn EST « Prénom Nom - Poste - Entreprise » et le snippet porte souvent
    la localisation — une mini-fiche déjà payée (1 crédit Serper) que le code
    jetait après le pré-filtre. Stockée ici, elle sert de fournisseur
    d'enrichissement de DERNIER RECOURS cookieless (cf. snippet_profile.py) :
    zéro coût marginal, zéro lecture LinkedIn.
    """

    lead = models.OneToOneField(
        "crm.Lead", on_delete=models.CASCADE, related_name="serp_meta",
    )
    title = models.CharField(max_length=300, blank=True)
    snippet = models.TextField(blank=True)
    captured_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "ekoalu"
        verbose_name = "Méta SERP (title/snippet Serper)"
        verbose_name_plural = "Métas SERP (title/snippet Serper)"

    def __str__(self):
        return f"{self.lead.public_identifier}: {self.title[:60]}"


class GoogleSourcingState(models.Model):
    campaign = models.OneToOneField(
        "linkedin.Campaign", on_delete=models.CASCADE, related_name="google_sourcing_state",
    )
    last_run_at = models.DateTimeField(null=True, blank=True)
    consecutive_empty_runs = models.PositiveIntegerField(default=0)
    exhausted = models.BooleanField(default=False)
    total_new_leads = models.PositiveIntegerField(default=0)
    total_queries = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = "ekoalu"
        verbose_name = "État sourcing Google (rotation ABM)"
        verbose_name_plural = "États sourcing Google (rotation ABM)"

    def __str__(self):
        flag = "ÉPUISÉE" if self.exhausted else f"{self.consecutive_empty_runs} runs vides"
        return f"{self.campaign.name} [{flag}]"
