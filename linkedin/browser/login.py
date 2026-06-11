# linkedin/browser/login.py
#
# Refonte anti-detection 11/06/2026 (benchmark Patchright + persistent context).
#
# Modele cible (convergence des 3 sources benchmark + repo linkedin-mcp-server) :
# 1. PATCHRIGHT (pas Playwright stock) — corrige le leak CDP `Runtime.enable` et
#    `navigator.webdriver` que `playwright-stealth` laissait passer.
# 2. PROFIL PERSISTANT sur disque (`launch_persistent_context`) — cookies +
#    localStorage + IndexedDB + fingerprint conserves = appareil de confiance
#    stable. Un contexte neuf + cookies injectes ressemblait a une session volee
#    → checkpoint a chaque login.
# 3. VRAI CHROME (`channel="chrome"`), `headless=False`, `no_viewport=True`,
#    ZERO override (pas de user_agent/args/init scripts custom).
# 4. JAMAIS de re-login automatique au mot de passe. Le 1er login est MANUEL
#    (commande `linkedin_manual_login`, Richard coche "Rester connecte" → cookie
#    `li_rm` device-trust 1 an). Ensuite le daemon reutilise le profil. Si le
#    profil n'est plus authentifie : STOP + fenetre laissee ouverte pour login
#    manuel, jamais de soumission auto de credentials (chaque resoumission
#    durcit le blocage LinkedIn).
import logging

from patchright.sync_api import sync_playwright
from termcolor import colored

from linkedin.browser.nav import goto_page
from linkedin.conf import (
    BROWSER_CHANNEL,
    BROWSER_DEFAULT_TIMEOUT_MS,
    LINKEDIN_PROFILE_DIR,
)

logger = logging.getLogger(__name__)

LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"
LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"

COMPLY_LOCATORS = [
    lambda p: p.locator('button#content__button--primary--muted'),
    lambda p: p.get_by_role("button", name="Agree to comply", exact=True),
    lambda p: p.locator('button.content__button--primary'),
]

COMPLY_PROBE_TIMEOUT_MS = 5000


def dismiss_comply_gate(page, timeout_ms: int = COMPLY_PROBE_TIMEOUT_MS) -> bool:
    """Click LinkedIn's 'Agree to comply' interstitial if present. Return True if clicked."""
    from patchright.sync_api import TimeoutError as PlaywrightTimeoutError

    for factory in COMPLY_LOCATORS:
        locator = factory(page).first
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            continue
        logger.info(colored("Dismissing 'Agree to comply' interstitial", "yellow"))
        locator.click()
        return True
    return False


def launch_persistent_browser():
    """Lance Chrome avec un PROFIL PERSISTANT sur disque (anti-detection).

    Retourne (page, context, browser, playwright). `browser` est None : en mode
    persistant le contexte EST le navigateur (pas d'objet browser separe).
    """
    LINKEDIN_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    playwright = sync_playwright().start()

    launch_kwargs = dict(
        user_data_dir=str(LINKEDIN_PROFILE_DIR),
        headless=False,        # tete visible (TSE: vraie console requise)
        no_viewport=True,      # pas de viewport force
        # ZERO override volontaire : pas de user_agent, pas d'args, pas de
        # extra_http_headers, pas d'init script — chaque override reintroduit
        # un tell detectable (cf. benchmark Patchright).
    )
    try:
        context = playwright.chromium.launch_persistent_context(
            channel=BROWSER_CHANNEL, **launch_kwargs,
        )
    except Exception:
        # Vrai Chrome absent → repli sur le Chromium bundle (moins ideal mais
        # fonctionnel). On log clairement pour que Richard installe Chrome.
        logger.warning(
            "Chrome (channel=%s) introuvable — repli sur Chromium bundle."
            " Installer Google Chrome ameliore le fingerprint.", BROWSER_CHANNEL,
        )
        context = playwright.chromium.launch_persistent_context(**launch_kwargs)

    context.set_default_timeout(BROWSER_DEFAULT_TIMEOUT_MS)
    context.set_default_navigation_timeout(BROWSER_DEFAULT_TIMEOUT_MS)
    page = context.pages[0] if context.pages else context.new_page()
    return page, context, None, playwright


def is_authenticated(page) -> bool:
    """True si le profil persistant a une session LinkedIn valide (sur /feed)."""
    from urllib.parse import unquote

    try:
        page.goto(LINKEDIN_FEED_URL)
        dismiss_comply_gate(page)
        page.wait_for_load_state("domcontentloaded")
    except Exception:
        logger.exception("Echec de navigation vers /feed")
        return False
    return "/feed" in unquote(page.url)


def _handle_login_failure(session, reason: str, exc: Exception | None = None):
    """Profil non authentifie (checkpoint LinkedIn ou cookies expires).

    Decision Richard 10-11/06 : on NE soumet PAS de credentials automatiquement
    (chaque resoumission durcit le blocage cote LinkedIn), on NE ferme PAS le
    navigateur (la fenetre reste ouverte sur la page de login/verification pour
    que Richard se connecte a la main au poste — il coche "Rester connecte"),
    et on engage le STOP des le 1er echec. On leve AuthenticationError pour que
    l'appelant cesse d'utiliser la session, SANS toucher au profil ni aux cookies.
    """
    from linkedin.exceptions import AuthenticationError

    logger.error(
        colored("AUTH FAIL", "red", attrs=["bold"])
        + " — %s. Fenetre LAISSEE OUVERTE pour login manuel ; STOP engage ;"
        + " aucune soumission auto de credentials, aucun effacement de profil.",
        reason,
    )
    # Amene la fenetre sur la page de login pour faciliter la saisie manuelle.
    try:
        if session is not None and session.page is not None:
            session.page.goto(LINKEDIN_LOGIN_URL)
    except Exception:
        logger.debug("Impossible d'ouvrir la page de login", exc_info=True)
    try:
        from ekoalu import auth_watch
        auth_watch.record_auth_failure(context=reason)
    except Exception:
        logger.exception("auth_watch indisponible (STOP non engage automatiquement)")
    raise AuthenticationError(reason) from exc


def start_browser_session(session: "AccountSession"):
    """Demarre la session navigateur a partir du PROFIL PERSISTANT.

    Plus de login automatique au mot de passe : si le profil n'est pas
    authentifie, on laisse la fenetre ouverte + STOP (login manuel requis).
    """
    logger.debug("Configuring persistent browser for %s", session)

    session.page, session.context, session.browser, session.playwright = (
        launch_persistent_browser()
    )

    if is_authenticated(session.page):
        logger.info(colored("Browser ready", "green", attrs=["bold"]) + " (profil persistant authentifie)")
    else:
        _handle_login_failure(
            session,
            "profil persistant non authentifie (1er login manuel requis OU"
            " cookies expires/checkpoint) — lancer `manage.py linkedin_manual_login`",
        )

    session.page.wait_for_load_state("load")


if __name__ == "__main__":
    from linkedin.browser.registry import cli_parser, cli_session

    parser = cli_parser("Start a LinkedIn browser session")
    args = parser.parse_args()
    session = cli_session(args)
    session.ensure_browser()

    start_browser_session(session=session)
    logger.info("Logged in! Close browser manually.")
    session.page.pause()
