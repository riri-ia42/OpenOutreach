"""Classification fabricant : Haiku en Batch API, escalade Sonnet si incertain.

Décision Richard 28/07 : Haiku 4.5 en batch (−50 %) pour le gros du volume,
Sonnet uniquement sur les cas que Haiku n'a pas tranchés. Sur 242 cibles,
l'ordre de grandeur est ~0,50 $ au total — le coût réel du chantier est le
scraping, pas le LLM.

Le Batch API est asynchrone (généralement < 1 h, garanti < 24 h). C'est adapté :
enrichissement one-shot puis passe hebdomadaire, jamais dans un chemin
interactif.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from ekoalu.fabricant_detect.prompts import (
    SYSTEM_PROMPT,
    VERDICT_SCHEMA,
    build_user_prompt,
)

logger = logging.getLogger(__name__)

MODEL_CHEAP = "claude-haiku-4-5"
MODEL_ESCALATION = "claude-sonnet-4-6"

MAX_TOKENS = 1200
POLL_INTERVAL_SECONDS = 20
POLL_TIMEOUT_SECONDS = 3600  # 1 h : au-delà, on rend la main sans bloquer un cron


@dataclass
class ClassifyInput:
    """Une société à classer, texte de site déjà récupéré."""

    siren: str
    entreprise: str
    code_naf: str
    ville: str
    url: str
    text: str


def _request_params(item: ClassifyInput, model: str) -> dict:
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "output_config": {"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
        "messages": [{
            "role": "user",
            "content": build_user_prompt(
                item.entreprise, item.code_naf, item.ville, item.url, item.text,
            ),
        }],
    }


def _parse_verdict(text: str) -> dict | None:
    """Le structured output garantit le schéma ; on reste tolérant au parse."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Verdict illisible : %s", (text or "")[:120])
        return None
    return data if isinstance(data, dict) and "verdict" in data else None


def _is_uncertain(verdict: dict) -> bool:
    """Verdict non concluant : indéterminé, ou tranché sans conviction."""
    return verdict.get("verdict") == "indetermine" or verdict.get("confiance") == "basse"


def _should_escalate(verdict: dict) -> bool:
    """Escalader ne sert QUE si la preuve est ambiguë, pas si l'indétermination
    est structurelle.

    Mesuré sur la passe du 28/07 : sur 107 indéterminés, 100 étaient des
    sociétés hors métier (génie électrique, carrelage, protection incendie) ou
    des sites sans rapport. Les réexaminer a coûté 117 appels Sonnet — 1,73 $,
    soit 4,5× le batch Haiku — pour **zéro** verdict amélioré : changer de
    modèle ne transforme pas un électricien en menuisier.

    Le signal discriminant est `materiaux` : s'il est vide, le modèle n'a
    reconnu aucune activité menuiserie et la question est déjà tranchée.
    """
    return _is_uncertain(verdict) and bool(verdict.get("materiaux"))


def classify_batch(client, items: list[ClassifyInput], *,
                   model: str = MODEL_CHEAP,
                   poll_timeout: int = POLL_TIMEOUT_SECONDS) -> dict[str, dict]:
    """Classe `items` via le Batch API. Retourne {siren: verdict}.

    Les échecs individuels sont ignorés (loggés) plutôt que de faire tomber le
    lot : une société non classée reste simplement sans verdict.
    """
    if not items:
        return {}

    requests = [
        {"custom_id": item.siren, "params": _request_params(item, model)}
        for item in items
    ]
    batch = client.messages.batches.create(requests=requests)
    logger.info("Batch %s créé : %d société(s), modèle=%s", batch.id, len(items), model)

    deadline = time.monotonic() + poll_timeout
    while True:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        if time.monotonic() > deadline:
            logger.error("Batch %s toujours en cours après %ds — abandon",
                         batch.id, poll_timeout)
            return {}
        time.sleep(POLL_INTERVAL_SECONDS)

    verdicts: dict[str, dict] = {}
    usage_in = usage_out = 0
    for result in client.messages.batches.results(batch.id):
        if result.result.type != "succeeded":
            logger.warning("Batch %s : %s en échec (%s)",
                           batch.id, result.custom_id, result.result.type)
            continue
        message = result.result.message
        usage_in += getattr(message.usage, "input_tokens", 0) or 0
        usage_out += getattr(message.usage, "output_tokens", 0) or 0
        text = next((b.text for b in message.content if b.type == "text"), "")
        parsed = _parse_verdict(text)
        if parsed is not None:
            parsed["_model"] = model
            verdicts[result.custom_id] = parsed
    _log_batch_usage(model, usage_in, usage_out)
    logger.info("Batch %s : %d/%d verdicts exploitables",
                batch.id, len(verdicts), len(items))
    return verdicts


def _log_batch_usage(model: str, input_tokens: int, output_tokens: int) -> None:
    """Trace la conso du Batch API dans ClaudeUsageLog.

    Le tracker global (`llm_usage/patch.py`) patche `messages.create` et ne voit
    donc PAS `messages.batches` : sans cet appel, une grosse passe batch serait
    invisible du garde-fou budgétaire et du dashboard de coût. On applique la
    remise batch (-50 %) au calcul.
    """
    if not input_tokens and not output_tokens:
        return
    try:
        from ekoalu.llm_usage.models import ClaudeUsageLog
        from ekoalu.llm_usage.pricing import BATCH_DISCOUNT, get_pricing

        price_in, price_out = get_pricing(model)
        cost = (input_tokens / 1e6) * price_in + (output_tokens / 1e6) * price_out
        ClaudeUsageLog.objects.create(
            model=model,
            context="fabricant_detect_batch",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost * BATCH_DISCOUNT,
        )
    except Exception:  # noqa: BLE001 — la traçabilité ne doit jamais casser la passe
        logger.exception("Traçabilité conso batch en échec (verdicts conservés)")


def classify_one(client, item: ClassifyInput, *, model: str = MODEL_ESCALATION) -> dict | None:
    """Appel synchrone unitaire — sert à l'escalade Sonnet (volume faible)."""
    try:
        response = client.messages.create(**_request_params(item, model))
    except Exception:  # noqa: BLE001 — un échec réseau ne doit pas tuer la passe
        logger.exception("Escalade %s (%s) en échec", item.siren, item.entreprise)
        return None
    text = next((b.text for b in response.content if b.type == "text"), "")
    parsed = _parse_verdict(text)
    if parsed is not None:
        parsed["_model"] = model
        parsed["_escalated"] = True
    return parsed


def classify_with_escalation(client, items: list[ClassifyInput], *,
                             escalate: bool = True,
                             poll_timeout: int = POLL_TIMEOUT_SECONDS,
                             ) -> tuple[dict[str, dict], int]:
    """Haiku en batch, puis Sonnet sur les incertains.

    Retourne `(verdicts, nb_escalades)`.
    """
    verdicts = classify_batch(client, items, poll_timeout=poll_timeout)
    if not escalate:
        return verdicts, 0

    by_siren = {item.siren: item for item in items}
    uncertain = [
        by_siren[siren] for siren, verdict in verdicts.items()
        if _should_escalate(verdict) and siren in by_siren
    ]
    if not uncertain:
        return verdicts, 0

    non_concluants = sum(1 for v in verdicts.values() if _is_uncertain(v))
    logger.info(
        "Escalade Sonnet : %d cas ambigu(s) sur %d non concluants (%d écartés — "
        "indétermination structurelle, hors métier)",
        len(uncertain), non_concluants, non_concluants - len(uncertain),
    )
    escalated = 0
    for item in uncertain:
        refined = classify_one(client, item)
        # On ne remplace que si Sonnet tranche vraiment mieux.
        if refined is not None and not _is_uncertain(refined):
            verdicts[item.siren] = refined
            escalated += 1
    return verdicts, escalated
