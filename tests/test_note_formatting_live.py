"""Live-model regression test for the scratchpad note-formatting bug.

Reported live: asking the system to add multiple points to the scratchpad
("add some key points about why China is giving AI models for free")
produced `note_text` as one run-on sentence with inline "1. 2. 3." digits
— valid-looking prose, but not real markdown list syntax, so
`MarkdownPreview` (remark-gfm) correctly rendered it as a single paragraph
instead of a list. The fix is a formatting contract added to
`interpret_prompt`'s `note_text` field (backend/prompts.py) — see
docs/tasklist.md §1.2.

This is a live-model-only, structural check (real markdown list syntax:
each item starts a new line with "- " or "N. "), not an LLM-judged
GEval — no `deepeval` needed, but it does need a live OpenRouter key,
same skip condition as the DeepEval suites.
"""

from __future__ import annotations

import re

import pytest

from backend.app import run_conversation
from backend.config import settings

pytestmark = pytest.mark.skipif(
    not settings.openrouter_api_key or not settings.openrouter_model,
    reason="Set OPENROUTER_API_KEY and OPENROUTER_MODEL in .env before running live tests.",
)

_LIST_ITEM_RE = re.compile(r"(?m)^\s*(?:[-*]|\d+\.)\s+\S")


def _is_real_markdown_list(text: str) -> bool:
    """True when at least 2 lines start with a list-item marker — the
    minimum shape remark-gfm needs to actually render a <ul>/<ol>, as
    opposed to prose that merely contains digits like '1.' inline."""

    return len(_LIST_ITEM_RE.findall(text)) >= 2


def test_multi_point_note_request_produces_real_markdown_list(isolated_state):
    """The exact reported scenario: ask the system to add several points
    to an existing scratchpad — the resulting body must use real markdown
    list syntax (each point on its own line), not a run-on sentence with
    inline digits that looks like a list but isn't."""

    run_conversation(
        user_id="note-fmt-live",
        conversation_id="note-fmt-live-c1",
        user_message="i want to get a recap of china's AI investment",
    )
    result = run_conversation(
        user_id="note-fmt-live",
        conversation_id="note-fmt-live-c1",
        user_message=(
            "Okay, can you add some key points about why China is giving "
            "AI models for free to the scratchpad?"
        ),
    )

    body = result["scratchpad"]["body"]
    assert body.strip()
    assert _is_real_markdown_list(body), (
        "expected real markdown list syntax (each point on its own line "
        f"with '- ' or 'N. '), got:\n{body!r}"
    )


def test_implausible_count_request_never_inserts_the_command_itself(isolated_state):
    """Reported live: "Can you add the top 500 points to the scratchpad
    about China and AI?" resulted in the scratchpad body containing a
    rephrased echo of the command itself ("Add the top 500 points about
    China and AI to the scratchpad.") — `interpret` gave up generating
    for an implausible count and either returned `note_text: null`
    (falling back to the raw message) or a `note_text` that was itself
    just a restatement of the request. Fixed by a prompt rule
    (interpret_prompt's note_text contract) plus a deterministic
    code-level guardrail (`_note()` + `textutil.looks_like_echo`) that
    catches either failure shape and asks for a smaller number instead
    of silently writing the command into the scratchpad. See
    docs/tasklist.md §1.2.

    This test allows either acceptable outcome — real generated content,
    or the "ask for a smaller number" guardrail reply — as long as the
    one broken outcome (the command itself landing in the scratchpad)
    never happens, since the underlying interpret call is a live,
    non-deterministic model call."""

    message = "Can you add the top 500 points to the scratchpad about China and AI?"
    result = run_conversation(
        user_id="note-count-live",
        conversation_id="note-count-live-c1",
        user_message=message,
    )

    body = result["scratchpad"]["body"]
    from backend.textutil import looks_like_echo

    assert not looks_like_echo(body, message), (
        f"scratchpad body looks like an echo of the request, not real "
        f"content or an empty/guardrail response: {body!r}"
    )
    if body.strip():
        # if content was written, it must actually be a real markdown list,
        # not the guardrail path (which leaves body empty) and not prose
        # that merely mentions the topic without being a list of points
        assert _is_real_markdown_list(body) or len(body.split()) > 15, (
            f"body is neither a real list nor substantial content: {body!r}"
        )
    else:
        assert result["status"] == "needs_input"
