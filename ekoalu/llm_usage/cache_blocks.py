"""Decoupage du system prompt en blocs pour le prompt caching Anthropic.

Le wrapper `llm_usage.patch._inject_cache_control` pose automatiquement
`cache_control` sur le DERNIER bloc systeme. Or dans les generateurs (cold
mail, DM LinkedIn, reponse email) le system etait UNE seule chaine
`regles_apprises + prompt + few_shot` : la partie la plus volatile (le
few-shot CorrectionExample, selectionne PAR PERSONA pour les DM) se trouvait
donc dans le prefixe cache. Resultat : un prefixe different par persona et a
chaque nouvelle correction de Richard -> cache quasi jamais relu.

`build_system_blocks` separe la partie stable (regles apprises + prompt de la
variante) du few-shot variable, et pose EXPLICITEMENT `cache_control` sur le
bloc stable. Le wrapper respecte un cache_control deja pose par l'appelant
(`if not any(... cache_control ...)`) et ne touche plus a rien : pas besoin de
modifier le wrapper, qui reste le filet de secours pour tous les autres
appels.

Le TEXTE envoye au modele est inchange : les blocs sont concatenes par l'API
dans l'ordre de la liste, qui est l'ordre d'origine.
"""
from __future__ import annotations

# Seuil minimum Anthropic pour cache_control = 1024 tokens sur Sonnet 4.6.
# On prend 4000 chars comme proxy (~1 token tous les 4 chars en FR). En
# dessous, l'API ignore silencieusement le marqueur (pas d'erreur, juste
# cache_creation_input_tokens = 0) -- on evite donc de le poser pour rien.
CACHE_MIN_CHARS = 4000


def build_system_blocks(stable: str, variable: str = "") -> list[dict]:
    """Construit le `system` en blocs : [stable (cache)] + [variable].

    Args:
        stable: partie identique d'un appel a l'autre (prompt metier, regles
            apprises). Recoit `cache_control` si elle depasse le seuil.
        variable: partie qui bouge (few-shot par persona). Jamais cachee, et
            omise si vide -- l'API refuse un bloc texte vide.
    """
    blocks: list[dict] = []
    if stable:
        block: dict = {"type": "text", "text": stable}
        if len(stable) >= CACHE_MIN_CHARS:
            block["cache_control"] = {"type": "ephemeral"}
        blocks.append(block)
    if variable and variable.strip():
        blocks.append({"type": "text", "text": variable})
    return blocks


def system_text(system) -> str:
    """Reconstitue le texte complet du system prompt (logs, tests, debug)."""
    if isinstance(system, str):
        return system
    if not system:
        return ""
    return "".join(
        b.get("text", "") if isinstance(b, dict) else str(b) for b in system
    )
