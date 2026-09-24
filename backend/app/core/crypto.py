"""Encryption for secrets stored in the database, such as social OAuth tokens (spec sections 20 and 46)."""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings


class DecryptionError(Exception):
    pass


@lru_cache(maxsize=4)
def _fernet(keys: str) -> MultiFernet:
    # The first key encrypts; every key can decrypt, which allows rotation.
    return MultiFernet([Fernet(key.strip().encode()) for key in keys.split(",") if key.strip()])


def _cipher() -> MultiFernet:
    key = get_settings().token_encryption_key
    if key is None or not key.get_secret_value().strip():
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is not set. Add it to your .env file.")
    return _fernet(key.get_secret_value())


def encrypt(value: str) -> str:
    return _cipher().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _cipher().decrypt(token.encode()).decode()
    except InvalidToken:
        raise DecryptionError("Stored secret could not be decrypted with the configured keys.") from None
