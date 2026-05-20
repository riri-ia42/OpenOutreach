"""Helpers d'affichage prospect : nom propre, societe, ville.

Extraction heuristique depuis le slug LinkedIn + profile_summary (mem0 facts).
Utilise par les vues pour enrichir les listes (messages, validation,
disqualifies, fiche prospect) au-dela du simple slug brut.
"""
from __future__ import annotations

import re

_SLUG_NOISE_RE = re.compile(r"^[a-z]+[0-9]{2,}$|^[0-9a-f]{8,}$", re.IGNORECASE)
# Top prenoms FR pour split de slugs sans tirets ("florianbrunetlecomte" -> "Florian Brunetlecomte")
_FRENCH_FIRST_NAMES = (
    "jean", "marie", "pierre", "paul", "michel", "philippe", "patrick", "nicolas",
    "daniel", "bernard", "christophe", "stephane", "laurent", "olivier", "thierry",
    "sebastien", "david", "eric", "frederic", "jerome", "florian", "maxime",
    "vincent", "julien", "romain", "antoine", "sylvain", "bruno", "pascal", "alain",
    "jacques", "christian", "henri", "louis", "marc", "guillaume", "hugo", "arnaud",
    "damien", "fabien", "thomas", "benjamin", "yann", "yannick", "matthieu", "francois",
    "francis", "luc", "lucas", "remi", "regis", "richard", "robert", "roland",
    "anne", "sophie", "julie", "nathalie", "isabelle", "catherine", "christine",
    "sandrine", "valerie", "stephanie", "caroline", "emilie", "aurelie", "pauline",
    "camille", "charlotte", "laura", "marion", "celine", "virginie", "helene",
    "florence", "patricia", "sylvie", "cecile", "claire", "elodie", "magali", "fanny",
    "orhan", "chems", "fabiana", "valentin", "erwan", "thibault",
)
_LOCATION_MARKERS = (
    "location:", "lives in", "based in", "ville :", "ville:", "city:",
    "region:", "lieu :", "lieu:",
)
_COMPANY_MARKERS = (
    "company:", "works at ", "entreprise :", "entreprise:",
    "societe :", "societe:", "société :", "société:", "chez ",
)
_TITLE_MARKERS = (
    "title:", "job_title:", "job title:", "headline:", "position:",
    "role:", "poste :", "poste:", "metier :", "metier:", "métier :", "métier:",
    "works as ", "occupation:",
)


def _facts_iter(profile_summary):
    """Itere les facts memo d'un profile_summary, quelque soit la forme."""
    if not profile_summary:
        return
    facts = profile_summary if isinstance(profile_summary, list) else (
        profile_summary.get("facts") if isinstance(profile_summary, dict) else []
    )
    for fact in facts or []:
        if isinstance(fact, dict):
            txt = fact.get("memory") or fact.get("text") or fact.get("fact") or ""
        else:
            txt = str(fact)
        if txt:
            yield txt


def _split_glued_slug(slug_lower: str) -> tuple[str, str]:
    """Essaye de splitter un slug sans tirets via dico prenoms FR.

    'florianbrunetlecomte' -> ('florian', 'brunetlecomte')
    'orhanc' -> ('orhan', 'c') -> filtre car nom trop court
    'inconnuabcd' -> ('', 'inconnuabcd')
    """
    for fn in _FRENCH_FIRST_NAMES:
        if slug_lower.startswith(fn) and len(slug_lower) > len(fn) + 2:
            rest = slug_lower[len(fn):]
            return fn, rest
    return "", slug_lower


def display_name_from_slug(slug: str) -> str:
    """Convertit 'patrick-gomes-gcr-suffix' -> 'Patrick Gomes'.

    Heuristique :
    - Si tirets dans le slug : split sur '-', capitalise, max 2 tokens
    - Si pas de tirets : tente de splitter via dico prenoms FR
    """
    if not slug:
        return ""
    slug_lower = slug.lower()

    if "-" in slug:
        tokens = []
        for tok in slug.split("-"):
            if not tok:
                continue
            if _SLUG_NOISE_RE.match(tok):
                break
            if len(tok) < 2 and tokens:
                continue
            tokens.append(tok)
            if len(tokens) >= 2:
                break
        if tokens:
            return " ".join(t.capitalize() for t in tokens)
        return slug

    # Slug sans tirets : tente split par dico
    fn, rest = _split_glued_slug(slug_lower)
    if fn:
        return f"{fn.capitalize()} {rest.capitalize()}"
    # Fallback : capitalise le slug tel quel
    return slug.capitalize()


_COMPANY_REGEX_PATTERNS = [
    re.compile(r"\b(?:is the|is a|est le|est la|est)\s+[\w'\s-]+?\s+(?:of|at|chez|de|d'|du|della)\s+([A-Z][\w\s&'-.]+?)(?:[.,;\n]|$)", re.IGNORECASE),
    re.compile(r"\b(?:works at|travaille chez|employed at)\s+([A-Z][\w\s&'-.]+?)(?:[.,;\n]|$)", re.IGNORECASE),
    re.compile(r"^([A-Z][\w\s&'-.]+?)\s+(?:is|est)\s+(?:based|located|situé|située|implant)", re.IGNORECASE),
    re.compile(r"\b(?:lead|prospect|profile|profil|le lead)\s+(?:is|est)\s+(?:the|le|la)?\s+\w[\w'\s-]*?\s+(?:of|at|chez|de|du)\s+([A-Z][\w\s&'-.]+?)(?:[.,;\n]|$)", re.IGNORECASE),
]
_LOCATION_REGEX_PATTERNS = [
    re.compile(r"\b(?:based in|located in|situé\s+(?:à|a)|situé[e]?\s+(?:à|a)|implant[ée]?[s]?\s+(?:à|a))\s+([A-Z][\w\s&,'-]+?)(?:[.;\n]|$)", re.IGNORECASE),
    re.compile(r"\b(?:in|à|a)\s+(Paris|Lyon|Marseille|Toulouse|Nice|Nantes|Strasbourg|Montpellier|Bordeaux|Lille|Rennes|Reims|Le Havre|Saint-Étienne|Toulon|Grenoble|Dijon|Angers|Nîmes|Villeurbanne|Aix-en-Provence|Chasselay)(?:[.,;\n\s]|$)", re.IGNORECASE),
    re.compile(r"\b((?:Île-de-France|Rhône-Alpes|Auvergne[- ]Rhône-Alpes|Provence[- ]Alpes[- ]Côte[- ]d'Azur|Nouvelle[- ]Aquitaine|Occitanie|Pays de la Loire|Bretagne|Normandie|Hauts[- ]de[- ]France|Grand[- ]Est|Centre[- ]Val[- ]de[- ]Loire|Bourgogne[- ]Franche[- ]Comt[ée])\s*(?:region|région)?)", re.IGNORECASE),
]
_TITLE_REGEX_PATTERNS = [
    re.compile(r"\b(?:lead|prospect|profil|le lead)\s+(?:is|est)\s+(?:the|le|la|un|une)?\s+([\w'\s-]+?)\s+(?:of|at|chez|de|d'|du|della)\b", re.IGNORECASE),
    re.compile(r"\bworks as\s+(?:a|an)?\s*([\w'\s-]+?)(?:[.,;\n]|$)", re.IGNORECASE),
    re.compile(r"\boccupation\s*:?\s*([\w'\s-]+?)(?:[.,;\n]|$)", re.IGNORECASE),
]


def _try_markers(txt: str, markers: tuple, max_len: int) -> str:
    low = txt.lower()
    for marker in markers:
        if marker in low:
            idx = low.find(marker) + len(marker)
            value = txt[idx:].strip(".,;:- \n").split("\n")[0]
            if value and len(value) <= max_len:
                return value.strip()
    return ""


def _try_regex(txt: str, patterns: list, max_len: int) -> str:
    for pat in patterns:
        m = pat.search(txt)
        if m:
            value = m.group(1).strip(".,;:- ")
            if value and len(value) <= max_len:
                return value
    return ""


def extract_company(profile_summary) -> str:
    """Extrait le nom de societe depuis les facts mem0 (markers + langage naturel)."""
    for txt in _facts_iter(profile_summary):
        v = _try_markers(txt, _COMPANY_MARKERS, 140) or _try_regex(txt, _COMPANY_REGEX_PATTERNS, 140)
        if v:
            return v
    return ""


def extract_location(profile_summary) -> str:
    """Extrait la localisation depuis les facts mem0 (markers + langage naturel)."""
    for txt in _facts_iter(profile_summary):
        v = _try_markers(txt, _LOCATION_MARKERS, 120) or _try_regex(txt, _LOCATION_REGEX_PATTERNS, 120)
        if v:
            return v
    return ""


def extract_job_title(profile_summary) -> str:
    """Extrait le metier / poste actuel depuis les facts mem0 (markers + langage naturel)."""
    for txt in _facts_iter(profile_summary):
        v = _try_markers(txt, _TITLE_MARKERS, 160) or _try_regex(txt, _TITLE_REGEX_PATTERNS, 160)
        if v:
            # filtre les faux positifs courants
            if v.lower() in ("based", "located", "is", "the", "a", "an", "lead", "prospect"):
                continue
            return v
    return ""


def resolve_prospect_display(slug: str, deal=None, company_hint: str = "") -> dict:
    """Renvoie un dict {name, company, location, job_title} pour les templates.

    Args:
        slug: public_identifier LinkedIn (toujours dispo)
        deal: Deal objet (pour acceder a profile_summary), optionnel
        company_hint: fallback company si profile_summary vide (ex : PendingOutbound.prospect_company)
    """
    name = display_name_from_slug(slug)
    profile_summary = getattr(deal, "profile_summary", None) if deal else None
    company = extract_company(profile_summary) or company_hint or ""
    location = extract_location(profile_summary)
    job_title = extract_job_title(profile_summary)
    return {"name": name, "company": company, "location": location, "job_title": job_title}


def resolve_for_lead(lead, deal=None) -> dict:
    """Variant qui prend un objet Lead. Utilise par fiche prospect."""
    slug = getattr(lead, "public_identifier", "")
    return resolve_prospect_display(slug, deal=deal)


# ---------------------------------------------------------------------------
# Analyse rapide d'un prospect disqualifie pour affichage compact
# Tableau de criteres + tldr 1-2 lignes
# ---------------------------------------------------------------------------

_TERTIARY_POS_WORDS = ("tertiaire", "bureaux", "erp", "hôtellerie", "hotellerie", "équipement public", "equipement public")
_RESIDENTIAL_NEG_WORDS = ("résidentiel", "residentiel", "habitat", "habitation", "logement collectif", "particulier")
_GEO_OK_WORDS = ("rhône-alpes", "rhone-alpes", "auvergne", "lyon", "chasselay", "isère", "isere", "savoie", "ain ", " 69 ", " 38 ", " 42 ", " 01 ")
_GEO_NA_WORDS = ("paris", "île-de-france", "ile-de-france", "idf ", "national")
_TECH_NICHE_WORDS = ("coupe-feu", "ei30", "ei60", "ei120", "désenfumage", "desenfumage", "denfc", "pare-balles", "bc1", "bc2", "bc3", "bc4", "mur-rideau", "mur rideau", "grandes dim", "acoustique", "rw>")
_TECH_OFF_WORDS = ("rénovation énergétique", "renovation energetique", "isolation", "étanchéité à l'air", "etancheite a l'air", "matériaux", "materiaux", "négoce", "negoce", "distribution")


def _criterion(label: str, status: str, micro: str = "") -> dict:
    """Helper : status in {ok, ko, na} pour les bordures couleur."""
    return {"label": label, "status": status, "micro": micro}


def analyze_disqualification(deal_reason: str, target_persona: str = "") -> dict:
    """Genere un resume structure : tableau de criteres + tldr 1-2 lignes.

    Heuristique pure sur le texte de la raison Claude. Pas d'appel LLM.

    Args:
        deal_reason: deal.reason (texte Claude expliquant le rejet)
        target_persona: persona cible de la campagne (informatif, peut ajuster la lecture)

    Returns:
        {
            "criteria": [{"label": "Marché", "status": "ko", "micro": "résidentiel"}, ...],
            "tldr": "Distributeur matériaux résidentiels, hors cible tertiaire EKOALU.",
        }
    """
    if not deal_reason:
        return {"criteria": [], "tldr": ""}

    low = deal_reason.lower()

    # Critere 1 : Marche
    has_tert_pos = any(w in low for w in _TERTIARY_POS_WORDS)
    has_resid_neg = any(w in low for w in _RESIDENTIAL_NEG_WORDS)
    if has_resid_neg and not has_tert_pos:
        marche = _criterion("Marché", "ko", "résidentiel/habitat")
    elif has_tert_pos and not has_resid_neg:
        marche = _criterion("Marché", "ok", "tertiaire")
    elif has_tert_pos and has_resid_neg:
        marche = _criterion("Marché", "na", "mixte")
    else:
        marche = _criterion("Marché", "na", "—")

    # Critere 2 : Geographie
    has_geo_ok = any(w in low for w in _GEO_OK_WORDS)
    has_geo_na = any(w in low for w in _GEO_NA_WORDS)
    if has_geo_ok:
        geo = _criterion("Géo", "ok", "Rhône-Alpes")
    elif "paris" in low or "île-de-france" in low or "ile-de-france" in low:
        geo = _criterion("Géo", "na", "Île-de-France")
    elif has_geo_na:
        geo = _criterion("Géo", "na", "national")
    else:
        geo = _criterion("Géo", "na", "—")

    # Critere 3 : Tech niche
    # On detecte les contextes de negation pour eviter les faux positifs
    # ("ne prescrit pas de coupe-feu" != "expert coupe-feu")
    _NEGATION_PATTERNS = ("ne prescrit pas", "ne fait pas", "pas de ", "ne correspond pas", "ne traite pas", "ne couvre pas", "hors champ", "absence de")
    def _is_negated(keyword: str) -> bool:
        idx = low.find(keyword)
        if idx < 0:
            return False
        window_start = max(0, idx - 100)
        window = low[window_start:idx]
        return any(neg in window for neg in _NEGATION_PATTERNS)

    tech_found = [w for w in _TECH_NICHE_WORDS if w in low and not _is_negated(w)]
    tech_off = [w for w in _TECH_OFF_WORDS if w in low]
    if tech_found:
        tech = _criterion("Tech niche", "ok", tech_found[0])
    elif tech_off:
        tech = _criterion("Tech niche", "ko", tech_off[0][:24])
    else:
        tech = _criterion("Tech niche", "na", "—")

    # Critere 4 : Type business (concepteur/poseur vs distributeur/concurrent)
    if "distribut" in low or "négoce" in low or "negoce" in low or "fournisseur" in low or "fabricant menuis" in low:
        biz = _criterion("Type biz", "ko", "distrib/concurrent")
    elif "architecte" in low or "moe" in low or "maître d'œuvre" in low or "maitre d'oeuvre" in low or "économiste" in low or "economiste" in low:
        biz = _criterion("Type biz", "ok", "prescripteur")
    elif "entreprise générale" in low or "entreprise generale" in low or "maçon" in low or "macon" in low or "métallier" in low or "metallier" in low or "charpentier" in low:
        biz = _criterion("Type biz", "ok", "EG/lot menuis.")
    elif "promoteur" in low or "fonciere" in low or "foncière" in low:
        biz = _criterion("Type biz", "ok", "promoteur")
    else:
        biz = _criterion("Type biz", "na", "—")

    criteria = [marche, geo, tech, biz]

    # TLDR : premiere phrase claire (max 180 chars) — on coupe au premier "." apres au moins 60 chars
    tldr = ""
    text = deal_reason.strip()
    # On enleve le pattern "Ce profil ne correspond pas a la cible d'EKOALU. " si present
    text = re.sub(r"^[^.]*ne correspond pas[^.]*\.\s*", "", text, flags=re.IGNORECASE)
    # Premiere vraie phrase
    m = re.search(r"^(.{40,200}?[\.\!\?])\s", text)
    if m:
        tldr = m.group(1).strip()
    else:
        tldr = text[:180].rstrip() + ("…" if len(text) > 180 else "")

    return {"criteria": criteria, "tldr": tldr}
