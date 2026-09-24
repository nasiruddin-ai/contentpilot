"""Exact and near-duplicate detection (spec section 26).

Semantic duplicates (same idea, different words) need embeddings and come with
vector search.
"""

import hashlib
import re

SIMHASH_BITS = 64
# Up to this many differing bits counts as the same text with small edits.
NEAR_DUPLICATE_DISTANCE = 3
# Below ~300 words a single edited word can move SimHash by 10+ bits (measured),
# so shorter texts rely on exact-hash and URL checks only.
NEAR_DUPLICATE_MIN_CHARS = 2000
_MAX_TOKENS = 20_000
_WORD = re.compile(r"\w+")


def content_hash(text: str) -> str:
    normalized = " ".join(_WORD.findall(text.lower()))
    return hashlib.sha256(normalized.encode()).hexdigest()


def simhash(text: str) -> int:
    """64-bit SimHash over word 3-grams, returned as a signed int for BIGINT storage."""
    tokens = _WORD.findall(text.lower())[:_MAX_TOKENS]
    shingles = [" ".join(tokens[i : i + 3]) for i in range(max(1, len(tokens) - 2))]

    weights = [0] * SIMHASH_BITS
    for shingle in shingles:
        value = int.from_bytes(hashlib.blake2b(shingle.encode(), digest_size=8).digest(), "big")
        for bit in range(SIMHASH_BITS):
            weights[bit] += 1 if value >> bit & 1 else -1

    unsigned = sum(1 << bit for bit, weight in enumerate(weights) if weight > 0)
    return unsigned - (1 << 64) if unsigned >= 1 << 63 else unsigned


def hamming_distance(a: int, b: int) -> int:
    return ((a ^ b) & ((1 << 64) - 1)).bit_count()


def is_near_duplicate(a: int, b: int) -> bool:
    return hamming_distance(a, b) <= NEAR_DUPLICATE_DISTANCE
