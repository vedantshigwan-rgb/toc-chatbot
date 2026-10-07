from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import jwt
from jwt import ExpiredSignatureError, InvalidTokenError

from config.settings import (
    AUTH_ACCESS_TOKEN_EXPIRE_MINUTES,
    AUTH_JWT_ALGORITHM,
    AUTH_JWT_SECRET,
)


PASSWORD_HASH_NAME = "sha256"
PASSWORD_HASH_ITERATIONS = 600_000
PASSWORD_SALT_BYTES = 16


class AuthTokenError(ValueError):
    pass


def validate_auth_configuration() -> None:
    secret = str(AUTH_JWT_SECRET or "").strip()
    if len(secret) < 32:
        raise RuntimeError(
            "AUTH_JWT_SECRET must be configured with at least 32 characters."
        )


def normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(PASSWORD_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        PASSWORD_HASH_NAME,
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
    )

    salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii")
    digest_b64 = base64.urlsafe_b64encode(digest).decode("ascii")

    return (
        f"pbkdf2_sha256${PASSWORD_HASH_ITERATIONS}"
        f"${salt_b64}${digest_b64}"
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        scheme, iterations_text, salt_b64, digest_b64 = (
            encoded_hash.split("$", 3)
        )
        if scheme != "pbkdf2_sha256":
            return False

        iterations = int(iterations_text)
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
    except (ValueError, TypeError):
        return False

    actual = hashlib.pbkdf2_hmac(
        PASSWORD_HASH_NAME,
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual, expected)


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(*, email: str, otp: str) -> str:
    message = f"{normalize_email(email)}:{otp}".encode("utf-8")
    return hmac.new(
        AUTH_JWT_SECRET.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def verify_otp(*, email: str, otp: str, encoded_hash: str) -> bool:
    actual = hash_otp(email=email, otp=otp)
    return hmac.compare_digest(actual, str(encoded_hash or ""))


def create_access_token(*, email: str) -> str:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(
        minutes=AUTH_ACCESS_TOKEN_EXPIRE_MINUTES
    )

    payload: Dict[str, Any] = {
        "sub": normalize_email(email),
        "type": "access",
        "iat": now,
        "exp": expires_at,
    }

    return jwt.encode(
        payload,
        AUTH_JWT_SECRET,
        algorithm=AUTH_JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> Dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            AUTH_JWT_SECRET,
            algorithms=[AUTH_JWT_ALGORITHM],
            options={
                "require": ["sub", "iat", "exp"],
            },
        )
    except ExpiredSignatureError as error:
        raise AuthTokenError("Access token has expired.") from error
    except InvalidTokenError as error:
        raise AuthTokenError("Access token is invalid.") from error

    if payload.get("type") != "access":
        raise AuthTokenError("Access token is invalid.")

    email = normalize_email(str(payload.get("sub") or ""))
    if not email:
        raise AuthTokenError("Access token is invalid.")

    payload["sub"] = email
    return payload
