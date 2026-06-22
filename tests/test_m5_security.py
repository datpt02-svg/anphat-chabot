"""Unit tests for M5 security primitives (PII redaction, JWT, HMAC)."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# Env vars must be set BEFORE importing agents.config (which reads them at import time).
os.environ["HMAC_SALT"] = "test_salt"
os.environ["HMAC_SALT_VERSION"] = "v1"
os.environ["JWT_SECRET"] = "test_secret"
os.environ["JWT_ALGORITHM"] = "HS256"

from agents import config  # noqa: E402

importlib.reload(config)
from agents.security import (  # noqa: E402
    PII_REDACTED,
    hash_user_id,
    redact_pii,
    verify_token,
)
import jwt  # noqa: E402

from agents.security import (  # noqa: E402
    PII_REDACTED,
    hash_user_id,
    redact_pii,
    verify_token,
)
import jwt  # noqa: E402


def test_redact_pii_phone_vn() -> None:
    out = redact_pii("Call me 0901234567 or 84901234567")
    assert PII_REDACTED in out
    assert "0901234567" not in out


def test_redact_pii_email() -> None:
    out = redact_pii("Email me at john.doe@example.com please")
    assert PII_REDACTED in out
    assert "john.doe" not in out


def test_redact_pii_api_key() -> None:
    out = redact_pii("Use key sk-ant-1234567890abcdef1234 here")
    assert PII_REDACTED in out


def test_redact_pii_empty() -> None:
    assert redact_pii("") == ""


def test_hash_user_id_deterministic() -> None:
    a = hash_user_id("user-1")
    b = hash_user_id("user-1")
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_hash_user_id_changes_with_salt_version(monkeypatch) -> None:
    a = hash_user_id("user-1")
    monkeypatch.setattr(config, "HMAC_SALT_VERSION", "v2")
    b = hash_user_id("user-1")
    assert a != b


def test_hash_user_id_empty() -> None:
    assert hash_user_id("") == ""


def test_verify_token_hs256_admin() -> None:
    token = jwt.encode(
        {"sub": "u1", "roles": ["admin"], "exp": 9_999_999_999},
        "test_secret",
        algorithm="HS256",
    )
    claims = verify_token(token)
    assert claims.user_id == "u1"
    assert claims.is_admin is True
    assert claims.user_id_hash == hash_user_id("u1")


def test_verify_token_hs256_non_admin() -> None:
    token = jwt.encode(
        {"sub": "u2", "roles": ["user"], "exp": 9_999_999_999},
        "test_secret",
        algorithm="HS256",
    )
    claims = verify_token(token)
    assert claims.is_admin is False


def test_verify_token_rejects_expired() -> None:
    token = jwt.encode(
        {"sub": "u3", "exp": 1},
        "test_secret",
        algorithm="HS256",
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        verify_token(token)


def test_verify_token_rejects_wrong_secret() -> None:
    token = jwt.encode(
        {"sub": "u4", "exp": 9_999_999_999},
        "wrong_secret",
        algorithm="HS256",
    )
    with pytest.raises(jwt.InvalidSignatureError):
        verify_token(token)
