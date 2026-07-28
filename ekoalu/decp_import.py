"""Parsing des cibles DECP (marchés publics attribués) pour import en Lead.

Source : `seances/decp-cibles-prospection.json` du projet `BDD PROSPECT`,
régénéré chaque dimanche par la séance antichambre (scripts/seance-decp.mjs).
Contenu : titulaires de lots métallerie/serrurerie/menuiserie ext/alu (AURA 92 j)
et menuiseries techniques désenfumage/coupe-feu/pare-balle/anti-effraction
(national 365 j), avec email vérifié MX et leurs marchés (objet, montant, date).

Décisions (Richard 2026-07-28) :
- TOUS les titulaires entrent dans le pipe (pas de filtre effectif/dirigeant/
  email nominatif comme bdd_prospect : un titulaire vient de gagner un lot,
  c'est un lead chaud, un `contact@` suffit).
- `cible_prioritaire=true` (poseur NON-fabricant qui gagne des lots en AURA)
  = consommé EN PREMIER par le vivier cold mail (cf. email_canal/pool.py).
- Le marché gagné sert d'accroche factuelle au cold mail (contexte injecté
  dans la génération — jamais de flatterie, cf. jumeau numérique).
- Dédup inter-sources par SIREN : mêmes identifiants synthétiques que
  bdd_prospect (`bdd-prospect-<siren>`) → un SIREN déjà importé est skippé.

Module pur (sans Django) : testable en unitaire, importé par le command.
"""
from __future__ import annotations

from dataclasses import dataclass, field

CONTACT_EMAIL_SOURCE_DECP = "decp"

#: Nombre max de marchés cités dans le contexte de génération (les plus récents).
MAX_MARCHES_CONTEXTE = 2


@dataclass(frozen=True)
class DecpCible:
    """Vue typée d'une cible du fichier decp-cibles-prospection.json."""

    email: str
    siren: str
    entreprise: str
    code_naf: str
    dirigeant: str
    email_dirigeant: str
    cible_prioritaire: bool
    fabricant: bool
    produit_cible: str
    in_base: bool
    marches: list = field(default_factory=list)
    raw: dict = field(repr=False, default_factory=dict)


REJECT_NO_EMAIL = "no_email"
REJECT_NO_SIREN = "no_siren"
REJECT_NO_MARCHE = "no_marche"


def parse_cible(raw: dict) -> DecpCible | None:
    """Convertit une entrée JSON en DecpCible. None si email manquant."""
    email = (raw.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return None
    return DecpCible(
        email=email,
        siren=str(raw.get("siren") or "").strip(),
        entreprise=(raw.get("entreprise") or "").strip(),
        code_naf=(raw.get("code_naf") or "").strip().upper(),
        dirigeant=(raw.get("dirigeant") or "").strip(),
        email_dirigeant=(raw.get("email_dirigeant") or "").strip().lower(),
        cible_prioritaire=bool(raw.get("cible_prioritaire")),
        fabricant=bool(raw.get("fabricant")),
        produit_cible=(raw.get("produit_cible") or "").strip(),
        in_base=bool(raw.get("in_base")),
        marches=list(raw.get("marches") or []),
        raw=raw,
    )


def is_eligible(cible: DecpCible) -> str | None:
    """None si éligible, sinon un code REJECT_*. Volontairement minimal."""
    if not cible.email:
        return REJECT_NO_EMAIL
    if not cible.siren:
        return REJECT_NO_SIREN
    if not cible.marches:
        return REJECT_NO_MARCHE
    return None


def _format_montant(montant) -> str:
    try:
        return f"{round(float(montant)):,} EUR".replace(",", " ")
    except (TypeError, ValueError):
        return "montant non publié"


def _format_critere(critere: str) -> str:
    return (critere or "lot").replace("_", " ").replace("cpv ", "")


def build_marche_contexte(raw_json: dict | None) -> str:
    """Contexte factuel « marché gagné » injecté dans la génération du cold mail.

    Construit depuis le raw_json de l'EmailLeadData (= l'entrée DECP complète).
    Chaîne vide si pas de marché exploitable (le mail redevient un cold classique).
    """
    marches = list((raw_json or {}).get("marches") or [])
    if not marches:
        return ""
    marches.sort(key=lambda m: m.get("date") or "", reverse=True)
    lignes = []
    for m in marches[:MAX_MARCHES_CONTEXTE]:
        objet = (m.get("objet") or "").strip().replace("\n", " ")
        if len(objet) > 160:
            objet = objet[:157] + "..."
        lignes.append(
            f"- {m.get('date') or 'date inconnue'} : {_format_critere(m.get('critere'))}, "
            f"{_format_montant(m.get('montant'))}, lieu {m.get('lieu') or '?'} — {objet}"
        )
    prioritaire = (raw_json or {}).get("cible_prioritaire")
    note = (
        "Ce prospect POSE mais ne FABRIQUE pas (NAF hors fabrication) : il cherche "
        "un fabricant pour sa fourniture aluminium.\n"
        if prioritaire
        else ""
    )
    return (
        "CONTEXTE COMMERCIAL (source publique DECP, factuel) : cette entreprise vient "
        "de remporter un ou plusieurs marchés publics :\n" + "\n".join(lignes) + "\n"
        + note
        + "Utilise ce marché comme accroche CONCRÈTE et sobre (jamais de félicitations "
        "ni de flatterie) : EKOALU peut fabriquer les menuiseries aluminium de ce type "
        "de chantier. Ne cite ni montant exact ni référence d'avis dans le mail."
    )
