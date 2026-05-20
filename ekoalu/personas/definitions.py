"""Définitions des 8 personas EKOALU pour la prospection.

Source de vérité : MARKETING.md du projet parent.
Chaque persona devient 1 Campaign dans OpenOutreach (via setup_ekoalu).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field

from ekoalu import conf


class PersonaCategory(str, enum.Enum):
    DIRIGEANT_CONNEXE = "c1"
    PRESCRIPTEUR = "c2"
    PROMOTEUR = "c3"
    OUVERTURE = "c4"


@dataclass
class Persona:
    slug: str                       # identifiant unique
    category: PersonaCategory
    priority: int                   # 1 = top priorité
    label: str                      # nom lisible (pour Campaign.name)
    product_docs: str               # description EKOALU pour le LLM
    campaign_objective: str         # objectif (pour LLM)
    search_keywords: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    geo_scope: str = "regional"     # "regional" ou "national"
    booking_link: str = ""          # url Outlook Bookings


# Description produit/société EKOALU commune (réutilisée par toutes les campagnes)
_EKOALU_PRODUCT_BASE = """\
EKOALU est une menuiserie technique basee a Chasselay (Rhone, 69380), specialisee
dans la fabrication et la pose de menuiseries alu, acier et bois technique sur-mesure
pour le marche tertiaire (bureaux, ERP, equipements publics, hotellerie, industries).

Expertise technique reconnue sur les produits niches :
- Coupe-feu EI30, EI60, EI120 (PV d essais fournis, jonctions documentees)
- Desenfumage DENFC, exutoires, facades a amenees d air (NF EN 12101)
- Mur-rideau (facades rideaux, integration verriere)
- Pare-balles BC1 a BC4 (banques, ambassades, sites sensibles)
- Grandes dimensions (coulissants > 3m, baies monumentales)
- Acoustique elevee (Rw > 40 dB, etude POA, vitrages speciaux)

Gammes fournisseurs : Cortizo, Sepalumic, SAPA, Wicona.
Atelier integre. Delais tenus. Tarification claire.

Pas de pose en habitation collective (sauf halls d entree).

Valeurs EKOALU : proximite, technicite, fiabilite, bienveillance sans chichi.
"""


PERSONAS: dict[str, Persona] = {
    "dg_eg_tertiaire": Persona(
        slug="dg_eg_tertiaire",
        category=PersonaCategory.DIRIGEANT_CONNEXE,
        priority=1,
        label="Dirigeant Entreprise Generale tertiaire",
        product_docs=_EKOALU_PRODUCT_BASE + (
            "\nCible specifique : dirigeants d Entreprises Generales (DG, President, "
            "Gerant) de 20 a 300 salaries, sur projets tertiaires bureaux/ERP/hotellerie. "
            "Leurs charges d affaires se heurtent regulierement aux lots menuiseries "
            "techniques (coupe-feu surtout). EKOALU se positionne comme sous-traitant "
            "expert sur ces lots — quand le metier de l industriel s arrete, le notre "
            "commence."
        ),
        campaign_objective=(
            "Construire une relation de top-of-mind avec les dirigeants d EG tertiaires "
            "sur les sujets techniques (coupe-feu, desenfumage, pare-balles). Objectif : "
            "qu ils nous appellent au moment ou leurs charges d affaires sont bloques. "
            "RDV visio qualifie comme conversion cible."
        ),
        search_keywords=[
            "entreprise generale tertiaire",
            "entreprise generale batiment ERP",
            "TCE tertiaire",
            "construction tertiaire bureaux",
        ],
        industries=["Construction", "Civil Engineering", "Building Materials"],
        titles=["Directeur General", "President", "Gerant", "DG", "PDG", "Dirigeant"],
        geo_scope="regional",
    ),

    "dg_charpente_metal": Persona(
        slug="dg_charpente_metal",
        category=PersonaCategory.DIRIGEANT_CONNEXE,
        priority=2,
        label="Dirigeant Charpente metallique",
        product_docs=_EKOALU_PRODUCT_BASE + (
            "\nCible specifique : dirigeants de charpentiers metalliques (10-150 salaries) "
            "qui prennent souvent les lots facade + verriere sur tertiaire, mais bloquent "
            "sur les menuiseries alu techniques (coupe-feu EI60 en jonction charpente, "
            "desenfumage en toiture). EKOALU intervient en sous-traitance sur ces lots "
            "techniques pour leurs chantiers."
        ),
        campaign_objective=(
            "Devenir le sous-traitant menuiserie alu de reference des charpentiers "
            "metalliques de la region sur les lots techniques EI60 et desenfumage."
        ),
        search_keywords=[
            "charpente metallique",
            "construction metallique",
            "structures metalliques batiment",
        ],
        industries=["Construction", "Metal Manufacturing"],
        titles=["Directeur General", "Gerant", "President", "Dirigeant"],
        geo_scope="regional",
    ),

    "dg_metallerie": Persona(
        slug="dg_metallerie",
        category=PersonaCategory.DIRIGEANT_CONNEXE,
        priority=3,
        label="Dirigeant Metallerie / Serrurerie",
        product_docs=_EKOALU_PRODUCT_BASE + (
            "\nCible specifique : dirigeants de metalliers / serruriers (5-80 salaries) "
            "souvent demandes sur lots vitrerie + menuiserie alu. Bloquent sur grandes "
            "dimensions alu et vitrages speciaux acoustiques. EKOALU les soulage sur ces "
            "lots ou la marge ne justifie pas l investissement d expertise."
        ),
        campaign_objective=(
            "Etre identifie comme l expert grandes dimensions et acoustique chez les "
            "metalliers de la region."
        ),
        search_keywords=[
            "metallerie",
            "serrurerie batiment",
            "ferronnerie batiment",
            "vitrerie",
        ],
        industries=["Construction", "Building Materials"],
        titles=["Directeur General", "Gerant", "Dirigeant"],
        geo_scope="regional",
    ),

    "dg_maconnerie_tertiaire": Persona(
        slug="dg_maconnerie_tertiaire",
        category=PersonaCategory.DIRIGEANT_CONNEXE,
        priority=4,
        label="Dirigeant Maconnerie / GO tertiaire",
        product_docs=_EKOALU_PRODUCT_BASE + (
            "\nCible specifique : dirigeants entreprises maconnerie / gros oeuvre tertiaire "
            "(30-200 salaries). Conducteurs de travaux confrontes aux jonctions "
            "menuiserie alu / beton precontraint, scellements, etancheite, planning. "
            "EKOALU intervient en lot menuiserie sur leurs chantiers tertiaires."
        ),
        campaign_objective=(
            "Devenir le menuisier alu prefere des entreprises de gros oeuvre tertiaire "
            "de la region pour les chantiers ou la jonction est technique."
        ),
        search_keywords=[
            "maconnerie tertiaire",
            "gros oeuvre batiment",
            "GO tertiaire",
        ],
        industries=["Construction"],
        titles=["Directeur General", "Gerant", "Dirigeant"],
        geo_scope="regional",
    ),

    "archi_tertiaire": Persona(
        slug="archi_tertiaire",
        category=PersonaCategory.PRESCRIPTEUR,
        priority=5,
        label="Architecte tertiaire",
        product_docs=_EKOALU_PRODUCT_BASE + (
            "\nCible specifique : architectes d agences 2-30 personnes specialisees "
            "tertiaire (bureaux, ERP, retail, hotellerie). Confrontes a la conception "
            "coupe-feu (sectorisation), facades verre/alu coherentes, desenfumage integre. "
            "EKOALU les aide a anticiper les details d execution."
        ),
        campaign_objective=(
            "Etre prescrit dans les CCTP des architectes tertiaires de la region sur "
            "les lots techniques (coupe-feu, desenfumage)."
        ),
        search_keywords=[
            "architecte tertiaire",
            "architecte bureaux",
            "architecte ERP",
        ],
        industries=["Architecture & Planning"],
        titles=["Architecte", "Architecte associe", "Directeur general adjoint"],
        geo_scope="regional",
    ),

    "moe_tertiaire": Persona(
        slug="moe_tertiaire",
        category=PersonaCategory.PRESCRIPTEUR,
        priority=6,
        label="MOE / Economiste tertiaire",
        product_docs=_EKOALU_PRODUCT_BASE + (
            "\nCible specifique : cabinets de maitrise d oeuvre et economistes de la "
            "construction sur projets tertiaires. Cherchent fiabilite + clarte des "
            "chiffrages sur lots techniques (devis detailles avec PV d essais)."
        ),
        campaign_objective=(
            "Devenir le sous-traitant menuiserie alu prefere des MOE tertiaires pour "
            "la clarte des chiffrages et la fiabilite des delais."
        ),
        search_keywords=[
            "maitrise d oeuvre tertiaire",
            "economiste construction",
            "MOE batiment",
        ],
        industries=["Architecture & Planning", "Construction"],
        titles=["Maitre d oeuvre", "Economiste", "Directeur"],
        geo_scope="regional",
    ),

    "bet_prescripteur": Persona(
        slug="bet_prescripteur",
        category=PersonaCategory.PRESCRIPTEUR,
        priority=7,
        label="BET / Controleur technique",
        product_docs=_EKOALU_PRODUCT_BASE + (
            "\nCible specifique : bureaux d etudes techniques (fluides, securite, "
            "acoustique) et controleurs techniques (Apave, Bureau Veritas, Socotec). "
            "EKOALU fournit PV d essais et details d execution avant chantier pour "
            "simplifier l instruction."
        ),
        campaign_objective=(
            "Devenir une reference technique pour les BET et controleurs sur les "
            "menuiseries alu coupe-feu et desenfumage."
        ),
        search_keywords=[
            "bureau d etudes securite incendie",
            "BET acoustique",
            "controleur technique batiment",
            "Apave Bureau Veritas Socotec",
        ],
        industries=["Civil Engineering", "Architecture & Planning"],
        titles=["Ingenieur", "Charge d affaires", "Responsable technique"],
        geo_scope="national",  # niches techniques → national
    ),

    "promoteur_tertiaire": Persona(
        slug="promoteur_tertiaire",
        category=PersonaCategory.PROMOTEUR,
        priority=8,
        label="Promoteur tertiaire",
        product_docs=_EKOALU_PRODUCT_BASE + (
            "\nCible specifique : petits et moyens promoteurs immobiliers tertiaire / "
            "mixte, foncieres. Sensibles a la rentabilite, a la conformite RE2020 "
            "tertiaire, aux FDES et au planning tenu."
        ),
        campaign_objective=(
            "Etre referencee dans les programmes tertiaires des promoteurs de la "
            "region en tant que fournisseur fiable sur les lots techniques."
        ),
        search_keywords=[
            "promoteur immobilier tertiaire",
            "fonciere tertiaire",
            "promoteur bureaux",
        ],
        industries=["Real Estate", "Construction"],
        titles=["Directeur de programmes", "Directeur general", "President"],
        geo_scope="regional",
    ),
}


def get_persona(slug: str) -> Persona:
    """Retourne un persona par son slug. KeyError si inexistant."""
    return PERSONAS[slug]


def list_personas_by_priority() -> list[Persona]:
    """Retourne les personas dans l ordre de priorite croissante."""
    return sorted(PERSONAS.values(), key=lambda p: p.priority)


# Hydrate les booking_link à l'import (post-construction pour éviter les imports circulaires)
for _p in PERSONAS.values():
    _p.booking_link = conf.CALENDAR_BOOKING_URL
