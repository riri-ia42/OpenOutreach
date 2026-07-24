"""Non-régression : persistance du refresh_token roulé (panne 2026-07-22).

Le refresh_token Microsoft Graph (offline_access) est roulé à chaque refresh.
S'il n'est pas persisté, on retombe indéfiniment sur le token d'origine qui
meurt à 90 jours d'inactivité (AADSTS700082) → pipe mail HS. Ces tests
verrouillent : (1) lecture prioritaire du fichier cache, (2) persistance du
token roulé, (3) fallback env quand le fichier est absent.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ekoalu.notifications import graph_mailer as gm


@pytest.fixture
def token_file(tmp_path, monkeypatch):
    path = tmp_path / "graph_token.json"
    monkeypatch.setattr(gm, "_TOKEN_FILE", path)
    # reset le cache access-token in-process
    gm._cached_token = None
    gm._token_expires_at = 0.0
    return path


def test_load_prefere_le_fichier_puis_env(token_file, monkeypatch):
    monkeypatch.setenv("GRAPH_REFRESH_TOKEN", "env-token")
    assert gm._load_refresh_token() == "env-token"  # fichier absent → env
    token_file.write_text(json.dumps({"refresh_token": "file-token"}), encoding="utf-8")
    assert gm._load_refresh_token() == "file-token"  # fichier présent → prioritaire


def test_refresh_persiste_le_token_roule(token_file, monkeypatch):
    token_file.write_text(json.dumps({"refresh_token": "old-rt"}), encoding="utf-8")
    for name in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET"):
        monkeypatch.setenv(name, "x")

    class _Resp:
        ok = True
        status_code = 200

        def json(self):
            return {"access_token": "AT", "refresh_token": "new-rt", "expires_in": 3600}

    monkeypatch.setattr(gm.requests, "post", lambda *a, **k: _Resp())

    assert gm._get_access_token() == "AT"
    persisted = json.loads(token_file.read_text(encoding="utf-8"))
    assert persisted["refresh_token"] == "new-rt"  # rotation persistée


def test_config_error_si_aucun_token(token_file, monkeypatch):
    monkeypatch.delenv("GRAPH_REFRESH_TOKEN", raising=False)
    for name in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET"):
        monkeypatch.setenv(name, "x")
    with pytest.raises(gm.GraphConfigError):
        gm._get_access_token()
