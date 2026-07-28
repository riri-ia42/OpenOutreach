"""Constitue le groupe d'influence des entreprises prioritaires DECP.

Pour chaque entreprise cible (Lead source=decp, cible_prioritaire par défaut) :
1. dirigeants (personnes physiques) via recherche-entreprises.api.gouv.fr
2. emails nominatifs publiés sur le site officiel (pages équipe/contact/mentions)
3. emails nominatifs indexés par Google (Serper, si configuré) : `"@domaine"`
4. pattern d'adressage déduit → emails candidats pour les dirigeants restants

Chaque personne devient un Lead mail-only distinct rattaché au même SIREN
(`bdd-prospect-<siren>-i<n>`), source EmailLeadData `decp_influence`, avec le
contexte marché hérité (accroche cold mail) et la priorité vivier de l'entreprise.

Usage :
    python manage.py enrich_influence_decp --dry-run
    python manage.py enrich_influence_decp                     # prioritaires only
    python manage.py enrich_influence_decp --all-decp --max-persons 4
    python manage.py enrich_influence_decp --siren 300820354
"""
from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand

from ekoalu.influence_enrich import (
    SITE_PAGES,
    build_influence_group,
    email_domain,
    extract_domain_emails,
    is_company_domain,
)

logger = logging.getLogger(__name__)

RECHERCHE_API = "https://recherche-entreprises.api.gouv.fr/search"
THROTTLE_S = 0.22  # ~4.5 req/s, sous la limite API gouv


def _fetch(url: str, timeout: int = 10) -> str:
    import requests

    try:
        res = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        if res.status_code != 200:
            return ""
        ctype = res.headers.get("content-type", "")
        if ctype and not any(t in ctype for t in ("html", "plain", "xml")):
            return ""
        return res.text[:500_000]
    except Exception:  # noqa: BLE001 — site injoignable = pas d'emails, on continue
        return ""


def fetch_site_emails(domain: str) -> set[str]:
    """Emails @domaine publiés sur le site officiel (arrêt dès 3 nominatifs potentiels)."""
    found: set[str] = set()
    for proto in ("https", "http"):
        base = f"{proto}://{domain}"
        if _fetch(base + "/"):
            for page in SITE_PAGES:
                found |= extract_domain_emails(_fetch(base + page), domain)
                if len(found) >= 6:
                    break
            return found
    return found


def fetch_google_emails(domain: str) -> set[str]:
    """Emails @domaine indexés par Google via Serper. Vide si non configuré."""
    from ekoalu.google_sourcing import client as serper

    if not serper.is_configured():
        return set()
    found: set[str] = set()
    try:
        for r in serper.search_raw(f'"@{domain}"', num=10):
            blob = " ".join(str(r.get(k, "")) for k in ("title", "snippet", "link"))
            found |= extract_domain_emails(blob, domain)
    except Exception as exc:  # noqa: BLE001 — Serper down ≠ blocage
        logger.warning("enrich_influence: serper KO pour %s : %s", domain, exc)
    return found


def fetch_dirigeants(siren: str) -> list[tuple[str, str]]:
    """Dirigeants personnes physiques [(prenom, nom), ...] via l'API gouv."""
    import requests

    time.sleep(THROTTLE_S)
    try:
        res = requests.get(RECHERCHE_API, params={"q": siren, "per_page": 1},
                           timeout=15)
        if res.status_code != 200:
            return []
        results = res.json().get("results") or []
        if not results or results[0].get("siren") != siren:
            return []
        out = []
        for d in results[0].get("dirigeants") or []:
            if d.get("type_dirigeant") != "personne physique":
                continue
            prenom = (d.get("prenoms") or "").split()[0] if d.get("prenoms") else ""
            nom = (d.get("nom") or "").split("(")[0].strip()
            if nom:
                out.append((prenom, nom))
        return out
    except Exception:  # noqa: BLE001
        return []


class Command(BaseCommand):
    help = "Trouve les emails de personnes physiques des entreprises prioritaires DECP (groupe d'influence)."

    def add_arguments(self, parser):
        parser.add_argument("--all-decp", action="store_true",
                            help="Toutes les entreprises DECP (défaut : cibles prioritaires).")
        parser.add_argument("--siren", default="", help="Une seule entreprise (siren 9 chiffres).")
        parser.add_argument("--max-persons", type=int, default=3,
                            help="Personnes max ajoutées par entreprise (défaut 3).")
        parser.add_argument("--limit-companies", type=int, default=0,
                            help="Limite d'entreprises traitées (0 = toutes).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Affiche le groupe d'influence sans rien créer.")

    def handle(self, *args, **opts):
        from crm.models import Lead
        from ekoalu.email_canal.models import EmailLeadData

        qs = EmailLeadData.objects.filter(source=EmailLeadData.SOURCE_DECP).select_related("lead")
        if opts["siren"]:
            qs = qs.filter(siren=opts["siren"])
        companies = [d for d in qs
                     if opts["all_decp"] or opts["siren"]
                     or (d.raw_json or {}).get("cible_prioritaire")]
        # Reprise : skip les entreprises dont le groupe existe déjà
        done_sirens = set(
            EmailLeadData.objects.filter(source=EmailLeadData.SOURCE_DECP_INFLUENCE)
            .values_list("siren", flat=True)
        )
        companies = [d for d in companies if d.siren not in done_sirens]
        if opts["limit_companies"] > 0:
            companies = companies[: opts["limit_companies"]]

        self.stdout.write(self.style.NOTICE(
            f"Entreprises à enrichir : {len(companies)} "
            f"(déjà faites : {len(done_sirens)}) | max_persons={opts['max_persons']} | "
            f"dry_run={opts['dry_run']}",
        ))

        created = 0
        skipped_domain = 0
        companies_with_people = 0
        for data in companies:
            domain = email_domain(data.lead.contact_email or "")
            if not is_company_domain(domain):
                skipped_domain += 1
                continue

            dirigeants = fetch_dirigeants(data.siren)
            site = fetch_site_emails(domain)
            google = fetch_google_emails(domain)
            existing = set(
                Lead.objects.filter(contact_email__endswith=f"@{domain}")
                .values_list("contact_email", flat=True)
            )
            people = build_influence_group(
                domain=domain, site_emails=site, google_emails=google,
                dirigeants=dirigeants, existing_emails=existing,
                max_persons=opts["max_persons"],
            )
            if not people:
                continue
            companies_with_people += 1
            self.stdout.write(f"\n→ {data.entreprise or domain} ({data.siren}) — "
                              f"{len(people)} personne(s)")
            for i, person in enumerate(people, start=1):
                self.stdout.write(f"   {person.display_name} <{person.email}> "
                                  f"[{person.source}{'/' + person.role if person.role else ''}]")
                if opts["dry_run"]:
                    continue
                if Lead.objects.filter(contact_email=person.email).exists():
                    continue
                public_id = f"bdd-prospect-{data.siren}-i{i}"
                if Lead.objects.filter(public_identifier=public_id).exists():
                    public_id = f"bdd-prospect-{data.siren}-i{i}-{int(time.time())}"
                raw = dict(data.raw_json or {})
                raw["influence"] = {"display_name": person.display_name,
                                    "source": person.source, "role": person.role,
                                    "entreprise_lead_id": data.lead_id}
                lead = Lead.objects.create(
                    linkedin_url=f"https://bdd-prospect.local/siren/{data.siren}/i{i}",
                    public_identifier=public_id,
                    contact_email=person.email,
                    contact_email_source="decp_influence",
                )
                EmailLeadData.objects.create(
                    lead=lead,
                    source=EmailLeadData.SOURCE_DECP_INFLUENCE,
                    siren=data.siren,
                    entreprise=data.entreprise,
                    dirigeant=person.display_name,
                    code_naf=data.code_naf,
                    cp=data.cp, dpt=data.dpt, ville=data.ville,
                    raw_json=raw,
                )
                created += 1

        self.stdout.write(self.style.SUCCESS(
            f"\n--- Bilan ---\n"
            f"  entreprises traitées         : {len(companies)}\n"
            f"  avec groupe d'influence      : {companies_with_people}\n"
            f"  personnes créées (leads)     : {created}\n"
            f"  skip domaine webmail/absent  : {skipped_domain}\n"
            f"  dry_run                      : {opts['dry_run']}",
        ))
        logger.info("enrich_influence_decp: companies=%d created=%d skipped_domain=%d",
                    len(companies), created, skipped_domain)
