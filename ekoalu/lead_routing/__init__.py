"""Routage des leads vers leur campagne d'origine.

Corrige la cause racine du ~97,6% de rejet : sans ce module, chaque campagne
qualifie TOUTE la base (`get_leads_for_qualification` renvoie
`Lead.objects.filter(disqualified=False)`), donc un profil trouve pour la
campagne A est teste — et rejete — contre les 54 autres.

Ici on memorise quelle campagne a DECOUVERT chaque profil (`LeadDiscovery`) et
on restreint la qualification d'une campagne a SES propres profils.
"""
