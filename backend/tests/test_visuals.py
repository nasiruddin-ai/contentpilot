import io
import json
import uuid
import zipfile
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.ai.base import AIProvider, AIProviderError, EmbeddingResult, TextRequest, TextResult
from app.ai.gemini_provider import GeminiProvider
from app.ai.prompts.visual import SlideSpec, VisualConcept
from app.ai.service import AIService, ModelTier
from app.core.config import get_settings
from app.core.database import sync_session
from app.models import Post
from app.models.visual import VisualType
from app.services import topic_service, visual_service
from app.storage import LocalStorage, check_key
from app.visuals import render
from tests.conftest import sign_up

STYLE = render.BrandStyle(name="Squareko", primary="#07142F", secondary="#6E5BFF", accent="#4BA8FF")


# --- Rendering -------------------------------------------------------------------------


def test_contrast_and_readable_text():
    assert round(render.contrast_ratio("#FFFFFF", "#000000")) == 21
    assert render.readable_on("#07142F") == render.LIGHT_TEXT
    assert render.readable_on("#F8FAFC") == render.DARK_TEXT
    # An accent almost identical to the background falls back to something visible.
    murky = render.BrandStyle(name="B", primary="#07142F", secondary="#08152F", accent="#07152F")
    assert render.accent_for(murky) == murky.text


def test_fit_text_shrinks_then_truncates():
    font, lines, cut = render.fit_text("Short headline", None, True, (900, 400), 90, 24)
    assert font.size == 90 and not cut
    long = "word " * 400
    font, lines, cut = render.fit_text(long, None, True, (400, 200), 90, 24)
    assert cut and lines[-1].endswith("…") and font.size == 24


@pytest.mark.parametrize("ratio", list(render.SIZES))
def test_every_layout_renders_at_every_ratio(ratio):
    for slide in (
        render.Slide("Your website isn't the problem.", "Your message might be."),
        render.Slide("Traffic doesn't fix positioning.", "People decide fast.", layout="text_left"),
        render.Slide("Clarity sells.", layout="quote", attribution="Squareko"),
        render.Slide("Three fixes", layout="title_points", points=["One", "Two", "Three"]),
    ):
        result = render.render_slide(slide, STYLE, ratio, number=2, total=5)
        assert result.image.size == render.SIZES[ratio]
        assert result.truncated == []


def test_exports():
    images = [render.render_slide(render.Slide(f"Slide {i}"), STYLE, "4:5").image for i in range(3)]
    pdf = render.to_pdf(images)
    assert pdf.startswith(b"%PDF") and pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page\r") + pdf.count(b"/Type /Page ") >= 3
    thumb = Image.open(io.BytesIO(render.to_thumbnail(images[0])))
    assert thumb.format == "JPEG" and thumb.width == 400
    assert Image.open(io.BytesIO(render.to_png(images[0]))).size == (1080, 1350)


# --- Storage -----------------------------------------------------------------------------


def test_local_storage_round_trip(tmp_path):
    storage = LocalStorage(str(tmp_path), "http://api/media")
    url = storage.save("visuals/a/b/slide-1.png", b"png-bytes", "image/png")
    assert url == "http://api/media/visuals/a/b/slide-1.png"
    assert storage.read("visuals/a/b/slide-1.png") == b"png-bytes"
    storage.delete("visuals/a/b/slide-1.png")
    assert not (tmp_path / "visuals/a/b/slide-1.png").exists()


@pytest.mark.parametrize("key", ["../etc/passwd", "visuals/../../x", "/abs/path", "Visuals/UPPER", "a b"])
def test_storage_rejects_unsafe_keys(key):
    with pytest.raises(ValueError):
        check_key(key)


# --- Layout rules and brand check ------------------------------------------------------


def concept(*headlines, points=()):
    return VisualConcept(
        slides=[SlideSpec(headline=h, subtext="sub", points=list(points)) for h in headlines], alt_text="alt"
    )


def test_layouts_are_decided_by_code():
    quote = visual_service.to_slides(VisualType.QUOTE_CARD, concept("Clarity sells"), "Squareko")
    assert quote[0].layout == "quote" and quote[0].subtext == "" and quote[0].attribution == "Squareko"

    info = visual_service.to_slides(VisualType.INFOGRAPHIC, concept("Tips", points=["a", "b", "c"]), "S")
    assert info[0].layout == "title_points" and info[0].points == ["a", "b", "c"]

    slides = visual_service.to_slides(VisualType.CAROUSEL, concept(*[f"S{i}" for i in range(10)]), "S")
    assert len(slides) == 8  # capped
    assert [s.layout for s in slides] == ["headline_center"] + ["text_left"] * 6 + ["headline_center"]

    with pytest.raises(Exception, match="fewer than 3"):
        visual_service.to_slides(VisualType.CAROUSEL, concept("One", "Two"), "S")


def test_brand_check():
    slides = [render.Slide("Pure synergy", "fine")]
    issues = visual_service.brand_check(slides, ["synergy"], ["a very long headline that got cut"])
    assert {(i["severity"], i["type"]) for i in issues} == {("error", "banned_word"), ("warning", "text_truncated")}


# --- Gemini plan limits ------------------------------------------------------------------------


async def test_zero_quota_is_not_in_plan_and_not_retried():
    message = "Rate limit exceeded for model gemini-3.1-flash-image (limit: 0 requests per day on Free Tier)."
    provider = GeminiProvider("k", transport=httpx.MockTransport(lambda _: httpx.Response(429, json={"error": {"message": message}})))
    with pytest.raises(AIProviderError) as exc:
        await provider.generate(TextRequest(model="gemini-3.1-flash-image", prompt="x"))
    assert exc.value.code == "AI_NOT_IN_PLAN" and not exc.value.retryable


# --- End to end: API + worker --------------------------------------------------------------------


class VisualAI(AIProvider):
    name = "fake"

    def __init__(self):
        self.slides = 5
        self.fail = False

    async def generate(self, request):
        if self.fail:
            raise AIProviderError("AI_AUTH_FAILED", "The Gemini API key was rejected.")
        slides = [{"headline": f"Point {i}", "subtext": "Short support.", "points": ["a", "b", "c"]} for i in range(self.slides)]
        body = {"slides": slides, "alt_text": "Navy slides with white headlines."}
        return TextResult(text=json.dumps(body), model=request.model, input_tokens=5, output_tokens=5)

    async def embed(self, texts, *, model, dimensions):
        return EmbeddingResult(vectors=[[0.0] * dimensions for _ in texts], model=model, input_tokens=0)


@pytest.fixture
def visual_ai(monkeypatch):
    provider = VisualAI()
    ai = AIService(
        provider,
        models={ModelTier.FAST: "fast", ModelTier.QUALITY: "quality"},
        embedding_model="e",
        embedding_dimensions=768,
        recorder=lambda r: None,
        backoff_seconds=0,
        max_attempts=1,
    )
    monkeypatch.setattr(topic_service, "ai_service_factory", lambda: ai)
    return provider


@pytest.fixture
def post(api_client, visual_ai, monkeypatch):
    enqueued = []
    monkeypatch.setattr(visual_service, "enqueue_visual", enqueued.append)
    sign_up(api_client, "owner@example.com")
    brand = api_client.post(
        "/api/v1/brands", json={"name": "Squareko", "primary_color": "#07142F", "accent_color": "#4BA8FF"}
    ).json()
    with sync_session() as db:
        row = Post(
            brand_id=uuid.UUID(brand["id"]),
            platform="instagram",
            content_type="carousel",
            hook="Your website isn't the problem.",
            body="Your message might be.",
            cta="Follow for more.",
            hashtags=["webdesign"],
        )
        db.add(row)
        db.flush()
        return {"id": str(row.id), "brand_id": brand["id"], "enqueued": enqueued}


def media_path(url: str) -> Path:
    return Path(get_settings().media_root) / url.split("/media/", 1)[1]


def generate(client, post_id, visual_type="carousel", **extra):
    return client.post("/api/v1/visuals/generate", json={"post_id": post_id, "visual_type": visual_type, **extra})


def test_carousel_end_to_end(api_client, post):
    response = generate(api_client, post["id"])
    assert response.status_code == 202
    visual = response.json()
    assert visual["status"] == "queued" and visual["aspect_ratio"] == "4:5"  # Instagram default
    assert post["enqueued"] == [uuid.UUID(visual["id"])]
    assert generate(api_client, post["id"]).json()["id"] == visual["id"]  # no duplicate job

    assert visual_service.execute_visual(uuid.UUID(visual["id"]))["status"] == "succeeded"
    done = api_client.get(f"/api/v1/visuals/{visual['id']}").json()
    slides = [a for a in done["assets"] if a["kind"] == "slide"]
    assert len(slides) == 5 and slides[0]["width"] == 1080 and slides[0]["height"] == 1350
    assert done["asset_url"].endswith("carousel.pdf")
    assert done["alt_text"] == "Navy slides with white headlines."
    assert [s["layout"] for s in done["concept"]["slides"]][0] == "headline_center"

    # Files exist and are served at /media.
    assert media_path(slides[0]["url"]).exists()
    served = api_client.get("/media/" + slides[0]["url"].split("/media/", 1)[1])
    assert served.status_code == 200 and served.headers["content-type"] == "image/png"

    assert api_client.get(f"/api/v1/posts/{post['id']}").json()["visual_id"] == visual["id"]

    archive = zipfile.ZipFile(io.BytesIO(api_client.get(f"/api/v1/visuals/{visual['id']}/download").content))
    assert sorted(archive.namelist()) == ["carousel.pdf"] + [f"slide-{i}.png" for i in range(1, 6)]


def test_regenerate_replaces_files(api_client, post):
    visual = generate(api_client, post["id"], "quote_card").json()
    visual_service.execute_visual(uuid.UUID(visual["id"]))
    first = api_client.get(f"/api/v1/visuals/{visual['id']}").json()

    again = api_client.post(f"/api/v1/visuals/{visual['id']}/regenerate")
    assert again.status_code == 202 and again.json()["status"] == "queued"
    visual_service.execute_visual(uuid.UUID(visual["id"]))
    second = api_client.get(f"/api/v1/visuals/{visual['id']}").json()

    assert second["asset_url"] != first["asset_url"]  # new URL, no stale caches
    assert media_path(second["asset_url"]).exists()
    assert not media_path(first["asset_url"]).exists()  # old version cleaned up


def test_ai_image_types_need_a_paid_model(api_client, post):
    response = generate(api_client, post["id"], "photo")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VISUAL_TYPE_NEEDS_IMAGE_MODEL"
    assert generate(api_client, post["id"], "quote_card", aspect_ratio="7:3").status_code == 422


def test_failed_generation_keeps_it_visible(api_client, post, visual_ai):
    visual_ai.fail = True
    visual = generate(api_client, post["id"], "minimal_graphic").json()
    assert visual_service.execute_visual(uuid.UUID(visual["id"]))["status"] == "failed"
    stored = api_client.get(f"/api/v1/visuals/{visual['id']}").json()
    assert stored["status"] == "failed" and "rejected" in stored["error"]
    assert api_client.get(f"/api/v1/visuals/{visual['id']}/download").status_code == 409


def test_visuals_are_private(api_client, post):
    visual = generate(api_client, post["id"], "quote_card").json()
    visual_service.execute_visual(uuid.UUID(visual["id"]))

    sign_up(api_client, "intruder@example.com")
    assert api_client.get(f"/api/v1/visuals/{visual['id']}").status_code == 404
    assert api_client.post(f"/api/v1/visuals/{visual['id']}/regenerate").status_code == 404
    assert api_client.get(f"/api/v1/visuals/{visual['id']}/download").status_code == 404
    assert generate(api_client, post["id"]).status_code == 404
