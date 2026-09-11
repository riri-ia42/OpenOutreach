"""Garde de salutation : ne jamais saluer la mauvaise personne (incident 28/08,
capture Richard 11/09).

Le cold mail ABAC saluait « Bonjour M. Domaison » (dirigeant légal au registre)
alors que l'adresse récoltée était barrier@abac-ingenierie.fr — un directeur
d'agence. Réponse du prospect : « Il n'y a pas de M. Domaison chez nous. »

Règle : si l'adresse de destination est NOMINATIVE (prenom.nom@, jdupont@…)
et que son nom ne recoupe pas le dirigeant connu, on n'utilise PAS le nom du
dirigeant — « Bonjour, » est toujours moins risqué qu'un mauvais nom. Une
adresse générique (contact@, accueil@…) laisse le nom du dirigeant : le mail
lui est adressé via la boîte commune, c'est légitime.
"""
from __future__ import annotations

import re
import unicodedata

# Locals d'adresses génériques : le mail arrive dans une boîte commune, saluer
# le dirigeant nominativement reste correct.
_GENERIC_LOCALS = {
    "contact", "info", "infos", "accueil", "bonjour", "hello", "agence",
    "commercial", "commerce", "vente", "ventes", "sales", "direction",
    "secretariat", "administration", "admin", "compta", "comptabilite",
    "gestion", "devis", "etudes", "be", "bureau", "sa", "sas", "sarl",
    "eurl", "societe", "entreprise", "mail", "courrier", "office",
}

# Marqueurs de PERSONNE MORALE dans le champ « dirigeant » des imports DECP :
# le registre donne parfois le mandataire (cabinet comptable, holding) et non
# une personne physique — « CABINET EMMANUEL CHEVIGNARD », « ARDOUREL &
# MATHONIER », « BROCARD PARTICIPATIONS » (capture Richard 11/09).
_COMPANY_MARKERS = frozenset({
    "cabinet", "sas", "sasu", "sarl", "eurl", "sa", "sci", "scp", "scop", "selarl",
    "snc", "gie", "holding", "participations", "groupe", "group", "associes",
    "associates", "expertise", "comptable", "comptables", "audit", "fiduciaire",
    "conseil", "conseils", "ets", "etablissements", "entreprise", "entreprises",
    "societe", "compagnie", "cie", "industrie", "industries", "batiment", "immobilier",
    "menuiserie", "serrurerie", "metallerie", "construction", "constructions",
    "developpement", "invest", "investissement", "investissements", "finance",
})

# Particules qui restent en minuscules au milieu d'un nom.
_PARTICLES = frozenset({"de", "du", "des", "da", "di", "le", "la", "les", "von", "van", "der", "el", "al"})


def clean_person_name(name: str) -> str:
    """Casse propre d'un nom : « Charles Tassin de saint pereuse » →
    « Charles Tassin de Saint Pereuse ». Les particules restent minuscules sauf
    en tête. Les imports DECP livrent des casses très irrégulières."""
    parts = (name or "").split()
    out = []
    for i, raw in enumerate(parts):
        low = _ascii_tokens(raw)
        token = low[0] if low else ""
        if i and token in _PARTICLES:
            out.append(raw.lower())
            continue
        out.append("-".join(w[:1].upper() + w[1:].lower() for w in raw.split("-")))
    return " ".join(out)


def is_person_name(name: str) -> bool:
    """True si `name` ressemble à une personne physique nommable.

    Exige au moins deux mots alphabétiques (prénom + nom), sans chiffre, sans
    « & », et sans marqueur de personne morale. Écarte donc « VIMABER »,
    « V2R », « DELT'AX », « CHLOE » (prénom seul), « CABINET X », « A & B ».
    """
    raw = (name or "").strip()
    if not raw or "&" in raw or any(ch.isdigit() for ch in raw):
        return False
    words = [w for w in raw.split() if _ascii_tokens(w)]
    if len(words) < 2:
        return False
    tokens = _ascii_tokens(raw)
    return bool(tokens) and not (set(tokens) & _COMPANY_MARKERS)


def _ascii_tokens(text: str) -> list[str]:
    ascii_ = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return [t for t in re.findall(r"[a-z]{2,}", ascii_.lower())]


def _is_company_mailbox(local_tokens: list[str], entreprise: str) -> bool:
    """Boîte commune de la société : tous les tokens du local sont génériques,
    repris de la raison sociale (serrurerie-nouvelle@) ou son acronyme (vmv@).

    Égalité EXACTE seulement : « ablampey » ne vaut pas « blampey » — c'est
    l'initiale plus le nom d'une personne, donc une adresse nominative.
    """
    company_tokens = set(_ascii_tokens(entreprise))
    acronym = "".join(t[:1] for t in _ascii_tokens(entreprise) if len(t) > 1)
    # « V.M.V. » ne donne aucun token de 2 lettres : on compare aussi la forme
    # compacte (lettres collées, mentions juridiques retirées) → 'vmv'.
    letters = unicodedata.normalize("NFKD", entreprise or "").encode("ascii", "ignore").decode()
    compact = "".join(ch for ch in letters.lower() if ch.isalpha())
    for marker in ("sas", "sarl", "eurl", "sasu", "sa", "sci", "snc"):
        if compact.endswith(marker):
            compact = compact[: -len(marker)]
    return all(
        t in _GENERIC_LOCALS or t in company_tokens
        or (len(acronym) > 1 and t == acronym) or (len(compact) > 1 and t == compact)
        for t in local_tokens
    )


def dirigeant_for_salutation(dirigeant: str, contact_email: str, entreprise: str = "") -> str:
    """Nom à utiliser dans la salutation, '' si le nom serait risqué.

    - dirigeant qui n'est pas une personne physique (cabinet, holding, prénom
      seul) → '' (capture Richard 11/09)
    - pas de dirigeant connu, ou pas d'email → tel quel
    - boîte commune de la société (contact@, serrurerie-nouvelle@, vmv@) →
      dirigeant conservé : le mail arrive dans une boîte partagée
    - local nominatif recoupant le dirigeant (jean.dupont@, jdupont@) → conservé
    - local nominatif SANS recoupement (barrier@ vs Domaison) → '' (Bonjour,)
    """
    if dirigeant and not is_person_name(dirigeant):
        return ""
    if not dirigeant or not contact_email or "@" not in contact_email:
        return dirigeant
    local = contact_email.split("@", 1)[0].lower()
    local_tokens = _ascii_tokens(local)
    if not local_tokens or _is_company_mailbox(local_tokens, entreprise):
        return dirigeant
    name_tokens = _ascii_tokens(dirigeant)
    for lt in local_tokens:
        for nt in name_tokens:
            # match exact, initiale+nom collés ('jdupont' vs 'dupont'), ou nom
            # noyé dans un local composite ('felicitedavidpro' vs 'felicite')
            if lt == nt or (len(lt) > len(nt) and lt.endswith(nt)) \
                    or (len(nt) > len(lt) >= 4 and nt.startswith(lt)) \
                    or (len(nt) >= 4 and nt in lt):
                return dirigeant
    return ""
