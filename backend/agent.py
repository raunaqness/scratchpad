"""CopilotKit-compatible AG-UI endpoint for Signal."""

from __future__ import annotations

import asyncio
import re
import sys
import uuid
from pathlib import Path
from typing import AsyncGenerator

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
    return {
        "status": result.get("status"),
        "draft": result.get("draft", ""),
        "draft_version": result.get("draft_version", 0),
        "validation": result.get("validation", {}),
        "requirements": result.get("requirements", {}),
    }


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
        from backend.app import run_conversation

        result = await asyncio.to_thread(
            run_conversation,
            user_id=f"agui-{input_data.thread_id}",
            conversation_id=input_data.thread_id,
            user_message=_last_user_message(input_data),
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
