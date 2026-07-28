"""Récupération du texte d'un site d'entreprise (sans dépendance HTML nouvelle).

Le domaine vient de l'email professionnel — 87 % des cibles DECP en ont un.
On lit l'accueil puis, s'il existe, une page « société / savoir-faire / atelier »
qui est là où se trouvent les preuves de production.

Volontairement minimal : requêtes HTTP simples, pas de JS, pas de navigateur.
Un site qui exige JS ressort vide → verdict `indetermine`, ce qui est le bon
comportement (on ne devine pas).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# Domaines B2C : pas de site d'entreprise derrière, inutile d'essayer.
B2C_DOMAINS = frozenset({
    "wanadoo.fr", "orange.fr", "gmail.com", "free.fr", "hotmail.fr", "hotmail.com",
    "yahoo.fr", "yahoo.com", "sfr.fr", "aliceadsl.fr", "laposte.net", "outlook.fr",
    "outlook.com", "live.fr", "bbox.fr", "neuf.fr", "club-internet.fr", "gmx.fr",
})

# Chemins où se trouvent les preuves de production, par ordre de rendement.
CANDIDATE_PATHS = (
    "/notre-atelier", "/atelier", "/savoir-faire", "/notre-savoir-faire",
    "/fabrication", "/production", "/notre-entreprise", "/entreprise",
    "/qui-sommes-nous", "/a-propos", "/presentation", "/societe",
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

TIMEOUT_SECONDS = 12.0
MAX_CHARS_PER_PAGE = 6000
MAX_TOTAL_CHARS = 12000

_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript|svg|head)\b.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")


@dataclass
class SiteText:
    """Texte agrégé d'un site + traçabilité."""

    domain: str
    url: str = ""
    text: str = ""
    pages_fetched: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def usable(self) -> bool:
        """Assez de matière pour qu'un verdict ait du sens."""
        return len(self.text) >= 300


def domain_from_email(email: str) -> str:
    """Domaine d'entreprise, ou '' si B2C / absent."""
    if not email or "@" not in email:
        return ""
    domain = email.rsplit("@", 1)[-1].strip().lower().rstrip(".")
    if not domain or domain in B2C_DOMAINS:
        return ""
    return domain


def html_to_text(html: str) -> str:
    """HTML → texte lisible. Suffisant pour un LLM, inutile de parser finement."""
    if not html:
        return ""
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = re.sub(r"<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub(" ", text)
    # Entités les plus courantes (pas de dépendance pour si peu)
    for entity, char in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&quot;", '"'), ("&#39;", "'"),
        ("&lt;", "<"), ("&gt;", ">"), ("&eacute;", "é"), ("&egrave;", "è"),
        ("&agrave;", "à"), ("&ccedil;", "ç"), ("&ocirc;", "ô"), ("&rsquo;", "'"),
    ):
        text = text.replace(entity, char)
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANKLINES_RE.sub("\n\n", text).strip()


def _get(client: httpx.Client, url: str) -> str:
    """GET tolérant : toute erreur = page absente, pas une exception."""
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        logger.debug("fetch KO %s : %s", url, exc)
        return ""
    if response.status_code != 200:
        return ""
    ctype = response.headers.get("content-type", "")
    if "html" not in ctype.lower():
        return ""
    return response.text[:400_000]


def fetch_site_text(domain: str) -> SiteText:
    """Accueil + au plus 2 pages « société », concaténées et tronquées."""
    result = SiteText(domain=domain)
    if not domain:
        result.error = "pas de domaine"
        return result

    headers = {"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"}
    chunks: list[str] = []
    with httpx.Client(
        timeout=TIMEOUT_SECONDS, follow_redirects=True, headers=headers, verify=False,
    ) as client:
        home_html = ""
        for scheme in ("https", "http"):
            candidate = f"{scheme}://{domain}"
            home_html = _get(client, candidate)
            if home_html:
                result.url = candidate
                break
        if not home_html:
            result.error = "site injoignable"
            return result

        home_text = html_to_text(home_html)[:MAX_CHARS_PER_PAGE]
        chunks.append(f"[ACCUEIL]\n{home_text}")
        result.pages_fetched.append(result.url)

        # Suit en priorité les liens réellement présents sur l'accueil, sinon
        # tente les chemins usuels — évite de marteler des 404.
        linked = {
            m.group(1).rstrip("/").lower()
            for m in re.finditer(r'href=["\'](/[^"\'#?]{2,60})["\']', home_html)
        }
        ordered = [p for p in CANDIDATE_PATHS if p in linked]
        ordered += [p for p in CANDIDATE_PATHS if p not in linked]

        for path in ordered:
            if len(result.pages_fetched) >= 3:
                break
            page_html = _get(client, f"{result.url}{path}")
            if not page_html:
                continue
            page_text = html_to_text(page_html)[:MAX_CHARS_PER_PAGE]
            if len(page_text) < 200:
                continue
            chunks.append(f"[{path}]\n{page_text}")
            result.pages_fetched.append(f"{result.url}{path}")

    result.text = "\n\n".join(chunks)[:MAX_TOTAL_CHARS]
    if not result.usable:
        result.error = result.error or "texte insuffisant"
    return result


def fetch_many(domains: list[str], *, workers: int = 8) -> dict[str, SiteText]:
    """Récupère plusieurs sites en parallèle. Retourne {domaine: SiteText}.

    Le scraping est le vrai coût de ce chantier, pas le LLM : en séquentiel,
    242 sites × jusqu'à 3 pages × 12 s de timeout = plus d'une heure. Les
    requêtes sont bloquées sur le réseau, donc des threads suffisent.

    `workers` reste modeste : on interroge des centaines d'hôtes DIFFÉRENTS,
    jamais le même en rafale — inutile de taper plus fort.
    """
    from concurrent.futures import ThreadPoolExecutor

    unique = list(dict.fromkeys(d for d in domains if d))
    if not unique:
        return {}
    with ThreadPoolExecutor(max_workers=min(workers, len(unique))) as pool:
        results = pool.map(fetch_site_text, unique)
    return {site.domain: site for site in results}
