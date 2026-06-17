"""Test du garde-fou anti 'Restaurer les pages' (revue 17/06 P0-1).

Chrome marque exit_type:"Crashed" quand le profil persistant n'est pas ferme
proprement (daemon tue). `_mark_profile_clean_exit` reecrit le flag a Normal
AVANT le lancement pour supprimer le bandeau + stopper la corruption du profil.
"""
from __future__ import annotations

import json

from linkedin.browser import login


def _write_prefs(profile_dir, payload: dict) -> None:
    default = profile_dir / "Default"
    default.mkdir(parents=True, exist_ok=True)
    (default / "Preferences").write_text(json.dumps(payload), encoding="utf-8")


def test_crashed_devient_normal(tmp_path, monkeypatch):
    monkeypatch.setattr(login, "LINKEDIN_PROFILE_DIR", tmp_path)
    _write_prefs(tmp_path, {"profile": {"exit_type": "Crashed", "exited_cleanly": False}})

    login._mark_profile_clean_exit()

    data = json.loads((tmp_path / "Default" / "Preferences").read_text(encoding="utf-8"))
    assert data["profile"]["exit_type"] == "Normal"
    assert data["profile"]["exited_cleanly"] is True


def test_conserve_les_autres_cles(tmp_path, monkeypatch):
    """On ne doit toucher QUE exit_type/exited_cleanly, pas le reste du profil."""
    monkeypatch.setattr(login, "LINKEDIN_PROFILE_DIR", tmp_path)
    _write_prefs(
        tmp_path,
        {"profile": {"exit_type": "Crashed", "name": "Default"}, "other": {"k": 1}},
    )

    login._mark_profile_clean_exit()

    data = json.loads((tmp_path / "Default" / "Preferences").read_text(encoding="utf-8"))
    assert data["profile"]["name"] == "Default"
    assert data["other"] == {"k": 1}


def test_pas_de_fichier_ne_plante_pas(tmp_path, monkeypatch):
    monkeypatch.setattr(login, "LINKEDIN_PROFILE_DIR", tmp_path)
    login._mark_profile_clean_exit()  # ne doit pas lever


def test_preferences_corrompu_ne_plante_pas(tmp_path, monkeypatch):
    monkeypatch.setattr(login, "LINKEDIN_PROFILE_DIR", tmp_path)
    default = tmp_path / "Default"
    default.mkdir(parents=True, exist_ok=True)
    (default / "Preferences").write_text("{not json", encoding="utf-8")
    login._mark_profile_clean_exit()  # JSONDecodeError avalee, pas de crash
