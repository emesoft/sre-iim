"""Unit tests for Encryptor — no DB, no network."""

import pytest

from app.infrastructure.security.encryptor import Encryptor

_KEY = "zH8yV2m3sVW6tG5v9pQwQflR4z1sT8y3lU9wA0b3iF4="  # a valid Fernet key for tests


def test_round_trip():
    enc = Encryptor(_KEY)
    ciphertext = enc.encrypt("AKIAEXAMPLE")
    assert ciphertext != "AKIAEXAMPLE"
    assert enc.decrypt(ciphertext) == "AKIAEXAMPLE"


def test_missing_key_raises():
    with pytest.raises(ValueError, match="SECRET_ENCRYPTION_KEY"):
        Encryptor("")
