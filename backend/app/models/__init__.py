# Import every model here so Base.metadata (and Alembic) sees all tables.
from app.models.analytics import AnalyticsSync, PostMetric, SyncStatus
from app.models.autopilot import DEFAULT_RULES, AutopilotMode, AutopilotRun, AutopilotRunStatus, AutopilotSettings
from app.models.engage import EngageMode, EngageSettings, InboxItem, InboxKind, InboxStatus
from app.models.ai_run import AIRun, AIRunStatus
from app.models.brand import Brand
from app.models.content_pillar import BrandContentPillar, ContentPillar
from app.models.notification import Notification
from app.models.opportunity import (
    ContentFormat,
    ContentOpportunity,
    OpportunityRun,
    OpportunityRunStatus,
    OpportunityStatus,
    Platform,
)
from app.models.post import (
    EDITABLE_STATUSES,
    ContentGeneration,
    GenerationStatus,
    Post,
    PostRevision,
    PostStatus,
    PostVersion,
    RevisionAction,
)
from app.models.refresh_token import RefreshToken
from app.models.research import ResearchContentType, ResearchItem, ResearchRun, RunStatus, RunTrigger
from app.models.social_account import SocialAccount, SocialAccountStatus
from app.models.source import FetchFrequency, Source, SourceStatus, SourceType
from app.models.topic import ResearchItemTopic, Topic
from app.models.user import User
from app.models.visual import RENDERED_TYPES, Visual, VisualStatus, VisualType

__all__ = [
    "AIRun",
    "AIRunStatus",
    "AnalyticsSync",
    "AutopilotMode",
    "AutopilotRun",
    "AutopilotRunStatus",
    "AutopilotSettings",
    "Brand",
    "BrandContentPillar",
    "ContentFormat",
    "ContentGeneration",
    "ContentOpportunity",
    "ContentPillar",
    "DEFAULT_RULES",
    "EDITABLE_STATUSES",
    "FetchFrequency",
    "GenerationStatus",
    "Notification",
    "OpportunityRun",
    "OpportunityRunStatus",
    "OpportunityStatus",
    "Platform",
    "Post",
    "PostMetric",
    "PostRevision",
    "PostStatus",
    "PostVersion",
    "RENDERED_TYPES",
    "RefreshToken",
    "ResearchContentType",
    "ResearchItem",
    "ResearchItemTopic",
    "ResearchRun",
    "RevisionAction",
    "RunStatus",
    "RunTrigger",
    "SocialAccount",
    "SocialAccountStatus",
    "Source",
    "SourceStatus",
    "SourceType",
    "SyncStatus",
    "Topic",
    "EngageMode",
    "EngageSettings",
    "InboxItem",
    "InboxKind",
    "InboxStatus",
    "User",
    "Visual",
    "VisualStatus",
    "VisualType",
]
