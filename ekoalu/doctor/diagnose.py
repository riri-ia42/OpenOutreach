"""Appel Claude pour produire un diagnostic JSON structure.

V0 : SDK Anthropic direct (pas pydantic-ai) -- le doctor doit etre robuste
meme si pydantic-ai casse. 1 appel, JSON-mode strict via prompt explicite.

Le ContextVar LLM_CONTEXT_VAR est pose a 'ekoalu_doctor' pour que le tracker
ClaudeUsageLog tagge ces appels separement (audit budget doctor).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

BIBLE_PATH = Path(__file__).resolve().parent / "bible.md"
DEFAULT_MODEL = "claude-sonnet-4-6"

# Cap de cout par appel diagnostic. Sonnet 4.6 = 3$/Mtoken input, 15$/Mtoken
# output. Un appel typique fait ~5k input + 1k output = ~0.03$. On laisse une
# marge x30 (max 1$) avant de couper net.
MAX_COST_USD_PER_DIAGNOSIS = 1.0


class DiagnosisError(RuntimeError):
    """Echec de l'appel ou parsing du diagnostic."""


def _load_bible() -> str:
    return BIBLE_PATH.read_text(encoding="utf-8")


def _signature_fallback(diagnosis_text: str) -> str:
    """Si Claude oublie de remplir signature, on hash le diagnosis."""
    h = hashlib.sha1(diagnosis_text.encode("utf-8")).hexdigest()
    return f"auto_{h[:12]}"


def diagnose(context: dict) -> tuple[dict, float]:
    """Appelle Claude avec le contexte + la bible, parse le JSON de reponse.

    Retourne (parsed_diagnosis_dict, cost_usd).

    Raise DiagnosisError si: API down, JSON invalide, schema manquant.
    """
    from linkedin.llm import LLM_CONTEXT_VAR

    LLM_CONTEXT_VAR.set("ekoalu_doctor")

    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise DiagnosisError(f"anthropic SDK manquant: {exc}") from exc

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise DiagnosisError("ANTHROPIC_API_KEY absent de l'environnement")

    model = os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

    bible = _load_bible()
    user_payload = json.dumps(context, ensure_ascii=False, indent=2, default=str)

    # System prompt long (bible) -> cache_control auto via le wrapper patch.py
    # (declenche au-dessus de ~4000 chars). La bible fait ~4500 chars -> on
    # bascule en cache des le 2eme incident, gain 90% sur l'input apres.
    client = Anthropic(api_key=api_key, max_retries=2)

    logger.info("Doctor: appel Claude diagnostic (model=%s, context size=%d chars)",
                model, len(user_payload))
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=2048,
            system=bible,
            messages=[{
                "role": "user",
                "content": (
                    "Analyse ce snapshot du systeme prospection-ia et produis "
                    "un diagnostic JSON conforme au schema de la bible. Ne mets "
                    "AUCUN texte avant ou apres le JSON.\n\n"
                    "## Snapshot\n```json\n" + user_payload + "\n```"
                ),
            }],
        )
    except Exception as exc:
        raise DiagnosisError(f"Appel Claude echoue: {exc}") from exc

    # Cout de l'appel (le wrapper patch.py va aussi logger en DB, mais on a
    # besoin de le connaitre ici pour le cap par incident)
    from ekoalu.llm_usage.pricing import compute_cost_usd
    usage = resp.usage
    cost = compute_cost_usd(
        model,
        getattr(usage, "input_tokens", 0) or 0,
        getattr(usage, "output_tokens", 0) or 0,
        getattr(usage, "cache_creation_input_tokens", 0) or 0,
        getattr(usage, "cache_read_input_tokens", 0) or 0,
    )

    if cost > MAX_COST_USD_PER_DIAGNOSIS:
        raise DiagnosisError(
            f"Cap cout doctor depasse ({cost:.4f} USD > {MAX_COST_USD_PER_DIAGNOSIS} USD)"
        )

    # Concat des blocs de texte (en pratique 1 seul bloc)
    text = "".join(block.text for block in resp.content if hasattr(block, "text")).strip()

    # Strip eventuels backticks markdown que Claude ajouterait malgre la consigne
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DiagnosisError(
            f"JSON invalide retourne par Claude: {exc} -- snippet: {text[:200]}",
        ) from exc

    # Validation minimale du schema
    required = ("diagnosis", "root_cause", "confidence", "actions", "advisory_summary")
    missing = [k for k in required if k not in parsed]
    if missing:
        raise DiagnosisError(f"Champs manquants dans le diagnostic: {missing}")

    if not parsed.get("signature"):
        parsed["signature"] = _signature_fallback(parsed["diagnosis"])

    # Garde-fou type
    try:
        parsed["confidence"] = float(parsed["confidence"])
    except (TypeError, ValueError):
        parsed["confidence"] = 0.5

    if not isinstance(parsed.get("actions"), list):
        parsed["actions"] = []

    return parsed, cost


# Whitelist des action_type acceptes -- utilise par le command pour filtrer
# les propositions hors-piste (Claude pourrait inventer un type).
WHITELIST_ACTIONS = {
    "toggle_kill_switch_on",
    "toggle_kill_switch_off",
    "kill_zombie_python",
    "rotate_daemon_log",
    "restart_daemon",
    "reset_watchdog_state",
    "wait_and_recheck",
    "mail_advisory",
}


def filter_actions(actions: list) -> tuple[list, list]:
    """Separe les actions whitelist (OK) des hors-piste.

    Retourne (ok, rejected) ou rejected porte un commentaire 'reason'.
    """
    ok: list = []
    rejected: list = []
    for action in actions:
        atype = action.get("action_type") if isinstance(action, dict) else None
        if atype in WHITELIST_ACTIONS:
            ok.append(action)
        else:
            r = dict(action) if isinstance(action, dict) else {"action_type": str(action)}
            r["reason"] = f"Hors whitelist: {atype!r}"
            rejected.append(r)
    return ok, rejected
