"""Filtrage + parsing des contacts BDD PROSPECT EKOALU pour import en Lead.

Source : `enrichis-sirene.json` du projet `BDD PROSPECT` (champ `code_naf` certain).

Décisions :
- canal mail-only : on ne touche pas le schéma `Lead` (linkedin_url/public_identifier
  restent unique+notnull) ; on génère des valeurs synthétiques préfixées
  `bdd-prospect-<siren>` / `https://bdd-prospect.local/siren/<siren>`.
  Le code LinkedIn filtre par URL prefix si besoin.
- Module pur (sans Django) : testable en unitaire, importé par le management command.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator

# --- Filtres NAF/APE (cf. CLAUDE.md prospection-ia §coexistence BDD PROSPECT) ----

NAF_P1 = frozenset({
    "41.20B",  # Construction d'autres bâtiments (EG tertiaires : bureaux/ERP)
    "43.32B",  # Travaux menuiserie métallique et serrurerie (métalleries chantier)
    "25.11Z",  # Fabrication de structures métalliques (charpentes métal)
})
NAF_P2 = frozenset({
    "71.11Z",  # Activités d'architecture (prescripteurs)
    "71.12B",  # Ingénierie, études techniques (BET / MOE)
})
NAF_P3 = frozenset({
    "74.10Z",  # Design (opt.)
})
NAF_EXCLUS = frozenset({
    "25.12Z",  # EKOALU = concurrents directs
    "41.20A",  # Maisons individuelles = habitat, hors stratégie tertiaire
})

# --- Filtres email -----------------------------------------------------------

B2C_DOMAINS = frozenset({
    "wanadoo.fr", "orange.fr", "free.fr", "gmail.com", "googlemail.com",
    "yahoo.fr", "yahoo.com", "ymail.com",
    "hotmail.fr", "hotmail.com", "outlook.fr", "outlook.com", "live.fr",
    "live.com", "msn.com", "icloud.com", "me.com", "mac.com",
    "laposte.net", "sfr.fr", "aliceadsl.fr", "alice.fr", "bbox.fr",
    "neuf.fr", "numericable.fr", "club-internet.fr", "tiscali.fr",
    "voila.fr", "9online.fr", "noos.fr",
})

GENERIC_LOCAL_PARTS = frozenset({
    "contact", "contacts", "info", "infos", "accueil", "commercial",
    "commerce", "secretariat", "secretaire", "compta", "comptabilite",
    "admin", "administration", "direction", "rh", "marketing", "support",
    "service-client", "serviceclient", "hello", "bonjour", "bureau",
    "ets", "etablissement", "sarl", "sa", "sas", "eurl",
})

# --- Préfixes synthétiques pour leads mail-only ------------------------------

SYNTHETIC_URL_PREFIX = "https://bdd-prospect.local/siren/"
SYNTHETIC_PUBLIC_ID_PREFIX = "bdd-prospect-"
CONTACT_EMAIL_SOURCE = "bdd_prospect"


def make_synthetic_linkedin_url(siren: str) -> str:
    return f"{SYNTHETIC_URL_PREFIX}{siren}"


def make_synthetic_public_identifier(siren: str) -> str:
    return f"{SYNTHETIC_PUBLIC_ID_PREFIX}{siren}"


def is_synthetic_lead_url(url: str | None) -> bool:
    """True si l'URL identifie un Lead mail-only issu de BDD PROSPECT."""
    return bool(url) and url.startswith(SYNTHETIC_URL_PREFIX)


# --- Dataclasses -------------------------------------------------------------


@dataclass(frozen=True)
class BddProspectContact:
    """Vue typée d'un row enrichi BDD PROSPECT."""

    email: str
    siren: str
    entreprise: str
    dirigeant: str
    code_naf: str
    cp: str
    dpt: str
    ville: str
    effectif_min: int
    effectif_max: int
    activite: str
    raw: dict = field(repr=False, default_factory=dict)


@dataclass(frozen=True)
class EligibilityFilters:
    """Critères d'éligibilité pour l'import. Tous activables/désactivables."""

    naf_allowed: frozenset = NAF_P1
    naf_excluded: frozenset = NAF_EXCLUS
    min_effectif: int = 10
    require_dirigeant: bool = True
    require_nominative_email: bool = True
    exclude_b2c_domains: bool = True


# --- Codes de rejet (chaînes courtes pour stats) -----------------------------

REJECT_NO_EMAIL = "no_email"
REJECT_NO_SIREN = "no_siren"
REJECT_NAF_EXCLUDED = "naf_excluded"
REJECT_NAF_NOT_TARGET = "naf_not_target"
REJECT_EFFECTIF_TOO_SMALL = "effectif_too_small"
REJECT_NO_DIRIGEANT = "no_dirigeant"
REJECT_EMAIL_GENERIC = "email_generic_local_part"
REJECT_EMAIL_B2C = "email_b2c_domain"


# --- Parsing -----------------------------------------------------------------


def _parse_int(value, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default


def parse_contact(raw: dict) -> BddProspectContact | None:
    """Convertit un row JSON enrichi en BddProspectContact.

    Retourne None si l'email est manquant (cas inutilisable côté canal mail).
    Les autres champs vides sont tolérés (filtrés en aval par is_eligible).
    """
    email = (raw.get("email") or "").strip().lower()
    if not email:
        return None

    props = raw.get("properties") or {}

    return BddProspectContact(
        email=email,
        siren=str(props.get("siren") or "").strip(),
        entreprise=(props.get("entreprise") or "").strip(),
        dirigeant=(props.get("dirigeant") or "").strip(),
        code_naf=(props.get("code_naf") or "").strip().upper(),
        cp=str(props.get("cp") or "").strip(),
        dpt=str(props.get("dpt") or "").strip(),
        ville=(props.get("ville") or "").strip(),
        effectif_min=_parse_int(props.get("effectif_min"), default=0),
        effectif_max=_parse_int(props.get("effectif_max"), default=0),
        activite=(props.get("activite") or "").strip(),
        raw=raw,
    )


# --- Validation --------------------------------------------------------------


def _is_b2c_domain(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    return domain in B2C_DOMAINS


def _is_generic_local_part(email: str) -> bool:
    local = email.split("@", 1)[0] if "@" in email else email
    # On normalise : retire chiffres + ponctuation triviale pour matcher "contact1", "contact-pro" → "contact"
    normalized = local.split("-")[0].split(".")[0].split("_")[0].rstrip("0123456789")
    return normalized in GENERIC_LOCAL_PARTS or local in GENERIC_LOCAL_PARTS


def is_eligible(contact: BddProspectContact, filters: EligibilityFilters) -> str | None:
    """Retourne None si éligible, sinon une raison de rejet (constante REJECT_*)."""
    if not contact.email:
        return REJECT_NO_EMAIL
    if not contact.siren:
        return REJECT_NO_SIREN

    if contact.code_naf in filters.naf_excluded:
        return REJECT_NAF_EXCLUDED
    if contact.code_naf not in filters.naf_allowed:
        return REJECT_NAF_NOT_TARGET

    # Effectif : on accepte si min OU max satisfait le seuil (souvent un seul est rempli)
    eff = max(contact.effectif_min, contact.effectif_max)
    if eff < filters.min_effectif:
        return REJECT_EFFECTIF_TOO_SMALL

    if filters.require_dirigeant and (not contact.dirigeant or contact.dirigeant == "0"):
        return REJECT_NO_DIRIGEANT

    if filters.require_nominative_email and _is_generic_local_part(contact.email):
        return REJECT_EMAIL_GENERIC

    if filters.exclude_b2c_domains and _is_b2c_domain(contact.email):
        return REJECT_EMAIL_B2C

    return None


def iter_eligible(
    raw_rows: Iterable[dict],
    filters: EligibilityFilters,
) -> Iterator[tuple[BddProspectContact, str | None]]:
    """Itère sur (contact, reject_reason) pour chaque row parsable.

    `reject_reason=None` ⇒ contact éligible. Sinon constante REJECT_*.
    Les rows non parsables (email manquant) sont silencieusement skippés.
    """
    for raw in raw_rows:
        contact = parse_contact(raw)
        if contact is None:
            continue
        yield contact, is_eligible(contact, filters)
