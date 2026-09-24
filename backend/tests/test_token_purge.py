import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import RefreshToken, User
from app.services.auth_service import purge_stale_refresh_tokens


def test_purge_removes_only_stale_tokens(db_session):
    user = User(name="U", email="u@example.com", password_hash="x")
    db_session.add(user)
    db_session.flush()

    now = datetime.now(UTC)

    def token(label, expires_in, revoked_ago=None):
        return RefreshToken(
            user_id=user.id,
            family_id=uuid.uuid4(),
            token_hash=label.ljust(64, "0"),
            expires_at=now + expires_in,
            revoked_at=now - revoked_ago if revoked_ago else None,
        )

    db_session.add_all([
        token("live", timedelta(days=10)),
        token("just-revoked", timedelta(days=10), revoked_ago=timedelta(hours=1)),
        token("long-expired", -timedelta(days=3)),
        token("long-revoked", timedelta(days=10), revoked_ago=timedelta(days=3)),
    ])
    db_session.flush()

    assert purge_stale_refresh_tokens(db_session) == 2
    remaining = {t.token_hash.rstrip("0") for t in db_session.scalars(select(RefreshToken))}
    assert remaining == {"live", "just-revoked"}
