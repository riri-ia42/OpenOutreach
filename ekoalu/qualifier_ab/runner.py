"""Etat + orchestration de l'A/B qualifier (challenger Haiku vs champion Sonnet).

Pilote par 2 fichiers dans ``data/`` :
- ``qualifier_ab_test.json`` : sentinel d'activation ``{remaining, challenger_model, started_at}``
- ``qualifier_ab_results.jsonl`` : 1 ligne JSON par profil score (champion + challenger)

A epuisement du quota, on re-cree ``qualifier_disabled.flag`` (re-pause) et on
maile le recap. Lecture/ecriture a chaque qualif (le daemon qualifie 1 profil par
cycle, pas de concurrence).
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_CHALLENGER = "claude-haiku-4-5-20251001"
SENTINEL_NAME = "qualifier_ab_test.json"
RESULTS_NAME = "qualifier_ab_results.jsonl"
DISABLED_FLAG_NAME = "qualifier_disabled.flag"


def _data_path(name: str) -> str:
    from django.conf import settings
    return os.path.join(getattr(settings, "BASE_DIR", "."), "data", name)


def _read_sentinel() -> dict | None:
    path = _data_path(SENTINEL_NAME)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("A/B sentinel illisible (%s) -- A/B ignore", exc)
        return None


def ab_is_active() -> bool:
    state = _read_sentinel()
    return bool(state) and int(state.get("remaining", 0)) > 0


def start_ab(n: int = 50, challenger_model: str = DEFAULT_CHALLENGER) -> dict:
    """Active l'A/B pour ``n`` qualifications et leve le kill-switch qualifier."""
    from django.utils import timezone

    state = {
        "remaining": int(n),
        "total": int(n),
        "challenger_model": challenger_model,
        "started_at": timezone.now().isoformat(),
    }
    with open(_data_path(SENTINEL_NAME), "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    flag = _data_path(DISABLED_FLAG_NAME)
    if os.path.exists(flag):
        os.remove(flag)
        logger.info("A/B: qualifier_disabled.flag retire (qualifier reactive pour le test)")
    logger.info("A/B qualifier active : %d qualifs, challenger=%s", n, challenger_model)
    return state


def run_ab_qualification(profile_text: str, product_docs: str, campaign_objective: str,
                         public_id: str, campaign_id: int) -> tuple[int, str]:
    """Score le profil avec champion + challenger, logge, decremente. Retourne le verdict CHAMPION."""
    from linkedin.ml.qualifier import qualify_with_llm

    champ_label, champ_reason = qualify_with_llm(
        profile_text, product_docs=product_docs, campaign_objective=campaign_objective,
    )

    state = _read_sentinel() or {}
    challenger_model = state.get("challenger_model", DEFAULT_CHALLENGER)
    chal_label, chal_reason = _score_challenger(
        profile_text, product_docs, campaign_objective, challenger_model,
    )

    _append_result({
        "public_id": public_id,
        "campaign_id": campaign_id,
        "champion_model": "claude-sonnet-4-6",
        "champion_label": champ_label,
        "champion_reason": champ_reason,
        "challenger_model": challenger_model,
        "challenger_label": chal_label,
        "challenger_reason": chal_reason,
        "agree": (chal_label is not None and champ_label == chal_label),
    })
    _consume_one()
    return champ_label, champ_reason


def _score_challenger(profile_text, product_docs, objective, model_name):
    """Retourne (label, reason) du challenger, ou (None, msg) si indisponible/erreur."""
    from linkedin.models import SiteConfig

    if SiteConfig.load().llm_provider != "anthropic":
        return None, "challenger skip: provider != anthropic"
    try:
        from linkedin.llm import get_named_anthropic_model
        from linkedin.ml.qualifier import qualify_with_llm
        model = get_named_anthropic_model(model_name)
        return qualify_with_llm(
            profile_text, product_docs=product_docs, campaign_objective=objective, model=model,
        )
    except Exception as exc:  # noqa: BLE001 -- challenger ne doit jamais casser la qualif reelle
        logger.warning("Challenger %s a echoue: %s", model_name, exc)
        return None, f"challenger error: {exc}"


def _append_result(row: dict) -> None:
    from django.utils import timezone
    row["scored_at"] = timezone.now().isoformat()
    with open(_data_path(RESULTS_NAME), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _consume_one() -> None:
    state = _read_sentinel()
    if not state:
        return
    state["remaining"] = int(state.get("remaining", 0)) - 1
    if state["remaining"] > 0:
        with open(_data_path(SENTINEL_NAME), "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        return
    _finalize(state)


def _finalize(state: dict) -> None:
    """Quota epuise : re-pause le qualifier, supprime le sentinel, maile le recap."""
    open(_data_path(DISABLED_FLAG_NAME), "w", encoding="utf-8").close()
    sentinel = _data_path(SENTINEL_NAME)
    if os.path.exists(sentinel):
        os.remove(sentinel)
    logger.info("A/B qualifier termine -- qualifier re-pause (flag recree)")
    try:
        _mail_recap(state)
    except Exception as exc:  # noqa: BLE001 -- un echec mail ne doit pas casser le daemon
        logger.warning("Envoi recap A/B echoue: %s", exc)


def summarize() -> dict:
    """Agrege le JSONL de resultats pour le recap."""
    rows = []
    path = _data_path(RESULTS_NAME)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    total = len(rows)
    champ_yes = sum(1 for r in rows if r.get("champion_label") == 1)
    chal_scored = [r for r in rows if r.get("challenger_label") is not None]
    chal_yes = sum(1 for r in chal_scored if r.get("challenger_label") == 1)
    agree = sum(1 for r in chal_scored if r.get("agree"))
    return {
        "total": total,
        "champion_qualified": champ_yes,
        "challenger_scored": len(chal_scored),
        "challenger_qualified": chal_yes,
        "agreement": agree,
        "agreement_pct": round(100 * agree / len(chal_scored), 1) if chal_scored else 0.0,
        "rows": rows,
    }


def _mail_recap(state: dict) -> None:
    from ekoalu.notifications.graph_mailer import send_mail

    s = summarize()
    disagreements = [r for r in s["rows"]
                     if r.get("challenger_label") is not None and not r.get("agree")][:10]
    lines = "".join(
        f"<li><b>{r['public_id']}</b> — Sonnet={'OUI' if r['champion_label'] else 'non'} / "
        f"Haiku={'OUI' if r['challenger_label'] else 'non'}<br>"
        f"<small>Sonnet: {r['champion_reason'][:160]}<br>Haiku: {str(r['challenger_reason'])[:160]}</small></li>"
        for r in disagreements
    )
    html = (
        f"<h2>A/B qualifier termine ({s['total']} profils)</h2>"
        f"<ul>"
        f"<li>Champion (Sonnet) qualifie : <b>{s['champion_qualified']}/{s['total']}</b></li>"
        f"<li>Challenger (Haiku) qualifie : <b>{s['challenger_qualified']}/{s['challenger_scored']}</b></li>"
        f"<li>Accord des deux modeles : <b>{s['agreement']}/{s['challenger_scored']} "
        f"({s['agreement_pct']}%)</b></li>"
        f"</ul>"
        f"<p><b>Le qualifier est re-mis en pause.</b> Decide : (a) garder Sonnet, "
        f"(b) basculer Haiku (~3x moins cher), (c) revoir les filtres amont si le "
        f"taux de qualification est tres bas.</p>"
        f"<h3>Desaccords (max 10)</h3><ul>{lines or '<li>aucun</li>'}</ul>"
    )
    send_mail(subject=f"[EKOALU] A/B qualifier termine — {s['champion_qualified']}/{s['total']} qualifies",
              html_body=html)
    logger.info("Recap A/B maile a Richard")
