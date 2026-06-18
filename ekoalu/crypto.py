"""Chiffrement at-rest des champs sensibles (Lot 2 RGPD, 18/06/2026).

Objectif : ne plus stocker en clair dans `db.sqlite3` les secrets (clé API
Claude, etc.) — cf. revue 17/06 P0-2 « chiffrement at-rest promis mais absent ».

Approche : chiffrement applicatif **transparent** via Fernet (AES-128 + HMAC).
La clé vit dans l'environnement (`EKOALU_FIELD_ENCRYPTION_KEY`, chargée depuis
`.env.production`), JAMAIS en base. `EncryptedCharField` chiffre à l'écriture et
déchiffre à la lecture — le reste du code manipule du clair sans le savoir.

Migration douce : un marqueur `fernet:` préfixe le ciphertext. Une valeur sans ce
préfixe est considérée « legacy clair » et renvoyée telle quelle (le premier
`save()` la chiffrera). Donc une base déjà peuplée continue de fonctionner et se
chiffre au fil des écritures + via la data-migration dédiée.

Dégradé sûr : si `EKOALU_FIELD_ENCRYPTION_KEY` est absente, on stocke/lit en
clair (avec un warning) plutôt que de casser le démarrage de l'app.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.db import models

logger = logging.getLogger(__name__)

_ENV_KEY = "EKOALU_FIELD_ENCRYPTION_KEY"
_PREFIX = "fernet:"  # marqueur ciphertext (distingue du clair legacy)


@lru_cache(maxsize=1)
def _fernet() -> Fernet | None:
    """Retourne l'instance Fernet, ou None si aucune clé n'est configurée."""
    key = os.environ.get(_ENV_KEY, "").strip()
    if not key:
        logger.warning(
            "%s absente : les champs sensibles restent en CLAIR (dégradé). "
            "Poser une clé Fernet dans .env.production pour activer le chiffrement.",
            _ENV_KEY,
        )
        return None
    return Fernet(key.encode())


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def encrypt(value: str) -> str:
    """Chiffre `value`. No-op si vide, déjà chiffré, ou pas de clé."""
    if not value or is_encrypted(value):
        return value
    fernet = _fernet()
    if fernet is None:
        return value
    return _PREFIX + fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """Déchiffre `value`. Renvoie tel quel si vide, clair legacy, ou pas de clé."""
    if not is_encrypted(value):
        return value
    fernet = _fernet()
    if fernet is None:
        logger.error("%s manquante : impossible de déchiffrer un champ chiffré.", _ENV_KEY)
        return value
    try:
        return fernet.decrypt(value[len(_PREFIX):].encode()).decode()
    except InvalidToken:
        logger.error("Déchiffrement échoué (clé invalide ou donnée corrompue).")
        return value


class EncryptedCharField(models.CharField):
    """CharField chiffré at-rest (Fernet, transparent).

    Le ciphertext base64 est plus long que le clair : prévoir un `max_length`
    confortable (≈ 4/3 de la taille + ~100 octets d'overhead Fernet).
    """

    def from_db_value(self, value, expression, connection):  # noqa: ARG002
        return decrypt(value) if value is not None else value

    def to_python(self, value):
        if value is None:
            return value
        return decrypt(value) if isinstance(value, str) else value

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt(value) if value else value
