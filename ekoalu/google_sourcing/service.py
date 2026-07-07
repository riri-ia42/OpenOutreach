"""Service de sourcing Serper par campagne — partagé par les commandes
``source_via_google`` (manuel) et ``source_via_google_rotate`` (rotation auto).

Retourne le détail (nouveaux profils vs déjà connus) pour que la rotation
puisse détecter l'épuisement d'une campagne (plus AUCUN nouveau profil).

Dédup contre la BASE (07/07) : Google ressert les mêmes top-10 chaque jour,
donc seuls les profils inconnus de la base (aucun Lead existant) comptent
dans le quota ``max_profiles``. Les profils déjà connus ne coûtent rien :
ils restent rattachés à la campagne (LeadDiscovery), et on continue de
dérouler les requêtes-rôles suivantes tant que le quota de NOUVEAUX n'est
pas atteint et qu'il reste du budget requêtes.

Pagination (07/07) : quand une page pleine est majoritairement déjà connue,
on va chercher la page suivante de Google (1 crédit/page), dans la limite
de ``EKOALU_SERPER_MAX_PAGES`` pages par requête (défaut 3) et du budget
requêtes du run.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Une campagne est épuisée après N passages consécutifs sans aucun nouveau profil.
EXHAUSTED_AFTER_EMPTY_RUNS = 2
# Une page est « pleine » si elle contient au moins ce ratio de per_query résultats.
FULL_PAGE_RATIO = 0.8


def _max_pages_per_query() -> int:
    try:
        return max(1, int(os.environ.get("EKOALU_SERPER_MAX_PAGES", "3")))
    except ValueError:
        return 3


@dataclass
class SourcingResult:
    campaign_name: str = ""
    queries_used: int = 0     # crédits Serper consommés
    urls_found: int = 0       # profils NOUVEAUX retenus (quota max_profiles)
    new_leads: int = 0        # leads créés (inconnus de la base avant ce run)
    already_known: int = 0    # profils déjà en base (rattachés, hors quota)
    prefiltered: int = 0      # résultats écartés AVANT lecture (hors-domaine)
    errors: int = 0
    # Run COMPLET : toutes les requêtes-rôles déroulées. Un run partiel (budget
    # épuisé avant la fin) ne compte pas pour l'épuisement de la campagne.
    all_queries_run: bool = False
    dry_run_urls: list[str] = field(default_factory=list)


@dataclass
class _Harvest:
    """Accumulateur d'un run : URLs nouvelles (quota) vs déjà connues (rattachement)."""
    new_urls: list[str] = field(default_factory=list)
    known_urls: list[str] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    query_by_url: dict[str, str] = field(default_factory=dict)  # traçabilité


def _lead_known(pid: str) -> bool:
    """Le profil existe-t-il déjà en base (Lead), toutes campagnes confondues ?"""
    from crm.models import Lead

    return Lead.objects.filter(public_identifier=pid).exists()


def source_campaign(
    campaign,
    *,
    max_profiles: int = 15,
    per_query: int = 10,
    query_budget: int = 9,
    dry_run: bool = False,
) -> SourcingResult:
    """Lance les requêtes Serper d'une campagne ABM/SECTEUR et crée/rattache les leads.

    ``query_budget`` borne le nombre de requêtes consommées ICI (1 crédit
    chacune) ; on s'arrête aussi dès que ``max_profiles`` profils NOUVEAUX
    (inconnus de la base) sont trouvés — un profil déjà connu ne consomme
    pas le quota.
    """
    from ekoalu.google_sourcing import queries

    result = SourcingResult(campaign_name=campaign.name)

    qlist = queries.build_queries(campaign)
    if not qlist:
        logger.warning("Campagne %r : aucune requete Serper (entreprise ABM ou "
                       "secteur introuvable).", campaign.name)
        return result

    harvest = _Harvest()
    result.all_queries_run = True
    for q in qlist:
        if len(harvest.new_urls) >= max_profiles or result.queries_used >= query_budget:
            result.all_queries_run = False
            break
        _harvest_query(q, harvest, result, max_profiles=max_profiles,
                       per_query=per_query, query_budget=query_budget)

    result.urls_found = len(harvest.new_urls)
    _persist_harvest(campaign, harvest, result, dry_run=dry_run)
    return result


def _harvest_query(q: str, harvest: _Harvest, result: SourcingResult,
                   *, max_profiles: int, per_query: int, query_budget: int) -> None:
    """Déroule une requête-rôle, page Google par page Google (1 crédit/page)."""
    from ekoalu.google_sourcing import client

    page = 1
    while True:
        result.queries_used += 1
        try:
            results = client.search_linkedin_results(q, num=per_query, page=page)
        except Exception as e:  # réseau/quota : on n'arrête pas tout
            logger.warning("Requête Serper échouée (%r page %d) : %s", q, page, e)
            result.errors += 1
            return
        known_on_page = _classify_results(q, results, harvest, result, max_profiles)
        if not _next_page_wanted(page, results, known_on_page, harvest, result,
                                 max_profiles=max_profiles, per_query=per_query,
                                 query_budget=query_budget):
            return
        page += 1


def _next_page_wanted(page: int, results: list[dict], known_on_page: int,
                      harvest: _Harvest, result: SourcingResult,
                      *, max_profiles: int, per_query: int, query_budget: int) -> bool:
    """Page suivante seulement si la page est pleine ET majoritairement connue.

    Bornée par ``EKOALU_SERPER_MAX_PAGES`` (défaut 3), le quota de nouveaux
    profils et le budget requêtes du run (chaque page = 1 crédit).
    """
    if page >= _max_pages_per_query():
        return False
    if result.queries_used >= query_budget:
        return False
    if len(harvest.new_urls) >= max_profiles:
        return False
    if len(results) < per_query * FULL_PAGE_RATIO:
        return False  # page creuse : Google n'a plus grand-chose derrière
    return known_on_page * 2 > len(results)


def _classify_results(q: str, results: list[dict], harvest: _Harvest,
                      result: SourcingResult, max_profiles: int) -> int:
    """Classe les résultats d'une page : nouveaux (quota) vs déjà connus.

    Renvoie le nombre de profils de la page déjà connus de la base.
    """
    from linkedin.url_utils import url_to_public_id
    from ekoalu.google_sourcing import prefilter

    known_on_page = 0
    for r in results:
        u = r["link"]
        pid = url_to_public_id(u)
        if not pid or pid in harvest.seen:
            continue
        # Pre-filtre AVANT lecture : on n'enregistre pas un profil dont le
        # titre/snippet montre un metier hors-domaine (economise 1 lecture).
        if not prefilter.passes_prefilter(r):
            result.prefiltered += 1
            logger.debug("Pre-filtre Serper : %s ecarte (%r)", pid, r.get("title", ""))
            continue
        harvest.seen.add(pid)
        harvest.query_by_url[u] = q
        if _lead_known(pid):
            known_on_page += 1
            harvest.known_urls.append(u)
        elif len(harvest.new_urls) < max_profiles:
            harvest.new_urls.append(u)
    return known_on_page


def _persist_harvest(campaign, harvest: _Harvest, result: SourcingResult,
                     *, dry_run: bool) -> None:
    """Crée les leads nouveaux et rattache TOUTES les URLs (nouvelles + connues)."""
    from crm.models import Lead
    from linkedin.url_utils import public_id_to_url, url_to_public_id
    from ekoalu.lead_routing.models import LeadDiscovery

    if dry_run:
        result.already_known = len(harvest.known_urls)
        for u in harvest.new_urls:
            pid = url_to_public_id(u)
            if pid:
                result.dry_run_urls.append(public_id_to_url(pid))
        return

    for u in harvest.new_urls + harvest.known_urls:
        pid = url_to_public_id(u)
        if not pid:
            continue
        lead, lead_created = Lead.objects.get_or_create(
            public_identifier=pid,
            defaults={"linkedin_url": public_id_to_url(pid)},
        )
        LeadDiscovery.objects.get_or_create(
            lead_id=lead.pk, campaign=campaign,
            defaults={"query": harvest.query_by_url.get(u, "")},
        )
        if lead_created:
            result.new_leads += 1
        else:
            result.already_known += 1


def update_rotation_state(campaign, result: SourcingResult) -> "GoogleSourcingState":
    """Met à jour l'état de rotation après un passage (épuisement compris).

    Un run ne compte comme « vide » que s'il a réellement déroulé TOUTES ses
    requêtes-rôles (``all_queries_run``) : un run partiel (budget requêtes
    épuisé avant la fin) sans nouveau profil ne pousse pas vers l'épuisement.
    Le ré-armement mensuel (``--reset-exhausted``) est déclenché par
    ``scripts/serper_rotation.ps1`` le premier jour ouvré du mois.
    """
    from django.utils import timezone

    from ekoalu.google_sourcing.models import GoogleSourcingState

    state, _ = GoogleSourcingState.objects.get_or_create(campaign=campaign)
    state.last_run_at = timezone.now()
    state.total_queries += result.queries_used
    state.total_new_leads += result.new_leads
    if result.new_leads > 0:
        state.consecutive_empty_runs = 0
        state.exhausted = False
    elif result.all_queries_run:
        state.consecutive_empty_runs += 1
        if state.consecutive_empty_runs >= EXHAUSTED_AFTER_EMPTY_RUNS:
            state.exhausted = True
            logger.info(
                "Campagne %r marquée ÉPUISÉE (%d passages complets sans nouveau profil)",
                campaign.name, state.consecutive_empty_runs,
            )
    else:
        logger.debug(
            "Campagne %r : run partiel sans nouveau profil — ignoré pour l'épuisement",
            campaign.name,
        )
    state.save()
    return state
