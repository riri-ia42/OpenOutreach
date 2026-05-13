from __future__ import annotations

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class EkoaluConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ekoalu"
    verbose_name = "EKOALU extensions"

    def ready(self) -> None:
        """Appliqué au démarrage Django.

        Active les patchs EKOALU sur OpenOutreach :
        - human_scheduler : intercepte la création de Tasks pour ajouter
          la logique gaussienne / pondération hebdo / pause déjeuner.
        """
        # Import tardif pour éviter les imports circulaires Django startup
        from ekoalu.human_scheduler.patch import apply_human_scheduler_patch
        from ekoalu.llm_usage.patch import apply_claude_logging_patch
        from ekoalu.outbound_validation.patch import apply_outbound_validation_patch
        from ekoalu.sourcing_filter.patch import apply_sourcing_filter_patch

        try:
            apply_human_scheduler_patch()
            logger.info("EKOALU human_scheduler patch applied")
        except Exception as e:
            logger.error("Failed to apply human_scheduler patch: %s", e, exc_info=True)

        try:
            apply_outbound_validation_patch()
            logger.info("EKOALU outbound_validation patch applied")
        except Exception as e:
            logger.error("Failed to apply outbound_validation patch: %s", e, exc_info=True)

        try:
            apply_claude_logging_patch()
            logger.info("EKOALU claude logging patch applied")
        except Exception as e:
            logger.error("Failed to apply claude logging patch: %s", e, exc_info=True)

        try:
            apply_sourcing_filter_patch()
            logger.info("EKOALU sourcing_filter patch applied")
        except Exception as e:
            logger.error("Failed to apply sourcing_filter patch: %s", e, exc_info=True)
