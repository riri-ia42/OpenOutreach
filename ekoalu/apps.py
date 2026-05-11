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

        try:
            apply_human_scheduler_patch()
            logger.info("EKOALU human_scheduler patch applied")
        except Exception as e:
            logger.error("Failed to apply EKOALU patches: %s", e, exc_info=True)
            # On ne lève pas — Django doit pouvoir démarrer pour migrations etc.
