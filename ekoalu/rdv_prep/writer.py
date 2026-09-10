"""Rédaction par Claude du contenu du brief et de la slide persona (JSON).

Le modèle ne produit pas de HTML : il renvoie un JSON à champs fixes que
`render.py` place dans les bases validées par Richard le 09/09. Règles de
fond en dur dans le prompt (délai non chiffré, jamais « on pose », gammes
citables, aucun signe IA, factuel sans slogans).
"""
from __future__ import annotations

import json
import logging
import os
import re

from ekoalu.email_generator.generator import _DEFAULT_MODEL, _get_anthropic_client
from ekoalu.llm_usage.cache_blocks import build_system_blocks

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Tu prépares Richard Gros, président d'EKOALU (Chasselay, 69), à un rendez-vous
prospect en visio. EKOALU : fabricant de menuiseries aluminium, acier et bois technique,
15 personnes, atelier de 1 000 m², créé en 2014 ; conçoit, fabrique et livre, NE POSE PAS.
Produits techniques : coupe-feu E/EI 30 à 120 (alu Wicona, acier Jansen), désenfumage DENFC
NF EN 12101-2 avec D+H, pare-balles FB4/FB6 NS testés, façade rideau RC2/RC3 (jusqu'à 12 m²
et 680 kg), grandes dimensions (coulissant L 6 200 × H 2 800), acoustique Rw > 40 dB, cintrés.
Multi-gammiste partenaire Hydro (Wicona, SAPA, Technal) + Jansen. GIE de menuisiers
indépendants (achats, outils, compétences ; capacité au-delà de 300 châssis ; 650 tenus).
Zone : Lyon, Saint-Étienne, Roanne ; produits techniques dans toute la France.

Règles absolues :
- Factuel. Aucun slogan, aucune flatterie, aucune formule de valeurs. Phrases courtes.
- Jamais « on pose ». Jamais Cortizo ni Sepalumic. Aucun délai chiffré : « réponse rapide,
  selon la taille et la technicité du dossier, date annoncée à la demande et tenue ».
- Aucun tiret cadratin, aucune puce markdown, aucun emoji, aucun gras markdown dans les textes.
- Quand un fait manque, dis-le (« non confirmé », « à demander en ouverture »), n'invente rien.
- Si le motif du rendez-vous est inconnu, propose 2 ou 3 lectures et une question d'ouverture
  qui tranche ; le déroulé s'adapte à la réponse.

Réponds UNIQUEMENT par un objet JSON (pas de texte autour) avec exactement ces clés :
{
 "essentiel": {"qui": str, "pourquoi": str, "pourquoi_detail": str, "promis": str, "voulu": str},
 "lead_in": str,
 "personne": [[str, str], ...],           // 3 à 5 paires libellé / valeur
 "societe": [[str, str], ...],            // 3 à 6 paires
 "hypotheses": str,                        // 1 à 3 phrases
 "vigilance_contact": str,                 // ce qu'il ne faut pas oublier ni dire
 "deroule": [{"heure": "HH:MM", "minutes": int, "titre": str, "texte": str, "phrase": str}, ...],  // 4 à 5 étapes
 "questions": [{"groupe": str, "items": [[bool, str], ...]}, ...],  // bool = question clé
 "arguments": [[str, str, str], ...],     // s'il dit / réponse / preuve, 4 à 6 lignes
 "vigilances_specifiques": [str, ...],    // 1 à 3, propres à ce prospect
 "apres": [str, ...],                      // 3 à 5 actions après le RDV
 "deck": {"head": str, "eyebrow": str, "bullets": [[str, str], ...], "lead": str, "foot": str, "recevez": str}
   // deck.bullets : 6 paires (accroche courte, complément) = ce qu'EKOALU apporte à CE persona ;
   // deck.foot : ex "Pour un bureau d'études" ; deck.recevez : ce qu'il reçoit le soir même
}
"""


def _extract_json(text: str) -> dict:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("pas de JSON dans la réponse")
    return json.loads(m.group(0))


def write_brief(facts: dict, model: str | None = None, max_tokens: int = 6000) -> dict:
    """Renvoie le JSON du brief, ou {} si l'appel a échoué."""
    client = _get_anthropic_client()
    if client is None:
        logger.warning("Pas de client Anthropic : brief impossible")
        return {}
    model_id = model or os.environ.get("EKOALU_RDV_PREP_MODEL") or os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)
    user_msg = ("Faits collectés (JSON) : " + json.dumps(facts, ensure_ascii=False)
                + "\n\nHeure de début du rendez-vous : " + (facts.get("start_iso") or "?")
                + ". Durée : calcule depuis start_iso et end_iso. Rédige le brief.")
    try:
        resp = client.messages.create(
            model=model_id, max_tokens=max_tokens,
            system=build_system_blocks(SYSTEM_PROMPT, ""),
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = (resp.content[0].text if resp.content else "").strip()
        return _extract_json(raw)
    except Exception as exc:  # noqa: BLE001 — log + vide, le service marque « failed »
        logger.exception("Rédaction du brief impossible : %s", exc)
        return {}
