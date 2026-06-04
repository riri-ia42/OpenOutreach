"""Construction du bloc de criteres affines par campagne (pilote par l'Excel
de Richard) injecte dans Campaign.campaign_objective, que lit le juge Claude.

Pur / testable : pas d'acces DB ni fichier ici. La mgmt command
apply_campaign_criteria lit l'Excel et appelle build_refined_objective.
"""
from __future__ import annotations

# Marqueur idempotent : on remplace ce bloc s'il existe deja.
CRITERIA_MARKER = "## Ciblage affine (Richard)"


def _clean(text: str) -> str:
    return " ".join((text or "").split()).strip()


def normalize_geo(geo: str) -> str:
    """Tolere les fautes de l'Excel : NATIONNAL/REGIONNAL -> national/regional."""
    g = (geo or "").strip().lower()
    if g.startswith("nation"):
        return "national"
    if g.startswith("reg") or g.startswith("rég"):
        return "regional"
    return ""


def build_refined_objective(base_objective: str, criteria_text: str, geo: str) -> str:
    """Renvoie l'objectif de campagne enrichi du bloc de criteres affines.

    Idempotent : si un bloc CRITERIA_MARKER existe deja, il est remplace.
    """
    base = base_objective or ""
    # Retire un ancien bloc de criteres s'il existe (idempotence).
    idx = base.find(CRITERIA_MARKER)
    if idx != -1:
        base = base[:idx].rstrip()

    lines = [CRITERIA_MARKER]

    geo_norm = normalize_geo(geo)
    if geo_norm == "national":
        lines.append(
            "- Geographie : NATIONAL (grand groupe / niche technique) — accepter hors Rhone-Alpes.",
        )
    elif geo_norm == "regional":
        lines.append(
            "- Geographie : REGIONAL Rhone-Alpes (69, 01, 38, 42, 73, 74, 26, 07) — "
            "rejeter les profils clairement hors region sauf niche technique forte.",
        )

    crit = _clean(criteria_text)
    if crit:
        lines.append(f"- Consignes Richard : {crit}")

    if len(lines) == 1:
        # Rien a ajouter : ne pas polluer l'objectif d'un marqueur vide.
        return base

    block = "\n".join(lines)
    return f"{base}\n\n{block}".strip() if base else block
