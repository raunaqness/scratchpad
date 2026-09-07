"""AG-UI (CopilotKit-compatible) endpoint for Signal.

Streams a real turn: artifact versions go out as ``STATE_SNAPSHOT`` as the graph
builds them, reply tokens stream as ``TEXT_MESSAGE_CONTENT`` as the model
produces them, and ``RUN_FINISHED`` is always sent — including after an error.
"""

from __future__ import annotations

import json
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
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend.app import astream_conversation
from backend.artifact import Artifact
from backend.config import settings
from backend.prompts import CURRENT_PROMPT_VERSION

app = FastAPI(title="Signal AG-UI Agent")
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
    "brainstorm": "Brainstorming angles",
    "draft": "Drafting",
    "revise": "Revising",
    "critique": "Critiquing",
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
    if isinstance(state.get("artifact"), dict) and state["artifact"]:
        return state["artifact"]
    if "body" in state or "angles" in state or "outline" in state:
        return state
    forwarded = _forwarded(input_data)
    if isinstance(forwarded.get("artifact"), dict):
        return forwarded["artifact"]
    return None


def _empty_state() -> dict[str, Any]:
    return _state_snapshot(
        Artifact().model_dump(),
        status="processing",
        processing=True,
        progress=_progress(None, "processing", [], done=False),
    )


def _state_snapshot(
    artifact: dict[str, Any],
    *,
    status: str,
    processing: bool,
    progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # `artifact.*` is the real shape; `draft` / `draft_version` are back-compat
    # mirrors so an older panel still renders something.
    snapshot = {
        "processing": processing,
        "status": status,
        "draft": artifact.get("body", ""),
        "draft_version": artifact.get("version", 0),
        "artifact": artifact,
        **artifact,
    }
    if progress is not None:
        snapshot["progress"] = progress
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

    try:
        yield encoder.encode(
            RunStartedEvent(
                type=EventType.RUN_STARTED, threadId=thread_id, runId=run_id
            )
        )
        yield encoder.encode(
            StateSnapshotEvent(type=EventType.STATE_SNAPSHOT, snapshot=_empty_state())
        )

        user_message = _last_user_message(input_data)
        if not user_message.strip():
            raise ValueError("No user message in the AG-UI request.")

        async for event in astream_conversation(
            user_id=_user_id(input_data),
            conversation_id=thread_id,
            user_message=user_message,
            client_artifact=_client_artifact(input_data),
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

            elif kind == "artifact":
                # An artifact update between reply tokens would split the message.
                if message_open:
                    continue
                yield encoder.encode(
                    StateSnapshotEvent(
                        type=EventType.STATE_SNAPSHOT,
                        snapshot=_state_snapshot(
                            event["artifact"],
                            status=last_status,
                            processing=True,
                            progress=progress,
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
                yield encoder.encode(
                    StateSnapshotEvent(
                        type=EventType.STATE_SNAPSHOT,
                        snapshot=_state_snapshot(
                            event.get("artifact", {}),
                            status=event.get("status", last_status),
                            processing=False,
                            progress=_progress(
                                progress.get("node"),
                                event.get("status", last_status),
                                progress_steps,
                                done=True,
                            ),
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

    except Exception as error:  # noqa: BLE001 - surface as an AG-UI error + finish
        if message_open:
            yield encoder.encode(
                TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, messageId=message_id)
            )
        yield encoder.encode(
            RunErrorEvent(type=EventType.RUN_ERROR, message="Signal hit an error on this turn.")
        )
        yield encoder.encode(
            RunFinishedEvent(
                type=EventType.RUN_FINISHED,
                threadId=thread_id,
                runId=run_id,
                result={"status": "error"},
            )
        )


@app.post("/agent")
async def agent_endpoint(input_data: RunAgentInput, request: Request) -> StreamingResponse:
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
