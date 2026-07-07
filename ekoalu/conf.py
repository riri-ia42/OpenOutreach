"""Configuration EKOALU — overrides des constantes OpenOutreach.

Cf. CLAUDE.md du projet parent pour les justifications.
"""
from __future__ import annotations

import os

# ----------------------------------------------------------------------
# Plages horaires actives (TSE peut tourner 24/7 mais scheduler décide)
# Format : (heure_début, heure_fin) en heures décimales locales
# ----------------------------------------------------------------------
ACTIVE_WINDOWS: tuple[tuple[float, float], ...] = (
    (7.5, 12.0),   # matin : 7h30-12h00 (BTP démarre tôt)
    (14.0, 20.0),  # après-midi : 14h00-20h00 (BTP débauche tard)
)

# Pondération par jour de semaine (0=lundi, 6=dimanche)
# 0.0 = pas d'action ce jour
WEEKDAY_WEIGHTS: dict[int, float] = {
    0: 1.0,  # Lundi
    1: 1.0,  # Mardi
    2: 0.9,  # Mercredi
    3: 1.0,  # Jeudi
    4: 0.7,  # Vendredi (volume réduit)
    5: 0.2,  # Samedi (très réduit)
    6: 0.0,  # Dimanche (off)
}

# Délais entre actions (en secondes)
MIN_DELAY_SECONDS = 90       # 1.5 min minimum (jamais < 60s = pattern bot)
MAX_DELAY_SECONDS = 1800     # 30 min maximum

# Volumes (cibles + hard caps appliques dans process_approved_queue)
WEEKLY_INVITE_TARGET = int(os.environ.get("EKOALU_WEEKLY_INVITE_TARGET", "30"))
WEEKLY_INVITE_HARD_CAP = int(os.environ.get("EKOALU_WEEKLY_INVITE_HARD_CAP", "80"))
DAILY_INVITE_CAP = int(os.environ.get("EKOALU_DAILY_INVITE_CAP", "8"))
# Messages LinkedIn (follow-up + reply) par 24h glissantes — benchmark 2026
# compte gratuit : ~100/sem, zone sure 15-20/j. Enforce par process_approved_queue.
DAILY_MESSAGE_CAP = int(os.environ.get("EKOALU_DAILY_MESSAGE_CAP", "15"))

# Cooldown post-acceptation (heures avant follow-up)
COOLDOWN_MIN_HOURS = 4
COOLDOWN_MAX_HOURS = 48

# Jours off aléatoires (en plus des dimanches naturels via WEEKDAY_WEIGHTS)
RANDOM_DAYS_OFF_PER_MONTH = 2

# Vitesse de frappe simulée (caractères par minute)
HUMAN_TYPING_CHARS_PER_MIN_MIN = 200
HUMAN_TYPING_CHARS_PER_MIN_MAX = 400

# ----------------------------------------------------------------------
# Stratégie commerciale EKOALU
# ----------------------------------------------------------------------

# URL Outlook Bookings (cf. mémoire liens_externes_ekoalu)
CALENDAR_BOOKING_URL = os.environ.get(
    "CALENDAR_BOOKING_URL",
    "https://outlook.office365.com/book/EKOALUPrisedeRDV@ekoalu.com/",
)

# Géographie segmentée par produit
GEO_STANDARD_DEPARTMENTS = ["69", "01", "38", "42", "73", "74", "26", "07"]
GEO_NICHE_SCOPE = "national"

# Produits niches (wedge strategy)
NICHE_PRODUCTS = [
    "coupe-feu", "EI30", "EI60", "EI120",
    "désenfumage", "DENFC",
    "pare-balles", "BC1", "BC2", "BC3", "BC4",
    "grandes dimensions", "grandes dim",
    "acoustique", "Rw", "POA",
    "mur-rideau", "mur rideau",
]

# ----------------------------------------------------------------------
# Signature DM follow-up (bloc final des messages post-acceptation)
# ----------------------------------------------------------------------
SIGNATURE_NAME = os.environ.get("EKOALU_SIGNATURE_NAME", "Richard Gros")
SIGNATURE_TITLE = os.environ.get("EKOALU_SIGNATURE_TITLE", "Président EKOALU")
SIGNATURE_MOBILE = os.environ.get("EKOALU_SIGNATURE_MOBILE", "06 14 26 31 24")
SIGNATURE_EMAIL = os.environ.get("EKOALU_SIGNATURE_EMAIL", "richard@ekoalu.com")

# Adresses "maison" EKOALU : un mail entrant dont l'expéditeur est l'une de ces
# adresses n'est JAMAIS un prospect (c'est Richard lui-même, une newsletter de
# test, un récap interne Plaud…). Le poller inbox les ignore (bug 18/06 : un Lead
# avec contact_email=richard@ekoalu.com faisait générer des brouillons de réponse
# sur les propres mails de Richard). Surchageable via EKOALU_OWN_EMAILS (CSV).
GRAPH_USER_EMAIL = os.environ.get("GRAPH_USER_EMAIL", "")


def own_email_addresses() -> set[str]:
    """Ensemble des adresses EKOALU 'maison' (lowercase) à exclure de l'inbound."""
    extra = os.environ.get("EKOALU_OWN_EMAILS", "")
    raw = {SIGNATURE_EMAIL, GRAPH_USER_EMAIL, *extra.split(",")}
    return {a.strip().lower() for a in raw if a and a.strip()}

# ----------------------------------------------------------------------
# Charte de signature EMAIL (source of truth :
# C:\Users\RI.GROS\Documents\CLAUDE\jumeau-numerique\03-synthese-style\SIGNATURES.md)
# Règle d'apposition : Claude génère la CLÔTURE TEXTUELLE seule dans le
# corps ; le code (email_canal/sender) appose le bloc coordonnées HTML.
# JAMAIS "Cordialement" — Richard signe toujours "Bien à vous".
# ----------------------------------------------------------------------
EMAIL_CLOSING_FORMAL_FIRST = "Bien à vous,\nRichard Gros"  # 1er contact (cold mail)
EMAIL_CLOSING_FORMAL = "Bien à vous,\nRichard"             # échange en cours (replies)
EMAIL_SIG_MOBILE = "06 14 26 31 24"
EMAIL_SIG_FIXE = "04 37 50 36 36"
EMAIL_SIG_ADDRESS = "53 ZAC du Crouloup, Route de Quincieux, 69380 Chasselay"
EMAIL_SIG_TAGLINE = (
    "EKOALU spécialiste menuiseries standards, alu, acier, murs-rideaux, "
    "désenfumage & feu"
)
EMAIL_SIG_GUIDE_URL = "https://ekoalu.com/"


def render_signature() -> str:
    """Bloc signature 4 lignes pour les DM follow-up EKOALU."""
    return (
        f"{SIGNATURE_NAME}\n"
        f"{SIGNATURE_TITLE}\n"
        f"{SIGNATURE_MOBILE}\n"
        f"{SIGNATURE_EMAIL}"
    )

# Ordre des personas EKOALU (priorité)
PERSONAS_PRIORITY: list[str] = [
    "dg_eg_tertiaire",
    "dg_charpente_metal",
    "dg_metallerie",
    "dg_maconnerie_tertiaire",
    "archi_tertiaire",
    "moe_tertiaire",
    "bet_prescripteur",
    "promoteur_tertiaire",
]
