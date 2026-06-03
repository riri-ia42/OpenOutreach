"""A/B qualifier : compare Haiku (challenger) vs Sonnet (champion) en live.

Pendant N qualifications, chaque profil scrape est score par les DEUX modeles
sur le meme texte. Le champion (SiteConfig, Sonnet) decide pour de vrai ; le
challenger (Haiku) est juste logge pour comparaison. A la fin du quota, le
qualifier se re-met en pause (flag) et un recap est maile a Richard.
"""
from __future__ import annotations
