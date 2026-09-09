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
from fastapi.responses import StreamingResponse

from backend.app import astream_conversation
from backend.artifact import Scratchpad
from backend.config import settings
from backend.prompts import CURRENT_PROMPT_VERSION
from backend.threads_store import create_thread, list_threads
from backend.versions import load_versions, version_items

logger = logging.getLogger(__name__)

app = FastAPI(title="Scratchpad AG-UI Agent")
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
    last_status = "processing"
    progress_steps: list[str] = []
    progress = _progress(None, last_status, progress_steps, done=False)
    pending_choice: dict[str, Any] | None = None
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

            elif kind == "final":
                if message_open:
                    yield encoder.encode(
                        TextMessageEndEvent(
                            type=EventType.TEXT_MESSAGE_END, messageId=message_id
                        )
                    )
                    message_open = False
                elif event.get("assistant_message"):
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
                if event.get("versions") is not None:
                    versions = event["versions"]
                    head = event.get("head", len(versions))
                if event.get("derived") is not None:
                    derived = event["derived"]
                yield encoder.encode(
                    StateSnapshotEvent(
                        type=EventType.STATE_SNAPSHOT,
                        snapshot=_state_snapshot(
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
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.agent:app", host="0.0.0.0", port=settings.agent_port)
