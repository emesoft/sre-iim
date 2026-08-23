"""Symmetric encryption for cloud-connection access keys at rest (Fernet, app-level key).

Not a managed secret store (no KMS/Secrets Manager) — deliberate scope decision, see the design
spec's "Non-goals". The key comes from `SECRET_ENCRYPTION_KEY` and must never be stored in the DB.

Construction is deliberately lazy about the key: SSO-only connections never call encrypt/decrypt,
so requiring the key up front (e.g. at FastAPI dependency-resolution time) would 500 on read/list/
delete/SSO endpoints just because SECRET_ENCRYPTION_KEY is unset. The key is only required at the
moment something is actually encrypted or decrypted.
"""

from __future__ import annotations

from cryptography.fernet import Fernet


class Encryptor:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode()) if key else None

    def encrypt(self, plaintext: str) -> str:
        return self._require_fernet().encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._require_fernet().decrypt(ciphertext.encode()).decode()

    def _require_fernet(self) -> Fernet:
        if self._fernet is None:
            raise ValueError("SECRET_ENCRYPTION_KEY is not set")
        return self._fernet
