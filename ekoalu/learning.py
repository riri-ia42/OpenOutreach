"""Boucle d'apprentissage des messages : selection des CorrectionExample.

Corrige le silotage des slugs (audit 07/07) — 2 axes de filtrage :
- canal (linkedin_dm / email_cold / email_reply) : TOUJOURS filtre en premier
  (plus de contamination email → DM quand le slug persona est vide) ;
- persona_slug : optionnel, privilegie s'il y a assez d'exemples (>= 3).

Dedup : les consignes quasi identiques (difflib ratio > 0.9) ne comptent
qu'une fois dans la fenetre few-shot (8 exemples DISTINCTS les plus recents).
Les consignes recurrentes (>= 3 occurrences) sont promues en "REGLES APPRISES"
injectees systematiquement en tete du system prompt.

Chaque exemple reellement injecte est marque used_in_prompt=True (dashboard).
"""
from __future__ import annotations

import difflib
import logging

logger = logging.getLogger(__name__)

FEW_SHOT_LIMIT = 8
MIN_PERSONA_EXAMPLES = 3
RULE_MIN_OCCURRENCES = 3
RULE_MAX = 5
DEDUP_RATIO = 0.9
_SCAN_WINDOW = 200  # nb max d'exemples recents parcourus


def channel_for_outbound_kind(kind: str) -> str:
    """Mappe un OutboundKind vers le canal d'apprentissage CorrectionExample."""
    from ekoalu.inbox_assist.models import CorrectionExample

    if str(kind).startswith("email"):
        return CorrectionExample.Channel.EMAIL_COLD
    return CorrectionExample.Channel.LINKEDIN_DM


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


def _near_duplicate(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    # Prefixe commun (une consigne retapee avec un suffixe en plus) — seulement
    # sur des textes assez longs pour eviter les faux positifs courts.
    if min(len(a), len(b)) >= 20 and (a.startswith(b) or b.startswith(a)):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() > DEDUP_RATIO


def _dedup_key(example) -> str:
    """Cle de dedup : la consigne si presente, sinon le texte final corrige."""
    if example.instruction.strip():
        return _normalize(example.instruction)
    pr = example.pending_reply
    return _normalize(pr.final_sent or pr.ai_draft)


def select_examples(
    channel: str,
    persona_slug: str = "",
    limit: int = FEW_SHOT_LIMIT,
    mark_used: bool = True,
) -> list:
    """Jusqu'a `limit` exemples DISTINCTS les plus recents pour ce canal.

    Filtre canal d'abord ; le slug persona est privilegie s'il compte au
    moins MIN_PERSONA_EXAMPLES exemples (sinon fallback canal entier).
    """
    from ekoalu.inbox_assist.models import CorrectionExample

    base = (
        CorrectionExample.objects
        .filter(channel=channel)
        .select_related("pending_reply")
        .order_by("-created_at", "-pk")
    )
    qs = base
    if persona_slug:
        persona_qs = base.filter(persona_slug=persona_slug)
        if persona_qs.count() >= MIN_PERSONA_EXAMPLES:
            qs = persona_qs

    selected: list = []
    keys: list[str] = []
    for ex in qs[:_SCAN_WINDOW]:
        key = _dedup_key(ex)
        if key and any(_near_duplicate(key, k) for k in keys):
            continue
        selected.append(ex)
        if key:
            keys.append(key)
        if len(selected) >= limit:
            break
    if mark_used and selected:
        mark_used_in_prompt(selected)
    return selected


def mark_used_in_prompt(examples: list) -> None:
    """Marque les exemples reellement injectes (dashboard 'corrections utilisees')."""
    from ekoalu.inbox_assist.models import CorrectionExample

    ids = [e.pk for e in examples if not e.used_in_prompt]
    if ids:
        CorrectionExample.objects.filter(pk__in=ids).update(used_in_prompt=True)


def render_few_shot(examples: list) -> str:
    """Bloc few-shot commun (avant/apres + consignes + refus)."""
    from ekoalu.inbox_assist.models import CorrectionExample

    if not examples:
        return ""
    lines = ["", "=== EXEMPLES DE FEEDBACK RICHARD (apprends ce style) ==="]
    for ex in examples:
        pr = ex.pending_reply
        entry: list[str] = []
        if ex.kind == CorrectionExample.Kind.REJECTION:
            entry.append(f"AI a propose : {pr.ai_draft[:400]}")
            entry.append(f"Richard a REFUSE ce message. Motif : {ex.instruction[:400]}")
        elif ex.kind == CorrectionExample.Kind.INSTRUCTION_ONLY:
            entry.append(f"CONSIGNE DE RICHARD : {ex.instruction[:400]}")
            entry.append(f"VERSION FINALE CONFORME : {(pr.final_sent or pr.ai_draft)[:400]}")
        elif ex.kind == CorrectionExample.Kind.BOTH:
            entry.append(f"CONSIGNE DE RICHARD : {ex.instruction[:400]}")
            entry.append(f"AI a propose : {pr.ai_draft[:400]}")
            entry.append(f"Richard a envoye : {(pr.final_sent or '')[:400]}")
            if ex.explanation:
                entry.append(f"Raison : {ex.explanation}")
        else:  # TEXT_CORRECTION
            if not pr.final_sent:
                continue
            entry.append(f"AI a propose : {pr.ai_draft[:400]}")
            entry.append(f"Richard a envoye : {pr.final_sent[:400]}")
            if ex.explanation:
                entry.append(f"Raison : {ex.explanation}")
        lines.append("---")
        lines.extend(entry)
    return "\n".join(lines)


def build_few_shot(channel: str, persona_slug: str = "", limit: int = FEW_SHOT_LIMIT) -> str:
    """Raccourci : selection + rendu + marquage used_in_prompt."""
    return render_few_shot(select_examples(channel, persona_slug=persona_slug, limit=limit))


def learned_rules(
    channel: str,
    min_occurrences: int = RULE_MIN_OCCURRENCES,
    max_rules: int = RULE_MAX,
) -> list[str]:
    """Consignes recurrentes (>= min_occurrences quasi-identiques) du canal.

    Clusterise les consignes par quasi-similarite ; le representant est la
    consigne la plus recente du cluster.
    """
    from ekoalu.inbox_assist.models import CorrectionExample

    instructions = (
        CorrectionExample.objects
        .filter(channel=channel)
        .exclude(instruction="")
        .order_by("-created_at", "-pk")
        .values_list("instruction", flat=True)[:_SCAN_WINDOW]
    )
    clusters: list[list] = []  # [cle_normalisee, representant, count]
    for instr in instructions:
        key = _normalize(instr)
        if not key:
            continue
        for cluster in clusters:
            if _near_duplicate(key, cluster[0]):
                cluster[2] += 1
                break
        else:
            clusters.append([key, instr.strip(), 1])
    return [rep for _, rep, n in clusters if n >= min_occurrences][:max_rules]


def learned_rules_block(channel: str) -> str:
    """Section 'REGLES APPRISES' a injecter EN TETE du system prompt."""
    rules = learned_rules(channel)
    if not rules:
        return ""
    lines = [
        "=== REGLES APPRISES (consignes recurrentes de Richard — "
        "a respecter SYSTEMATIQUEMENT) ===",
    ]
    lines += [f"- {r}" for r in rules]
    return "\n".join(lines) + "\n\n"
