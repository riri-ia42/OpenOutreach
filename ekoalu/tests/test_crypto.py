"""Tests du chiffrement at-rest des champs sensibles (Lot 2 RGPD, P0-2)."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from ekoalu import crypto

pytestmark = pytest.mark.django_db


@pytest.fixture
def fernet_key(monkeypatch):
    """Pose une clé Fernet de test et vide le cache du module."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("EKOALU_FIELD_ENCRYPTION_KEY", key)
    crypto._fernet.cache_clear()
    yield key
    crypto._fernet.cache_clear()


class TestEncryptDecrypt:
    def test_round_trip(self, fernet_key):
        secret = "sk-ant-api03-XYZ-0123456789"
        token = crypto.encrypt(secret)
        assert crypto.is_encrypted(token)
        assert token != secret
        assert crypto.decrypt(token) == secret

    def test_idempotent_double_encrypt(self, fernet_key):
        token = crypto.encrypt("hello")
        assert crypto.encrypt(token) == token  # ne ré-encapsule pas

    def test_vide_inchange(self, fernet_key):
        assert crypto.encrypt("") == ""
        assert crypto.decrypt("") == ""

    def test_clair_legacy_lu_tel_quel(self, fernet_key):
        # Une valeur sans préfixe = clair legacy → renvoyée telle quelle
        assert crypto.decrypt("sk-clair-legacy") == "sk-clair-legacy"

    def test_sans_cle_degrade_en_clair(self, monkeypatch):
        monkeypatch.delenv("EKOALU_FIELD_ENCRYPTION_KEY", raising=False)
        crypto._fernet.cache_clear()
        assert crypto._fernet() is None
        # Pas de clé → on stocke/relit en clair sans crasher
        assert crypto.encrypt("secret") == "secret"
        assert crypto.decrypt("secret") == "secret"
        crypto._fernet.cache_clear()


class TestEncryptedCharFieldOnSiteConfig:
    def test_db_value_est_chiffree_lecture_transparente(self, fernet_key):
        """La valeur stockée en base est chiffrée, mais l'accès ORM rend le clair."""
        from django.db import connection

        from linkedin.models import SiteConfig

        cfg = SiteConfig.load()
        cfg.llm_api_key = "sk-ant-secret-key"
        cfg.save()

        # Lecture ORM = clair (from_db_value déchiffre)
        assert SiteConfig.load().llm_api_key == "sk-ant-secret-key"

        # Lecture brute SQL = ciphertext préfixé (jamais le clair)
        with connection.cursor() as cur:
            cur.execute("SELECT llm_api_key FROM linkedin_siteconfig WHERE id=1")
            raw = cur.fetchone()[0]
        assert raw.startswith("fernet:")
        assert "sk-ant-secret-key" not in raw
