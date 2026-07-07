"""Generateur EKOALU pour DM follow-up post-acceptation.

Deux modes (audit 07/07) :
- 1er message : structure rigide 4-blocs (salutation/question, service + niche
  obligatoire, CTA, signature configurable) ;
- relance/reponse (`relance=True`, le Deal a deja >= 1 message sortant) : prompt
  allege SANS pitch ni structure 4-blocs — reagir au contexte, apporter UNE info
  utile ou une question, ne PAS repeter l'offre/les competences deja presentees.

Pas de flatterie, pas de commentaire sur le parcours/poste, pas de jargon.
Apprentissage : regles apprises + few-shot CorrectionExample (canal linkedin_dm)
via ekoalu.learning ; garde-fou de style post-generation (1 regeneration max).
"""
from __future__ import annotations

import logging
import os
import re

from ekoalu import conf

logger = logging.getLogger(__name__)


from ekoalu.follow_up.prompts import (  # noqa: F401 — re-export compat
    BASE_SYSTEM_PROMPT,
    RELANCE_SYSTEM_PROMPT,
    _INSTRUCTION_OVERRIDE_CLAUSE,
    _render_system_prompt,
)


def _get_anthropic_client():
    """Cree un client Anthropic ou renvoie None si pas d'API key."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        try:
            from linkedin.models import SiteConfig
            cfg = SiteConfig.load()
            api_key = cfg.llm_api_key or ""
        except Exception:
            return None
    if not api_key:
        return None
    try:
        from anthropic import Anthropic
        return Anthropic(api_key=api_key)
    except ImportError:
        logger.error("anthropic SDK non installe")
        return None


def _facts_to_text(summary) -> str:
    """Transforme un profile_summary mem0 en texte plat pour le prompt."""
    if not summary:
        return ""
    facts = summary if isinstance(summary, list) else (summary.get("facts") or [])
    lines = []
    for fact in facts:
        if isinstance(fact, dict):
            txt = fact.get("memory") or fact.get("text") or fact.get("fact") or ""
        else:
            txt = str(fact)
        if txt:
            lines.append(f"- {txt}")
    return "\n".join(lines)


def _extract_first_name(public_id: str, profile_summary, chat_summary) -> str:
    """Heuristique pour extraire le prenom du prospect.

    Ordre : 1) facts profile_summary, 2) facts chat_summary, 3) slug LinkedIn.
    Renvoie "" si rien d'utilisable.
    """
    for blob in (profile_summary, chat_summary):
        text = _facts_to_text(blob).lower()
        for marker in ("first_name:", "prenom:", "prénom :", "first name:"):
            idx = text.find(marker)
            if idx >= 0:
                rest = text[idx + len(marker):].split("\n", 1)[0].strip()
                rest = rest.strip(".,;:- ")
                if rest:
                    return rest.split()[0].capitalize()
    if public_id:
        first_token = public_id.split("-")[0]
        if first_token and first_token.isalpha():
            return first_token.capitalize()
    return ""


def _build_few_shot(persona_slug: str = "", limit: int = 8) -> str:
    """Section few-shot depuis les CorrectionExample du canal DM LinkedIn.

    Filtre canal d'abord (plus de contamination email → DM), persona ensuite ;
    dedup des consignes quasi identiques ; marque used_in_prompt=True.
    """
    from ekoalu import learning
    from ekoalu.inbox_assist.models import CorrectionExample

    return learning.build_few_shot(
        CorrectionExample.Channel.LINKEDIN_DM,
        persona_slug=persona_slug,
        limit=limit,
    )


def _build_user_message(
    public_id: str,
    profile_summary,
    chat_summary,
    recent_messages_text: str,
    first_name: str,
    instruction: str,
    relance: bool = False,
) -> str:
    """Compose le bloc utilisateur envoye a Claude."""
    first_name_display = first_name or "(inconnu — utiliser 'Bonjour,' sans prenom)"
    if relance:
        parts = [
            "Genere le message LinkedIn de RELANCE pour ce prospect "
            "(conversation en cours, pitch deja envoye — ne le repete pas).",
        ]
    else:
        parts = ["Genere le message LinkedIn de follow-up pour ce prospect."]
    # La consigne est placee EN TETE et marquee prioritaire : c'est la demande
    # explicite de Richard pour CETTE version, elle prime sur la structure par defaut.
    if instruction.strip():
        parts += [
            "",
            "=== CONSIGNE EXPLICITE DE RICHARD (PRIORITAIRE) ===",
            instruction.strip(),
            "Applique cette consigne fidelement. Elle PRIME sur la structure 4-blocs "
            "et sur les regles de format/longueur par defaut en cas de conflit. "
            "Ne conserve comme contraintes dures que : zero mot banni, zero flatterie.",
        ]
    parts += [
        "",
        f"Slug LinkedIn : {public_id}",
        f"Prenom detecte : {first_name_display}",
        "",
        "Faits profil :",
        _facts_to_text(profile_summary) or "(aucun fait connu)",
    ]
    chat_text = _facts_to_text(chat_summary)
    if chat_text:
        parts += ["", "Faits conversation :", chat_text]
    if recent_messages_text:
        parts += ["", "Derniers messages echanges :", recent_messages_text]
    if instruction.strip():
        parts += [
            "",
            "Reponds UNIQUEMENT avec le message complet, mis en forme selon la consigne "
            "prioritaire ci-dessus (elle prime sur la structure par defaut).",
        ]
    elif relance:
        parts += ["", "Reponds UNIQUEMENT avec le message de relance (court, sans pitch)."]
    else:
        parts += [
            "",
            "Reponds UNIQUEMENT avec le message complet (4 blocs separes par une ligne vide).",
        ]
    return "\n".join(parts)


def _call_model(client, model_id: str, system: str, user_msg: str) -> str:
    """Un appel Claude, texte strippe ou "" si erreur."""
    try:
        resp = client.messages.create(
            model=model_id,
            max_tokens=900,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        return (resp.content[0].text if resp.content else "").strip()
    except Exception as e:
        logger.exception("Erreur generation DM EKOALU : %s", e)
        return ""


def _post_process(text: str, instruction: str, relance: bool) -> str:
    """Strip guillemets + garantit la signature (1er message uniquement)."""
    text = text.strip().strip('"').strip("'").strip()
    if not text or relance:
        # Mode relance : pas de re-appose du bloc signature/pitch (le prospect
        # a deja recu la presentation complete — consigne recurrente Richard).
        return text
    # MAIS si la consigne manuelle traite explicitement de la signature
    # (ex "sans signature", "signature courte"), on ne la re-impose pas de force.
    instruction_touches_signature = "signature" in instruction.lower()
    if conf.SIGNATURE_NAME not in text and not instruction_touches_signature:
        text = f"{text}\n\n{conf.render_signature()}"
    return text


def generate_ekoalu_dm(
    *,
    public_id: str,
    profile_summary=None,
    chat_summary=None,
    recent_messages_text: str = "",
    persona_slug: str = "",
    include_booking: bool = False,
    instruction: str = "",
    relance: bool = False,
    model: str | None = None,
) -> str:
    """Genere un DM EKOALU : 1er message structure 4-blocs, ou relance allegee.

    Retourne le texte du message ou "" si la generation a echoue.
    """
    from ekoalu import learning
    from ekoalu.inbox_assist.models import CorrectionExample
    from ekoalu.message_validator.style_guard import enforce_style

    client = _get_anthropic_client()
    if not client:
        logger.warning("Pas d'Anthropic client, retour vide")
        return ""

    first_name = _extract_first_name(public_id, profile_summary, chat_summary)
    has_instruction = bool(instruction.strip())
    system = (
        learning.learned_rules_block(CorrectionExample.Channel.LINKEDIN_DM)
        + _render_system_prompt(include_booking, has_instruction=has_instruction,
                                relance=relance)
        + _build_few_shot(persona_slug)
    )
    user_msg = _build_user_message(
        public_id=public_id,
        profile_summary=profile_summary,
        chat_summary=chat_summary,
        recent_messages_text=recent_messages_text,
        first_name=first_name,
        instruction=instruction,
        relance=relance,
    )
    model_id = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    text = _post_process(_call_model(client, model_id, system, user_msg),
                         instruction, relance)
    if not text:
        return ""

    def _regenerate(motif: str) -> str:
        return _post_process(
            _call_model(client, model_id, system, f"{user_msg}\n\n{motif}"),
            instruction, relance,
        )

    return enforce_style(text, _regenerate, channel="linkedin_dm")


def detect_first_name(public_id: str, profile_summary=None, chat_summary=None) -> str:
    """Helper public pour les tests/UI."""
    return _extract_first_name(public_id, profile_summary, chat_summary)


_NICHE_PATTERN = re.compile(
    r"\b(coupe[- ]feu|EI\s*\d+|desenfumage|désenfumage|denfc|pare[- ]balles?|"
    r"BC[1-4]|mur[- ]rideau|grandes? dim(?:ensions?)?|acoustique|Rw\s*[>=]?\s*\d+|POA)\b",
    re.IGNORECASE,
)


def has_niche_mention(text: str) -> bool:
    """Renvoie True si le message mentionne au moins 1 produit niche EKOALU."""
    return bool(_NICHE_PATTERN.search(text or ""))
