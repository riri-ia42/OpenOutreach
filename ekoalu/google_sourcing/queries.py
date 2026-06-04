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
