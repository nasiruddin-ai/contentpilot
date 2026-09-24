"""Brand and brand kit management. Every query is scoped to the owning user."""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Brand, BrandContentPillar, User
from app.models.content_pillar import DEFAULT_PILLAR_WEIGHTS
from app.schemas.brand import BrandCreate, BrandUpdate, PillarWeight

logger = logging.getLogger(__name__)


async def list_brands(db: AsyncSession, user: User) -> list[Brand]:
    result = await db.scalars(select(Brand).where(Brand.user_id == user.id).order_by(Brand.created_at))
    return list(result)


async def get_brand(db: AsyncSession, user: User, brand_id: uuid.UUID) -> Brand:
    brand = await db.scalar(select(Brand).where(Brand.id == brand_id, Brand.user_id == user.id))
    if brand is None:
        # Same answer whether it doesn't exist or belongs to someone else.
        raise AppError("BRAND_NOT_FOUND", "Brand not found.", 404)
    return brand


async def create_brand(db: AsyncSession, user: User, data: BrandCreate) -> Brand:
    fields = data.model_dump(exclude={"content_pillars"})
    pillars = (
        data.content_pillars
        if data.content_pillars is not None
        else [PillarWeight(pillar=p, weight=w) for p, w in DEFAULT_PILLAR_WEIGHTS.items()]
    )
    brand = Brand(user_id=user.id, **fields)
    _set_pillars(brand, pillars)
    _check_word_lists(brand)

    db.add(brand)
    await db.commit()
    await db.refresh(brand)
    logger.info("brand_created", extra={"user_id": str(user.id), "brand_id": str(brand.id)})
    return brand


async def update_brand(db: AsyncSession, user: User, brand_id: uuid.UUID, data: BrandUpdate) -> Brand:
    brand = await get_brand(db, user, brand_id)
    changes = data.model_dump(exclude_unset=True, exclude={"content_pillars"})
    for field, value in changes.items():
        setattr(brand, field, value)
    if data.content_pillars is not None:
        _set_pillars(brand, data.content_pillars)
    _check_word_lists(brand)

    await db.commit()
    await db.refresh(brand)
    return brand


async def delete_brand(db: AsyncSession, user: User, brand_id: uuid.UUID) -> None:
    brand = await get_brand(db, user, brand_id)
    await db.delete(brand)
    await db.commit()
    logger.info("brand_deleted", extra={"user_id": str(user.id), "brand_id": str(brand_id)})


def _set_pillars(brand: Brand, pillars: list[PillarWeight]) -> None:
    # Update rows in place rather than replacing the list: SQLAlchemy inserts before it
    # deletes, so re-adding an existing pillar would trip the (brand_id, pillar) unique key.
    existing = {row.pillar: row for row in brand.content_pillars}
    wanted = {p.pillar: p.weight for p in pillars}

    for pillar, row in existing.items():
        if pillar in wanted:
            row.weight = wanted[pillar]
        else:
            brand.content_pillars.remove(row)
    for pillar, weight in wanted.items():
        if pillar not in existing:
            brand.content_pillars.append(BrandContentPillar(pillar=pillar, weight=weight))


def _check_word_lists(brand: Brand) -> None:
    preferred = {w.casefold() for w in brand.preferred_words}
    conflicts = sorted(w for w in brand.banned_words if w.casefold() in preferred)
    if conflicts:
        raise AppError(
            "WORD_LIST_CONFLICT",
            f"Words can't be both preferred and banned: {', '.join(conflicts)}",
            422,
        )
