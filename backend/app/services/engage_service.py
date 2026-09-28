"""Engagement: poll connected Pages for new comments and messages, draft replies,
and send them only with approval (or automatically when the mode and the AI both
say the reply is safe).

No webhooks: polling works without a public server and without Meta app review.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.prompts import engage as engage_prompts
from app.ai.prompts.brand import brand_context
from app.ai.service import AIContext, AIError, ModelTier
from app.core import crypto
from app.core.database import sync_session
from app.core.errors import AppError
from app.integrations import facebook
from app.models import Brand, Platform, Post, PostStatus, User
from app.models.engage import EngageMode, EngageSettings, InboxItem, InboxKind, InboxStatus
from app.models.social_account import SocialAccount
from app.services import brand_service, notification_service, social_service, topic_service

logger = logging.getLogger(__name__)

POLL_INTERVAL = timedelta(minutes=9)
# Comments are read from posts published in this window.
COMMENT_WINDOW = timedelta(days=30)
MAX_POSTS_PER_POLL = 20
# Facebook only allows Messenger replies within 24h of the person's last message.
MESSAGE_REPLY_WINDOW = timedelta(hours=24)
MAX_REPLY_CHARS = 1000


def enqueue_poll(brand_id: uuid.UUID) -> None:
    from app.workers.engage_tasks import poll_brand

    poll_brand.delay(str(brand_id))


def parse_graph_time(value: str) -> datetime | None:
    """Graph timestamps look like 2026-09-26T10:00:00+0000."""
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(value, fmt)
        except (ValueError, TypeError):
            continue
    return None


def tick() -> dict:
    """Beat task body: queue a poll for each enabled brand that is due."""
    now = datetime.now(UTC)
    with sync_session() as db:
        due = db.scalars(
            select(EngageSettings).where(
                EngageSettings.mode != EngageMode.OFF,
                (EngageSettings.last_polled_at.is_(None)) | (EngageSettings.last_polled_at <= now - POLL_INTERVAL),
            )
        ).all()
        brand_ids = []
        for settings in due:
            settings.last_polled_at = now
            brand_ids.append(settings.brand_id)
    for brand_id in brand_ids:
        enqueue_poll(brand_id)
    return {"queued": len(brand_ids)}


def _facebook_client(db, brand_id: uuid.UUID):
    """(client, page_id) for the brand's usable Facebook connection, or (None, reason)."""
    account = db.scalar(
        select(SocialAccount).where(SocialAccount.brand_id == brand_id, SocialAccount.platform == Platform.FACEBOOK)
    )
    if account is None or not social_service.is_usable(account):
        return None, "Facebook isn't connected. Connect it under Settings."
    try:
        token = crypto.decrypt(account.access_token_encrypted)
    except crypto.DecryptionError:
        return None, "The saved Facebook login can't be read. Reconnect Facebook."
    return facebook.FacebookClient(token), account.external_account_id


def _draft(brand: Brand, settings: EngageSettings, *, kind: str, author: str, text: str, post_text: str | None):
    return asyncio.run(
        topic_service.ai_service_factory().generate_structured(
            task="engage_reply",
            prompt=engage_prompts.reply_prompt(
                kind=kind,
                brand_context=brand_context(brand),
                business_facts=settings.business_facts,
                post_text=post_text,
                author_name=author,
                text=text,
                language=brand.language,
            ),
            schema=engage_prompts.ReplyDraft,
            system=engage_prompts.SYSTEM,
            context=AIContext(user_id=brand.user_id, brand_id=brand.id),
            tier=ModelTier.FAST,
            max_output_tokens=2048,
            thinking_level="minimal",
        )
    )


def _auto_sends_today(db, brand_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count(InboxItem.id)).where(
                InboxItem.brand_id == brand_id,
                InboxItem.sent_automatically.is_(True),
                InboxItem.replied_at > datetime.now(UTC) - timedelta(hours=24),
            )
        )
        or 0
    )


def _known_ids(db, brand_id: uuid.UUID, external_ids: list[str]) -> set[str]:
    if not external_ids:
        return set()
    rows = db.scalars(
        select(InboxItem.external_id).where(InboxItem.brand_id == brand_id, InboxItem.external_id.in_(external_ids))
    )
    return set(rows)


def poll_brand(brand_id: uuid.UUID) -> dict:
    """Worker body: fetch new comments/messages for one brand, draft, and maybe send."""
    with sync_session() as db:
        settings = db.scalar(select(EngageSettings).where(EngageSettings.brand_id == brand_id))
        if settings is None or settings.mode == EngageMode.OFF:
            return {"status": "skipped"}
        brand = db.get(Brand, brand_id)
        client, page = _facebook_client(db, brand_id)
        if client is None:
            settings.last_error = page
            return {"status": "skipped", "error": page}
        page_id = page
        anchor = settings.activated_at or settings.created_at

        # What to look at: our recently published Facebook posts, for their comments.
        posts = db.scalars(
            select(Post)
            .where(
                Post.brand_id == brand_id,
                Post.platform == Platform.FACEBOOK,
                Post.status == PostStatus.PUBLISHED,
                Post.external_post_id.is_not(None),
                Post.published_at >= datetime.now(UTC) - COMMENT_WINDOW,
            )
            .order_by(Post.published_at.desc())
            .limit(MAX_POSTS_PER_POLL)
        ).all()
        post_map = {p.external_post_id: (p.id, p.hook, p.body) for p in posts}

    new_items: list[dict] = []
    error: str | None = None

    if settings.reply_to_comments:
        for external_post_id, (our_post_id, hook, body) in post_map.items():
            try:
                fetched = asyncio.run(client.comments(external_post_id))
            except facebook.FacebookError as exc:
                if exc.code == "REJECTED":
                    continue  # the post was deleted on Facebook; nothing to read there
                error = f"{exc.code}: {exc.message}"[:500]
                break
            for c in fetched:
                created = parse_graph_time(c["created_time"])
                if c["parent_id"] or not c["message"].strip():
                    continue  # only top-level comments with text
                if c["author_id"] == page_id:
                    continue  # the Page's own comments
                if created is None or created <= anchor:
                    continue
                new_items.append(
                    {
                        "kind": InboxKind.COMMENT,
                        "external_id": c["id"],
                        "thread_external_id": external_post_id,
                        "post_id": our_post_id,
                        "author_external_id": c["author_id"],
                        "author_name": c["author_name"],
                        "text": c["message"],
                        "received_at": created,
                        "post_text": f"{hook}\n\n{body}",
                    }
                )
    if settings.reply_to_messages and error is None:
        try:
            convos = asyncio.run(client.conversations(page_id))
        except facebook.FacebookError as exc:
            convos = []
            error = f"{exc.code}: {exc.message}"[:500]
        for convo in convos:
            messages = convo["messages"]
            if not messages:
                continue
            latest = messages[0]  # newest first
            if latest["author_id"] == page_id:
                continue  # we (or the owner) already have the last word
            created = parse_graph_time(latest["created_time"])
            if created is None or created <= anchor or not latest["message"].strip():
                continue
            new_items.append(
                {
                    "kind": InboxKind.MESSAGE,
                    "external_id": latest["id"],
                    "thread_external_id": convo["id"],
                    "post_id": None,
                    "author_external_id": latest["author_id"],
                    "author_name": latest["author_name"],
                    "text": latest["message"],
                    "received_at": created,
                    "post_text": None,
                }
            )

    with sync_session() as db:
        known = _known_ids(db, brand_id, [i["external_id"] for i in new_items])
    new_items = [i for i in new_items if i["external_id"] not in known]

    created_review, sent, skipped = 0, 0, 0
    for item in new_items:
        with sync_session() as db:
            brand = db.get(Brand, brand_id)
            settings = db.scalar(select(EngageSettings).where(EngageSettings.brand_id == brand_id))
            row = InboxItem(
                brand_id=brand_id,
                platform=Platform.FACEBOOK,
                kind=item["kind"],
                external_id=item["external_id"],
                thread_external_id=item["thread_external_id"],
                post_id=item["post_id"],
                author_external_id=item["author_external_id"],
                author_name=item["author_name"][:200],
                text=item["text"],
                received_at=item["received_at"],
            )
            try:
                draft = _draft(
                    brand, settings, kind=item["kind"], author=item["author_name"], text=item["text"], post_text=item["post_text"]
                )
                row.assessment = draft.model_dump(exclude={"reply"})
                if not draft.should_reply:
                    row.status = InboxStatus.SKIPPED
                else:
                    row.draft_reply = draft.reply.strip()[:MAX_REPLY_CHARS]
            except AIError as exc:
                # Still surface the comment; the person writes the reply themselves.
                row.assessment = {"needs_human": True, "reasons": [f"AI draft failed: {exc.code}"]}
            db.add(row)
            try:
                db.flush()
            except IntegrityError:
                continue  # another worker got there first

            may_auto = (
                settings.mode == EngageMode.AUTO
                and row.status == InboxStatus.REVIEW
                and row.draft_reply
                and not row.assessment.get("needs_human", True)
                and _auto_sends_today(db, brand_id) < settings.max_replies_per_day
            )
            if may_auto:
                try:
                    _send(client, page_id, row, row.draft_reply)
                    row.sent_automatically = True
                    sent += 1
                except facebook.FacebookError as exc:
                    row.error = f"{exc.code}: {exc.message}"[:500]
                    error = error or row.error
            if row.status == InboxStatus.REVIEW:
                created_review += 1
            elif row.status == InboxStatus.SKIPPED:
                skipped += 1

    with sync_session() as db:
        settings = db.scalar(select(EngageSettings).where(EngageSettings.brand_id == brand_id))
        settings.last_error = error
        if created_review:
            notification_service.notify(
                db,
                brand_id=brand_id,
                type="engage_review",
                title=f"{created_review} repl{'y' if created_review == 1 else 'ies'} waiting for your review",
                message="New comments or messages have drafted replies in the inbox.",
            )
    logger.info(
        "engage_polled",
        extra={"brand_id": str(brand_id), "review": created_review, "auto_sent": sent, "skipped": skipped, "error": error},
    )
    return {"status": "ok", "review": created_review, "auto_sent": sent, "skipped": skipped, "error": error}


def _send(client: facebook.FacebookClient, page_id: str, row: InboxItem, reply: str) -> None:
    """Sends and marks the row. Raises FacebookError on failure."""
    reply = reply.strip()[:MAX_REPLY_CHARS]
    if not reply:
        raise facebook.FacebookError("EMPTY_REPLY", "The reply is empty.")
    if row.kind == InboxKind.MESSAGE:
        if not row.author_external_id:
            raise facebook.FacebookError("NO_RECIPIENT", "Facebook hid the sender of this message, so it can't be answered from here.")
        if row.received_at < datetime.now(UTC) - MESSAGE_REPLY_WINDOW:
            raise facebook.FacebookError("WINDOW_CLOSED", "Facebook only allows replies within 24 hours of the person's message.")
        external = asyncio.run(client.send_message(page_id, row.author_external_id, reply))
    else:
        external = asyncio.run(client.reply_to_comment(row.external_id, reply))
    row.status = InboxStatus.SENT
    row.sent_reply = reply
    row.sent_reply_external_id = external or None
    row.replied_at = datetime.now(UTC)
    row.error = None


# --- API-facing (async) ----------------------------------------------------------------


async def get_settings(db: AsyncSession, user: User, brand_id: uuid.UUID) -> EngageSettings:
    await brand_service.get_brand(db, user, brand_id)
    settings = await db.scalar(select(EngageSettings).where(EngageSettings.brand_id == brand_id))
    if settings is None:
        settings = EngageSettings(brand_id=brand_id)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


async def update_settings(db: AsyncSession, user: User, brand_id: uuid.UUID, changes: dict) -> EngageSettings:
    settings = await get_settings(db, user, brand_id)
    turning_on = changes.get("mode") in (EngageMode.REVIEW, EngageMode.AUTO) and settings.mode == EngageMode.OFF
    if changes.get("mode") in (EngageMode.REVIEW, EngageMode.AUTO):
        account = await db.scalar(
            select(SocialAccount).where(SocialAccount.brand_id == brand_id, SocialAccount.platform == Platform.FACEBOOK)
        )
        if account is None or not social_service.is_usable(account):
            raise AppError("ACCOUNT_NOT_CONNECTED", "Connect Facebook before turning replies on.", 409)
    for key, value in changes.items():
        setattr(settings, key, value)
    if turning_on:
        # Only respond to what arrives from now on.
        settings.activated_at = datetime.now(UTC)
        settings.last_error = None
    await db.commit()
    await db.refresh(settings)
    return settings


async def list_inbox(
    db: AsyncSession, user: User, brand_id: uuid.UUID, *, status: InboxStatus | None, limit: int, offset: int
) -> list[InboxItem]:
    await brand_service.get_brand(db, user, brand_id)
    stmt = select(InboxItem).where(InboxItem.brand_id == brand_id)
    if status is not None:
        stmt = stmt.where(InboxItem.status == status)
    stmt = stmt.order_by(InboxItem.received_at.desc()).limit(limit).offset(offset)
    return list(await db.scalars(stmt))


async def _get_item(db: AsyncSession, user: User, item_id: uuid.UUID) -> InboxItem:
    item = await db.get(InboxItem, item_id)
    if item is None:
        raise AppError("NOT_FOUND", "This inbox item doesn't exist.", 404)
    await brand_service.get_brand(db, user, item.brand_id)
    return item


async def send_reply(db: AsyncSession, user: User, item_id: uuid.UUID, reply: str | None) -> InboxItem:
    """Approve and send, optionally with an edited reply. Runs in the request so the
    user sees the result immediately."""
    item = await _answerable_item(db, user, item_id)
    text = (reply if reply is not None else item.draft_reply).strip()
    client, page = await _facebook_client_async(db, item.brand_id)
    try:
        # _send is sync bookkeeping around async Graph calls; unwrap it here.
        text = text[:MAX_REPLY_CHARS]
        if not text:
            raise facebook.FacebookError("EMPTY_REPLY", "Write a reply first.")
        if item.kind == InboxKind.MESSAGE:
            if not item.author_external_id:
                raise facebook.FacebookError("NO_RECIPIENT", "Facebook hid the sender of this message, so it can't be answered from here.")
            if item.received_at < datetime.now(UTC) - MESSAGE_REPLY_WINDOW:
                raise facebook.FacebookError("WINDOW_CLOSED", "Facebook only allows replies within 24 hours of the person's message.")
            external = await client.send_message(page, item.author_external_id, text)
        else:
            external = await client.reply_to_comment(item.external_id, text)
    except facebook.FacebookError as exc:
        item.error = f"{exc.code}: {exc.message}"[:500]
        await db.commit()
        raise AppError(exc.code, exc.message, 502 if exc.retryable else 409) from None
    item.status = InboxStatus.SENT
    item.sent_reply = text
    item.sent_reply_external_id = external or None
    item.sent_automatically = False
    item.replied_at = datetime.now(UTC)
    item.error = None
    await db.commit()
    await db.refresh(item)
    return item


async def _answerable_item(db: AsyncSession, user: User, item_id: uuid.UUID) -> InboxItem:
    item = await _get_item(db, user, item_id)
    if item.status == InboxStatus.SENT:
        raise AppError("ALREADY_SENT", "This one has already been answered.", 409)
    return item


async def _facebook_client_async(db: AsyncSession, brand_id: uuid.UUID):
    account = await db.scalar(
        select(SocialAccount).where(SocialAccount.brand_id == brand_id, SocialAccount.platform == Platform.FACEBOOK)
    )
    if account is None or not social_service.is_usable(account):
        raise AppError("ACCOUNT_NOT_CONNECTED", "Facebook isn't connected. Connect it under Settings.", 409)
    try:
        token = crypto.decrypt(account.access_token_encrypted)
    except crypto.DecryptionError:
        raise AppError("RECONNECT_REQUIRED", "The saved Facebook login can't be read. Reconnect Facebook.", 409) from None
    return facebook.FacebookClient(token), account.external_account_id


async def dismiss(db: AsyncSession, user: User, item_id: uuid.UUID) -> InboxItem:
    item = await _answerable_item(db, user, item_id)
    item.status = InboxStatus.DISMISSED
    await db.commit()
    await db.refresh(item)
    return item


async def poll_now(db: AsyncSession, user: User, brand_id: uuid.UUID) -> None:
    settings = await get_settings(db, user, brand_id)
    if settings.mode == EngageMode.OFF:
        raise AppError("ENGAGE_OFF", "Turn replies on first.", 409)
    settings.last_polled_at = datetime.now(UTC)
    await db.commit()
    enqueue_poll(brand_id)
