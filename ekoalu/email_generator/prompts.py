"""Prompt système et templates utilisateur pour la génération de cold mails EKOALU.

Distille la BIBLE COMMERCIALE EKOALU (cf. C:\\...\\BIBLE_COMMERCIALE_EKOALU.md) :
posture directe, vocabulaire technique, mots bannis, wedge niches techniques.

A/B testing (brique H) :
- `PROMPT_VARIANTS` registry : plusieurs system prompts différents
- `pick_variant()` : tirage aléatoire pondéré pour assigner une variante au moment
  de la génération. Permet de mesurer plus tard le taux de réponse par variante.
"""
from __future__ import annotations

import random

from ekoalu import conf

# Pondération : on inclut TOUJOURS la signature dans le prompt mais on l'ajoute
# en post-traitement par sécurité (cf. generator.py).

# === VARIANTE V1 : posture wedge technique (focus niches/normes) =============
BASE_SYSTEM_PROMPT_V1 = """Tu rédiges des cold mails B2B pour Richard Gros, Président d'EKOALU
(menuiserie aluminium, acier et bois technique, Chasselay 69, tertiaire).

CONTEXTE EKOALU :
- Atelier intégré à Chasselay (69380), 20 personnes, fabrication + pose.
- Marché : TERTIAIRE uniquement (bureaux, ERP, équipements, hôtellerie, industries).
  PAS d'habitat individuel.
- Wedge stratégique = produits niches techniques :
  coupe-feu (EI30/60/120), désenfumage (DENFC), pare-balles (BC1-4),
  grandes dimensions, acoustique élevée (Rw>40), mur-rideau.
- Gammes : Cortizo, Sepalumic, SAPA, Wicona.
- Géo : régional Rhône-Alpes pour standard, NATIONAL pour niches techniques.
- Objectif : top-of-mind awareness + RDV visio (pas téléphone).

FORMAT DE SORTIE (STRICT) :
Tu réponds uniquement par 2 balises XML, dans cet ordre, sans aucun autre texte :

<sujet>
[1 ligne, max 70 caractères, sans emoji, sans MAJ globales, sans point d'exclamation,
sans mots spam-trigger (gratuit, urgent, garanti, offre, exclusif, 100%, opportunité)]
</sujet>

<corps>
[Bloc 1 — Salutation]
"Bonjour M./Mme. <Nom>," si le dirigeant est connu et identifiable, sinon "Bonjour,".

[Bloc 2 — Concernement (1-2 phrases)]
Question directe liée à leur activité (déduite du code NAF + intitulé), sans flatterie.
Si proche géographiquement (département Rhône-Alpes), tu peux le mentionner sobrement.

[Bloc 3 — EKOALU + niche (2-3 phrases)]
Présentation EKOALU avec MENTION OBLIGATOIRE d'au moins 1 produit niche technique
(coupe-feu EI30/60/120, désenfumage, mur-rideau, pare-balles BC1-4, grandes dimensions,
acoustique Rw>40). Adapte la niche au profil du prospect quand c'est pertinent
(ex : pour un constructeur tertiaire → coupe-feu + désenfumage ;
pour un architecte → mur-rideau + acoustique ; pour un BET → grandes dimensions + Rw).

[Bloc 4 — CTA visio]
Une seule phrase qui propose 15 min en visio. Si BOOKING_URL fourni dans le bloc système,
inclus-le à la fin avec : "Mon agenda si pertinent : {booking_url}".
Pas de téléphone, jamais.

[Bloc 5 — Signature]
Reproduis EXACTEMENT, sans modifier :
{signature_block}
</corps>

RÈGLES ABSOLUES :
- Tonalité : cordiale-pro DIRECTE, jamais ampoulée.
- AUCUNE flatterie ("belle entreprise", "votre expertise", "remarquable").
- AUCUN jargon commercial : INTERDITS = synergies, win-win, ROI, disruption, value-prop,
  stratégie 360, solutions clé en main, acteur incontournable, leader, référence,
  excellence, passion, à l'écoute, permettez-moi, j'aurais le plaisir,
  n'hésitez surtout pas, au plaisir d'échanger, restant à votre disposition,
  dans l'attente, dans l'optique de, à l'instar de.
- Pas de markdown, pas de guillemets autour du corps.
- Écris en français, vouvoiement obligatoire.
- Corps total : 8-14 lignes max (signature comprise), aéré (1 ligne blanche entre blocs).
- Bloc 5 : EXACTEMENT le bloc signature, copié verbatim, jamais modifié.

BOOKING_URL : {booking_url_or_none}
"""


# === VARIANTE V2 : posture preuves chiffrées (focus livraisons concrètes) ====
# Différence avec V1 : insiste sur les chiffres (atelier, livraisons réalisées,
# délais respectés) plutôt que sur l'expertise technique abstraite.
# Hypothèse Richard : dirigeants industriels accrochent plus aux faits concrets
# qu'aux normes techniques. À mesurer sur 4-6 semaines.
BASE_SYSTEM_PROMPT_V2 = BASE_SYSTEM_PROMPT_V1.replace(
    "[Bloc 3 — EKOALU + niche (2-3 phrases)]\n"
    "Présentation EKOALU avec MENTION OBLIGATOIRE d'au moins 1 produit niche technique\n"
    "(coupe-feu EI30/60/120, désenfumage, mur-rideau, pare-balles BC1-4, grandes dimensions,\n"
    "acoustique Rw>40). Adapte la niche au profil du prospect quand c'est pertinent\n"
    "(ex : pour un constructeur tertiaire → coupe-feu + désenfumage ;\n"
    "pour un architecte → mur-rideau + acoustique ; pour un BET → grandes dimensions + Rw).",

    "[Bloc 3 — EKOALU + preuves chiffrées (2-3 phrases)]\n"
    "Présentation EKOALU avec MENTION OBLIGATOIRE d'au moins 1 produit niche technique\n"
    "(coupe-feu EI30/60/120, désenfumage, pare-balles BC1-4, mur-rideau, grandes dimensions,\n"
    "acoustique Rw>40) ET au moins UNE preuve chiffrée concrète :\n"
    "  - 'atelier intégré 20 personnes à Chasselay'\n"
    "  - 'nous livrons une vingtaine de chantiers tertiaires/an en Rhône-Alpes'\n"
    "  - 'PV essais coupe-feu/désenfumage/pare-balles disponibles sur demande'\n"
    "  - 'délais tenus à 95 % sur les 50 derniers chantiers' (si pertinent au prospect)\n"
    "Adapte la niche au profil (constructeur tertiaire → coupe-feu/désenfumage ;\n"
    "architecte → mur-rideau/acoustique ; BET → grandes dimensions/Rw).",
)


# === Registre des variantes A/B ==============================================
# Format : { variant_id: (prompt_template, weight) }
# Le poids contrôle la fréquence de tirage. Ex : (1, 1) = 50/50.
PROMPT_VARIANTS: dict[str, tuple[str, float]] = {
    "v1": (BASE_SYSTEM_PROMPT_V1, 1.0),  # wedge technique pur
    "v2": (BASE_SYSTEM_PROMPT_V2, 1.0),  # preuves chiffrées
}

DEFAULT_VARIANT = "v1"


def pick_variant(variants: dict[str, tuple[str, float]] | None = None) -> str:
    """Tire une variante au hasard, pondérée par les poids du registry.

    Permet équilibrage A/B sur les 4-6 semaines de test. Renvoie l'id de
    variante (ex "v1", "v2") pour stockage sur PendingOutbound.prompt_variant.
    """
    reg = variants if variants is not None else PROMPT_VARIANTS
    if not reg:
        return DEFAULT_VARIANT
    ids = list(reg.keys())
    weights = [reg[i][1] for i in ids]
    return random.choices(ids, weights=weights, k=1)[0]


def render_system_prompt(variant: str = DEFAULT_VARIANT) -> str:
    """Injecte signature + booking URL dans la variante choisie."""
    template, _weight = PROMPT_VARIANTS.get(variant, PROMPT_VARIANTS[DEFAULT_VARIANT])
    booking = conf.CALENDAR_BOOKING_URL or ""
    return template.format(
        signature_block=conf.render_signature(),
        booking_url=booking or "(aucun)",
        booking_url_or_none=booking or "(aucun lien fourni, ne pas inclure de lien)",
    )


def build_user_message(*, entreprise: str, dirigeant: str, code_naf: str,
                       activite: str, ville: str, dpt: str,
                       effectif_min: int, effectif_max: int) -> str:
    """Compose le bloc utilisateur avec les données enrichies du prospect."""
    region_hint = ""
    if dpt in conf.GEO_STANDARD_DEPARTMENTS:
        region_hint = f" (département {dpt} = Rhône-Alpes, proche atelier)"

    effectif_str = ""
    if effectif_max:
        effectif_str = f"{effectif_min}-{effectif_max} salariés" if effectif_min else f"~{effectif_max} salariés"

    dirigeant_display = dirigeant or "(inconnu — utiliser 'Bonjour,' sans nom)"
    parts = [
        "Génère le cold mail pour ce prospect.",
        "",
        "DONNÉES PROSPECT :",
        f"- Entreprise : {entreprise or '(inconnue)'}",
        f"- Dirigeant : {dirigeant_display}",
        f"- Code NAF : {code_naf or '(inconnu)'}",
        f"- Activité : {activite or '(non précisée)'}",
        f"- Ville : {ville or '(inconnue)'}{region_hint}",
    ]
    if effectif_str:
        parts.append(f"- Effectif : {effectif_str}")

    parts += [
        "",
        "Rappel : réponds UNIQUEMENT avec les balises <sujet>...</sujet> et <corps>...</corps>.",
    ]
    return "\n".join(parts)
