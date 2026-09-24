import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.brand import BrandCreate, BrandRead, BrandUpdate
from app.services import brand_service

router = APIRouter(prefix="/brands", tags=["brands"])


@router.get("")
async def list_brands(user: CurrentUser, db: DbSession) -> list[BrandRead]:
    brands = await brand_service.list_brands(db, user)
    return [BrandRead.model_validate(b) for b in brands]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_brand(body: BrandCreate, user: CurrentUser, db: DbSession) -> BrandRead:
    return BrandRead.model_validate(await brand_service.create_brand(db, user, body))


@router.get("/{brand_id}")
async def get_brand(brand_id: uuid.UUID, user: CurrentUser, db: DbSession) -> BrandRead:
    return BrandRead.model_validate(await brand_service.get_brand(db, user, brand_id))


@router.patch("/{brand_id}")
async def update_brand(brand_id: uuid.UUID, body: BrandUpdate, user: CurrentUser, db: DbSession) -> BrandRead:
    return BrandRead.model_validate(await brand_service.update_brand(db, user, brand_id, body))


@router.delete("/{brand_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_brand(brand_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    await brand_service.delete_brand(db, user, brand_id)
