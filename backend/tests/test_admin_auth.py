"""Unit tests for the admin token signer/verifier — no DB, no network."""

import pytest

from app.infrastructure.security.admin_auth import create_admin_token, verify_admin_token

_SECRET = "test-jwt-secret"


def test_a_freshly_created_token_verifies():
    token = create_admin_token(_SECRET)
    assert verify_admin_token(token, _SECRET) is True


def test_a_token_signed_with_a_different_secret_does_not_verify():
    token = create_admin_token(_SECRET)
    assert verify_admin_token(token, "wrong-secret") is False


def test_a_tampered_token_does_not_verify():
    token = create_admin_token(_SECRET)
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    assert verify_admin_token(tampered, _SECRET) is False


def test_an_expired_token_does_not_verify():
    token = create_admin_token(_SECRET, ttl_seconds=-1)  # already expired
    assert verify_admin_token(token, _SECRET) is False


def test_garbage_input_does_not_verify():
    assert verify_admin_token("not-a-real-token", _SECRET) is False
    assert verify_admin_token("", _SECRET) is False


def test_creating_a_token_with_an_empty_secret_raises():
    # Fail loud at issuance time — an empty secret would make every token forgeable by anyone who
    # can compute HMAC-SHA256 with an empty key (the signing scheme is public, open-source code).
    with pytest.raises(ValueError, match="ADMIN_JWT_SECRET"):
        create_admin_token("")


def test_a_token_forged_with_an_empty_secret_never_verifies():
    # Defense in depth: even if a token bearing an empty-secret signature reaches verification
    # (e.g. an attacker computed one directly, bypassing create_admin_token), it must not pass —
    # this is the actual exploit the fail-open bug enabled.
    import base64
    import hashlib
    import hmac
    import time

    expires_at = int(time.time()) + 3600
    payload = f"admin:{expires_at}"
    forged_signature = hmac.new(b"", payload.encode(), hashlib.sha256).hexdigest()
    forged_token = f"{base64.urlsafe_b64encode(payload.encode()).decode()}.{forged_signature}"
    assert verify_admin_token(forged_token, "") is False
