"""CopilotKit-compatible AG-UI endpoint for Signal."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from pathlib import Path
from typing import AsyncGenerator

from ag_ui.core import (
    EventType,
    Interrupt,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunFinishedInterruptOutcome,
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
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

SIGNAL_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(SIGNAL_ROOT / ".env")
if str(SIGNAL_ROOT) not in sys.path:
    sys.path.insert(0, str(SIGNAL_ROOT))

app = FastAPI(title="Signal AG-UI Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _message_text(message: object) -> str:
    """Extract text from an AG-UI message's supported content shapes."""
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
    """Return the latest user message from a typed AG-UI request."""
    for message in reversed(input_data.messages):
        if getattr(message, "role", "") == "user":
            return _message_text(message)
    return ""


def _state_snapshot(result: dict[str, object]) -> dict[str, object]:
    """Expose Signal's useful workflow state through AG-UI."""
    trajectory = result.get("trajectory", [])
    completed_steps = [
        step.get("step")
        for step in trajectory
        if isinstance(step, dict) and step.get("step")
    ] if isinstance(trajectory, list) else []
    return {
        "processing": False,
        "progress": {
            "stage": completed_steps[-1] if completed_steps else "complete",
            "label": "LangGraph run complete",
            "completed_steps": completed_steps,
        },
        "status": result.get("status"),
        "draft": result.get("draft", ""),
        "draft_version": result.get("draft_version", 0),
        "validation": result.get("validation", {}),
        "requirements": result.get("requirements", {}),
        "pending_input": result.get("pending_input", {}),
    }


def _progress_snapshot(progress: dict[str, object]) -> dict[str, object]:
    """Expose one completed LangGraph node through AG-UI state."""
    return {
        "processing": True,
        "status": "processing",
        "progress": progress,
        "tasks": progress.get("tasks", []),
        "completed_tasks": progress.get("completed_tasks", []),
    }


def _choice_response(input_data: RunAgentInput) -> dict[str, object]:
    """Extract a resolved AG-UI interrupt or tool result."""
    for resume in input_data.resume or []:
        if resume.status == "resolved" and isinstance(resume.payload, dict):
            payload = resume.payload
            return {
                "id": payload.get("id", "tone"),
                "value": payload.get("value", payload.get("optionId")),
            }
    for message in reversed(input_data.messages):
        if getattr(message, "role", "") != "tool":
            continue
        content = _message_text(message)
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            payload = {"id": "tone", "value": content}
        if isinstance(payload, dict) and payload.get("value", payload.get("optionId")):
            return {
                "id": payload.get("id", "tone"),
                "value": payload.get("value", payload.get("optionId")),
            }
    return {}


async def _signal_events(
    input_data: RunAgentInput, encoder: EventEncoder
) -> AsyncGenerator[str, None]:
    """Run Signal and encode its result as CopilotKit-compatible events."""
    try:
        yield encoder.encode(
            RunStartedEvent(
                type=EventType.RUN_STARTED,
                threadId=input_data.thread_id,
                runId=input_data.run_id,
                parentRunId=input_data.parent_run_id,
                input=input_data,
            )
        )
        yield encoder.encode(
            StateSnapshotEvent(
                type=EventType.STATE_SNAPSHOT,
                snapshot={
                    "processing": True,
                    "status": "processing",
                    "progress": {
                        "stage": "analyze",
                        "label": "Analyzing the request",
                        "completed_steps": [],
                    },
                    "draft": "",
                    "draft_version": 0,
                    "validation": {},
                    "requirements": {},
                    "pending_input": {},
                },
            )
        )
        from backend.app import run_conversation

        choice_response = _choice_response(input_data)
        progress_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def on_progress(progress: dict[str, object]) -> None:
            loop.call_soon_threadsafe(progress_queue.put_nowait, progress)

        run_task = asyncio.create_task(
            asyncio.to_thread(
                run_conversation,
                user_id=f"agui-{input_data.thread_id}",
                conversation_id=input_data.thread_id,
                user_message=_last_user_message(input_data),
                choice_response=choice_response or None,
                progress_callback=on_progress,
            )
        )
        while not run_task.done():
            try:
                progress = await asyncio.wait_for(progress_queue.get(), 0.1)
            except TimeoutError:
                continue
            yield encoder.encode(
                StateSnapshotEvent(
                    type=EventType.STATE_SNAPSHOT,
                    snapshot=_progress_snapshot(progress),
                )
            )
        result = await run_task
        while not progress_queue.empty():
            yield encoder.encode(
                StateSnapshotEvent(
                    type=EventType.STATE_SNAPSHOT,
                    snapshot=_progress_snapshot(progress_queue.get_nowait()),
                )
            )
        message_id = str(uuid.uuid4())
        yield encoder.encode(
            TextMessageStartEvent(
                type=EventType.TEXT_MESSAGE_START,
                messageId=message_id,
                role="assistant",
            )
        )
        response = str(result["assistant_message"])
        for chunk in re.findall(r"\S+\s*|\n+", response):
            yield encoder.encode(
                TextMessageContentEvent(
                    type=EventType.TEXT_MESSAGE_CONTENT,
                    messageId=message_id,
                    delta=chunk,
                )
            )
            await asyncio.sleep(0)
        yield encoder.encode(
            TextMessageEndEvent(
                type=EventType.TEXT_MESSAGE_END,
                messageId=message_id,
            )
        )
        yield encoder.encode(
            StateSnapshotEvent(
                type=EventType.STATE_SNAPSHOT,
                snapshot=_state_snapshot(result),
            )
        )
        pending_input = result.get("pending_input")
        if pending_input:
            tool_call_id = f"choice-{pending_input['id']}"
            yield encoder.encode(
                ToolCallStartEvent(
                    type=EventType.TOOL_CALL_START,
                    toolCallId=tool_call_id,
                    toolCallName="request_choice",
                    parentMessageId=message_id,
                )
            )
            yield encoder.encode(
                ToolCallArgsEvent(
                    type=EventType.TOOL_CALL_ARGS,
                    toolCallId=tool_call_id,
                    delta=json.dumps(pending_input),
                )
            )
            yield encoder.encode(
                ToolCallEndEvent(
                    type=EventType.TOOL_CALL_END,
                    toolCallId=tool_call_id,
                )
            )
            yield encoder.encode(
                RunFinishedEvent(
                    type=EventType.RUN_FINISHED,
                    threadId=input_data.thread_id,
                    runId=input_data.run_id,
                    outcome=RunFinishedInterruptOutcome(
                        interrupts=[
                            Interrupt(
                                # assistant-ui keys the pending action by the
                                # tool call id. Keep both AG-UI identifiers
                                # aligned so resume responses are accepted.
                                id=tool_call_id,
                                reason="user_input",
                                message=pending_input["question"],
                                toolCallId=tool_call_id,
                                responseSchema={
                                    "type": "string",
                                    "enum": [
                                        option["id"]
                                        for option in pending_input["options"]
                                    ],
                                },
                            )
                        ]
                    ),
                )
            )
            return
        yield encoder.encode(
            RunFinishedEvent(
                type=EventType.RUN_FINISHED,
                threadId=input_data.thread_id,
                runId=input_data.run_id,
                result={"status": result.get("status")},
            )
        )
    except Exception as error:
        yield encoder.encode(
            RunErrorEvent(type=EventType.RUN_ERROR, message=str(error))
        )


@app.post("/agent")
async def agent_endpoint(input_data: RunAgentInput, request: Request) -> StreamingResponse:
    """Accept a typed AG-UI run and stream Signal events."""
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
    """Report adapter health and provider configuration."""
    from backend.config import settings

    return {
        "status": "ok",
        "openrouter_configured": bool(settings.openrouter_api_key),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.agent:app", host="0.0.0.0", port=8001)
