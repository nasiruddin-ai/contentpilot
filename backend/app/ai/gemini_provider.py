"""Google Gemini via the Interactions API (text) and embedContent (embeddings).

Reference: https://ai.google.dev/api/interactions-v1.md.txt
"""

import math
import re

import httpx

from app.ai.base import AIProvider, AIProviderError, EmbeddingResult, TextRequest, TextResult, inline_json_schema

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
GENERATE_TIMEOUT = httpx.Timeout(90.0, connect=10.0)
EMBED_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
EMBED_BATCH_SIZE = 100


class GeminiProvider(AIProvider):
    name = "gemini"

    def __init__(self, api_key: str | None, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._api_key = api_key
        self._transport = transport

    async def generate(self, request: TextRequest) -> TextResult:
        generation_config: dict = {"max_output_tokens": request.max_output_tokens}
        if request.thinking_level:
            generation_config["thinking_level"] = request.thinking_level
        body: dict = {
            "model": request.model,
            "input": request.prompt,
            "generation_config": generation_config,
            # Don't keep our prompts (which include customer data) on Google's side.
            "store": False,
        }
        if request.system:
            body["system_instruction"] = request.system
        if request.json_schema is not None:
            body["response_format"] = {
                "type": "text",
                "mime_type": "application/json",
                "schema": inline_json_schema(request.json_schema),
            }

        data = await self._post("/interactions", body, GENERATE_TIMEOUT)
        status = data.get("status")
        if status == "incomplete":
            raise AIProviderError("AI_OUTPUT_TRUNCATED", "The model hit its output limit before finishing.")
        if status != "completed":
            raise AIProviderError("AI_FAILED", f"The model did not complete (status: {status}).")

        text = data.get("output_text") or _output_text(data.get("steps", []))
        if not text:
            raise AIProviderError("AI_EMPTY_OUTPUT", "The model returned no text.")
        usage = data.get("usage") or {}
        return TextResult(
            text=text,
            model=data.get("model") or request.model,
            input_tokens=int(usage.get("total_input_tokens") or 0),
            output_tokens=int(usage.get("total_output_tokens") or 0) + int(usage.get("total_thought_tokens") or 0),
        )

    async def embed(self, texts: list[str], *, model: str, dimensions: int) -> EmbeddingResult:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[start : start + EMBED_BATCH_SIZE]
            body = {
                "requests": [
                    {
                        "model": f"models/{model}",
                        "content": {"parts": [{"text": text}]},
                        "outputDimensionality": dimensions,
                    }
                    for text in batch
                ]
            }
            data = await self._post(f"/models/{model}:batchEmbedContents", body, EMBED_TIMEOUT)
            embeddings = data.get("embeddings") or []
            if len(embeddings) != len(batch):
                raise AIProviderError("AI_FAILED", "The embedding response didn't match the request.")
            vectors.extend(_normalize(e.get("values") or []) for e in embeddings)
        # The embedding endpoint doesn't report usage; ~4 characters per token is Google's rule of thumb.
        return EmbeddingResult(vectors=vectors, model=model, input_tokens=sum(len(t) for t in texts) // 4)

    async def _post(self, path: str, body: dict, timeout: httpx.Timeout) -> dict:
        if not self._api_key:
            raise AIProviderError("AI_NOT_CONFIGURED", "No Gemini API key is set. Add GOOGLE_AI_API_KEY to .env.")
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=timeout, trust_env=False) as client:
                response = await client.post(
                    f"{BASE_URL}{path}", json=body, headers={"x-goog-api-key": self._api_key}
                )
        except httpx.TimeoutException:
            raise AIProviderError("AI_TIMEOUT", "The AI provider took too long to respond.", retryable=True) from None
        except httpx.TransportError:
            raise AIProviderError("AI_NETWORK_ERROR", "Couldn't reach the AI provider.", retryable=True) from None

        if response.status_code == 200:
            try:
                return response.json()
            except ValueError:
                raise AIProviderError("AI_FAILED", "The AI provider sent an unreadable response.") from None
        raise _error_for(response, self._api_key)


def _output_text(steps: list[dict]) -> str:
    parts = [
        block.get("text", "")
        for step in steps
        if step.get("type") == "model_output"
        for block in step.get("content") or []
        if block.get("type") == "text"
    ]
    return "".join(parts)


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector] if norm else vector


def _error_for(response: httpx.Response, api_key: str) -> AIProviderError:
    status = response.status_code
    error = _error_body(response)
    detail = _redact(str(error.get("message", ""))[:300], api_key)
    reasons = {d.get("reason") for d in error.get("details") or [] if isinstance(d, dict)}
    if status == 429 and re.search(r"limit:\s*0\b", detail):
        # A quota of zero means the model isn't in this plan at all; waiting won't help.
        return AIProviderError("AI_NOT_IN_PLAN", f"This model isn't included in your Gemini plan. {detail}".strip())
    if status == 429:
        return AIProviderError(
            "AI_RATE_LIMITED",
            "The AI provider's rate limit or quota was reached.",
            retryable=True,
            retry_after=_retry_after(response),
        )
    if "CONSUMER_SUSPENDED" in reasons:
        return AIProviderError(
            "AI_KEY_SUSPENDED", "Google has suspended this Gemini API key or its project. Create a new key."
        )
    if status in (401, 403):
        return AIProviderError("AI_AUTH_FAILED", "The Gemini API key was rejected. Check GOOGLE_AI_API_KEY.")
    if status == 404:
        return AIProviderError("AI_MODEL_NOT_FOUND", f"The AI model wasn't found. {detail}".strip())
    if status >= 500:
        return AIProviderError("AI_PROVIDER_ERROR", f"The AI provider had an error (HTTP {status}).", retryable=True)
    return AIProviderError("AI_BAD_REQUEST", f"The AI provider rejected the request. {detail}".strip())


def _error_body(response: httpx.Response) -> dict:
    """Google sends {"error": {...}}, sometimes wrapped in a list."""
    try:
        data = response.json()
    except ValueError:
        return {}
    if isinstance(data, list):
        data = data[0] if data else {}
    error = data.get("error") if isinstance(data, dict) else None
    return error if isinstance(error, dict) else {}


def _redact(message: str, api_key: str) -> str:
    # Google's error messages can quote the API key ("Consumer 'api_key:...'").
    # These messages end up in logs, ai_runs and API responses, so the key must never survive.
    if api_key:
        message = message.replace(api_key, "[redacted]")
    return re.sub(r"api_key:[^\s'\"]+", "api_key:[redacted]", message)


def _retry_after(response: httpx.Response) -> float | None:
    header = response.headers.get("retry-after", "")
    if header.isdigit():
        return float(header)
    # Google gives the wait either in the error details ("retryDelay": "17s")
    # or only in the message ("Please retry in 41s").
    match = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', response.text) or re.search(
        r"retry in (\d+(?:\.\d+)?)s", response.text, re.IGNORECASE
    )
    return float(match.group(1)) if match else None
