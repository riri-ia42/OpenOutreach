"""Garde-fou d'accroche : l'objet ne doit pas annoncer l'intention.

Remarque Richard 28/07 : « pas d'accroche trop racoleuse du type partenariat à
valider, c'est trop direct et ça se développe dans le mail ». Un objet qui
annonce « partenariat » se lit comme du démarchage avant même l'ouverture ;
l'idée doit se déduire des faits et se développer dans le corps.

La règle est dans le prompt, mais un prompt n'est pas une garantie : sur la
génération du 28/07, 1 objet sur 25 est passé au travers. Ce module la vérifie
après coup et déclenche UNE régénération ciblée.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Mots qui annoncent l'intention au lieu de la laisser déduire.
_MOTS_RACOLEURS = (
    "partenariat", "partenaire", "complémentarité", "complémentaire",
    "collaboration", "collaborer", "rapprochement", "synergie",
    "travaillons ensemble", "à valider", "et si on",
)

_PATTERN = re.compile(
    "|".join(re.escape(m) for m in _MOTS_RACOLEURS), re.IGNORECASE,
)


def find_accroche_violations(subject: str) -> list[str]:
    """Mots d'annonce trouvés dans l'objet (vide si l'objet est factuel)."""
    if not subject:
        return []
    return sorted({m.group(0).lower() for m in _PATTERN.finditer(subject)})


def accroche_fix_instruction(violations: list[str]) -> str:
    """Motif de régénération à joindre au prompt."""
    return (
        "CORRECTION D'ACCROCHE OBLIGATOIRE : ton objet annonçait l'intention "
        f"({', '.join(violations)}), ce qui se lit comme du démarchage. "
        "Régénère avec un objet strictement FACTUEL — ce qu'on fabrique, un "
        "produit précis, l'atelier — sans aucun de ces mots. Le fond du corps "
        "ne change pas : l'idée doit se déduire des faits et se développer dans "
        "le mail, jamais s'annoncer dans l'objet."
    )


def enforce_accroche(subject: str, body: str, regenerate):
    """Valide l'objet ; si annonce d'intention, tente UNE régénération.

    `regenerate` : callable(motif: str) -> (subject, body) ; ("", "") si échec.
    Ne bloque jamais — au pire le mail part en file de validation Richard avec
    un warning, comme le garde-fou de style.
    """
    violations = find_accroche_violations(subject)
    if not violations:
        return subject, body

    logger.warning("Accroche racoleuse (%s) : %r — régénération", violations, subject)
    new_subject, new_body = regenerate(accroche_fix_instruction(violations))
    if not new_subject:
        logger.warning(
            "Régénération d'accroche vide — on garde la version initiale "
            "(part en file de validation Richard)",
        )
        return subject, body

    if find_accroche_violations(new_subject):
        logger.warning(
            "Accroche toujours racoleuse après régénération : %r — le mail part "
            "quand même en file de validation Richard", new_subject,
        )
    return new_subject, new_body
