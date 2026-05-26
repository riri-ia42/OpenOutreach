"""LLM model factory: build a pydantic-ai `Model` from `SiteConfig`.

Single boundary for LLM construction. Call sites import `get_llm_model()` and
hand the result to `pydantic_ai.Agent(...)`. Provider-specific routing lives
here so the rest of the codebase stays provider-agnostic.

Importing this module applique UN patch au boot :

``Agent.run_sync`` execute dans un thread dedie -- absolument necessaire pour
cohabiter avec Playwright Sync (browser automation OpenOutreach). Sans cette
isolation, anyio laisse le slot "running loop" du thread principal populé
apres retour, et Playwright Sync detecte cette boucle au prochain appel :
``Playwright Sync API inside the asyncio loop`` (regression observee
2026-05-22 : zombie loop ~30h, ~18 USD/jour brules en replay de qualif a
chaque crash/restart).

Le thread worker (max_workers=1, sequence preservee) cree sa propre boucle,
l'execute, la libere ; le thread principal n'en voit jamais l'execution.
Cout : 1 thread switch par appel LLM (~ms negligeable).

Note historique (2026-05-26) : un ``nest_asyncio.apply()`` etait posé en plus
au boot pour autoriser un ``loop.run_until_complete`` imbrique dans le thread
principal. Devenu inutile avec le fix thread isolation ET contre-productif :
nest_asyncio rendait persistantes les "running loops" entre appels, ce qui
maintenait la regression Playwright meme avec le worker thread (zombies
residuels observes 23-25/05 -> cap Anthropic sature le 25/05). Patch retire.

Voir aussi : https://pydantic.dev/docs/ai/overview/troubleshooting/
"""
from __future__ import annotations

import contextvars
import inspect
from concurrent.futures import ThreadPoolExecutor


# ── Asyncio isolation pour cohabiter avec Playwright Sync ──

_LLM_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pyai")
_ORIGINAL_RUN_SYNC = None


# ContextVar pose dans le thread caller (ou la stack metier est intacte) et
# propage au thread worker via copy_context(). Le wrapper Anthropic SDK
# (ekoalu/llm_usage/patch.py) lit ce var en priorite -- 99% des appels passent
# par pydantic-ai donc l'inspection de stack dans le worker echoue (frames
# metier inexistantes dans le thread worker).
LLM_CONTEXT_VAR: contextvars.ContextVar[str] = contextvars.ContextVar(
    "ekoalu_llm_context", default="",
)


# Mapping (substring du chemin fichier) -> label de contexte. Centralise ici
# pour eviter la duplication avec ekoalu/llm_usage/patch.py:_guess_context().
_CONTEXT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("ekoalu/inbox_assist", "inbox_assist"),
    ("ekoalu/outbound_validation", "outbound_invitation"),
    ("ekoalu/company_validation", "company_suggester"),
    ("linkedin/agents/follow_up", "follow_up_agent"),
    ("ekoalu/follow_up", "follow_up_ekoalu"),
    ("linkedin/pipeline/qualify", "qualifier"),
    ("linkedin/ml/qualifier", "qualifier_ml"),
    ("linkedin/db/summaries", "summary"),
    ("linkedin/pipeline/search_keywords", "search_keywords"),
    ("linkedin/daemon", "daemon"),
    ("ekoalu/sourcing_filter", "sourcing_filter"),
)


def _detect_caller_context() -> str:
    """Detecte le module metier appelant via la stack du thread caller.

    Appele DANS le thread principal (avant submit dans le worker), ou la
    stack contient encore les frames metier. Le worker pyai herite ensuite
    de cette valeur via contextvars.
    """
    try:
        for frame_info in inspect.stack()[2:25]:
            fname = frame_info.filename.replace("\\", "/").lower()
            for needle, label in _CONTEXT_PATTERNS:
                if needle in fname:
                    return label
    except Exception:
        pass
    return ""


def _isolated_run_sync(self, *args, **kwargs):
    """Execute pydantic-ai Agent.run_sync dans un thread dedie.

    Le thread worker absorbe toute la machinerie anyio/asyncio de pydantic-ai
    et la libere a la fin de l'appel. Le thread principal (qui pilote
    Playwright Sync) ne voit jamais de "running loop", ce qui evite la
    regression ``Playwright Sync API inside the asyncio loop``.

    Capture aussi le contexte metier appelant et le propage au worker via
    contextvars.copy_context() : sans ca le wrapper Anthropic SDK ne pourrait
    pas tagger les appels (la stack du worker ne contient pas les frames
    metier).
    """
    # Priorite : valeur posee explicitement par le caller > auto-detect.
    # Permet aux call sites de forcer un tag specifique si la detection
    # auto ne suffit pas (sous-modules, helpers partages, etc).
    explicit = LLM_CONTEXT_VAR.get()
    ctx_label = explicit or _detect_caller_context() or "pydantic_ai_unknown"

    def _run():
        LLM_CONTEXT_VAR.set(ctx_label)
        return _ORIGINAL_RUN_SYNC(self, *args, **kwargs)

    captured_ctx = contextvars.copy_context()
    return _LLM_EXECUTOR.submit(captured_ctx.run, _run).result()


def _apply_asyncio_isolation_patch() -> None:
    """Monkey-patch ``pydantic_ai.Agent.run_sync`` une seule fois (idempotent)."""
    global _ORIGINAL_RUN_SYNC
    if _ORIGINAL_RUN_SYNC is not None:
        return
    from pydantic_ai import Agent

    _ORIGINAL_RUN_SYNC = Agent.run_sync
    Agent.run_sync = _isolated_run_sync


_apply_asyncio_isolation_patch()


# Override the SDK default of 2. Each retry uses the SDK's built-in jittered
# exponential backoff and honors `Retry-After`, so 8 attempts ride through
# typical 429/529 capacity blips (~1–2 minutes) instead of failing in ~1.5s.
_MAX_RETRIES = 8


# ── Per-provider builders ──

def _build_openai(cfg):
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIModel
    from pydantic_ai.providers.openai import OpenAIProvider
    client = AsyncOpenAI(api_key=cfg.llm_api_key, max_retries=_MAX_RETRIES)
    return OpenAIModel(cfg.ai_model, provider=OpenAIProvider(openai_client=client))


def _build_anthropic(cfg):
    from anthropic import AsyncAnthropic
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider
    client = AsyncAnthropic(api_key=cfg.llm_api_key, max_retries=_MAX_RETRIES)
    return AnthropicModel(cfg.ai_model, provider=AnthropicProvider(anthropic_client=client))


def _build_google(cfg):
    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.providers.google import GoogleProvider
    return GoogleModel(cfg.ai_model, provider=GoogleProvider(api_key=cfg.llm_api_key))


def _build_groq(cfg):
    from groq import AsyncGroq
    from pydantic_ai.models.groq import GroqModel
    from pydantic_ai.providers.groq import GroqProvider
    client = AsyncGroq(api_key=cfg.llm_api_key, max_retries=_MAX_RETRIES)
    return GroqModel(cfg.ai_model, provider=GroqProvider(groq_client=client))


def _build_mistral(cfg):
    from pydantic_ai.models.mistral import MistralModel
    from pydantic_ai.providers.mistral import MistralProvider
    return MistralModel(cfg.ai_model, provider=MistralProvider(api_key=cfg.llm_api_key))


def _build_cohere(cfg):
    from pydantic_ai.models.cohere import CohereModel
    from pydantic_ai.providers.cohere import CohereProvider
    return CohereModel(cfg.ai_model, provider=CohereProvider(api_key=cfg.llm_api_key))


def _build_openai_compatible(cfg):
    if not cfg.llm_api_base:
        raise ValueError("LLM_API_BASE is required for the openai_compatible provider.")
    from pydantic_ai.models.openai import OpenAIModel
    from pydantic_ai.providers.openai import OpenAIProvider
    return OpenAIModel(cfg.ai_model, provider=OpenAIProvider(
        base_url=cfg.llm_api_base, api_key=cfg.llm_api_key,
    ))


_PROVIDER_BUILDERS = {
    "openai": _build_openai,
    "anthropic": _build_anthropic,
    "google": _build_google,
    "groq": _build_groq,
    "mistral": _build_mistral,
    "cohere": _build_cohere,
    "openai_compatible": _build_openai_compatible,
}


# ── Public API ──

def _validated_site_config():
    """Load `SiteConfig` and assert the required LLM fields are populated."""
    from linkedin.models import SiteConfig

    cfg = SiteConfig.load()
    if not cfg.llm_api_key:
        raise ValueError("LLM_API_KEY is not set in Site Configuration.")
    if not cfg.ai_model:
        raise ValueError("AI_MODEL is not set in Site Configuration.")
    return cfg


def get_llm_model():
    """Return a configured pydantic-ai `Model` for the current `SiteConfig`."""
    cfg = _validated_site_config()
    builder = _PROVIDER_BUILDERS.get(cfg.llm_provider)
    if builder is None:
        raise ValueError(f"Unknown LLM provider: {cfg.llm_provider!r}")
    return builder(cfg)
