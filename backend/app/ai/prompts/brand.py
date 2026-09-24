"""Brand context shared by every generation workflow (spec sections 32 and 64)."""

from app.models import Brand


def brand_context(brand: Brand) -> str:
    fields = [
        ("Brand", brand.name),
        ("Website", brand.website),
        ("Industry", brand.industry),
        ("Description", brand.description),
        ("Audience", brand.audience),
        ("Market", brand.market),
        ("Goals", ", ".join(brand.goals)),
        ("Tone", ", ".join(brand.tone)),
        ("Visual style", ", ".join(brand.visual_style)),
        ("Preferred words", ", ".join(brand.preferred_words)),
        ("Banned words (never use)", ", ".join(brand.banned_words)),
    ]
    lines = [f"{label}: {value}" for label, value in fields if value]
    if brand.content_pillars:
        mix = ", ".join(f"{p.pillar} {p.weight}%" for p in brand.content_pillars)
        lines.append(f"Content pillar mix: {mix}")
    return "\n".join(lines)
