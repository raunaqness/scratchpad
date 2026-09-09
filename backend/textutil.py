"""Small deterministic text helpers shared across Signal.

These used to be duplicated in ``app.py`` and ``policy.py``; they live here now so
there is exactly one implementation of each.
"""

from __future__ import annotations

import json
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


_WHOLE_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_+.-]*[ \t]*\r?\n(.*)\r?\n```$", re.DOTALL)
# Keys a model might wrap markdown prose in when it answers with a JSON envelope
# instead of the plain body we asked for.
_ENVELOPE_TEXT_KEYS = (
    "body",
    "scratchpad_body",
    "text",
    "content",
    "markdown",
    "md",
    "output",
    "result",
    "draft",
)


def unwrap_model_text(text: str) -> str:
    """Best-effort recovery of plain markdown from a stray model wrapper.

    Scratchpad ops and skills ask for the body as plain markdown, but a model
    sometimes returns it fenced (```` ```markdown ... ``` ````) or inside a JSON
    envelope (``{"title": ..., "body": "..."}``) — occasionally truncated by the
    token limit. Rendering that verbatim shows raw JSON in the artifact panel.
    This peels one such wrapper; anything it does not recognise is returned
    unchanged.
    """

    if not isinstance(text, str):
        return ""
    out = text.strip()

    match = _WHOLE_FENCE_RE.match(out)
    if match and "```" not in match.group(1):
        out = match.group(1).strip()

    if out[:1] in ("{", "["):
        if out[-1:] in ("}", "]"):
            try:
                return _from_envelope(json.loads(out), fallback=out)
            except (json.JSONDecodeError, ValueError):
                pass  # truncated / malformed — fall through to the regex lift
        lifted = _lift_text_field(out)
        if lifted is not None:
            return lifted
    return out


def _from_envelope(data: Any, *, fallback: str) -> str:
    if isinstance(data, dict):
        nested = data.get("scratchpad")
        if isinstance(nested, dict) and isinstance(nested.get("body"), str):
            body = nested["body"].strip()
            if body:
                return body
        for key in _ENVELOPE_TEXT_KEYS:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return fallback


_LIFT_KEYS = ("body", "scratchpad_body", "text", "content", "markdown")
_NEXT_KEY_RE = re.compile(r'"\s*,\s*"[a-zA-Z_]+"\s*:\s*[\[{"]?')


def _lift_text_field(blob: str) -> str | None:
    """Pull a ``"body": "..."`` value out of a JSON-ish blob the parser rejected."""

    for key in _LIFT_KEYS:
        exact = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', blob)
        if exact:
            return _json_unescape(exact.group(1))
        # Truncated mid-value: take the rest, trim an obvious next-key tail.
        partial = re.search(rf'"{key}"\s*:\s*"(.*)', blob, re.DOTALL)
        if partial:
            raw = _NEXT_KEY_RE.split(partial.group(1), maxsplit=1)[0]
            return _json_unescape(raw.rstrip().rstrip('"'))
    return None


def _json_unescape(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"')
    except (json.JSONDecodeError, ValueError):
        return raw.encode("utf-8", "ignore").decode("unicode_escape", "ignore")


def dedupe(items: list[str]) -> list[str]:
    """Order-preserving de-duplication of trimmed, non-empty strings."""

    seen: dict[str, None] = {}
    for item in items:
        if isinstance(item, str) and item.strip():
            seen.setdefault(item.strip(), None)
    return list(seen)
