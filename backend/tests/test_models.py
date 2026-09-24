import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import Brand, User


def make_user(email: str = "founder@example.com") -> User:
    return User(name="Founder", email=email, password_hash="not-a-real-hash")


def test_user_defaults(db_session):
    user = make_user()
    db_session.add(user)
    db_session.flush()
    db_session.refresh(user)

    assert user.id is not None
    assert user.email_verified is False
    assert user.is_active is True
    assert user.created_at is not None and user.updated_at is not None


def test_email_is_normalized(db_session):
    user = make_user("  Founder@Example.COM ")
    db_session.add(user)
    db_session.flush()
    assert user.email == "founder@example.com"


def test_duplicate_email_rejected_regardless_of_case(db_session):
    db_session.add(make_user("founder@example.com"))
    db_session.flush()

    db_session.add(make_user("FOUNDER@example.com"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_brand_kit_round_trip(db_session):
    user = make_user()
    brand = Brand(
        user=user,
        name="Squareko",
        industry="Squarespace Web Design",
        goals=["lead generation"],
        tone=["professional", "conversational", "clear"],
        visual_style=["minimal", "editorial"],
        primary_color="#07142F",
        secondary_color="#6E5BFF",
        accent_color="#4BA8FF",
        heading_font="Inter",
        body_font="Inter",
        banned_words=["synergy"],
    )
    db_session.add(brand)
    db_session.flush()
    db_session.expire_all()

    loaded = db_session.scalars(select(Brand).where(Brand.name == "Squareko")).one()
    assert loaded.tone == ["professional", "conversational", "clear"]
    assert loaded.banned_words == ["synergy"]
    assert loaded.preferred_words == []
    assert loaded.user.email == "founder@example.com"


@pytest.mark.parametrize("color", ["red", "#FFF", "#GGGGGG", "07142F"])
def test_invalid_brand_color_rejected(db_session, color):
    db_session.add(Brand(user=make_user(), name="Bad colors", primary_color=color))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_brand_requires_existing_user(db_session):
    import uuid

    db_session.add(Brand(user_id=uuid.uuid4(), name="Orphan"))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_deleting_user_deletes_their_brands(db_session):
    user = make_user()
    db_session.add_all([Brand(user=user, name="One"), Brand(user=user, name="Two")])
    db_session.flush()

    db_session.delete(user)
    db_session.flush()
    assert db_session.scalars(select(Brand)).all() == []
