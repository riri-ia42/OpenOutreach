"""Identité de personne pour l'anti-doublon inter-leads (Richard 27/08).

Le dedup historique compare l'email exact — insuffisant : le lead « société »
(contact@entreprise.fr, dirigeant renseigné) et la personne du groupe
d'influence (prenom.nom@entreprise.fr) désignent souvent le MÊME humain.
On compare donc (siren, nom normalisé) : accents retirés, casse ignorée,
tokens triés (« Jean-Phillippe Mazet » == « MAZET Jean Phillippe »).
"""
from __future__ import annotations

import re
import unicodedata


def norm_person_name(name: str) -> str:
    """Normalise un nom de personne pour comparaison ('' si vide/illisible)."""
    if not name:
        return ""
    ascii_ = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    tokens = sorted(re.findall(r"[a-z]+", ascii_.lower()))
    return " ".join(tokens)


def same_person(name_a: str, name_b: str) -> bool:
    """True si les deux noms désignent vraisemblablement la même personne."""
    na, nb = norm_person_name(name_a), norm_person_name(name_b)
    return bool(na) and na == nb
