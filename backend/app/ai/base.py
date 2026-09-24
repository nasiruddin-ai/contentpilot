"""Provider-neutral AI interface (spec section 28). Business logic never talks to a
vendor directly: Router → Service → AIService → AIProvider."""

import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass


class AIProviderError(Exception):
    def __init__(
        self, code: str, message: str, *, retryable: bool = False, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after


@dataclass
class TextRequest:
    model: str
    prompt: str
    system: str | None = None
    max_output_tokens: int = 2048
    # Provider-specific reasoning effort, e.g. Gemini's "minimal" | "low" | "medium" | "high".
    thinking_level: str | None = None
    # When set, the provider must return JSON matching this JSON Schema.
    json_schema: dict | None = None


@dataclass
class TextResult:
    text: str
    model: str
    input_tokens: int
    # Includes any hidden reasoning tokens, since those are billed as output.
    output_tokens: int


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    input_tokens: int


class AIProvider(ABC):
    name: str

    @abstractmethod
    async def generate(self, request: TextRequest) -> TextResult: ...

    @abstractmethod
    async def embed(self, texts: list[str], *, model: str, dimensions: int) -> EmbeddingResult: ...

    async def generate_image(self, prompt: str, *, model: str) -> bytes:
        # Arrives with visual generation (spec prompt 10).
        raise AIProviderError("AI_NOT_SUPPORTED", f"{self.name} image generation isn't available yet.")


def inline_json_schema(schema: dict) -> dict:
    """Resolves local $refs and drops cosmetic keys, for providers that accept only
    a plain JSON Schema subset. Pydantic models must not be recursive."""
    definitions = schema.get("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].rsplit("/", 1)[-1]
                return resolve(copy.deepcopy(definitions[name]))
            return {k: resolve(v) for k, v in node.items() if k not in ("$defs", "title")}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)
