"""Classifieur d'intention rule-based pour les messages entrants.

V1 : règles simples (mots-clés).
V2 : Claude API (plus fiable).

Décide entre 5 intentions qui conditionnent le brouillon de réponse :
- RDV_REQUEST       : prospect demande à se voir / appeler / visio
                      → le brouillon inclut le lien Booking
- TECHNICAL_QUESTION: question technique
                      → brouillon focus réponse technique, PAS de lien RDV
- OBJECTION         : objection à traiter
                      → brouillon avec contre-argument factuel
- OFF_TOPIC         : conversation hors sujet
                      → brouillon neutre court
- OPT_OUT           : demande de désabonnement
                      → status OPT_OUT permanent + accusé de réception
"""
from __future__ import annotations

import enum
import re


class Intent(str, enum.Enum):
    RDV_REQUEST = "rdv_request"
    TECHNICAL_QUESTION = "technical_question"
    OBJECTION = "objection"
    OFF_TOPIC = "off_topic"
    OPT_OUT = "opt_out"


# Patterns par intention (case-insensitive). Ordre important : OPT_OUT > RDV > OBJECTION > TECH.
_PATTERNS_OPT_OUT = [
    r"\bretir(?:er|e[zr]?)\b.{0,40}\b(?:liste|coordonn[ée]es?|contact)\b",
    r"\benlever\b.{0,30}\b(?:liste|contact)\b",
    r"\bd[ée]sabonner?\b",
    r"\bstop\s+messages?\b",
    r"\bne plus me contacter\b",
    r"\bpas int[ée]ress[ée]\b",
    r"\bmerci d['\s]?arr[êe]ter\b",
    r"\bunsubscribe\b",
]

_PATTERNS_RDV = [
    r"\b(rdv|rendez-vous|rendez vous)\b",
    r"\b(appeler|appel|call|visio|t[ée]l[ée]phone)\b",
    r"\b(disponibilit[ée]s?|cr[ée]neaux?|agenda|planning)\b",
    r"\bquand (peut-on|on peut|pouvez-vous|on se|se voir)\b",
    r"\bon (peut|pourrait) se (voir|parler|caler|appeler)\b",
    r"\bvous (me|nous) recevez\b",
    r"\b(rencontrer|rencontre|rencontrons)\b",
]

_PATTERNS_OBJECTION = [
    r"\btrop cher\b",
    r"\bcher pour nous\b",
    r"\bpas (?:le|notre|de) budget\b",
    r"\bhors budget\b",
    r"\bon a d[ée]j[àa] un fournisseur\b",
    r"\bon travaille (?:d[ée]j[àa] )?avec\b",
    r"\bd[ée]j[àa] (?:fait|sign[ée])\b",
    r"\bpas (?:le )?temps\b",
    r"\btrop occup[ée]\b",
    r"\bpas (?:notre|pour nous|une priorit[ée])\b",
]

_PATTERNS_TECHNICAL = [
    r"\b(EI ?\d{2,3}|coupe[- ]feu|d[ée]senfumage|denfc|pare[- ]balles?|BC[1-4])\b",
    r"\b(grandes? dim|grandes? dimensions?|rw|acoustique|poa)\b",
    r"\b(d[ée]tail|joint|seuil|rpt|aev|fdes|re2020)\b",
    r"\b(cortizo|sepalumic|sapa|wicona)\b",
    r"\bcomment (?:vous )?(faire|trait(?:er|ez)|g[ée]r(?:er|ez))\b",
    r"\bquelle (epaisseur|gamme|configuration|valeur)\b",
    r"\bquel (delta|coefficient)\b",
    r"\b(c['\s]?est) quoi le\b",
    r"\bjonction\b.{0,30}\b(b[ée]ton|charpente|menuiserie|alu)\b",
]


def _any_match(text: str, patterns: list[str]) -> bool:
    """True si au moins un pattern matche text (case-insensitive)."""
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in patterns)


def classify_intent(text: str) -> Intent:
    """Classifie l intention d un message entrant.

    Ordre de priorité (le premier qui matche gagne) :
    1. OPT_OUT (priorité absolue — désinscription)
    2. RDV_REQUEST (signal d'achat fort)
    3. OBJECTION
    4. TECHNICAL_QUESTION
    5. OFF_TOPIC (par défaut)
    """
    if not text or not text.strip():
        return Intent.OFF_TOPIC

    if _any_match(text, _PATTERNS_OPT_OUT):
        return Intent.OPT_OUT
    if _any_match(text, _PATTERNS_RDV):
        return Intent.RDV_REQUEST
    if _any_match(text, _PATTERNS_OBJECTION):
        return Intent.OBJECTION
    if _any_match(text, _PATTERNS_TECHNICAL):
        return Intent.TECHNICAL_QUESTION

    return Intent.OFF_TOPIC
