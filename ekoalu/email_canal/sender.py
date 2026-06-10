"""Envoi des cold mails approuvés via Microsoft Graph (richard@ekoalu.com).

Wrapper léger autour de `ekoalu.notifications.graph_mailer.send_mail` :
- résout le destinataire depuis le PendingOutbound (via Lead.contact_email)
- convertit le body texte en HTML simple (paragraphes + <br>)
- inclut un footer désinscription minimal (RGPD art. 21)
- retourne (success: bool, error_msg: str) — pas d'exception remontée
"""
from __future__ import annotations

import html
import logging
from functools import lru_cache
from pathlib import Path

from ekoalu.email_canal.models import EmailLeadData  # noqa: F401 (futur usage)
from ekoalu.notifications.graph_mailer import (
    GraphAuthError,
    GraphConfigError,
    GraphSendError,
    send_mail,
)
from ekoalu.outbound_validation.models import OutboundKind, PendingOutbound

logger = logging.getLogger(__name__)

EMAIL_KINDS = (OutboundKind.EMAIL_COLD, OutboundKind.EMAIL_FOLLOW_UP)

_UNSUB_FOOTER_HTML = (
    "<hr style='border:none;border-top:1px solid #ddd;margin:24px 0 12px;'>"
    "<p style='color:#888;font-size:11px;font-family:Arial,sans-serif;'>"
    "Vous recevez ce message car votre activité tertiaire correspond à notre champ. "
    "Pour ne plus recevoir nos messages, répondez « stop » à cet email — "
    "exclusion immédiate de notre base."
    "</p>"
)


# Logo EKOALU embarqué inline (cid:) — extrait de la signature Outlook native
# (image002.png, 119x139). Fichier : ekoalu/assets/logo_ekoalu.png.
LOGO_CID = "logoekoalu"
_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "logo_ekoalu.png"


@lru_cache(maxsize=1)
def get_logo_bytes() -> bytes | None:
    """Bytes PNG du logo EKOALU (None si le fichier manque — mail sans logo)."""
    try:
        return _LOGO_PATH.read_bytes()
    except OSError:
        logger.warning("Logo EKOALU introuvable (%s) — signature sans logo", _LOGO_PATH)
        return None


def signature_block_html(*, formal_first: bool = True, with_logo: bool = True) -> str:
    """Bloc coordonnées HTML de la charte signature Richard (SIGNATURES.md).

    Reproduit le gabarit de la signature Outlook native EKOALU : logo à
    gauche (cid inline), texte serré à droite (9pt, interligne 1.35).
    Apposé par le CODE après la clôture textuelle générée par Claude.
    `formal_first=True` ajoute la mention « Dirigeant » (1er contact).
    """
    from ekoalu import conf

    title = ", Dirigeant" if formal_first else ""
    # Interlignes en POINTS + mso-line-height-rule : le moteur Word d'Outlook
    # ignore les line-height relatifs et rognait les jambages (texte tronqué).
    line_main = (
        "margin:0;padding:0;font-family:'Segoe UI',Calibri,Arial,sans-serif;"
        "font-size:11pt;mso-line-height-rule:exactly;line-height:16pt;color:#222;"
    )
    line_sub = (
        "margin:0;padding:0;font-family:'Segoe UI',Calibri,Arial,sans-serif;"
        "font-size:9pt;mso-line-height-rule:exactly;line-height:14pt;color:#555;"
    )
    logo_td = ""
    if with_logo and get_logo_bytes() is not None:
        # PNG 149x182 = contenu a ras en haut + 8px de marge EN BAS (Outlook
        # rognait le wordmark "eKoalu"). 60x73 = meme ratio.
        # font-size:0/line-height:0 sur la cellule : sans ca le moteur Word
        # d'Outlook traite l'img comme du texte inline et la decale d'une
        # demi-ligne vers le bas (le "logo desaligne" des captures Richard).
        logo_td = (
            "<td valign=\"top\" style=\"padding:0 12px 0 0;vertical-align:top;"
            "font-size:0;line-height:0;mso-line-height-rule:exactly;\">"
            f"<img src=\"cid:{LOGO_CID}\" width=\"60\" height=\"73\" alt=\"EKOALU\""
            " style=\"display:block;border:0;width:60px;height:73px;\"></td>"
        )
    return (
        "<table cellpadding=\"0\" cellspacing=\"0\""
        " style=\"border-collapse:collapse;margin-top:14px;\"><tr>"
        f"{logo_td}"
        "<td valign=\"top\" style=\"padding:0 0 6px 0;vertical-align:top;\">"
        f"<p style=\"{line_main}\"><strong>Richard Gros</strong>{title}"
        f" – {conf.EMAIL_SIG_MOBILE} – {conf.SIGNATURE_EMAIL}</p>"
        f"<p style=\"{line_sub}\">Fixe {conf.EMAIL_SIG_FIXE} – "
        f"{conf.EMAIL_SIG_ADDRESS}</p>"
        f"<p style=\"{line_sub}\">{html.escape(conf.EMAIL_SIG_TAGLINE)}</p>"
        f"<p style=\"{line_sub}\">"
        f"<a href=\"{conf.EMAIL_SIG_GUIDE_URL}\">Notre guide des solutions</a> – "
        f"<a href=\"{conf.CALENDAR_BOOKING_URL}\">Prendre RDV</a></p>"
        "</td></tr></table>"
    )


def text_body_to_html(body: str) -> str:
    """Convertit un body texte en HTML simple, sans CSS exotique.

    - escape HTML
    - groupe en paragraphes (séparés par ligne vide)
    - retours à la ligne simples → <br>
    """
    escaped = html.escape(body.strip())
    paragraphs = [p.strip() for p in escaped.split("\n\n") if p.strip()]
    pieces = []
    for para in paragraphs:
        para_html = para.replace("\n", "<br>")
        pieces.append(
            f"<p style='font-family:Arial,sans-serif;font-size:14px;"
            f"line-height:1.5;color:#222;margin:0 0 12px;'>{para_html}</p>"
        )
    return "\n".join(pieces)


def build_html_email(body: str) -> str:
    """Body texte → HTML complet : paragraphes + bloc signature charte
    (formal-first, mention Dirigeant) + footer désinscription."""
    return (
        text_body_to_html(body)
        + "\n" + signature_block_html(formal_first=True)
        + "\n" + _UNSUB_FOOTER_HTML
    )


def _resolve_recipient(po: PendingOutbound) -> str | None:
    """Récupère l'adresse email du destinataire depuis le Lead correspondant.

    Renvoie None si le Lead n'a pas de contact_email, s'il est unsubscribed,
    ou si l'adresse a hard-bouncé (NDR détecté par le poller).
    """
    from crm.models import Lead

    lead = Lead.objects.filter(public_identifier=po.prospect_public_id).first()
    if not lead:
        return None
    if not lead.contact_email:
        return None
    if lead.unsubscribed_at is not None:
        return None
    if lead.email_bounced_at is not None:
        return None
    return lead.contact_email


def send_cold_email(po: PendingOutbound) -> tuple[bool, str]:
    """Envoie un seul PendingOutbound de kind email_*. Retourne (success, error_msg).

    N'effectue PAS la mise à jour du statut — c'est la responsabilité de l'appelant
    (le management command), pour garder cette fonction réutilisable et testable.
    """
    if po.kind not in EMAIL_KINDS:
        return False, f"kind non email: {po.kind}"
    if not po.subject:
        return False, "subject vide"

    recipient = _resolve_recipient(po)
    if not recipient:
        return False, "destinataire introuvable (lead absent, sans email, ou unsubscribed)"

    body_text = po.content_to_send
    if not body_text.strip():
        return False, "body vide"

    html_body = build_html_email(body_text)
    logo = get_logo_bytes()
    inline_images = {LOGO_CID: logo} if logo else None

    try:
        send_mail(subject=po.subject, html_body=html_body, to=recipient,
                  inline_images=inline_images)
    except GraphConfigError as exc:
        logger.error("Graph mal configuré : %s", exc)
        return False, f"graph_config: {exc}"
    except GraphAuthError as exc:
        logger.error("Graph auth failed : %s", exc)
        return False, f"graph_auth: {exc}"
    except GraphSendError as exc:
        logger.error("Graph sendMail KO pour %s : %s", recipient, exc)
        return False, f"graph_send: {exc}"
    except Exception as exc:  # noqa: BLE001 — on log + propage en error_msg
        logger.exception("Échec inattendu envoi cold mail à %s", recipient)
        return False, f"unexpected: {exc}"

    logger.info("Cold mail envoyé à %s (PO #%s, sujet=%r)",
                recipient, po.pk, po.subject[:80])
    return True, ""
