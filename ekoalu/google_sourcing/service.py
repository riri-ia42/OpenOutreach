"""Service de sourcing Serper par campagne — partagé par les commandes
``source_via_google`` (manuel) et ``source_via_google_rotate`` (rotation auto).

Retourne le détail (nouveaux profils vs déjà connus) pour que la rotation
puisse détecter l'épuisement d'une campagne (plus AUCUN nouveau profil).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Une campagne est épuisée après N passages consécutifs sans aucun nouveau profil.
EXHAUSTED_AFTER_EMPTY_RUNS = 2


@dataclass
class SourcingResult:
    campaign_name: str = ""
    queries_used: int = 0
    urls_found: int = 0
    new_leads: int = 0        # leads nouvellement rattachés à la campagne
    already_known: int = 0    # profils déjà découverts pour cette campagne
    prefiltered: int = 0      # résultats écartés AVANT lecture (hors-domaine)
    errors: int = 0
    dry_run_urls: list[str] = field(default_factory=list)


def source_campaign(
    campaign,
    *,
    max_profiles: int = 15,
    per_query: int = 10,
    query_budget: int = 9,
    dry_run: bool = False,
) -> SourcingResult:
    """Lance les requêtes Serper d'une campagne ABM et crée/rattache les leads.

    ``query_budget`` borne le nombre de requêtes consommées ICI (1 crédit
    chacune) ; on s'arrête aussi dès que ``max_profiles`` URLs sont trouvées.
    """
    from crm.models import Lead
    from linkedin.url_utils import public_id_to_url, url_to_public_id
    from ekoalu.google_sourcing import client, prefilter, queries
    from ekoalu.lead_routing.models import LeadDiscovery

    result = SourcingResult(campaign_name=campaign.name)

    qlist = queries.build_queries(campaign)
    if not qlist:
        logger.warning("Campagne %r : aucune requete Serper (entreprise ABM ou "
                       "secteur introuvable).", campaign.name)
        return result

    found: list[str] = []
    seen: set[str] = set()
    query_by_url: dict[str, str] = {}  # tracabilite : quelle requete a trouve quoi
    for q in qlist:
        if len(found) >= max_profiles or result.queries_used >= query_budget:
            break
        result.queries_used += 1
        try:
            results = client.search_linkedin_results(q, max_results=per_query)
        except Exception as e:  # réseau/quota : on n'arrête pas tout
            logger.warning("Requête Serper échouée (%r) : %s", q, e)
            result.errors += 1
            continue
        for r in results:
            u = r["link"]
            pid = url_to_public_id(u)
            if not pid or pid in seen:
                continue
            # Pre-filtre AVANT lecture : on n'enregistre pas un profil dont le
            # titre/snippet montre un metier hors-domaine (economise 1 lecture).
            if not prefilter.passes_prefilter(r):
                result.prefiltered += 1
                logger.debug("Pre-filtre Serper : %s ecarte (%r)", pid, r.get("title", ""))
                continue
            seen.add(pid)
            found.append(u)
            query_by_url[u] = q

    found = found[:max_profiles]
    result.urls_found = len(found)

    for u in found:
        pid = url_to_public_id(u)
        if not pid:
            continue
        if dry_run:
            result.dry_run_urls.append(public_id_to_url(pid))
            continue
        lead, _ = Lead.objects.get_or_create(
            public_identifier=pid,
            defaults={"linkedin_url": public_id_to_url(pid)},
        )
        _, created = LeadDiscovery.objects.get_or_create(
            lead_id=lead.pk, campaign=campaign,
            defaults={"query": query_by_url.get(u, "")},
        )
        if created:
            result.new_leads += 1
        else:
            result.already_known += 1

    return result


def update_rotation_state(campaign, result: SourcingResult) -> "GoogleSourcingState":
    """Met à jour l'état de rotation après un passage (épuisement compris)."""
    from django.utils import timezone

    from ekoalu.google_sourcing.models import GoogleSourcingState

    state, _ = GoogleSourcingState.objects.get_or_create(campaign=campaign)
    state.last_run_at = timezone.now()
    state.total_queries += result.queries_used
    state.total_new_leads += result.new_leads
    if result.new_leads == 0:
        state.consecutive_empty_runs += 1
        if state.consecutive_empty_runs >= EXHAUSTED_AFTER_EMPTY_RUNS:
            state.exhausted = True
            logger.info(
                "Campagne %r marquée ÉPUISÉE (%d passages sans nouveau profil)",
                campaign.name, state.consecutive_empty_runs,
            )
    else:
        state.consecutive_empty_runs = 0
        state.exhausted = False
    state.save()
    return state
