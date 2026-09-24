"""Password hashing and token primitives. No I/O here."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

JWT_ALGORITHM = "HS256"

_hasher = PasswordHasher()
# Checked against when an email isn't registered, so login takes the same time
# either way and response timing doesn't reveal which emails have accounts.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def burn_password_check(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


def _jwt_secret() -> str:
    secret = get_settings().jwt_secret
    if secret is None:
        raise RuntimeError("JWT_SECRET is not set. Add it to your .env file.")
    return secret.get_secret_value()


def create_access_token(user_id: uuid.UUID) -> str:
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=get_settings().access_token_ttl_minutes)
    claims = {"sub": str(user_id), "type": "access", "iat": now, "exp": expires}
    return jwt.encode(claims, _jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> uuid.UUID | None:
    """Returns the user ID, or None for any invalid, expired or wrong-type token."""
    try:
        claims = jwt.decode(
            token, _jwt_secret(), algorithms=[JWT_ALGORITHM], options={"require": ["sub", "exp", "iat"]}
        )
        if claims.get("type") != "access":
            return None
        return uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError):
        return None


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    # Refresh tokens are long and random, so a fast hash is enough.
    return hashlib.sha256(token.encode()).hexdigest()
