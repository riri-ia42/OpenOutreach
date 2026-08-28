"""Tests remontée LinkedIn (lot 1 unification — export_linkedin_enrichment)."""
from __future__ import annotations

import json

import pytest
from django.core.management import call_command

from crm.models import Lead
from ekoalu.management.commands.export_linkedin_enrichment import (
    resolve_siren,
    snapshot_fields,
)


def _snap(**over):
    base = {
        "full_name": "Patrick Gomes",
        "first_name": "Patrick",
        "last_name": "Gomes",
        "headline": "Directeur Travaux chez GCR",
        "location_name": "Lyon, Auvergne-Rhône-Alpes",
        "industry": "Construction",
        "positions": [{"title": "Directeur Travaux", "company_name": "GCR"}],
    }
    base.update(over)
    return base


class TestSnapshotFields:
    def test_extraction_nominale(self):
        f = snapshot_fields(_snap())
        assert f == {
            "nom": "Gomes", "prenom": "Patrick",
            "poste": "Directeur Travaux chez GCR", "societe": "GCR",
            "localisation": "Lyon, Auvergne-Rhône-Alpes", "industrie": "Construction",
        }

    def test_industry_en_dict_ne_crashe_pas(self):
        # Vu en réel : industry peut être {"name": ...} selon la source du snapshot
        f = snapshot_fields(_snap(industry={"name": "BTP"}))
        assert f["industrie"] == "BTP"

    def test_company_en_dict(self):
        f = snapshot_fields(_snap(positions=[{"title": "PDG", "company": {"name": "ACME"}}]))
        assert f["societe"] == "ACME"

    def test_sans_positions_poste_depuis_headline(self):
        f = snapshot_fields(_snap(positions=[]))
        assert f["societe"] == ""
        assert f["poste"] == "Directeur Travaux chez GCR"

    def test_sans_nom_renvoie_none(self):
        assert snapshot_fields({}) is None
        assert snapshot_fields({"full_name": ""}) is None

    def test_prenom_deduit_du_full_name(self):
        f = snapshot_fields(_snap(first_name="", last_name="", full_name="Marie Perret"))
        assert f["prenom"] == "Marie"
        assert f["nom"] == "Perret"


class TestResolveSiren:
    class _FakeSession:
        def __init__(self, results):
            self._results = results

        def get(self, *a, **k):
            results = self._results

            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"results": results}
            return R()

    def test_garde_anti_homonyme(self):
        # Le résultat API ne recoupe pas le nom cherché → ''
        s = self._FakeSession([{"siren": "111", "nom_complet": "AUTRE CHOSE SAS"}])
        assert resolve_siren("METALLERIE DURAND", s) == ""

    def test_match_valide(self):
        s = self._FakeSession([{"siren": "123456789", "nom_complet": "METALLERIE DURAND"}])
        assert resolve_siren("METALLERIE DURAND", s) == "123456789"

    def test_nom_trop_court(self):
        assert resolve_siren("AB", self._FakeSession([])) == ""


@pytest.mark.django_db
class TestCommande:
    def test_export_dry_run(self, tmp_path, monkeypatch, capsys):
        import ekoalu.management.commands.export_linkedin_enrichment as mod
        monkeypatch.setattr(mod, "EXPORT_PATH", tmp_path / "linkedin-enrichis.json")
        monkeypatch.setattr(mod, "resolve_siren", lambda c, s: "999000111")
        Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/patrick-gomes",
            public_identifier="patrick-gomes",
            profile_snapshot=_snap(),
        )
        # Lead mail-only : exclu de l'export
        Lead.objects.create(
            linkedin_url="https://bdd-prospect.local/siren/123",
            public_identifier="bdd-prospect-123",
            profile_snapshot=_snap(),
        )
        call_command("export_linkedin_enrichment", "--dry-run")
        out = capsys.readouterr().out
        assert "Profils exportés : 1" in out

    def test_export_ecrit_le_fichier(self, tmp_path, monkeypatch):
        import ekoalu.management.commands.export_linkedin_enrichment as mod
        target = tmp_path / "linkedin-enrichis.json"
        monkeypatch.setattr(mod, "EXPORT_PATH", target)
        monkeypatch.setattr(mod, "resolve_siren", lambda c, s: "999000111")
        Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/patrick-gomes",
            public_identifier="patrick-gomes",
            profile_snapshot=_snap(),
        )
        call_command("export_linkedin_enrichment")
        data = json.loads(target.read_text(encoding="utf-8"))
        p = data["profils"]["patrick-gomes"]
        assert p["nom"] == "Gomes"
        assert p["poste"].startswith("Directeur Travaux")
        assert p["siren"] == "999000111"
        assert p["statut_prospection"] == "sourced"
        assert p["linkedin_url"] == "https://www.linkedin.com/in/patrick-gomes"

    def test_cache_siren_reutilise(self, tmp_path, monkeypatch):
        import ekoalu.management.commands.export_linkedin_enrichment as mod
        target = tmp_path / "linkedin-enrichis.json"
        monkeypatch.setattr(mod, "EXPORT_PATH", target)
        calls = []
        monkeypatch.setattr(mod, "resolve_siren",
                            lambda c, s: calls.append(c) or "999000111")
        Lead.objects.create(
            linkedin_url="https://www.linkedin.com/in/patrick-gomes",
            public_identifier="patrick-gomes",
            profile_snapshot=_snap(),
        )
        call_command("export_linkedin_enrichment")
        call_command("export_linkedin_enrichment")
        # 2e run : siren lu depuis l'export précédent, aucune résolution
        assert len(calls) == 1
