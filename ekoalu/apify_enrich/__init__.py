"""Enrichissement de profils LinkedIn via acteurs Apify COOKIELESS.

Objectif : lire une fiche LinkedIn publique SANS le compte de Richard
(~8 $/1000 profils) pour que le compte ne serve plus qu'a ENGAGER
(invitations/messages), pas a lire. Squelette LOT F — PAS cable dans le
pipeline daemon : la decision de cablage viendra apres le test reel
10-20 profils (``manage.py test_apify_enrich``, cf. docs/APIFY_ENRICH.md).

REGLE ABSOLUE : on ne transmet JAMAIS notre cookie/session LinkedIn a
Apify — uniquement des URLs de profils publics.
"""
from __future__ import annotations
