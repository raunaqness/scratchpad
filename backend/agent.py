"""AG-UI (CopilotKit-compatible) endpoint for Signal.

Streams a real turn: artifact versions go out as ``STATE_SNAPSHOT`` as the graph
builds them, reply tokens stream as ``TEXT_MESSAGE_CONTENT`` as the model
produces them, and ``RUN_FINISHED`` is always sent — including after an error.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Iterator

from ag_ui.core import (
    EventType,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateSnapshotEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from backend.app import aget_thread_state, astream_conversation
from backend.artifact import Scratchpad
from backend.capabilities.skills import run_skill
from backend.config import settings
from backend.prompts import CURRENT_PROMPT_VERSION
from backend.skills.registry import get_skill
from backend.threads_store import create_thread, list_threads
from backend.versions import load_versions, version_items

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_: "FastAPI"):
    if not settings.database_url:
        yield
        return
    from backend.db import close_pool, run_migrations

    try:
        applied = await run_migrations()
        if applied:
            logger.info("db migrations applied: %s", ", ".join(applied))
    except Exception:  # noqa: BLE001 - a bad migration must not stop the API
        logger.exception("db migrations failed; credit features may be degraded")
    try:
        yield
    finally:
        await close_pool()


app = FastAPI(title="Scratchpad AG-UI Agent", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


# Human-readable label per graph node, for the dev progress indicator.
_PROGRESS_LABELS = {
    "interpret": "Reading your turn",
    "note": "Jotting it down",
    "expand": "Developing the notes",
    "tighten": "Tightening",
    "brainstorm": "Exploring angles",
    "critique": "Reviewing",
    "build": "Building",
    "respond": "Replying",
}


def _progress(node: str | None, status: str, steps: list[str], *, done: bool) -> dict[str, Any]:
    return {
        "node": node,
        "label": "Done" if done else _PROGRESS_LABELS.get(node or "", node or "Working"),
        "status": status,
        "steps": list(steps),
        "done": done,
    }


def _choice_tool_events(
    encoder: EventEncoder, choice: dict[str, Any], parent_message_id: str | None
) -> Iterator[str]:
    """Render a ``ui_choice`` as a self-contained ``request_choice`` tool call."""

    tool_call_id = f"choice-{uuid.uuid4()}"
    args = {
        "id": choice.get("id"),
        "question": choice.get("question") or "Choose an option",
        "options": choice.get("options") or [],
    }
    yield encoder.encode(
        ToolCallStartEvent(
            type=EventType.TOOL_CALL_START,
            toolCallId=tool_call_id,
            toolCallName="request_choice",
            parentMessageId=parent_message_id,
        )
    )
    yield encoder.encode(
        ToolCallArgsEvent(
            type=EventType.TOOL_CALL_ARGS,
            toolCallId=tool_call_id,
            delta=json.dumps(args),
        )
    )
    yield encoder.encode(
        ToolCallEndEvent(type=EventType.TOOL_CALL_END, toolCallId=tool_call_id)
    )


def _follow_up_tool_events(
    encoder: EventEncoder,
    items: list[dict[str, Any]],
    parent_message_id: str | None,
) -> Iterator[str]:
    """Render the creative agent's suggestions as a ``follow_up`` tool call —
    the client renders each item as a button that sends its label as a message."""

    tool_call_id = f"followup-{uuid.uuid4()}"
    yield encoder.encode(
        ToolCallStartEvent(
            type=EventType.TOOL_CALL_START,
            toolCallId=tool_call_id,
            toolCallName="follow_up",
            parentMessageId=parent_message_id,
        )
    )
    yield encoder.encode(
        ToolCallArgsEvent(
            type=EventType.TOOL_CALL_ARGS,
            toolCallId=tool_call_id,
            delta=json.dumps({"items": items}),
        )
    )
    yield encoder.encode(
        ToolCallEndEvent(type=EventType.TOOL_CALL_END, toolCallId=tool_call_id)
    )


def _message_text(message: object) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(getattr(part, "text", ""))
            for part in content
            if getattr(part, "type", "") == "text"
        )
    return str(content or "")


def _last_user_message(input_data: RunAgentInput) -> str:
    for message in reversed(input_data.messages or []):
        if getattr(message, "role", "") == "user":
            return _message_text(message)
    return ""


def _forwarded(input_data: RunAgentInput) -> dict[str, Any]:
    value = getattr(input_data, "forwarded_props", None)
    return value if isinstance(value, dict) else {}


def _user_id(input_data: RunAgentInput) -> str:
    forwarded = _forwarded(input_data)
    for candidate in (forwarded.get("user_id"), forwarded.get("userId")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    state = input_data.state if isinstance(input_data.state, dict) else {}
    if isinstance(state.get("user_id"), str) and state["user_id"].strip():
        return state["user_id"].strip()
    return f"agui-{input_data.thread_id}"


def _identity(input_data: RunAgentInput) -> tuple[str, str, str]:
    """(user_id, email, name) as injected by the Next proxy."""

    forwarded = _forwarded(input_data)
    email = forwarded.get("user_email") or forwarded.get("userEmail") or ""
    name = forwarded.get("user_name") or forwarded.get("userName") or ""
    return _user_id(input_data), str(email or ""), str(name or "")


# Ids that are never real paying users: CLI / tests / anonymous AG-UI / dev.
def _is_billable(user_id: str) -> bool:
    return bool(
        settings.credits_active
        and settings.google_auth_enabled
        and user_id
        and not user_id.startswith("agui-")
        and user_id not in ("dev-user", "system")
    )


async def _credit_gate(input_data: RunAgentInput, thread_id: str) -> dict[str, Any]:
    """Provision the account and spend one credit for this turn.

    Returns ``{"allow": bool, "message": str | None, "balance": int | None}``.
    Any failure inside here allows the turn — credits must never break chat.
    """

    result: dict[str, Any] = {"allow": True, "message": None, "balance": None}
    user_id, email, name = _identity(input_data)
    if not _is_billable(user_id):
        return result

    from backend import credits  # local import: optional dependency (asyncpg)

    try:
        account = await credits.ensure_account(user_id, email, name)
        if account["status"] == "blocked":
            return {
                "allow": False,
                "message": "This account is disabled.",
                "balance": account["credits_balance"],
            }
        new_balance = await credits.try_debit(
            user_id, settings.message_cost, reason="message", ref=thread_id
        )
        if new_balance is None:
            result["balance"] = account["credits_balance"]
            if settings.credits_enforce:
                return {
                    "allow": False,
                    "message": "You're out of credits. Add more to keep going.",
                    "balance": 0,
                }
        else:
            result["balance"] = new_balance
    except Exception:  # noqa: BLE001 - credits are never load-bearing
        logger.exception("credit gate failed for %s; allowing turn", thread_id)
    return result


def _client_artifact(input_data: RunAgentInput) -> dict[str, Any] | None:
    state = input_data.state if isinstance(input_data.state, dict) else {}
    for key in ("scratchpad", "artifact"):
        if isinstance(state.get(key), dict) and state[key]:
            return state[key]
    if "body" in state or "angles" in state or "outline" in state:
        return state
    forwarded = _forwarded(input_data)
    for key in ("scratchpad", "artifact"):
        if isinstance(forwarded.get(key), dict):
            return forwarded[key]
    return None


def _base_version(input_data: RunAgentInput) -> int | None:
    """1-based version the client is previewing when it sent this turn, if any."""

    forwarded = _forwarded(input_data)
    run_config = forwarded.get("runConfig") if isinstance(forwarded.get("runConfig"), dict) else {}
    state = input_data.state if isinstance(input_data.state, dict) else {}
    for candidate in (
        forwarded.get("base_version"),
        forwarded.get("baseVersion"),
        run_config.get("base_version"),
        state.get("base_version"),
    ):
        if isinstance(candidate, bool):
            continue
        if isinstance(candidate, int) and candidate >= 1:
            return candidate
        if isinstance(candidate, str) and candidate.strip().isdigit():
            return int(candidate)
    return None


def _empty_state(
    versions: list[dict[str, Any]] | None = None, head: int | None = None
) -> dict[str, Any]:
    return _state_snapshot(
        Scratchpad().model_dump(),
        status="processing",
        processing=True,
        progress=_progress(None, "processing", [], done=False),
        versions=versions,
        head=head,
        derived=[],
    )


def _state_snapshot(
    scratchpad: dict[str, Any],
    *,
    status: str,
    processing: bool,
    progress: dict[str, Any] | None = None,
    versions: list[dict[str, Any]] | None = None,
    head: int | None = None,
    derived: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # `scratchpad.*` is spread for convenience; `draft` / `draft_version` stay as
    # back-compat mirrors so an older panel still renders something.
    snapshot = {
        "processing": processing,
        "status": status,
        "draft": scratchpad.get("body", ""),
        "draft_version": scratchpad.get("version", 0),
        "scratchpad": scratchpad,
        "artifact": scratchpad,
        **scratchpad,
    }
    if progress is not None:
        snapshot["progress"] = progress
    if versions is not None:
        snapshot["versions"] = versions
    if head is not None:
        snapshot["head"] = head
    if derived is not None:
        snapshot["derived"] = derived
        snapshot["active_tab"] = derived[-1]["id"] if derived else None
    return snapshot


async def _signal_events(
    input_data: RunAgentInput, encoder: EventEncoder
) -> AsyncGenerator[str, None]:
    thread_id = input_data.thread_id
    run_id = input_data.run_id
    message_id = str(uuid.uuid4())
    message_open = False
    message_closed_early = False  # closed by the follow-up phase, not by `final`
    last_status = "processing"
    progress_steps: list[str] = []
    progress = _progress(None, last_status, progress_steps, done=False)
    pending_choice: dict[str, Any] | None = None
    pending_follow_ups: list[dict[str, Any]] | None = None
    follow_up_pending = False
    derived: list[dict[str, Any]] = []
    last_scratchpad: dict[str, Any] = Scratchpad().model_dump()

    try:
        try:
            versions = version_items(await load_versions(thread_id))
        except Exception:  # noqa: BLE001 - history is best-effort
            versions = []
        head = len(versions)

        yield encoder.encode(
            RunStartedEvent(
                type=EventType.RUN_STARTED, threadId=thread_id, runId=run_id
            )
        )
        yield encoder.encode(
            StateSnapshotEvent(
                type=EventType.STATE_SNAPSHOT,
                snapshot=_empty_state(versions, head),
            )
        )

        user_message = _last_user_message(input_data)
        if not user_message.strip():
            raise ValueError("No user message in the AG-UI request.")

        gate = await _credit_gate(input_data, thread_id)
        if not gate["allow"]:
            yield encoder.encode(
                StateSnapshotEvent(
                    type=EventType.STATE_SNAPSHOT,
                    snapshot={
                        **_empty_state(versions, head),
                        "credits_balance": gate["balance"],
                        "out_of_credits": True,
                    },
                )
            )
            yield encoder.encode(
                RunErrorEvent(type=EventType.RUN_ERROR, message=gate["message"])
            )
            return

        async for event in astream_conversation(
            user_id=_user_id(input_data),
            conversation_id=thread_id,
            user_message=user_message,
            client_artifact=_client_artifact(input_data),
            base_version=_base_version(input_data),
        ):
            kind = event["type"]

            if kind == "reply":
                if not message_open:
                    message_open = True
                    yield encoder.encode(
                        TextMessageStartEvent(
                            type=EventType.TEXT_MESSAGE_START,
                            messageId=message_id,
                            role="assistant",
                        )
                    )
                yield encoder.encode(
                    TextMessageContentEvent(
                        type=EventType.TEXT_MESSAGE_CONTENT,
                        messageId=message_id,
                        delta=event["delta"],
                    )
                )

            elif kind == "scratchpad":
                last_scratchpad = event["scratchpad"]
                # A snapshot between reply tokens would split the message.
                if message_open:
                    continue
                yield encoder.encode(
                    StateSnapshotEvent(
                        type=EventType.STATE_SNAPSHOT,
                        snapshot=_state_snapshot(
                            last_scratchpad,
                            status=last_status,
                            processing=True,
                            progress=progress,
                            versions=versions,
                            head=head,
                            derived=derived,
                        ),
                    )
                )

            elif kind == "derived":
                item = event["derived"]
                if any(d.get("id") == item.get("id") for d in derived):
                    derived = [item if d.get("id") == item.get("id") else d for d in derived]
                else:
                    derived = [*derived, item]
                if message_open:
                    continue
                yield encoder.encode(
                    StateSnapshotEvent(
                        type=EventType.STATE_SNAPSHOT,
                        snapshot=_state_snapshot(
                            last_scratchpad,
                            status=last_status,
                            processing=True,
                            progress=progress,
                            versions=versions,
                            head=head,
                            derived=derived,
                        ),
                    )
                )

            elif kind == "status":
                last_status = event["status"]
                node = event.get("node")
                if node and node not in progress_steps:
                    progress_steps.append(node)
                progress = _progress(node, last_status, progress_steps, done=False)

            elif kind == "ui_choice":
                pending_choice = event

            elif kind == "follow_up_status":
                # The creative agent runs after the reply is done. Close the
                # assistant message so we can show a "working" snapshot without
                # splitting it.
                follow_up_pending = event.get("phase") == "working"
                if message_open:
                    yield encoder.encode(
                        TextMessageEndEvent(
                            type=EventType.TEXT_MESSAGE_END, messageId=message_id
                        )
                    )
                    message_open = False
                    message_closed_early = True
                yield encoder.encode(
                    StateSnapshotEvent(
                        type=EventType.STATE_SNAPSHOT,
                        snapshot={
                            **_state_snapshot(
                                last_scratchpad,
                                status=last_status,
                                processing=follow_up_pending,
                                progress=progress,
                                versions=versions,
                                head=head,
                                derived=derived,
                            ),
                            "follow_up_pending": follow_up_pending,
                        },
                    )
                )

            elif kind == "follow_ups":
                pending_follow_ups = event.get("items") or None

            elif kind == "final":
                if message_open:
                    yield encoder.encode(
                        TextMessageEndEvent(
                            type=EventType.TEXT_MESSAGE_END, messageId=message_id
                        )
                    )
                    message_open = False
                elif event.get("assistant_message") and not message_closed_early:
                    yield encoder.encode(
                        TextMessageStartEvent(
                            type=EventType.TEXT_MESSAGE_START,
                            messageId=message_id,
                            role="assistant",
                        )
                    )
                    yield encoder.encode(
                        TextMessageContentEvent(
                            type=EventType.TEXT_MESSAGE_CONTENT,
                            messageId=message_id,
                            delta=event["assistant_message"],
                        )
                    )
                    yield encoder.encode(
                        TextMessageEndEvent(
                            type=EventType.TEXT_MESSAGE_END, messageId=message_id
                        )
                    )
                if pending_choice is not None:
                    for raw in _choice_tool_events(encoder, pending_choice, message_id):
                        yield raw
                    pending_choice = None
                if pending_follow_ups:
                    for raw in _follow_up_tool_events(
                        encoder, pending_follow_ups, message_id
                    ):
                        yield raw
                    pending_follow_ups = None
                if event.get("versions") is not None:
                    versions = event["versions"]
                    head = event.get("head", len(versions))
                if event.get("derived") is not None:
                    derived = event["derived"]
                yield encoder.encode(
                    StateSnapshotEvent(
                        type=EventType.STATE_SNAPSHOT,
                        snapshot={
                            **_state_snapshot(
                                event.get("scratchpad") or last_scratchpad,
                                status=event.get("status", last_status),
                                processing=False,
                                progress=_progress(
                                    progress.get("node"),
                                    event.get("status", last_status),
                                    progress_steps,
                                    done=True,
                                ),
                                versions=versions,
                                head=head,
                                derived=derived,
                            ),
                            "follow_up_pending": False,
                        },
                    )
                )
                yield encoder.encode(
                    RunFinishedEvent(
                        type=EventType.RUN_FINISHED,
                        threadId=thread_id,
                        runId=run_id,
                        result={"status": event.get("status")},
                    )
                )
                return

        # Stream ended without a final event — still finish cleanly.
        if message_open:
            yield encoder.encode(
                TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, messageId=message_id)
            )
        if pending_choice is not None:
            for raw in _choice_tool_events(encoder, pending_choice, message_id):
                yield raw
            pending_choice = None
        if pending_follow_ups:
            for raw in _follow_up_tool_events(encoder, pending_follow_ups, message_id):
                yield raw
            pending_follow_ups = None
        yield encoder.encode(
            RunFinishedEvent(
                type=EventType.RUN_FINISHED, threadId=thread_id, runId=run_id
            )
        )

    except Exception as error:  # noqa: BLE001 - surface as a terminal AG-UI error
        logger.exception("scratchpad turn failed (thread=%s run=%s): %s", thread_id, run_id, error)
        if message_open:
            yield encoder.encode(
                TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, messageId=message_id)
            )
        # A non-processing snapshot so the panel stops its spinner and keeps
        # showing the last good scratchpad / tabs.
        yield encoder.encode(
            StateSnapshotEvent(
                type=EventType.STATE_SNAPSHOT,
                snapshot=_state_snapshot(
                    last_scratchpad,
                    status="error",
                    processing=False,
                    progress=_progress(progress.get("node"), "error", progress_steps, done=True),
                    versions=versions,
                    head=head,
                    derived=derived,
                ),
            )
        )
        # RUN_ERROR is itself terminal — do NOT send RUN_FINISHED after it
        # (the AG-UI client rejects any event after a terminal one).
        yield encoder.encode(
            RunErrorEvent(
                type=EventType.RUN_ERROR,
                message="Scratchpad hit an error on this turn — please try again.",
            )
        )


def _require_proxy(request: Request) -> None:
    """Reject direct hits on the backend's public port when auth is enabled.

    The Next.js BFF is the only sanctioned caller — it proves it by echoing the
    shared session secret. Without this, anything on the network could POST to
    ``:8001`` with an arbitrary ``forwarded_props.user_id``.
    """

    if not settings.google_auth_enabled:
        return
    expected = settings.proxy_shared_secret
    presented = request.headers.get("x-signal-proxy-secret", "")
    if not expected or not secrets.compare_digest(presented, expected):
        raise HTTPException(status_code=403, detail="proxy authentication required")


@app.get("/api/threads")
async def list_threads_endpoint(request: Request, user_id: str) -> dict[str, Any]:
    _require_proxy(request)
    return {"threads": await list_threads(user_id)}


@app.get("/api/account")
async def get_account_endpoint(request: Request, user_id: str) -> dict[str, Any]:
    """Balance + status for the signed-in user, for the BFF to show a pill."""

    _require_proxy(request)
    if not settings.credits_active:
        return {"credits_enabled": False, "credits_balance": None, "status": "active"}
    from backend import credits

    account = await credits.get_account(user_id)
    if account is None:
        return {
            "credits_enabled": True,
            "credits_balance": settings.signup_credits,
            "status": "active",
            "provisioned": False,
        }
    return {
        "credits_enabled": True,
        "credits_enforced": settings.credits_enforce,
        "credits_balance": account["credits_balance"],
        "status": account["status"],
        "provisioned": True,
    }


@app.post("/api/threads")
async def create_thread_endpoint(request: Request) -> dict[str, Any]:
    _require_proxy(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")
    user_id = str(body.get("user_id") or "").strip()
    thread_id = str(body.get("thread_id") or "").strip()
    if not user_id or not thread_id:
        raise HTTPException(
            status_code=400, detail="user_id and thread_id are required"
        )
    return await create_thread(user_id, thread_id, body.get("title"))


# --- artifact generation (standalone skill runs, off the chat turn) --------

@app.get("/api/artifacts")
async def list_artifacts_endpoint(request: Request, thread_id: str) -> dict[str, Any]:
    _require_proxy(request)
    from backend import artifacts_store

    return {"artifacts": await artifacts_store.list_artifacts(thread_id)}


@app.post("/api/artifacts/generate")
async def generate_artifact_endpoint(request: Request):
    """Run one skill against a thread's scratchpad and stream the artifact.

    Costs one credit. Not an AG-UI turn — a plain SSE stream of
    ``{"type":"delta","text":…}`` then ``{"type":"done","artifact":{…}}``.
    """

    _require_proxy(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")

    thread_id = str(body.get("thread_id") or "").strip()
    skill_id = str(body.get("skill_id") or "").strip()
    user_id = str(body.get("user_id") or "").strip()
    if not thread_id or not skill_id:
        raise HTTPException(status_code=400, detail="thread_id and skill_id are required")

    skill = get_skill(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"unknown skill_id: {skill_id!r}")

    # one credit per generation
    if _is_billable(user_id):
        from backend import credits

        try:
            account = await credits.ensure_account(
                user_id,
                str(body.get("user_email") or ""),
                str(body.get("user_name") or ""),
            )
            if account["status"] == "blocked":
                return JSONResponse({"error": "account_disabled"}, status_code=403)
            spent = await credits.try_debit(
                user_id, settings.message_cost, reason="artifact",
                ref=f"{thread_id}:{skill_id}",
            )
            if spent is None and settings.credits_enforce:
                return JSONResponse(
                    {"error": "insufficient_credits"}, status_code=402
                )
        except Exception:  # noqa: BLE001 - credits never block generation on a bug
            logger.exception("artifact credit debit failed for %s; allowing", user_id)

    scratchpad = await aget_thread_state(thread_id)
    scratchpad = scratchpad.get("scratchpad", {}) if isinstance(scratchpad, dict) else {}

    async def _stream() -> AsyncGenerator[str, None]:
        parts: list[str] = []
        try:
            for delta in run_skill(skill, scratchpad, {}):
                parts.append(delta)
                yield f"data: {json.dumps({'type': 'delta', 'text': delta})}\n\n"
        except Exception:  # noqa: BLE001
            logger.exception("skill run failed (thread=%s skill=%s)", thread_id, skill_id)
            yield f"data: {json.dumps({'type': 'error'})}\n\n"
            return
        body_text = "".join(parts).strip()
        artifact: dict[str, Any] = {"skill_id": skill_id, "version": None, "body": body_text}
        if body_text:
            try:
                from backend import artifacts_store

                artifact = await artifacts_store.save_artifact(
                    thread_id, skill_id, body_text
                )
            except Exception:  # noqa: BLE001
                logger.exception("save_artifact failed (thread=%s)", thread_id)
        yield f"data: {json.dumps({'type': 'done', 'artifact': artifact})}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/agent")
async def agent_endpoint(input_data: RunAgentInput, request: Request) -> StreamingResponse:
    _require_proxy(request)
    encoder = EventEncoder(accept=request.headers.get("accept"))
    return StreamingResponse(
        _signal_events(input_data, encoder),
        media_type=encoder.get_content_type(),
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "openrouter_configured": bool(settings.openrouter_api_key),
        "prompt_version": CURRENT_PROMPT_VERSION,
        "credits": (
            "enforced"
            if settings.credits_active and settings.credits_enforce
            else "tracking"
            if settings.credits_active
            else "off"
        ),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.agent:app", host="0.0.0.0", port=settings.agent_port)
