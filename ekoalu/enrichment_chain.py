"""Chaîne d'enrichissement cookieless — abstraction multi-fournisseurs.

Décision Richard 02/09 (« il faut être malin ») : ne plus dépendre d'un seul
fournisseur bridé. Ordre de la cascade :

1. **Bright Data** (primaire) — 5 000 enregistrements/mois gratuits, données
   complètes, le plus solide juridiquement.
2. **Apify apimaestro** (secours) — 10 profils/jour en free-tier.
3. **Mini-fiche SERP** (dernier recours cookieless) — title/snippet Serper
   stockés au sourcing, gratuits, partiels mais suffisants pour le tri.

Le repli Voyager (1 lecture du COMPTE LinkedIn) reste du ressort de
l'appelant (qualify.py) : il n'est jamais déclenché d'ici.

Leçon Proxycurl (fermé 07/2025 sous la pression de LinkedIn) : aucun
fournisseur n'est garanti à 18 mois — en changer doit rester une ligne dans
``_providers()``, pas une refonte.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _providers():
    """Liste ordonnée (nom, ready() -> bool, enrich(lead) -> bool)."""
    from ekoalu.apify_enrich import service as apify
    from ekoalu.brightdata_enrich import service as brightdata
    from ekoalu.google_sourcing import snippet_profile

    return (
        ("brightdata", brightdata.brightdata_ready, brightdata.enrich_lead),
        ("apify", apify.apify_ready, apify.enrich_lead),
        ("serper_snippet", lambda: True, snippet_profile.enrich_lead_from_serp),
    )


def enrich_lead_cookieless(lead) -> str | None:
    """Essaie chaque fournisseur dans l'ordre. Renvoie le nom du fournisseur
    qui a posé snapshot + embedding, None si tous ont échoué (l'appelant
    replie alors sur Voyager)."""
    for name, ready, enrich in _providers():
        if not ready():
            continue
        try:
            if enrich(lead):
                return name
        except Exception:  # noqa: BLE001 — un fournisseur cassé ne bloque pas la chaîne
            logger.exception("Fournisseur d'enrichissement %s en erreur pour %s "
                             "— fournisseur suivant", name, lead.public_identifier)
    return None
