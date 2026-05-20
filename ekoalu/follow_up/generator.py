"""Generateur EKOALU pour DM follow-up post-acceptation.

Structure rigide 4-blocs imposee :
1. Salutation + question concernement (tertiaire, technique)
2. Service + niches techniques (mention au moins 1)
3. CTA d echange court
4. Signature configurable (nom + titre + mobile + email)

Pas de flatterie, pas de commentaire sur le parcours/poste, pas de jargon.
"""
from __future__ import annotations

import logging
import os
import re

from ekoalu import conf

logger = logging.getLogger(__name__)


BASE_SYSTEM_PROMPT = """Tu rediges des messages LinkedIn post-acceptation pour
Richard Gros, President d'EKOALU (menuiserie aluminium, acier et bois technique,
Chasselay 69).

Tu DOIS produire un message en 4 blocs separes par une ligne blanche, dans cet ordre.
Format strict — n'invente PAS de bloc supplementaire.

--- BLOC 1 : Salutation + question concernement ---
Une phrase d'ouverture sobre + une question directe sur leur activite tertiaire.
Pas de "j'ai vu votre profil", pas de "belle trajectoire", pas de commentaire sur leur
poste passe ou actuel. Aucune flatterie.
Exemple : "Bonjour {{prenom}}, gerez-vous des projets dans le tertiaire (bureaux, ERP,
equipements, hotellerie, industries) ?"

--- BLOC 2 : Service + niches techniques ---
Presentation EKOALU en 2-3 lignes, avec mention obligatoire des produits techniques
(au moins 1 parmi : coupe-feu EI30/60/120, desenfumage, mur-rideau, pare-balles BC1-4,
grandes dimensions, acoustique Rw).
Exemple : "Chez EKOALU (Chasselay 69), nous sommes specialises en menuiserie alu,
acier et bois technique : coupe-feu EI30/60/120, desenfumage, mur-rideau, pare-balles,
grandes dimensions, acoustique Rw>40. Atelier integre, multi-gammes (Cortizo, Sepalumic,
SAPA, Wicona)."

--- BLOC 3 : CTA d'echange ---
Une seule phrase d'invitation a echanger. Pas de pression, pas de "au plaisir d'echanger".
{booking_clause}
Exemple sans lien : "Souhaitez-vous en echanger ?"
Exemple avec lien : "Si pertinent, voici mon agenda pour caler 15 min : {booking_url}"

--- BLOC 4 : Signature ---
Reproduis EXACTEMENT ce bloc (4 lignes), sans modification :
{signature_block}

REGLES ABSOLUES :
- Tonalite cordial-pro DIRECTE, jamais ampoulee.
- AUCUNE flatterie ("belle trajectoire", "parcours impressionnant", "surement", "bien costaud", "remarquable").
- AUCUN commentaire sur leur poste/parcours/ancien employeur.
- Pas de demande de RDV telephonique (visio uniquement, via le lien si fourni).
- Pas de jargon commercial : INTERDITS = synergies, win-win, ROI, disruption, value-prop,
  acteur incontournable, leader, reference, excellence, passion, permettez-moi,
  j'aurais le plaisir, n'hesitez surtout pas, au plaisir d'echanger,
  restant a votre disposition, dans l'attente, solutions cle en main.
- Si tu connais le prenom, utilise-le (extrait du profil/historique). Sinon : "Bonjour,".
- Pas de markdown, pas de guillemets autour du message.
- Ecris en francais.

Tu reponds UNIQUEMENT par le texte du message complet (4 blocs separes par
une ligne vide), rien d'autre.
"""


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
    """Construit la section few-shot depuis les CorrectionExample passees.

    Tient compte des 3 types : text_correction, instruction_only, both.
    """
    from ekoalu.inbox_assist.models import CorrectionExample

    qs = (
        CorrectionExample.objects
        .select_related("pending_reply")
        .order_by("-created_at")
    )
    if persona_slug:
        qs = qs.filter(persona_slug=persona_slug)

    examples = list(qs[:limit])
    if not examples:
        return ""

    lines = ["", "=== EXEMPLES DE FEEDBACK RICHARD (apprends ce style) ==="]
    for ex in examples:
        pr = ex.pending_reply
        lines.append("---")
        if ex.kind == CorrectionExample.Kind.INSTRUCTION_ONLY:
            lines.append(f"CONSIGNE DE RICHARD : {ex.instruction[:400]}")
            target = (pr.final_sent or pr.ai_draft)[:400]
            lines.append(f"VERSION FINALE CONFORME : {target}")
        elif ex.kind == CorrectionExample.Kind.BOTH:
            lines.append(f"CONSIGNE DE RICHARD : {ex.instruction[:400]}")
            lines.append(f"AI a propose : {pr.ai_draft[:400]}")
            lines.append(f"Richard a envoye : {(pr.final_sent or '')[:400]}")
            if ex.explanation:
                lines.append(f"Raison : {ex.explanation}")
        else:  # TEXT_CORRECTION
            if not pr.final_sent:
                lines.pop()  # remove the "---" we added
                continue
            lines.append(f"AI a propose : {pr.ai_draft[:400]}")
            lines.append(f"Richard a envoye : {pr.final_sent[:400]}")
            if ex.explanation:
                lines.append(f"Raison : {ex.explanation}")
    return "\n".join(lines)


def _render_system_prompt(include_booking: bool) -> str:
    """Injecte signature + clause booking dans le system prompt."""
    if include_booking and conf.CALENDAR_BOOKING_URL:
        booking_clause = (
            "Tu PEUX inclure le lien de prise de RDV (voir exemple avec lien)."
        )
        booking_url = conf.CALENDAR_BOOKING_URL
    else:
        booking_clause = (
            "N'inclus PAS de lien de RDV dans ce message. On reste sur une simple "
            "invitation a echanger."
        )
        booking_url = ""
    return BASE_SYSTEM_PROMPT.format(
        signature_block=conf.render_signature(),
        booking_clause=booking_clause,
        booking_url=booking_url,
    )


def _build_user_message(
    public_id: str,
    profile_summary,
    chat_summary,
    recent_messages_text: str,
    first_name: str,
    instruction: str,
) -> str:
    """Compose le bloc utilisateur envoye a Claude."""
    first_name_display = first_name or "(inconnu — utiliser 'Bonjour,' sans prenom)"
    parts = [
        "Genere le message LinkedIn de follow-up pour ce prospect.",
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
            "=== CONSIGNE EXPLICITE DE RICHARD POUR CETTE VERSION ===",
            instruction.strip(),
            "Tu DOIS respecter cette consigne en plus des regles habituelles.",
        ]
    parts += [
        "",
        "Reponds UNIQUEMENT avec le message complet (4 blocs separes par une ligne vide).",
    ]
    return "\n".join(parts)


def generate_ekoalu_dm(
    *,
    public_id: str,
    profile_summary=None,
    chat_summary=None,
    recent_messages_text: str = "",
    persona_slug: str = "",
    include_booking: bool = False,
    instruction: str = "",
    model: str | None = None,
) -> str:
    """Genere un DM follow-up EKOALU structure (4 blocs + signature).

    Retourne le texte du message ou "" si la generation a echoue.
    """
    client = _get_anthropic_client()
    if not client:
        logger.warning("Pas d'Anthropic client, retour vide")
        return ""

    first_name = _extract_first_name(public_id, profile_summary, chat_summary)
    system = _render_system_prompt(include_booking) + _build_few_shot(persona_slug)
    user_msg = _build_user_message(
        public_id=public_id,
        profile_summary=profile_summary,
        chat_summary=chat_summary,
        recent_messages_text=recent_messages_text,
        first_name=first_name,
        instruction=instruction,
    )

    try:
        resp = client.messages.create(
            model=model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            max_tokens=900,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = (resp.content[0].text if resp.content else "").strip()
    except Exception as e:
        logger.exception("Erreur generation DM EKOALU : %s", e)
        return ""

    # Strip guillemets parasites en tete/queue
    text = text.strip().strip('"').strip("'").strip()
    # S assurer que la signature est presente (Claude peut l'oublier)
    sig = conf.render_signature()
    if conf.SIGNATURE_NAME not in text:
        text = f"{text}\n\n{sig}"
    return text


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
