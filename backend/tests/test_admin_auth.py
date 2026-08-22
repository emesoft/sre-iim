"""Unit tests for the admin token signer/verifier — no DB, no network."""

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
