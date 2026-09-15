"""Scratchpad's LangGraph backend — a freeform thinking surface + skills.

One turn = one run through the graph:

    interpret ─► route ─► note | expand | tighten | brainstorm | critique
                                  | build | respond ─► END

``interpret`` produces a validated :class:`TurnPlan` (structured output, temp 0)
and the router trusts it — no regex overrides. Scratchpad nodes return an updated
:class:`Scratchpad`; the ``build`` node runs a skill against a scratchpad
snapshot and appends a :class:`DerivedArtifact` (a tab) without touching the
scratchpad. ``astream_conversation`` streams each version, the derived artifact,
and the reply tokens to the AG-UI layer. Thread state is the SQLite checkpointer;
the scratchpad version list is a table in the same DB; per-user memory is JSON.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from backend.artifact import (
    DerivedArtifact,
    Scratchpad,
    apply_client_edits,
    ensure_scratchpad,
)
from backend.capabilities.skills import run_skill
from backend.capabilities.writing import (
    brainstorm,
    chat_reply,
    critique,
    expand,
    follow_ups,
    grounding_notes,
    tighten,
)
from backend.config import settings
from backend.llm import get_chat_model
from backend.memory_store import load_memory, save_memory
from backend.policy import check_output, check_skill, preflight
from backend.prompts import (
    CURRENT_PROMPT_VERSION,
    DISALLOWED_REPLY,
    PUBLISH_REPLY,
    SCRATCHPAD_SYSTEM_PROMPT,
    interpret_prompt,
    skill_menu_reply,
)
from backend.signal_models import TurnPlan
from backend.skills.registry import get_skill
from backend.threads_store import touch_thread
from backend.tracing import flush as flush_traces
from backend.tracing import build_turn_handler, get_langfuse_handler, trace_metadata
from backend.textutil import (
    dedupe,
    is_placeholder_only,
    looks_like_echo,
    unwrap_model_text,
    word_count,
)
from backend.versions import (
    append_version,
    artifact_changed,
    load_versions,
    replace_versions,
    version_items,
)

logger = logging.getLogger(__name__)

_STREAM_EVERY = 24  # tokens between mid-stream snapshots


class SignalState(TypedDict, total=False):
    user_id: str
    conversation_id: str
    user_message: str
    client_artifact: dict[str, Any]
    memory: dict[str, Any]

    turns: list[dict[str, Any]]
    summary: str

    plan: dict[str, Any]
    scratchpad: dict[str, Any]
    prior_scratchpad: dict[str, Any]  # pre-turn snapshot, for the follow-up diff
    derived: list[dict[str, Any]]
    assistant_message: str
    status: str
    trajectory: list[dict[str, Any]]
    # facts retrieved from the account's ingest knowledge graph for this turn,
    # or None if there was nothing to ground with — this-turn-only, never persisted
    grounding: list[dict[str, Any]] | None


# ---------------------------------------------------------------------------
# streaming helpers
# ---------------------------------------------------------------------------

def _emit(payload: dict[str, Any]) -> None:
    """Push a payload to the active stream, or no-op outside a stream."""

    try:
        writer = get_stream_writer()
    except RuntimeError:  # pragma: no cover - not in a streaming context
        return
    if writer is not None:
        writer(payload)


def _emit_scratchpad(scratchpad: Scratchpad) -> None:
    _emit({"type": "scratchpad", "scratchpad": scratchpad.model_dump()})


def _emit_reply(text: str) -> None:
    if text:
        _emit({"type": "reply", "delta": text})


def _emit_derived(derived: dict[str, Any]) -> None:
    _emit({"type": "derived", "derived": derived})


def _emit_follow_ups(items: list[dict[str, str]]) -> None:
    if items:
        _emit({"type": "follow_ups", "items": items})


def _emit_sources(items: list[dict[str, str]]) -> None:
    if items:
        _emit({"type": "sources", "items": items})


def _emit_follow_up_status(phase: str) -> None:
    """``working`` before the creative agent runs, ``done`` after."""

    _emit({"type": "follow_up_status", "phase": phase})


def _emit_choice(choice_id: str, question: str, options: list[str]) -> None:
    """Ask the client to render a picker; consumed by the AG-UI layer as a
    ``request_choice`` tool call. No-ops for fewer than two real options."""

    opts = [o.strip() for o in options if isinstance(o, str) and o.strip()]
    if len(opts) < 2:
        return
    _emit(
        {
            "type": "ui_choice",
            "id": choice_id,
            "question": question,
            "options": [{"id": str(i), "label": opt} for i, opt in enumerate(opts)],
        }
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(text: str | None) -> str:
    return " ".join((text or "").lower().split())


# ---------------------------------------------------------------------------
# turn interpretation
# ---------------------------------------------------------------------------

def _rollup(summary: str, turns: list[dict[str, Any]]) -> str:
    """Compress older turns into ``summary`` once the transcript gets long."""

    if len(turns) <= settings.summarize_after:
        return summary
    keep = settings.history_window
    older = turns[:-keep]
    try:
        model = get_chat_model(temperature=0.0, tags=["signal:summary"])
        response = model.invoke(
            [
                SystemMessage(
                    content=(
                        "Summarize this idea-development conversation so far in "
                        "4-6 sentences: the subject, confirmed facts, the "
                        "direction taking shape, and open questions. No preamble."
                    )
                ),
                HumanMessage(content=json.dumps(older, ensure_ascii=False)),
            ]
        )
        text = getattr(response, "content", "")
        return text if isinstance(text, str) and text.strip() else summary
    except Exception:  # pragma: no cover - defensive
        return summary


def _seed_scratchpad(state: SignalState, plan: TurnPlan) -> Scratchpad:
    """Carry the scratchpad forward, folding in client edits and plan hints."""

    scratch = ensure_scratchpad(state.get("scratchpad"))
    scratch = apply_client_edits(scratch, state.get("client_artifact"))

    # Subject rename mid-conversation: rebase onto the new subject and drop
    # context scoped to the old one. This turn's confirmed_facts are re-added.
    if plan.subject_changed and plan.topic and _norm(plan.topic) != _norm(scratch.topic):
        scratch = scratch.model_copy(
            update={
                "topic": plan.topic,
                "title": plan.topic,
                "sources": [],
                "open_questions": [],
                "angles": [],
                "outline": [],
                "tags": [],
            }
        )

    update: dict[str, Any] = {}
    if plan.product_mode:
        update["product_mode"] = plan.product_mode
    if plan.topic:
        update["topic"] = plan.topic
        if not scratch.title:
            update["title"] = plan.topic
    if update:
        scratch = scratch.model_copy(update=update)
    if plan.confirmed_facts:
        scratch = scratch.with_sources(plan.confirmed_facts)
    if scratch.status == "empty" and (scratch.sources or scratch.topic or scratch.body):
        scratch = scratch.model_copy(update={"status": "notes"})
    return scratch


_INTERPRET_STATUS = {
    "note": "notes",
    "expand": "developing",
    "tighten": "developing",
    "brainstorm": "notes",
    "critique": "developing",
    "build": "developing",
    "chat": "notes",
}


def _interpret(state: SignalState) -> SignalState:
    turns = state.get("turns", [])
    summary = _rollup(state.get("summary", ""), turns)

    prior = apply_client_edits(
        ensure_scratchpad(state.get("scratchpad")), state.get("client_artifact")
    )

    plan = TurnPlan(mode="chat", reply_gist="acknowledge and offer a next step")
    try:
        model = get_chat_model(
            temperature=settings.interpret_temperature, tags=["signal:interpret"]
        )
        structured = model.with_structured_output(TurnPlan)
        result = structured.invoke(
            [
                SystemMessage(content=SCRATCHPAD_SYSTEM_PROMPT),
                HumanMessage(
                    content=interpret_prompt(
                        summary=summary,
                        turns=turns[-settings.history_window :],
                        scratchpad=prior.model_dump(),
                        memory=state.get("memory", {}),
                        user_message=state["user_message"],
                    )
                ),
            ]
        )
        if isinstance(result, TurnPlan):
            plan = result
        elif isinstance(result, dict):
            plan = TurnPlan.model_validate(result)
    except Exception as error:  # pragma: no cover - defensive
        logger.warning("turn interpretation failed, defaulting to chat: %s", error)

    decision = preflight(state["user_message"])
    if decision.flag != "ok":
        plan.safety_flag = decision.flag

    scratch = _seed_scratchpad(state, plan)

    status = _INTERPRET_STATUS.get(plan.mode, "notes")
    if scratch.status not in ("empty",) and plan.mode in ("chat", "critique", "build"):
        status = scratch.status
    if plan.clarifying_question or plan.safety_flag != "ok":
        status = "needs_input"

    return {
        **state,
        "summary": summary,
        "plan": plan.model_dump(),
        "scratchpad": scratch.model_dump(),
        "prior_scratchpad": prior.model_dump(),
        "status": status,
        "trajectory": [
            *state.get("trajectory", []),
            {"step": "interpret", "mode": plan.mode, "status": status, "at": _now()},
        ],
    }


# ---------------------------------------------------------------------------
# knowledge-base grounding (read-only, runs before routing)
# ---------------------------------------------------------------------------

def _library_items_as_facts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adapt ``runs_store.library_items`` rows into the same
    ``{"fact", "valid_at", "sources": [{"title", "url"}]}`` shape
    ``blog_graph.query`` returns, so every downstream consumer
    (``chat_reply``, ``_match_sources``, the SOURCES-line contract) can
    treat "here is the article list" the same as "here are some facts" —
    no separate payload shape to teach the model about.
    """

    facts: list[dict[str, Any]] = []
    for item in items:
        title = item.get("title") or item.get("url") or "(untitled)"
        published = item.get("published_at")
        fact = f"The user has an ingested article titled '{title}'" + (
            f", published {published}." if published else "."
        )
        facts.append(
            {
                "fact": fact,
                "valid_at": str(published) if published else None,
                "sources": [{"title": title, "url": item.get("url") or ""}],
            }
        )
    return facts


async def _ground(state: SignalState) -> SignalState:
    """Best-effort retrieval from the account's ingest knowledge graph.

    Runs on every turn, but does almost nothing for the common case (an
    account with nothing ingested): one cheap count check, then out. Never
    blocks or breaks a turn on failure — same fail-open posture as
    ``_follow_up``. See docs/plan-chat-knowledge-graph-integration.md §4.3
    option (a).

    Additive, not either/or: the existing per-fact semantic search
    (``blog_graph.query``) always runs when there's anything ingested —
    it's the right tool for content questions ("what did I say about X").
    When ``plan.asks_about_knowledge_base`` (set by ``interpret``) is also
    true — a meta-question about the corpus itself, e.g. "summarize my
    last 3 blogs", "what are your sources for the blogs you've generated"
    — the article list (title + date, ``runs_store.library_items``) is
    layered on top, because there is no fact edge in the graph meaning
    "this is one of your 3 most recent posts"; semantic search alone
    structurally can't answer that shape of question. Merging rather than
    branching means a misclassified flag (model says yes when the message
    is really a content question, or vice versa) never costs the turn its
    real answer — it can only ever add a bit of harmless extra context,
    per the "judge relevance yourself, ignore what's not useful" contract
    every consumer of ``knowledge_base_facts`` already follows.
    """

    user_id = state.get("user_id") or ""
    plan = state.get("plan") or {}
    grounding: list[dict[str, Any]] | None = None
    if user_id:
        try:
            from backend.db import database_configured
            from backend.ingest import runs_store
            from backend.ingest.blog import graph as blog_graph

            if database_configured() and await runs_store.ingested_count(user_id) > 0:
                facts = await blog_graph.query(
                    user_id, state["user_message"], num_results=5
                )
                if plan.get("asks_about_knowledge_base"):
                    items = await runs_store.library_items(user_id)
                    facts = [*_library_items_as_facts(items), *facts]
                grounding = facts or None
        except Exception:  # noqa: BLE001 - grounding is never load-bearing
            logger.exception("grounding lookup failed for %s", user_id)
            grounding = None
    return {
        **state,
        "grounding": grounding,
        "trajectory": [
            *state.get("trajectory", []),
            {"step": "ground", "found": bool(grounding), "at": _now()},
        ],
    }


def _route(state: SignalState) -> str:
    plan = state["plan"]
    scratch = state.get("scratchpad", {}) or {}
    if plan.get("safety_flag", "ok") != "ok" or plan.get("clarifying_question"):
        return "respond"

    mode = plan.get("mode", "chat")
    has_body = bool((scratch.get("body") or "").strip())

    if mode == "note":
        return "note"
    if mode == "expand":
        return "expand"
    if mode == "tighten":
        return "tighten" if has_body else "note"
    if mode == "brainstorm":
        return "brainstorm"
    if mode == "critique":
        return "critique" if has_body else "respond"
    if mode == "build":
        return "build"
    return "respond"


# ---------------------------------------------------------------------------
# work nodes
# ---------------------------------------------------------------------------

def _brief(state: SignalState) -> dict[str, Any]:
    plan = state["plan"]
    scratch = state.get("scratchpad", {}) or {}
    return {
        "product_mode": scratch.get("product_mode", "existing"),
        "topic": scratch.get("topic") or plan.get("topic") or "",
        "body": scratch.get("body", ""),
        "sources": scratch.get("sources", []),
        "angle": plan.get("chosen_angle") or (scratch.get("angles") or [None])[0],
        "outline": scratch.get("outline", []),
        "open_questions": scratch.get("open_questions", []),
    }


def _finish(
    state: SignalState,
    *,
    node: str,
    scratch: Scratchpad,
    message: str,
    status: str,
) -> SignalState:
    _emit_scratchpad(scratch)
    _emit_reply(message)
    return {
        **state,
        "scratchpad": scratch.model_dump(),
        "assistant_message": message,
        "status": status,
        "turns": [*state.get("turns", []), {"role": "assistant", "content": message}],
        "trajectory": [
            *state.get("trajectory", []),
            {"step": node, "status": status, "at": _now()},
        ],
    }


def _op_failed(state: SignalState, node: str) -> SignalState:
    message = (
        "That came back empty. Tell me a bit more about what you want on the "
        "scratchpad and I'll try again."
    )
    _emit_reply(message)
    return {
        **state,
        "assistant_message": message,
        "status": "needs_input",
        "turns": [*state.get("turns", []), {"role": "assistant", "content": message}],
        "trajectory": [
            *state.get("trajectory", []),
            {"step": node, "status": "needs_input", "at": _now()},
        ],
    }


def _rebase_note(state: SignalState, scratch: Scratchpad) -> str:
    if state["plan"].get("subject_changed"):
        return (
            f"Rebased the scratchpad onto {scratch.topic or 'the new subject'} and "
            "cleared the old fact list — re-share anything you want kept. "
        )
    return ""


_REQUEST_PHRASE_RE = re.compile(r"\b(can you|could you|would you|please)\b", re.I)
_GENERATION_VERB_RE = re.compile(
    r"\b(add|generate|give me|list|write|note down|put|come up with|create)\b", re.I
)


def _looks_like_generation_request(message: str) -> bool:
    """True when the raw message reads as a request for the ASSISTANT to
    produce content ("can you add ... ?"), not a message that already IS
    the content ("jot this down: X", where the ":" marks real payload
    following the instruction). Used only to decide whether falling back
    to the raw message as `note_text` would silently insert the command
    itself into the scratchpad instead of real content — see
    docs/tasklist.md §1.2 ("top 500 points" bug)."""

    if ":" in message:
        return False
    has_request_phrase = bool(_REQUEST_PHRASE_RE.search(message)) or message.rstrip().endswith("?")
    has_generation_verb = bool(_GENERATION_VERB_RE.search(message))
    return has_request_phrase and has_generation_verb


_NOTE_GENERATION_FAILED_MESSAGE = (
    "That's a lot to generate reliably in one go — try a smaller number, or "
    "share a few real points yourself and I'll build on them."
)


def _note(state: SignalState) -> SignalState:
    scratch = ensure_scratchpad(state["scratchpad"])
    user_message = state["user_message"]
    note_text = state["plan"].get("note_text")

    # Deterministic guardrail, not just prompt wording: `interpret` is a
    # classification call being asked to also generate open-ended content
    # in this one field, and it sometimes fails silently — returning
    # `note_text: null` for a message that is a request ("can you add the
    # top 500 points...?"), not raw content, or returning `note_text` that
    # just echoes the request back. Either way, falling through to the
    # existing `note_text or user_message` default would insert the user's
    # own command into the scratchpad instead of real content. Catch both
    # failure shapes here rather than trusting the prompt never to slip.
    if note_text and looks_like_echo(note_text, user_message):
        note_text = None
    if note_text is None and _looks_like_generation_request(user_message):
        _emit_reply(_NOTE_GENERATION_FAILED_MESSAGE)
        return {
            **state,
            "assistant_message": _NOTE_GENERATION_FAILED_MESSAGE,
            "status": "needs_input",
            "turns": [
                *state.get("turns", []),
                {"role": "assistant", "content": _NOTE_GENERATION_FAILED_MESSAGE},
            ],
            "trajectory": [
                *state.get("trajectory", []),
                {"step": "note", "status": "needs_input", "at": _now()},
            ],
        }

    text = (note_text or user_message).strip()
    body = f"{scratch.body}\n\n{text}".strip() if scratch.body else text
    scratch = scratch.model_copy(update={"body": body}).touched(status="notes")
    message = (
        _rebase_note(state, scratch)
        + "Added that to the scratchpad. Want me to develop it, or put a few "
        "angles on the board?"
    )
    return _finish(state, node="note", scratch=scratch, message=message, status="notes")


def _split_trailing_sources_marker(text: str) -> tuple[str, list[str]]:
    """If ``text`` ends with a ``SOURCES: <title>|<title>`` line, strip it and
    return the titles; otherwise return ``text`` unchanged and no titles.
    Shared by every scratchpad-writing node that can receive
    ``knowledge_base_facts`` — see ``_stream_grounded_reply`` below for the
    live-streaming variant of the same convention."""

    stripped = text.rstrip()
    nl = stripped.rfind("\n")
    tail = stripped[nl + 1 :] if nl != -1 else stripped
    if tail.upper().startswith(_SOURCES_PREFIX):
        titles = [t.strip() for t in tail[len(_SOURCES_PREFIX):].split("|") if t.strip()]
        clean = stripped[:nl] if nl != -1 else ""
        return clean.rstrip(), titles
    return text, []


def _visible_prefix(text: str) -> str:
    """Best-effort trim of a knowledge-base SOURCES marker that might still be
    forming, for a live interim snapshot while a scratchpad-writing node is
    still streaming. Exact stripping happens once the stream ends, via
    ``_split_trailing_sources_marker``."""

    nl = text.rfind("\n")
    tail = text[nl + 1 :] if nl != -1 else text
    maybe_marker = bool(tail) and (
        _SOURCES_PREFIX.startswith(tail.upper()) or tail.upper().startswith(_SOURCES_PREFIX)
    )
    return (text[:nl] if nl != -1 else "") if maybe_marker else text


def _develop(state: SignalState, *, node: str, streamer) -> SignalState:
    base = ensure_scratchpad(state["scratchpad"])
    instruction = state["plan"].get("edit_instruction") or state["user_message"]
    knowledge_facts = state.get("grounding") or []

    parts: list[str] = []
    since_flush = 0
    for delta in streamer(base.model_dump(), instruction, knowledge_facts):
        parts.append(delta)
        since_flush += 1
        if since_flush >= _STREAM_EVERY:
            since_flush = 0
            _emit_scratchpad(
                base.model_copy(
                    update={
                        "body": unwrap_model_text(_visible_prefix("".join(parts))),
                        "status": "developing",
                    }
                )
            )
    raw_body = unwrap_model_text("".join(parts))
    body, source_titles = _split_trailing_sources_marker(raw_body)
    # Same guardrail class as `_note()`'s: don't trust the prompt alone to
    # never degenerate on a hard instruction (e.g. "add the top 500
    # points") — catch a body that's non-empty but only a placeholder
    # marker, or one that just echoes the instruction back, before it
    # overwrites the scratchpad with something that looks like content
    # but isn't. See docs/tasklist.md §1.2.
    if (
        not check_output(body).allowed
        or is_placeholder_only(body)
        or looks_like_echo(body, instruction)
    ):
        return _op_failed(state, node)

    notes = grounding_notes(body, base.sources, base.product_mode)
    grounded = _match_sources(source_titles, knowledge_facts) if source_titles else []
    scratch = base.model_copy(
        update={"body": body, "grounded_sources": grounded}
    ).with_questions(notes)
    scratch = scratch.touched(status="developing")

    verb = "Developed" if node == "expand" else "Tightened"
    message = _rebase_note(state, scratch) + f"{verb} the scratchpad ({word_count(body)} words)."
    if notes:
        message += f" Flagged {len(notes)} thing{'s' if len(notes) != 1 else ''} to confirm."
    return _finish(state, node=node, scratch=scratch, message=message, status="developing")


def _expand(state: SignalState) -> SignalState:
    return _develop(state, node="expand", streamer=expand)


def _tighten(state: SignalState) -> SignalState:
    return _develop(state, node="tighten", streamer=tighten)


def _brainstorm(state: SignalState) -> SignalState:
    brief = _brief(state)
    knowledge_facts = state.get("grounding") or []
    result = brainstorm(brief, knowledge_facts)
    base = ensure_scratchpad(state["scratchpad"])
    picked = bool(result["outline"])
    grounded = (
        _match_sources(result["sources_used"], knowledge_facts)
        if result.get("sources_used")
        else []
    )
    scratch = base.model_copy(
        update={
            "angles": dedupe([*base.angles, *result["angles"]]) if not picked else base.angles,
            "outline": result["outline"] or base.outline,
            "grounded_sources": grounded,
        }
    )
    scratch = scratch.with_questions(result["open_questions"]).touched(status="notes")

    if picked:
        message = (
            f"Outlined it — {len(scratch.outline)} beats on the board. "
            "Want me to develop any of them on the scratchpad?"
        )
    else:
        message = (
            f"Put {len(result['angles'])} angles on the board. "
            "Tell me which to run with (or mix two)."
        )
        _emit_choice(f"angle-v{scratch.version}", "Which angle should I run with?", scratch.angles)
    return _finish(state, node="brainstorm", scratch=scratch, message=message, status="notes")


def _critique(state: SignalState) -> SignalState:
    base = ensure_scratchpad(state["scratchpad"])
    knowledge_facts = state.get("grounding") or []
    review = critique(base.model_dump(), knowledge_facts)
    grounded = (
        _match_sources(review.sources_used, knowledge_facts) if review.sources_used else []
    )
    scratch = base.model_copy(
        update={
            "open_questions": dedupe([*base.open_questions, *review.points]),
            "grounded_sources": grounded,
        }
    ).touched(status=base.status if base.status != "empty" else "developing")
    message = review.summary.strip() or "Reviewed the scratchpad — notes are on the board."
    return _finish(state, node="critique", scratch=scratch, message=message, status=scratch.status)


def _build(state: SignalState) -> SignalState:
    plan = state["plan"]
    skill_id = plan.get("skill_id")
    scratch = ensure_scratchpad(state["scratchpad"])

    if not check_skill(skill_id).allowed:
        message = skill_menu_reply()
        _emit_reply(message)
        return {
            **state,
            "assistant_message": message,
            "status": "needs_input",
            "turns": [*state.get("turns", []), {"role": "assistant", "content": message}],
            "trajectory": [
                *state.get("trajectory", []),
                {"step": "build", "status": "needs_input", "at": _now()},
            ],
        }

    skill = get_skill(skill_id)
    hints = {
        k: plan.get(k) for k in ("tone", "length", "audience", "cta") if plan.get(k)
    }
    # One tab per skill: a stable id so a re-run replaces that tab's content
    # rather than opening a new one.
    derived_id = f"d-{skill.id}"
    from_version = scratch.version

    def _snapshot(body: str, open_questions: list[str] | None = None) -> dict[str, Any]:
        return DerivedArtifact(
            id=derived_id,
            skill_id=skill.id,
            skill_name=skill.name,
            title=skill.name,
            body=body,
            from_version=from_version,
            open_questions=open_questions or [],
        ).model_dump()

    parts: list[str] = []
    since_flush = 0
    for delta in run_skill(skill, scratch.model_dump(), hints):
        parts.append(delta)
        since_flush += 1
        if since_flush >= _STREAM_EVERY:
            since_flush = 0
            _emit_derived(_snapshot(unwrap_model_text("".join(parts))))

    body = unwrap_model_text("".join(parts))
    if not check_output(body).allowed:
        return _op_failed(state, "build")

    notes = grounding_notes(body, scratch.sources, scratch.product_mode)
    derived = _snapshot(body, notes)
    _emit_derived(derived)

    message = f"Built a {skill.name.lower()} from v{from_version} — see the {skill.name} tab."
    if notes:
        message += (
            f" {len(notes)} claim{'s' if len(notes) != 1 else ''} in it "
            "aren't in your sources — listed on the tab."
        )
    _emit_reply(message)
    status = scratch.status if scratch.status != "empty" else "notes"
    prior = state.get("derived", [])
    if any(d.get("skill_id") == skill.id for d in prior):
        next_derived = [derived if d.get("skill_id") == skill.id else d for d in prior]
    else:
        next_derived = [*prior, derived]
    return {
        **state,
        "derived": next_derived,
        "assistant_message": message,
        "status": status,
        "turns": [*state.get("turns", []), {"role": "assistant", "content": message}],
        "trajectory": [
            *state.get("trajectory", []),
            {"step": "build", "status": status, "skill": skill.id, "at": _now()},
        ],
    }


_SOURCES_PREFIX = "SOURCES:"
_SOURCES_LOOKBEHIND = len(_SOURCES_PREFIX) + 4  # slack for the streaming cut point


def _stream_grounded_reply(
    turns: list[dict[str, Any]],
    scratchpad: dict[str, Any],
    reply_gist: str | None,
    knowledge_facts: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    """Stream ``chat_reply``'s tokens out via ``_emit_reply``. Returns
    ``(message_text, source_titles)``.

    With no ``knowledge_facts`` (the common case — most accounts have nothing
    ingested) this streams every delta immediately, byte-for-byte the same as
    before grounding existed. With facts present, the model may end its reply
    with a trailing ``SOURCES: <title>|<title>`` marker line — that line is
    held back from the live stream (never shown as prose) and parsed instead;
    ``source_titles`` is empty unless the model actually drew on a fact.
    """

    parts: list[str] = []

    if not knowledge_facts:
        for delta in chat_reply(turns, scratchpad, reply_gist, knowledge_facts):
            parts.append(delta)
            _emit_reply(delta)
        return "".join(parts).strip(), []

    pending = ""
    for delta in chat_reply(turns, scratchpad, reply_gist, knowledge_facts):
        parts.append(delta)
        pending += delta
        nl = pending.rfind("\n")
        tail = pending[nl + 1 :] if nl != -1 else pending
        maybe_marker = bool(tail) and (
            _SOURCES_PREFIX.startswith(tail.upper())
            or tail.upper().startswith(_SOURCES_PREFIX)
        )
        if maybe_marker:
            # Flush everything confirmed before the newline immediately —
            # only the (possible) marker line itself stays buffered.
            if nl != -1:
                _emit_reply(pending[: nl + 1])
                pending = tail
            continue
        if len(pending) > _SOURCES_LOOKBEHIND:
            cut = len(pending) - _SOURCES_LOOKBEHIND
            _emit_reply(pending[:cut])
            pending = pending[cut:]

    full = "".join(parts)
    tail = pending.strip()
    source_titles: list[str] = []
    if tail.upper().startswith(_SOURCES_PREFIX):
        source_titles = [
            t.strip() for t in tail[len(_SOURCES_PREFIX):].split("|") if t.strip()
        ]
        message = full[: len(full) - len(pending)].rstrip()
    else:
        _emit_reply(pending)
        message = full.strip()
    return message, source_titles


def _match_sources(
    titles: list[str], facts: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Resolve the model's cited article titles back to {title, url} pairs
    from the retrieved facts — only these get shown, never the raw facts."""

    wanted = {t.lower() for t in titles}
    seen: set[str] = set()
    matched: list[dict[str, str]] = []
    for fact in facts:
        for source in fact.get("sources") or []:
            title = (source.get("title") or "").strip()
            key = title.lower()
            if key and key in wanted and key not in seen:
                seen.add(key)
                matched.append({"title": title, "url": source.get("url") or ""})
    return matched


def _respond(state: SignalState) -> SignalState:
    plan = state["plan"]
    scratch = ensure_scratchpad(state["scratchpad"])

    if plan.get("safety_flag") == "publish_request":
        message = PUBLISH_REPLY
        _emit_reply(message)
    elif plan.get("safety_flag") == "disallowed":
        message = DISALLOWED_REPLY
        _emit_reply(message)
    elif plan.get("clarifying_question"):
        message = plan["clarifying_question"]
        _emit_reply(message)
    else:
        knowledge_facts = state.get("grounding") or []
        message, source_titles = _stream_grounded_reply(
            state.get("turns", [])[-settings.history_window :],
            scratch.model_dump(),
            plan.get("reply_gist"),
            knowledge_facts,
        )
        message = message or (
            "Tell me what you're thinking about and I'll get it onto the scratchpad."
        )
        if source_titles and knowledge_facts:
            _emit_sources(_match_sources(source_titles, knowledge_facts))

    unresolved = plan.get("clarifying_question") or plan.get("safety_flag", "ok") != "ok"
    status = "needs_input" if unresolved else (scratch.status if scratch.status != "empty" else "notes")
    _emit_scratchpad(scratch)
    return {
        **state,
        "assistant_message": message,
        "status": status,
        "turns": [*state.get("turns", []), {"role": "assistant", "content": message}],
        "trajectory": [
            *state.get("trajectory", []),
            {"step": "respond", "status": status, "at": _now()},
        ],
    }


# ---------------------------------------------------------------------------
# creative follow-up agent (read-only, runs after a scratchpad change)
# ---------------------------------------------------------------------------

def _follow_up(state: SignalState) -> SignalState:
    """Read the new scratchpad and stream 3-5 next-move buttons.

    Never mutates canonical state. Skips entirely when the turn didn't move the
    scratchpad (a greeting, a pure chat reply, an artifact-only turn routes past
    this node). Any failure inside is swallowed — follow-ups never break a turn.
    """

    if not artifact_changed(
        state.get("prior_scratchpad"), state.get("scratchpad")
    ):
        return {}
    try:
        _emit_follow_up_status("working")
        _emit_follow_ups(follow_ups(state.get("scratchpad") or {}))
    except Exception:  # noqa: BLE001 - advisory only
        logger.exception("follow-up agent failed")
    finally:
        _emit_follow_up_status("done")
    return {}


# ---------------------------------------------------------------------------
# graph
# ---------------------------------------------------------------------------

def _builder() -> StateGraph:
    graph = StateGraph(SignalState)
    graph.add_node("interpret", _interpret)
    graph.add_node("ground", _ground)
    for name, fn in (
        ("note", _note),
        ("expand", _expand),
        ("tighten", _tighten),
        ("brainstorm", _brainstorm),
        ("critique", _critique),
        ("build", _build),
        ("respond", _respond),
        ("follow_up", _follow_up),
    ):
        graph.add_node(name, fn)
    graph.add_edge(START, "interpret")
    graph.add_edge("interpret", "ground")
    graph.add_conditional_edges(
        "ground",
        _route,
        {
            "note": "note",
            "expand": "expand",
            "tighten": "tighten",
            "brainstorm": "brainstorm",
            "critique": "critique",
            "build": "build",
            "respond": "respond",
        },
    )
    # Every scratchpad-touching mode passes through the creative agent; `build`
    # (artifact-only) goes straight to END.
    for node in ("note", "expand", "tighten", "brainstorm", "critique", "respond"):
        graph.add_edge(node, "follow_up")
    graph.add_edge("follow_up", END)
    graph.add_edge("build", END)
    return graph


_GRAPH_BUILDER = _builder()


def _compile(checkpointer: Any):
    return _GRAPH_BUILDER.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------

def _thread_config(
    conversation_id: str, *, user_id: str = "system", turn_index: int | None = None
) -> dict[str, Any]:
    """``turn_index`` (0-based, this thread's Nth complete turn) pins this
    turn's LLM calls to a deterministic Langfuse trace id — see
    ``backend.tracing.build_turn_handler``. Omit it for read-only config
    (e.g. ``aget_thread_state``), which falls back to the cached shared
    handler since there's no turn to pin a trace to.
    """

    metadata: dict[str, Any] = {
        "conversation_id": conversation_id,
        "session_id": conversation_id,
        "user_id": user_id,
        "prompt_version": CURRENT_PROMPT_VERSION,
        **trace_metadata(
            conversation_id=conversation_id,
            user_id=user_id,
            prompt_version=CURRENT_PROMPT_VERSION,
        ),
    }
    if turn_index is not None:
        metadata["turn_index"] = turn_index
    config: dict[str, Any] = {
        "configurable": {"thread_id": conversation_id},
        "tags": ["signal", CURRENT_PROMPT_VERSION],
        "metadata": metadata,
    }
    if turn_index is not None:
        handler, _trace_id = build_turn_handler(conversation_id, turn_index)
    else:
        handler = get_langfuse_handler()
    if handler is not None:
        config["callbacks"] = [handler]
    return config


async def astream_conversation(
    *,
    user_id: str,
    conversation_id: str,
    user_message: str,
    client_artifact: dict[str, Any] | None = None,
    base_version: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run one turn, yielding normalized events:

    ``{"type": "scratchpad", "scratchpad": {...}}``
    ``{"type": "reply", "delta": "..."}``
    ``{"type": "derived", "derived": {...}}``
    ``{"type": "ui_choice", "id", "question", "options": [{"id", "label"}]}``
    ``{"type": "status", "status": "...", "node": "..."}``
    ``{"type": "final", "assistant_message", "scratchpad", "derived", "status",
       "plan", "versions", "head"}``

    ``base_version`` (1-based) is the scratchpad version the client was previewing
    when it sent. If it points before the tip, the run continues from that
    snapshot and every later version is discarded.
    """

    if not user_message.strip():
        raise ValueError("user_message must not be empty")

    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    memory = load_memory(user_id)

    versions = await load_versions(conversation_id)
    branching = base_version is not None and 1 <= base_version < len(versions)
    base_scratch = versions[base_version - 1]["artifact"] if branching else None

    async with AsyncSqliteSaver.from_conn_string(str(settings.db_path)) as checkpointer:
        graph = _compile(checkpointer)

        # A minimal, tracing-free config to read prior state — turn_index
        # (this thread's Nth complete turn so far) has to come from that
        # state before the real, trace-pinned config can be built below.
        prior = await graph.aget_state({"configurable": {"thread_id": conversation_id}})
        prior_values = prior.values if prior else {}
        turn_index = len(prior_values.get("turns", [])) // 2
        config = _thread_config(conversation_id, user_id=user_id, turn_index=turn_index)

        turns = list(prior_values.get("turns", []))
        turns.append({"role": "user", "content": user_message})

        seed = base_scratch if branching else prior_values.get("scratchpad", {})

        graph_input: SignalState = {
            "user_id": user_id,
            "conversation_id": conversation_id,
            "user_message": user_message,
            "client_artifact": {} if branching else (client_artifact or {}),
            "memory": memory,
            "turns": turns,
            "summary": prior_values.get("summary", ""),
            "scratchpad": seed or {},
            "derived": prior_values.get("derived", []),
            "trajectory": prior_values.get("trajectory", []),
        }

        collected_follow_ups: list[dict[str, str]] = []
        async for mode, chunk in graph.astream(
            graph_input, config, stream_mode=["custom", "updates"]
        ):
            if mode == "custom" and isinstance(chunk, dict):
                kind = chunk.get("type")
                if kind in {
                    "scratchpad",
                    "reply",
                    "ui_choice",
                    "derived",
                    "follow_ups",
                    "follow_up_status",
                    "sources",
                }:
                    if kind == "follow_ups":
                        collected_follow_ups = chunk.get("items", [])
                    yield chunk
            elif mode == "updates" and isinstance(chunk, dict):
                for node, update in chunk.items():
                    if isinstance(update, dict) and update.get("status"):
                        yield {"type": "status", "status": update["status"], "node": node}

        final = await graph.aget_state(config)
        values = final.values if final else {}

    # Persistence side-effects are best-effort: a failure here (e.g. disk full)
    # must not lose the turn the user just watched happen.
    try:
        save_memory(user_id, values.get("plan", {}), values.get("scratchpad", {}))
    except Exception:  # noqa: BLE001
        logger.exception("save_memory failed for %s", user_id)

    try:
        await touch_thread(conversation_id, user_id=user_id, title=user_message)
    except Exception:  # noqa: BLE001
        logger.exception("touch_thread failed for %s", conversation_id)

    new_scratch = values.get("scratchpad", {})
    prev_scratch = (
        base_scratch if branching else (versions[-1]["artifact"] if versions else {})
    )
    if artifact_changed(prev_scratch, new_scratch):
        candidate = append_version(
            versions,
            new_scratch,
            user_message,
            base_seq=base_version if branching else None,
        )
        try:
            await replace_versions(conversation_id, candidate)
            versions = candidate
        except Exception:  # noqa: BLE001
            logger.exception("replace_versions failed for %s", conversation_id)

    yield {
        "type": "final",
        "assistant_message": values.get("assistant_message", ""),
        "scratchpad": new_scratch,
        "derived": values.get("derived", []),
        "follow_ups": collected_follow_ups,
        "status": values.get("status", ""),
        "plan": values.get("plan", {}),
        "trajectory": values.get("trajectory", []),
        "versions": version_items(versions),
        "head": len(versions),
        "turn_index": turn_index,
    }


def run_conversation(
    *,
    user_id: str,
    conversation_id: str,
    user_message: str,
    client_artifact: dict[str, Any] | None = None,
    base_version: int | None = None,
) -> dict[str, Any]:
    """Synchronous one-turn entry point (CLI + deterministic tests)."""

    async def _collect() -> dict[str, Any]:
        final: dict[str, Any] = {}
        async for event in astream_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
            user_message=user_message,
            client_artifact=client_artifact,
            base_version=base_version,
        ):
            if event["type"] == "final":
                final = event
        return final

    try:
        final = asyncio.run(_collect())
    finally:
        flush_traces()
    plan = final.get("plan", {})
    return {
        "assistant_message": final.get("assistant_message", ""),
        "response": final.get("assistant_message", ""),
        "conversation_id": conversation_id,
        "status": final.get("status", ""),
        "scratchpad": final.get("scratchpad", {}),
        "derived": final.get("derived", []),
        "follow_ups": final.get("follow_ups", []),
        "plan": plan,
        "mode": plan.get("mode"),
        "skill_id": plan.get("skill_id"),
        "pending_question": plan.get("clarifying_question"),
        "trajectory": final.get("trajectory", []),
        "versions": final.get("versions", []),
        "head": final.get("head", 0),
    }


async def aget_thread_state(conversation_id: str) -> dict[str, Any]:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(settings.db_path)) as checkpointer:
        graph = _compile(checkpointer)
        snapshot = await graph.aget_state(_thread_config(conversation_id))
    return snapshot.values if snapshot else {}


def get_thread_state(conversation_id: str) -> dict[str, Any]:
    return asyncio.run(aget_thread_state(conversation_id))
