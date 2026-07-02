"""Analyse fine HEBDOMADAIRE : sourcing Google -> lectures LinkedIn -> selection.

Commande EN LECTURE SEULE (que des SELECT, aucun write, hors chemin du daemon)
demandee par Richard (18/06) : visualiser sur toute la data de la semaine
ce qui est CHERCHE / TROUVE / LU / VALIDE ou NON VALIDE, par campagne et
lead par lead, pour corriger l'ICP / le sourcing / le pre-filtre au besoin.

Deux couches de selection sont tracees :
  1. Verdict IA       : Deal.state QUALIFIED+ (retenu) vs FAILED/wrong_fit (rejete) + Deal.reason
  2. Validation Richard: PendingOutbound APPROVED/SENT (valide) vs REJECTED (refuse) + rejection_reason

Sortie : data/analyses/SEMAINE_<debut>_<fin>.html (rapport autonome avec
filtre client) + resume texte sur stdout. N'envoie aucun mail, ne modifie rien.

Usage (lundi matin) :
  python manage.py analyse_semaine                 # 7 derniers jours (hier inclus)
  python manage.py analyse_semaine --start 2026-06-15 --days 7
  python manage.py analyse_semaine --last-week     # lundi->dimanche precedent
"""
from __future__ import annotations

import html
import logging
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db.models import Count, Min
from django.utils import timezone as dj_tz

from chat.models import ChatMessage
from crm.models import Deal, Lead
from linkedin.models import SearchKeyword
from ekoalu.google_sourcing.models import GoogleSourcingState
from ekoalu.google_sourcing.queries import ABM_ROLE_TERMS, target_company_name
from ekoalu.lead_routing.models import LeadDiscovery
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound
from ekoalu.read_guard.models import ProfileReadDay

logger = logging.getLogger(__name__)

# Outcomes "fantomes" (doublons/ombres) a ne pas compter comme un vrai rejet metier.
SHADOW_OUTCOMES = {"duplicate", "shadow"}
SELECTED_STATES = {"Qualified", "Ready_to_connect", "Pending", "Connected", "Completed"}
# Etats du Deal qui prouvent que l'invitation LinkedIn a bien ete envoyee.
INVITED_STATES = {"Pending", "Connected", "Completed"}


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """Bornes aware [start 00:00, end+1 00:00) dans la timezone courante."""
    tz = dj_tz.get_current_timezone()
    lo = dj_tz.make_aware(datetime.combine(start, time.min), tz)
    hi = dj_tz.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)
    return lo, hi


def _campaign_label(name: str | None) -> str:
    return (name or "(sans campagne)").replace("EKOALU - ", "")


def collect(start: date, end: date) -> dict:
    """Rassemble toutes les mesures de la semaine. Aucun write."""
    lo, hi = _bounds(start, end)

    # --- 1. SOURCING : profils trouves (LeadDiscovery) par campagne ---
    discoveries = (
        LeadDiscovery.objects.filter(created_at__gte=lo, created_at__lt=hi)
        .select_related("campaign", "lead")
    )
    found_by_campaign: Counter[str] = Counter()
    found_lead_ids: set[int] = set()
    for d in discoveries:
        found_by_campaign[_campaign_label(d.campaign.name)] += 1
        found_lead_ids.add(d.lead_id)

    sourcing_state = [
        {
            "name": _campaign_label(s.campaign.name),
            "last_run": s.last_run_at,
            "exhausted": s.exhausted,
            "empty_runs": s.consecutive_empty_runs,
            "total_new": s.total_new_leads,
            "total_queries": s.total_queries,
        }
        for s in GoogleSourcingState.objects.select_related("campaign").order_by("-last_run_at")
        if s.last_run_at and s.last_run_at >= lo
    ]

    # --- 2. LECTURES LinkedIn : volume par jour x usage ---
    read_rows = ProfileReadDay.objects.filter(date__gte=start, date__lte=end).order_by("date")
    reads_by_day = []
    reads_total = 0
    reads_usage: Counter[str] = Counter()
    for r in read_rows:
        reads_total += r.count
        for k, v in (r.sources or {}).items():
            reads_usage[k] += v
        reads_by_day.append({"date": r.date, "count": r.count, "sources": r.sources or {}})

    # Profils effectivement LUS cette semaine (snapshot pose dans la fenetre).
    read_lead_ids = set(
        Lead.objects.filter(profile_snapshot_at__gte=lo, profile_snapshot_at__lt=hi)
        .values_list("id", flat=True)
    )

    # --- 3. SELECTION couche IA : deals touches dans la fenetre ---
    deals = (
        Deal.objects.filter(update_date__gte=lo, update_date__lt=hi)
        .select_related("lead", "campaign")
    )
    ia_selected = ia_rejected = 0
    reject_reasons: Counter[str] = Counter()
    reject_by_campaign: Counter[str] = Counter()
    deal_by_lead: dict[int, Deal] = {}
    for dl in deals:
        deal_by_lead.setdefault(dl.lead_id, dl)
        is_reject = dl.state == "Failed" and dl.outcome == "wrong_fit"
        is_shadow = dl.outcome in SHADOW_OUTCOMES
        if is_reject:
            ia_rejected += 1
            reason = (dl.reason or "(sans motif)").strip()[:120]
            reject_reasons[reason] += 1
            reject_by_campaign[_campaign_label(dl.campaign.name if dl.campaign else None)] += 1
        elif dl.state in SELECTED_STATES and not is_shadow:
            ia_selected += 1

    # --- 4. SELECTION couche Richard : PendingOutbound decides dans la fenetre ---
    po_qs = PendingOutbound.objects.filter(created_at__gte=lo, created_at__lt=hi)
    raw = {row["status"]: row["n"] for row in po_qs.values("status").annotate(n=Count("id"))}
    po_counts = {
        "valides": raw.get(OutboundStatus.APPROVED, 0) + raw.get(OutboundStatus.SENT, 0),
        "refuses": raw.get(OutboundStatus.REJECTED, 0),
        "attente": raw.get(OutboundStatus.PENDING, 0),
        "echecs": raw.get(OutboundStatus.FAILED, 0),
    }
    richard_rejections = [
        {"company": po.prospect_company, "campaign": _campaign_label(po.campaign_name),
         "reason": (po.rejection_reason or "(sans motif)").strip()[:140], "kind": po.kind}
        for po in po_qs.filter(status=OutboundStatus.REJECTED).order_by("-created_at")[:50]
    ]
    po_by_lead_public: dict[str, str] = {
        po.prospect_public_id: po.status
        for po in po_qs.exclude(prospect_public_id="").order_by("created_at")
        if po.prospect_public_id
    }

    # --- 5. DETAIL lead par lead (union : trouve / lu / verdict cette semaine) ---
    touched_ids = found_lead_ids | read_lead_ids | set(deal_by_lead)

    # Engagement (cycle de vie, TOUTE periode : la chaine s'etale sur plusieurs
    # semaines — cooldown 4-48h, acceptation tardive). On lit l'etat ACTUEL du lead.
    invited_at = _invitations_sent(touched_ids)                       # public_id -> sent_at
    first_msg_at = _first_outgoing_messages(touched_ids)              # lead_id -> creation_date
    disco_campaign = _discovery_campaign(touched_ids)                 # lead_id -> campaign
    native_kw = _native_keywords_map()                                # campaign_id -> [mots-cles]

    leads = Lead.objects.filter(id__in=touched_ids).select_related().order_by("-creation_date")
    rows = []
    invited_n = accepted_n = messaged_n = 0
    for lead in leads:
        dl = deal_by_lead.get(lead.id)
        if dl is None:
            dl = lead.deal_set.order_by("-update_date").first() if hasattr(lead, "deal_set") else None
        verdict, reason, campaign = _verdict(dl)
        if verdict == "—" and lead.id in read_lead_ids:
            # Profil lu (embedding/enrichissement) mais jamais selectionne par
            # l'active-learning pour un verdict LLM -> pas de Deal.
            verdict = "lu, non qualifie"
        camp, proven_query = disco_campaign.get(lead.id, (None, ""))
        if camp is None and dl:
            camp = dl.campaign
        company, is_google = _google_keywords(camp)
        # Sourcing par recherche LinkedIn NATIVE (persona) : pas de requete Serper
        # loguee, mais le mot-cle vient bien de la campagne persona (ex. "maçon").
        is_native = camp is not None and not proven_query and not is_google
        persona = _persona_label(camp) if is_native else ""
        native_terms = native_kw.get(camp.id, []) if (is_native and camp) else []
        location, poste, entreprise = _snapshot_facts(lead)
        pub = lead.public_identifier or ""
        invite_dt = invited_at.get(pub) or (dl.connected_at if dl and dl.state in INVITED_STATES else None)
        invited = bool(invite_dt) or (dl is not None and dl.state in INVITED_STATES)
        accepted_dt = dl.connected_at if dl else None
        msg_dt = first_msg_at.get(lead.id)
        invited_n += int(invited)
        accepted_n += int(bool(accepted_dt))
        messaged_n += int(bool(msg_dt))
        rows.append({
            "name": pub or lead.linkedin_url or f"lead#{lead.id}",
            "url": lead.linkedin_url,
            "company": company, "is_google": is_google, "proven_query": proven_query,
            "is_native": is_native, "persona": persona, "native_kw": native_terms,
            "location": location, "poste": poste, "entreprise": entreprise,
            "campaign": campaign,
            "found": lead.id in found_lead_ids,
            "read": lead.id in read_lead_ids,
            "verdict": verdict,
            "reason": reason,
            "richard": po_by_lead_public.get(pub, ""),
            "invited": invited, "invited_at": invite_dt,
            "accepted_at": accepted_dt,
            "first_msg_at": msg_dt,
        })

    return {
        "start": start, "end": end,
        "found_by_campaign": found_by_campaign, "found_total": len(found_lead_ids),
        "sourcing_state": sourcing_state,
        "reads_total": reads_total, "reads_usage": reads_usage, "reads_by_day": reads_by_day,
        "read_total_leads": len(read_lead_ids),
        "ia_selected": ia_selected, "ia_rejected": ia_rejected,
        "reject_reasons": reject_reasons.most_common(20),
        "reject_by_campaign": reject_by_campaign.most_common(),
        "po_counts": po_counts, "richard_rejections": richard_rejections,
        "engage": {"invited": invited_n, "accepted": accepted_n, "messaged": messaged_n},
        "rows": rows,
    }


def _discovery_campaign(lead_ids: set[int]) -> dict[int, tuple]:
    """lead_id -> (campagne, requete Serper prouvee) de la 1re decouverte.

    ``query`` vide = origine NON tracee (decouverte avant le logging des requetes,
    recherche native, ou cross-attribution a l'enrichissement) : dans ce cas on
    n'affiche PAS l'entreprise comme un mot-cle Google (ce serait trompeur)."""
    out: dict[int, tuple] = {}
    qs = (
        LeadDiscovery.objects.filter(lead_id__in=lead_ids)
        .select_related("campaign").order_by("created_at")
    )
    for x in qs:
        out.setdefault(x.lead_id, (x.campaign, x.query or ""))
    return out


def _google_keywords(campaign) -> tuple[str, bool]:
    """Renvoie (entreprise ciblee, True) pour une campagne ABM sourcee via Google,
    sinon ("", False) — profil trouve par la recherche LinkedIn native."""
    if campaign is None:
        return "", False
    company = target_company_name(campaign)
    return (company, True) if company else ("", False)


def _native_keywords_map() -> dict[int, list[str]]:
    """campaign_id -> liste des mots-cles de recherche LinkedIn native de la campagne.

    Les campagnes persona (maçon, dirigeant, archi...) cherchent via ces mots-cles.
    On ne loge pas LEQUEL a matche un profil donne, mais on affiche le jeu de la
    campagne : le mot-cle 'maçon' EST bien saisi quelque part (reponse Richard 01/07)."""
    out: dict[int, list[str]] = defaultdict(list)
    for cid, kw in SearchKeyword.objects.values_list("campaign_id", "keyword"):
        out[cid].append(kw)
    return out


def _persona_label(campaign) -> str:
    """Libelle persona court d'une campagne (nom sans le prefixe 'EKOALU - ')."""
    name = (getattr(campaign, "name", "") or "").strip()
    return name[len("EKOALU - "):] if name.startswith("EKOALU - ") else name


def _snapshot_facts(lead) -> tuple[str, str, str]:
    """(localisation, poste, entreprise) depuis le snapshot LinkedIn si lu, sinon vides."""
    snap = lead.profile_snapshot or {}
    if not isinstance(snap, dict):
        return "", "", ""
    location = (snap.get("location_name") or "").strip()
    poste = (snap.get("headline") or "").strip()
    positions = snap.get("positions") or []
    entreprise = ""
    if positions and isinstance(positions[0], dict):
        entreprise = (positions[0].get("company_name") or "").strip()
    return location, poste, entreprise


def _invitations_sent(lead_ids: set[int]) -> dict[str, datetime]:
    """public_identifier -> date d'envoi de l'invitation LinkedIn (PendingOutbound SENT)."""
    pub_ids = set(
        Lead.objects.filter(id__in=lead_ids)
        .exclude(public_identifier="")
        .values_list("public_identifier", flat=True)
    )
    out: dict[str, datetime] = {}
    qs = PendingOutbound.objects.filter(
        kind=OutboundKind.INVITATION, status=OutboundStatus.SENT,
        prospect_public_id__in=pub_ids,
    ).order_by("sent_at")
    for po in qs:
        out.setdefault(po.prospect_public_id, po.sent_at)
    return out


def _first_outgoing_messages(lead_ids: set[int]) -> dict[int, datetime]:
    """lead_id -> date du PREMIER message sortant (1er contact apres acceptation)."""
    ct = ContentType.objects.get_for_model(Lead)
    rows = (
        ChatMessage.objects.filter(content_type=ct, object_id__in=lead_ids, is_outgoing=True)
        .values("object_id").annotate(first=Min("creation_date"))
    )
    return {r["object_id"]: r["first"] for r in rows}


def _verdict(deal: Deal | None) -> tuple[str, str, str]:
    if deal is None:
        return "—", "", "(non qualifie)"
    campaign = _campaign_label(deal.campaign.name if deal.campaign else None)
    if deal.state == "Failed" and deal.outcome == "wrong_fit":
        return "rejete", (deal.reason or "").strip(), campaign
    if deal.state in SELECTED_STATES:
        return "retenu", (deal.reason or "").strip(), campaign
    return deal.state, (deal.reason or "").strip(), campaign


# ----------------------------- rendu HTML -----------------------------

def render_html(d: dict) -> str:
    eff = (100.0 * d["ia_selected"] / (d["ia_selected"] + d["ia_rejected"])
           if (d["ia_selected"] + d["ia_rejected"]) else None)
    eff_txt = f"{eff:.1f}%" if eff is not None else "n/a"

    found_rows = "".join(
        f"<tr><td>{_esc(k)}</td><td style='text-align:right'>{v}</td></tr>"
        for k, v in d["found_by_campaign"].most_common()
    ) or "<tr><td colspan=2 style='color:#6b7280'>(aucun profil trouve)</td></tr>"

    usage_rows = "".join(
        f"<tr><td>{_esc(k)}</td><td style='text-align:right'>{v}</td></tr>"
        for k, v in d["reads_usage"].most_common()
    ) or "<tr><td colspan=2 style='color:#6b7280'>(aucune lecture)</td></tr>"

    day_rows = "".join(
        f"<tr><td>{r['date']:%a %d/%m}</td><td style='text-align:right'>{r['count']}</td>"
        f"<td style='color:#6b7280;font-size:12px'>{_esc(', '.join(f'{k}:{v}' for k,v in r['sources'].items()))}</td></tr>"
        for r in d["reads_by_day"]
    ) or "<tr><td colspan=3 style='color:#6b7280'>(aucune lecture)</td></tr>"

    reject_reason_rows = "".join(
        f"<tr><td>{_esc(r)}</td><td style='text-align:right'>{n}</td></tr>"
        for r, n in d["reject_reasons"]
    ) or "<tr><td colspan=2 style='color:#6b7280'>(aucun rejet)</td></tr>"

    reject_camp_rows = "".join(
        f"<tr><td>{_esc(k)}</td><td style='text-align:right'>{n}</td></tr>"
        for k, n in d["reject_by_campaign"]
    ) or "<tr><td colspan=2 style='color:#6b7280'>(aucun rejet)</td></tr>"

    po = d["po_counts"]
    richard_rows = "".join(
        f"<tr><td>{_esc(r['company'])}</td><td>{_esc(r['campaign'])}</td>"
        f"<td>{_esc(r['kind'])}</td><td>{_esc(r['reason'])}</td></tr>"
        for r in d["richard_rejections"]
    ) or "<tr><td colspan=4 style='color:#6b7280'>(aucun refus Richard sur la periode)</td></tr>"

    detail_rows = "".join(_detail_row(r) for r in d["rows"]) or \
        "<tr><td colspan=6 style='color:#6b7280'>(aucun lead touche)</td></tr>"

    sourcing_rows = "".join(
        f"<tr><td>{_esc(s['name'])}</td>"
        f"<td>{s['last_run']:%d/%m %H:%M}</td>"
        f"<td style='text-align:center'>{'⛔ epuisee' if s['exhausted'] else '✅'}</td>"
        f"<td style='text-align:right'>{s['empty_runs']}</td>"
        f"<td style='text-align:right'>{s['total_new']}</td>"
        f"<td style='text-align:right'>{s['total_queries']}</td></tr>"
        for s in d["sourcing_state"]
    ) or "<tr><td colspan=6 style='color:#6b7280'>(aucune rotation Serper sur la periode)</td></tr>"

    return f"""<!DOCTYPE html><html lang=fr><head><meta charset=utf-8>
<title>Analyse semaine {d['start']:%d/%m}-{d['end']:%d/%m}</title>
<style>
 body{{font-family:-apple-system,Segoe UI,sans-serif;max-width:1100px;margin:0 auto;padding:24px;color:#111827}}
 h1{{border-bottom:2px solid #3b82f6;padding-bottom:8px}}
 h2{{color:#1f2937;margin-top:32px}}
 table{{width:100%;border-collapse:collapse;margin:12px 0;font-size:14px}}
 th{{background:#f3f4f6;text-align:left;padding:6px}} td{{padding:6px;border-bottom:1px solid #eee}}
 .kpi{{display:inline-block;background:#eff6ff;border-radius:8px;padding:12px 18px;margin:4px}}
 .kpi b{{font-size:24px;color:#2563eb;display:block}}
 .b-retenu{{background:#dcfce7;color:#166534;padding:2px 8px;border-radius:6px;font-size:12px}}
 .b-rejete{{background:#fee2e2;color:#991b1b;padding:2px 8px;border-radius:6px;font-size:12px}}
 .b-na{{background:#f3f4f6;color:#6b7280;padding:2px 8px;border-radius:6px;font-size:12px}}
 .b-attente{{background:#fef9c3;color:#854d0e;padding:2px 8px;border-radius:6px;font-size:12px}}
 input{{padding:8px;width:320px;border:1px solid #d1d5db;border-radius:6px;margin:8px 0}}
</style></head><body>
<h1>Analyse fine de la semaine — {d['start']:%d/%m/%Y} → {d['end']:%d/%m/%Y}</h1>

<h2>Entonnoir global</h2>
<div>
 <span class=kpi>Profils trouves<b>{d['found_total']}</b></span>
 <span class=kpi>Profils lus (LinkedIn)<b>{d['read_total_leads']}</b></span>
 <span class=kpi>Retenus (IA)<b>{d['ia_selected']}</b></span>
 <span class=kpi>Rejetes (IA)<b>{d['ia_rejected']}</b></span>
 <span class=kpi>Efficacite tri<b>{eff_txt}</b></span>
 <span class=kpi>Lectures totales<b>{d['reads_total']}</b></span>
</div>
<div>
 <span class=kpi>Demandes LinkedIn<b>{d['engage']['invited']}</b></span>
 <span class=kpi>Accords prospect<b>{d['engage']['accepted']}</b></span>
 <span class=kpi>Premiers messages<b>{d['engage']['messaged']}</b></span>
</div>
<p style='color:#6b7280;font-size:13px'>Engagement = etat ACTUEL des leads touches cette
 semaine (la chaine invitation -> acceptation -> message s'etale sur plusieurs semaines).</p>
<p style='color:#6b7280;font-size:13px'>Validation Richard sur la periode —
 valides : {po['valides']} ·
 refuses : {po['refuses']} ·
 en attente : {po['attente']}</p>

<h2>1. Sourcing Google (rotation Serper)</h2>
<table><thead><tr><th>Campagne</th><th>Profils trouves (semaine)</th></tr></thead><tbody>{found_rows}</tbody></table>
<table><thead><tr><th>Campagne</th><th>Dernier run</th><th>Etat</th><th>Runs vides</th><th>Total leads (cumul)</th><th>Requetes (cumul)</th></tr></thead><tbody>{sourcing_rows}</tbody></table>

<h2>2. Lectures LinkedIn (anti-ban)</h2>
<table><thead><tr><th>Jour</th><th>Lectures</th><th>Ventilation</th></tr></thead><tbody>{day_rows}</tbody></table>
<table><thead><tr><th>Usage</th><th>Total semaine</th></tr></thead><tbody>{usage_rows}</tbody></table>

<h2>3. Selection IA — motifs de rejet</h2>
<table><thead><tr><th>Motif (tronque)</th><th>Occurrences</th></tr></thead><tbody>{reject_reason_rows}</tbody></table>
<table><thead><tr><th>Campagne</th><th>Rejets</th></tr></thead><tbody>{reject_camp_rows}</tbody></table>

<h2>4. Refus de Richard (validation manuelle)</h2>
<table><thead><tr><th>Entreprise</th><th>Campagne</th><th>Type</th><th>Motif</th></tr></thead><tbody>{richard_rows}</tbody></table>

<h2>5. Chaine complete par lead — mot-cle -> resultat -> lecture -> verdict -> invitation -> accord -> message</h2>
<p style='color:#6b7280;font-size:13px'>La colonne <b>Mot-cle / origine</b> montre LA requete qui a trouve le profil :
 <b>(1)</b> <code>site:linkedin.com/in ...</code> = requete Serper EXACTE (ABM/SECTEUR, logging depuis le 25/06) ;
 <b>(1 bis)</b> <span style='color:#2563eb'>native</span> · <code>mot-cle</code> = requete de recherche LinkedIn native EXACTE (logging depuis le 02/07) ;
 <b>(2)</b> <span style='color:#2563eb'>recherche native</span> ancienne (avant le 02/07) : la phrase exacte n'est pas tracee, on liste
 alors les mots-cles candidats de la campagne persona (« l'une de : … ») ;
 <b>(3)</b> <span style='color:#b45309'>entreprise — origine non tracee</span> = campagne ABM sans preuve de requete
 (decouverte ancienne ou cross-attribution). Les colonnes <b>Poste / Entreprise / Localisation</b>
 viennent de la fiche LinkedIn lue (vides si le profil n'a pas encore ete lu).</p>
<input id=flt placeholder="Filtrer (entreprise, profil, poste, lieu, motif, verdict)..." onkeyup="flt()">
<table id=detail><thead><tr><th>Mot-cle / origine</th><th>Resultat (profil)</th><th>Poste</th><th>Entreprise</th><th>Localisation</th><th>Lu</th><th>Verdict IA</th><th>Justification</th><th>Invitation</th><th>Accord</th><th>1er msg</th></tr></thead>
<tbody>{detail_rows}</tbody></table>
<script>
function flt(){{var q=document.getElementById('flt').value.toLowerCase();
 document.querySelectorAll('#detail tbody tr').forEach(function(tr){{
  tr.style.display = tr.innerText.toLowerCase().indexOf(q)>-1 ? '' : 'none';}});}}
</script>
<hr><p style='color:#6b7280;font-size:12px'>Genere par <code>manage.py analyse_semaine</code> — lecture seule, aucune donnee modifiee.</p>
</body></html>"""


def _stage_cell(dt: datetime | None, mark_ok: bool = False) -> str:
    """Cellule d'etape : date si connue, '✓' si l'etape est atteinte sans date, sinon vide."""
    if dt is not None:
        return f"<td style='text-align:center;font-size:12px'>{dt:%d/%m}</td>"
    return f"<td style='text-align:center;color:#16a34a'>{'✓' if mark_ok else ''}</td>"


def _keywords_cell(r: dict) -> str:
    # 1. Requete EXACTE journalisee -> on affiche LA requete utilisee.
    #    Serper (ABM/SECTEUR) commence par 'site:' ; sinon = mot-cle natif exact
    #    (logging natif actif depuis le 02/07).
    if r["proven_query"]:
        q = r["proven_query"]
        if q.startswith("site:"):
            return f"<td style='font-size:12px'><code>{_esc(q)}</code></td>"
        return (f"<td style='font-size:12px'><span style='color:#2563eb'>native</span> · "
                f"<code>{_esc(q)}</code></td>")
    # 2. Recherche native ANCIENNE (avant le logging du 02/07) : on ne connait pas
    #    LA phrase exacte, on affiche donc les mots-cles candidats de la campagne.
    if r.get("is_native") and r["persona"]:
        terms = r.get("native_kw") or []
        if terms:
            lst = _esc(" · ".join(terms))
            return (f"<td style='font-size:11px'>"
                    f"<span style='color:#2563eb'>recherche native</span> · <b>{_esc(r['persona'])}</b>"
                    f"<br><span style='color:#9ca3af'>requete exacte non tracee — l'une de : {lst}</span></td>")
        return (f"<td style='font-size:12px'><span style='color:#2563eb'>recherche native</span> · "
                f"<b>{_esc(r['persona'])}</b></td>")
    # 3. Campagne ABM mais origine NON tracee : on ne pretend PAS que l'entreprise
    #    est le mot-cle (cf. cibles 'vides' sur Google + cross-attribution).
    if r["is_google"] and r["company"]:
        return (f"<td style='font-size:12px;color:#b45309'>{_esc(r['company'])}"
                f"<br><i style='color:#9ca3af'>campagne — origine non tracee</i></td>")
    return "<td style='font-size:12px;color:#9ca3af'>origine non tracee</td>"


def _detail_row(r: dict) -> str:
    badge = {"retenu": "b-retenu", "rejete": "b-rejete",
             "lu, non qualifie": "b-attente"}.get(r["verdict"], "b-na")
    name = f"<a href='{_esc(r['url'])}'>{_esc(r['name'])}</a>" if r["url"] else _esc(r["name"])
    rich = f" · Richard: {_esc(r['richard'])}" if r["richard"] else ""
    return (
        f"<tr>{_keywords_cell(r)}<td>{name}</td>"
        f"<td style='font-size:12px'>{_esc(r['poste'])}</td>"
        f"<td style='font-size:12px'>{_esc(r['entreprise'])}</td>"
        f"<td style='font-size:12px'>{_esc(r['location'])}</td>"
        f"<td style='text-align:center'>{'•' if r['read'] else ''}</td>"
        f"<td><span class='{badge}'>{_esc(r['verdict'])}</span></td>"
        f"<td style='font-size:12px;color:#374151'>{_esc(r['reason'])}{rich}</td>"
        f"{_stage_cell(r['invited_at'], r['invited'])}"
        f"{_stage_cell(r['accepted_at'])}"
        f"{_stage_cell(r['first_msg_at'])}</tr>"
    )


def render_text(d: dict) -> str:
    total = d["ia_selected"] + d["ia_rejected"]
    eff = f"{100.0*d['ia_selected']/total:.1f}%" if total else "n/a"
    po = d["po_counts"]
    motifs = [f"  - ({n}) {r}" for r, n in d["reject_reasons"][:8]] or ["  (aucun)"]
    return "\n".join([
        f"=== Analyse semaine {d['start']:%d/%m} -> {d['end']:%d/%m} ===",
        f"Profils trouves     : {d['found_total']}",
        f"Profils lus (LK)    : {d['read_total_leads']}  (lectures totales {d['reads_total']})",
        f"Retenus / rejetes IA: {d['ia_selected']} / {d['ia_rejected']}  (efficacite {eff})",
        f"Validation Richard  : valides {po['valides']} / "
        f"refuses {po['refuses']} / attente {po['attente']}",
        f"Engagement (actuel) : invites {d['engage']['invited']} / "
        f"acceptes {d['engage']['accepted']} / 1er msg {d['engage']['messaged']}",
        f"Leads detailles     : {len(d['rows'])}",
        "Top motifs de rejet IA :",
        *motifs,
    ])


class Command(BaseCommand):
    help = "Analyse hebdo lecture seule : sourcing Google -> lectures LinkedIn -> selection."

    def add_arguments(self, parser):
        parser.add_argument("--start", type=str, default=None,
                            help="Premier jour analyse (YYYY-MM-DD). Defaut: il y a 7 jours.")
        parser.add_argument("--days", type=int, default=7, help="Nombre de jours (defaut 7).")
        parser.add_argument("--last-week", action="store_true",
                            help="Lundi->dimanche de la semaine precedente (ignore --start/--days).")

    def handle(self, *args, **opts):
        today = dj_tz.localdate()
        if opts["last_week"]:
            this_monday = today - timedelta(days=today.weekday())
            start = this_monday - timedelta(days=7)
            end = this_monday - timedelta(days=1)
        elif opts["start"]:
            start = datetime.strptime(opts["start"], "%Y-%m-%d").date()
            end = start + timedelta(days=opts["days"] - 1)
        else:
            end = today - timedelta(days=1)
            start = end - timedelta(days=opts["days"] - 1)

        data = collect(start, end)
        out_dir = Path(settings.ROOT_DIR) / "data" / "analyses"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"SEMAINE_{start:%Y-%m-%d}_{end:%Y-%m-%d}.html"
        out.write_text(render_html(data), encoding="utf-8")

        self.stdout.write(render_text(data))
        self.stdout.write(self.style.SUCCESS(f"\nRapport HTML : {out}"))
        logger.info("analyse_semaine %s->%s ecrite dans %s", start, end, out)
