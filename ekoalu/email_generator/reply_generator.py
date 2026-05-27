"""Génération de brouillons de réponse email via Claude (inbound_email → reply).

Conditionne le brouillon selon l'intent classifié :
- RDV_REQUEST       → propose un créneau visio + lien Booking
- TECHNICAL_QUESTION → réponse technique factuelle, pas de relance
- OBJECTION         → contre-argument factuel court, pas de pression
- OFF_TOPIC         → accusé de réception court
- OPT_OUT           → confirmation désinscription (1 phrase)
"""
from __future__ import annotations

import logging
import os

from ekoalu import conf
from ekoalu.email_generator.generator import _get_anthropic_client
from ekoalu.email_generator.models import ColdEmailDraft
from ekoalu.inbox_assist.intent_classifier import Intent

logger = logging.getLogger(__name__)


_DEFAULT_MODEL = "claude-sonnet-4-6"

_BASE_SYSTEM_PROMPT = """Tu rédiges des brouillons de réponse email pour Richard Gros,
Président d'EKOALU (menuiserie aluminium, acier et bois technique, Chasselay 69).

Tu réponds à un message reçu d'un prospect tertiaire (entreprise B2B). Le message
entrant t'est fourni avec son intent déjà classifié.

INTENT DÉTECTÉ : {intent}
INSTRUCTION SPÉCIFIQUE POUR CET INTENT :
{intent_guidance}

CONTRAINTES GÉNÉRALES :
- Tonalité cordial-pro DIRECTE, jamais ampoulée. Vouvoiement.
- 3-6 lignes max (signature comprise), pas de blabla.
- AUCUNE flatterie ("merci pour votre retour rapide", "votre intérêt me touche"...).
- AUCUN jargon : INTERDITS = synergies, ROI, win-win, leader, référence, excellence,
  passion, à l'écoute, permettez-moi, j'aurais le plaisir, n'hésitez surtout pas,
  au plaisir d'échanger, restant à votre disposition, dans l'attente.
- Si signature attendue (intent ≠ OPT_OUT), reproduis EXACTEMENT le bloc :
{signature_block}

FORMAT DE SORTIE (STRICT) :
Tu réponds UNIQUEMENT par 2 balises XML, sans autre texte autour :

<sujet>
[Objet — préfixé "Re: " suivi du sujet original. Garde court (<= 80 car).]
</sujet>

<corps>
[Salutation courte + corps adapté à l'intent + signature si applicable.]
</corps>

BOOKING_URL : {booking_url_or_none}
"""

_INTENT_GUIDANCE = {
    Intent.RDV_REQUEST: (
        "Le prospect veut un RDV. Confirme sobrement que tu es OK pour 15-20 min "
        "en visio. Inclus le lien Booking. PAS de téléphone. PAS de questions "
        "pré-RDV (on découvre en visio)."
    ),
    Intent.TECHNICAL_QUESTION: (
        "Le prospect pose une question technique. Réponds factuellement, court "
        "(2-3 phrases techniques max). Si la question est complexe ou nécessite "
        "des plans, propose d'en discuter en visio (sans pression)."
    ),
    Intent.OBJECTION: (
        "Le prospect émet une objection (budget, fournisseur existant, timing...). "
        "Reconnais l'objection sans la contester, puis pose UN fait factuel court "
        "sur ce qu'EKOALU fait différemment (ex : atelier intégré, gamme niche). "
        "Pas de relance commerciale."
    ),
    Intent.OFF_TOPIC: (
        "Message hors sujet ou très court (genre 'bien reçu', 'merci'). Réponds "
        "par une phrase d'accusé de réception neutre. Pas de relance."
    ),
    Intent.OPT_OUT: (
        "Le prospect demande à être désinscrit. UNE seule phrase de confirmation : "
        "'C'est noté, je vous retire de notre base, plus aucune relance de notre "
        "part. Bonne continuation.' PAS DE SIGNATURE complète, juste 'Cordialement, "
        "Richard Gros' en fin."
    ),
}


def _render_reply_system_prompt(intent: Intent) -> str:
    booking = conf.CALENDAR_BOOKING_URL or ""
    return _BASE_SYSTEM_PROMPT.format(
        intent=intent.value,
        intent_guidance=_INTENT_GUIDANCE.get(intent, "Réponds sobrement, sans relance."),
        signature_block=conf.render_signature(),
        booking_url_or_none=booking or "(aucun lien fourni, ne pas inclure de lien)",
    )


def _build_user_message(*, inbound_subject: str, inbound_message: str,
                        entreprise: str, dirigeant: str) -> str:
    parts = [
        "Voici le message entrant à traiter :",
        "",
        f"Sujet original : {inbound_subject or '(sans sujet)'}",
        f"Expéditeur : {dirigeant or '(inconnu)'} ({entreprise or '(entreprise inconnue)'})",
        "",
        "Corps du message :",
        "---",
        inbound_message.strip() or "(corps vide)",
        "---",
        "",
        "Réponds UNIQUEMENT avec les balises <sujet>...</sujet> et <corps>...</corps>.",
    ]
    return "\n".join(parts)


def generate_email_reply(
    *,
    intent: Intent,
    inbound_subject: str,
    inbound_message: str,
    entreprise: str = "",
    dirigeant: str = "",
    model: str | None = None,
    max_tokens: int = 600,
) -> ColdEmailDraft:
    """Génère un brouillon de réponse email pour le message entrant.

    Retourne `ColdEmailDraft(subject="", body="")` si la génération échoue.
    """
    client = _get_anthropic_client()
    if not client:
        logger.warning("Pas de client Anthropic, génération reply impossible")
        return ColdEmailDraft(subject="", body="")

    system = _render_reply_system_prompt(intent)
    user_msg = _build_user_message(
        inbound_subject=inbound_subject,
        inbound_message=inbound_message,
        entreprise=entreprise,
        dirigeant=dirigeant,
    )
    model_id = model or os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)

    try:
        resp = client.messages.create(
            model=model_id,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = (resp.content[0].text if resp.content else "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Échec génération reply : %s", exc)
        return ColdEmailDraft(subject="", body="")

    from ekoalu.email_generator.generator import parse_response
    draft = parse_response(raw)
    if not draft.is_valid():
        logger.warning("Reply parse vide (subject=%r, body chars=%d)",
                       draft.subject, len(draft.body))
        return draft

    # Normalise le préfixe "Re:" si absent
    if not draft.subject.lower().startswith("re:"):
        draft.subject = f"Re: {draft.subject}"
    draft.model_used = model_id
    return draft
