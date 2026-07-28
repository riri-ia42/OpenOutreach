"""Détection fabricant / revendeur.

Les tests de fiabilité portent sur la GRILLE : c'est elle qui porte le savoir
métier (remarque Richard 28/07 — un catalogue alu+PVC+bois est un marqueur de
négoce, et acheter des profilés à un gammiste ne disqualifie pas).
"""
from __future__ import annotations

import pytest

from ekoalu.fabricant_detect.classifier import (
    ClassifyInput,
    _is_uncertain,
    _parse_verdict,
    _request_params,
)
from ekoalu.fabricant_detect.fetch import (
    B2C_DOMAINS,
    SiteText,
    domain_from_email,
    html_to_text,
)
from ekoalu.fabricant_detect.prompts import (
    GAMMISTES,
    MARQUES_PRODUITS_FINIS,
    SYSTEM_PROMPT,
    VERDICT_SCHEMA,
    build_user_prompt,
)


class TestDomaine:
    @pytest.mark.parametrize("email,attendu", [
        ("contact@metallerie-durand.fr", "metallerie-durand.fr"),
        ("Contact@Metallerie-Durand.FR", "metallerie-durand.fr"),
        ("j.dupont@alu-services.com", "alu-services.com"),
    ])
    def test_domaine_pro_extrait(self, email, attendu):
        assert domain_from_email(email) == attendu

    @pytest.mark.parametrize("email", [
        "ets.denjean@wanadoo.fr", "contact@orange.fr", "x@gmail.com",
        "y@free.fr", "z@sfr.fr", "a@aliceadsl.fr",
    ])
    def test_domaine_b2c_ecarte(self, email):
        """Un email B2C = pas de site d'entreprise : inutile de scraper."""
        assert domain_from_email(email) == ""

    @pytest.mark.parametrize("email", ["", "pasunemail", None])
    def test_email_invalide(self, email):
        assert domain_from_email(email or "") == ""

    def test_b2c_couvre_les_fai_francais(self):
        for fai in ("wanadoo.fr", "orange.fr", "free.fr", "sfr.fr", "laposte.net"):
            assert fai in B2C_DOMAINS


class TestHtmlVersTexte:
    def test_scripts_et_styles_supprimes(self):
        html = "<html><head><style>p{color:red}</style></head><body>" \
               "<script>var x=1;</script><p>Notre atelier de Chasselay</p></body></html>"
        texte = html_to_text(html)
        assert "Notre atelier de Chasselay" in texte
        assert "color:red" not in texte
        assert "var x" not in texte

    def test_entites_html_decodees(self):
        assert "fabriqué" in html_to_text("<p>fabriqu&eacute;</p>")
        assert "&" in html_to_text("<p>alu &amp; PVC</p>")

    def test_balises_de_bloc_deviennent_des_sauts_de_ligne(self):
        texte = html_to_text("<li>alu</li><li>PVC</li><li>bois</li>")
        assert texte.count("\n") >= 2

    def test_html_vide(self):
        assert html_to_text("") == ""


class TestSiteText:
    def test_texte_trop_court_inexploitable(self):
        """Un site vide ne doit jamais produire un verdict."""
        assert not SiteText(domain="x.fr", text="Bienvenue").usable

    def test_texte_suffisant_exploitable(self):
        assert SiteText(domain="x.fr", text="a" * 400).usable


class TestGrille:
    """La grille est le livrable métier — ces assertions la verrouillent."""

    def test_piege_multi_materiaux_encode(self):
        """Le point de Richard : alu+PVC+bois = marqueur de NÉGOCE."""
        assert "alu ET PVC ET bois" in SYSTEM_PROMPT
        assert "revendeur_poseur" in SYSTEM_PROMPT
        # La justification doit être présente, pas seulement la règle
        assert "soudeuse PVC" in SYSTEM_PROMPT

    def test_acheter_des_profiles_ne_disqualifie_pas(self):
        """EKOALU achète ses profilés et fabrique : la grille doit le dire."""
        assert "ne disqualifie PAS" in SYSTEM_PROMPT
        for gammiste in ("Cortizo", "Sepalumic", "SAPA", "Wicona"):
            assert gammiste in SYSTEM_PROMPT

    def test_discriminant_transformation_vs_pose(self):
        assert "ASSEMBLENT" in SYSTEM_PROMPT
        assert "POSENT" in SYSTEM_PROMPT

    def test_gammistes_et_marques_finies_sont_distincts(self):
        """Confondre les deux listes casserait tout le raisonnement."""
        assert not set(GAMMISTES) & set(MARQUES_PRODUITS_FINIS)
        assert "Cortizo" in GAMMISTES
        assert "K-Line" in MARQUES_PRODUITS_FINIS

    def test_doute_mene_a_indetermine(self):
        assert "Dans le doute" in SYSTEM_PROMPT
        assert "indetermine" in SYSTEM_PROMPT

    def test_fabrication_sur_mesure_seule_ne_suffit_pas(self):
        """Piège connu : les revendeurs disent tous « sur mesure »."""
        assert "sur mesure" in SYSTEM_PROMPT
        assert "ne suffit pas" in SYSTEM_PROMPT

    def test_pose_ne_disqualifie_pas(self):
        assert "la pose ne disqualifie pas" in SYSTEM_PROMPT


class TestSchema:
    def test_schema_strict(self):
        """Structured outputs exige additionalProperties=false + required."""
        assert VERDICT_SCHEMA["additionalProperties"] is False
        assert set(VERDICT_SCHEMA["required"]) == set(VERDICT_SCHEMA["properties"])

    def test_verdicts_possibles(self):
        assert VERDICT_SCHEMA["properties"]["verdict"]["enum"] == [
            "fabricant", "revendeur_poseur", "indetermine",
        ]

    def test_confiance_bornee(self):
        assert VERDICT_SCHEMA["properties"]["confiance"]["enum"] == [
            "haute", "moyenne", "basse",
        ]


class TestRequete:
    def _item(self):
        return ClassifyInput(
            siren="123456789", entreprise="METALLERIE DURAND",
            code_naf="43.32B", ville="LYON",
            url="https://metallerie-durand.fr", text="Notre atelier de 1200 m²…",
        )

    def test_params_batch_bien_formes(self):
        params = _request_params(self._item(), "claude-haiku-4-5")
        assert params["model"] == "claude-haiku-4-5"
        assert params["system"] == SYSTEM_PROMPT
        assert params["output_config"]["format"]["type"] == "json_schema"
        assert params["messages"][0]["role"] == "user"

    def test_fiche_societe_dans_le_prompt(self):
        prompt = build_user_prompt(
            "METALLERIE DURAND", "43.32B", "LYON",
            "https://x.fr", "Notre atelier",
        )
        assert "METALLERIE DURAND" in prompt
        assert "43.32B" in prompt
        assert "Notre atelier" in prompt

    def test_entreprise_inconnue_ne_casse_pas(self):
        assert "(inconnue)" in build_user_prompt("", "", "", "https://x.fr", "texte")


class TestEscalade:
    @pytest.mark.parametrize("verdict,attendu", [
        ({"verdict": "indetermine", "confiance": "haute"}, True),
        ({"verdict": "fabricant", "confiance": "basse"}, True),
        ({"verdict": "revendeur_poseur", "confiance": "basse"}, True),
        ({"verdict": "fabricant", "confiance": "haute"}, False),
        ({"verdict": "revendeur_poseur", "confiance": "moyenne"}, False),
    ])
    def test_cas_a_escalader(self, verdict, attendu):
        """On escalade sur indétermination OU sur conviction faible."""
        assert _is_uncertain(verdict) is attendu


class TestParse:
    def test_verdict_valide(self):
        parsed = _parse_verdict('{"verdict": "fabricant", "confiance": "haute"}')
        assert parsed["verdict"] == "fabricant"

    @pytest.mark.parametrize("brut", ["", "pas du json", "{}", '{"autre": 1}', "[]"])
    def test_reponse_inexploitable_rejetee(self, brut):
        """Une réponse illisible ne doit jamais produire un faux verdict."""
        assert _parse_verdict(brut) is None


@pytest.mark.django_db
class TestModele:
    def test_fabricant_avere_exige_de_la_conviction(self):
        from ekoalu.fabricant_detect.models import FabricantVerdict
        sur = FabricantVerdict.objects.create(
            siren="111", verdict="fabricant", confiance="haute")
        doute = FabricantVerdict.objects.create(
            siren="222", verdict="fabricant", confiance="basse")
        revendeur = FabricantVerdict.objects.create(
            siren="333", verdict="revendeur_poseur", confiance="haute")
        assert sur.is_fabricant is True
        assert doute.is_fabricant is False, "un verdict peu sûr ne vaut pas confirmation"
        assert revendeur.is_fabricant is False

    def test_siren_unique(self):
        from django.db.utils import IntegrityError

        from ekoalu.fabricant_detect.models import FabricantVerdict
        FabricantVerdict.objects.create(siren="999", verdict="fabricant")
        with pytest.raises(IntegrityError):
            FabricantVerdict.objects.create(siren="999", verdict="revendeur_poseur")


class TestTarifs:
    """La table de tarifs surestimait Opus d'un facteur 3 (héritage Opus 3)."""

    def test_opus_4_x_corrige(self):
        from ekoalu.llm_usage.pricing import get_pricing
        for model in ("claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6"):
            assert get_pricing(model) == (5.0, 25.0), model

    def test_haiku_et_sonnet(self):
        from ekoalu.llm_usage.pricing import get_pricing
        assert get_pricing("claude-haiku-4-5") == (1.0, 5.0)
        assert get_pricing("claude-sonnet-4-6") == (3.0, 15.0)

    def test_famille_5_connue(self):
        from ekoalu.llm_usage.pricing import get_pricing
        assert get_pricing("claude-opus-5") == (5.0, 25.0)
        assert get_pricing("claude-sonnet-5") == (3.0, 15.0)

    def test_remise_batch(self):
        from ekoalu.llm_usage.pricing import BATCH_DISCOUNT
        assert BATCH_DISCOUNT == 0.5


class TestPiegesObserves:
    """Cas réels rencontrés au dry-run du 28/07 — verrouillés pour ne pas régresser."""

    def test_site_sans_rapport_doit_etre_detecte(self):
        """`SAS CLAUDE LAUMOND` → le-galetas.com (résidence d'écrivains finlandaise).
        Le domaine de l'email ne pointe pas toujours sur le site de la société."""
        assert "pointer ailleurs" in SYSTEM_PROMPT
        assert "site de quelqu'un d'autre" in SYSTEM_PROMPT

    def test_activite_hors_menuiserie_non_classee_revendeur(self):
        """`PARQUETSOL` fait du parquet : ni fabricant menuiserie, ni revendeur menuiserie."""
        assert "hors menuiserie" in SYSTEM_PROMPT
        assert "parquet" in SYSTEM_PROMPT


class TestScrapingParallele:
    """Le scraping est le goulot (pas le LLM) : 242 sites en sequentiel = >1h."""

    def test_domaines_dedupliques(self, monkeypatch):
        from ekoalu.fabricant_detect import fetch as fetch_mod
        appels = []

        def faux_fetch(domain):
            appels.append(domain)
            return SiteText(domain=domain, text="a" * 400)

        monkeypatch.setattr(fetch_mod, "fetch_site_text", faux_fetch)
        res = fetch_mod.fetch_many(["a.fr", "b.fr", "a.fr", ""], workers=4)
        assert sorted(appels) == ["a.fr", "b.fr"], "un domaine repete ne doit etre sonde qu'une fois"
        assert set(res) == {"a.fr", "b.fr"}

    def test_liste_vide(self):
        from ekoalu.fabricant_detect.fetch import fetch_many
        assert fetch_many([]) == {}
        assert fetch_many(["", ""]) == {}

    def test_un_site_qui_casse_ne_tue_pas_le_lot(self, monkeypatch):
        """Un domaine injoignable ne doit pas faire tomber les 241 autres."""
        from ekoalu.fabricant_detect import fetch as fetch_mod

        def faux_fetch(domain):
            if domain == "casse.fr":
                return SiteText(domain=domain, error="site injoignable")
            return SiteText(domain=domain, text="a" * 400)

        monkeypatch.setattr(fetch_mod, "fetch_site_text", faux_fetch)
        res = fetch_mod.fetch_many(["ok.fr", "casse.fr", "ok2.fr"], workers=3)
        assert len(res) == 3
        assert res["ok.fr"].usable and res["ok2.fr"].usable
        assert not res["casse.fr"].usable


@pytest.mark.django_db
class TestTracabiliteBatch:
    """Le tracker global patche messages.create, PAS messages.batches :
    sans ce log, une passe batch serait invisible du garde-fou budgetaire."""

    def test_conso_batch_tracee_avec_remise(self):
        from ekoalu.fabricant_detect.classifier import _log_batch_usage
        from ekoalu.llm_usage.models import ClaudeUsageLog

        _log_batch_usage("claude-haiku-4-5", 1_000_000, 200_000)
        row = ClaudeUsageLog.objects.get(context="fabricant_detect_batch")
        assert row.input_tokens == 1_000_000
        # (1M x 1$) + (0.2M x 5$) = 2$ plein tarif -> 1$ en batch
        assert float(row.cost_usd) == pytest.approx(1.0)

    def test_conso_nulle_non_tracee(self):
        from ekoalu.fabricant_detect.classifier import _log_batch_usage
        from ekoalu.llm_usage.models import ClaudeUsageLog

        _log_batch_usage("claude-haiku-4-5", 0, 0)
        assert not ClaudeUsageLog.objects.filter(context="fabricant_detect_batch").exists()


class TestGardeFouEscalade:
    """Passe du 28/07 : 117 appels Sonnet, 1,73 $, ZERO verdict ameliore.
    100 des 107 indetermines etaient des societes hors metier — changer de
    modele ne transforme pas un electricien en menuisier."""

    def test_hors_metier_non_escalade(self):
        from ekoalu.fabricant_detect.classifier import _should_escalate
        hors_metier = {"verdict": "indetermine", "confiance": "basse", "materiaux": []}
        assert _should_escalate(hors_metier) is False

    def test_vraie_ambiguite_escaladee(self):
        from ekoalu.fabricant_detect.classifier import _should_escalate
        ambigu = {"verdict": "indetermine", "confiance": "basse",
                  "materiaux": ["alu", "pvc", "bois"]}
        assert _should_escalate(ambigu) is True

    def test_verdict_conclusif_jamais_escalade(self):
        from ekoalu.fabricant_detect.classifier import _should_escalate
        sur = {"verdict": "fabricant", "confiance": "haute", "materiaux": ["alu"]}
        assert _should_escalate(sur) is False

    def test_faible_conviction_avec_materiaux_escaladee(self):
        from ekoalu.fabricant_detect.classifier import _should_escalate
        assert _should_escalate(
            {"verdict": "fabricant", "confiance": "basse", "materiaux": ["acier"]}) is True

    def test_cas_reels_de_la_passe(self):
        """Repris tels quels de la passe du 28/07."""
        from ekoalu.fabricant_detect.classifier import _should_escalate
        # ETCHART ENERGIES : genie electrique -> aucun materiau
        assert not _should_escalate(
            {"verdict": "indetermine", "confiance": "basse", "materiaux": []})
        # MENUISERIE DU FOREZ : bois+alu+pvc, atelier non mentionne -> a trancher
        assert _should_escalate(
            {"verdict": "indetermine", "confiance": "moyenne",
             "materiaux": ["bois", "alu", "pvc"]})


class TestRegleMultiMateriauxImperative:
    """La regle etait enoncee mais pas appliquee : MENUISERIE DU FOREZ est
    ressortie `indetermine` alors que le modele constatait lui-meme
    « trois materiaux sans aucune mention d'atelier »."""

    def test_regle_marquee_imperative(self):
        assert "RÈGLE IMPÉRATIVE" in SYSTEM_PROMPT

    def test_indetermine_explicitement_interdit_dans_ce_cas(self):
        assert "ne réponds PAS `indetermine`" in SYSTEM_PROMPT

    def test_seuil_generalise_a_trois_materiaux(self):
        assert "trois matériaux ou plus" in SYSTEM_PROMPT
