import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.security import (
    JWT_ALGORITHM,
    create_access_token,
    decode_access_token,
    hash_password,
    hash_token,
    verify_password,
)


def test_password_hash_round_trip():
    hashed = hash_password("correct horse battery")
    assert hashed.startswith("$argon2id$")
    assert "correct horse battery" not in hashed
    assert verify_password("correct horse battery", hashed)
    assert not verify_password("wrong password", hashed)


def test_verify_rejects_garbage_hash():
    assert verify_password("anything", "not-a-hash") is False


def test_access_token_round_trip():
    user_id = uuid.uuid4()
    assert decode_access_token(create_access_token(user_id)) == user_id


def test_access_token_rejects_tampering_and_expiry():
    user_id = uuid.uuid4()
    secret = "test-secret-that-is-long-enough-for-hs256-signing"
    now = datetime.now(UTC)

    expired = jwt.encode(
        {"sub": str(user_id), "type": "access", "iat": now - timedelta(hours=1), "exp": now - timedelta(minutes=1)},
        secret,
        algorithm=JWT_ALGORITHM,
    )
    wrong_key = jwt.encode(
        {"sub": str(user_id), "type": "access", "iat": now, "exp": now + timedelta(minutes=5)},
        "some-other-secret-that-is-also-long-enough",
        algorithm=JWT_ALGORITHM,
    )
    wrong_type = jwt.encode(
        {"sub": str(user_id), "type": "refresh", "iat": now, "exp": now + timedelta(minutes=5)},
        secret,
        algorithm=JWT_ALGORITHM,
    )
    unsigned = jwt.encode(
        {"sub": str(user_id), "type": "access", "iat": now, "exp": now + timedelta(minutes=5)},
        None,
        algorithm="none",
    )

    for token in (expired, wrong_key, wrong_type, unsigned, "not.a.jwt"):
        assert decode_access_token(token) is None


def test_hash_token_is_deterministic_and_hides_value():
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("abd")
    assert len(hash_token("abc")) == 64


@pytest.mark.parametrize("env", ["staging", "production"])
def test_weak_jwt_secret_rejected_outside_dev(env):
    with pytest.raises(ValidationError):
        Settings(app_env=env, jwt_secret="short")
    Settings(app_env=env, jwt_secret="x" * 32)
