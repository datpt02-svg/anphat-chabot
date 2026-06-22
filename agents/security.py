"""M5 security: JWT auth, PII redaction, user_id_hash, slowapi rate limits.

Implements M5 plan §5.3-§5.4 (Auth, PII redaction, rate limit).
"""
from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any

import jwt
from slowapi import Limiter

from agents import config

# --- PII redaction (M5 plan §2) ---
PHONE_RE = re.compile(r"(?:84|0[3|5|7|8|9])+([0-9]{8})\b")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
API_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9-]{16,}\b")
PII_PATTERNS: tuple[re.Pattern[str], ...] = (PHONE_RE, EMAIL_RE, API_KEY_RE)

PII_REDACTED = "[REDACTED]"

PII_FOLLOWUP_MESSAGE = (
    "Tôi không lưu trữ số điện thoại hay email trong cuộc trò chuyện. "
    "Nếu cần hỗ trợ, vui lòng gọi hotline 1900-xxxx hoặc chat với nhân viên."
)


def redact_pii(text: str) -> str:
    """Replace phone numbers, emails, and API keys with `[REDACTED]`."""
    if not text:
        return text
    for pat in PII_PATTERNS:
        text = pat.sub(PII_REDACTED, text)
    return text


# --- HMAC user_id hash (M5 plan §5.3) ---
def hash_user_id(user_id: str) -> str:
    """Deterministic, PII-safe user identifier.

    `current_salt + ":" + rotation_index` is the input so historical Langfuse
    traces remain linked to the same user even after a salt rotation.
    """
    if not user_id:
        return ""
    salt_input = f"{config.HMAC_SALT}:{config.HMAC_SALT_VERSION}"
    return hmac.new(
        salt_input.encode("utf-8"),
        user_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# --- JWT verification (M5 plan §5.3) ---
@dataclass
class TokenClaims:
    user_id: str
    user_id_hash: str
    is_admin: bool
    raw: dict[str, Any]


def verify_token(token: str) -> TokenClaims:
    """Verify a JWT and return the resolved claims.

    Dispatches between RS256 (with JWKS) and HS256 (with shared secret) based
    on `JWT_ALGORITHM`. Raises `jwt.PyJWTError` on any verification failure.
    """
    if config.JWT_ALGORITHM == "RS256":
        if not config.JWT_JWKS_URL:
            raise RuntimeError("JWT_ALGORITHM=RS256 requires JWT_JWKS_URL")
        jwks_client = jwt.PyJWKClient(config.JWT_JWKS_URL, cache_keys=True, lifespan=3600)
        signing_key = jwks_client.get_signing_key_from_jwt(token).key
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            options={"require": ["exp"]},
        )
    elif config.JWT_ALGORITHM == "HS256":
        if not config.JWT_SECRET:
            raise RuntimeError("JWT_ALGORITHM=HS256 requires JWT_SECRET")
        payload = jwt.decode(
            token,
            config.JWT_SECRET,
            algorithms=["HS256"],
            options={"require": ["exp"]},
        )
    else:
        raise RuntimeError(f"Unsupported JWT_ALGORITHM: {config.JWT_ALGORITHM}")

    user_id = str(payload.get("sub") or payload.get("user_id") or "")
    is_admin = config.JWT_ADMIN_CLAIM_VALUE in (payload.get(config.JWT_ADMIN_CLAIM_KEY) or [])
    return TokenClaims(
        user_id=user_id,
        user_id_hash=hash_user_id(user_id),
        is_admin=is_admin,
        raw=payload,
    )


# --- slowapi limiter (M5 plan §5.3) ---
limiter = Limiter(key_func=lambda: "global")
