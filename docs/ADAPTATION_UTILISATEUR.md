# Outil évolutif en direct : adapter une application à l'utilisateur externe

*v2, 23/09/2026. Recadrage : la cible, ce sont les **utilisateurs externes** de GTI Chiffrage et de Cockpit (alias P2), pas les outils internes ni le hub.*

> **Limite de cette version.** Le code de Cockpit / P2 n'est pas accessible depuis cette session : aucun dépôt ne correspond. Le dépôt `chiffrage-gti` visible date du 11/06 et le décrit encore comme un outil **interne**. Les typologies ci-dessous sont donc des **hypothèses fondées sur le métier** (chaîne de valeur de la menuiserie extérieure en B2B). Elles sont à confronter aux utilisateurs réels.

---

## 0. Synthèse

1. **On ne classe pas les gens en « visuel » ou « auditif ».** Cette théorie des styles d'apprentissage n'a pas été validée. Ce qui prédit réellement la bonne interface, c'est :
   - le **rôle dans la chaîne** : architecte, économiste, entreprise, poseur, maître d'ouvrage ;
   - l'**expertise en menuiserie** ;
   - l'**intention de la visite** : budget rapide, chiffrage ferme, vérification technique ;
   - le **contexte** : bureau ou chantier, ordinateur ou mobile.

   Le format préféré (schéma, tableau, texte) en découle en grande partie.
2. **Le rôle déclaré à l'entrée porte la majorité de la valeur.** Une question unique (« Vous êtes… ») plus deux ou trois signaux observés dans la première minute suffisent à choisir le bon mode.
3. **« Évolutif en direct » ne veut pas dire une interface générée à la volée par une IA.** Cela veut dire un **profil à confiance croissante**, mis à jour à chaque action, qui pilote une **liste finie de variantes testées** (vue, densité, vocabulaire, sortie, guidage). L'utilisateur garde toujours la main par une bascule visible.
4. **Règle non négociable : l'adaptation porte sur la forme, jamais sur le prix ni sur les conditions commerciales.** Faire varier un prix selon le profil détruirait la confiance et exposerait à un risque juridique.
5. **Recommandation : option B**, profil par scores + règles explicites, dans une brique partagée entre GTI et Cockpit. Le bandit (option C) ne vient qu'après, et seulement si le volume le justifie.

---

## 1. Cartographie des profils externes

### 1.1 Rôles probables (à valider)

| Rôle | Ce qu'il vient chercher | Expertise menuiserie | Format dominant attendu | Contexte |
|---|---|---|---|---|
| **R1 Architecte / maître d'œuvre** | Faisabilité, rendu, performance (Uw, PMR, sécurité incendie) | Moyenne | **Schéma** (élévation), fiche technique | Bureau, phase conception |
| **R2 Économiste / bureau d'études** | Prix fiable par poste, quantitatif | Moyenne à forte | **Tableau** (format DPGF, Excel) | Bureau, en volume |
| **R3 Entreprise générale / acheteur** | Prix ferme, délai, comparaison | Faible à moyenne | Tableau court + total + délai | Bureau, pression prix |
| **R4 Menuisier / poseur partenaire** | Nomenclature, débit, dimensions de pose | **Experte** | Liste de débit, codes profils, schéma coté | Atelier, chantier, mobile |
| **R5 Maître d'ouvrage / bailleur / gestionnaire** | Budget global, conformité, durabilité | Faible | **Texte** de synthèse + 3 chiffres | Bureau, décision |
| **R6 Particulier ou prospect non qualifié** | Ordre de grandeur | Nulle | Fourchette de prix, visuel simple | Mobile |
| **R7 Commercial EKOALU face au client** | Montrer et convaincre en rendez-vous | Experte | Mode présentation : schéma + total | Tablette chez le client |

Pour Cockpit / P2, le tableau reste à remplir : il faut connaître la fonction de l'outil pour aller plus loin.

### 1.2 Les dimensions qui pilotent l'adaptation

On ne stocke pas une étiquette figée (« R2 »). On stocke un **vecteur** de dimensions, chacune avec sa confiance. Un utilisateur réel est souvent un mélange : un économiste peut être expert en menuiserie.

| Dimension | Valeurs | Ce qui change dans l'outil |
|---|---|---|
| `expertise` | novice / intermédiaire / expert | Vocabulaire (« dormant » ou « cadre fixe »), aide contextuelle, codes profils visibles ou masqués |
| `intention` | estimer / chiffrer / vérifier / présenter | Parcours : saisie en 3 champs ou configurateur complet |
| `format` | schéma / tableau / texte | Vue par défaut du résultat |
| `densite` | synthèse / détail | Sections repliées ou dépliées, longueur des réponses du chat |
| `sortie` | PDF / Excel / DPGF / lien | Export mis en avant |
| `contexte` | ordinateur / mobile / présentation | Mise en page, taille des cibles tactiles |
| `guidage` | assistant pas à pas / formulaire libre | Mode assistant ou expert |

---

## 2. Comment détecter le profil

### 2.1 Trois étages

| Étage | Moment | Moyen | Poids initial |
|---|---|---|---|
| **Déclaré** | Entrée dans l'outil | 1 question obligatoire (rôle) + 1 optionnelle (« Plutôt schéma ou tableau ? ») | Élevé au départ, décroît |
| **Observé** | Chaque action | Événements d'usage (§2.2) | Croît avec le volume |
| **Inféré** | Chat ou upload | Classification par LLM du vocabulaire et des documents déposés | Moyen, jamais seul |

Pas de questionnaire long : au-delà de 2 questions, l'abandon à l'entrée coûte plus que le gain. Le profil se complète **au fil de l'usage**.

### 2.2 Signaux observables, ce qu'ils indiquent et leur poids

| Signal | Indique | Poids |
|---|---|---|
| Dépose un PDF de plan ou un DPGF au lieu de saisir | intention `chiffrer`, format `tableau` | fort |
| Saisit des dimensions en mm au premier essai, sans aide | `expertise` expert | fort |
| Utilise des codes profils ou du vocabulaire technique dans le chat (tapée, dormant, ouvrant T) | `expertise` expert | fort |
| Ouvre l'aide ou survole les infobulles à répétition | `expertise` novice | moyen |
| Bascule sur la vue schéma, ou ne la quitte pas | `format` schéma | moyen |
| Déplie le détail de la nomenclature | `densite` détail | moyen |
| Choisit l'export Excel plutôt que PDF | `sortie` Excel, rôle R2 / R3 | fort |
| Session sur mobile en journée de semaine | `contexte` chantier | moyen |
| Revient plusieurs fois sur le même devis et modifie les options | `intention` comparer ou arbitrer | moyen |
| Abandonne à une étape donnée | Point de friction **pour ce profil** | indicateur de pilotage |

**Le signal le plus fiable reste la bascule manuelle** (« Vue simple / Vue expert », « Schéma / Tableau »). Un choix manuel l'emporte sur toute inférence et se mémorise.

### 2.3 Mise à jour du profil

- Chaque signal ajuste le score d'une dimension (exemple : `+0,3` vers expert).
- On applique une **décroissance** : les signaux anciens pèsent moins, et le profil suit l'évolution d'un utilisateur qui monte en compétence.
- On bascule de mode seulement au-delà d'un **seuil**, avec une **hystérésis** (un seuil de retour différent du seuil d'aller) pour que l'interface ne change pas d'aspect à chaque clic.
- Un changement de mode s'annonce en une ligne (« Vue passée en mode expert — revenir »), jamais en silence.

---

## 3. Architecture de l'outil évolutif

### 3.1 Options

| | **A. Modes fixes** | **B. Profil par scores + règles** (recommandé) | **C. Optimisation automatique (bandit) / interface générée par IA** |
|---|---|---|---|
| Principe | 2 ou 3 modes (Simple / Expert / Présentation), choisis par l'utilisateur | Profil multidimensionnel mis à jour en direct ; des règles lisibles choisissent les variantes | L'algorithme teste les variantes et garde la plus performante par profil, ou un LLM compose l'écran |
| Délai | ~1 semaine | ~3 à 4 semaines, A compris | +4 à 8 semaines |
| Données nécessaires | Aucune | Quelques dizaines d'utilisateurs | **Plusieurs centaines de sessions par semaine et par point de décision** |
| Explicabilité | Totale | Bonne (chaque règle est lisible) | Faible |
| Risques | Profil rigide | Réglage des seuils | Sur-ingénierie, comportements imprévisibles, IA qui invente un écran faux |
| Évolutivité | Faible | Forte : on ajoute des règles et des variantes | Maximale |

**Recommandation : B**, bâti sur A. C seulement si le volume l'exige, et **uniquement en bandit sur des variantes déjà validées**. Jamais d'interface générée par IA sur un outil de chiffrage : une cote ou un prix mal affiché coûte plus que ce que l'adaptation rapporte.

### 3.2 Schéma de principe

```
 Navigateur (GTI, Cockpit)
   ├─ sdk-adaptation.js : capte les événements (§2.2), applique le manifeste
   │        │ événements                     ▲ manifeste (vue, densité, vocabulaire, export)
   ▼        ▼                                │
 Service profil (brique partagée, base de l'application, PAS le hub)
   ├─ profil = { dimension: {valeur, confiance, maj} } + choix manuels (prioritaires)
   ├─ moteur de règles  : profil → manifeste d'adaptation
   └─ bloc prompt       : profil → consignes de registre et de profondeur pour le chat / RAG
        │
        └─ tableau de pilotage : taux de devis finalisés, temps de chiffrage, abandons, par profil
```

**Le manifeste d'adaptation**, exemple :

```json
{
  "vue_resultat": "schema",
  "densite": "synthese",
  "vocabulaire": "technique",
  "codes_profils_visibles": true,
  "export_principal": "excel",
  "guidage": "libre",
  "raison": "expertise=expert (0.82), sortie=excel (0.74)"
}
```

Le champ `raison` sert à expliquer l'adaptation à l'utilisateur, et au débogage.

### 3.3 Ce que l'on adapte, et ce que l'on n'adapte jamais

| Adapté | Jamais adapté |
|---|---|
| Vue par défaut, ordre des sections, densité | **Prix, remises, coefficients, conditions commerciales** |
| Vocabulaire, aide, infobulles | Hypothèses de calcul et avertissements techniques (chute, performance) |
| Export mis en avant | Mentions légales et réglementaires |
| Registre et profondeur des réponses du chat | Contenu factuel des réponses |
| Parcours : assistant ou formulaire libre | Accès aux fonctions : tout reste accessible en un clic |

### 3.4 Brique commune à GTI et Cockpit

Il faut une seule brique `adaptation` (SDK côté navigateur + module serveur), et non deux implémentations :
- même vocabulaire de dimensions ;
- chaque application déclare ses propres **variantes** et ses **règles** ;
- profil stocké **par application**. Un profil partagé entre les deux n'a de sens que si les utilisateurs sont communs **et** consentent à ce croisement.

---

## 4. Contraintes

| Sujet | Contrainte | Traitement |
|---|---|---|
| **RGPD** | Un utilisateur B2B identifié reste une personne physique : le profil est une donnée personnelle | Base légale : intérêt légitime (ergonomie), à documenter ; information dans la politique de confidentialité ; aucune inférence sensible ; droit de réinitialiser son profil ; durée de conservation (exemple : 13 mois sans activité) |
| **Traceurs (CNIL)** | La mesure d'usage via cookies ou stockage local peut exiger un consentement | Événements rattachés au compte, côté serveur, finalité strictement fonctionnelle (adaptation). Si des outils d'analyse tiers sont ajoutés, bandeau de consentement |
| **Confiance** | Une interface qui change d'aspect sans prévenir déstabilise | Hystérésis, annonce du changement, bascule manuelle toujours visible |
| **Qualité du chiffrage** | Masquer une information à un novice peut cacher un risque | Les avertissements techniques ne sont jamais masqués, seulement reformulés |
| **Compte ou anonyme** | Sans compte, le profil ne survit qu'à la session | Avant l'identification, le profil est tenu par la session (et par appareil si le consentement le permet) ; rattachement au compte à la connexion |

---

## 5. Pilotage : prouver que l'adaptation rapporte

Indicateurs à suivre **par profil**, en comparant une version adaptée à une version standard (groupe témoin d'environ 10 %) :

| Indicateur | Cible indicative |
|---|---|
| Taux de devis menés jusqu'à l'export | +15 à 25 % |
| Temps médian jusqu'au premier chiffrage | −30 % |
| Abandons à l'entrée (question de rôle) | < 10 % |
| Taux de bascule manuelle après une adaptation automatique | < 20 % (au-delà, la règle est mauvaise) |
| Conversion devis → demande de contact ou commande | À mesurer ; c'est l'indicateur du ROI |

Sans groupe témoin, on ne peut pas savoir si l'adaptation rapporte ou si l'on se fait plaisir.

---

## 6. Suite proposée

| Lot | Contenu | Durée |
|---|---|---|
| **L0 Cartographie réelle** | Confronter les rôles R1 à R7 aux utilisateurs réels de GTI et de Cockpit (liste des comptes, 5 entretiens ou relecture des demandes reçues) ; fixer 3 profils prioritaires | 2 à 3 jours |
| **L1 Instrumentation + modes** | SDK d'événements, question de rôle, modes Simple / Expert / Présentation avec bascule, sur GTI | ~1 semaine |
| **L2 Profil en direct** | Scores, décroissance, hystérésis, moteur de règles, manifeste, bloc prompt pour le chat RAG ; extension à Cockpit | 2 à 3 semaines |
| **L3 Optimisation** | Groupe témoin, tableau de pilotage, bandit sur 1 ou 2 points de décision si le volume le permet | Selon le volume |

**Éléments nécessaires pour passer de l'hypothèse au concret :**
1. Accès au dépôt de Cockpit / P2 et à la version actuelle de GTI Chiffrage, ouverte à l'externe.
2. Qui sont aujourd'hui les utilisateurs externes (rôles, nombre, fréquence) et s'ils ont un compte.
3. Le volume attendu (sessions par mois), qui décide de l'opportunité de l'option C.
