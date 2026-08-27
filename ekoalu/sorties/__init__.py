"""Sorties de prospection (demande Richard 27/08).

Registre des personnes et sociétés sorties volontairement de la prospection,
distinct du bruit de `Lead.disqualified` (rejets LLM, unreachable…) :
le registre ne contient QUE les sorties décidées par Richard.

- personne : disqualification cascade (lead_exclusion) + entrée registre
- société : toutes les personnes rattachées (siren / nom) + entrée registre
  + garde à l'import (les 4 commandes d'import skippent les sirens sortis)
- export JSON partagé `_partage/sorties_prospection.json` pour BDD PROSPECT /
  séances antichambre (filtrage à la source)

Périmètre (décision Richard 27/08) : UNIQUEMENT la prospection 1-to-1
(LinkedIn + cold mail personnalisé). Le mass mailing Mailjet n'est PAS
concerné — ses exclusions légales (bounces/désabonnements, `exclusions.json`)
restent un circuit séparé, ne jamais les fusionner avec ce registre.
"""
