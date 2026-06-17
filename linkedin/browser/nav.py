# linkedin/browser/nav.py
import logging
import random
import time
from urllib.parse import unquote, urlparse, urljoin

from patchright.sync_api import TimeoutError as PlaywrightTimeoutError

from linkedin.conf import BROWSER_NAV_TIMEOUT_MS, DUMP_PAGES, FIXTURE_PAGES_DIR
from linkedin.exceptions import SkipProfile

logger = logging.getLogger(__name__)


def goto_page(session,
              action,
              expected_url_pattern: str,
              timeout: int = BROWSER_NAV_TIMEOUT_MS,
              error_message: str = "",
              ):
    page = session.page
    action()
    if not page:
        return

    try:
        page.wait_for_url(lambda url: expected_url_pattern in unquote(url), timeout=timeout)
    except PlaywrightTimeoutError:
        pass  # we still continue and check URL below

    session.wait()

    current = unquote(page.url)
    if expected_url_pattern not in current:
        if "/404" in current:
            raise SkipProfile(f"Profile returned 404 → {current}")
        raise RuntimeError(f"{error_message} → expected '{expected_url_pattern}' | got '{current}'")

    logger.debug("Navigated to %s", page.url)


def extract_in_urls(page):
    """Extract all /in/ profile URLs from the current page."""
    from linkedin.url_utils import url_to_public_id

    seen = set()
    urls = []
    for link in page.locator('a[href*="/in/"]').all():
        href = link.get_attribute("href")
        if href and "/in/" in href:
            full_url = urljoin(page.url, href.strip())
            clean = urlparse(full_url)._replace(query="", fragment="").geturl()
            if not url_to_public_id(clean):
                continue
            if clean not in seen:
                seen.add(clean)
                urls.append(clean)
    logger.debug(f"Extracted {len(urls)} unique /in/ profiles")
    return urls


def find_first_visible(page, selectors: list[str]):
    """Try selectors in order, return first locator that matches."""
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() > 0:
            return locator.first
    return None


def resolve_locator(page, candidates, timeout_per_ms: int = 5000):
    """Try locator factories in order, return the first one that becomes visible."""
    for factory in candidates:
        locator = factory(page).first
        try:
            locator.wait_for(state="visible", timeout=timeout_per_ms)
            return locator
        except PlaywrightTimeoutError:
            continue
    raise RuntimeError(f"No locator matched on {page.url}")


TOP_CARD_SELECTORS = [
    'section:has(div.top-card-background-hero-image)',
    'section[data-member-id]',
    'section.artdeco-card:has(> div.pv-top-card)',
    'section:has(> div[class*="pv-top-card"])',
    'section[componentkey*="com.linkedin.sdui.profile.card"]',
]


def find_top_card(session):
    top_card = find_first_visible(session.page, TOP_CARD_SELECTORS)
    if top_card is None:
        logger.warning("Top card not found on %s", session.page.url)
        raise SkipProfile("Top Card section not found")
    return top_card


def _per_char_delay_bounds_ms(min_delay: int | None, max_delay: int | None) -> tuple[int, int]:
    """Bornes de delai PAR CARACTERE en ms.

    Par defaut, derivees de la cadence de frappe cible EKOALU 200-400 c/min
    (HUMAN_TYPING_CHARS_PER_MIN_*) → ~150-300 ms/char."""
    if min_delay is not None and max_delay is not None:
        return min_delay, max_delay
    from ekoalu.conf import (
        HUMAN_TYPING_CHARS_PER_MIN_MAX,
        HUMAN_TYPING_CHARS_PER_MIN_MIN,
    )

    lo = int(60_000 / HUMAN_TYPING_CHARS_PER_MIN_MAX)  # cadence haute → delai mini
    hi = int(60_000 / HUMAN_TYPING_CHARS_PER_MIN_MIN)  # cadence basse → delai maxi
    return lo, hi


def human_type(locator, text: str, min_delay: int | None = None, max_delay: int | None = None):
    """Frappe caractere par caractere avec un delai VARIABLE par caractere.

    `locator.type(text, delay=)` tirait UN seul delai applique a toute la chaine
    (rythme regulier = signature bot) et l'appelant message.py forcait 10-50
    ms/char (1200-6000 c/min, surhumain). On tape desormais char par char avec
    un delai retire a CHAQUE frappe dans la cadence humaine cible (200-400 c/min
    ≈ 150-300 ms/char). LinkedIn instrumente le clavier du compose : c'est
    l'action la plus surveillee. Cf. revue 17/06 P1-6."""
    lo, hi = _per_char_delay_bounds_ms(min_delay, max_delay)
    for ch in text:
        locator.type(ch)
        time.sleep(random.randint(lo, hi) / 1000.0)


def dump_page_html(session: "AccountSession", profile: dict, category: str = "connect"):
    if not DUMP_PAGES:
        return
    dest = FIXTURE_PAGES_DIR / category
    dest.mkdir(parents=True, exist_ok=True)
    filepath = dest / f"{profile.get('public_identifier')}.html"
    html_content = session.page.content()
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)
    logger.info("Saved page snapshot → %s", filepath)
