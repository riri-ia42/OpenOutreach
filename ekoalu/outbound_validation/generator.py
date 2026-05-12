"""Generateur de note d'invitation LinkedIn via Claude API.

Genere un texte court (< 250 char) personnalise pour chaque prospect.
Injecte les CorrectionExample passees en few-shot pour que Claude apprenne
du style de Richard.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """Tu rediges des notes d'invitation LinkedIn pour Richard Gros,
president d'EKOALU (menuiserie aluminium technique, Chasselay 69).

REGLES ABSOLUES :
- TEXTE COURT : 150-250 caracteres maximum (limite LinkedIn = 300).
- TON CORDIAL-PRO DIRECT : pas ampoule, pas jargon commercial.
- MENTION TECHNIQUE EKOALU (au moins 1) : coupe-feu, EI60, EI120, desenfumage,
  pare-balles, grandes dim, acoustique Rw, Cortizo, Sepalumic, SAPA.
- AUCUNE DEMANDE DE RDV. Juste se connecter pour se suivre.
- Style EKOALU : direct, ancrage Chasselay/Rhone-Alpes (sauf si prospect lointain).
- Pas de signature (LinkedIn la met automatiquement).

INTERDIT (mots bannis) :
- "permettez-moi", "j'aurais le plaisir", "n'hesitez surtout pas"
- "synergies", "win-win", "ROI", "disruption", "value-prop"
- "acteur incontournable", "leader", "reference", "excellence", "passion"
- "au plaisir d'echanger", "restant a votre disposition"

EXEMPLE BON :
"Vu votre operation tertiaire a Lyon Confluence livree en avril. Sur ce
type de chantier le lot menuiseries coupe-feu pose souvent souci aux EG.
Heureux de vous suivre si pertinent."

EXEMPLE MAUVAIS :
"Bonjour, permettez-moi de me presenter. Je suis le president d'EKOALU et
j'aurais l'extreme plaisir de vous proposer nos solutions clé en main..."

Tu reponds UNIQUEMENT par le texte de l'invitation, rien d'autre. Pas de
guillemets, pas de markdown, pas d'introduction.
"""


def _get_anthropic_client():
    """Cree un client Anthropic ou renvoie None si pas d'API key."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        from linkedin.models import SiteConfig
        cfg = SiteConfig.load()
        api_key = cfg.llm_api_key or ""
    if not api_key:
        return None
    try:
        from anthropic import Anthropic
        return Anthropic(api_key=api_key)
    except ImportError:
        logger.error("anthropic SDK non installe")
        return None


def _build_few_shot_examples(persona_slug: str = "", limit: int = 10) -> str:
    """Construit la section few-shot depuis les CorrectionExample passees."""
    from ekoalu.inbox_assist.models import CorrectionExample
    qs = CorrectionExample.objects.filter(
        similarity_ratio__lt=0.95,  # seulement les vraies corrections
    ).select_related("pending_reply").order_by("-created_at")
    if persona_slug:
        qs = qs.filter(persona_slug=persona_slug)
    examples = qs[:limit]

    if not examples:
        return ""

    lines = ["\n\n=== EXEMPLES DE CORRECTIONS DE RICHARD (apprends ce style) ==="]
    for ex in examples:
        pr = ex.pending_reply
        if not pr.final_sent:
            continue
        lines.append("---")
        lines.append(f"AI a propose : {pr.ai_draft[:300]}")
        lines.append(f"Richard a envoye : {pr.final_sent[:300]}")
    return "\n".join(lines)


def generate_invitation_note(
    prospect_public_id: str,
    prospect_company: str = "",
    prospect_headline: str = "",
    prospect_summary: str = "",
    campaign_persona_slug: str = "",
) -> str:
    """Genere une note d'invitation < 250 char via Claude.

    Retourne le texte (string) ou une chaine vide si echec.
    """
    client = _get_anthropic_client()
    if not client:
        logger.warning("Pas d'Anthropic client, retour brouillon vide")
        return ""

    few_shot = _build_few_shot_examples(persona_slug=campaign_persona_slug)

    user_msg = f"""Genere une note d'invitation LinkedIn pour ce prospect :

Slug LinkedIn : {prospect_public_id}
Entreprise : {prospect_company or "(non identifiee)"}
Headline LinkedIn : {prospect_headline or "(non identifiee)"}
Resume profil : {prospect_summary[:500] or "(non disponible)"}

Reponds UNIQUEMENT avec le texte de la note (150-250 char). Sans guillemets."""

    system = SYSTEM_PROMPT + few_shot

    try:
        resp = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            max_tokens=400,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = (resp.content[0].text if resp.content else "").strip()
    except Exception as e:
        logger.exception("Erreur generation note Claude : %s", e)
        return ""

    # Strip eventuel markdown ou guillemets
    text = text.strip().strip('"').strip("'").strip()

    # Tronquer si > 300 char (limite LinkedIn)
    if len(text) > 300:
        text = text[:297] + "..."

    return text
