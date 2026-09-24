"""Research runs and research items.

Worker side (sync): execute a run, deduplicate and store what it collected,
schedule due sources. API side (async): start runs, read results. Every API
query is scoped to the brand owner.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.exceptions import LockError, RedisError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core import rate_limit
from app.core.database import sync_session
from app.core.errors import AppError
from app.core.redis import get_sync_redis
from app.models import Brand, ResearchItem, ResearchRun, RunStatus, RunTrigger, Source, SourceStatus, User
from app.models.research import ACTIVE_RUN_STATUSES, ResearchContentType
from app.models.source import FETCH_INTERVALS, FETCHABLE_SOURCE_TYPES
from app.research import pipeline
from app.research.deduplication import (
    NEAR_DUPLICATE_MIN_CHARS,
    content_hash,
    is_near_duplicate,
    simhash,
)
from app.research.fetcher import PER_HOST_LIMIT, FetchError, SafeFetcher
from app.research.parser import ParseError
from app.services import brand_service, source_service

logger = logging.getLogger(__name__)

# A run stuck longer than this (worker died, message lost) no longer blocks new runs.
STALE_RUN_AFTER = timedelta(minutes=30)
NEAR_DUPLICATE_WINDOW = timedelta(days=90)
MAX_BACKOFF_FACTOR = 16
SCHEDULE_BATCH = 100
LOCK_SECONDS = 15 * 60


# --- Worker side -------------------------------------------------------------


async def _worker_host_limiter(host: str) -> bool:
    # Workers use the sync Redis client: each task runs its own short-lived event loop.
    return rate_limit.allow_sync(f"fetch:host:{host}", *PER_HOST_LIMIT)


def fetcher_factory() -> SafeFetcher:
    return SafeFetcher(host_limiter=_worker_host_limiter)


@dataclass
class StoreCounts:
    new: int = 0
    updated: int = 0
    duplicate: int = 0


def execute_run(run_id: uuid.UUID) -> dict:
    """Runs one research job end to end. Safe to call again for the same run."""
    with sync_session() as db:
        run = db.get(ResearchRun, run_id)
        # RUNNING is allowed: the message is redelivered if a worker dies mid-run.
        if run is None or run.status not in ACTIVE_RUN_STATUSES:
            return {"status": "skipped"}
        source = db.get(Source, run.source_id)
        run.status = RunStatus.RUNNING
        run.started_at = datetime.now(UTC)
        brand_id, source_id = run.brand_id, run.source_id
        source_url, source_type = source.url, source.source_type
        known_urls = set(db.scalars(select(ResearchItem.canonical_url).where(ResearchItem.brand_id == brand_id)))

    lock = get_sync_redis().lock(f"lock:research:source:{source_id}", timeout=LOCK_SECONDS)
    try:
        if not lock.acquire(blocking=False):
            return _finish_failed(run_id, "Another fetch of this source is already in progress.", count_error=False)
    except RedisError:
        lock = None  # Redis down: carry on without the lock rather than stall research.

    try:
        collection = asyncio.run(pipeline.collect(source_url, source_type, fetcher_factory(), known_urls))
    except FetchError as exc:
        return _finish_failed(run_id, exc.message)
    except ParseError as exc:
        return _finish_failed(run_id, str(exc))
    except Exception:
        logger.exception("research_run_crashed", extra={"run_id": str(run_id)})
        _finish_failed(run_id, "Unexpected error while collecting research.")
        raise
    finally:
        if lock is not None:
            try:
                lock.release()
            except (LockError, RedisError):
                pass

    with sync_session() as db:
        counts = store_items(db, brand_id, source_id, collection.items)
        counts.duplicate += collection.known_skipped
        run = db.get(ResearchRun, run_id)
        run.status = RunStatus.SUCCEEDED
        run.items_found = len(collection.items) + collection.known_skipped
        run.items_new, run.items_updated, run.items_duplicate = counts.new, counts.updated, counts.duplicate
        run.finished_at = datetime.now(UTC)
        _mark_source(db.get(Source, source_id), ok=True)

    result = {"status": "succeeded", "new": counts.new, "updated": counts.updated, "duplicate": counts.duplicate}
    logger.info("research_run_finished", extra={"run_id": str(run_id), **result})
    return result


def _finish_failed(run_id: uuid.UUID, message: str, *, count_error: bool = True) -> dict:
    with sync_session() as db:
        run = db.get(ResearchRun, run_id)
        run.status = RunStatus.FAILED
        run.error = message[:500]
        run.finished_at = datetime.now(UTC)
        if count_error:
            _mark_source(db.get(Source, run.source_id), ok=False, error=message)
    logger.info("research_run_failed", extra={"run_id": str(run_id), "error": message})
    return {"status": "failed", "error": message}


def _mark_source(source: Source | None, *, ok: bool, error: str | None = None) -> None:
    if source is None:
        return
    now = datetime.now(UTC)
    interval = FETCH_INTERVALS[source.fetch_frequency]
    if ok:
        source.status = SourceStatus.OK
        source.error_count = 0
        source.last_error = None
        source.last_fetched_at = now
        source.next_fetch_at = now + interval
    else:
        source.status = SourceStatus.ERROR
        source.error_count += 1
        source.last_error = (error or "")[:500] or None
        # Failing sources are retried less and less often.
        source.next_fetch_at = now + interval * min(2 ** (source.error_count - 1), MAX_BACKOFF_FACTOR)


def store_items(
    db: Session, brand_id: uuid.UUID, source_id: uuid.UUID | None, items: list[pipeline.CollectedItem]
) -> StoreCounts:
    """Deduplicate (spec section 26) and store. Same URL: update if the text changed."""
    counts = StoreCounts()
    now = datetime.now(UTC)
    recent = list(
        db.execute(
            select(ResearchItem.id, ResearchItem.simhash).where(
                ResearchItem.brand_id == brand_id,
                ResearchItem.fetched_at >= now - NEAR_DUPLICATE_WINDOW,
                func.length(ResearchItem.clean_text) >= NEAR_DUPLICATE_MIN_CHARS,
            )
        )
    )

    for item in items:
        digest = content_hash(item.text)
        existing = db.scalar(
            select(ResearchItem).where(ResearchItem.brand_id == brand_id, ResearchItem.canonical_url == item.url)
        )
        if existing is not None:
            if existing.content_hash == digest:
                counts.duplicate += 1
            else:
                _apply(existing, item, digest, now)
                counts.updated += 1
            continue

        if db.scalar(
            select(ResearchItem.id).where(ResearchItem.brand_id == brand_id, ResearchItem.content_hash == digest)
        ):
            counts.duplicate += 1
            continue

        fingerprint = simhash(item.text)
        if len(item.text) >= NEAR_DUPLICATE_MIN_CHARS and any(
            is_near_duplicate(fingerprint, other) for _, other in recent
        ):
            counts.duplicate += 1
            continue

        record = ResearchItem(brand_id=brand_id, source_id=source_id, canonical_url=item.url)
        _apply(record, item, digest, now, fingerprint)
        db.add(record)
        db.flush()
        if len(item.text) >= NEAR_DUPLICATE_MIN_CHARS:
            recent.append((record.id, fingerprint))
        counts.new += 1
    return counts


def _apply(
    record: ResearchItem, item: pipeline.CollectedItem, digest: str, now: datetime, fingerprint: int | None = None
) -> None:
    record.title = item.title[:300]
    record.author = item.author
    record.published_at = item.published_at
    record.fetched_at = now
    record.summary = item.summary
    record.clean_text = item.text
    record.content_type = ResearchContentType(item.content_type)
    record.content_hash = digest
    record.simhash = fingerprint if fingerprint is not None else simhash(item.text)
    record.source_metadata = item.metadata


def schedule_due_runs(db: Session) -> list[uuid.UUID]:
    """Creates queued runs for active sources whose next fetch is due."""
    now = datetime.now(UTC)
    due = db.scalars(
        select(Source)
        .where(
            Source.active.is_(True),
            Source.source_type.in_(FETCHABLE_SOURCE_TYPES),
            or_(Source.next_fetch_at.is_(None), Source.next_fetch_at <= now),
        )
        .order_by(Source.next_fetch_at.asc().nulls_first())
        .limit(SCHEDULE_BATCH)
        .with_for_update(skip_locked=True)
    ).all()

    run_ids = []
    for source in due:
        # Push the next check out now, so the next scheduler tick doesn't queue it again.
        source.next_fetch_at = now + FETCH_INTERVALS[source.fetch_frequency]
        if db.scalar(_active_run_query(source.id)):
            continue
        run = ResearchRun(brand_id=source.brand_id, source_id=source.id, trigger=RunTrigger.SCHEDULED)
        db.add(run)
        db.flush()
        run_ids.append(run.id)
    return run_ids


def _active_run_query(source_id: uuid.UUID):
    return select(ResearchRun).where(
        ResearchRun.source_id == source_id,
        ResearchRun.status.in_(ACTIVE_RUN_STATUSES),
        ResearchRun.created_at > datetime.now(UTC) - STALE_RUN_AFTER,
    )


def enqueue_run(run_id: uuid.UUID) -> None:
    from app.workers.research_tasks import fetch_source  # tasks import this module

    fetch_source.delay(str(run_id))


# --- API side ----------------------------------------------------------------


async def start_source_fetch(db: AsyncSession, user: User, source_id: uuid.UUID) -> ResearchRun:
    source = await source_service.get_source(db, user, source_id)
    return await _start_run(db, source)


async def start_brand_research(db: AsyncSession, user: User, brand_id: uuid.UUID) -> list[ResearchRun]:
    await brand_service.get_brand(db, user, brand_id)
    sources = await db.scalars(
        select(Source).where(
            Source.brand_id == brand_id,
            Source.active.is_(True),
            Source.source_type.in_(FETCHABLE_SOURCE_TYPES),
        )
    )
    return [await _start_run(db, source) for source in sources.all()]


async def _start_run(db: AsyncSession, source: Source) -> ResearchRun:
    """Returns the source's in-flight run if there is one, so repeated clicks don't pile up jobs."""
    if source.source_type not in FETCHABLE_SOURCE_TYPES:
        raise AppError("SOURCE_TYPE_NOT_SUPPORTED", "This source type can't be fetched yet.", 422)
    existing = await db.scalar(_active_run_query(source.id))
    if existing is not None:
        return existing

    run = ResearchRun(brand_id=source.brand_id, source_id=source.id, trigger=RunTrigger.MANUAL)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    # Only after commit, so the worker can see the row. The broker call is blocking I/O.
    await run_in_threadpool(enqueue_run, run.id)
    return run


async def list_runs(db: AsyncSession, user: User, brand_id: uuid.UUID, limit: int) -> list[ResearchRun]:
    await brand_service.get_brand(db, user, brand_id)
    result = await db.scalars(
        select(ResearchRun)
        .where(ResearchRun.brand_id == brand_id)
        .order_by(ResearchRun.created_at.desc())
        .limit(limit)
    )
    return list(result)


async def get_run(db: AsyncSession, user: User, run_id: uuid.UUID) -> ResearchRun:
    run = await db.scalar(
        select(ResearchRun).join(Brand).where(ResearchRun.id == run_id, Brand.user_id == user.id)
    )
    if run is None:
        raise AppError("RUN_NOT_FOUND", "Research run not found.", 404)
    return run


async def list_items(
    db: AsyncSession,
    user: User,
    brand_id: uuid.UUID,
    *,
    source_id: uuid.UUID | None,
    query: str | None,
    limit: int,
    offset: int,
) -> list[ResearchItem]:
    await brand_service.get_brand(db, user, brand_id)
    stmt = select(ResearchItem).where(ResearchItem.brand_id == brand_id)
    if source_id is not None:
        stmt = stmt.where(ResearchItem.source_id == source_id)
    if query:
        pattern = f"%{_escape_like(query)}%"
        stmt = stmt.where(
            or_(ResearchItem.title.ilike(pattern, escape="\\"), ResearchItem.summary.ilike(pattern, escape="\\"))
        )
    stmt = stmt.order_by(
        func.coalesce(ResearchItem.published_at, ResearchItem.fetched_at).desc(), ResearchItem.id
    )
    result = await db.scalars(stmt.limit(limit).offset(offset))
    return list(result)


async def get_item(db: AsyncSession, user: User, item_id: uuid.UUID) -> ResearchItem:
    item = await db.scalar(
        select(ResearchItem).join(Brand).where(ResearchItem.id == item_id, Brand.user_id == user.id)
    )
    if item is None:
        raise AppError("RESEARCH_ITEM_NOT_FOUND", "Research item not found.", 404)
    return item


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
