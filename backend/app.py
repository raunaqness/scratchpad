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
from backend.tracing import get_langfuse_handler, trace_metadata
from backend.textutil import dedupe, unwrap_model_text, word_count
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


def _note(state: SignalState) -> SignalState:
    scratch = ensure_scratchpad(state["scratchpad"])
    text = (state["plan"].get("note_text") or state["user_message"]).strip()
    body = f"{scratch.body}\n\n{text}".strip() if scratch.body else text
    scratch = scratch.model_copy(update={"body": body}).touched(status="notes")
    message = (
        _rebase_note(state, scratch)
        + "Added that to the scratchpad. Want me to develop it, or put a few "
        "angles on the board?"
    )
    return _finish(state, node="note", scratch=scratch, message=message, status="notes")


def _develop(state: SignalState, *, node: str, streamer) -> SignalState:
    base = ensure_scratchpad(state["scratchpad"])
    instruction = state["plan"].get("edit_instruction") or state["user_message"]

    parts: list[str] = []
    since_flush = 0
    for delta in streamer(base.model_dump(), instruction):
        parts.append(delta)
        since_flush += 1
        if since_flush >= _STREAM_EVERY:
            since_flush = 0
            _emit_scratchpad(
                base.model_copy(
                    update={
                        "body": unwrap_model_text("".join(parts)),
                        "status": "developing",
                    }
                )
            )
    body = unwrap_model_text("".join(parts))
    if not check_output(body).allowed:
        return _op_failed(state, node)

    notes = grounding_notes(body, base.sources, base.product_mode)
    scratch = base.model_copy(update={"body": body}).with_questions(notes)
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
    result = brainstorm(brief)
    base = ensure_scratchpad(state["scratchpad"])
    picked = bool(result["outline"])
    scratch = base.model_copy(
        update={
            "angles": dedupe([*base.angles, *result["angles"]]) if not picked else base.angles,
            "outline": result["outline"] or base.outline,
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
    review = critique(base.model_dump())
    scratch = base.model_copy(
        update={"open_questions": dedupe([*base.open_questions, *review.points])}
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
        parts: list[str] = []
        for delta in chat_reply(
            state.get("turns", [])[-settings.history_window :],
            scratch.model_dump(),
            plan.get("reply_gist"),
        ):
            parts.append(delta)
            _emit_reply(delta)
        message = "".join(parts).strip() or (
            "Tell me what you're thinking about and I'll get it onto the scratchpad."
        )

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
    graph.add_conditional_edges(
        "interpret",
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

def _thread_config(conversation_id: str, *, user_id: str = "system") -> dict[str, Any]:
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
    config: dict[str, Any] = {
        "configurable": {"thread_id": conversation_id},
        "tags": ["signal", CURRENT_PROMPT_VERSION],
        "metadata": metadata,
    }
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
        config = _thread_config(conversation_id, user_id=user_id)

        prior = await graph.aget_state(config)
        prior_values = prior.values if prior else {}
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
