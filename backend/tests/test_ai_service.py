import uuid
from decimal import Decimal

import pytest
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.ai.base import AIProvider, AIProviderError, EmbeddingResult, TextRequest, TextResult
from app.ai.pricing import estimate_cost
from app.ai.service import AIContext, AIError, AIRunRecord, AIService, ModelTier, record_ai_run
from app.models import AIRun


class Post(BaseModel):
    hook: str
    body: str
    hashtags: list[str] = []
    content_type: str = Field(pattern="^(linkedin_text|carousel)$")


VALID = '{"hook": "You have a signal problem.", "body": "More content is not the answer.", "content_type": "linkedin_text"}'


class ScriptedProvider(AIProvider):
    """Returns (or raises) the scripted outcomes in order."""

    name = "fake"

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests: list[TextRequest] = []

    async def generate(self, request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return TextResult(text=outcome, model=request.model, input_tokens=10, output_tokens=5)

    async def embed(self, texts, *, model, dimensions):
        self.requests.append(texts)
        return EmbeddingResult(vectors=[[1.0] * dimensions for _ in texts], model=model, input_tokens=4)


def service(provider, records=None, **kwargs):
    records = records if records is not None else []
    return AIService(
        provider,
        models={ModelTier.FAST: "fast-model", ModelTier.QUALITY: "quality-model"},
        embedding_model="embed-model",
        embedding_dimensions=4,
        recorder=records.append,
        backoff_seconds=0,
        **kwargs,
    )


async def test_routes_by_tier():
    provider = ScriptedProvider("a", "b")
    ai = service(provider)
    await ai.generate_text(task="summary", prompt="x")
    await ai.generate_text(task="write", prompt="x", tier=ModelTier.QUALITY)
    assert [r.model for r in provider.requests] == ["fast-model", "quality-model"]


async def test_records_every_call_with_context():
    records: list[AIRunRecord] = []
    user, brand = uuid.uuid4(), uuid.uuid4()
    await service(ScriptedProvider("ok"), records).generate_text(
        task="summary", prompt="x", context=AIContext(user_id=user, brand_id=brand)
    )
    (record,) = records
    assert (record.task_type, record.status, record.model, record.provider) == ("summary", "succeeded", "fast-model", "fake")
    assert (record.input_tokens, record.output_tokens, record.attempts) == (10, 5, 1)
    assert (record.context.user_id, record.context.brand_id) == (user, brand)


async def test_retries_temporary_failures():
    records = []
    provider = ScriptedProvider(
        AIProviderError("AI_PROVIDER_ERROR", "503", retryable=True),
        AIProviderError("AI_TIMEOUT", "slow", retryable=True),
        "finally",
    )
    assert await service(provider, records).generate_text(task="t", prompt="x") == "finally"
    assert len(provider.requests) == 3
    assert records[0].attempts == 3 and records[0].status == "succeeded"


async def test_gives_up_after_max_attempts():
    records = []
    provider = ScriptedProvider(*[AIProviderError("AI_PROVIDER_ERROR", "503", retryable=True)] * 3)
    with pytest.raises(AIError) as exc:
        await service(provider, records).generate_text(task="t", prompt="x")
    assert exc.value.code == "AI_PROVIDER_ERROR" and exc.value.status_code == 502
    assert records[0].status == "failed" and records[0].attempts == 3


async def test_permanent_errors_are_not_retried():
    provider = ScriptedProvider(AIProviderError("AI_AUTH_FAILED", "bad key"))
    with pytest.raises(AIError) as exc:
        await service(provider).generate_text(task="t", prompt="x")
    assert exc.value.code == "AI_AUTH_FAILED"
    assert len(provider.requests) == 1


async def test_long_rate_limit_waits_fail_fast():
    provider = ScriptedProvider(AIProviderError("AI_RATE_LIMITED", "quota", retryable=True, retry_after=3600))
    with pytest.raises(AIError) as exc:
        await service(provider).generate_text(task="t", prompt="x")
    assert exc.value.code == "AI_RATE_LIMITED" and exc.value.status_code == 429
    assert len(provider.requests) == 1


async def test_missing_key_is_a_503():
    provider = ScriptedProvider(AIProviderError("AI_NOT_CONFIGURED", "no key"))
    with pytest.raises(AIError) as exc:
        await service(provider).generate_text(task="t", prompt="x")
    assert exc.value.status_code == 503


async def test_structured_output_is_validated():
    provider = ScriptedProvider(VALID)
    post = await service(provider).generate_structured(task="write", prompt="x", schema=Post)
    assert isinstance(post, Post) and post.hook == "You have a signal problem."
    assert provider.requests[0].json_schema == Post.model_json_schema()


async def test_structured_output_tolerates_code_fences():
    provider = ScriptedProvider(f"```json\n{VALID}\n```")
    assert (await service(provider).generate_structured(task="w", prompt="x", schema=Post)).body


async def test_invalid_output_gets_one_repair_attempt():
    records = []
    provider = ScriptedProvider('{"hook": "missing fields"}', VALID)
    post = await service(provider, records).generate_structured(task="write", prompt="Write a post", schema=Post)

    assert post.content_type == "linkedin_text"
    assert [r.status for r in records] == ["invalid_output", "succeeded"]
    assert records[1].task_type == "write:repair"
    repair_prompt = provider.requests[1].prompt
    assert repair_prompt.startswith("Write a post") and "body" in repair_prompt
    assert "missing fields" not in repair_prompt  # the bad output isn't echoed back


async def test_repeated_invalid_output_raises():
    provider = ScriptedProvider("not json", '{"hook": 1, "body": "b", "content_type": "tweet"}')
    with pytest.raises(AIError) as exc:
        await service(provider).generate_structured(task="w", prompt="x", schema=Post)
    assert exc.value.code == "AI_INVALID_OUTPUT"
    assert len(provider.requests) == 2


async def test_embed():
    provider = ScriptedProvider()
    ai = service(provider)
    assert await ai.embed(task="embed", texts=[]) == []
    assert provider.requests == []
    assert await ai.embed(task="embed", texts=["a", "b"]) == [[1.0] * 4, [1.0] * 4]


async def test_recorder_failure_does_not_break_the_call():
    def broken(_record):
        raise RuntimeError("db down")

    ai = service(ScriptedProvider("still fine"))
    ai._recorder = broken
    assert await ai.generate_text(task="t", prompt="x") == "still fine"


def test_estimate_cost():
    assert estimate_cost("gemini-3.5-flash-lite", 1_000_000, 1_000_000) == Decimal("2.80")
    assert estimate_cost("models/gemini-3.8-flash", 2000, 1000) == Decimal("0.00525")
    assert estimate_cost("some-future-model", 10, 10) is None


def test_record_ai_run_writes_row(api_client, db_engine):
    record_ai_run(
        AIRunRecord(
            context=AIContext(),
            task_type="summary",
            provider="gemini",
            model="gemini-3.5-flash-lite",
            status="succeeded",
            duration_ms=812,
            attempts=1,
            input_tokens=1000,
            output_tokens=200,
        )
    )
    from sqlalchemy.orm import Session

    with Session(db_engine) as db:
        run = db.scalars(select(AIRun)).one()
        assert (run.task_type, run.status, run.input_tokens, run.output_tokens) == ("summary", "succeeded", 1000, 200)
        assert run.estimated_cost == Decimal("0.000800")
        db.delete(run)
        db.commit()


def service_with_fallback(provider, records=None):
    records = records if records is not None else []
    return AIService(
        provider,
        models={ModelTier.FAST: "fast-model", ModelTier.QUALITY: "quality-model"},
        fallbacks={ModelTier.QUALITY: "backup-model"},
        embedding_model="embed-model",
        embedding_dimensions=4,
        recorder=records.append,
        backoff_seconds=0,
    )


async def test_overloaded_model_falls_back():
    records = []
    overloaded = AIProviderError("AI_PROVIDER_ERROR", "503", retryable=True)
    provider = ScriptedProvider(overloaded, overloaded, overloaded, "from backup")
    text = await service_with_fallback(provider, records).generate_text(task="write", prompt="x", tier=ModelTier.QUALITY)

    assert text == "from backup"
    assert [r.model for r in provider.requests] == ["quality-model"] * 3 + ["backup-model"]
    assert [(r.model, r.status) for r in records] == [("quality-model", "failed"), ("backup-model", "succeeded")]


async def test_rate_limited_model_falls_back_immediately():
    provider = ScriptedProvider(AIProviderError("AI_RATE_LIMITED", "5 rpm", retryable=True, retry_after=41), VALID)
    post = await service_with_fallback(provider).generate_structured(
        task="write", prompt="x", schema=Post, tier=ModelTier.QUALITY
    )
    assert post.hook
    assert [r.model for r in provider.requests] == ["quality-model", "backup-model"]
    assert provider.requests[1].json_schema == Post.model_json_schema()


async def test_no_fallback_for_permanent_errors_or_tiers_without_one():
    ai = service_with_fallback(ScriptedProvider(AIProviderError("AI_BAD_REQUEST", "bad")))
    with pytest.raises(AIError):
        await ai.generate_text(task="w", prompt="x", tier=ModelTier.QUALITY)

    fast = ScriptedProvider(*[AIProviderError("AI_PROVIDER_ERROR", "503", retryable=True)] * 3)
    with pytest.raises(AIError):
        await service_with_fallback(fast).generate_text(task="w", prompt="x", tier=ModelTier.FAST)
    assert {r.model for r in fast.requests} == {"fast-model"}
