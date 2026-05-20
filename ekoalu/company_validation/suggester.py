"""Suggester d'entreprises via Claude API.

Demande à Claude de proposer N entreprises pertinentes selon l'ICP wedge
tertiaire EKOALU. Les insère en ApprovedCompany PENDING avec rationale.
"""
from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)


PROMPT_SYSTEM = """Tu es un consultant en sourcing B2B pour EKOALU, menuiserie
aluminium technique à Chasselay (Rhône, 69).

Stratégie commerciale EKOALU (wedge tertiaire technique) :
- Cible PRIMAIRE : entreprises générales tertiaires, charpentiers métalliques,
  métalliers/serruriers, maçons tertiaires (PME et ETI françaises)
- Géographie : régional Rhône-Alpes en priorité pour les standards,
  national pour les niches techniques (coupe-feu, désenfumage, pare-balles)
- Produits niches "wedge" : EI30-120, désenfumage DENFC, pare-balles BC1-4,
  grandes dimensions (>3m), acoustique Rw>40

Ton rôle : proposer des entreprises CIBLES pour la prospection automatisée.
Pas de particuliers, pas de promoteurs résidentiels, pas de fournisseurs concurrents.

Tu réponds en JSON STRICT — un tableau d'objets. Chaque objet :
{
  "name": "Nom de l'entreprise",
  "city": "Ville (si connue)",
  "rationale": "1-2 phrases : pourquoi cette entreprise correspond à l'ICP EKOALU"
}

Pas de texte avant ou après le JSON. Pas de markdown. Juste le tableau JSON.
"""


def _parse_company_json_tolerant(text: str) -> list[dict]:
    """Parse JSON tableau d'entreprises avec tolerance aux troncatures.

    Si la reponse Claude est coupee a max_tokens, le dernier objet est
    incomplet. On essaye le parse strict, puis on backtrack en cherchant
    le dernier objet `}` complet pour ne pas tout perdre.
    """
    text = text.strip()
    # Retirer markdown ```json ... ``` au cas ou
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # Tolerance : truncation a max_tokens. On cherche le dernier '}' qui
    # ferme un objet complet et on referme le tableau a la main.
    last_close = text.rfind("}")
    if last_close > 0:
        partial = text[: last_close + 1].rstrip(", \n\t")
        if not partial.endswith("]"):
            partial = partial + "]"
        try:
            data = json.loads(partial)
            if isinstance(data, list) and data:
                logger.warning(
                    "JSON Claude tronque (max_tokens probablement atteint), "
                    "recuperation partielle : %d objets",
                    len(data),
                )
                return data
        except json.JSONDecodeError as e:
            logger.error("JSON parse error meme apres tolerance : %s", e)

    logger.error("Impossible de parser la reponse Claude. Texte brut :\n%s", text[:2000])
    return []


def suggest_companies(n: int = 10, focus: str = "") -> list[dict]:
    """Demande à Claude N suggestions d'entreprises.

    Args:
        n: nombre d'entreprises à proposer
        focus: contrainte additionnelle ("Rhône-Alpes", "charpentier metal", etc.)

    Returns:
        Liste de dicts {name, city, rationale}.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        from linkedin.models import SiteConfig
        cfg = SiteConfig.load()
        api_key = cfg.llm_api_key or ""

    if not api_key:
        logger.error("Pas d'ANTHROPIC_API_KEY pour suggester")
        return []

    try:
        from anthropic import Anthropic
    except ImportError:
        logger.error("anthropic SDK non installe")
        return []

    user_msg = f"Propose {n} entreprises françaises correspondant à l'ICP."
    if focus:
        user_msg += f" Focus particulier : {focus}."

    # Budget tokens : ~300 tokens par entreprise (nom + ville + rationale 1-2 phrases)
    # Plafonne a 8000 pour rester raisonnable (cout ~0.12 USD max).
    max_tokens_budget = min(max(n * 300, 1500), 8000)

    client = Anthropic(api_key=api_key)
    try:
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            max_tokens=max_tokens_budget,
            system=PROMPT_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = resp.content[0].text if resp.content else ""
        stop_reason = getattr(resp, "stop_reason", "")
        if stop_reason == "max_tokens":
            logger.warning(
                "Claude reponse tronquee (stop_reason=max_tokens) — augmente max_tokens_budget actuel %d.",
                max_tokens_budget,
            )
    except Exception as e:
        logger.exception("Erreur Claude API : %s", e)
        return []

    data = _parse_company_json_tolerant(text)
    if not isinstance(data, list):
        return []

    # Validation minimale
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        result.append({
            "name": name,
            "city": (item.get("city") or "").strip(),
            "rationale": (item.get("rationale") or "").strip(),
        })

    return result


def import_suggestions_into_db(suggestions: list[dict]) -> dict:
    """Crée les ApprovedCompany en PENDING pour les suggestions.

    Returns: stats {created, skipped_existing}
    """
    from ekoalu.company_validation.models import (
        ApprovedCompany,
        CompanySource,
        CompanyStatus,
        _normalize_company_name,
    )

    stats = {"created": 0, "skipped_existing": 0}

    for s in suggestions:
        name = s["name"]
        normalized = _normalize_company_name(name)
        if not normalized:
            continue
        if ApprovedCompany.objects.filter(name_normalized=normalized).exists():
            stats["skipped_existing"] += 1
            continue

        rationale = s.get("rationale", "")
        if s.get("city"):
            rationale = f"[{s['city']}] {rationale}"

        ApprovedCompany.objects.create(
            name=name,
            source=CompanySource.AI_PROPOSED,
            status=CompanyStatus.PENDING,
            rationale=rationale[:1000],
        )
        stats["created"] += 1

    return stats
