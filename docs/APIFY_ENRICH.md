# Enrichissement Apify cookieless (câblé dans le pipeline le 07/07)

## Pourquoi

Aujourd'hui chaque lead sourcé (Serper) doit être **lu sur LinkedIn avec le
compte de Richard** : 1 lecture du cap quotidien (60-75/j), et c'est ce type
de lectures qui a déclenché le checkpoint du 06/06. Les acteurs Apify
« cookieless » scrapent un profil LinkedIn **public** sans cookie de session
(~8 $/1000 profils) : on pourrait enrichir/qualifier sans toucher au compte,
qui ne servirait plus qu'à **engager** (invitations, messages).

**RÈGLE ABSOLUE : ne JAMAIS transmettre notre cookie/session LinkedIn
(`li_at`…) à Apify — uniquement des URLs de profils publics.** Le client
(`ekoalu/apify_enrich/client.py`) ne construit que `{"profileUrls": [...]}`
et un test verrouille l'absence de toute clé cookie/session.

## Ce qui existe (squelette)

| Fichier | Rôle |
|---|---|
| `ekoalu/apify_enrich/client.py` | Run synchrone d'un acteur (API v2 `run-sync-get-dataset-items`, lib `requests`) |
| `ekoalu/apify_enrich/mapper.py` | JSON acteur → format `profile_snapshot` interne (défensif, champs absents = `None`, `source: "apify"`) |
| `manage.py test_apify_enrich` | Test à blanc : affiche les snapshots mappés + coût estimé, **zéro écriture DB** |

**PAS de câblage dans le pipeline daemon** : la décision viendra après le
test réel 10-20 profils.

## À créer côté Richard (une fois, ~10 min)

1. **Compte Apify** : https://console.apify.com/sign-up (email pro OK).
   Le plan Free inclut ~5 $ de crédit/mois — suffisant pour le test.
2. **Crédit** : si besoin au-delà du free tier, charger 5-10 $
   (Billing > Add funds). Le test 10-20 profils coûte ~0,10-0,20 $.
3. **Token** : Console Apify > Settings > **API & Integrations** > copier le
   *Personal API token*.
4. Renseigner dans `.env.production` (à la main, jamais via script) :
   ```
   EKOALU_APIFY_TOKEN=apify_api_xxxxxxxx
   # optionnel — défaut : harvestapi~linkedin-profile-scraper
   EKOALU_APIFY_ACTOR=harvestapi~linkedin-profile-scraper
   ```
   Acteur par défaut : **HarvestAPI** (cookieless, 4 $/1000 en mode
   « no email », accepte l'API sur le plan Free). `dev_fusion` a été écarté
   au test réel du 07/07 : il refuse les runs API sur le plan Free
   (« run through the UI only »).

## Test réel (10-20 profils)

```powershell
# 1. À blanc (aucun appel, aucun coût) : voir ce qui serait envoyé
.venv\Scripts\python.exe manage.py test_apify_enrich --from-serper 15 --dry-run

# 2. Réel : appelle l'acteur, affiche les snapshots mappés (zéro écriture DB)
.venv\Scripts\python.exe manage.py test_apify_enrich --from-serper 15

# Variante : URLs choisies à la main
.venv\Scripts\python.exe manage.py test_apify_enrich --urls "https://www.linkedin.com/in/xxx/,https://www.linkedin.com/in/yyy/"
```

`--from-serper N` prend les N leads URL-only les plus récents (sans snapshot
ni embedding, URL LinkedIn réelle — les leads mail-only `bdd-prospect.local`
sont exclus). Lecture seule de la DB.

## Critères GO / NO-GO (après le run réel)

1. **Complétude vs snapshot Voyager** : la commande affiche `complétude X/6`
   par profil (full_name, headline, summary, location_name,
   public_identifier, positions). GO si ≥ 5/6 sur la majorité des profils —
   ce sont les champs qui nourrissent le verdict LLM et la génération de
   messages. Vérifier aussi que `positions[0].company_name` est fiable.
2. **Coût réel** : relever la facture Apify du run (Console > Billing) et
   comparer à l'estimation ~0,008 $/profil. GO si ≤ ~0,015 $/profil
   (≈ 1,2 $/mois pour 30 leads/j — négligeable vs le risque compte).
3. **Latence** : GO si le run 15 profils tient dans le timeout (300 s) ;
   noter la durée pour dimensionner un éventuel batch quotidien.
4. **Taux d'échec** : profils privés/introuvables renvoyés vides — GO si
   ≥ 80 % des URLs Serper ressortent exploitables.

Au premier run réel : **valider chaque clé du mapper** (commentaires
« à confirmer au test réel » dans `mapper.py`) contre le JSON effectivement
renvoyé, puis décider du câblage (remplacer la lecture Voyager d'embed/
qualification par Apify, le compte Richard ne servant plus qu'à engager).

## ✅ Résultat du test réel (2026-07-07) — GO technique

- **Acteur retenu : `harvestapi~linkedin-profile-scraper`** (mode
  « Profile details no email ($4 per 1k) »).
- **15/15 profils Serper récupérés**, complétude **5/6 ou 6/6** partout
  (seul `summary` manque quand le profil n'a pas de section « À propos » —
  pas un défaut de mapping). Poste + entreprise + localisation fiables.
- **Coût réel : ~0,004 $/profil** (0,06 $ le run de 15). Latence : OK par
  lots de 5 (`client.BATCH_SIZE` — l'endpoint run-sync plafonne à ~300 s,
  15 URLs d'un coup rendent un dataset tronqué).
- Clés mapper validées contre le JSON réel : `publicIdentifier`,
  `linkedinUrl`, `firstName/lastName`, `headline`, `about`,
  `location.linkedinText`, `experience[].position/companyName`,
  `profileTopEducation[]`.
- **Étape suivante (décision à part)** : câbler dans le pipeline —
  remplacer la lecture Voyager de l'embed/qualification par Apify pour les
  leads sourcés ; le compte LinkedIn de Richard ne servirait plus qu'à
  engager (invitations/messages) et vérifier les degrés.

## ✅ Câblé le 07/07 : comment ça marche

**GO Richard** : les lectures de fiche pour l'**embed/qualification** des
leads sourcés passent par Apify (cookieless, zéro empreinte sur le compte).
Le compte LinkedIn ne sert plus qu'à **engager** : visites pré-invitation,
invitations, messages, degrés de connexion, sondes check_pending — ces
chemins n'ont pas changé.

### Les deux chemins câblés

| Chemin | Fichier | Comportement |
|---|---|---|
| **Backlog (tâche planifiée)** | `manage.py apify_enrich_backlog [--max N] [--dry-run]` via `scripts/apify_enrich.ps1` (repo parent, log `data/apify_enrich.log`) | Enrichit les leads URL-only en attente (snapshot + embedding), plus anciens d'abord |
| **Daemon (Apify-first)** | `linkedin/pipeline/qualify.py:_embed_urlonly_leads` | À chaque cycle de qualification, les N leads URL-only sont enrichis via Apify au lieu de `Lead.get_embedding` (lecture Voyager) |

Service commun : `ekoalu/apify_enrich/service.py` —
`enrich_urlonly_leads(max_leads)` (lot) et `enrich_lead(lead)` (unitaire).
Candidats : pas de snapshot, pas d'embedding, non disqualifié, URL
`linkedin.com/in/` réelle (mail-only `bdd-prospect.local` exclus), découvert
(`LeadDiscovery`) par une campagne **active**. Le snapshot stocké porte
`source: "apify"` + `fetched_at` (isoformat).

### Plafond quotidien

- Env `EKOALU_APIFY_DAILY_CAP` (défaut **40** profils/jour ≈ 0,16 $/j).
- Compteur DB `ApifyUsageDay` (migration ekoalu 0025), une ligne par jour,
  on compte les **tentatives** avant l'appel réseau (patron `ProfileReadDay`).
- Plafond atteint → le service s'arrête proprement, le daemon replie sur
  Voyager. Reset naturel à minuit.
- **Étanche au read_guard** : un fetch Apify n'incrémente JAMAIS le cap
  lectures LinkedIn (il ne passe pas par `get_profile` patché).

### Kill-switch

`EKOALU_APIFY_ENRICH=0` → service inactif (log info) : la commande ne fait
rien, le daemon reprend le chemin Voyager historique. Token absent
(`EKOALU_APIFY_TOKEN`) = même effet (comportement d'origine inchangé).

### Repli Voyager

Tout échec Apify (réseau, erreur acteur, profil privé/introuvable, item sans
`publicIdentifier`) laisse le lead **intact** (compté en échec, log warning) :
le chemin Voyager du daemon le rattrape au cycle suivant (1 lecture compte,
cadence LOT C 20-45 s conservée entre deux lectures Voyager).
