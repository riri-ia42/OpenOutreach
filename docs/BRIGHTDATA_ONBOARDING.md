# Bright Data — création du compte (à faire par Richard, ~10 min)

> Décision 02/09 : Bright Data devient le fournisseur PRIMAIRE d'enrichissement
> de profils LinkedIn (5 000 enregistrements/mois **gratuits, renouvelés le 1er**).
> Le code est prêt et testé — il ne manque que le token.

## Étapes

1. **Créer le compte** : https://brightdata.com → *Start free trial / Sign up*.
   - Utiliser **richard@ekoalu.com** (un email de domaine pro est requis pour
     débloquer le free tier complet).
   - Une **carte bancaire** est demandée à l'enregistrement : elle n'est PAS
     débitée tant qu'on reste dans les 5 000 enregistrements gratuits/mois.
     Notre garde-fou logiciel (`EKOALU_BRIGHTDATA_MONTHLY_CAP=4500`) bloque
     la consommation AVANT la fin du gratuit — aucun débit possible sans
     décision explicite.

2. **Récupérer le token API** : dans le dashboard → *Account settings* →
   *API tokens* → créer un token (scope par défaut suffit). Il ressemble à
   une longue chaîne hexadécimale.

3. **Poser le token** dans `openoutreach/.env.production` :
   ```
   EKOALU_BRIGHTDATA_TOKEN=<le token>
   ```
   ⚠️ Ne PAS éditer ce fichier avec Get-Content/Set-Content PowerShell
   (double-encodage UTF-8) — édition via Claude Code ou un éditeur de texte.

4. **Valider en réel** (1 profil, ~0 $) :
   ```
   cd openoutreach
   .venv\Scripts\python.exe manage.py brightdata_smoke --url https://www.linkedin.com/in/<un-profil-du-backlog>/
   ```
   La commande affiche les champs mappés (nom, headline, entreprise…).
   Si le mapping sort vide, elle affiche l'enregistrement brut : me le donner,
   j'ajuste `mapper.py` (les clés sont mappées défensivement, à confirmer au
   premier test réel — même procédure qu'Apify le 15/07).

5. **Redémarrer le daemon** (pour qu'il voie la nouvelle variable) :
   kill des process `rundaemon` + `Start-ScheduledTask EKOALU-Prospection-Watchdog`.

## Ce qui se passe ensuite (automatique)

- La tâche planifiée du matin (`scripts/apify_enrich.ps1` → `manage.py
  enrich_backlog`) enrichit le backlog via **Bright Data d'abord**, Apify en
  secours (10/j), mini-fiche SERP en dernier recours cookieless.
- Le daemon utilise la même chaîne pour ses embeds à la volée.
- Compteur mensuel visible : `BrightdataUsageMonth` (Django Admin) ou
  `manage.py enrich_backlog --dry-run`.

## Garde-fous en place

| Garde-fou | Valeur | Rôle |
|---|---|---|
| `EKOALU_BRIGHTDATA_MONTHLY_CAP` | 4500 | Jamais au-delà du gratuit (5 000) sans décision |
| `EKOALU_BRIGHTDATA_ENRICH=0` | kill-switch | Coupe le fournisseur, la chaîne continue |
| Échec réseau/API | remboursé | Compteur décrémenté, lead intact, fournisseur suivant |
| Profil supprimé (`dead_page`) | disqualifié | Sort du backlog, pas de re-facturation quotidienne |
| URL synthétique `bdd-prospect.local` | exclue | Jamais envoyée à un fournisseur externe |

Règle absolue inchangée : **URLs publiques uniquement, jamais notre cookie LinkedIn.**
