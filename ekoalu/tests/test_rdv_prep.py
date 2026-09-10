"""Préparation automatique des RDV (10/09) : collecte, rendu, service, vues.
Aucun réseau : Graph, Gateway, API entreprises, Serper et Claude sont simulés."""
from __future__ import annotations

import datetime as dt
import json
from unittest.mock import patch

import pytest
from django.utils import timezone

from ekoalu.email_canal.models import ProspectRdv
from ekoalu.notifications import graph_calendar
from ekoalu.rdv_prep import render, research, service

CONTENT = {
    "essentiel": {"qui": "BE mécanique, 15 personnes", "pourquoi": "Inconnu", "pourquoi_detail": "réservation spontanée",
                  "promis": "Rien", "voulu": "Trancher le motif"},
    "lead_in": "Trois lectures possibles.",
    "personne": [["Métier", "Responsable BE"]], "societe": [["Forme", "SAS 1965"]],
    "hypotheses": "Projet ou fournisseur ?", "vigilance_contact": "Aucun contact avant.",
    "deroule": [{"heure": "16:00", "minutes": 5, "titre": "Cadrer.", "texte": "Merci.", "phrase": "Qu'est-ce qui vous amène ?"}],
    "questions": [{"groupe": "Activité", "items": [[True, "Quels projets ?"], [False, "Quelles agences ?"]]}],
    "arguments": [["Vous posez ?", "Non.", "Périmètre clair"]],
    "vigilances_specifiques": ["Il vient peut-être vendre."],
    "apres": ["Mail de suite."],
    "deck": {"head": "Ce qu'on apporte à un BE", "eyebrow": "Du plan à l'ensemble", "bullets": [["Ensembles sur plans :", "alu et acier"]] * 6,
             "lead": "Ce qui nous aide.", "foot": "Pour un bureau d'études", "recevez": "le guide."},
}


def _rdv(**kw):
    start = timezone.now() + dt.timedelta(days=3)
    defaults = dict(event_id="EV-1", who="Anthony Laveau", service="Visio présentation complète",
                    start=start.replace(hour=14, minute=0, second=0, microsecond=0),
                    end=start.replace(hour=15, minute=0, second=0, microsecond=0),
                    prospect_email="", status=ProspectRdv.Status.PLANNED)
    defaults.update(kw)
    return ProspectRdv.objects.create(**defaults)


EVENT = {"id": "GRAPH-EV", "isCancelled": False, "subject": "Visio présentation complète - Anthony Laveau",
         "organizer": {"emailAddress": {"address": "EKOALUPrisedeRDV@ekoalu.com"}},
         "body": {"contentType": "html", "content": "Nom : Anthony Laveau<br>Adresse de courrier : anthony.laveau@berlioz-industrie.fr<br>Numéro de téléphone : 0767427915<br><a href=\"https://teams.microsoft.com/meet/373?p=abc\">Rejoindre</a>"},
         "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/x"}}


class TestGraphCalendar:
    def test_booking_details(self):
        d = graph_calendar.booking_details(EVENT)
        assert d["email"] == "anthony.laveau@berlioz-industrie.fr"
        assert d["phone"] == "0767427915"
        assert d["teams_url"] == "https://teams.microsoft.com/meet/373?p=abc"   # lien court du corps prime
        assert d["event_id"] == "GRAPH-EV"


class TestResearch:
    def test_company_hint(self):
        assert research.company_hint_from("GEAY Lionel société SAGE ECO", "") == "SAGE ECO"
        assert research.company_hint_from("Anthony Laveau", "a@berlioz-industrie.fr") == "berlioz industrie"
        assert research.company_hint_from("X Y", "x@gmail.com") == ""

    def test_registre_entreprise_ko_renvoie_vide(self):
        import requests as _rq
        with patch("ekoalu.rdv_prep.research.requests.get", side_effect=_rq.ConnectionError("boom")):
            assert research.registre_entreprise("berlioz industrie") == {}


@pytest.mark.django_db
class TestService:
    def _mocks(self, tmp_path, monkeypatch, content=CONTENT):
        monkeypatch.setenv("EKOALU_SUPPORTS_DIR", str(_supports(tmp_path)))
        return [
            patch("ekoalu.notifications.graph_calendar.list_events", return_value=[dict(EVENT, start={"dateTime": "2030-01-01T00:00:00.0000000"})]),
            patch("ekoalu.rdv_prep.research.registre_entreprise", return_value={"nom": "BERLIOZ INDUSTRIE", "siren": "965501158"}),
            patch("ekoalu.rdv_prep.research.outlook_exchanges", return_value=[]),
            patch("ekoalu.rdv_prep.research.linkedin_public", return_value={"title": "Anthony LAVEAU - BERLIOZ INDUSTRIE"}),
            patch("ekoalu.rdv_prep.writer.write_brief", return_value=content),
            patch("ekoalu.rdv_prep.render.deck_to_pdf", return_value=False),
            patch("ekoalu.rdv_prep.service._hub"),
        ]

    def test_prepare_one_ecrit_les_fichiers_et_cree_le_creneau(self, tmp_path, monkeypatch, settings):
        settings.BASE_DIR = tmp_path
        rdv = _rdv()
        mocks = self._mocks(tmp_path, monkeypatch)
        from contextlib import ExitStack
        with ExitStack() as stack, patch("ekoalu.notifications.graph_calendar.create_event", return_value="PREP-EV") as ce:
            for m in mocks:
                stack.enter_context(m)
            # find_calendar_event compare les heures : on force la correspondance
            stack.enter_context(patch("ekoalu.rdv_prep.service.find_calendar_event",
                                      return_value=graph_calendar.booking_details(EVENT)))
            res = service.prepare_one(rdv)
        assert res["ok"], res
        rdv.refresh_from_db()
        assert rdv.prep_status == ProspectRdv.Prep.DONE and rdv.prep_event_id == "PREP-EV"
        assert rdv.prospect_email == "anthony.laveau@berlioz-industrie.fr" and rdv.teams_url.startswith("https://teams")
        out = tmp_path / "data" / "rdv" / str(rdv.pk)
        brief = (out / "brief.html").read_text(encoding="utf-8")
        assert "Anthony Laveau" in brief and "vous amène ?" in brief and "Jamais « on pose »" in brief
        deck = (out / "deck.html").read_text(encoding="utf-8")
        assert "apporte à un BE" in deck and "data:image/jpeg;base64" in deck and "<title>EKOALU pour BERLIOZ INDUSTRIE</title>" in deck
        ce.assert_called_once()
        kw = ce.call_args.kwargs
        assert kw["start"] == (timezone.localtime(rdv.start) - dt.timedelta(minutes=30)).replace(tzinfo=None)
        assert res["links"]["brief"].endswith(f"/ekoalu/rdv/{rdv.pk}/brief/")

    def test_claude_vide_marque_failed_sans_fichier(self, tmp_path, monkeypatch, settings):
        settings.BASE_DIR = tmp_path
        rdv = _rdv(event_id="EV-2")
        from contextlib import ExitStack
        with ExitStack() as stack:
            for m in self._mocks(tmp_path, monkeypatch, content={}):
                stack.enter_context(m)
            stack.enter_context(patch("ekoalu.rdv_prep.service.find_calendar_event", return_value={}))
            res = service.prepare_one(rdv)
        assert not res["ok"]
        rdv.refresh_from_db()
        assert rdv.prep_status == ProspectRdv.Prep.FAILED
        assert not (tmp_path / "data" / "rdv" / str(rdv.pk)).exists()

    def test_pending_rdvs_exclut_prepares_et_passes(self):
        a = _rdv(event_id="A")
        b = _rdv(event_id="B", prep_status=ProspectRdv.Prep.DONE)
        _rdv(event_id="C", start=timezone.now() - dt.timedelta(days=1), end=timezone.now() - dt.timedelta(hours=23))
        ids = {r.pk for r in service.pending_rdvs(21)}
        assert a.pk in ids and b.pk not in ids and len(ids) == 1

    def test_vues_servent_les_fichiers(self, tmp_path, admin_client):
        rdv = _rdv(event_id="V")
        d = tmp_path / "rdvV"; d.mkdir()
        (d / "brief.html").write_text("<h1>BRIEF</h1>", encoding="utf-8")
        rdv.prep_dir, rdv.prep_status = str(d), ProspectRdv.Prep.DONE
        rdv.save()
        assert b"BRIEF" in admin_client.get(f"/ekoalu/rdv/{rdv.pk}/brief/").content
        assert admin_client.get(f"/ekoalu/rdv/{rdv.pk}/deck/").status_code == 404
        assert b"Anthony Laveau" in admin_client.get("/ekoalu/rdv/").content


def _supports(tmp_path):
    """Copie minimale des bases supports/ (brief + deck) pour le rendu."""
    import shutil
    from pathlib import Path

    real = Path(__file__).resolve().parents[3] / "supports"
    dst = tmp_path / "supports"
    if real.exists():
        shutil.copytree(real / "brief-rdv", dst / "brief-rdv", ignore=shutil.ignore_patterns("*.html"));
        shutil.copy(real / "brief-rdv" / "template.html", dst / "brief-rdv" / "template.html")
        shutil.copytree(real / "deck-rdv", dst / "deck-rdv")
    return dst


class TestRender:
    def test_render_brief_echappe_le_html(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EKOALU_SUPPORTS_DIR", str(_supports(tmp_path)))
        facts = {"who": "X <script>", "email": "x@y.fr", "phone": "", "service": "Visio", "start_iso": "2026-09-14T16:00:00+02:00",
                 "end_iso": "2026-09-14T17:00:00+02:00", "teams_url": "", "company": {}, "company_hint": "Y", "history": [], "outlook": [], "sources": []}
        html = render.render_brief(facts, CONTENT)
        assert "&lt;script&gt;" in html and "<script>alert" not in html
        assert "Lundi 14 septembre 2026" in html
