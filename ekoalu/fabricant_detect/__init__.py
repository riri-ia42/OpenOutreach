"""Détection fabricant vs revendeur-poseur par lecture du site web.

Le code NAF est déclaratif et se trompe dans les deux sens. Ce module lit le
site de l'entreprise et tranche sur des preuves de production.

API publique :
- `domain_from_email(email)` / `fetch_site_text(domain)` — récupération
- `classify_with_escalation(client, items)` — Haiku batch + Sonnet si incertain
- `FabricantVerdict` — persistance, clé SIREN
"""
from ekoalu.fabricant_detect.classifier import (
    ClassifyInput,
    classify_batch,
    classify_one,
    classify_with_escalation,
)
from ekoalu.fabricant_detect.fetch import domain_from_email, fetch_site_text

__all__ = [
    "ClassifyInput",
    "classify_batch",
    "classify_one",
    "classify_with_escalation",
    "domain_from_email",
    "fetch_site_text",
]
