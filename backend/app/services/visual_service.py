"""Visual generation (spec section 42):

Post → visual agent (on-image copy) → layout → render → storage → brand check → post.
"""

import asyncio
import io
import logging
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.ai.prompts import visual as visual_prompts
from app.ai.prompts.visual import VisualConcept
from app.ai.service import AIContext, AIError, ModelTier
from app.core.database import sync_session
from app.core.errors import AppError
from app.core.language import banned_word_pattern
from app.models import Brand, Platform, User
from app.models.post import Post
from app.models.visual import RENDERED_TYPES, Visual, VisualStatus, VisualType
from app.services import content_service, topic_service
from app.services.content_quality import render as render_post
from app.storage import get_storage
from app.visuals import render

logger = logging.getLogger(__name__)

ACTIVE = (VisualStatus.QUEUED, VisualStatus.RUNNING)
STALE_AFTER = timedelta(minutes=30)
CAROUSEL_SLIDES = (3, 8)

# Sensible defaults per platform; carousels are portrait for Instagram and LinkedIn documents.
DEFAULT_RATIO = {
    Platform.INSTAGRAM: "4:5",
    Platform.LINKEDIN: "1:1",
    Platform.FACEBOOK: "1:1",
    Platform.REDDIT: "1:1",
    Platform.X: "16:9",
    Platform.YOUTUBE: "16:9",
}

LAYOUT = {
    VisualType.QUOTE_CARD: "quote",
    VisualType.MINIMAL_GRAPHIC: "headline_center",
    VisualType.INFOGRAPHIC: "title_points",
}


def default_ratio(platform: str, visual_type: VisualType) -> str:
    if visual_type == VisualType.CAROUSEL and platform in (Platform.INSTAGRAM, Platform.LINKEDIN):
        return "4:5"
    return DEFAULT_RATIO.get(Platform(platform), "1:1")


def to_slides(visual_type: VisualType, concept: VisualConcept, brand_name: str) -> list[render.Slide]:
    """Applies the layout for the type; the model only supplies words."""
    specs = concept.slides
    if visual_type != VisualType.CAROUSEL:
        spec = specs[0]
        return [
            render.Slide(
                headline=spec.headline,
                subtext="" if visual_type == VisualType.QUOTE_CARD else spec.subtext,
                layout=LAYOUT[visual_type],
                points=spec.points if visual_type == VisualType.INFOGRAPHIC else [],
                attribution=brand_name if visual_type == VisualType.QUOTE_CARD else "",
            )
        ]
    low, high = CAROUSEL_SLIDES
    if len(specs) < low:
        raise AppError("VISUAL_CONCEPT_TOO_SHORT", f"The carousel plan had fewer than {low} slides.", 502)
    specs = specs[:high]
    last = len(specs) - 1
    return [
        render.Slide(
            headline=s.headline,
            subtext=s.subtext,
            layout="headline_center" if i in (0, last) else "text_left",
        )
        for i, s in enumerate(specs)
    ]


def brand_check(slides: list[render.Slide], banned_words: list[str], truncated: list[str]) -> list[dict]:
    issues = []
    text = "\n".join(" ".join([s.headline, s.subtext, *s.points]) for s in slides)
    for word in banned_words:
        if word and banned_word_pattern(word).search(text):
            issues.append({"severity": "error", "type": "banned_word", "detail": f'Uses the banned word "{word}".'})
    for value in truncated:
        issues.append({"severity": "warning", "type": "text_truncated", "detail": f'Shortened to fit: "{value[:60]}"'})
    return issues


def style_for(brand: Brand) -> render.BrandStyle:
    defaults = render.DEFAULT_COLORS
    return render.BrandStyle(
        name=brand.name,
        primary=brand.primary_color or defaults["primary"],
        secondary=brand.secondary_color or defaults["secondary"],
        accent=brand.accent_color or defaults["accent"],
        heading_font=brand.heading_font,
        body_font=brand.body_font,
    )


# --- Worker side ---------------------------------------------------------------


def execute_visual(visual_id: uuid.UUID) -> dict:
    with sync_session() as db:
        visual = db.get(Visual, visual_id)
        if visual is None or visual.status not in ACTIVE:
            return {"status": "skipped"}
        post = db.get(Post, visual.post_id) if visual.post_id else None
        if post is None:
            visual.status = VisualStatus.FAILED
            visual.error = "The post no longer exists."
            return {"status": "failed", "error": visual.error}
        visual.status = VisualStatus.RUNNING
        brand = db.get(Brand, visual.brand_id)
        style, banned, language = style_for(brand), list(brand.banned_words), brand.language
        visual_type, ratio, brand_id = visual.visual_type, visual.aspect_ratio, brand.id
        previous_keys = [a["key"] for a in visual.assets or []]
        post_id = post.id
        post_text = render_post(post.hook, post.body, post.cta, post.hashtags, Platform(post.platform))
        if Platform(post.platform) in (Platform.REDDIT, Platform.YOUTUBE):
            post_text = f"{post.hook}\n\n{post_text}"
        context = AIContext(user_id=brand.user_id, brand_id=brand.id)

    try:
        concept = asyncio.run(
            topic_service.ai_service_factory().generate_structured(
                task="visual_concept",
                prompt=visual_prompts.concept_prompt(visual_type.value, style.name, banned, post_text, language),
                schema=VisualConcept,
                system=visual_prompts.SYSTEM,
                context=context,
                tier=ModelTier.FAST,
                max_output_tokens=4096,
                thinking_level="minimal",
            )
        )
        slides = to_slides(visual_type, concept, style.name)
    except (AIError, AppError) as exc:
        return _fail(visual_id, exc.message)

    try:
        assets, thumbnail, issues = _render_and_store(slides, style, ratio, visual_type, banned, brand_id, visual_id)
    except Exception:
        logger.exception("visual_render_crashed", extra={"visual_id": str(visual_id)})
        _fail(visual_id, "Unexpected error while rendering the visual.")
        raise

    with sync_session() as db:
        visual = db.get(Visual, visual_id)
        visual.status = VisualStatus.SUCCEEDED
        visual.provider = "pillow"
        visual.error = None
        visual.concept = {
            "slides": [
                {"headline": s.headline, "subtext": s.subtext, "layout": s.layout, "points": s.points}
                for s in slides
            ]
        }
        visual.alt_text = concept.alt_text or None
        visual.assets = assets
        visual.asset_url = next(a["url"] for a in assets if a["kind"] == ("pdf" if len(slides) > 1 else "slide"))
        visual.thumbnail_url = thumbnail
        visual.issues = issues
        post = db.get(Post, post_id)
        if post is not None:
            post.visual_id = visual.id

    # Only after the new version is saved, so a failed regenerate keeps the old images.
    storage = get_storage()
    for key in previous_keys:
        storage.delete(key)
    logger.info("visual_generated", extra={"visual_id": str(visual_id), "slides": len(slides)})
    return {"status": "succeeded", "slides": len(slides), "issues": len(issues)}


def _render_and_store(slides, style, ratio, visual_type, banned, brand_id, visual_id):
    storage = get_storage()
    # A fresh folder per version, so regenerated images get new URLs (no stale caches).
    prefix = f"visuals/{brand_id}/{visual_id}/{uuid.uuid4().hex[:10]}"
    images, truncated, assets = [], [], []
    total = len(slides)
    for index, slide in enumerate(slides, start=1):
        result = render.render_slide(
            slide, style, ratio, number=index if total > 1 else None, total=total if total > 1 else None
        )
        images.append(result.image)
        truncated.extend(result.truncated)
        key = f"{prefix}/slide-{index}.png"
        url = storage.save(key, render.to_png(result.image), "image/png")
        width, height = result.image.size
        assets.append({"kind": "slide", "index": index, "key": key, "url": url, "width": width, "height": height})

    if visual_type == VisualType.CAROUSEL:
        key = f"{prefix}/carousel.pdf"
        assets.append({"kind": "pdf", "index": 0, "key": key, "url": storage.save(key, render.to_pdf(images), "application/pdf")})

    thumb_key = f"{prefix}/thumbnail.jpg"
    thumbnail = storage.save(thumb_key, render.to_thumbnail(images[0]), "image/jpeg")
    assets.append({"kind": "thumbnail", "index": 0, "key": thumb_key, "url": thumbnail})
    return assets, thumbnail, brand_check(slides, banned, truncated)


def _fail(visual_id: uuid.UUID, message: str) -> dict:
    with sync_session() as db:
        visual = db.get(Visual, visual_id)
        visual.status = VisualStatus.FAILED
        visual.error = message[:500]
    logger.info("visual_failed", extra={"visual_id": str(visual_id), "error": message})
    return {"status": "failed", "error": message}


def enqueue_visual(visual_id: uuid.UUID) -> None:
    from app.workers.visual_tasks import generate_visual  # tasks import this module

    generate_visual.delay(str(visual_id))


# --- API side ------------------------------------------------------------------


async def start_generation(
    db: AsyncSession, user: User, post_id: uuid.UUID, visual_type: VisualType, aspect_ratio: str | None
) -> Visual:
    post, _ = await content_service.get_post(db, user, post_id)
    if visual_type not in RENDERED_TYPES:
        raise AppError(
            "VISUAL_TYPE_NEEDS_IMAGE_MODEL",
            f"'{visual_type}' visuals need an AI image model, which the Gemini free tier doesn't include. "
            "Use quote_card, minimal_graphic, infographic or carousel.",
            422,
        )
    ratio = aspect_ratio or default_ratio(post.platform, visual_type)
    if ratio not in render.SIZES:
        raise AppError("INVALID_ASPECT_RATIO", f"Aspect ratio must be one of {', '.join(render.SIZES)}.", 422)

    existing = await db.scalar(
        select(Visual).where(
            Visual.post_id == post.id,
            Visual.status.in_(ACTIVE),
            Visual.updated_at > datetime.now(UTC) - STALE_AFTER,
        )
    )
    if existing is not None:
        return existing

    visual = Visual(brand_id=post.brand_id, post_id=post.id, visual_type=visual_type, aspect_ratio=ratio)
    db.add(visual)
    await db.commit()
    await db.refresh(visual)
    await run_in_threadpool(enqueue_visual, visual.id)
    return visual


async def get_visual(db: AsyncSession, user: User, visual_id: uuid.UUID) -> Visual:
    visual = await db.scalar(select(Visual).join(Brand).where(Visual.id == visual_id, Brand.user_id == user.id))
    if visual is None:
        raise AppError("VISUAL_NOT_FOUND", "Visual not found.", 404)
    return visual


async def regenerate(db: AsyncSession, user: User, visual_id: uuid.UUID) -> Visual:
    """New copy and images for the same visual. The current images stay until the new ones exist."""
    visual = await get_visual(db, user, visual_id)
    if visual.status in ACTIVE and visual.updated_at > datetime.now(UTC) - STALE_AFTER:
        return visual
    if visual.post_id is None:
        raise AppError("POST_NOT_FOUND", "The post for this visual no longer exists.", 404)
    visual.status = VisualStatus.QUEUED
    visual.error = None
    await db.commit()
    await db.refresh(visual)
    await run_in_threadpool(enqueue_visual, visual.id)
    return visual


async def download(db: AsyncSession, user: User, visual_id: uuid.UUID) -> bytes:
    """ZIP of the visual's images (and carousel PDF)."""
    visual = await get_visual(db, user, visual_id)
    if visual.status != VisualStatus.SUCCEEDED:
        raise AppError("VISUAL_NOT_READY", "This visual hasn't finished generating.", 409)
    storage = get_storage()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for asset in visual.assets:
            if asset["kind"] == "thumbnail":
                continue
            data = await run_in_threadpool(storage.read, asset["key"])
            archive.writestr(asset["key"].rsplit("/", 1)[-1], data)
    return buffer.getvalue()
