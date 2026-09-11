"""Générateur de la relance mail (fiche hub #250 + consigne Richard 09/09).

Un message court (3 à 5 lignes), dans le fil du cold mail, qui ne répète pas
le premier angle : il dit qu'EKOALU fabrique aussi la menuiserie standard en
plus des produits sécurité incendie, donc peut traiter un chantier dans sa
globalité, et propose de tester un chiffrage sur un dossier en cours. Il
s'adapte au premier mail (le corps du cold mail est fourni au modèle).
Style : charte jumeau (phrases courtes, chiffres, pas de jargon, pas de
flatterie, pas de signes IA), clôture « Bien à vous, Richard », jamais
« on pose », jamais Cortizo ni Sepalumic, aucun délai chiffré.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from ekoalu import conf
from ekoalu.email_generator.generator import _DEFAULT_MODEL, _get_anthropic_client
from ekoalu.llm_usage.cache_blocks import build_system_blocks

logger = logging.getLogger(__name__)

FOLLOWUP_SYSTEM_PROMPT = """Tu écris pour Richard Gros, président d'EKOALU (Chasselay, 69), fabricant
de menuiseries aluminium, acier et bois technique, 15 personnes, atelier intégré.
EKOALU conçoit, fabrique et livre ; EKOALU ne pose pas.

Tâche : une RELANCE, unique, envoyée dans le fil du premier mail resté sans réponse.

Règles :
- 3 à 5 lignes, pas plus. Pas de rappel du premier mail, pas d'excuse, pas d'insistance.
- Un AUTRE angle que le premier mail : EKOALU fabrique aussi les menuiseries
  STANDARD (fenêtres, portes, coulissants, façade) en plus des produits sécurité
  incendie (coupe-feu, désenfumage). Cela permet de traiter un chantier dans sa
  globalité avec un seul fabricant.
- Proposer concrètement de tester un chiffrage sur un dossier en cours : le
  prospect envoie un CCTP ou une liste de châssis, EKOALU renvoie un prix budget.
  Aucun délai chiffré : « rapidement, selon la taille et la technicité du dossier ».
- S'adapter au métier du destinataire et au contenu du premier mail (ne pas répéter
  ce qu'il disait, s'appuyer dessus).
- Style Richard Gros : phrases courtes, direct, un terme technique si utile, aucun
  jargon commercial (synergies, partenariat gagnant-gagnant, à l'écoute...), aucune
  flatterie, aucun tiret cadratin, aucune puce, aucun emoji, aucun gras.
- Jamais « on pose ». Gammes citables : Hydro (Wicona, SAPA, Technal), Jansen.
  Ne jamais citer Cortizo ni Sepalumic.
- Salutation : « Bonjour Monsieur X, » ou « Bonjour Madame X, » si le nom est connu
  et fiable, sinon « Bonjour, ». Clôture exacte : « Bien à vous,\\nRichard ».

Réponds UNIQUEMENT avec le corps du message entre les balises <corps> et </corps>.
"""

_CORPS_RE = re.compile(r"<corps>(.*?)</corps>", re.DOTALL | re.IGNORECASE)


@dataclass
class FollowupDraft:
    body: str
    model_used: str = ""

    def is_valid(self) -> bool:
        return len(self.body.strip()) > 40


def _user_message(*, entreprise: str, dirigeant: str, code_naf: str, activite: str,
                  ville: str, original_subject: str, original_body: str,
                  instruction: str = "") -> str:
    parts = [
        f"Destinataire : {dirigeant or 'inconnu'} — {entreprise or 'société inconnue'} "
        f"({code_naf or 'NAF ?'}, {activite or ''}, {ville or ''})",
        f"Premier mail envoyé, objet : {original_subject}",
        "Premier mail envoyé, corps :",
        original_body.strip(),
    ]
    if instruction:
        parts.append(f"Consigne supplémentaire : {instruction}")
    return "\n\n".join(parts)


def _ensure_closing(body: str) -> str:
    cleaned = body.rstrip()
    if "Bien à vous" in cleaned:
        return cleaned
    lines = cleaned.splitlines()
    while lines and lines[-1].strip().rstrip(",") in ("Richard", "Richard Gros", "Cordialement",
                                                       "Bien cordialement", ""):
        lines.pop()
    return "\n".join(lines).rstrip() + "\n\n" + conf.EMAIL_CLOSING_FORMAL


def generate_email_followup(*, entreprise: str = "", dirigeant: str = "", code_naf: str = "",
                            activite: str = "", ville: str = "", original_subject: str = "",
                            original_body: str = "", instruction: str = "",
                            contact_email: str = "",
                            model: str | None = None, max_tokens: int = 700) -> FollowupDraft:
    """Renvoie FollowupDraft(body="") si la génération a échoué.

    `contact_email` : adresse de destination, pour la garde de salutation.
    """
    from ekoalu.email_generator.salutation import (
        clean_person_name,
        company_confirmed_by_email,
        dirigeant_for_salutation,
    )
    from ekoalu.message_validator.style_guard import enforce_style

    # Garde de salutation (capture Richard 11/09) : la relance nommait le
    # dirigeant du registre même quand l'adresse désigne quelqu'un d'autre
    # (« Bonjour M. Duchateau » vers ablampey@blampey.fr) ou quand le champ
    # porte un cabinet comptable. Même point de passage que le cold mail.
    dirigeant = clean_person_name(dirigeant_for_salutation(dirigeant, contact_email, entreprise))
    if entreprise and contact_email and not company_confirmed_by_email(entreprise, contact_email):
        logger.info("Société %r non confirmée par %s — nom retiré du prompt de relance",
                    entreprise, contact_email)
        entreprise = ""

    client = _get_anthropic_client()
    if client is None:
        logger.warning("Pas de client Anthropic, relance impossible")
        return FollowupDraft(body="")
    model_id = model or os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)
    system = build_system_blocks(FOLLOWUP_SYSTEM_PROMPT, "")
    user_msg = _user_message(
        entreprise=entreprise, dirigeant=dirigeant, code_naf=code_naf, activite=activite,
        ville=ville, original_subject=original_subject, original_body=original_body,
        instruction=instruction,
    )

    def _once(extra: str = "") -> str:
        msg = user_msg + (f"\n\nConsigne supplémentaire : {extra}" if extra else "")
        try:
            resp = client.messages.create(
                model=model_id, max_tokens=max_tokens, system=system,
                thinking={"type": "disabled"},
                messages=[{"role": "user", "content": msg}],
            )
            raw = (resp.content[0].text if resp.content else "").strip()
        except Exception as exc:  # noqa: BLE001 — log + vide, l'appelant skippe
            logger.exception("Échec génération relance mail : %s", exc)
            return ""
        m = _CORPS_RE.search(raw)
        return (m.group(1) if m else raw).strip()

    body = _once()
    if not body:
        return FollowupDraft(body="")
    body = enforce_style(body, _once, channel="email_cold")
    return FollowupDraft(body=_ensure_closing(body), model_used=model_id)
