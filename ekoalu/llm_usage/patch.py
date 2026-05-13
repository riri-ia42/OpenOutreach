"""Monkey-patch des appels Anthropic SDK pour logger usage + cout.

Applique au boot Django via ekoalu.apps.EkoaluConfig.ready().
Couvre `anthropic.Anthropic().messages.create` (sync) et
`anthropic.AsyncAnthropic().messages.create` (async).
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_PATCH_APPLIED = False


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
    """Devine le contexte d'appel via la stack (sans crash)."""
    try:
        import inspect
        for frame_info in inspect.stack()[2:8]:
            fname = frame_info.filename.replace("\\", "/").lower()
            if "ekoalu/inbox_assist" in fname:
                return "inbox_assist"
            if "ekoalu/outbound_validation" in fname:
                return "outbound_invitation"
            if "ekoalu/company_validation" in fname:
                return "company_suggester"
            if "linkedin/agents/follow_up" in fname:
                return "follow_up_agent"
            if "linkedin/pipeline/qualif" in fname:
                return "qualifier"
            if "linkedin/db/summaries" in fname:
                return "summary"
    except Exception:
        pass
    return ""


def apply_claude_logging_patch() -> None:
    """Wrap Messages.create + AsyncMessages.create. Idempotent."""
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return

    try:
        from anthropic.resources.messages import Messages, AsyncMessages
    except ImportError:
        logger.warning("Cannot patch claude logging (anthropic SDK not installed)")
        return

    original_sync = Messages.create
    original_async = AsyncMessages.create

    def patched_sync(self, *args, **kwargs):
        t0 = time.perf_counter()
        resp = original_sync(self, *args, **kwargs)
        dt = int((time.perf_counter() - t0) * 1000)
        model = kwargs.get("model") or getattr(resp, "model", "")
        usage = getattr(resp, "usage", None)
        if usage is not None:
            _safe_log(model, usage, dt, _guess_context())
        return resp

    async def patched_async(self, *args, **kwargs):
        t0 = time.perf_counter()
        resp = await original_async(self, *args, **kwargs)
        dt = int((time.perf_counter() - t0) * 1000)
        model = kwargs.get("model") or getattr(resp, "model", "")
        usage = getattr(resp, "usage", None)
        if usage is not None:
            _safe_log(model, usage, dt, _guess_context())
        return resp

    Messages.create = patched_sync
    AsyncMessages.create = patched_async
    _PATCH_APPLIED = True
    logger.info("EKOALU Claude usage logging patch applique")
