"""
tests/test_auth_security.py — Unit tests for auth/security.py

Tests: password hashing, JWT creation/decoding, password strength validation.
"""

import pytest
import jwt

from auth.security import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
    validate_password_strength,
    SECRET_KEY,
    ALGORITHM,
)
from fastapi import HTTPException


class TestPasswordHashing:
    def test_hash_is_not_plaintext(self):
        hashed = hash_password("MyPassword1")
        assert hashed != "MyPassword1"

    def test_verify_correct_password(self):
        hashed = hash_password("MyPassword1")
        assert verify_password("MyPassword1", hashed) is True

    def test_verify_wrong_password(self):
        hashed = hash_password("MyPassword1")
        assert verify_password("WrongPassword", hashed) is False

    def test_hash_is_unique_per_call(self):
        h1 = hash_password("SamePassword1")
        h2 = hash_password("SamePassword1")
        assert h1 != h2  # bcrypt salts should differ


class TestPasswordStrength:
    def test_too_short_raises(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_password_strength("Ab1")
        assert exc_info.value.status_code == 422

    def test_no_digit_raises(self):
        with pytest.raises(HTTPException):
            validate_password_strength("NoDigitHere")

    def test_no_letter_raises(self):
        with pytest.raises(HTTPException):
            validate_password_strength("12345678")

    def test_valid_password_passes(self):
        validate_password_strength("ValidPass1")  # Should not raise


class TestJWT:
    def test_create_and_decode_token(self):
        token = create_access_token(user_id=42, email="alice@test.com")
        payload = decode_access_token(token)
        assert payload["sub"] == "42"
        assert payload["email"] == "alice@test.com"

    def test_tampered_token_raises(self):
        token = create_access_token(user_id=1, email="bob@test.com")
        tampered = token[:-5] + "XXXXX"
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(tampered)
        assert exc_info.value.status_code == 401

    def test_expired_token_raises(self):
        from datetime import datetime, timezone, timedelta
        payload = {"sub": "1", "email": "test@test.com", "exp": datetime.now(timezone.utc) - timedelta(seconds=1)}
        expired_token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(expired_token)
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()

    def test_demo_token_is_rejected(self):
        """The critical test: DEMO_TOKEN must NEVER be a valid JWT."""
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token("DEMO_TOKEN")
        assert exc_info.value.status_code == 401
