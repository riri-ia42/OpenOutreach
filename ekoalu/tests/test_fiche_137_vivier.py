"""Fiche hub #137 (09/09) : filtres d'import (dpt, seuil par NAF) et tri du
vivier cold mail par rendement observé, réserve Mailjet en queue."""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from crm.models import Lead
from ekoalu.bdd_prospect_import import (
    DPT_RHONE_ALPES,
    REJECT_DPT_NOT_TARGET,
    REJECT_EFFECTIF_TOO_SMALL,
    EligibilityFilters,
    is_eligible,
    parse_contact,
)
from ekoalu.email_canal.models import EmailLeadData
from ekoalu.email_canal.pool import cold_mail_candidates
from ekoalu.email_canal.yield_score import build_yield_table, rank_key_factory
from ekoalu.inbox_assist.models import PendingReply
from ekoalu.management.commands.import_bdd_prospect import _parse_dpt
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound


def _raw(naf="43.32B", dpt="42", eff_min=5, eff_max=9, email="lionel.geay@sageco.fr"):
    return {"email": email, "properties": {
        "siren": "994930733", "entreprise": "SAGE ECO", "dirigeant": "Lionel Geay",
        "code_naf": naf, "cp": f"{dpt}120", "dpt": dpt, "ville": "COMMELLE-VERNAY",
        "effectif_min": eff_min, "effectif_max": eff_max}}


class TestFiltresImport:
    def test_parse_dpt(self):
        assert _parse_dpt("") is None
        assert _parse_dpt("RA") == DPT_RHONE_ALPES
        assert _parse_dpt("69, 1,42") == frozenset({"69", "01", "42"})

    def test_dpt_hors_perimetre_rejete(self):
        f = EligibilityFilters(naf_allowed=frozenset({"43.32B"}), dpt_allowed=DPT_RHONE_ALPES,
                               min_effectif=5)
        assert is_eligible(parse_contact(_raw(dpt="42")), f) is None
        assert is_eligible(parse_contact(_raw(dpt="75")), f) == REJECT_DPT_NOT_TARGET

    def test_seuil_effectif_par_naf(self):
        f = EligibilityFilters(naf_allowed=frozenset({"43.32B", "71.12B"}), min_effectif=10,
                               min_effectif_by_naf=(("43.32B", 5),))
        # métallerie de 5 à 9 : acceptée grâce au seuil spécifique
        assert is_eligible(parse_contact(_raw(naf="43.32B", eff_min=5, eff_max=9)), f) is None
        # BET de même taille : seuil global 10 conservé
        assert is_eligible(parse_contact(_raw(naf="71.12B", eff_min=5, eff_max=9)), f) == REJECT_EFFECTIF_TOO_SMALL

    def test_sans_dpt_toute_la_france(self):
        f = EligibilityFilters(naf_allowed=frozenset({"43.32B"}), min_effectif=5)
        assert is_eligible(parse_contact(_raw(dpt="75")), f) is None


def _lead(pid, source, naf, dpt, email, siren=None, raw=None):
    lead = Lead.objects.create(
        linkedin_url=f"https://bdd-prospect.local/siren/{pid}", public_identifier=pid,
        contact_email=email,
    )
    EmailLeadData.objects.create(
        lead=lead, source=source, siren=siren or pid[-9:], entreprise=pid.upper(),
        dirigeant=f"D {pid}", code_naf=naf, dpt=dpt, raw_json=raw or {},
    )
    return lead


def _sent(pid, days_ago=10, replied=False, intent="rdv_request"):
    PendingOutbound.objects.create(
        prospect_public_id=pid, kind=OutboundKind.EMAIL_COLD, status=OutboundStatus.SENT,
        ai_draft="x", sent_at=timezone.now() - timedelta(days=days_ago),
    )
    if replied:
        PendingReply.objects.create(
            prospect_public_id=pid, channel=PendingReply.CHANNEL_EMAIL, intent=intent,
            inbound_message="Bonjour, oui", ai_draft="",
        )


@pytest.mark.django_db
class TestYieldTable:
    def test_taux_par_segment_et_repli(self):
        for i in range(10):
            _lead(f"m{i}", "bdd_prospect", "43.32B", "42", f"m{i}@x.fr")
            _sent(f"m{i}", replied=(i < 4))          # 40 % de réponses
        for i in range(10):
            _lead(f"j{i}", "mailjet_hot", "", "", f"j{i}@y.fr")
            _sent(f"j{i}", replied=False)
        # une réponse hors sujet ne compte pas
        _lead("h1", "bdd_prospect", "43.32B", "42", "h1@x.fr")
        _sent("h1", replied=True, intent="off_topic")
        t = build_yield_table(days=90)
        assert t.sent[("bdd_prospect", "43.32B", "42")] == 11
        assert t.replies[("bdd_prospect", "43.32B", "42")] == 4
        assert t.score("bdd_prospect", "43.32B", "42") > t.score("mailjet_hot", "", "")
        # segment inconnu : repli sur le global, jamais d'exception
        assert 0 < t.score("decp", "25.11Z", "69") < 1

    def test_hors_fenetre_ignore(self):
        _lead("old", "bdd_prospect", "43.32B", "42", "old@x.fr")
        _sent("old", days_ago=200, replied=True)
        assert build_yield_table(days=90).sent[()] == 0


@pytest.mark.django_db
class TestTriDuVivier:
    def test_ordre_decp_puis_rendement_puis_reserve_mailjet(self, monkeypatch):
        monkeypatch.setenv("EKOALU_YIELD_RANKING", "1")
        # Historique : métalleries 42 répondent, BET 69 non
        for i in range(6):
            _lead(f"hm{i}", "bdd_prospect", "43.32B", "42", f"hm{i}@x.fr")
            _sent(f"hm{i}", replied=True)
            _lead(f"hb{i}", "bdd_prospect", "71.12B", "69", f"hb{i}@z.fr")
            _sent(f"hb{i}", replied=False)
        # Candidats (créés dans l'ordre inverse du rendement attendu)
        _lead("c-mailjet", "mailjet_hot", "", "", "c1@a.fr")
        _lead("c-bet", "bdd_prospect", "71.12B", "69", "c2@b.fr")
        _lead("c-metal", "bdd_prospect", "43.32B", "42", "c3@c.fr")
        _lead("c-decp", "decp", "43.32B", "69", "c4@d.fr", raw={"cible_prioritaire": True})
        order = [c.public_identifier for c, _ in [(x, 0) for x in cold_mail_candidates()[0]]]
        assert order == ["c-decp", "c-metal", "c-bet", "c-mailjet"]

    def test_kill_switch_revient_au_fifo(self, monkeypatch):
        monkeypatch.setenv("EKOALU_YIELD_RANKING", "0")
        _lead("f-mailjet", "mailjet_hot", "", "", "f1@a.fr")
        _lead("f-metal", "bdd_prospect", "43.32B", "42", "f2@c.fr")
        order = [c.public_identifier for c in cold_mail_candidates()[0]]
        assert order == ["f-mailjet", "f-metal"]

    def test_rank_key_stable_sans_email_data(self):
        table = build_yield_table(days=90)
        key = rank_key_factory(table)
        lead = Lead.objects.create(linkedin_url="https://x/in/a", public_identifier="a")
        assert key(lead)[0] is True and key(lead)[1] is False
