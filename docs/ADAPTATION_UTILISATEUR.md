# Adapter les applications à leur utilisateur — étude de cadrage

*23/09/2026 — étape 1 : cartographie des utilisateurs, puis mode de captation et architecture cible. Aucune ligne de code n'est encore écrite.*

---

## 0. Synthèse (lecture rapide)

1. **Écarter le modèle « visuel / auditif / kinesthésique » (VAK) comme base de classement.** L'hypothèse selon laquelle on apprend mieux quand le format correspond à son « style » n'a jamais été validée (Pashler et al., 2008 ; méta-analyses ultérieures). Classer un utilisateur « visuel » produirait une adaptation qui n'améliore rien de mesurable. Ce qui est établi et exploitable :
   - la **nature du contenu** dicte le format : une élévation de porte se lit en schéma, un écart budgétaire en tableau, une consigne en texte ;
   - le **niveau d'expertise** : un expert lit plus vite quand on retire l'explication, un novice a besoin de l'étape par étape (effet d'inversion d'expertise) ;
   - le **contexte d'usage** : poste de travail, mobile, réunion, validation en rafale ;
   - les **préférences déclarées et observées** de format et de densité, qui influent sur l'adoption même quand elles n'influent pas sur la compréhension.
2. **14 applications sur 16 n'ont qu'un seul utilisateur : Richard.** Pour elles, le sujet n'est pas de typer des personnes différentes. Il s'agit de décrire **un utilisateur dans plusieurs situations** (chiffreur, validateur, dirigeant en réunion, lecteur du matin). Seules trois applications ont de vraies populations à profiler : Pleine Lune, observatoire-ia et vinriricaviste.
3. **Le profil de Richard existe déjà en partie, mais il est dispersé :**
   - le jumeau numérique décrit la manière d'**écrire** en son nom ;
   - les consignes, les règles apprises et les commentaires captureIA relèvent ses **corrections** ;
   - ses préférences personnelles de restitution (tableaux, conclusion d'abord, pas de ton marketing) sont une **déclaration** complète, mais aucune application ne la lit.

   Il manque le **profil de restitution** : comment Richard veut **recevoir** l'information.
4. **Recommandation :** un profil de restitution unique, versionné, servi par le hub (`GET /api/profile`), avec des valeurs par défaut si le hub ne répond pas. Il serait consommé de deux façons :
   - par les prompts, via un bloc injecté ;
   - par les interfaces, via le format affiché par défaut.

   Il serait alimenté d'abord par la déclaration, puis par les signaux déjà captés. Mise en place en 3 lots, dont le premier coûte environ 2 jours.

---

## 1. Cartographie des applications et des utilisateurs

### 1.1 Inventaire (16 dépôts)

| Application | Utilisateurs | Interface | Formats produits | Signaux de préférence existants | Prompt LLM injectable |
|---|---|---|---|---|---|
| **chiffrage-gti** | Richard en chiffreur ; l'atelier lit les sorties | Web (Next.js), chat RAG, scripts | Excel, tableaux de coût, schéma SVG, texte RAG | `devis.coef_vente`, statut, surcharges ; corrections notées à la main | `app/api/rag/chat/route.ts` |
| **prospection-ia / OpenOutreach** | Richard en validateur | Web Django, mails, hub | DM, mails, graphiques, brief et deck de RDV | **Les plus riches** : `CorrectionExample`, `PendingReply` (écart entre brouillon IA et envoi), `QualificationFeedback`, `UndoEntry`, `ActionLog` | `learning.py`, `rdv_prep/writer.py`, générateurs |
| **mail-assistant** | Richard | Balises mail, VBA Outlook | Brouillons, Notion, rapport hebdo | `processed_emails` (commandes utilisées), `rdv_proposals` (créneau choisi) ; brouillons retouchés **non captés** | `jumeau-prompt.ts`, `weekly-report.ts` |
| **extraction-plaud** | Richard | CLI planifiée, mails | Comptes rendus, récap du matin, Notion | `draft_skip.txt`, `extra_instruction` ; retouches **non captées** | `draft_mail_cr.py`, `daily_recap.py` |
| **EKOALU-dashboard** | Richard en réunion de direction ; commerciaux en sujets | Streamlit | Graphiques, KPI, tableaux | Filtres de session uniquement | aucun |
| **news-ia** | Richard en lecteur | Mail du matin | HTML | Aucun | prompt amont (Cowork) |
| **cor70-indus / orgadata / thermique-profile-alu** | Richard en chiffreur ; partenaires destinataires | Scripts, Excel, web prévu | XLSX, DXF, CSV, rapports | PROGRESS / DECISIONS en texte libre | RAG prévu |
| **captureIA** | Richard, sur tous les projets | Capture Windows | Texte injecté dans Claude | **Commentaires de captures** : le seul flux de retours transverse | `inject-captures.ps1` (préambule commun) |
| **mailing-mailjet** | Richard ; destinataires B2B | CLI | Campagnes HTML | Règle « voir les données avant d'agir » | aucun |
| **observatoire-ia** | **Externe, plusieurs utilisateurs** (métalliers, FFB) | Web React, JWT | Fiches, bibliothèque de prompts | `users.metier / company_size / region`, likes, téléchargements, `audit_log` | aucun (chatbot retiré) |
| **pleine-lune + admin** | **Grand public** (lecteurs) + libraires | Application mobile, back-office | Cartes livre, avis, défis | `user_books`, `reviews`, `feature_votes`, événements | modération seulement |
| **vinriricaviste** | Amis (sans compte), Richard administrateur | Formulaire web | Formulaire, CSV, messages | `vin_responses` | aucun |

**Deux points non résolus :**
- **« LMAI »** : aucun dépôt ne porte ce nom ni ce terme. À préciser : s'agit-il de mail-assistant, d'observatoire-ia, ou d'un projet non encore sur GitHub ?
- **hub-ekoalu** : le dépôt n'est pas accessible depuis cette session.

### 1.2 Typologie : 5 situations et 3 populations

Le classement retenu n'est pas « visuel ou auditif » mais **situation × population**.

**A. Situations de Richard** (un seul individu, cinq contextes)

| Situation | Applications | Contrainte dominante | Format optimal (hypothèse à valider) |
|---|---|---|---|
| **S1 Chiffreur** | chiffrage-gti, cor70, orgadata, thermique | Exactitude ; objets spatiaux | Schéma et tableau chiffré ; hypothèses explicites ; texte minimal |
| **S2 Validateur en rafale** | file `/ekoalu/messages/`, brouillons mail-assistant et Plaud | Débit (des centaines d'items) | Liste dense, écart mis en évidence, action en un clic, annulation plutôt que confirmation (déjà adopté le 11/09) |
| **S3 Dirigeant en pilotage** | EKOALU-dashboard, daily_recap, conformité, hub | Décider vite | Verdict d'abord, puis 3 chiffres, puis le détail sur demande ; graphique avec objectif |
| **S4 Lecteur du matin** | news-ia, récap Plaud, récap prospection | Temps court, souvent sur mobile | 5 à 10 lignes, lien vers le détail |
| **S5 Préparation de RDV** | rdv_prep (brief et deck) | Mémorisation avant l'échange | Une page avec l'essentiel en tête, puis les questions ; lisible sur mobile |

**B. Populations externes**

| Population | Application | Hétérogénéité | Enjeu |
|---|---|---|---|
| **P1 Lecteurs** (grand public) | Pleine Lune | Forte : âge, goûts, rythme | Recommandation, onboarding, notifications |
| **P2 Professionnels du métal** | observatoire-ia | Moyenne : métier, taille, maturité IA | Niveau de détail des cas, prompts adaptés au métier |
| **P3 Destinataires** (prospects, amis, partenaires) | prospection, vinriricaviste, cor70 | Déjà traitée par les personas prospects et le jumeau | Hors périmètre : on ne profile pas un destinataire sur sa façon de consommer l'information |

**Implication :** pour S1 à S5, l'adaptation porte sur la **situation** ; le profil personnel ne sert qu'à régler les curseurs. Pour P1 et P2, le profil est individuel, et le RGPD s'applique pleinement (voir §4).

### 1.3 Dimensions qui valent d'être mesurées

| Dimension | Valeurs | Mesurable par | Levier dans l'application |
|---|---|---|---|
| Format principal | tableau / schéma / texte / graphique | déclaration + bascule de vue | vue par défaut |
| Densité | synthèse / standard / expert | déclaration + taux de dépliage du détail | longueur, `max_tokens`, sections repliées |
| Ordre | conclusion d'abord / chronologique | déclaration | gabarit de prompt |
| Niveau d'expertise par domaine | novice → expert | déclaration + vocabulaire des corrections | explications retirées ou ajoutées |
| Canal | écran / mail / audio / mobile | usage observé (heure, appareil) | choix du canal de restitution |
| Autonomie | valider chaque action / valider en lot / agir puis annuler | usage de l'annulation, validations en masse | confirmation ou annulation |
| Ton | sobre / pédagogique | déclaration | bloc de style |

L'**auditif** ne figure pas dans ce tableau comme un trait de personnalité : c'est un **canal**, pertinent en situation (voiture, atelier). Le levier réel est de proposer une version audio du récap du matin, pas d'étiqueter une personne « auditive ».

---

## 2. Comment faire remonter l'information

| Voie | Coût | Fiabilité | Délai | Usage recommandé |
|---|---|---|---|---|
| **Déclaration** : questionnaire court | Faible | Bonne sur les préférences, mauvaise sur les comportements réels | Immédiat | Amorçage (v0 du profil) |
| **Observation** : signaux implicites | Moyen (instrumentation) | Bonne sur le comportement | 2 à 6 semaines | Corriger la déclaration |
| **Inférence** : LLM sur les corrections et les captures | Faible (données déjà là) | Moyenne, à faire valider | Immédiat | Proposer des règles, validées par l'humain |
| Test A/B par utilisateur | Élevé | Très bonne | Il faut du volume | Pleine Lune seulement |

### 2.1 Questionnaire : 8 questions, choix forcés sur des exemples réels

Principe : on ne demande pas « êtes-vous visuel ? ». On montre **deux rendus du même contenu** et on demande lequel est le plus utile. Exemples de paires :

1. Récap du matin : tableau de 5 lignes, ou 3 phrases.
2. Devis : schéma coté accompagné de la liste de débit, ou la liste seule.
3. Écart budgétaire : graphique avec objectif, ou tableau des écarts.
4. Brouillon de mail : afficher l'écart avec la version précédente, ou le texte complet.
5. Densité : version 5 lignes, ou version 30 lignes.
6. Ordre : conclusion puis arguments, ou analyse puis conclusion.
7. Action risquée : confirmation préalable, ou annulation possible après coup.
8. Canal pour une alerte non urgente : mail, notification du hub, ou résumé audio.

Durée : moins de 2 minutes. Les réponses forment la v0 du profil.

### 2.2 Signaux déjà captés, ou à capter pour un coût faible

| Signal | Où | Ce qu'il révèle | État |
|---|---|---|---|
| Écart entre brouillon IA et texte envoyé | `PendingReply` (prospection) | Style, longueur, ton | **Capté** |
| Consignes de régénération | `CorrectionExample` | Préférences de fond et de forme | **Capté**, promu en règles à partir de 3 occurrences |
| Usage de l'annulation, actions en masse | `UndoEntry` | Autonomie | **Capté** |
| Commentaires captureIA | `captures/*.md` sur tous les projets | Irritants d'interface | **Capté mais non structuré** : c'est la source la plus riche et elle n'est exploitée par rien |
| Écart entre brouillon et envoi | mail-assistant `/r`, Plaud comptes rendus | Style | **Manquant** (lecture des Éléments envoyés via le Gateway) |
| Bascule tableau / graphique, dépliage du détail | dashboards | Format, densité | **Manquant** (un événement JS vers le hub) |
| Ouverture et lecture des récaps | news-ia, récaps | Canal, horaire | **Manquant** (pixel ou lien de suivi interne) |

### 2.3 Inférence encadrée

Une tâche hebdomadaire, sur le modèle de `learner_weekly`, lit les corrections, les captures et les écarts de la semaine. Elle **propose** des modifications du profil sous forme de fiches du hub ; Richard les valide ou les refuse. **Le profil n'est jamais réécrit sans validation.** C'est le même principe que les règles apprises et les variantes de prompt déjà en place.

---

## 3. Solution proposée

### 3.1 Options

| | **A. Fichier déclaré** | **B. Service de profil dans le hub** (recommandé) | **C. Adaptation automatique par utilisateur** |
|---|---|---|---|
| Principe | `profil_restitution.json` local, lu par chaque application | `GET /api/profile?user=&situation=` + `POST /api/profile/signals` ; valeurs par défaut si le hub ne répond pas | Bandit ou modèle par utilisateur, qui choisit le format |
| Coût | ~2 jours | ~2 à 3 semaines, en incluant A | Plusieurs semaines par application |
| Gain | Cohérence immédiate des prompts | Profil vivant, alimenté par les signaux, une seule source | Optimal à grand volume |
| Risques | Profil figé, dérive entre applications | Dépendance au hub (atténuée par les valeurs par défaut), gouvernance | Sur-ingénierie : pas de volume pour 14 applications sur 16 |
| Charge mentale pour Richard | Nulle | 1 fiche de validation par semaine | Opaque |
| Pertinent pour | Toutes les applications de Richard | Toutes les applications de Richard | Pleine Lune uniquement, à terme |

**Recommandation :** faire A, puis B. C ne se justifie que sur Pleine Lune, et seulement au-delà d'environ 1 000 utilisateurs actifs.

### 3.2 Architecture cible (option B)

```
            ┌───────────── déclaration (questionnaire, page /reglages du hub)
            │  ┌────────── signaux (corrections, écarts, annulations, bascules, captures)
            ▼  ▼
   hub-ekoalu  ── profil versionné { commun + surcharges par situation }
            │        ▲
            │        └── fiche hebdo « modification de profil proposée » → validation Richard
            ▼
   GET /api/profile?user=richard&situation=S3   (cache 60 s ; valeurs par défaut si le hub ne répond pas)
       ├─► bloc prompt  : profile_block(situation)  → ajouté au prompt SYSTÈME (préfixe stable, mis en cache)
       └─► UI           : vue par défaut, densité, confirmation ou annulation
```

Règles de conception :
- **Deux profils séparés.** Le jumeau numérique décrit comment écrire *au nom de* Richard. Le profil de restitution décrit comment écrire *pour* Richard. Mélanger les deux ferait fuiter le style de restitution dans les messages envoyés aux prospects.
- **La situation l'emporte sur le trait.** Le profil a une partie commune et une surcharge par situation (S1 à S5). Le chiffreur veut le schéma ; le lecteur du matin veut 5 lignes.
- **Aucun blocage.** Hub injoignable = valeurs par défaut codées, sur le même modèle que `hub_gate.py` qui laisse passer en cas de panne.
- **Cache de prompt préservé.** Le bloc de profil ne change qu'à la validation d'une nouvelle version : il reste stable et peut donc aller dans le prompt système.
- **Versionnement.** Chaque version du profil est horodatée, ce qui permet de revenir en arrière et d'attribuer un effet à un changement.

### 3.3 Schéma du profil (proposition)

```json
{
  "user": "richard",
  "version": 3,
  "valide_le": "2026-09-30",
  "commun": {
    "ordre": "conclusion_dabord",
    "densite": "synthese_puis_detail",
    "format_prefere": ["tableau", "schema"],
    "a_eviter": ["ton_marketing", "digressions", "metaphores"],
    "expertise": { "batiment": "expert", "finance": "expert", "juridique": "avance", "dev": "avance" },
    "autonomie": "agir_puis_annuler",
    "canal_alerte": "hub"
  },
  "situations": {
    "S1_chiffreur": { "format_prefere": ["schema", "tableau"], "hypotheses_explicites": true },
    "S2_validateur": { "densite": "minimale", "afficher_ecart": true },
    "S3_pilotage": { "graphique_avec_objectif": true, "max_lignes_synthese": 10 },
    "S4_matin": { "max_lignes": 8, "canal": "mail" },
    "S5_rdv": { "une_page": true }
  },
  "source": { "declare": 0.7, "observe": 0.3 }
}
```

Les valeurs ci-dessus sont des exemples. Le contenu réel du profil reste **local ou dans le hub**, jamais dans un dépôt : OpenOutreach est public.

### 3.4 Points d'injection prioritaires

| Rang | Point | Situation | Effort | Gain |
|---|---|---|---|---|
| 1 | Préambule `captureIA/inject-captures.ps1` + `REGLES_CTO.md` | toutes les sessions Claude Code | 0,5 jour | Élevé : touche tous les projets d'un coup |
| 2 | `rdv_prep/writer.py`, `daily_recap`, `daily_conformity` | S3, S5 | 0,5 jour | Élevé |
| 3 | `chiffrage-gti` : chat RAG + vue devis | S1 | 1 jour | Moyen à élevé |
| 4 | `mail-assistant/weekly-report.ts`, `extraction-plaud/daily_recap.py` | S4 | 0,5 jour | Moyen |
| 5 | EKOALU-dashboard : vue par défaut persistée | S3 | 1 jour | Moyen |
| 6 | Pleine Lune : préférences à l'onboarding + recommandations | P1 | 1 à 2 semaines | Produit : fidélisation |

Les générateurs de prospection (DM, cold mail) **ne sont pas concernés** : ils écrivent pour le prospect, pas pour Richard.

---

## 4. Risques et limites

- **Données personnelles (P1, P2).** Le profilage des lecteurs de Pleine Lune et des membres de l'observatoire demande :
  - une information claire dans la politique de confidentialité ;
  - le consentement pour toute inférence qui n'est pas strictement nécessaire au service ;
  - la minimisation des données ;
  - la possibilité de réinitialiser son profil.

  L'observatoire appartient à l'Union des Métalliers : l'accord de son propriétaire est un préalable. L'article 22 du RGPD (décision automatisée) n'est pas en jeu, car l'adaptation d'un format ne produit pas d'effet juridique.
- **Bulle de préférence.** Un profil trop appliqué finit par masquer l'information inhabituelle. Garde-fou : une alerte critique (sécurité, budget, blocage) ignore toujours le profil.
- **Préférence et efficacité ne coïncident pas toujours.** Préférer le tableau ne garantit pas une meilleure décision avec le tableau. Pour S3, il faut mesurer le temps entre la réception et l'action, pas seulement la satisfaction.
- **Dérive entre applications.** Sans source unique (option A seule), chaque application finira avec sa propre copie. C'est ce qui justifie l'option B.
- **Limites de cette étude :**
  - hub-ekoalu non inspecté ;
  - « LMAI » non identifié ;
  - les formats optimaux par situation sont des hypothèses, à confirmer par le questionnaire.

---

## 5. Suite proposée

| Lot | Contenu | Durée | Livrable |
|---|---|---|---|
| **L1** | Questionnaire (8 paires) → profil v0 ; bloc injecté dans le préambule captureIA, `REGLES_CTO.md`, rdv_prep et les récaps | ~2 jours | Profil v0 + 4 points d'injection |
| **L2** | Endpoint profil dans le hub + page /reglages ; captation des écarts brouillon / envoi (mail-assistant, Plaud) et des bascules de vue ; fiche hebdo « modification proposée » | ~2 à 3 semaines | Profil vivant |
| **L3** | Pleine Lune : préférences à l'onboarding, recommandations, consentement | ~2 semaines | Profil lecteur |

**Décisions attendues :**
1. Identifier « LMAI ».
2. Donner accès au code du hub (pour L2).
3. Valider le choix A puis B.
4. Pleine Lune et l'observatoire : dans ce chantier ou dans un chantier séparé ?
