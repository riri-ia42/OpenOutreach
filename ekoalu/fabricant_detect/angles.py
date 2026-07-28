"""Angle commercial dérivé du verdict fabricant (décision Richard 28/07).

On garde les fabricants dans le pipe — ce sont des confrères, donc des
partenaires potentiels — mais **on adapte le discours** : on ne propose jamais
ce qu'ils savent déjà faire, on propose ce qui leur manque.

| Ce qu'ils fabriquent | Ce qu'on met en avant                                |
|----------------------|------------------------------------------------------|
| alu seul             | acier + produits techniques                          |
| acier seul           | alu + Jansen acier + produits techniques             |
| alu ET acier         | sous-traitance + produits techniques                 |
| bois / PVC seul      | alu + acier + produits techniques  *(cf. note)*      |

⚠️ La dernière ligne est une EXTENSION de la règle de Richard, pas sa consigne :
il a cadré alu / acier / les deux. Pour un fabricant bois ou PVC, toute la gamme
EKOALU est complémentaire — c'est la lecture logique de sa règle, mais elle
reste à confirmer.

Politiques sensibles respectées : ni Cortizo ni Sepalumic ne sont cités (règle
com EKOALU). Jansen est un système de profilés acier, citable.
"""
from __future__ import annotations

from dataclasses import dataclass

# Les niches EKOALU — la porte d'entrée quel que soit l'angle (cf. MARKETING.md).
PRODUITS_TECHNIQUES = (
    "coupe-feu EI30/EI60/EI120, désenfumage, pare-balles BC1-BC4, "
    "grandes dimensions, acoustique élevée"
)

ALU = "alu"
ACIER = "acier"


@dataclass(frozen=True)
class Angle:
    """Angle commercial à injecter dans la génération du cold mail."""

    cle: str
    resume: str
    contexte: str

    def __bool__(self) -> bool:
        return bool(self.contexte)


ANGLE_STANDARD = Angle(cle="standard", resume="Discours standard", contexte="")


def _angle(cle: str, resume: str, corps: str) -> Angle:
    return Angle(
        cle=cle,
        resume=resume,
        contexte=(
            "CONTEXTE DESTINATAIRE — c'est un CONFRÈRE FABRICANT, pas un client final.\n"
            f"{corps}\n"
            "Ton : d'égal à égal, entre gens du métier. Ne lui explique pas son propre "
            "travail et ne lui propose jamais ce qu'il fabrique déjà — ce serait se "
            "poser en concurrent. L'angle est la complémentarité."
        ),
    )


ANGLE_ALU_VERS_ACIER = _angle(
    "alu_vers_acier",
    "Fabricant alu → on propose l'acier + les produits techniques",
    "Il fabrique de l'aluminium. Ne propose PAS d'aluminium et ne présente pas "
    "EKOALU comme un fabricant alu — il l'est déjà.\n"
    "L'offre à mettre en avant, NOMMÉMENT et dès le corps du mail, est double :\n"
    "  1. l'ACIER — cite-le explicitement comme ce qu'on peut produire pour lui ;\n"
    f"  2. les produits techniques ({PRODUITS_TECHNIQUES}).\n"
    "C'est ce qu'il ne produit pas et sur quoi il doit aujourd'hui refuser ou "
    "sous-traiter des affaires.",
)

ANGLE_ACIER_VERS_ALU = _angle(
    "acier_vers_alu",
    "Fabricant acier → on propose l'alu, le Jansen acier + les produits techniques",
    "Il fabrique de l'acier. Ne le présente pas comme un domaine où EKOALU "
    "viendrait le concurrencer.\n"
    "L'offre à mettre en avant, NOMMÉMENT et dès le corps du mail, est triple :\n"
    "  1. l'ALUMINIUM ;\n"
    "  2. le système JANSEN en acier (profilés fins, coupe-feu) — cite la marque : "
    "c'est un système spécifique que peu d'ateliers maîtrisent, il se justifie même "
    "face à un acièriste ;\n"
    f"  3. les produits techniques ({PRODUITS_TECHNIQUES}).",
)

ANGLE_SOUS_TRAITANCE = _angle(
    "sous_traitance",
    "Fabricant alu + acier → sous-traitance + produits techniques",
    "Il fabrique DÉJÀ l'alu ET l'acier : rien de la gamme courante ne l'intéresse. "
    "L'angle est la SOUS-TRAITANCE — absorber ses débordements de charge, les lots "
    f"qu'il ne peut pas honorer — et les produits techniques ({PRODUITS_TECHNIQUES}) "
    "qu'il ne traite pas en interne.",
)

ANGLE_COMPLEMENT_TOTAL = _angle(
    "complement_total",
    "Fabricant bois/PVC → alu + acier + produits techniques",
    "Il fabrique du bois et/ou du PVC, pas de métal. Toute la gamme EKOALU lui est "
    f"complémentaire : ALUMINIUM, ACIER et produits techniques ({PRODUITS_TECHNIQUES}). "
    "Il doit aujourd'hui refuser ou sous-traiter les affaires métal.",
)


def angle_for_materiaux(materiaux: list[str] | None) -> Angle:
    """Angle déduit des matériaux fabriqués. À n'appeler que sur un fabricant."""
    mats = {str(m).strip().lower() for m in (materiaux or [])}
    fait_alu = ALU in mats
    fait_acier = ACIER in mats

    if fait_alu and fait_acier:
        return ANGLE_SOUS_TRAITANCE
    if fait_alu:
        return ANGLE_ALU_VERS_ACIER
    if fait_acier:
        return ANGLE_ACIER_VERS_ALU
    if mats:  # bois, PVC, verre… : tout le métal est complémentaire
        return ANGLE_COMPLEMENT_TOTAL
    # Fabricant sans matériau identifié : on ne devine pas, discours standard.
    return ANGLE_STANDARD


def angle_for_siren(siren: str) -> Angle:
    """Angle pour une société, depuis son verdict en base.

    Standard si pas de verdict, si ce n'est pas un fabricant, ou si le verdict
    manque de conviction (`is_fabricant` exige une confiance non-basse) — on
    n'adapte jamais le discours sur une supposition.
    """
    if not siren:
        return ANGLE_STANDARD
    from ekoalu.fabricant_detect.models import FabricantVerdict

    verdict = FabricantVerdict.objects.filter(siren=siren).first()
    if verdict is None or not verdict.is_fabricant:
        return ANGLE_STANDARD
    return angle_for_materiaux(verdict.materiaux)
