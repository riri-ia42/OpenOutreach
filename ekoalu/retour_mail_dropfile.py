"""Drop-file partagé avec le worker retour-mail (`_partage/retour-mail-corrections.json`).

retour-mail y dépose ses corrections (bounces, changements d'adresse) et chaque
projet cible les importe (`manage.py import_retour_mail`). Depuis la fiche hub
du 2026-09-09, prospection-ia y dépose AUSSI les adresses jugées inexistantes
par la vérification avant envoi, déjà marquées `imported_by: ["prospection-ia"]`
puisque le Lead est bouncé sur place : c'est l'antichambre qui doit suivre, et
retour-mail relance son import dès qu'une entrée reste en attente.
Format : append-only, dédoublonné par (type, email) — même règle que le worker.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

APP = "prospection-ia"


def drop_file_path() -> Path:
    custom = os.environ.get("RETOUR_MAIL_DROP_FILE", "").strip()
    if custom:
        return Path(custom)
    # ekoalu/ openoutreach/ prospection-ia/ -> parents[3] = CLAUDE/
    return Path(__file__).resolve().parents[3] / "_partage" / "retour-mail-corrections.json"


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("corrections"), list):
        return {"updated_at": "", "corrections": []}
    return data


def add_hard_bounce(email: str, provenance: str) -> bool:
    """Dépose une correction `hard_bounce` déjà appliquée ici. False si déjà présente."""
    path = drop_file_path()
    data = _load(path)
    clean = email.strip().lower()
    key = f"hard_bounce|{clean}"
    for c in data["corrections"]:
        if isinstance(c, dict) and f"{c.get('type')}|{str(c.get('email') or '').lower()}" == key:
            return False
    data["corrections"].append({
        "type": "hard_bounce",
        "email": clean,
        "provenance": provenance,
        "added_at": date.today().isoformat(),
        "imported_by": [APP],
    })
    data["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return True
