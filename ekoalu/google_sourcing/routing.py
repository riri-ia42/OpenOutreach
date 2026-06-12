"""Routage des sources de sourcing par type de campagne (décision Richard 12/06).

- Campagnes ABM (1 entreprise cible) -> Serper UNIQUEMENT : la recherche
  LinkedIn native par mots-clés ramène des homonymes mondiaux sur les noms
  courts (incident ROMETAL 12/06 : 13 mineurs chiliens lus puis rejetés) et
  brûle le cap lectures. Serper fait des requêtes Google exactes, sans lecture.
- Campagnes globales (personas métallier, maçon, EG...) -> recherche LinkedIn
  native conservée (pas de nom d'entreprise à chercher sur Google).

Kill-switch : env EKOALU_ABM_NATIVE_SEARCH=1 réactive la recherche native
sur les ABM (debug uniquement).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def is_abm_campaign(campaign) -> bool:
    """True si la campagne cible une entreprise (lien ABM ou nom 'ABM - ...')."""
    if campaign is None:
        return False
    try:
        if getattr(campaign, "abm_link", None) is not None:
            return True
    except Exception:
        pass
    return " ABM - " in (campaign.name or "")


def native_search_allowed(campaign) -> bool:
    """False pour les campagnes ABM (servies par Serper), True sinon."""
    if os.environ.get("EKOALU_ABM_NATIVE_SEARCH", "") == "1":
        return True
    if is_abm_campaign(campaign):
        logger.info(
            "Recherche LinkedIn native désactivée pour la campagne ABM %r "
            "(sourcing via Serper — cf. ekoalu/google_sourcing)",
            getattr(campaign, "name", "?"),
        )
        return False
    return True
