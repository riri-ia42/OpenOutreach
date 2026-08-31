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
    WRONG_FIT = "wrong_fit"  # activité incompatible (« exclusivement revêtements de sols »)


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


# Activité incompatible : le prospect dit que son métier n'a pas de rapport.
# → sortie polie, pas de lien RDV, candidat à la sortie de prospection.
_PATTERNS_WRONG_FIT = [
    r"\b(?:nous faisons|on fait|nous sommes)\s+(?:exclusivement|uniquement)\b",
    r"\b(?:exclusivement|uniquement)\s+(?:dans|des|du|de la|de l')\b",
    r"\bne (?:faisons|fait|fabriquons|posons|traitons|vendons) (?:pas|plus|aucun)\b",
    r"\bpas (?:notre|mon) (?:activit[ée]|m[ée]tier|domaine|secteur|c[oe]ur de m[ée]tier)\b",
    r"\bhors de (?:notre|mon) (?:activit[ée]|p[ée]rim[èe]tre|domaine)\b",
    r"\b(?:pas|non) concern[ée]s?\b",
    r"\baucun rapport avec (?:notre|mon)\b",
    r"\bne (?:travaillons|travaille) pas (?:dans|sur|avec) ce\b",
    r"\bnous ne sommes pas (?:menuisiers?|fabricants?|poseurs?|dans)\b",
]

# Marqueurs de début de mail cité (réponse Outlook/Gmail) : tout ce qui suit est
# NOTRE propre message — le classifier ne doit JAMAIS le lire (bug 31/08 :
# « mon agenda en ligne » du cold mail cité faisait classer un refus en rdv_request).
_QUOTED_MARKERS = re.compile(
    r"(?im)^[>\s]*(?:de\s?:|from\s?:|-{2,}\s?(?:message d'origine|original message)"
    r"|le .{4,80} a [ée]crit\s?:|envoy[ée]\s?:|on .{4,80} wrote\s?:)",
)
# Corps aplati (Graph text sans sauts de ligne) : marqueurs à casse STRICTE
# pour ne pas couper sur du « de : » de prose française.
_QUOTED_MARKERS_INLINE = re.compile(
    r"\s(?:De\s?:\s|From\s?:\s|Envoy[ée]\s?:\s|-{2,}\s?Message d'origine)",
)


def strip_quoted_reply(text: str) -> str:
    """Ne garde que la partie écrite par l'expéditeur (coupe le fil cité)."""
    if not text:
        return ""
    cut = len(text)
    m = _QUOTED_MARKERS.search(text)
    if m:
        cut = min(cut, m.start())
    m2 = _QUOTED_MARKERS_INLINE.search(text)
    if m2:
        cut = min(cut, m2.start())
    stripped = text[:cut]
    # Filet : si le marqueur ouvre le message (transfert), garder l'original
    return stripped if stripped.strip() else text


def _any_match(text: str, patterns: list[str]) -> bool:
    """True si au moins un pattern matche text (case-insensitive)."""
    text_lower = text.lower()
    return any(re.search(p, text_lower) for p in patterns)


def classify_intent(text: str) -> Intent:
    """Classifie l intention d un message entrant.

    Ordre de priorité (le premier qui matche gagne) :
    1. OPT_OUT (priorité absolue — désinscription)
    2. WRONG_FIT (activité incompatible — avant RDV : un refus net prime)
    3. RDV_REQUEST (signal d'achat fort)
    4. OBJECTION
    5. TECHNICAL_QUESTION
    6. OFF_TOPIC (par défaut)

    Seule la partie écrite par l'expéditeur est analysée — le fil cité
    (notre propre cold mail) est coupé (bug rdv_request du 31/08).
    """
    if not text or not text.strip():
        return Intent.OFF_TOPIC
    text = strip_quoted_reply(text)

    if _any_match(text, _PATTERNS_OPT_OUT):
        return Intent.OPT_OUT
    if _any_match(text, _PATTERNS_WRONG_FIT):
        return Intent.WRONG_FIT
    if _any_match(text, _PATTERNS_RDV):
        return Intent.RDV_REQUEST
    if _any_match(text, _PATTERNS_OBJECTION):
        return Intent.OBJECTION
    if _any_match(text, _PATTERNS_TECHNICAL):
        return Intent.TECHNICAL_QUESTION

    return Intent.OFF_TOPIC
