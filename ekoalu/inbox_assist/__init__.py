"""inbox_assist — brouillon de réponse Claude pour validation Richard.

V1 minimal :
- Modèle PendingReply (stocke inbound + draft + final)
- Modèle CorrectionExample (delta brouillon vs envoi final, pour apprentissage)
- Classifieur d'intention (rule-based en V1, LLM en V2)
- Génération de brouillon : non implémentée en V1 (intégration agent en Phase 3.5/V2)

API publique :
- classify_intent(text) : retourne Intent enum
- Intent : enum des 5 intentions
"""
from ekoalu.inbox_assist.intent_classifier import Intent, classify_intent

__all__ = [
    "Intent",
    "classify_intent",
]
