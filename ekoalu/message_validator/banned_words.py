"""Mots et tournures bannis dans les messages EKOALU.

Cf. MARKETING.md §4.1 et mémoire valeurs_ekoalu.
"""
from __future__ import annotations

import os
import re

# Jargon commercial creux
_JARGON_COMMERCIAL = [
    "synergies",
    "synergie",
    "win-win",
    "ROI",
    "disruption",
    "value-prop",
    "value proposition",
    "stratégie 360",
    "solutions clé en main",
    "solutions clés en main",
    "value-added",
    "value added",
]

# Tournures ampoulées
_TOURNURES_AMPOULEES = [
    "permettez-moi",
    "permettez moi",
    "j'aurais le plaisir",
    "j aurais le plaisir",
    "n'hésitez surtout pas",
    "n hésitez surtout pas",
    "dans l'optique de",
    "dans l optique de",
    "à l'instar de",
    "à l instar de",
    "il me serait agréable",
]

# Auto-éloges
_AUTO_ELOGES = [
    "acteur incontournable",
    "leader sur",
    "référence sur",
    "excellence",
    "à l'écoute",
    "à l ecoute",
    "passion",
]

# Closings creux
_CLOSINGS_CREUX = [
    "au plaisir d'échanger",
    "au plaisir d echanger",
    "restant à votre disposition",
    "restant a votre disposition",
    "dans l'attente de votre retour",
    "dans l attente de votre retour",
]

# Affirmations fausses sur EKOALU (consigne Richard 27/08 : beaucoup de
# tertiaire mais PAS exclusivement — la nuance compte ; bible v2.2 : EKOALU
# ne fait PAS la pose, formulations ciblées pour éviter les faux positifs)
_AFFIRMATIONS_FAUSSES = [
    "exclusivement tertiaire",
    "uniquement tertiaire",
    "orienté exclusivement",
    "oriente exclusivement",
    "nous posons vos",
    "fabrication et pose",
    "fabrication + pose",
    "on fabrique et on pose",
    "nous assurons la pose",
]

BANNED_WORDS: list[str] = (
    _JARGON_COMMERCIAL
    + _TOURNURES_AMPOULEES
    + _AUTO_ELOGES
    + _CLOSINGS_CREUX
    + _AFFIRMATIONS_FAUSSES
)

# ── Signes typographiques IA (capture Richard 08/09 : « interdire les grands
# traits et autres signes de l'IA », tous canaux). Richard n'écrit JAMAIS ces
# signes lui-même : chacun trahit la génération. Le trait d'union simple "-",
# les guillemets « » et les "..." restent autorisés (style Richard réel).
_SIGNES_IA_SIMPLES: list[tuple[str, str]] = [
    ("—", 'tiret cadratin "—" (remplacer par virgule, parenthèses ou point)'),
    ("–", 'tiret demi-cadratin "–" (remplacer par virgule ou trait d\'union simple)'),
    ("•", 'puce "•"'),
    ("**", "gras markdown **"),
    ("__", "soulignement markdown __"),
    ("](", "lien markdown [texte](url)"),
    ("`", "backtick markdown"),
]

# Puces / titres markdown en début de ligne ("- xxx", "* xxx", "# Titre").
_SIGNES_IA_DEBUT_LIGNE = re.compile(r"^\s*(?:[-*]\s+\S|#{1,4}\s)", re.MULTILINE)

# Émojis, pictogrammes, flèches, coches (✅ ⚠ ✓ → 🚀 …) — jamais dans un
# message généré, quel que soit le canal (la tolérance 😊 du registre GIE ne
# concerne que les mails que Richard écrit lui-même).
_SIGNES_IA_UNICODE = re.compile(
    "[←-⇿"    # flèches
    "⌀-⏿"     # pictos techniques (⏰ ⌛ …)
    "─-╿"     # traits de cadre ─ │ (grands traits horizontaux)
    "☀-➿"     # symboles divers + dingbats (✅ ✓ ✗ ⚠ ☀ ✂ …)
    "⬀-⯿"     # flèches et symboles additionnels (⬆ ⭐ …)
    "\U0001f000-\U0001faff]"  # émojis
)


def find_ai_signs(text: str) -> list[str]:
    """Signes typographiques IA détectés dans `text` (libellés lisibles)."""
    if not text:
        return []
    found: list[str] = []
    for needle, label in _SIGNES_IA_SIMPLES:
        if needle in text:
            found.append(label)
    if _SIGNES_IA_DEBUT_LIGNE.search(text):
        found.append("liste à puces / titre markdown en début de ligne")
    m = _SIGNES_IA_UNICODE.search(text)
    if m:
        found.append(f'émoji ou pictogramme "{m.group(0)}"')
    return found

# Extra mots bannis via env (séparés par virgule)
_extra = os.environ.get("EKOALU_EXTRA_BANNED_WORDS", "").strip()
if _extra:
    BANNED_WORDS = BANNED_WORDS + [w.strip() for w in _extra.split(",") if w.strip()]


def find_banned_words(text: str) -> list[str]:
    """Retourne la liste des mots bannis détectés dans `text` (case-insensitive).

    Match en sous-chaîne pour les multi-mots. Pour les mots simples, match en
    mot entier pour éviter faux positifs (ex: "ROI" ne matche pas "héroïque").
    """
    if not text:
        return []
    found: list[str] = []
    text_lower = text.lower()
    for banned in BANNED_WORDS:
        banned_lower = banned.lower()
        # Si le mot banni contient un espace ou tiret, match en sous-chaîne
        if " " in banned_lower or "-" in banned_lower or "'" in banned_lower:
            if banned_lower in text_lower:
                found.append(banned)
        else:
            # Mot simple : match en mot entier
            pattern = r"\b" + re.escape(banned_lower) + r"\b"
            if re.search(pattern, text_lower):
                found.append(banned)
    return found


def contains_banned_word(text: str) -> bool:
    """True si le texte contient au moins 1 mot banni."""
    return bool(find_banned_words(text))
