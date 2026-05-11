"""ekoalu — extensions EKOALU au-dessus d'OpenOutreach.

Modules :
- conf : constantes EKOALU (heures actives, délais, limites)
- human_scheduler : humanisation comportementale (gauss, pondération hebdo, pause déjeuner)
- message_validator : mots bannis EKOALU, termes niches obligatoires, lien booking conditionnel
- personas : 8 personas EKOALU + management command setup_ekoalu
- inbox_assist : brouillon de réponse Claude pour validation Richard
"""
default_app_config = "ekoalu.apps.EkoaluConfig"
