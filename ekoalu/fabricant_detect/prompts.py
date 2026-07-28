"""Grille de classification fabricant / revendeur-poseur.

⚠️ Prompt INTERNE — jamais envoyé à un tiers, jamais cité en externe.

Le code NAF ne suffit pas : il est déclaratif, jamais audité. Un métallier-poseur
déclaré en 43.32B peut très bien avoir un atelier, et un 25.12Z peut ne plus
faire que du négoce. On lit donc le site web.

**Le piège n°1 (remarque Richard 28/07)** : une société qui affiche « alu, PVC,
bois, acier » ressemble à un fabricant alors que c'est presque toujours un
REVENDEUR. Personne n'a simultanément une chaîne alu, une soudeuse PVC et un
atelier bois — la largeur de catalogue est un marqueur de négoce, pas de
production.

**Le discriminant réel** : ce n'est pas d'acheter de la matière à un gammiste
(EKOALU achète ses profilés et fabrique bel et bien), c'est ce qu'on en fait.
- Transformer en atelier (coupe, usinage, assemblage, vitrage) = FABRICANT
- Poser des produits finis achetés tout faits                 = REVENDEUR/POSEUR
"""
from __future__ import annotations

# Gammistes : vendent des PROFILÉS aux fabricants. Les citer est AMBIGU —
# un fabricant nomme son gammiste, un revendeur peut aussi afficher le logo.
GAMMISTES = (
    "Cortizo", "Sepalumic", "SAPA", "Wicona", "Technal", "Schüco", "Schuco",
    "Reynaers", "Kawneer", "Profils Systèmes", "Hydro", "Aliplast", "Alumil",
)

# Marques / réseaux de PRODUITS FINIS : les afficher comme offre est un
# marqueur FORT de négoce — on revend leur catalogue, on ne fabrique pas.
MARQUES_PRODUITS_FINIS = (
    "K-Line", "Kline", "Tryba", "Janneau", "Bel'M", "BelM", "Zilten", "Solabaie",
    "Art & Fenêtres", "Art et Fenêtres", "Komilfo", "Lapeyre", "Millet",
    "Internorm", "Grosfillex", "Oknoplast", "Atrya", "Monsieur Store",
    "Storistes de France", "Réseau Confort", "Pro&Cie",
)

SYSTEM_PROMPT = """Tu analyses le site web d'une entreprise du bâtiment pour une seule question : \
est-ce qu'elle FABRIQUE des menuiseries dans son propre atelier, ou est-ce qu'elle REVEND et POSE \
des produits fabriqués par d'autres ?

Cette distinction sert à qualifier des partenaires industriels. Un faux positif coûte cher : \
on approcherait un simple poseur comme un confrère fabricant.

## Le discriminant

Acheter de la matière première à un gammiste ne disqualifie PAS : tous les fabricants de \
menuiserie alu achètent leurs profilés (Cortizo, Sepalumic, SAPA, Wicona, Technal, Schüco, \
Reynaers, Profils Systèmes...). Ce qui compte, c'est ce qu'ils en font :

- Ils COUPENT, USINENT, ASSEMBLENT, VITRENT dans leur atelier → **fabricant**
- Ils reçoivent des menuiseries finies et les POSENT → **revendeur_poseur**

## Indices de FABRICATION

- « notre atelier », « nos ateliers », « notre unité de production », « notre usine »
- « fabriqué dans nos ateliers », « fabrication française », « fabrication sur mesure »
- Machines nommées : centre d'usinage, tronçonneuse, sertisseuse, presse, banc de soudure, \
cabine de thermolaquage, table de vitrage, CN / commande numérique
- Pages « notre parc machines », « notre outil de production », « visite de l'atelier »
- Surface d'atelier / m² de production annoncés
- Marquage CE apposé sur SES ouvrages, PV d'essais en propre, avis technique en son nom

## Indices de NÉGOCE (souvent décisifs)

- **Catalogue multi-matériaux : alu ET PVC ET bois (et/ou acier).** C'est le marqueur le plus \
fiable de revente : personne n'exploite en propre une chaîne alu, une soudeuse PVC et un atelier \
bois. Une entreprise qui propose les trois REVEND, sauf preuve d'atelier explicite et \
circonstanciée pour chacun.
- Marques de produits FINIS affichées comme offre : K-Line, Tryba, Janneau, Bel'M, Zilten, \
Solabaie, Art & Fenêtres, Komilfo, Lapeyre, Millet, Internorm, Grosfillex, Oknoplast...
- Vocabulaire de distribution : « showroom », « concessionnaire », « partenaire agréé », \
« distributeur », « revendeur », « point de vente », « nos partenaires fabricants », \
« franchisé », « adhérent réseau »
- Le site ne parle que de pose, rénovation, dépannage, devis — jamais de production
- Catalogue très large hors menuiserie (volets roulants, portails, stores, vérandas, \
portes de garage, alarmes) sans aucune mention d'atelier

## Règles de décision

1. **RÈGLE IMPÉRATIVE.** Catalogue de trois matériaux ou plus (typiquement alu + PVC + bois) \
SANS mention d'atelier explicite → `revendeur_poseur`, confiance `haute`. Si tu constates \
toi-même la largeur du catalogue et l'absence d'atelier, tu as déjà la réponse : ne réponds \
PAS `indetermine`. Une entreprise qui affiche trois matières sans jamais parler de sa \
production est un revendeur, c'est le cas le plus fréquent du métier.
2. Mention d'atelier claire et circonstanciée (machines, surface, photos de production) → \
`fabricant`.
3. « Fabrication sur mesure » SEUL, sans atelier ni machines, ne suffit pas : les revendeurs \
l'emploient pour du produit fini configuré. Reste `indetermine` si rien d'autre.
4. Site vide, en construction, page parking, ou texte trop pauvre → `indetermine` avec \
confiance basse. Ne devine jamais à partir du seul nom de l'entreprise.
5. Poser ET fabriquer est courant : la pose ne disqualifie pas si l'atelier est attesté.
6. **Vérifie d'abord que le site est bien celui de l'entreprise.** Le domaine vient de \
l'adresse email et peut pointer ailleurs : hébergeur mutualisé, site racheté, homonyme, \
activité sans aucun rapport (restaurant, association, site étranger). Si le contenu ne \
correspond ni au nom de l'entreprise ni au bâtiment, réponds `indetermine` en confiance \
basse et dis-le dans la justification — ne classe jamais une société sur le site de \
quelqu'un d'autre.
7. Une activité clairement hors menuiserie/métallerie (parquet, peinture, plomberie, \
couverture) n'est pas un fabricant de menuiserie : `revendeur_poseur` ne convient pas non \
plus — réponds `indetermine` et signale l'activité réelle dans la justification.

Sois strict sur les preuves. Dans le doute, `indetermine` — un verdict incertain sera \
réexaminé, un faux verdict ne le sera pas."""


def build_user_prompt(entreprise: str, code_naf: str, ville: str,
                      url: str, page_text: str) -> str:
    """Message utilisateur : la fiche société + le texte du site."""
    return (
        f"Entreprise : {entreprise or '(inconnue)'}\n"
        f"Code NAF déclaré : {code_naf or '(inconnu)'}\n"
        f"Ville : {ville or '(inconnue)'}\n"
        f"Site : {url}\n\n"
        f"--- TEXTE DU SITE ---\n{page_text}\n--- FIN ---\n\n"
        "Classe cette entreprise."
    )


# Structured output : le modèle ne peut pas renvoyer autre chose que ce schéma.
VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["fabricant", "revendeur_poseur", "indetermine"],
        },
        "confiance": {
            "type": "string",
            "enum": ["haute", "moyenne", "basse"],
            "description": "haute = preuves explicites ; basse = texte pauvre ou contradictoire",
        },
        "materiaux": {
            "type": "array",
            "items": {"type": "string", "enum": ["alu", "pvc", "bois", "acier", "verre", "autre"]},
            "description": "Matériaux proposés par l'entreprise",
        },
        "indices_fabrication": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Citations courtes du site attestant une production propre",
        },
        "indices_negoce": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Citations courtes du site attestant de la revente",
        },
        "marques_produits_finis": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Marques de produits finis affichées (K-Line, Tryba...)",
        },
        "justification": {
            "type": "string",
            "description": "Une phrase, factuelle, appuyée sur le texte du site",
        },
    },
    "required": [
        "verdict", "confiance", "materiaux", "indices_fabrication",
        "indices_negoce", "marques_produits_finis", "justification",
    ],
    "additionalProperties": False,
}
