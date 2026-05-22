"""ekoalu-doctor — diagnostic + advisory automatique des anomalies daemon.

V0 (2026-05-22) : ADVISORY ONLY. Le doctor detecte une anomalie via HEALTH.json
et le log daemon, collecte le contexte, demande un diagnostic + plan d'action a
Claude Sonnet 4.6, et envoie un mail recapitulatif a Richard. AUCUNE action
corrective n'est executee automatiquement en V0.

V1 (futur) : execution automatique des actions de la whitelist (kill process
zombie, rotate log, toggle kill-switch) avec caps anti-cascade.
"""
