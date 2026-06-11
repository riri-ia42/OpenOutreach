"""Génération de cold mails EKOALU via Claude (Sonnet 4.6 par défaut).

Calque sur `ekoalu/follow_up/generator.py` :
- récupération client Anthropic (env var puis SiteConfig fallback)
- appel `messages.create` avec system prompt + user message
- parsing de la réponse balisée <sujet>/<corps>
- fallback signature en post-traitement
"""
from __future__ import annotations

import logging
import os
import re

from ekoalu import conf
from ekoalu.email_generator.models import ColdEmailDraft
from ekoalu.email_generator.prompts import (
    DEFAULT_VARIANT,
    build_user_message,
    pick_variant,
    render_system_prompt,
)

logger = logging.getLogger(__name__)


_DEFAULT_MODEL = "claude-sonnet-4-6"
_SUJET_RE = re.compile(r"<sujet>\s*(.+?)\s*</sujet>", re.DOTALL | re.IGNORECASE)
_CORPS_RE = re.compile(r"<corps>\s*(.+?)\s*</corps>", re.DOTALL | re.IGNORECASE)


def _get_anthropic_client():
    """Retourne un client Anthropic ou None si pas d'API key."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        try:
            from linkedin.models import SiteConfig
            cfg = SiteConfig.load()
            api_key = cfg.llm_api_key or ""
        except Exception:  # noqa: BLE001 — bootstrap, on log + None
            logger.exception("SiteConfig indisponible")
            return None
    if not api_key:
        return None
    try:
        from anthropic import Anthropic
        return Anthropic(api_key=api_key)
    except ImportError:
        logger.error("anthropic SDK non installé")
        return None


def parse_response(raw: str) -> ColdEmailDraft:
    """Extrait subject + body depuis la réponse balisée. Vide si parse foiré."""
    if not raw:
        return ColdEmailDraft(subject="", body="")
    sujet_match = _SUJET_RE.search(raw)
    corps_match = _CORPS_RE.search(raw)
    subject = sujet_match.group(1).strip() if sujet_match else ""
    body = corps_match.group(1).strip() if corps_match else ""
    return ColdEmailDraft(subject=subject, body=body)


def _ensure_closing(body: str) -> str:
    """Filet de sécurité charte : garantit la clôture "Bien à vous, Richard Gros".

    Le bloc coordonnées n'est PAS ajouté ici (apposé en HTML par le sender,
    cf. SIGNATURES.md §3). On corrige juste un Claude qui aurait oublié la
    clôture ou glissé un "Cordialement" banni.
    """
    if not body:
        return body
    cleaned = body.rstrip()
    if "Bien à vous" in cleaned:
        return cleaned
    # Retire une clôture interdite éventuelle ("Cordialement," + nom) avant
    # d'apposer la clôture charte.
    lines = cleaned.splitlines()
    while lines and lines[-1].strip() in ("Richard Gros", "Richard", ""):
        lines.pop()
    while lines and lines[-1].strip().rstrip(",") in ("Cordialement", "Bien cordialement"):
        lines.pop()
    cleaned = "\n".join(lines).rstrip()
    return f"{cleaned}\n\n{conf.EMAIL_CLOSING_FORMAL_FIRST}"


def _ensure_booking(body: str) -> str:
    """Garantit la proposition RDV 15-30 min avec le lien Bookings OFFICIEL.

    Le prompt impose le marqueur [LIEN_RDV] (le LLM n'écrit jamais d'URL →
    zéro hallucination possible). Ici : remplacement du marqueur par
    conf.CALENDAR_BOOKING_URL ; si le marqueur manque (LLM distrait), on
    insère la phrase RDV standard juste avant la clôture.
    """
    if not body:
        return body
    url = conf.CALENDAR_BOOKING_URL
    if "[LIEN_RDV]" in body:
        return body.replace("[LIEN_RDV]", url)
    if url in body:
        return body
    sentence = (
        "Si le sujet vous parle, 15-30 min en visio suffisent — "
        f"mon agenda en ligne : {url}"
    )
    lines = body.rstrip().splitlines()
    idx = next((i for i, line in enumerate(lines) if "Bien à vous" in line), None)
    if idx is None:
        return f"{body.rstrip()}\n\n{sentence}"
    return "\n".join(lines[:idx] + [sentence, ""] + lines[idx:])


def generate_cold_email(
    *,
    entreprise: str = "",
    dirigeant: str = "",
    code_naf: str = "",
    activite: str = "",
    ville: str = "",
    dpt: str = "",
    effectif_min: int = 0,
    effectif_max: int = 0,
    model: str | None = None,
    max_tokens: int = 900,
    variant: str | None = None,
    instruction: str = "",
) -> ColdEmailDraft:
    """Génère un cold mail EKOALU pour les données prospect fournies.

    Args:
        variant: id de la variante de prompt à utiliser (cf. PROMPT_VARIANTS).
            Si None, tirage aléatoire pondéré via pick_variant() (A/B testing).

    Retourne `ColdEmailDraft(subject="", body="")` si la génération a échoué.
    `variant_used` est rempli systématiquement (même en cas d'échec) pour audit.
    """
    chosen_variant = variant or pick_variant()

    client = _get_anthropic_client()
    if not client:
        logger.warning("Pas de client Anthropic, génération impossible")
        return ColdEmailDraft(subject="", body="", variant_used=chosen_variant)

    system = render_system_prompt(chosen_variant)
    user_msg = build_user_message(
        entreprise=entreprise, dirigeant=dirigeant, code_naf=code_naf,
        activite=activite, ville=ville, dpt=dpt,
        effectif_min=effectif_min, effectif_max=effectif_max,
    )
    if instruction.strip():
        user_msg += (
            "\n\nCONSIGNE DE RÉGÉNÉRATION (priorité absolue, demandée par Richard) :\n"
            f"{instruction.strip()}\n"
            "Rappel : le seul lien autorisé reste le marqueur [LIEN_RDV] tel quel."
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
    except Exception as exc:  # noqa: BLE001 — on log + retourne vide pour que l'appelant skip
        logger.exception("Échec génération cold mail : %s", exc)
        return ColdEmailDraft(subject="", body="", variant_used=chosen_variant)

    draft = parse_response(raw)
    draft.variant_used = chosen_variant
    if not draft.is_valid():
        logger.warning("Cold mail parse vide ou incomplet (subject=%r, body chars=%d)",
                       draft.subject, len(draft.body))
        return draft

    draft.body = _ensure_booking(_ensure_closing(draft.body))
    draft.model_used = model_id
    return draft


# --- Helpers pour validation post-gen ---------------------------------------

_NICHE_PATTERN = re.compile(
    r"\b(coupe[- ]feu|EI\s*\d+|d[eé]senfumage|denfc|pare[- ]balles?|"
    r"BC[1-4]|mur[- ]rideau|grandes? dim(?:ensions?)?|acoustique|Rw\s*[>=]?\s*\d+|POA)\b",
    re.IGNORECASE,
)


def has_niche_mention(text: str) -> bool:
    """True si le texte mentionne au moins 1 produit niche EKOALU."""
    return bool(_NICHE_PATTERN.search(text or ""))
