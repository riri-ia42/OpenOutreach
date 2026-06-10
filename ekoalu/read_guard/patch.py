"""Monkey-patch du client Voyager : chaque lecture de profil passe par le cap.

Applique au boot via ekoalu/apps.py. Enveloppe les deux methodes de
``PlaywrightLinkedinAPI`` qui touchent /identity/dash/profiles :

- ``get_profile``           (fiche complete — la lecture "lourde")
- ``get_connection_degree`` (topcard — plus legere mais meme endpoint)

Avant chaque appel : ``check_read_allowed()`` (raise ReadCapExceededError si
cap atteint) puis ``record_read()`` (on compte la tentative, LinkedIn voit la
requete meme en cas d'echec).
"""
from __future__ import annotations

import functools
import logging

logger = logging.getLogger(__name__)

_PATCH_APPLIED = False


def _wrap_profile_read(fn, label: str):
    """Enveloppe une methode de lecture profil avec le garde-fou."""

    @functools.wraps(fn)
    def wrapped(self, *args, **kwargs):
        from ekoalu.read_guard.guard import check_read_allowed, record_read

        check_read_allowed()
        record_read(source=label)
        return fn(self, *args, **kwargs)

    wrapped._ekoalu_read_guard = True
    return wrapped


def apply_read_guard_patch() -> None:
    """Wrap get_profile + get_connection_degree. Idempotent."""
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return

    try:
        from linkedin.api.client import PlaywrightLinkedinAPI
    except ImportError:
        logger.warning("Cannot patch read_guard (linkedin.api.client not importable)")
        return

    if not getattr(PlaywrightLinkedinAPI.get_profile, "_ekoalu_read_guard", False):
        PlaywrightLinkedinAPI.get_profile = _wrap_profile_read(
            PlaywrightLinkedinAPI.get_profile, "get_profile",
        )
    if not getattr(
        PlaywrightLinkedinAPI.get_connection_degree, "_ekoalu_read_guard", False,
    ):
        PlaywrightLinkedinAPI.get_connection_degree = _wrap_profile_read(
            PlaywrightLinkedinAPI.get_connection_degree, "get_connection_degree",
        )

    _PATCH_APPLIED = True
    logger.info("EKOALU read_guard patch applique (cap lectures profil/jour)")
