"""Construction des requetes Google par campagne ABM (cible 1 entreprise).

Postes = criteres Richard (operationnels + decideurs + achats). Le qualifier
Claude filtrera ensuite les fonctions support / hors-cible ; ici on ratisse.
"""
from __future__ import annotations

# Un terme = une requete (10 resultats max chacune). Garde la liste courte pour
# menager le quota 100/jour (cf. --max-queries de la commande).
ABM_ROLE_TERMS = [
    "directeur",
    "conducteur de travaux",
    "chargé d'affaires",
    "responsable travaux",
    "métreur",
    "bureau d'études",
    "économiste",
    "acheteur",
    "chef d'agence",
]


def target_company_name(campaign) -> str | None:
    """Nom de l'entreprise ciblee : via AbmCampaignLink si present, sinon parse
    du nom de campagne 'EKOALU - ABM - <Entreprise>'."""
    try:
        link = campaign.abm_link
    except Exception:
        link = None
    if link is not None and link.target_company_id:
        name = (link.target_company.name or "").strip()
        if name:
            return name
    name = campaign.name or ""
    if " ABM - " in name:
        return name.split(" ABM - ", 1)[1].strip()
    return None


def build_abm_queries(campaign) -> list[str]:
    """Une requete par poste, restreinte aux profils LinkedIn de l'entreprise."""
    company = target_company_name(campaign)
    if not company:
        return []
    return [f'site:linkedin.com/in "{company}" "{role}"' for role in ABM_ROLE_TERMS]


# --- Campagnes SECTEUR (Serper par secteur d'activite + poste, pas par entreprise) ---
# Contrairement a l'ABM (1 entreprise), on cible un SECTEUR : ancre metier + postes
# + biais geographique. Une entree = un slug parse depuis le nom 'EKOALU - SECTEUR - <slug>'.
# Requetes validees en live le 01/07 : "logement social" ramene de vrais bailleurs
# (LOGIREP, CDC Habitat, GrandLyon Habitat...) la ou "bailleur social" ramene du BTP
# generique ; ajouter "Lyon" biaise nettement vers Rhone-Alpes.
SECTOR_SPECS: dict[str, dict] = {
    "bailleurs sociaux ra": {
        "anchor": "logement social",
        "roles": [
            "responsable travaux",
            "directeur du patrimoine",
            "chargé d'opérations",
            "responsable réhabilitation",
            "responsable maintenance",
            "responsable entretien",
        ],
        # Biais Rhone-Alpes (Richard : "regional d'abord"). Elargir = ajouter des villes
        # (Grenoble, Saint-Étienne...) en gardant roles x regions <= --per-campaign-queries (9).
        "regions": ["Lyon"],
    },
}


def sector_slug(campaign) -> str | None:
    """Slug secteur parse du nom 'EKOALU - SECTEUR - <slug>' (normalise minuscule)."""
    name = campaign.name or ""
    if " SECTEUR - " not in name:
        return None
    return name.split(" SECTEUR - ", 1)[1].strip().lower()


def build_sector_queries(campaign) -> list[str]:
    """Requetes Serper d'une campagne SECTEUR : ancre metier x poste x biais geo."""
    spec = SECTOR_SPECS.get(sector_slug(campaign) or "")
    if not spec:
        return []
    anchor = spec["anchor"]
    return [
        f'site:linkedin.com/in "{anchor}" "{role}" {region}'
        for role in spec["roles"]
        for region in spec["regions"]
    ]


def build_queries(campaign) -> list[str]:
    """Dispatcher : requetes Serper selon le type de campagne (ABM ou SECTEUR)."""
    if sector_slug(campaign):
        return build_sector_queries(campaign)
    return build_abm_queries(campaign)
