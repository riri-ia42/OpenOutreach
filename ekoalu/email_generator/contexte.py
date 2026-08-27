"""Contexte d'accroche factuel pour la génération de cold mails.

Concatène les morceaux de contexte issus d'un EmailLeadData :
- marché public gagné (leads DECP / groupe d'influence) — accroche factuelle
- angle commercial confrère fabricant (verdict site web, décision Richard 28/07)

Source unique partagée par la commande generate_cold_emails, la régénération
UI (_regenerate_email_draft) et la régénération en masse — avant, la
régénération UI perdait le contexte DECP.
"""
from __future__ import annotations


def build_generation_contexte(data) -> tuple[str, list[str]]:
    """Renvoie (contexte, notes) pour un EmailLeadData (ou None).

    `contexte` est injecté dans le prompt de génération ; `notes` décrit les
    morceaux retenus (pour les logs de la commande).
    """
    if data is None:
        return "", []

    from ekoalu.decp_import import build_marche_contexte
    from ekoalu.email_canal.models import EmailLeadData
    from ekoalu.fabricant_detect.angles import angle_for_siren

    morceaux: list[str] = []
    notes: list[str] = []

    if data.source in (EmailLeadData.SOURCE_DECP, EmailLeadData.SOURCE_DECP_INFLUENCE):
        marche = build_marche_contexte(data.raw_json)
        if marche:
            morceaux.append(marche)
            notes.append("contexte DECP : marché gagné injecté")

    angle = angle_for_siren(data.siren)
    if angle:
        morceaux.append(angle.contexte)
        notes.append(f"angle fabricant : {angle.resume}")

    return "\n\n".join(morceaux), notes
