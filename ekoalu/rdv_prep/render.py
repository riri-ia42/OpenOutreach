"""Rendu HTML du brief et du deck depuis les bases `supports/brief-rdv` et
`supports/deck-rdv` du dépôt parent (validées par Richard le 09/09/2026).

- Brief : le CSS et le squelette viennent de `brief-rdv/template.html` ; le
  contenu (JSON de writer.py) remplit les sections. Aucun HTML n'est écrit par
  le modèle : tout passe par `html.escape`.
- Deck : `deck-rdv/template.html` + `img/` (images incrustées), slide 6
  remplacée par la slide persona, cover et slide 8 adaptées ; PDF paysage via
  Chrome headless (Patchright), même recette que `deck-rdv/build.py`.
"""
from __future__ import annotations

import base64
import html
import os
import re
from pathlib import Path

_H = html.escape


def supports_dir() -> Path:
    custom = os.environ.get("EKOALU_SUPPORTS_DIR", "").strip()
    return Path(custom) if custom else Path(__file__).resolve().parents[3] / "supports"


def _brief_head() -> str:
    tpl = (supports_dir() / "brief-rdv" / "template.html").read_text(encoding="utf-8")
    return tpl[: tpl.index("<main>")]


def _brief_tail() -> str:
    tpl = (supports_dir() / "brief-rdv" / "template.html").read_text(encoding="utf-8")
    return tpl[tpl.index("<script>"):]


def _pairs(rows) -> str:
    return "".join(f"<dt>{_H(str(k))}</dt><dd>{_H(str(v))}</dd>" for k, v in (rows or []))


def render_brief(facts: dict, content: dict, *, deck_url: str = "") -> str:
    e = content.get("essentiel") or {}
    who, start, end = facts.get("who", ""), facts.get("start_iso", ""), facts.get("end_iso", "")
    day = _fr_date(start)
    hhmm, hhmm_end = start[11:16], end[11:16]
    teams = facts.get("teams_url", "")
    company = (facts.get("company") or {}).get("nom") or facts.get("company_hint", "")
    slug = re.sub(r"[^a-z0-9]+", "-", (who + " " + company).lower()).strip("-")[:60]
    head = _brief_head().replace("<title>Trame RDV [[PRENOM NOM]] [[SOCIETE]]</title>",
                                 f"<title>Visio {_H(who)}</title>")
    tail = _brief_tail().replace("var KEY = 'brief-[[slug-prospect]]';", f"var KEY = 'brief-{slug}';")
    parts = [head, "<main>",
        '<header class="fiche"><div class="who">',
        f'<span class="eyebrow">{_H(facts.get("service", "Rendez-vous"))} · réservé via Bookings</span>',
        f"<h1>{_H(who)}{(', ' + _H(company)) if company else ''}</h1>",
        f"<p>{_H(e.get('qui', ''))}</p>",
        f'<p class="mono" style="font-size:13.5px;color:var(--muted)">{_H(facts.get("phone", ""))} · {_H(facts.get("email", ""))}</p>',
        '</div><div class="when">',
        f'<span class="eyebrow">{_H(day)}</span><div class="clock">{_H(hhmm)}</div>',
        f'<span class="mono" style="font-size:13px;color:var(--muted)">jusqu\'à {_H(hhmm_end)}</span></div>',
        '<div class="teams">',
        (f'<a class="btn" href="{_H(teams)}">Rejoindre la réunion Teams</a>' if teams else '<span class="mono">Lien Teams : voir l\'invitation Bookings</span>'),
        (f'<a href="{_H(deck_url)}">Deck de présentation</a>' if deck_url else ""),
        "</div></header>",
        # 01
        '<section><h2><span class="eyebrow">01</span>L\'essentiel en trente secondes</h2><div class="facts">',
        f'<div><span class="k">Qui</span><span class="v">{_H(e.get("qui", ""))}</span></div>',
        f'<div><span class="k">Pourquoi il nous voit</span><span class="v big">{_H(e.get("pourquoi", ""))}</span><span style="font-size:13px;color:var(--muted)">{_H(e.get("pourquoi_detail", ""))}</span></div>',
        f'<div><span class="k">Ce qu\'on lui a promis</span><span class="v">{_H(e.get("promis", ""))}</span></div>',
        f'<div><span class="k">Ce qu\'on veut</span><span class="v">{_H(e.get("voulu", ""))}</span></div>',
        f'</div><p class="lead-in">{_H(content.get("lead_in", ""))}</p></section>',
        # 02
        '<section><h2><span class="eyebrow">02</span>Qui est en face</h2><div class="duo">',
        f'<div class="panel"><h3>La personne</h3><dl>{_pairs(content.get("personne"))}</dl></div>',
        f'<div class="panel"><h3>La société</h3><dl>{_pairs(content.get("societe"))}</dl></div></div>',
        f'<div class="callout"><strong>Hypothèses à vérifier en ouverture.</strong> {_H(content.get("hypotheses", ""))}</div></section>',
        # 03
        '<section><h2><span class="eyebrow">03</span>Historique du contact</h2><ul class="timeline">',
        *(f'<li><span class="d">{_H(h.get("date", ""))}</span><span>{_H(h.get("type", ""))} : {_H(h.get("objet") or h.get("intent") or "")} {_H((h.get("extrait") or "")[:160])}</span></li>'
          for h in (facts.get("history") or [])),
        *(f'<li><span class="d">{_H(x.get("date", ""))}</span><span>Outlook, de {_H(x.get("from", ""))} : {_H(x.get("objet", ""))}</span></li>'
          for x in (facts.get("outlook") or [])),
        (f'<li><span class="d">{_H(day[:10])}</span><span class="hit">Réservation Bookings « {_H(facts.get("service", ""))} »</span></li>'),
        f'</ul><div class="callout warn">{_H(content.get("vigilance_contact", ""))}</div></section>',
        # 04
        '<section><h2><span class="eyebrow">04</span>Déroulé</h2><ol class="plan">',
        *(f'<li><span class="t">{_H(s.get("heure", ""))}<small>{int(s.get("minutes") or 0)} min</small></span><div><p><strong>{_H(s.get("titre", ""))}</strong> {_H(s.get("texte", ""))}</p>'
          + (f'<p class="say">« {_H(s.get("phrase", ""))} »</p>' if s.get("phrase") else "") + "</div></li>"
          for s in (content.get("deroule") or [])),
        "</ol></section>",
        # 05
        '<section><h2><span class="eyebrow">05</span>Questions de découverte</h2><div class="qs">',
        *(f'<div class="grp"><h3>{_H(g.get("groupe", ""))}</h3><ul>'
          + "".join(f'<li class="{"key" if k else ""}">{_H(q)}</li>' for k, q in (g.get("items") or []))
          + "</ul></div>" for g in (content.get("questions") or [])),
        "</div></section>",
        # 06
        '<section><h2><span class="eyebrow">06</span>Arguments et preuves à sortir</h2><div class="tbl"><table>',
        "<thead><tr><th>S'il dit</th><th>Réponse</th><th>Preuve</th></tr></thead><tbody>",
        *(f"<tr><td>{_H(a)}</td><td>{_H(b)}</td><td>{_H(c)}</td></tr>" for a, b, c in
          ((r + ["", "", ""])[:3] for r in (content.get("arguments") or []))),
        "</tbody></table></div></section>",
        # 07
        '<section><h2><span class="eyebrow">07</span>Points de vigilance</h2><div class="vig">',
        '<div><span class="dot red"></span><span><strong>Jamais « on pose ».</strong> EKOALU conçoit, fabrique, livre.</span></div>',
        '<div><span class="dot red"></span><span><strong>Gammes à citer : Hydro, Wicona, SAPA, Technal, Jansen.</strong> Ni Cortizo ni Sepalumic.</span></div>',
        '<div><span class="dot red"></span><span><strong>Pas de délai chiffré.</strong> Réponse rapide selon taille et technicité, date annoncée et tenue. Atelier : 1 000 m².</span></div>',
        *(f'<div><span class="dot"></span><span>{_H(v)}</span></div>' for v in (content.get("vigilances_specifiques") or [])),
        '<div><span class="dot"></span><span><strong>Ton.</strong> Direct, des chiffres, aucune flatterie. Mails terminés par « Bien à vous ».</span></div>',
        "</div></section>",
        # 08
        '<section><h2><span class="eyebrow">08</span>Après le rendez-vous</h2><ul class="todo" id="todo">',
        *(f'<li><label><input type="checkbox" data-k="a{i}"><span>{_H(a)}</span></label></li>'
          for i, a in enumerate(content.get("apres") or [])),
        '</ul><div class="notes" style="margin-top:18px"><h3>Notes de séance</h3><textarea id="notes" placeholder="Projets cités, mots employés, engagements pris de part et d\'autre…"></textarea><div class="hint">Les notes et les cases restent dans ce navigateur uniquement.</div></div></section>',
        # 09
        '<section class="sources"><h2><span class="eyebrow">09</span>Sources</h2><ul>',
        *(f"<li>{_H(s)}</li>" for s in (facts.get("sources") or ["agenda Outlook (Bookings)"])),
        "<li>Agenda Outlook (événement Bookings : coordonnées, lien Teams). Brief généré automatiquement, relu par personne : vérifier les faits marqués non confirmés.</li>",
        "</ul></section></main>", tail]
    return "\n".join(parts)


_FR_DAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
_FR_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
              "septembre", "octobre", "novembre", "décembre"]


def _fr_date(iso: str) -> str:
    import datetime as dt

    try:
        d = dt.datetime.fromisoformat(iso)
    except ValueError:
        return iso[:10]
    return f"{_FR_DAYS[d.weekday()]} {d.day} {_FR_MONTHS[d.month - 1]} {d.year}"


# ---------------------------------------------------------------- deck

_OFFER_START = '<div class="head"><h2>Ce que vous recevez pour vos DPGF</h2>'
_OFFER_END = '<div class="foot"><span>EKOALU · Pour les économistes</span>'
_COVER = 'Visio découverte · 9 septembre 2026 · SAGE ECO, Commelle-Vernay'
_RECV_RE = re.compile(r'<strong>Ce que vous recevez ce soir</strong>.*?</p>', re.S)


def render_deck(facts: dict, deck: dict) -> str:
    base = supports_dir() / "deck-rdv"
    s = (base / "template.html").read_text(encoding="utf-8")
    who = facts.get("who", "")
    company = (facts.get("company") or {}).get("nom") or facts.get("company_hint", "") or who
    s = s.replace("<title>EKOALU pour SAGE ECO</title>", f"<title>EKOALU pour {_H(company)}</title>", 1)
    s = s.replace(_COVER, f"{_H(facts.get('service', 'Visio'))} · {_H(_fr_date(facts.get('start_iso', '')))} · {_H(company)}", 1)
    nl = "\n"
    bullets = "".join(
        '        <div><span class="dot"></span><span><strong>' + _H(a) + "</strong> " + _H(b) + "</span></div>" + nl
        for a, b in ((list(x) + ["", ""])[:2] for x in (deck.get("bullets") or []))
    )
    body = (
        '<div class="head"><h2>' + _H(deck.get("head", "Ce qu'on apporte")) + '</h2><span class="eyebrow">'
        + _H(deck.get("eyebrow", "")) + "</span></div>" + nl
        + '    <div class="body">' + nl + '      <div class="offer">' + nl + bullets + "      </div>" + nl
        + '      <p class="lead">' + _H(deck.get("lead", "")) + "</p>" + nl + "    </div>" + nl + "    "
    )
    i, j = s.index(_OFFER_START), s.index(_OFFER_END)
    s = s[:i] + body + s[j:]
    s = s.replace(_OFFER_END, f'<div class="foot"><span>EKOALU · {_H(deck.get("foot", ""))}</span>', 1)
    s = _RECV_RE.sub('<strong>Ce que vous recevez ce soir</strong> : ' + _H(deck.get("recevez", "le guide des solutions et le récapitulatif de ce que nous nous sommes dit.")) + "</p>", s, count=1)

    def _img(m):
        p = base / "img" / (m.group(1) + ".jpg")
        return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()
    s = re.sub(r"\{\{IMG:([a-z0-9_]+)\}\}", _img, s)
    return s


PRINT_CSS = """<style>
@page { size: 297mm 167mm; margin: 0; }
html,body{background:#fff !important;background-image:none !important;}
.deck{padding:0 !important;gap:0 !important;max-width:none !important;}
.slide{width:297mm !important;height:167mm !important;aspect-ratio:auto !important;border:0 !important;break-after:page;page-break-after:always;box-sizing:border-box;}
.slide:last-child{break-after:auto;page-break-after:auto;}
.nav,.hint{display:none !important;}
</style>"""


def deck_to_pdf(html_text: str, pdf_path: Path) -> bool:
    """PDF paysage via Chrome headless. False si Playwright/Chrome indisponible."""
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return False
    tmp = pdf_path.with_suffix(".print.html")
    tmp.write_text(html_text.replace("</style>", "</style>" + PRINT_CSS, 1), encoding="utf-8")
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(channel="chrome", headless=True)
            pg = b.new_page(viewport={"width": 1123, "height": 631})
            pg.goto("file:///" + str(tmp).replace("\\", "/"))
            pg.wait_for_load_state("networkidle")
            pg.wait_for_timeout(1200)
            pg.emulate_media(media="print")
            pg.pdf(path=str(pdf_path), width="297mm", height="167mm", print_background=True, prefer_css_page_size=True)
            b.close()
    except Exception:  # noqa: BLE001 — le HTML reste disponible, le PDF est un plus
        return False
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return True
