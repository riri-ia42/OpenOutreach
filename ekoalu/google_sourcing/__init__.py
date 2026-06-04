"""Sourcing de profils LinkedIn via Google Custom Search (chantier #3).

But : trouver les URLs de profils via Google (`site:linkedin.com/in "Entreprise"
"poste"`) au lieu de la recherche interne LinkedIn → zero activite de recherche
cote LinkedIn + URLs bien plus ciblees. Les profils trouves sont crees en leads
URL-only + rattaches a leur campagne (LeadDiscovery, via le routage) ; le daemon
les enrichit et les qualifie ensuite dans SA session browser (pas de 2e browser).

V1 : commande separee `source_via_google` (manuel/cron), ABM d'abord.
Config : env GOOGLE_CSE_API_KEY + GOOGLE_CSE_CX.
"""
