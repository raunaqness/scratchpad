"""Signal's LangGraph backend — a creative thinking-pad for content.

One turn = one run through the graph:

    interpret ─► route ─► brainstorm | draft | revise | critique | respond ─► END

``interpret`` produces a validated :class:`TurnPlan` (structured output, temp 0)
and the router trusts it — there are no regex overrides. Every work node returns
an updated artifact; ``astream_conversation`` streams each version (and the
reply tokens) to the AG-UI layer. Thread state is the SQLite checkpointer; the
only JSON left is per-user durable memory.
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

from backend.artifact import Artifact, apply_client_edits, ensure_artifact
from backend.capabilities.writing import (
    brainstorm,
    chat_reply,
    critique,
    grounding_notes,
    revise_content,
    write_content,
)
from backend.config import settings
from backend.llm import get_chat_model
from backend.memory_store import compact as compact_memory
from backend.memory_store import load_memory, save_memory
from backend.policy import check_draft, preflight
from backend.prompts import (
    CURRENT_PROMPT_VERSION,
    DISALLOWED_REPLY,
    PUBLISH_REPLY,
    THINKPAD_SYSTEM_PROMPT,
    interpret_prompt,
)
from backend.signal_models import TurnPlan
from backend.textutil import dedupe, enforce_max_words, max_words, word_count
from backend.versions import (
    append_version,
    artifact_changed,
    load_versions,
    replace_versions,
    version_items,
)

logger = logging.getLogger(__name__)

_ARTIFACT_STREAM_EVERY = 24  # tokens between mid-draft artifact snapshots


class SignalState(TypedDict, total=False):
    user_id: str
    conversation_id: str
    user_message: str
    client_artifact: dict[str, Any]
    memory: dict[str, Any]

    turns: list[dict[str, Any]]
    summary: str

    plan: dict[str, Any]
    artifact: dict[str, Any]
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


def _emit_artifact(artifact: Artifact) -> None:
    _emit({"type": "artifact", "artifact": artifact.model_dump()})


def _emit_reply(text: str) -> None:
    if text:
        _emit({"type": "reply", "delta": text})


def _emit_choice(choice_id: str, question: str, options: list[str]) -> None:
    """Ask the client to render a picker (radio buttons) for a set of options.

    Consumed by the AG-UI layer, which turns it into a ``request_choice`` tool
    call; the client renders it and a selection comes back as a normal user turn.
    Silently no-ops for fewer than two real options.
    """

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
    """Loose comparison key for subject strings."""

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
                        "Summarize this content-brainstorming conversation so far "
                        "in 4-6 sentences: the product/idea, confirmed facts, "
                        "chosen direction, and open questions. No preamble."
                    )
                ),
                HumanMessage(content=json.dumps(older, ensure_ascii=False)),
            ]
        )
        text = getattr(response, "content", "")
        return text if isinstance(text, str) and text.strip() else summary
    except Exception:  # pragma: no cover - defensive
        return summary


def _seed_artifact(state: SignalState, plan: TurnPlan) -> Artifact:
    """Carry the artifact forward, folding in client edits and plan hints."""

    artifact = ensure_artifact(state.get("artifact"))
    artifact = apply_client_edits(artifact, state.get("client_artifact"))

    # Subject rename mid-conversation: rebase the artifact onto the new subject.
    # Title/topic follow the new subject; context scoped to the old one
    # (facts, open questions, angles, outline) is dropped. This turn's
    # `confirmed_facts` are re-added below, so anything restated survives.
    if plan.subject_changed and plan.topic and _norm(plan.topic) != _norm(artifact.topic):
        artifact = artifact.model_copy(
            update={
                "topic": plan.topic,
                "title": plan.topic,
                "sources": [],
                "open_questions": [],
                "angles": [],
                "outline": [],
            }
        )

    update: dict[str, Any] = {}
    if plan.format:
        update["format"] = plan.format
    if plan.product_mode:
        update["product_mode"] = plan.product_mode
    if plan.topic:
        update["topic"] = plan.topic
        if not artifact.title:
            update["title"] = plan.topic
    if update:
        artifact = artifact.model_copy(update=update)
    if plan.confirmed_facts:
        artifact = artifact.with_sources(plan.confirmed_facts)
    if artifact.status == "empty" and (artifact.sources or artifact.topic):
        artifact = artifact.model_copy(update={"status": "exploring"})
    return artifact


def _interpret(state: SignalState) -> SignalState:
    turns = state.get("turns", [])
    summary = _rollup(state.get("summary", ""), turns)
    memory_view = compact_memory(state.get("memory", {}))

    prior_artifact = apply_client_edits(
        ensure_artifact(state.get("artifact")), state.get("client_artifact")
    )

    plan = TurnPlan(mode="chat", reply_gist="acknowledge and offer a next step")
    try:
        model = get_chat_model(
            temperature=settings.interpret_temperature, tags=["signal:interpret"]
        )
        structured = model.with_structured_output(TurnPlan)
        result = structured.invoke(
            [
                SystemMessage(content=THINKPAD_SYSTEM_PROMPT),
                HumanMessage(
                    content=interpret_prompt(
                        summary=summary,
                        turns=turns[-settings.history_window :],
                        artifact=prior_artifact.model_dump(),
                        memory=memory_view,
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

    # Deterministic safety gate — not left to the model.
    decision = preflight(state["user_message"])
    if decision.flag != "ok":
        plan.safety_flag = decision.flag

    artifact = _seed_artifact(state, plan)

    status = {
        "brainstorm": "exploring",
        "draft": "drafting",
        "revise": "refining",
        "critique": "refining",
        "chat": artifact.status if artifact.status != "empty" else "exploring",
    }.get(plan.mode, "exploring")
    if plan.clarifying_question or plan.safety_flag != "ok":
        status = "needs_input"

    return {
        **state,
        "summary": summary,
        "plan": plan.model_dump(),
        "artifact": artifact.model_dump(),
        "status": status,
        "trajectory": [
            *state.get("trajectory", []),
            {"step": "interpret", "mode": plan.mode, "status": status, "at": _now()},
        ],
    }


def _route(state: SignalState) -> str:
    plan = state["plan"]
    artifact = state["artifact"]
    if plan.get("safety_flag", "ok") != "ok" or plan.get("clarifying_question"):
        return "respond"

    mode = plan.get("mode", "chat")
    has_body = bool((artifact or {}).get("body", "").strip())
    if mode == "revise":
        return "revise" if has_body else "draft"
    if mode == "critique":
        return "critique" if has_body else "respond"
    if mode == "draft":
        substantive = (
            artifact.get("topic")
            or artifact.get("sources")
            or plan.get("chosen_angle")
            or len(state["user_message"]) > 40
        )
        return "draft" if substantive else "brainstorm"
    if mode == "brainstorm":
        return "brainstorm"
    return "respond"


# ---------------------------------------------------------------------------
# work nodes
# ---------------------------------------------------------------------------

def _brief(state: SignalState) -> dict[str, Any]:
    plan = state["plan"]
    artifact = state["artifact"]
    length = plan.get("length")
    return {
        "format": artifact.get("format", "linkedin_post"),
        "product_mode": artifact.get("product_mode", "existing"),
        "topic": artifact.get("topic") or plan.get("topic") or "",
        "sources": artifact.get("sources", []),
        "angle": plan.get("chosen_angle") or (artifact.get("angles") or [None])[0],
        "outline": artifact.get("outline", []),
        "tone": plan.get("tone"),
        "audience": plan.get("audience"),
        "cta": plan.get("cta"),
        "length": length,
        "max_words": max_words(length),
        "open_questions": artifact.get("open_questions", []),
    }


def _finish(
    state: SignalState,
    *,
    node: str,
    artifact: Artifact,
    message: str,
    status: str,
) -> SignalState:
    _emit_artifact(artifact)
    _emit_reply(message)
    return {
        **state,
        "artifact": artifact.model_dump(),
        "assistant_message": message,
        "status": status,
        "turns": [*state.get("turns", []), {"role": "assistant", "content": message}],
        "trajectory": [
            *state.get("trajectory", []),
            {"step": node, "status": status, "at": _now()},
        ],
    }


def _brainstorm(state: SignalState) -> SignalState:
    brief = _brief(state)
    result = brainstorm(brief)
    base = ensure_artifact(state["artifact"])
    picked = bool(result["outline"])
    artifact = base.model_copy(
        update={
            "kind": "outline" if picked else "idea_board",
            "angles": dedupe([*base.angles, *result["angles"]]) if not picked else base.angles,
            "outline": result["outline"] or base.outline,
        }
    )
    artifact = artifact.with_questions(result["open_questions"]).touched(
        status="drafting" if picked else "exploring"
    )
    if picked:
        message = (
            f"Outlined the {artifact.format.replace('_', ' ')} — "
            f"{len(artifact.outline)} beats on the board. Say the word and I'll draft it."
        )
    else:
        message = (
            f"Put {len(result['angles'])} angles on the board. "
            "Tell me which to run with (or mix two) and I'll outline it."
        )
        _emit_choice(
            f"angle-v{artifact.version}",
            "Which angle should I run with?",
            artifact.angles,
        )
    return _finish(state, node="brainstorm", artifact=artifact, message=message,
                   status=artifact.status)


def _write_stream(state: SignalState, *, revise: bool) -> Artifact:
    brief = _brief(state)
    base = ensure_artifact(state["artifact"])
    fmt = brief["format"]

    if revise:
        stream = revise_content(
            fmt, brief, base.body, state["plan"].get("revise_instruction", "")
        )
    else:
        stream = write_content(fmt, brief)

    parts: list[str] = []
    since_flush = 0
    for delta in stream:
        parts.append(delta)
        since_flush += 1
        if since_flush >= _ARTIFACT_STREAM_EVERY:
            since_flush = 0
            _emit_artifact(
                base.model_copy(update={"kind": "draft", "body": "".join(parts),
                                        "status": "drafting"})
            )
    body = enforce_max_words("".join(parts).strip(), brief["max_words"])

    notes = grounding_notes(body, base.sources, base.product_mode)
    artifact = base.model_copy(update={"kind": "draft", "body": body})
    artifact = artifact.with_questions(notes).touched(status="refining")
    return artifact


def _writer_failed(state: SignalState, node: str) -> SignalState:
    message = (
        "The draft came back empty. Tell me a bit more about the angle or the "
        "key point you want and I'll try again."
    )
    artifact = ensure_artifact(state["artifact"])
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


def _draft(state: SignalState) -> SignalState:
    artifact = _write_stream(state, revise=False)
    if not check_draft(artifact.body).allowed:
        return _writer_failed(state, "draft")
    flags = len(artifact.open_questions)
    message = f"Drafted a first pass ({word_count(artifact.body)} words)."
    if state["plan"].get("subject_changed"):
        message = (
            f"Rebased the piece onto {artifact.topic or 'the new subject'} and cleared "
            f"the old fact list — re-share any specs you want me to use. " + message
        )
    if flags:
        message += f" Flagged {flags} thing{'s' if flags != 1 else ''} to confirm — see open questions."
    message += " Want it punchier, shorter, or a different angle?"
    return _finish(state, node="draft", artifact=artifact, message=message,
                   status="refining")


def _revise(state: SignalState) -> SignalState:
    artifact = _write_stream(state, revise=True)
    if not check_draft(artifact.body).allowed:
        return _writer_failed(state, "revise")
    instruction = state["plan"].get("revise_instruction") or "your notes"
    if state["plan"].get("subject_changed"):
        message = (
            f"Rebased the piece onto {artifact.topic or 'the new subject'} (v{artifact.version}, "
            f"{word_count(artifact.body)} words) and cleared the old fact list — "
            "re-share any specs you want me to use."
        )
    else:
        message = (
            f"Revised for {instruction!r} (v{artifact.version}, "
            f"{word_count(artifact.body)} words). Take a look."
        )
    return _finish(state, node="revise", artifact=artifact, message=message,
                   status="refining")


def _critique(state: SignalState) -> SignalState:
    brief = _brief(state)
    base = ensure_artifact(state["artifact"])
    review = critique(brief["format"], brief, base.body)
    artifact = base.model_copy(
        update={"open_questions": dedupe([*base.open_questions, *review.points])}
    ).touched(status="refining")
    message = review.summary.strip() or "Reviewed the draft — notes are on the board."
    return _finish(state, node="critique", artifact=artifact, message=message,
                   status="refining")


def _respond(state: SignalState) -> SignalState:
    plan = state["plan"]
    artifact = ensure_artifact(state["artifact"])

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
            artifact.model_dump(),
            plan.get("reply_gist"),
        ):
            parts.append(delta)
            _emit_reply(delta)
        message = "".join(parts).strip() or (
            "Tell me what you'd like to work on and I'll get a first version on the board."
        )

    unresolved = plan.get("clarifying_question") or plan.get("safety_flag", "ok") != "ok"
    status = "needs_input" if unresolved else artifact.status
    _emit_artifact(artifact)
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
# graph
# ---------------------------------------------------------------------------

def _builder() -> StateGraph:
    graph = StateGraph(SignalState)
    graph.add_node("interpret", _interpret)
    graph.add_node("brainstorm", _brainstorm)
    graph.add_node("draft", _draft)
    graph.add_node("revise", _revise)
    graph.add_node("critique", _critique)
    graph.add_node("respond", _respond)
    graph.add_edge(START, "interpret")
    graph.add_conditional_edges(
        "interpret",
        _route,
        {
            "brainstorm": "brainstorm",
            "draft": "draft",
            "revise": "revise",
            "critique": "critique",
            "respond": "respond",
        },
    )
    for node in ("brainstorm", "draft", "revise", "critique", "respond"):
        graph.add_edge(node, END)
    return graph


_GRAPH_BUILDER = _builder()


def _compile(checkpointer: Any):
    return _GRAPH_BUILDER.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------

def _thread_config(conversation_id: str) -> dict[str, Any]:
    return {
        "configurable": {"thread_id": conversation_id},
        "tags": ["signal", CURRENT_PROMPT_VERSION],
        "metadata": {
            "conversation_id": conversation_id,
            "session_id": conversation_id,
            "prompt_version": CURRENT_PROMPT_VERSION,
        },
    }


async def astream_conversation(
    *,
    user_id: str,
    conversation_id: str,
    user_message: str,
    client_artifact: dict[str, Any] | None = None,
    base_version: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run one turn, yielding normalized events:

    ``{"type": "artifact", "artifact": {...}}``
    ``{"type": "reply", "delta": "..."}``
    ``{"type": "ui_choice", "id", "question", "options": [{"id", "label"}]}``
    ``{"type": "status", "status": "...", "node": "..."}``
    ``{"type": "final", "assistant_message", "artifact", "status", "plan",
       "versions", "head"}``

    ``base_version`` (1-based) is the version the client was previewing when it
    sent. If it points before the tip, the run continues from that snapshot and
    every later version is discarded.
    """

    if not user_message.strip():
        raise ValueError("user_message must not be empty")

    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    memory = load_memory(user_id)

    versions = await load_versions(conversation_id)
    branching = base_version is not None and 1 <= base_version < len(versions)
    base_artifact = versions[base_version - 1]["artifact"] if branching else None

    async with AsyncSqliteSaver.from_conn_string(str(settings.db_path)) as checkpointer:
        graph = _compile(checkpointer)
        config = _thread_config(conversation_id)

        prior = await graph.aget_state(config)
        prior_values = prior.values if prior else {}
        turns = list(prior_values.get("turns", []))
        turns.append({"role": "user", "content": user_message})

        seed_artifact = base_artifact if branching else prior_values.get("artifact", {})

        graph_input: SignalState = {
            "user_id": user_id,
            "conversation_id": conversation_id,
            "user_message": user_message,
            "client_artifact": {} if branching else (client_artifact or {}),
            "memory": memory,
            "turns": turns,
            "summary": prior_values.get("summary", ""),
            "artifact": seed_artifact or {},
            "trajectory": prior_values.get("trajectory", []),
        }

        async for mode, chunk in graph.astream(
            graph_input, config, stream_mode=["custom", "updates"]
        ):
            if mode == "custom" and isinstance(chunk, dict):
                if chunk.get("type") in {"artifact", "reply", "ui_choice"}:
                    yield chunk
            elif mode == "updates" and isinstance(chunk, dict):
                for node, update in chunk.items():
                    if isinstance(update, dict) and update.get("status"):
                        yield {
                            "type": "status",
                            "status": update["status"],
                            "node": node,
                        }

        final = await graph.aget_state(config)
        values = final.values if final else {}

    save_memory(user_id, values.get("plan", {}), values.get("artifact", {}))

    new_artifact = values.get("artifact", {})
    prev_artifact = (
        base_artifact if branching else (versions[-1]["artifact"] if versions else {})
    )
    if artifact_changed(prev_artifact, new_artifact):
        versions = append_version(
            versions,
            new_artifact,
            user_message,
            base_seq=base_version if branching else None,
        )
        await replace_versions(conversation_id, versions)

    yield {
        "type": "final",
        "assistant_message": values.get("assistant_message", ""),
        "artifact": new_artifact,
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

    final = asyncio.run(_collect())
    plan = final.get("plan", {})
    return {
        "assistant_message": final.get("assistant_message", ""),
        "response": final.get("assistant_message", ""),
        "conversation_id": conversation_id,
        "status": final.get("status", ""),
        "artifact": final.get("artifact", {}),
        "plan": plan,
        "mode": plan.get("mode"),
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
