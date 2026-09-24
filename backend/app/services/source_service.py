"""Research sources: management and connectivity checks. Scoped to the brand owner."""

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Brand, Source, SourceStatus, User
from app.models.source import FETCHABLE_SOURCE_TYPES, SourceType
from app.research.cleaner import clean_text
from app.research.fetcher import FetchError, SafeFetcher
from app.research.parser import ParseError, is_feed, parse_feed, parse_page
from app.schemas.source import (
    FeedItemPreview,
    SourceCreate,
    SourceTestError,
    SourceTestResult,
    SourceUpdate,
)
from app.services import brand_service
from app.utils.urls import UnsafeUrlError, check_public_url

logger = logging.getLogger(__name__)

MAX_SOURCES_PER_BRAND = 50
PREVIEW_ITEMS = 5
EXCERPT_CHARS = 500


async def list_sources(db: AsyncSession, user: User, brand_id: uuid.UUID) -> list[Source]:
    await brand_service.get_brand(db, user, brand_id)
    result = await db.scalars(select(Source).where(Source.brand_id == brand_id).order_by(Source.created_at))
    return list(result)


async def get_source(db: AsyncSession, user: User, source_id: uuid.UUID) -> Source:
    source = await db.scalar(
        select(Source).join(Brand).where(Source.id == source_id, Brand.user_id == user.id)
    )
    if source is None:
        raise AppError("SOURCE_NOT_FOUND", "Source not found.", 404)
    return source


async def create_source(db: AsyncSession, user: User, data: SourceCreate) -> Source:
    await brand_service.get_brand(db, user, data.brand_id)
    _check_type_supported(data.source_type)
    url = await _validated_url(data.url)

    count = await db.scalar(select(func.count()).select_from(Source).where(Source.brand_id == data.brand_id))
    if count >= MAX_SOURCES_PER_BRAND:
        raise AppError("SOURCE_LIMIT_REACHED", f"A brand can have up to {MAX_SOURCES_PER_BRAND} sources.", 409)
    await _check_not_duplicate(db, data.brand_id, url)

    source = Source(
        brand_id=data.brand_id,
        name=data.name,
        url=url,
        source_type=data.source_type,
        fetch_frequency=data.fetch_frequency,
        active=data.active,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)
    logger.info("source_created", extra={"user_id": str(user.id), "source_id": str(source.id)})
    return source


async def update_source(db: AsyncSession, user: User, source_id: uuid.UUID, data: SourceUpdate) -> Source:
    source = await get_source(db, user, source_id)
    changes = data.model_dump(exclude_unset=True)

    if "source_type" in changes:
        _check_type_supported(changes["source_type"])
    if "url" in changes:
        url = await _validated_url(changes["url"])
        if url != source.url:
            await _check_not_duplicate(db, source.brand_id, url)
            # A new URL hasn't been checked yet; fetch it at the next scheduler tick.
            source.status = SourceStatus.PENDING
            source.error_count = 0
            source.last_error = None
            source.next_fetch_at = None
        changes["url"] = url

    for field, value in changes.items():
        setattr(source, field, value)
    await db.commit()
    await db.refresh(source)
    return source


async def delete_source(db: AsyncSession, user: User, source_id: uuid.UUID) -> None:
    source = await get_source(db, user, source_id)
    await db.delete(source)
    await db.commit()


async def test_source(db: AsyncSession, user: User, source_id: uuid.UUID, fetcher: SafeFetcher) -> SourceTestResult:
    """Fetches the source now and reports what it contains. Updates the source's health."""
    source = await get_source(db, user, source_id)
    result = await _preview(source.url, fetcher)

    if result.ok:
        source.status = SourceStatus.OK
        source.error_count = 0
        source.last_error = None
    else:
        source.status = SourceStatus.ERROR
        source.error_count += 1
        source.last_error = result.error.message[:500] if result.error else None
    await db.commit()
    return result


async def _preview(url: str, fetcher: SafeFetcher) -> SourceTestResult:
    try:
        fetched = await fetcher.fetch(url)
        if is_feed(fetched):
            feed = parse_feed(fetched)
            return SourceTestResult(
                ok=True,
                final_url=fetched.url,
                content_type=fetched.content_type,
                kind="feed",
                title=feed.title or None,
                items=[
                    FeedItemPreview(title=i.title, url=i.url, published=i.published)
                    for i in feed.items[:PREVIEW_ITEMS]
                ],
            )
        page = parse_page(fetched)
        return SourceTestResult(
            ok=True,
            final_url=fetched.url,
            content_type=fetched.content_type,
            kind="page",
            title=page.title or None,
            excerpt=clean_text(page.text, EXCERPT_CHARS) or None,
            feed_urls=page.feed_urls,
        )
    except FetchError as exc:
        return SourceTestResult(ok=False, error=SourceTestError(code=exc.code, message=exc.message))
    except ParseError as exc:
        return SourceTestResult(ok=False, error=SourceTestError(code="PARSE_ERROR", message=str(exc)))


def _check_type_supported(source_type: SourceType) -> None:
    if source_type not in FETCHABLE_SOURCE_TYPES:
        raise AppError(
            "SOURCE_TYPE_NOT_SUPPORTED",
            f"'{source_type}' sources need an official API integration that isn't available yet. "
            "For a YouTube channel, add its RSS feed as an 'rss' source.",
            422,
        )


async def _validated_url(raw: str) -> str:
    try:
        return (await check_public_url(raw)).url
    except UnsafeUrlError as exc:
        raise AppError(exc.code, exc.message, 422) from None


async def _check_not_duplicate(db: AsyncSession, brand_id: uuid.UUID, url: str) -> None:
    exists = await db.scalar(select(Source.id).where(Source.brand_id == brand_id, Source.url == url))
    if exists:
        raise AppError("SOURCE_EXISTS", "This brand already has a source with this URL.", 409)
