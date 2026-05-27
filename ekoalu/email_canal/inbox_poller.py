"""Polling de l'inbox Outlook → matching Lead → création de PendingReply.

Flow :
1. `list_inbox_messages(since)` via Graph API.
2. Pour chaque message : skip si `inbound_message_id` déjà connu (idempotence).
3. Match `from_email` → `Lead.contact_email` (lower). Skip si pas de match
   (ce sont des mails normaux non liés à la prospection).
4. Classifier l'intent du body via `classify_intent`.
5. Générer un brouillon de réponse via Claude (`generate_email_reply`).
6. Créer un `PendingReply(channel="email", ...)` en statut PENDING.

Cette fonction NE marque PAS les mails Outlook comme lus — Richard garde sa
gestion d'inbox normale. L'idempotence est garantie par `inbound_message_id`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ekoalu.email_generator.reply_generator import generate_email_reply
from ekoalu.inbox_assist.intent_classifier import classify_intent
from ekoalu.notifications.graph_mailer import list_inbox_messages

logger = logging.getLogger(__name__)


@dataclass
class PollStats:
    fetched: int = 0
    already_seen: int = 0
    no_lead_match: int = 0
    drafts_created: int = 0
    drafts_failed: int = 0


def _lookup_lead_by_email(email: str):
    """Retourne le Lead correspondant à `email` (matché sur contact_email lower)."""
    from crm.models import Lead
    return Lead.objects.filter(contact_email__iexact=email).first()


def _build_pending_reply(*, msg: dict, lead, intent_value: str,
                         draft_subject: str, draft_body: str):
    """Crée le PendingReply en DB. Suppose que l'idempotence est déjà vérifiée."""
    from ekoalu.inbox_assist.models import PendingReply

    return PendingReply.objects.create(
        prospect_public_id=lead.public_identifier,
        channel=PendingReply.CHANNEL_EMAIL,
        inbound_message_id=msg["id"],
        inbound_subject=msg["subject"][:300],
        sender_email=msg["from_email"][:255],
        inbound_message=msg["body_text"],
        intent=intent_value,
        ai_draft=draft_body,
        final_sent="",
        status=PendingReply.Status.PENDING,
    )


def process_message(msg: dict, *, generate_draft=True) -> str:
    """Traite un seul message inbox. Retourne un code de statut.

    Codes : "already_seen", "no_lead_match", "draft_created", "draft_failed".
    Si `generate_draft=False`, on stocke un PendingReply avec ai_draft vide
    (utile pour tests / mode squelette).
    """
    from ekoalu.inbox_assist.models import PendingReply

    msg_id = msg.get("id", "")
    if not msg_id:
        logger.warning("Message sans id Graph, skip")
        return "no_lead_match"

    # Idempotence : si on a déjà un PendingReply pour cet inbound_message_id, skip
    if PendingReply.objects.filter(inbound_message_id=msg_id).exists():
        return "already_seen"

    sender = msg.get("from_email", "")
    if not sender:
        logger.debug("Message %s sans expéditeur, skip", msg_id)
        return "no_lead_match"

    lead = _lookup_lead_by_email(sender)
    if not lead:
        return "no_lead_match"

    # Récupère le contexte enrichi si disponible (EmailLeadData)
    entreprise = ""
    dirigeant = ""
    try:
        data = lead.email_data
        entreprise = data.entreprise
        dirigeant = data.dirigeant
    except Exception:  # noqa: BLE001 — pas d'EmailLeadData, on continue
        pass

    body_text = msg.get("body_text", "") or ""
    intent = classify_intent(body_text)

    # RGPD : intent OPT_OUT déclenche un désabonnement IMMÉDIAT du Lead.
    # On ne dépend pas de la validation de Richard pour cesser tout envoi futur.
    # Le brouillon de réponse continue à être généré (Richard confirme au prospect),
    # mais aucun cold mail ni reply ne partira plus à ce contact entretemps.
    if intent.value == "opt_out" and lead.unsubscribed_at is None:
        from django.utils import timezone
        lead.unsubscribed_at = timezone.now()
        lead.save(update_fields=["unsubscribed_at"])
        logger.info("Lead %s auto-désinscrit (intent OPT_OUT détecté sur msg %s)",
                    lead.public_identifier, msg_id)

    if generate_draft:
        draft = generate_email_reply(
            intent=intent,
            inbound_subject=msg.get("subject", ""),
            inbound_message=body_text,
            entreprise=entreprise,
            dirigeant=dirigeant,
        )
        if not draft.is_valid():
            # On crée quand même le PendingReply (avec body vide) pour ne pas
            # perdre la trace du message reçu — Richard verra qu'il faut écrire.
            _build_pending_reply(
                msg=msg, lead=lead, intent_value=intent.value,
                draft_subject="", draft_body="",
            )
            return "draft_failed"
        _build_pending_reply(
            msg=msg, lead=lead, intent_value=intent.value,
            draft_subject=draft.subject, draft_body=draft.body,
        )
        return "draft_created"
    else:
        _build_pending_reply(
            msg=msg, lead=lead, intent_value=intent.value,
            draft_subject="", draft_body="",
        )
        return "draft_created"


def poll_inbox(*, since_iso_utc: str, max_n: int = 50,
               generate_drafts: bool = True) -> PollStats:
    """Récupère les mails depuis `since_iso_utc` et crée les PendingReply.

    Retourne des stats agrégées (PollStats).
    """
    stats = PollStats()
    messages = list_inbox_messages(since_iso_utc=since_iso_utc, max_n=max_n)
    stats.fetched = len(messages)

    for msg in messages:
        result = process_message(msg, generate_draft=generate_drafts)
        if result == "already_seen":
            stats.already_seen += 1
        elif result == "no_lead_match":
            stats.no_lead_match += 1
        elif result == "draft_created":
            stats.drafts_created += 1
        elif result == "draft_failed":
            stats.drafts_failed += 1

    return stats
