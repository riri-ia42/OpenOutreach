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

# Distribution gaussienne intra-plage (concentration autour de mu)
GAUSSIAN_MORNING_MU = 10.0    # heure pic matin
GAUSSIAN_AFTERNOON_MU = 16.0  # heure pic après-midi
GAUSSIAN_SIGMA = 1.5          # écart-type (en heures)

# Délais entre actions (en secondes)
MIN_DELAY_SECONDS = 90       # 1.5 min minimum (jamais < 60s = pattern bot)
MAX_DELAY_SECONDS = 1800     # 30 min maximum

# Volumes (cibles + hard caps appliques dans process_approved_queue)
WEEKLY_INVITE_TARGET = int(os.environ.get("EKOALU_WEEKLY_INVITE_TARGET", "30"))
WEEKLY_INVITE_HARD_CAP = int(os.environ.get("EKOALU_WEEKLY_INVITE_HARD_CAP", "80"))
DAILY_INVITE_CAP = int(os.environ.get("EKOALU_DAILY_INVITE_CAP", "8"))
DAILY_MESSAGE_CAP = int(os.environ.get("EKOALU_DAILY_MESSAGE_CAP", "80"))

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
]

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
