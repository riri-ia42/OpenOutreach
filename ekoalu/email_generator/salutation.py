"""Garde de salutation : ne jamais saluer la mauvaise personne (incident 28/08).

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


def _ascii_tokens(text: str) -> list[str]:
    ascii_ = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return [t for t in re.findall(r"[a-z]{2,}", ascii_.lower())]


def dirigeant_for_salutation(dirigeant: str, contact_email: str) -> str:
    """Nom à utiliser dans la salutation, '' si le nom serait risqué.

    - pas de dirigeant connu, ou pas d'email → tel quel
    - local générique (contact@, accueil@…) → dirigeant conservé
    - local nominatif recoupant le dirigeant (jean.dupont@, jdupont@) → conservé
    - local nominatif SANS recoupement (barrier@ vs Domaison) → '' (Bonjour,)
    """
    if not dirigeant or not contact_email or "@" not in contact_email:
        return dirigeant
    local = contact_email.split("@", 1)[0].lower()
    local_tokens = _ascii_tokens(local)
    if not local_tokens or all(t in _GENERIC_LOCALS for t in local_tokens):
        return dirigeant
    name_tokens = _ascii_tokens(dirigeant)
    for lt in local_tokens:
        for nt in name_tokens:
            # match exact, ou initiale+nom collés ('jdupont' vs 'dupont')
            if lt == nt or (len(lt) > len(nt) and lt.endswith(nt)) \
                    or (len(nt) > len(lt) >= 4 and nt.startswith(lt)):
                return dirigeant
    return ""
