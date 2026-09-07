"""Small deterministic text helpers shared across Signal.

These used to be duplicated in ``app.py`` and ``policy.py``; they live here now so
there is exactly one implementation of each.
"""

from __future__ import annotations

import re
from typing import Any

_MAX_WORDS_PATTERNS = (
    r"\b(?:under|less than|below|max(?:imum)?(?: of)?|up to|no more than)\s*(\d+)",
    r"\b(\d+)\s*words?\b",
)


def max_words(length: Any) -> int | None:
    """Return a hard word ceiling from a free-text length constraint, if any."""

    if not isinstance(length, str):
        return None
    lowered = length.lower()
    for pattern in _MAX_WORDS_PATTERNS:
        match = re.search(pattern, lowered)
        if match:
            return max(1, int(match.group(1)))
    return None


def enforce_max_words(text: str, ceiling: int | None) -> str:
    """Trim ``text`` to ``ceiling`` words when a ceiling is set."""

    if ceiling is None:
        return text
    words = text.split()
    if len(words) <= ceiling:
        return text
    return " ".join(words[:ceiling])


def word_count(text: str) -> int:
    return len(text.split())


def dedupe(items: list[str]) -> list[str]:
    """Order-preserving de-duplication of trimmed, non-empty strings."""

    seen: dict[str, None] = {}
    for item in items:
        if isinstance(item, str) and item.strip():
            seen.setdefault(item.strip(), None)
    return list(seen)
