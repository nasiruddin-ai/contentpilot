"""Engagement: replying to comments and Messenger messages on connected Pages.

New comments and messages are polled from the platform, an AI reply is drafted in the
brand's voice, and by default every reply waits in the inbox for a person. Auto mode
sends only replies the AI marked safe; everything else still waits.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin, str_enum


class EngageMode(StrEnum):
    OFF = "off"
    # Draft replies, but a person approves every one.
    REVIEW = "review"
    # Send safe drafts automatically; hold anything flagged for review.
    AUTO = "auto"


class EngageSettings(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "engage_settings"

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), unique=True)
    mode: Mapped[EngageMode] = mapped_column(str_enum(EngageMode, "engage_mode"), default=EngageMode.OFF, server_default="off")
    reply_to_comments: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    reply_to_messages: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    # Facts the AI may state in replies (opening hours, delivery area, how to order...).
    # It must not answer factual questions from anywhere else.
    business_facts: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Safety cap across comments and messages.
    max_replies_per_day: Mapped[int] = mapped_column(default=20, server_default=text("20"))
    # Only comments/messages that arrive after this are answered, so switching the
    # feature on doesn't reply to a year-old thread.
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Why the last poll couldn't do its job (usually a missing Facebook permission).
    last_error: Mapped[str | None] = mapped_column(String(500))


class InboxKind(StrEnum):
    COMMENT = "comment"
    MESSAGE = "message"


class InboxStatus(StrEnum):
    # Draft ready (or empty), waiting for a person.
    REVIEW = "review"
    SENT = "sent"
    DISMISSED = "dismissed"
    # The AI decided no reply is appropriate (spam, abuse, nothing to add).
    SKIPPED = "skipped"
    FAILED = "failed"


# Precomputed: inside InboxItem's class body the `text` column shadows sqlalchemy.text.
_EMPTY_JSON = text("'{}'::jsonb")
_FALSE = text("false")


class InboxItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One incoming comment or Messenger message, with the drafted reply and its fate."""

    __tablename__ = "inbox_items"
    __table_args__ = (UniqueConstraint("brand_id", "platform", "external_id"),)

    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(20), default="facebook", server_default="facebook")
    kind: Mapped[InboxKind] = mapped_column(str_enum(InboxKind, "inbox_kind"))
    # The platform's ID for the comment or message.
    external_id: Mapped[str] = mapped_column(String(200))
    # The Page post (comments) or conversation (messages) it belongs to.
    thread_external_id: Mapped[str | None] = mapped_column(String(200))
    # Our post, when the comment is on a post ContentPilot published.
    post_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("posts.id", ondelete="SET NULL"))
    author_external_id: Mapped[str | None] = mapped_column(String(200))
    author_name: Mapped[str] = mapped_column(String(200), default="", server_default="")
    text: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    status: Mapped[InboxStatus] = mapped_column(str_enum(InboxStatus, "inbox_status"), default=InboxStatus.REVIEW, server_default="review")
    draft_reply: Mapped[str] = mapped_column(Text, default="", server_default="")
    # What the AI concluded: category, needs_human, reasons, should_reply.
    assessment: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=_EMPTY_JSON)
    # Reply as actually sent (possibly edited by the user), and the platform's ID for it.
    sent_reply: Mapped[str | None] = mapped_column(Text)
    sent_reply_external_id: Mapped[str | None] = mapped_column(String(200))
    sent_automatically: Mapped[bool] = mapped_column(default=False, server_default=_FALSE)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(500))

    def __repr__(self) -> str:
        return f"<InboxItem {self.id} {self.kind} {self.status}>"
