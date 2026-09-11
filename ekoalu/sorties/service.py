"""Sorties de prospection : cascade + registre + export partagé + garde import."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_EXPORT_PATH_ENV = "EKOALU_SORTIES_EXPORT_PATH"
_DEFAULT_EXPORT_PATH = r"C:\Users\RI.GROS\Documents\CLAUDE\_partage\sorties_prospection.json"

# Cache 60s du set de sirens sortis (même pattern que shared_exclusions)
_cache: tuple[float, frozenset[str]] | None = None
_CACHE_TTL_SECONDS = 60


def undo_kind_person() -> str:
    from ekoalu.undo.models import UndoEntry
    return UndoEntry.Kind.SORTIR_PROSPECT


def undo_kind_company() -> str:
    from ekoalu.undo.models import UndoEntry
    return UndoEntry.Kind.SORTIR_SOCIETE


def _display_label(public_id: str) -> str:
    """Nom affichable d'un lead (dirigeant mail-only ou heuristique slug)."""
    from ekoalu.prospect_display import resolve_prospect_display
    return resolve_prospect_display(public_id)["name"]


def sortir_prospect(public_id: str, *, reason: str = "") -> str:
    """Sort UNE personne de la prospection (tous canaux). Renvoie son label.

    Idempotent : re-sortir une personne déjà sortie ne crée pas de doublon.
    """
    from crm.models import Lead

    from ekoalu.lead_exclusion import disqualify_leads
    from ekoalu.sorties.models import ProspectionSortie
    from ekoalu.undo import service as undo

    reason = reason or "Sorti de la prospection par Richard"
    # Photographie AVANT la cascade : après, l'état précédent est perdu
    # (bouton « Annuler la dernière action », capture Richard 11/09).
    snapshot = undo.snapshot_leads([public_id])
    disqualify_leads([public_id], reason)

    lead = Lead.objects.filter(public_identifier=public_id).select_related("email_data").first()
    data = getattr(lead, "email_data", None) if lead else None
    label = _display_label(public_id) or public_id
    sortie, created = ProspectionSortie.objects.get_or_create(
        kind=ProspectionSortie.Kind.PERSON,
        public_identifier=public_id,
        defaults={
            "siren": (data.siren if data else ""),
            "label": label,
            "company_name": (data.entreprise if data else ""),
            "reason": reason,
        },
    )
    snapshot["sorties"] = [sortie.pk] if created else []
    undo.record(undo_kind_person(), f"Sortie de {label}", snapshot)
    export_shared_json()
    logger.info("Sortie prospect : %s (%s)", label, public_id)
    return label


def sortir_societe(*, siren: str = "", company_name: str = "",
                   reason: str = "") -> tuple[int, str, list[str]]:
    """Sort une SOCIÉTÉ entière : toutes les personnes rattachées + registre.

    Rattachement par siren (leads mail-only, groupe d'influence) ET par nom
    d'entreprise exact (PendingOutbound.prospect_company / EmailLeadData).
    Le siren entre aussi dans la garde à l'import.
    Renvoie (n_personnes, label, slugs_concernés) — les slugs servent à l'UI
    pour retirer les lignes sans recharger la page.
    """
    from ekoalu.email_canal.models import EmailLeadData
    from ekoalu.lead_exclusion import disqualify_leads
    from ekoalu.outbound_validation.models import PendingOutbound
    from ekoalu.sorties.models import ProspectionSortie

    if not siren and not company_name:
        return 0, "", []
    reason = reason or f"Société sortie de la prospection par Richard ({company_name or siren})"

    slugs: set[str] = set()
    if siren:
        slugs.update(
            EmailLeadData.objects.filter(siren=siren)
            .values_list("lead__public_identifier", flat=True)
        )
    if company_name:
        slugs.update(
            EmailLeadData.objects.filter(entreprise__iexact=company_name)
            .values_list("lead__public_identifier", flat=True)
        )
        slugs.update(
            PendingOutbound.objects.filter(prospect_company__iexact=company_name)
            .values_list("prospect_public_id", flat=True)
        )

    from ekoalu.undo import service as undo

    snapshot = undo.snapshot_leads(sorted(slugs))
    n_leads, _n_deals = disqualify_leads(sorted(slugs), reason)

    label = company_name or siren
    if siren:
        exists = ProspectionSortie.objects.filter(
            kind=ProspectionSortie.Kind.COMPANY, siren=siren,
        )
    else:
        exists = ProspectionSortie.objects.filter(
            kind=ProspectionSortie.Kind.COMPANY, company_name__iexact=company_name,
        )
    snapshot["sorties"] = []
    if not exists.exists():
        sortie = ProspectionSortie.objects.create(
            kind=ProspectionSortie.Kind.COMPANY,
            siren=siren,
            label=label,
            company_name=company_name,
            reason=reason,
        )
        snapshot["sorties"] = [sortie.pk]
    undo.record(undo_kind_company(), f"Sortie de la société {label}", snapshot)
    invalidate_cache()
    export_shared_json()
    logger.info("Sortie société : %s (siren=%s) — %d personne(s) disqualifiée(s)",
                label, siren or "?", len(slugs))
    return len(slugs), label, sorted(slugs)


def excluded_sirens() -> frozenset[str]:
    """Sirens des sociétés sorties (cache 60s) — consulté par les imports."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_TTL_SECONDS:
        return _cache[1]
    from ekoalu.sorties.models import ProspectionSortie
    sirens = frozenset(
        s for s in ProspectionSortie.objects
        .filter(kind=ProspectionSortie.Kind.COMPANY)
        .exclude(siren="")
        .values_list("siren", flat=True)
    )
    _cache = (now, sirens)
    return sirens


def is_company_excluded(siren: str) -> bool:
    """True si la société (siren) est sortie de la prospection."""
    return bool(siren) and siren in excluded_sirens()


def invalidate_cache() -> None:
    global _cache
    _cache = None


def export_shared_json(path: str | None = None) -> Path:
    """Écrit l'état complet des sorties dans le JSON partagé `_partage/`.

    Consommable par BDD PROSPECT / séances antichambre pour filtrer à la
    source. Échec d'écriture = warning, jamais bloquant (le registre DB
    reste la source of truth).
    """
    from ekoalu.sorties.models import ProspectionSortie

    target = Path(path or os.environ.get(_EXPORT_PATH_ENV) or _DEFAULT_EXPORT_PATH)
    rows = [
        {
            "kind": s.kind,
            "public_identifier": s.public_identifier,
            "siren": s.siren,
            "label": s.label,
            "company_name": s.company_name,
            "reason": s.reason,
            "added_at": s.created_at.isoformat(),
        }
        for s in ProspectionSortie.objects.all()
    ]
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"sorties": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Export sorties_prospection.json impossible (%s) : %s", target, exc)
    return target
