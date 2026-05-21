"""Monkey-patch des appels Anthropic pour logger usage + cout + prompt caching.

Applique au boot Django via ekoalu.apps.EkoaluConfig.ready().
Couvre QUATRE surfaces pour ne rien rater :
1. `anthropic.Anthropic().messages.create` (SDK sync stable)
2. `anthropic.AsyncAnthropic().messages.create` (SDK async stable)
3. `anthropic.resources.beta.messages.Messages.create` (+ AsyncMessages) -- utilise
   par pydantic_ai qui passe par le namespace beta pour cache_control + thinking.
4. `pydantic_ai.models.anthropic.AnthropicModel._messages_create` -- filet de secu
   au cas ou pydantic_ai stocke des references precoces aux methodes patchees.

Sans (3) et (4) on rate ~96% du trafic (verifie 2026-05-21 : 29 appels SDK
trackes contre 8.5$ facturees reels).

Le wrapper applique aussi le PROMPT CACHING automatique : si `system` >= ~1024
tokens (~4000 chars), on ajoute `cache_control: ephemeral` sur le dernier bloc.
Gain estime : 30-50% sur les system prompts ICP/persona repetes (qualif, suggester,
follow_up, invitation) sans aucun changement metier. Desactivable via
EKOALU_ENABLE_PROMPT_CACHE=false.
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

_PATCH_APPLIED = False
_PYDANTIC_PATCH_APPLIED = False

# Seuil minimum Anthropic pour cache_control = 1024 tokens. On prend 4000 chars
# comme proxy en FR (~1 token tous les 4 chars).
_CACHE_MIN_CHARS = 4000


def _cache_enabled() -> bool:
    return os.environ.get("EKOALU_ENABLE_PROMPT_CACHE", "true").lower() in ("1", "true", "yes")


def _inject_cache_control(kwargs: dict) -> dict:
    """Ajoute cache_control au system prompt si gros et pas deja present.

    Cible : system prompts ICP/persona repetes a chaque appel.
    Effet : facture l'ecriture 1 fois (125% prix input) puis lectures a 10% du prix.
    TTL : 5 min par defaut. Si appels en rafale (qualif batch), gain maximal.
    """
    if not _cache_enabled():
        return kwargs
    system = kwargs.get("system")
    if not system:
        return kwargs

    if isinstance(system, str):
        if len(system) >= _CACHE_MIN_CHARS:
            kwargs["system"] = [{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }]
    elif isinstance(system, list) and system:
        # Verifie qu'aucun bloc n'a deja cache_control (sinon on respecte le caller)
        if not any(isinstance(b, dict) and b.get("cache_control") for b in system):
            last = system[-1]
            if (isinstance(last, dict)
                    and last.get("type") == "text"
                    and len(last.get("text", "")) >= _CACHE_MIN_CHARS):
                new_last = dict(last)
                new_last["cache_control"] = {"type": "ephemeral"}
                kwargs["system"] = list(system[:-1]) + [new_last]
    return kwargs


def _safe_log(model, usage_obj, duration_ms, context=""):
    """Enregistre l'usage en DB (silent fail si pb)."""
    try:
        from ekoalu.llm_usage.models import ClaudeUsageLog
        from ekoalu.llm_usage.pricing import compute_cost_usd

        input_tokens = int(getattr(usage_obj, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage_obj, "output_tokens", 0) or 0)
        cache_creation = int(getattr(usage_obj, "cache_creation_input_tokens", 0) or 0)
        cache_read = int(getattr(usage_obj, "cache_read_input_tokens", 0) or 0)
        cost = compute_cost_usd(model, input_tokens, output_tokens, cache_creation, cache_read)
        ClaudeUsageLog.objects.create(
            model=model or "unknown",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
            cost_usd=cost,
            context=context[:64],
            duration_ms=duration_ms,
        )
    except Exception as exc:
        logger.debug("ClaudeUsageLog write failed: %s", exc)


def _guess_context() -> str:
    """Devine le contexte d'appel via la stack (sans crash).

    Scanne plus profond (2..15) pour gerer les wrappers pydantic_ai (request ->
    _messages_create -> client.beta.messages.create) qui ajoutent 4-6 frames.
    """
    try:
        import inspect
        for frame_info in inspect.stack()[2:15]:
            fname = frame_info.filename.replace("\\", "/").lower()
            if "ekoalu/inbox_assist" in fname:
                return "inbox_assist"
            if "ekoalu/outbound_validation" in fname:
                return "outbound_invitation"
            if "ekoalu/company_validation" in fname:
                return "company_suggester"
            if "linkedin/agents/follow_up" in fname:
                return "follow_up_agent"
            if "ekoalu/follow_up" in fname:
                return "follow_up_ekoalu"
            if "linkedin/pipeline/qualify" in fname or "linkedin/pipeline/qualif" in fname:
                return "qualifier"
            if "linkedin/ml/qualifier" in fname:
                return "qualifier_ml"
            if "linkedin/db/summaries" in fname:
                return "summary"
            if "linkedin/pipeline/search_keywords" in fname:
                return "search_keywords"
            if "linkedin/daemon" in fname:
                return "daemon_reconcile"
            if "ekoalu/sourcing_filter" in fname:
                return "sourcing_filter"
    except Exception:
        pass
    return ""


def _make_sync_wrapper(original):
    def patched(self, *args, **kwargs):
        kwargs = _inject_cache_control(kwargs)
        t0 = time.perf_counter()
        resp = original(self, *args, **kwargs)
        dt = int((time.perf_counter() - t0) * 1000)
        model = kwargs.get("model") or getattr(resp, "model", "")
        usage = getattr(resp, "usage", None)
        if usage is not None:
            _safe_log(model, usage, dt, _guess_context())
        return resp
    return patched


def _make_async_wrapper(original):
    async def patched(self, *args, **kwargs):
        kwargs = _inject_cache_control(kwargs)
        t0 = time.perf_counter()
        resp = await original(self, *args, **kwargs)
        dt = int((time.perf_counter() - t0) * 1000)
        model = kwargs.get("model") or getattr(resp, "model", "")
        usage = getattr(resp, "usage", None)
        if usage is not None:
            _safe_log(model, usage, dt, _guess_context())
        return resp
    return patched


def apply_claude_logging_patch() -> None:
    """Wrap toutes les surfaces Anthropic + pydantic_ai. Idempotent."""
    global _PATCH_APPLIED, _PYDANTIC_PATCH_APPLIED
    if _PATCH_APPLIED:
        return

    # 1+2. SDK Anthropic version stable
    try:
        from anthropic.resources.messages import Messages, AsyncMessages
        Messages.create = _make_sync_wrapper(Messages.create)
        AsyncMessages.create = _make_async_wrapper(AsyncMessages.create)
        logger.info("EKOALU patch: Anthropic SDK Messages (stable) wrapped")
    except ImportError:
        logger.warning("Cannot patch anthropic SDK (not installed)")
        return

    # 3. SDK Anthropic namespace beta -- utilise par pydantic_ai pour cache_control
    try:
        from anthropic.resources.beta.messages.messages import (
            Messages as BetaMessages,
            AsyncMessages as BetaAsyncMessages,
        )
        BetaMessages.create = _make_sync_wrapper(BetaMessages.create)
        BetaAsyncMessages.create = _make_async_wrapper(BetaAsyncMessages.create)
        logger.info("EKOALU patch: Anthropic SDK Messages (beta) wrapped")
    except ImportError:
        logger.warning("Cannot patch anthropic beta namespace (skipping)")

    _PATCH_APPLIED = True

    # 4. PAS de patch pydantic_ai : le filet de securite est superflu puisque
    # pydantic_ai appelle in fine `self.client.beta.messages.create` qui EST
    # patche par (3). Patcher les 2 niveaux genere du double-counting (verifie
    # 2026-05-21 : chaque appel etait enregistre 2x dans ClaudeUsageLog).
    # Si un jour pydantic_ai bypasse les niveaux beta, on remettra ce filet.
    logger.info("EKOALU patch: pydantic_ai filet de secours desactive (double-count)")
