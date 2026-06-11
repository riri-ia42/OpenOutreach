# linkedin/browser/session.py
from __future__ import annotations

import logging
import random
import time
from functools import cached_property

from linkedin.conf import MIN_DELAY, MAX_DELAY

logger = logging.getLogger(__name__)

# The main LinkedIn auth cookie
_AUTH_COOKIE_NAME = "li_at"


def random_sleep(min_val, max_val):
    delay = random.uniform(min_val, max_val)
    logger.debug(f"Pause: {delay:.2f}s")
    time.sleep(delay)


class AccountSession:
    def __init__(self, linkedin_profile):
        self.linkedin_profile = linkedin_profile
        self.django_user = linkedin_profile.user

        # Active campaign — set by the daemon before each lane execution
        self.campaign = None

        # Playwright objects – created on first access or after crash
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None

    @cached_property
    def campaigns(self):
        """All campaigns this user belongs to (cached)."""
        from linkedin.models import Campaign
        return list(Campaign.objects.filter(users=self.django_user))

    def ensure_browser(self):
        """Launch or recover browser + login if needed. Call before using .page"""
        from linkedin.browser.login import start_browser_session

        if not self.page or self.page.is_closed():
            logger.debug("Launching/recovering browser for %s", self)
            # Si une instance Playwright precedente est encore vivante (page
            # morte sur timeout mais self.playwright pas stoppe), sa loop
            # asyncio tourne toujours dans le greenlet. sync_playwright().start()
            # detecte cette loop running et leve :
            #   "Playwright Sync API inside the asyncio loop"
            # Cf. scripts/test_asyncio_relaunch_bug.py pour la reproduction.
            if self.playwright is not None:
                self.close()
            start_browser_session(session=self)
        else:
            self._maybe_refresh_cookies()

    @cached_property
    def self_profile(self) -> dict:
        """Authenticated user's profile dict, fetched once per session.

        The dict isn't persisted to DB (we dropped ``Lead.profile_data``),
        so the first access per session triggers a Voyager call; the
        ``cached_property`` keeps it warm for the rest of the session.
        """
        from linkedin.setup.self_profile import discover_self_profile

        self.ensure_browser()
        return discover_self_profile(self)

    def wait(self, min_delay=MIN_DELAY, max_delay=MAX_DELAY):
        random_sleep(min_delay, max_delay)
        self.page.wait_for_load_state("domcontentloaded")

    def reauthenticate(self):
        """OBSOLETE depuis la refonte 11/06 — ne JAMAIS re-logger automatiquement.

        L'ancien comportement (fermer le navigateur + effacer les cookies +
        re-login auto au mot de passe sur un 401) etait la cause directe du
        checkpoint a chaque connexion (cf. benchmark anti-detection). Conservee
        en no-op pour ne pas casser d'eventuels appelants : on engage le STOP et
        on laisse la session telle quelle (fenetre ouverte pour login manuel).
        """
        from ekoalu import auth_watch

        logger.warning(
            "reauthenticate() appele mais DESACTIVE (anti-checkpoint) — STOP"
            " engage, login manuel requis via le profil persistant.",
        )
        auth_watch.record_auth_failure(context="reauthenticate() obsolete")

    def _maybe_refresh_cookies(self):
        """No-op : avec le profil persistant Patchright, LinkedIn re-emet `li_at`
        tout seul tant que `li_rm` (device trust) est present. On ne touche plus
        aux cookies en DB et on ne relance jamais de login auto."""
        return

    def close(self):
        if self.context:
            try:
                # Profil persistant : fermer le contexte sauvegarde tout le
                # profil sur disque (cookies/localStorage/IndexedDB). `browser`
                # est None en mode persistant.
                self.context.close()
                if self.browser:
                    self.browser.close()
                if self.playwright:
                    self.playwright.stop()
                logger.info("Browser closed gracefully (%s)", self)
            except Exception as e:
                logger.debug("Error closing browser: %s", e)
            finally:
                self.page = self.context = self.browser = self.playwright = None

        logger.info("Account session closed → %s", self)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def __repr__(self) -> str:
        return self.linkedin_profile.linkedin_username
