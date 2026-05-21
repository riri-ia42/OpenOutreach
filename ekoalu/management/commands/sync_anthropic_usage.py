"""Sync quotidien de la conso Anthropic via Admin API.

Lance par tache planifiee (cron) une fois par jour, p.ex. 00:30 UTC :

    python manage.py sync_anthropic_usage [--days 7]

Stocke chaque (date, modele, api_key, workspace) dans AnthropicUsageDaily.
Met a jour le cost_usd avec la valeur officielle d'Anthropic.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timezone

from django.core.management.base import BaseCommand
from django.db import transaction

from ekoalu.llm_usage.anthropic_admin import (
    AdminAPIError,
    AdminConfigError,
    CostBucket,
    UsageBucket,
    default_window,
    fetch_cost,
    fetch_usage_messages,
)
from ekoalu.llm_usage.models import AnthropicUsageDaily

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Synchronise la conso Anthropic (Admin API) dans AnthropicUsageDaily."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days", type=int, default=7,
            help="Nombre de jours en arriere a synchroniser (defaut 7)",
        )

    def handle(self, *args, **opts):
        try:
            start, end = default_window(days_back=opts["days"])
            self.stdout.write(f"Sync Anthropic Admin API : {start} -> {end}")

            usage_buckets = fetch_usage_messages(start, end, bucket_width="1d")
            cost_buckets = fetch_cost(start, end)

            self.stdout.write(f"  {len(usage_buckets)} buckets usage, {len(cost_buckets)} buckets cost")

            # Index couts par (date, model, workspace) pour les ratacher aux usages
            cost_index: dict[tuple[date, str, str], float] = defaultdict(float)
            for cb in cost_buckets:
                key = (cb.starts_at.date(), _normalize_model(cb.model), cb.workspace_id)
                cost_index[key] += cb.amount_usd

            n_created, n_updated = 0, 0
            with transaction.atomic():
                for ub in usage_buckets:
                    d = ub.starts_at.date()
                    model = _normalize_model(ub.model)
                    workspace = ub.workspace_id or ""
                    api_key = ub.api_key_id or ""

                    # Cout : priorite a la valeur du cost_report ; sinon
                    # fallback sur calcul local (pricing.py) si pas dispo.
                    cost = cost_index.get((d, model, workspace), 0.0)
                    if cost == 0.0:
                        from ekoalu.llm_usage.pricing import compute_cost_usd
                        cost = compute_cost_usd(
                            model, ub.input_tokens, ub.output_tokens,
                            ub.cache_creation_tokens, ub.cache_read_tokens,
                        )

                    row, created = AnthropicUsageDaily.objects.update_or_create(
                        date=d, model=model, api_key_id=api_key, workspace_id=workspace,
                        defaults={
                            "input_tokens": ub.input_tokens,
                            "output_tokens": ub.output_tokens,
                            "cache_creation_tokens": ub.cache_creation_tokens,
                            "cache_read_tokens": ub.cache_read_tokens,
                            "cost_usd": cost,
                        },
                    )
                    if created:
                        n_created += 1
                    else:
                        n_updated += 1

            self.stdout.write(self.style.SUCCESS(
                f"OK : {n_created} crees, {n_updated} mis a jour"
            ))
            total_cost = sum(b.amount_usd for b in cost_buckets)
            self.stdout.write(f"Cout total sur fenetre : {total_cost:.4f} $")
            logger.info("sync_anthropic_usage OK : %d created, %d updated, total %.4f$",
                        n_created, n_updated, total_cost)

        except AdminConfigError as exc:
            self.stdout.write(self.style.WARNING(str(exc)))
            return
        except AdminAPIError as exc:
            self.stdout.write(self.style.ERROR(f"Admin API : {exc}"))
            raise


def _normalize_model(model: str) -> str:
    """Strip vendor prefix / version suffix pour matcher les noms model courts."""
    if not model:
        return ""
    m = model.lower()
    # Strip "anthropic." prefix
    if m.startswith("anthropic."):
        m = m[len("anthropic."):]
    # Strip date suffix style "claude-sonnet-4-6-20251001"
    parts = m.split("-")
    if parts and parts[-1].isdigit() and len(parts[-1]) >= 6:
        m = "-".join(parts[:-1])
    return m
