"""Groupe d'influence : emails de PERSONNES PHYSIQUES des entreprises prioritaires.

Décision Richard 2026-07-28 : pour les entreprises prioritaires (titulaires DECP),
ne pas se contenter de contact@/info@ — constituer un groupe d'influence dans
chaque boîte (dirigeant + interlocuteurs identifiés) pour multiplier les contacts.

Sources d'emails nominatifs, par fiabilité décroissante :
1. SITE : pages équipe/contact/mentions du site officiel (emails publiés)
2. GOOGLE (Serper, si configuré) : recherche `"@domaine"` — emails publiés ailleurs
   (annuaires pro, PDF d'appels d'offres, presse locale)
3. PATTERN : le schéma d'adressage de la boîte est déduit des exemples trouvés
   (prenom.nom@, p.nom@, prenom@…) puis appliqué aux dirigeants connus de
   l'API recherche-entreprises → CANDIDATS (non vérifiés mailbox, marqués comme tels ;
   le bounce handling les sortira du pipe en cas d'échec).

Règles qualité :
- jamais un local-part générique (contact, info, compta… cf. bdd_prospect_import)
- jamais un domaine webmail (le domaine doit appartenir à l'entreprise)
- un local-part sans séparateur (jdupont) n'est retenu QUE s'il matche un
  dirigeant connu — sinon impossible de distinguer une personne d'un alias.

Module pur (réseau injecté par le command) : testable en unitaire.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ekoalu.bdd_prospect_import import B2C_DOMAINS, GENERIC_LOCAL_PARTS

#: Pages du site sondées pour trouver des emails nominatifs.
SITE_PAGES = (
    "/", "/contact", "/contact/", "/equipe", "/notre-equipe", "/team",
    "/qui-sommes-nous", "/a-propos", "/about", "/mentions-legales",
    "/mentions-legales/", "/societe", "/entreprise",
)

#: Local-parts poubelle jamais retenus (en plus des génériques).
JUNK_LOCALS = frozenset({
    "noreply", "no-reply", "webmaster", "postmaster", "abuse", "privacy",
    "dpo", "rgpd", "recrutement", "candidature", "sav", "devis", "commande",
    "facturation", "achat", "achats", "qualite", "hse", "communication",
})

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

SOURCE_SITE = "site"
SOURCE_GOOGLE = "google"
SOURCE_PATTERN = "pattern_candidate"


@dataclass(frozen=True)
class Person:
    """Un membre du groupe d'influence d'une entreprise."""

    email: str
    display_name: str  # "Jean Dupont" ou "J. Dupont" — sert à la personnalisation
    source: str  # site | google | pattern_candidate
    role: str = ""  # "dirigeant" quand issu de l'API, sinon ""


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:].lower() if s else ""


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def is_company_domain(domain: str) -> bool:
    """True si le domaine peut appartenir à l'entreprise (pas un webmail)."""
    return bool(domain) and domain not in B2C_DOMAINS


# --- Extraction depuis HTML / snippets ---------------------------------------


def extract_domain_emails(text: str, domain: str) -> set[str]:
    """Tous les emails @domain trouvés dans un texte, en minuscules."""
    out = set()
    for e in EMAIL_RE.findall(text or ""):
        e = e.lower().strip(".")
        if email_domain(e) == domain and len(e) <= 60:
            out.add(e)
    return out


def _local_tokens(local: str) -> list[str]:
    return [t for t in re.split(r"[._-]", local) if t]


def is_generic_local(local: str) -> bool:
    norm = local.rstrip("0123456789")
    tokens = _local_tokens(norm)
    if not tokens:
        return True
    return (
        norm in GENERIC_LOCAL_PARTS
        or norm in JUNK_LOCALS
        or tokens[0] in GENERIC_LOCAL_PARTS
        or tokens[0] in JUNK_LOCALS
    )


def local_to_person_name(local: str) -> str:
    """Nom affichable depuis un local-part nominatif. "" si indécidable.

    jean.dupont → "Jean Dupont" ; j.dupont → "J. Dupont" ; jdupont → "" (ambigu).
    """
    tokens = _local_tokens(local.rstrip("0123456789"))
    if len(tokens) < 2 or len(tokens) > 3:
        return ""
    if any(not t.isalpha() for t in tokens):
        return ""
    parts = []
    for t in tokens:
        parts.append(f"{t.upper()}." if len(t) == 1 else _cap(t))
    return " ".join(parts)


def match_dirigeant(local: str, dirigeants: list[tuple[str, str]]) -> str:
    """Si un local-part sans séparateur correspond à un dirigeant, retourne son nom.

    dirigeants = [(prenom, nom), ...]. Match : jdupont / jean.dupont / dupontj /
    jeandupont / dupont pour ("Jean", "Dupont").
    """
    l = _strip_accents(local.lower()).rstrip("0123456789")
    for prenom, nom in dirigeants:
        p = _strip_accents((prenom or "").lower())
        n = _strip_accents((nom or "").lower()).replace("-", "").replace(" ", "")
        if not n or len(n) < 3:
            continue
        candidates = {n, f"{p}{n}", f"{n}{p}", f"{p[:1]}{n}", f"{n}{p[:1]}"}
        candidates |= {f"{p}.{n}", f"{p[:1]}.{n}", f"{p}-{n}", f"{p}_{n}"} if p else set()
        if l.replace(".", "").replace("-", "").replace("_", "") in {
            c.replace(".", "").replace("-", "").replace("_", "") for c in candidates
        }:
            return f"{_cap(prenom)} {_cap(nom)}".strip()
    return ""


def nominative_people_from_emails(
    emails: set[str], dirigeants: list[tuple[str, str]], source: str,
) -> list[Person]:
    """Filtre un lot d'emails @domaine → personnes physiques identifiables."""
    people = []
    for e in sorted(emails):
        local = e.split("@", 1)[0]
        if is_generic_local(local):
            continue
        name = local_to_person_name(local)
        role = ""
        if not name:
            name = match_dirigeant(local, dirigeants)
            role = "dirigeant" if name else ""
        if not name:
            continue  # alias indécidable (jdupont inconnu, devis69…)
        people.append(Person(email=e, display_name=name, source=source, role=role))
    return people


# --- Pattern d'adressage ------------------------------------------------------


def detect_pattern(examples: list[str]) -> str:
    """Déduit le schéma d'adressage depuis des local-parts nominatifs.

    Retourne 'prenom.nom' (défaut), 'p.nom', 'prenom-nom', 'prenom' ou 'nom'.
    """
    votes: dict[str, int] = {}
    for local in examples:
        tokens = _local_tokens(local)
        if len(tokens) == 2:
            sep = "." if "." in local else "-" if "-" in local else "_"
            key = ("p" if len(tokens[0]) == 1 else "prenom") + sep + "nom"
            votes[key] = votes.get(key, 0) + 1
        elif len(tokens) == 1 and tokens[0].isalpha():
            votes["prenom"] = votes.get("prenom", 0) + 1
    if not votes:
        return "prenom.nom"
    return max(votes, key=lambda k: votes[k])


def apply_pattern(pattern: str, prenom: str, nom: str) -> str:
    """Construit le local-part d'un dirigeant selon le pattern détecté."""
    p = _strip_accents((prenom or "").lower()).replace(" ", "").replace("-", "")
    n = _strip_accents((nom or "").lower()).replace(" ", "").replace("-", "")
    if not p or not n:
        return ""
    table = {
        "prenom.nom": f"{p}.{n}", "p.nom": f"{p[0]}.{n}",
        "prenom-nom": f"{p}-{n}", "p-nom": f"{p[0]}-{n}",
        "prenom_nom": f"{p}_{n}", "p_nom": f"{p[0]}_{n}",
        "prenom": p, "nom": n,
    }
    return table.get(pattern, f"{p}.{n}")


def candidates_for_dirigeants(
    dirigeants: list[tuple[str, str]],
    domain: str,
    known_locals: set[str],
    pattern: str,
) -> list[Person]:
    """Emails candidats (non vérifiés) pour les dirigeants sans email trouvé."""
    out = []
    for prenom, nom in dirigeants:
        if not prenom or not nom:
            continue  # dénomination PM ou état civil incomplet : pas de candidat
        local = apply_pattern(pattern, prenom, nom)
        if not local or local in known_locals:
            continue
        out.append(Person(
            email=f"{local}@{domain}",
            display_name=f"{_cap(prenom)} {_cap(nom)}",
            source=SOURCE_PATTERN,
            role="dirigeant",
        ))
    return out


def build_influence_group(
    *,
    domain: str,
    site_emails: set[str],
    google_emails: set[str],
    dirigeants: list[tuple[str, str]],
    existing_emails: set[str],
    max_persons: int = 3,
) -> list[Person]:
    """Assemble le groupe d'influence d'une entreprise, dédupliqué et plafonné.

    Ordre de fiabilité : site > google > pattern. `existing_emails` (déjà en DB
    ou email générique de la boîte) sont exclus.
    """
    people: list[Person] = []
    seen = {e.lower() for e in existing_emails}
    for source, emails in ((SOURCE_SITE, site_emails), (SOURCE_GOOGLE, google_emails)):
        for person in nominative_people_from_emails(emails, dirigeants, source):
            if person.email in seen:
                continue
            seen.add(person.email)
            people.append(person)
    nominative_locals = [p.email.split("@", 1)[0] for p in people]
    pattern = detect_pattern(nominative_locals)
    known_names = {_strip_accents(p.display_name.lower()) for p in people}
    for cand in candidates_for_dirigeants(
        dirigeants, domain, {*nominative_locals}, pattern,
    ):
        if cand.email in seen:
            continue
        if _strip_accents(cand.display_name.lower()) in known_names:
            continue  # la personne a déjà un email trouvé (vrai > candidat)
        seen.add(cand.email)
        people.append(cand)
    return people[:max_persons]
