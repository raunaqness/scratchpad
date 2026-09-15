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


def _normalized_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def looks_like_echo(candidate: str, source: str) -> bool:
    """True when ``candidate`` is basically ``source`` restated, not new
    content — the model failed to generate anything and fell back to
    paraphrasing/repeating the instruction it was given, instead of
    writing real content (see docs/tasklist.md §1.2, the "top 500
    points" bug: `note_text` sometimes came back as e.g. "top 500 points
    about China and AI" — a near-total subset of the request's own
    words, not a real point).

    Containment, not symmetric similarity: almost all of `candidate`'s
    words already exist in `source`, AND `candidate` isn't substantially
    longer than `source` — genuinely generated content reuses some
    words from the request (the topic) but also brings in many new ones
    and is usually longer; a bare echo/paraphrase does neither.
    """

    candidate_words = _normalized_words(candidate)
    source_words = set(_normalized_words(source))
    if not candidate_words or not source_words:
        return False
    contained = sum(1 for w in candidate_words if w in source_words)
    containment = contained / len(candidate_words)
    not_much_longer = len(candidate_words) <= len(source_words) * 1.5
    return containment > 0.85 and not_much_longer


_TK_MARKER_RE = re.compile(r"\[(?:TK|assumption)\s*:.*?\]", re.I | re.S)


def is_placeholder_only(text: str) -> bool:
    """True when the text contains a `[TK: ...]` / `[assumption: ...]`
    marker AND almost nothing else — the model gave up on generating
    real content and returned only a placeholder. Distinct from
    `check_output`'s plain empty-string check: a stream that yields e.g.
    just "[TK: confirm ...]" is non-empty but still has no usable content
    (see docs/tasklist.md §1.2, the "top 500 points" bug — this shape
    showed up via the `expand` path, not just `note`).

    Only fires when a marker is actually present — ordinary short-but-
    real content ("Developed.") has no marker at all and is never
    flagged, regardless of length.
    """

    if not _TK_MARKER_RE.search(text):
        return False
    stripped = _TK_MARKER_RE.sub("", text).strip()
    return len(_normalized_words(stripped)) < 5


def dedupe(items: list[str]) -> list[str]:
    """Order-preserving de-duplication of trimmed, non-empty strings."""

    seen: dict[str, None] = {}
    for item in items:
        if isinstance(item, str) and item.strip():
            seen.setdefault(item.strip(), None)
    return list(seen)
