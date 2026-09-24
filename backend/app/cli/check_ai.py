"""Live check of the configured AI provider. Makes three small real API calls.

    docker compose exec worker python -m app.cli.check_ai
"""

import asyncio
import sys

from pydantic import BaseModel

from app.ai.service import AIError, ModelTier, get_ai_service
from app.core.config import get_settings


class Check(BaseModel):
    topic: str
    angle: str
    audience: str


async def main() -> int:
    settings = get_settings()
    ai = get_ai_service()
    print(f"Provider: {ai.provider.name}")
    print(f"Models: fast={settings.gemini_fast_model} quality={settings.gemini_quality_model} "
          f"embedding={settings.gemini_embedding_model}")

    failures = 0
    checks = [
        ("text (fast model)", lambda: ai.generate_text(task="check_ai", prompt="Reply with exactly: OK", max_output_tokens=64)),
        (
            "structured JSON (quality model)",
            lambda: ai.generate_structured(
                task="check_ai",
                prompt="Suggest one social post idea for a Squarespace web design agency.",
                schema=Check,
                tier=ModelTier.QUALITY,
                max_output_tokens=1024,
            ),
        ),
        ("embeddings", lambda: ai.embed(task="check_ai", texts=["website conversion", "clear headlines"])),
    ]
    for name, run in checks:
        try:
            result = await run()
        except AIError as exc:
            failures += 1
            print(f"FAIL {name}: {exc.code} - {exc.message}")
            continue
        if isinstance(result, list):
            summary = f"{len(result)} vectors x {len(result[0])} dims"
        else:
            summary = repr(result)[:160]
        print(f"OK   {name}: {summary}")

    print("Each call is logged in the ai_runs table with tokens and estimated cost.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
