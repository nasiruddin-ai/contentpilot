import json
import math

import httpx
import pytest
from pydantic import BaseModel

from app.ai.base import AIProviderError, TextRequest
from app.ai.gemini_provider import GeminiProvider

KEY = "test-gemini-key-123"


class Slide(BaseModel):
    headline: str


class Carousel(BaseModel):
    title: str
    slides: list[Slide]


def completed(text="Hello!", **usage):
    return {
        "id": "v1_abc",
        "model": "gemini-3.5-flash-lite",
        "object": "interaction",
        "status": "completed",
        "steps": [{"type": "model_output", "content": [{"type": "text", "text": text}]}],
        "usage": {"total_input_tokens": 7, "total_output_tokens": 20, "total_thought_tokens": 22, **usage},
    }


def provider_with(handler, key=KEY):
    requests = []

    def record(request):
        requests.append(request)
        return handler(request)

    return GeminiProvider(key, transport=httpx.MockTransport(record)), requests


async def test_sends_interactions_request():
    provider, requests = provider_with(lambda _: httpx.Response(200, json=completed()))
    await provider.generate(
        TextRequest(
            model="gemini-3.5-flash-lite",
            prompt="Say hi",
            system="Be brief.",
            max_output_tokens=256,
            thinking_level="low",
            json_schema=Carousel.model_json_schema(),
        )
    )

    request = requests[0]
    assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert request.headers["x-goog-api-key"] == KEY
    body = json.loads(request.content)
    assert body["model"] == "gemini-3.5-flash-lite"
    assert body["input"] == "Say hi"
    assert body["system_instruction"] == "Be brief."
    assert body["store"] is False
    assert body["generation_config"] == {"max_output_tokens": 256, "thinking_level": "low"}
    assert body["response_format"]["mime_type"] == "application/json"

    schema = body["response_format"]["schema"]
    assert "$defs" not in json.dumps(schema) and "$ref" not in json.dumps(schema)
    assert schema["properties"]["slides"]["items"]["properties"]["headline"]["type"] == "string"


async def test_parses_text_and_counts_thinking_as_output():
    provider, _ = provider_with(lambda _: httpx.Response(200, json=completed("Hi there")))
    result = await provider.generate(TextRequest(model="gemini-3.5-flash-lite", prompt="x"))
    assert result.text == "Hi there"
    assert (result.input_tokens, result.output_tokens) == (7, 42)
    assert result.model == "gemini-3.5-flash-lite"


async def test_prefers_output_text_convenience_field():
    body = {**completed("from steps"), "output_text": "from output_text"}
    provider, _ = provider_with(lambda _: httpx.Response(200, json=body))
    assert (await provider.generate(TextRequest(model="m", prompt="x"))).text == "from output_text"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({**completed(), "status": "incomplete"}, "AI_OUTPUT_TRUNCATED"),
        ({**completed(), "status": "failed"}, "AI_FAILED"),
        ({**completed(), "steps": []}, "AI_EMPTY_OUTPUT"),
    ],
)
async def test_unusable_responses(body, code):
    provider, _ = provider_with(lambda _: httpx.Response(200, json=body))
    with pytest.raises(AIProviderError) as exc:
        await provider.generate(TextRequest(model="m", prompt="x"))
    assert exc.value.code == code
    assert not exc.value.retryable


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (httpx.Response(401, json={"error": {"message": "API key not valid"}}), "AI_AUTH_FAILED", False),
        (httpx.Response(403, json={"error": {"message": "denied"}}), "AI_AUTH_FAILED", False),
        (httpx.Response(404, json={"error": {"message": "models/nope is not found"}}), "AI_MODEL_NOT_FOUND", False),
        (httpx.Response(400, json={"error": {"message": "bad field"}}), "AI_BAD_REQUEST", False),
        (httpx.Response(500, text="oops"), "AI_PROVIDER_ERROR", True),
        (httpx.Response(503, text="overloaded"), "AI_PROVIDER_ERROR", True),
    ],
)
async def test_http_errors(response, code, retryable):
    provider, _ = provider_with(lambda _: response)
    with pytest.raises(AIProviderError) as exc:
        await provider.generate(TextRequest(model="m", prompt="x"))
    assert (exc.value.code, exc.value.retryable) == (code, retryable)
    assert KEY not in exc.value.message


async def test_rate_limit_reads_retry_delay():
    error = {"error": {"code": 429, "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17s"}]}}
    provider, _ = provider_with(lambda _: httpx.Response(429, json=error))
    with pytest.raises(AIProviderError) as exc:
        await provider.generate(TextRequest(model="m", prompt="x"))
    assert exc.value.code == "AI_RATE_LIMITED"
    assert exc.value.retryable and exc.value.retry_after == 17.0


async def test_network_failures_are_retryable():
    def boom(request):
        raise httpx.ConnectError("down", request=request)

    provider, _ = provider_with(boom)
    with pytest.raises(AIProviderError) as exc:
        await provider.generate(TextRequest(model="m", prompt="x"))
    assert exc.value.code == "AI_NETWORK_ERROR" and exc.value.retryable


async def test_missing_key_fails_without_calling_out():
    provider, requests = provider_with(lambda _: httpx.Response(200, json=completed()), key=None)
    with pytest.raises(AIProviderError) as exc:
        await provider.generate(TextRequest(model="m", prompt="x"))
    assert exc.value.code == "AI_NOT_CONFIGURED"
    assert requests == []


async def test_embeddings_are_batched_and_normalized():
    def handler(request):
        body = json.loads(request.content)
        assert request.url.path.endswith("/models/gemini-embedding-2:batchEmbedContents")
        assert body["requests"][0]["outputDimensionality"] == 3
        return httpx.Response(200, json={"embeddings": [{"values": [3.0, 4.0, 0.0]} for _ in body["requests"]]})

    provider, requests = provider_with(handler)
    result = await provider.embed([f"text {i}" for i in range(150)], model="gemini-embedding-2", dimensions=3)

    assert len(requests) == 2  # 100 + 50
    assert len(result.vectors) == 150
    assert result.vectors[0] == [0.6, 0.8, 0.0]
    assert math.isclose(sum(v * v for v in result.vectors[-1]), 1.0)


async def test_embedding_count_mismatch_is_an_error():
    provider, _ = provider_with(lambda _: httpx.Response(200, json={"embeddings": []}))
    with pytest.raises(AIProviderError):
        await provider.embed(["a"], model="gemini-embedding-2", dimensions=3)


async def test_suspended_key_error_never_contains_the_key():
    # The real shape Google returned on 2026-09-24: a list, with the key quoted in the message.
    body = [
        {
            "error": {
                "code": 403,
                "message": f"Permission denied: Consumer 'api_key:{KEY}' has been suspended.",
                "status": "PERMISSION_DENIED",
                "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "CONSUMER_SUSPENDED"}],
            }
        }
    ]
    provider, _ = provider_with(lambda _: httpx.Response(403, json=body))
    with pytest.raises(AIProviderError) as exc:
        await provider.generate(TextRequest(model="m", prompt="x"))
    assert exc.value.code == "AI_KEY_SUSPENDED" and not exc.value.retryable
    assert KEY not in exc.value.message


async def test_provider_messages_are_redacted():
    message = f"Invalid argument for api_key:{KEY} and also {KEY} and api_key:someOtherKey123"
    provider, _ = provider_with(lambda _: httpx.Response(400, json={"error": {"message": message}}))
    with pytest.raises(AIProviderError) as exc:
        await provider.generate(TextRequest(model="m", prompt="x"))
    assert exc.value.code == "AI_BAD_REQUEST"
    assert KEY not in exc.value.message and "someOtherKey123" not in exc.value.message
    assert "[redacted]" in exc.value.message


async def test_rate_limit_reads_wait_from_message():
    # The real free-tier response seen on 2026-09-24.
    message = (
        "Rate limit exceeded for model gemini-3.8-flash (limit: 5 requests per minute on Free Tier). "
        "Please retry in 41s or upgrade your tier at https://ai.dev/rate-limit."
    )
    provider, _ = provider_with(lambda _: httpx.Response(429, json=[{"error": {"code": 429, "message": message}}]))
    with pytest.raises(AIProviderError) as exc:
        await provider.generate(TextRequest(model="gemini-3.8-flash", prompt="x"))
    assert exc.value.code == "AI_RATE_LIMITED" and exc.value.retry_after == 41.0
