"""Symmetric encryption for cloud-connection access keys at rest (Fernet, app-level key).

Not a managed secret store (no KMS/Secrets Manager) — deliberate scope decision, see the design
spec's "Non-goals". The key comes from `SECRET_ENCRYPTION_KEY` and must never be stored in the DB.
"""

from __future__ import annotations

from cryptography.fernet import Fernet


class Encryptor:
    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError("SECRET_ENCRYPTION_KEY is not set")
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()
