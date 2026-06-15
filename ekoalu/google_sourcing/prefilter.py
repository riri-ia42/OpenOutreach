"""Pre-filtre des resultats Serper AVANT la lecture LinkedIn (1 lecture = budget
anti-ban). Decide 04 + remarque Richard (15/06) : on ne paie pas la lecture d'un
profil dont le titre/snippet montre deja un metier hors-domaine.

Cible : le residuel d'homonymes / hors-cible evidents que Serper ramene malgre
la requete ``site:linkedin.com/in "Entreprise" "poste"`` (ex. postdoc Princeton
matche sur un nom de famille, avocat, photographe...). On reste CONSERVATEUR :
on n'exclut QUE les metiers sans aucun rapport avec le batiment/la construction
tertiaire. Le tri fin (bon rolemauvaise campagne) reste au LLM.

Kill-switch : env ``EKOALU_SERPER_PREFILTER=0``.
"""
from __future__ import annotations

import os
import unicodedata

# Metiers franchement hors-domaine batiment/construction. Liste volontairement
# etroite : tout faux negatif (profil garde a tort) est juste 1 lecture, mais un
# faux positif (bon profil exclu) est une perte seche. On n'ajoute donc que des
# termes sans ambiguite. Compares en minuscules sans accents sur title+snippet.
OFFDOMAIN_TERMS = (
    "avocat", "juriste", "notaire",
    "photographe", "comedien", "comedienne", "musicien", "artiste", "auteur",
    "psychotherapeute", "psychologue", "therapeute", "coach", "sophrologue",
    "formateur", "formatrice", "enseignant", "enseignante", "professeur",
    "postdoctoral", "post-doctoral", "postdoc", "doctorant", "doctorante",
    "chercheur", "researcher", "phd ",
    "geometre", "data engineer", "data analyst", "data scientist",
    "estheticienne", "coiffeur", "coiffeuse", "dieteticien",
    "restauration scolaire", "evenementiel", "wedding",
    "recrutement", "ressources humaines", "talent acquisition",
    "naturopathe", "osteopathe", "kinesitherapeute",
)


def _normalize(text: str) -> str:
    """minuscules + suppression des accents (NFKD)."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def prefilter_enabled() -> bool:
    return os.environ.get("EKOALU_SERPER_PREFILTER", "1").lower() in ("1", "true", "yes")


def is_offdomain(title: str = "", snippet: str = "") -> bool:
    """True si le titre/snippet revele un metier clairement hors-domaine."""
    blob = _normalize(f"{title} {snippet}")
    return any(term in blob for term in OFFDOMAIN_TERMS)


def passes_prefilter(result: dict) -> bool:
    """True si le resultat Serper merite une lecture LinkedIn.

    Conservateur : on ne rejette QUE le hors-domaine evident. Si le pre-filtre
    est desactive, tout passe.
    """
    if not prefilter_enabled():
        return True
    return not is_offdomain(result.get("title", ""), result.get("snippet", ""))
