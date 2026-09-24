"""AIService: the only way the rest of the app uses AI (spec sections 27-30, 68-69).

- Routes each call to a cheap or a stronger model.
- Retries temporary provider failures with exponential backoff.
- Validates structured output with Pydantic, with one repair attempt.
- Records every call in ai_runs with tokens, estimated cost and timing.
"""

import asyncio
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.ai.base import AIProvider, AIProviderError, EmbeddingResult, TextRequest, TextResult
from app.ai.pricing import estimate_cost
from app.core.config import get_settings
from app.core.errors import AppError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 1.0
# If the provider asks us to wait longer than this, fail now instead of holding a worker.
MAX_RETRY_WAIT_SECONDS = 20.0
# Failures where a different model may well succeed (overload, per-model quota, timeouts).
FALLBACK_CODES = {"AI_PROVIDER_ERROR", "AI_RATE_LIMITED", "AI_TIMEOUT", "AI_NETWORK_ERROR", "AI_NOT_IN_PLAN"}


class ModelTier(StrEnum):
    FAST = "fast"  # summaries, extraction, classification, short rewrites
    QUALITY = "quality"  # original writing and editing


@dataclass
class AIContext:
    """Who the call is for, so costs can be attributed."""

    user_id: uuid.UUID | None = None
    brand_id: uuid.UUID | None = None


@dataclass
class AIRunRecord:
    context: AIContext
    task_type: str
    provider: str
    model: str
    status: str
    duration_ms: int
    attempts: int
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


class AIError(AppError):
    """An AI call failed in a way the caller should report, not crash on."""


_STATUS_FOR_CODE = {"AI_NOT_CONFIGURED": 503, "AI_RATE_LIMITED": 429, "AI_NOT_IN_PLAN": 402}


def _to_app_error(exc: AIProviderError) -> AIError:
    return AIError(exc.code, exc.message, _STATUS_FOR_CODE.get(exc.code, 502))


class _InvalidOutput(Exception):
    def __init__(self, problems: str, result: TextResult) -> None:
        super().__init__(problems)
        self.problems = problems
        self.result = result


class AIService:
    def __init__(
        self,
        provider: AIProvider,
        *,
        models: dict[ModelTier, str],
        fallbacks: dict[ModelTier, str] | None = None,
        embedding_model: str,
        embedding_dimensions: int,
        recorder: Callable[[AIRunRecord], None] | None = None,
        max_attempts: int = MAX_ATTEMPTS,
        backoff_seconds: float = BACKOFF_SECONDS,
    ) -> None:
        self.provider = provider
        self._models = models
        self._fallbacks = {tier: model for tier, model in (fallbacks or {}).items() if model}
        self._embedding_model = embedding_model
        self._embedding_dimensions = embedding_dimensions
        self._recorder = recorder if recorder is not None else record_ai_run
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds

    async def generate_text(
        self,
        *,
        task: str,
        prompt: str,
        system: str | None = None,
        context: AIContext | None = None,
        tier: ModelTier = ModelTier.FAST,
        max_output_tokens: int = 2048,
        thinking_level: str | None = None,
    ) -> str:
        result = await self._generate(
            task,
            context,
            tier,
            lambda model: TextRequest(
                model=model,
                prompt=prompt,
                system=system,
                max_output_tokens=max_output_tokens,
                thinking_level=thinking_level,
            ),
        )
        return result.text

    async def generate_structured(
        self,
        *,
        task: str,
        prompt: str,
        schema: type[T],
        system: str | None = None,
        context: AIContext | None = None,
        tier: ModelTier = ModelTier.FAST,
        max_output_tokens: int = 4096,
        thinking_level: str | None = None,
    ) -> T:
        """Returns a validated instance of `schema`. Model output is never trusted unvalidated."""
        json_schema = schema.model_json_schema()

        def request_for(text: str) -> Callable[[str], TextRequest]:
            return lambda model: TextRequest(
                model=model,
                prompt=text,
                system=system,
                max_output_tokens=max_output_tokens,
                thinking_level=thinking_level,
                json_schema=json_schema,
            )

        def validate(result: TextResult) -> T:
            try:
                return schema.model_validate_json(_strip_code_fence(result.text))
            except ValidationError as exc:
                raise _InvalidOutput(_summarize(exc), result) from None

        try:
            return await self._generate(task, context, tier, request_for(prompt), validate)
        except _InvalidOutput as first:
            # One repair attempt, telling the model what was wrong. The invalid output
            # itself isn't echoed back, since it may contain text from untrusted sources.
            repair = (
                f"{prompt}\n\nYour previous reply did not match the required JSON schema: {first.problems}\n"
                "Reply again with only JSON that matches the schema."
            )
            try:
                return await self._generate(f"{task}:repair", context, tier, request_for(repair), validate)
            except _InvalidOutput as second:
                raise AIError(
                    "AI_INVALID_OUTPUT", "The AI returned output that didn't match the expected format.", 502
                ) from second

    async def embed(self, *, task: str, texts: list[str], context: AIContext | None = None) -> list[list[float]]:
        if not texts:
            return []
        result: EmbeddingResult = await self._call(
            task,
            context,
            self._embedding_model,
            lambda: self.provider.embed(texts, model=self._embedding_model, dimensions=self._embedding_dimensions),
        )
        return result.vectors

    async def _generate(
        self,
        task: str,
        context: AIContext | None,
        tier: ModelTier,
        make_request: Callable[[str], TextRequest],
        validate: Callable[[TextResult], T] | None = None,
    ):
        """Tries the tier's model, then its fallback if the first is overloaded or rate limited."""
        models = [self._models[tier], *([self._fallbacks[tier]] if tier in self._fallbacks else [])]
        for index, model in enumerate(models):
            request = make_request(model)
            try:
                return await self._call(task, context, model, lambda: self.provider.generate(request), validate)
            except AIError as exc:
                if index == len(models) - 1 or exc.code not in FALLBACK_CODES:
                    raise
                logger.warning(
                    "ai_fallback",
                    extra={"task": task, "from_model": model, "to_model": models[index + 1], "error": exc.code},
                )
        raise AssertionError("unreachable")

    async def _call(
        self,
        task: str,
        context: AIContext | None,
        model: str,
        invoke: Callable[[], Awaitable[R]],
        validate: Callable[[R], T] | None = None,
    ) -> R | T:
        context = context or AIContext()
        started = time.perf_counter()
        attempt = 0
        while True:
            attempt += 1
            try:
                result = await invoke()
                break
            except AIProviderError as exc:
                wait = self._retry_wait(exc, attempt)
                if wait is None:
                    await self._record(task, context, model, "failed", started, attempt, error=exc)
                    raise _to_app_error(exc) from None
                logger.info("ai_retry", extra={"task": task, "error": exc.code, "attempt": attempt, "wait": wait})
                await asyncio.sleep(wait)

        if validate is None:
            await self._record(task, context, model, "succeeded", started, attempt, result=result)
            return result
        try:
            value = validate(result)
        except _InvalidOutput as invalid:
            await self._record(task, context, model, "invalid_output", started, attempt, result=result, note=invalid.problems)
            raise
        await self._record(task, context, model, "succeeded", started, attempt, result=result)
        return value

    def _retry_wait(self, exc: AIProviderError, attempt: int) -> float | None:
        if not exc.retryable or attempt >= self._max_attempts:
            return None
        if exc.retry_after is not None:
            return exc.retry_after if exc.retry_after <= MAX_RETRY_WAIT_SECONDS else None
        # Exponential backoff with jitter so parallel workers don't retry in lockstep.
        return self._backoff * 2 ** (attempt - 1) * random.uniform(0.8, 1.2)

    async def _record(
        self,
        task: str,
        context: AIContext,
        model: str,
        status: str,
        started: float,
        attempts: int,
        *,
        result: TextResult | EmbeddingResult | None = None,
        error: AIProviderError | None = None,
        note: str | None = None,
    ) -> None:
        record = AIRunRecord(
            context=context,
            task_type=task,
            provider=self.provider.name,
            model=getattr(result, "model", None) or model,
            status=status,
            duration_ms=round((time.perf_counter() - started) * 1000),
            attempts=attempts,
            input_tokens=getattr(result, "input_tokens", 0),
            output_tokens=getattr(result, "output_tokens", 0),
            error=(f"{error.code}: {error.message}" if error else note),
        )
        logger.info(
            "ai_call",
            extra={
                "task": task,
                "model": record.model,
                "status": status,
                "input_tokens": record.input_tokens,
                "output_tokens": record.output_tokens,
                "duration_ms": record.duration_ms,
                "attempts": attempts,
                "user_id": str(context.user_id) if context.user_id else None,
                "brand_id": str(context.brand_id) if context.brand_id else None,
            },
        )
        try:
            # Its own short transaction, so failed calls are logged even if the caller rolls back.
            await asyncio.to_thread(self._recorder, record)
        except Exception:
            logger.exception("ai_run_record_failed")


def record_ai_run(record: AIRunRecord) -> None:
    from app.core.database import sync_session
    from app.models import AIRun

    with sync_session() as db:
        db.add(
            AIRun(
                user_id=record.context.user_id,
                brand_id=record.context.brand_id,
                task_type=record.task_type[:60],
                provider=record.provider,
                model=record.model[:100],
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                estimated_cost=estimate_cost(record.model, record.input_tokens, record.output_tokens),
                duration_ms=record.duration_ms,
                attempts=record.attempts,
                status=record.status,
                error=record.error[:500] if record.error else None,
            )
        )


def _strip_code_fence(text: str) -> str:
    """Some models wrap JSON in ``` fences despite being asked not to."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def _summarize(exc: ValidationError) -> str:
    problems = []
    for error in exc.errors()[:10]:
        location = ".".join(str(p) for p in error["loc"]) or "(root)"
        problems.append(f"{location}: {error['msg']}")
    return "; ".join(problems)[:1000]


def get_ai_service() -> AIService:
    settings = get_settings()
    from app.ai.gemini_provider import GeminiProvider
    from app.models.base import EMBEDDING_DIMENSIONS

    key = settings.google_ai_api_key.get_secret_value() if settings.google_ai_api_key else None
    return AIService(
        GeminiProvider(key),
        models={ModelTier.FAST: settings.gemini_fast_model, ModelTier.QUALITY: settings.gemini_quality_model},
        fallbacks={
            ModelTier.FAST: settings.gemini_fast_fallback_model,
            ModelTier.QUALITY: settings.gemini_quality_fallback_model,
        },
        embedding_model=settings.gemini_embedding_model,
        embedding_dimensions=EMBEDDING_DIMENSIONS,
    )
