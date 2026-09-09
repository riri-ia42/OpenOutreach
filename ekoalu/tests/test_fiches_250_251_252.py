"""Fiches hub #250 (relance J+5 ouvrés), #251 (déjà en relation Outlook),
#252 (RDV Bookings rattachés). Aucun appel réseau : le Gateway est simulé."""
from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest
from django.utils import timezone

from crm.models import Lead
from ekoalu.email_canal import followup, relation_check, rdv
from ekoalu.email_canal.models import EmailLeadData, ProspectRdv
from ekoalu.inbox_assist.models import PendingReply
from ekoalu.outbound_validation.models import OutboundKind, OutboundStatus, PendingOutbound


def _lead(pid, email, source="bdd_prospect", naf="43.32B", dpt="42"):
    lead = Lead.objects.create(linkedin_url=f"https://bdd-prospect.local/siren/{pid}",
                               public_identifier=pid, contact_email=email)
    EmailLeadData.objects.create(lead=lead, source=source, siren=pid[-9:].zfill(9), entreprise=pid.upper(),
                                 dirigeant=f"D {pid}", code_naf=naf, dpt=dpt)
    return lead


def _cold(pid, days_ago, subject="Objet test"):
    return PendingOutbound.objects.create(
        prospect_public_id=pid, kind=OutboundKind.EMAIL_COLD, status=OutboundStatus.SENT,
        subject=subject, ai_draft="Bonjour, corps du cold mail.", prompt_variant="v2",
        sent_at=timezone.now() - dt.timedelta(days=days_ago),
    )


# ---------------------------------------------------------------------------
# #250 relance
# ---------------------------------------------------------------------------


class TestJoursOuvres:
    def test_week_end_et_ferie_exclus(self):
        # vendredi 04/09/2026 → vendredi 11/09/2026 = 5 jours ouvrés
        assert followup.working_days_between(dt.date(2026, 9, 4), dt.date(2026, 9, 11)) == 5
        # lundi 06/07 → lundi 20/07 : 14 juillet férié → 9 jours ouvrés
        assert followup.working_days_between(dt.date(2026, 7, 6), dt.date(2026, 7, 20)) == 9
        assert followup.working_days_between(dt.date(2026, 9, 9), dt.date(2026, 9, 9)) == 0


@pytest.mark.django_db
class TestEligibiliteRelance:
    def test_seulement_apres_5_ouvres_sans_reponse_et_une_seule_fois(self):
        _lead("bdd-prospect-1", "a@metal.fr"); _cold("bdd-prospect-1", days_ago=10)
        _lead("bdd-prospect-2", "b@metal.fr"); _cold("bdd-prospect-2", days_ago=2)      # trop tôt
        _lead("bdd-prospect-3", "c@metal.fr"); _cold("bdd-prospect-3", days_ago=10)
        PendingReply.objects.create(prospect_public_id="bdd-prospect-3", channel="email",
                                    intent="off_topic", inbound_message="x", ai_draft="")   # a répondu
        _lead("bdd-prospect-4", "d@metal.fr"); _cold("bdd-prospect-4", days_ago=10)
        PendingOutbound.objects.create(prospect_public_id="bdd-prospect-4", kind=OutboundKind.EMAIL_FOLLOW_UP,
                                       status=OutboundStatus.REJECTED, ai_draft="x")         # déjà relancé
        l5 = _lead("bdd-prospect-5", "e@metal.fr"); _cold("bdd-prospect-5", days_ago=10)
        l5.email_bounced_at = timezone.now(); l5.save()                                        # rebond
        eligible = followup.eligible_followups(today=timezone.localtime().date())
        assert [po.prospect_public_id for po, _ in eligible] == ["bdd-prospect-1"]

    def test_quota_40_pour_cent(self):
        with patch("ekoalu.email_canal.quota.cold_mail_quota_for", return_value=50):
            assert followup.followup_quota_for(dt.date(2026, 9, 10)) == 20

    def test_commande_cree_la_relance_liee(self):
        from django.core.management import call_command

        _lead("bdd-prospect-9", "z@metal.fr", naf="71.12B")
        cold = _cold("bdd-prospect-9", days_ago=12, subject="Menuiseries techniques")
        with patch("ekoalu.email_generator.followup_generator.generate_email_followup") as gen:
            from ekoalu.email_generator.followup_generator import FollowupDraft
            gen.return_value = FollowupDraft(body="Bonjour,\n\nOn fabrique aussi le standard.\n\nBien à vous,\nRichard")
            call_command("generate_email_followups", "--limit", "5")
        fu = PendingOutbound.objects.get(kind=OutboundKind.EMAIL_FOLLOW_UP)
        assert fu.parent_id == cold.pk and fu.subject == "Re: Menuiseries techniques"
        assert fu.status == OutboundStatus.PENDING and fu.prompt_variant == "relance_v1"
        # deuxième passage : plus rien d'éligible (jamais plus de 2 mails)
        assert followup.eligible_followups() == []

    def test_relance_comptee_dans_le_quota_du_jour(self):
        from ekoalu.email_canal.quota import cold_mails_sent_on

        PendingOutbound.objects.create(prospect_public_id="x", kind=OutboundKind.EMAIL_FOLLOW_UP,
                                       status=OutboundStatus.SENT, ai_draft="x", sent_at=timezone.now())
        assert cold_mails_sent_on(timezone.localtime().date()) == 1


@pytest.mark.django_db
class TestEnvoiDansLeFil:
    def test_reply_dans_le_fil_quand_le_cold_mail_est_retrouve(self):
        from ekoalu.email_canal import sender

        _lead("bdd-prospect-7", "g@metal.fr")
        cold = _cold("bdd-prospect-7", days_ago=8, subject="Objet initial")
        fu = PendingOutbound.objects.create(prospect_public_id="bdd-prospect-7", kind=OutboundKind.EMAIL_FOLLOW_UP,
                                            status=OutboundStatus.APPROVED, subject="Re: Objet initial",
                                            ai_draft="Bonjour,\n\nrelance.\n\nBien à vous,\nRichard", parent=cold)
        found = [{"id": "GRAPH-ID-1", "subject": "Objet initial",
                  "toRecipients": [{"emailAddress": {"address": "g@metal.fr"}}]}]
        with patch("ekoalu.notifications.outlook_gateway.search_messages", return_value=found), \
             patch("ekoalu.notifications.graph_mailer.send_reply") as reply, \
             patch("ekoalu.notifications.graph_mailer.send_mail") as plain, \
             patch("ekoalu.email_canal.sender._verify_before_send", return_value=None):
            ok, err = sender.send_cold_email(fu)
        assert ok and err == ""
        reply.assert_called_once()
        plain.assert_not_called()
        cold.refresh_from_db()
        assert cold.graph_message_id == "GRAPH-ID-1"

    def test_repli_envoi_classique_si_fil_introuvable(self):
        from ekoalu.email_canal import sender

        _lead("bdd-prospect-8", "h@metal.fr")
        cold = _cold("bdd-prospect-8", days_ago=8)
        fu = PendingOutbound.objects.create(prospect_public_id="bdd-prospect-8", kind=OutboundKind.EMAIL_FOLLOW_UP,
                                            status=OutboundStatus.APPROVED, subject="Re: Objet test",
                                            ai_draft="Bonjour,\n\nrelance.\n\nBien à vous,\nRichard", parent=cold)
        with patch("ekoalu.notifications.outlook_gateway.search_messages", return_value=[]), \
             patch("ekoalu.notifications.graph_mailer.send_reply") as reply, \
             patch("ekoalu.email_canal.sender.send_mail") as plain, \
             patch("ekoalu.email_canal.sender._verify_before_send", return_value=None):
            ok, _ = sender.send_cold_email(fu)
        assert ok
        reply.assert_not_called()
        plain.assert_called_once()


# ---------------------------------------------------------------------------
# #251 déjà en relation
# ---------------------------------------------------------------------------


def _msg(sender, to, subject, when="2026-08-01T10:00:00Z"):
    return {"from": {"emailAddress": {"address": sender}}, "subject": subject,
            "toRecipients": [{"emailAddress": {"address": to}}], "receivedDateTime": when}


class TestCheckRelation:
    def test_domaine_generique_jamais_interroge(self):
        with patch("ekoalu.notifications.outlook_gateway.search_messages", return_value=[]) as s:
            assert relation_check.check_relation("paul@gmail.com") is False
        assert s.call_count == 1          # adresse seule, pas de recherche domaine

    def test_domaine_pro_interroge_apres_l_adresse(self):
        with patch("ekoalu.notifications.outlook_gateway.search_messages",
                   side_effect=[[], [_msg("marie@sageco.fr", "richard@ekoalu.com", "Devis")]]) as s:
            hit = relation_check.check_relation("lionel@sageco.fr")
        assert hit and hit.matched == "domain" and hit.count == 1
        assert s.call_args_list[1].args[0] == "@sageco.fr"

    def test_nos_cold_mails_ne_comptent_pas(self):
        msgs = [_msg("richard@ekoalu.com", "lionel@sageco.fr", "EKOALU fabricant menuiseries")]
        with patch("ekoalu.notifications.outlook_gateway.search_messages", return_value=msgs):
            assert relation_check.check_relation("lionel@sageco.fr",
                                                 ignore_subjects={"EKOALU fabricant menuiseries"}) is False

    def test_tiers_en_copie_ou_envoi_groupe_ne_comptent_pas(self):
        tiers = _msg("rh@interim.fr", "lionel@sageco.fr", "Candidatures spontanées")
        tiers["toRecipients"].append({"emailAddress": {"address": "richard@ekoalu.com"}})
        groupe = _msg("richard@ekoalu.com", "lionel@sageco.fr", "Voeux 2026")
        groupe["toRecipients"] += [{"emailAddress": {"address": f"x{i}@y.fr"}} for i in range(6)]
        with patch("ekoalu.notifications.outlook_gateway.search_messages", return_value=[tiers, groupe]):
            assert relation_check.check_relation("lionel@sageco.fr") is False
        perso = _msg("richard@ekoalu.com", "lionel@sageco.fr", "Suite à notre échange")
        with patch("ekoalu.notifications.outlook_gateway.search_messages", return_value=[perso]):
            assert relation_check.check_relation("lionel@sageco.fr").matched == "email"

    def test_gateway_indisponible_renvoie_none(self):
        with patch("ekoalu.notifications.outlook_gateway.search_messages", return_value=None):
            assert relation_check.check_relation("lionel@sageco.fr") is None


@pytest.mark.django_db
class TestScreening:
    def test_lead_en_relation_ecarte_et_jamais_en_file(self):
        from ekoalu.email_canal.pool import cold_mail_candidates

        a = _lead("bdd-prospect-21", "a@acier.fr")
        b = _lead("bdd-prospect-22", "b@acier.fr")
        def fake(query, top=25, folder=None):
            return [_msg("a@acier.fr", "richard@ekoalu.com", "Re: chantier")] if query == "a@acier.fr" else []
        with patch("ekoalu.notifications.outlook_gateway.search_messages", side_effect=fake):
            res = relation_check.screen_candidates([a, b], limit=5)
        assert [x.public_identifier for x in res.kept] == ["bdd-prospect-22"] and res.removed == 1
        a.refresh_from_db(); a.email_data.refresh_from_db()
        assert a.disqualified and a.email_data.relation_existante["count"] == 1
        assert "bdd-prospect-21" not in [c.public_identifier for c in cold_mail_candidates()[0]]

    def test_gateway_en_panne_n_empeche_pas_la_generation(self):
        a = _lead("bdd-prospect-23", "c@acier.fr")
        with patch("ekoalu.notifications.outlook_gateway.search_messages", return_value=None):
            res = relation_check.screen_candidates([a], limit=5)
        assert res.kept == [a] and res.gateway_down


# ---------------------------------------------------------------------------
# #252 RDV
# ---------------------------------------------------------------------------

_BODY = ("Nouvelle réservation de GEAY Lionel société SAGE ECO Visio Decouverte avec Richard GROS "
         "mercredi 9 septembre 2026 11:00 - 11:30 Adresse de courrier : l.geay.eco@orange.fr")


@pytest.mark.django_db
class TestRdv:
    def _notice(self, mid="MSG-1", subject="Nouvelle réservation GEAY Lionel société SAGE ECO pour Visio Decouverte"):
        return rdv.parse_notice({"id": mid, "subject": subject, "receivedDateTime": "2026-08-31T14:01:39Z"}, _BODY)

    def test_parse_notification(self):
        n = self._notice()
        assert n.email == "l.geay.eco@orange.fr" and n.who == "GEAY Lionel société SAGE ECO"
        assert n.service == "Visio Decouverte" and n.start.day == 9 and n.start.hour == 11 and n.end.minute == 30
        assert not n.cancelled

    def test_rapprochement_par_adresse_et_idempotence(self):
        lead = _lead("bdd-prospect-410048607", "l.geay.eco@orange.fr")
        _cold("bdd-prospect-410048607", days_ago=9)
        r1, c1 = rdv.upsert_rdv(self._notice())
        r2, c2 = rdv.upsert_rdv(self._notice())
        assert c1 and not c2 and r1.pk == r2.pk
        assert r1.lead_id == lead.pk and r1.matched_by == "email" and r1.channel == "email"
        assert r1.cold_variant == "v2" and r1.status == ProspectRdv.Status.HELD   # date passée

    def test_rapprochement_par_domaine_nominatif(self):
        lead = _lead("bdd-prospect-77", "jean.durand@sageco.fr")
        body = _BODY.replace("l.geay.eco@orange.fr", "compta@sageco.fr")
        n = rdv.parse_notice({"id": "MSG-2", "subject": "Nouvelle réservation X pour Visio"}, body)
        r, _ = rdv.upsert_rdv(n)
        assert r.lead_id == lead.pk and r.matched_by == "domain"

    def test_annulation_et_sans_prospect(self):
        rdv.upsert_rdv(self._notice())
        cancel = self._notice(mid="MSG-3", subject="Réservation annulée GEAY Lionel pour Visio Decouverte")
        assert cancel.cancelled
        rdv.upsert_rdv(cancel)
        r = ProspectRdv.objects.get(event_id="MSG-1")
        assert r.status == ProspectRdv.Status.CANCELLED and r.lead is None

    def test_rapprochement_par_nom_quand_pas_d_adresse(self):
        lead = _lead("bdd-prospect-55", "contact@sageco.fr")
        lead.email_data.dirigeant = "Lionel Geay"; lead.email_data.entreprise = "SAGE ECO"; lead.email_data.save()
        body = _BODY.replace("Adresse de courrier : l.geay.eco@orange.fr", "")
        n = rdv.parse_notice({"id": "MSG-4", "subject": "Nouvelle réservation GEAY Lionel société SAGE ECO pour Visio"}, body)
        assert n.email == ""
        r, _ = rdv.upsert_rdv(n)
        assert r.lead_id == lead.pk and r.matched_by == "name"

