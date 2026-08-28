"""Remontée LinkedIn → bases (lot 1 du chantier unification, GO Richard 28/08).

Exporte tous les leads porteurs d'un profile_snapshot (nom, poste, société,
localisation, URL LinkedIn) vers `BDD PROSPECT\\seances\\linkedin-enrichis.json`,
au format des autres fichiers de séance (dict keyé). L'import antichambre les
consommera pour remplir nom/prenom/poste + linkedinUrl (et la boucle emails du
lot 2 créera les contacts des personnes sans email).

Résolution SIREN par nom de société via recherche-entreprises.api.gouv.fr
(throttle, résultats réutilisés d'un run à l'autre : le fichier de sortie
sert de cache — seules les sociétés jamais résolues sont interrogées).

Usage :
    python manage.py export_linkedin_enrichment [--limit N] [--dry-run]

Idempotent, relançable ; prévu en tâche hebdo (dimanche, après la séance
Signaux). Aucune lecture LinkedIn : tout vient de la DB locale.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import requests
from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger(__name__)

EXPORT_PATH = Path(r"C:\Users\RI.GROS\Documents\CLAUDE\BDD PROSPECT\seances\linkedin-enrichis.json")
RECHERCHE_API = "https://recherche-entreprises.api.gouv.fr/search"
THROTTLE_SECONDS = 0.25  # aligné sur les séances antichambre (~4 req/s)

# États de deal → statut de prospection lisible par les autres bases
_WARM_STATES = ("Connected", "Completed")


def _txt(v) -> str:
    """Chaîne sûre depuis un champ snapshot (str, dict {name}, None…)."""
    if isinstance(v, dict):
        v = v.get("name") or v.get("full_name") or ""
    return str(v).strip() if v else ""


def snapshot_fields(snap: dict) -> dict | None:
    """Extrait les champs utiles d'un profile_snapshot (None si inexploitable)."""
    if not snap or not _txt(snap.get("full_name")):
        return None
    positions = snap.get("positions") or []
    company = title = ""
    if positions:
        company = _txt(positions[0].get("company_name") or positions[0].get("company"))
        title = _txt(positions[0].get("title"))
    full_name = _txt(snap.get("full_name"))
    first = _txt(snap.get("first_name"))
    last = _txt(snap.get("last_name"))
    if not first and " " in full_name:
        first, last = full_name.split(" ", 1)
    return {
        "nom": last or full_name,
        "prenom": first,
        "poste": (_txt(snap.get("headline")) or title)[:200],
        "societe": company[:200],
        "localisation": _txt(snap.get("location_name"))[:120],
        "industrie": _txt(snap.get("industry"))[:120],
    }


def resolve_siren(company: str, session: requests.Session) -> str | None:
    """SIREN via API gouv. '' = définitivement introuvable/ambigu (cacheable),
    None = erreur transitoire (429/réseau — à retenter au prochain run)."""
    if not company or len(company) < 3:
        return ""
    for attempt in (1, 2):
        try:
            r = session.get(RECHERCHE_API, params={"q": company, "per_page": 1}, timeout=15)
            if r.status_code == 429 and attempt == 1:
                time.sleep(2.5)
                continue
            r.raise_for_status()
            results = r.json().get("results", [])
            break
        except requests.RequestException as exc:
            if attempt == 2:
                logger.warning("API gouv KO pour %r : %s", company, exc)
                return None
            time.sleep(2.5)
    else:  # pragma: no cover
        return None
    if not results:
        return ""
    res = results[0]
    # Garde anti-homonyme : le nom retourné doit recouper le nom cherché
    nom_api = (res.get("nom_complet") or "").lower()
    tokens = [t for t in company.lower().split() if len(t) > 3]
    if tokens and not any(t in nom_api for t in tokens):
        return ""
    return res.get("siren") or ""


class Command(BaseCommand):
    help = "Exporte les profils LinkedIn (snapshots) vers BDD PROSPECT/seances/linkedin-enrichis.json"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0,
                            help="Limite de leads traités (0 = tous)")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--max-api-calls", type=int, default=400,
                            help="Plafond de résolutions SIREN par run (les autres attendront le run suivant)")

    def handle(self, *args, **opts):
        from crm.models import Deal, Lead

        # Cache = export précédent (résolutions siren conservées)
        previous: dict = {}
        if EXPORT_PATH.exists():
            try:
                previous = json.loads(EXPORT_PATH.read_text(encoding="utf-8")).get("profils", {})
            except (OSError, ValueError):
                logger.warning("Export précédent illisible — repart de zéro")

        leads = (
            Lead.objects
            .exclude(profile_snapshot__isnull=True)
            .exclude(linkedin_url__startswith="https://bdd-prospect.local")
            .exclude(linkedin_url__startswith="https://mailjet-hot.local")
        )
        if opts["limit"]:
            leads = leads[: opts["limit"]]

        # Statut prospection par lead (meilleur deal)
        deal_states = {}
        for slug, state in Deal.objects.filter(
            lead__public_identifier__in=[l.public_identifier for l in leads],
        ).values_list("lead__public_identifier", "state"):
            best = deal_states.get(slug)
            if state in _WARM_STATES or best is None:
                deal_states[slug] = state

        session = requests.Session()
        profils: dict[str, dict] = {}
        api_calls = 0
        reused = new_siren = no_siren = skipped = 0

        for lead in leads.iterator():
            fields = snapshot_fields(lead.profile_snapshot or {})
            if fields is None:
                skipped += 1
                continue
            slug = lead.public_identifier
            prev = previous.get(slug) or {}
            siren = prev.get("siren", "")
            definitif = bool(prev.get("siren_definitif"))
            if siren:
                reused += 1
            elif definitif and prev.get("societe") == fields["societe"]:
                reused += 1  # déjà tenté SANS erreur : introuvable, ne pas retenter
            elif fields["societe"] and api_calls < opts["max_api_calls"]:
                resolved = resolve_siren(fields["societe"], session)
                api_calls += 1
                time.sleep(THROTTLE_SECONDS)
                if resolved:  # trouvé
                    siren = resolved
                    definitif = True
                    new_siren += 1
                elif resolved == "":  # introuvable CONFIRMÉ par l'API
                    definitif = True
                # None (429/réseau) : ni siren ni definitif → retenté au prochain run
            if not siren:
                no_siren += 1

            state = deal_states.get(slug, "")
            statut = ("warm" if state in _WARM_STATES
                      else "contacted" if state else "sourced")
            profils[slug] = {
                **fields,
                "siren": siren,
                "siren_definitif": definitif,
                "linkedin_url": lead.linkedin_url,
                "email": lead.contact_email or "",
                "statut_prospection": statut,
                "disqualifie": lead.disqualified,
            }

        payload = {
            "genere_le": timezone.localtime().isoformat(),
            "description": ("Profils LinkedIn remontés par prospection-ia "
                            "(export_linkedin_enrichment) — nom/poste/société/URL. "
                            "Consommé par l'import antichambre (nom/prenom/poste + linkedinUrl) "
                            "et la boucle de découverte d'emails."),
            "profils": profils,
        }
        self.stdout.write(
            f"Profils exportés : {len(profils)} (skip sans nom : {skipped})\n"
            f"SIREN : {reused} réutilisés, {new_siren} résolus ce run "
            f"({api_calls} appels API), {no_siren} sans siren"
        )
        if opts["dry_run"]:
            self.stdout.write("(dry-run : rien écrit)")
            return
        EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        EXPORT_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8",
        )
        self.stdout.write(self.style.SUCCESS(f"Écrit : {EXPORT_PATH}"))
