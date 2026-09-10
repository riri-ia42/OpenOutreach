"""Préparation automatique des rendez-vous prospects (10/09/2026, demande Richard).

Pour chaque RDV Bookings à venir : collecte des faits (agenda, base, Outlook,
registre des entreprises, LinkedIn public), rédaction du brief et de la slide
persona par Claude, rendu HTML des deux supports depuis les bases
`supports/brief-rdv` et `supports/deck-rdv`, PDF du deck, publication sur le
dashboard (`/ekoalu/rdv/<id>/…`) et création d'un créneau de préparation
30 min avant le RDV dans l'agenda de Richard avec les liens.
"""
