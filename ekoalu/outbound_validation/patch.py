"""Monkey-patch d'interception des envois LinkedIn.

Quand approval_mode = require_approval :
- send_connection_request → crée PendingOutbound(INVITATION) au lieu d'envoyer
- send_raw_message → crée PendingOutbound(FOLLOW_UP) au lieu d'envoyer

Le daemon retourne un état "neutre" (QUALIFIED inchangé pour invit, False pour message)
de sorte qu'OpenOutreach pense que l'action a échoué/n'a pas progressé : la queue
de validation devient le seul chemin pour envoyer.

Le sender (outbound_validation.sender) utilise les fonctions originales exposées
ci-dessous pour envoyer les messages approuvés sans repasser par l'interception.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_PATCH_APPLIED = False

# Références vers les fonctions originales OpenOutreach, exposées pour
# le sender. Remplies par apply_outbound_validation_patch().
_original_send_connection_request = None
_original_send_raw_message = None


def get_original_send_connection_request():
    """Retourne la fonction originale send_connection_request (sans patch)."""
    return _original_send_connection_request


def get_original_send_raw_message():
    """Retourne la fonction originale send_raw_message (sans patch)."""
    return _original_send_raw_message


def apply_outbound_validation_patch() -> None:
    """Wrap send_connection_request + send_raw_message pour rediriger vers PendingOutbound.

    Idempotent : ne s applique qu une fois.
    """
    global _PATCH_APPLIED, _original_send_connection_request, _original_send_raw_message
    if _PATCH_APPLIED:
        return

    try:
        from linkedin.actions import connect as connect_module
        from linkedin.actions import message as message_module
        from linkedin.enums import ProfileState
    except ImportError as e:
        logger.warning("Cannot patch outbound_validation (linkedin not importable): %s", e)
        return

    original_send_connection = connect_module.send_connection_request
    original_send_raw_message = message_module.send_raw_message

    # Exposer les originales pour le sender
    _original_send_connection_request = original_send_connection
    _original_send_raw_message = original_send_raw_message

    def _enrich_from_lead(public_id):
        """Récupère company + headline + summary depuis le Lead+Deal en DB."""
        company = ""
        headline = ""
        summary = ""
        try:
            from crm.models import Deal, Lead
            lead = Lead.objects.filter(public_identifier=public_id).first()
            if not lead:
                return company, headline, summary
            deal = Deal.objects.filter(lead=lead).select_related("campaign").first()
            if deal and deal.profile_summary:
                facts_text = []
                for fact in deal.profile_summary:
                    if isinstance(fact, dict):
                        f = fact.get("memory") or fact.get("text") or ""
                    else:
                        f = str(fact)
                    if f:
                        facts_text.append(f)
                summary = " | ".join(facts_text[:10])
                # Try extract company from facts
                for f in facts_text:
                    f_lower = f.lower()
                    for kw in ["company:", "works at ", "entreprise :", "société :", "chez "]:
                        if kw in f_lower:
                            idx = f_lower.find(kw) + len(kw)
                            company = f[idx:].strip(".,;\n").split("\n")[0][:120]
                            break
                    if company:
                        break
            if deal and not summary:
                summary = (deal.reason or "")[:500]
        except Exception as e:
            logger.warning("Cannot enrich lead %s: %s", public_id, e)
        return company, headline, summary

    _PENDING_OUTBOUND_OPEN_STATUSES = None  # filled lazily

    def _has_open_outbound(public_id, campaign_id, kind, ignore_campaign=False):
        """Vrai si un PendingOutbound non-terminal existe deja pour ce prospect.

        Avec ignore_campaign=True (defaut metier ABM EKOALU) : dedup sur
        (public_id, kind) toutes campagnes confondues. C'est ce qu'on veut
        car un meme prospect peut etre cible par N campagnes ABM mais on
        ne veut JAMAIS lui envoyer 2 invitations / follow-ups en parallele
        (risque ban LinkedIn + multiplication couts IA par N).

        Avec ignore_campaign=False : dedup intra-campagne uniquement
        (comportement OpenOutreach upstream).
        """
        from ekoalu.outbound_validation.models import OutboundStatus, PendingOutbound
        nonlocal _PENDING_OUTBOUND_OPEN_STATUSES
        if _PENDING_OUTBOUND_OPEN_STATUSES is None:
            _PENDING_OUTBOUND_OPEN_STATUSES = [
                OutboundStatus.PENDING,
                OutboundStatus.APPROVED,
                OutboundStatus.BLOCKED_COMPANY,
            ]
        filter_kwargs = dict(
            prospect_public_id=public_id,
            kind=kind,
            status__in=_PENDING_OUTBOUND_OPEN_STATUSES,
        )
        if not ignore_campaign:
            filter_kwargs["campaign_id"] = campaign_id
        return PendingOutbound.objects.filter(**filter_kwargs).exists()

    def patched_send_connection_request(session, profile):
        from ekoalu.company_validation.config import is_company_validation_enabled
        from ekoalu.company_validation.models import ApprovedCompany
        from ekoalu.outbound_validation.config import is_approval_required
        from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

        if not is_approval_required():
            return original_send_connection(session, profile)

        # Mode require_approval + invitations sans note (acte 13/05 - LinkedIn Free
        # limite a ~5 notes/mois) : on cree juste un PendingOutbound marqueur,
        # le sender enverra l'invitation sans note. Pas de generation Claude.
        public_id = profile.get("public_identifier", "")
        urn = profile.get("urn", "")
        company = profile.get("company", "") or profile.get("company_name", "")

        campaign_id_early = getattr(getattr(session, "campaign", None), "pk", None)
        if _has_open_outbound(public_id, campaign_id_early, OutboundKind.INVITATION,
                               ignore_campaign=True):
            logger.info(
                "EKOALU: PendingOutbound invitation deja en file pour %s "
                "(toutes campagnes confondues) - skip dedup ABM",
                public_id,
            )
            return ProfileState.QUALIFIED

        # Enrichissement company depuis Lead/Deal si pas dans profile dict
        if not company:
            ec, _, _ = _enrich_from_lead(public_id)
            company = company or ec

        campaign = getattr(session, "campaign", None)
        campaign_id = getattr(campaign, "pk", None)
        campaign_name = getattr(campaign, "name", "") if campaign else ""

        # Vérif entreprise (si validation entreprise activée)
        initial_status = OutboundStatus.PENDING
        if is_company_validation_enabled() and company:
            if ApprovedCompany.is_rejected(company):
                logger.info(
                    "EKOALU: invitation pour %s SKIP - entreprise '%s' refusee",
                    public_id, company,
                )
                PendingOutbound.objects.create(
                    prospect_public_id=public_id,
                    prospect_urn=urn,
                    prospect_company=company,
                    campaign_id=campaign_id,
                    campaign_name=campaign_name,
                    kind=OutboundKind.INVITATION,
                    ai_draft="(Invitation LinkedIn sans note)",
                    status=OutboundStatus.REJECTED,
                    rejection_reason=f"Entreprise refusee: {company}",
                )
                return ProfileState.QUALIFIED

            if not ApprovedCompany.is_approved(company):
                ApprovedCompany.find_or_create_pending(company)
                initial_status = OutboundStatus.BLOCKED_COMPANY
                logger.info(
                    "EKOALU: invitation pour %s BLOQUEE - entreprise '%s' a valider",
                    public_id, company,
                )

        PendingOutbound.objects.create(
            prospect_public_id=public_id,
            prospect_urn=urn,
            prospect_company=company,
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            kind=OutboundKind.INVITATION,
            ai_draft="(Invitation LinkedIn sans note)",
            status=initial_status,
        )
        logger.info(
            "EKOALU: invitation pour %s capturee en file de validation (pas envoyee)",
            public_id,
        )
        # Retourne QUALIFIED pour qu'OpenOutreach ne marque pas comme PENDING
        # (le vrai changement d'état arrivera quand Richard valide via UI)
        return ProfileState.QUALIFIED

    def patched_send_raw_message(session, profile, message):
        from ekoalu.company_validation.config import is_company_validation_enabled
        from ekoalu.company_validation.models import ApprovedCompany
        from ekoalu.outbound_validation.config import is_approval_required
        from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound

        if not is_approval_required():
            return original_send_raw_message(session, profile, message)

        public_id = profile.get("public_identifier", "")
        urn = profile.get("urn", "")
        company = profile.get("company", "") or profile.get("company_name", "")
        campaign = getattr(session, "campaign", None)
        campaign_id = getattr(campaign, "pk", None)
        campaign_name = getattr(campaign, "name", "") if campaign else ""

        if _has_open_outbound(public_id, campaign_id, OutboundKind.FOLLOW_UP,
                               ignore_campaign=True):
            logger.info(
                "EKOALU: PendingOutbound follow_up deja en file pour %s "
                "(toutes campagnes confondues) - skip dedup ABM",
                public_id,
            )
            return False

        # Vérif entreprise
        initial_status = OutboundStatus.PENDING
        if is_company_validation_enabled() and company:
            if ApprovedCompany.is_rejected(company):
                logger.info(
                    "EKOALU: message pour %s SKIP - entreprise '%s' refusee",
                    public_id, company,
                )
                return False
            if not ApprovedCompany.is_approved(company):
                ApprovedCompany.find_or_create_pending(company)
                initial_status = OutboundStatus.BLOCKED_COMPANY

        PendingOutbound.objects.create(
            prospect_public_id=public_id,
            prospect_urn=urn,
            prospect_company=company,
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            kind=OutboundKind.FOLLOW_UP,
            ai_draft=message,
            status=initial_status,
        )
        logger.info(
            "EKOALU: message pour %s capture en file de validation (pas envoye)",
            public_id,
        )
        # Retourne False pour qu'OpenOutreach pense que l'envoi a échoué
        # (retry pas idéal mais évite que l'état avance prématurément)
        return False

    connect_module.send_connection_request = patched_send_connection_request
    message_module.send_raw_message = patched_send_raw_message
    _PATCH_APPLIED = True
    logger.info("EKOALU outbound_validation patch applique (mode require_approval)")
