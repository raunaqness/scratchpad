"""AG-UI adapter: the event stream must always be well-formed.

No network, no graph — ``astream_conversation`` is replaced with a scripted
async generator so we can assert the shape of what ``_signal_events`` emits.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import backend.agent as agent
from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder


def _input() -> RunAgentInput:
    return RunAgentInput(
        thread_id="t-1",
        run_id="r-1",
        state={},
        messages=[{"id": "m0", "role": "user", "content": "help me post about X"}],
        tools=[],
        context=[],
        forwarded_props={},
    )


def _drain(monkeypatch, events) -> list[dict]:
    async def fake_stream(**_kwargs):
        for event in events:
            if isinstance(event, Exception):
                raise event
            yield event

    monkeypatch.setattr(agent, "astream_conversation", fake_stream)
    encoder = EventEncoder(accept="text/event-stream")

    async def run() -> list[dict]:
        out: list[dict] = []
        async for raw in agent._signal_events(_input(), encoder):
            for line in raw.splitlines():
                if line.startswith("data:"):
                    out.append(json.loads(line[5:].strip()))
        return out

    return asyncio.run(run())


def _types(events: list[dict]) -> list[str]:
    return [e.get("type") for e in events]


def test_happy_path_stream_is_well_formed(monkeypatch):
    events = _drain(
        monkeypatch,
        [
            {"type": "status", "status": "drafting", "node": "draft"},
            {"type": "artifact", "artifact": {"body": "", "version": 0}},
            {"type": "reply", "delta": "Drafted "},
            {"type": "reply", "delta": "a first pass."},
            {"type": "final", "assistant_message": "Drafted a first pass.",
             "artifact": {"body": "hello", "version": 1}, "status": "refining", "plan": {}},
        ],
    )
    kinds = _types(events)

    assert kinds[0] == "RUN_STARTED"
    assert kinds[-1] == "RUN_FINISHED"
    assert kinds.count("TEXT_MESSAGE_START") == 1
    assert kinds.count("TEXT_MESSAGE_END") == 1
    assert kinds.index("TEXT_MESSAGE_START") < kinds.index("TEXT_MESSAGE_END")
    assert kinds.count("TEXT_MESSAGE_CONTENT") == 2
    assert "STATE_SNAPSHOT" in kinds
    assert "RUN_ERROR" not in kinds

    # no STATE_SNAPSHOT is sandwiched inside the text message
    start, end = kinds.index("TEXT_MESSAGE_START"), kinds.index("TEXT_MESSAGE_END")
    assert not any(k == "STATE_SNAPSHOT" for k in kinds[start:end])


def test_final_without_streamed_reply_still_emits_one_message(monkeypatch):
    events = _drain(
        monkeypatch,
        [
            {"type": "artifact", "artifact": {"body": "", "version": 0}},
            {"type": "final", "assistant_message": "Here are 3 angles.",
             "artifact": {"angles": ["a", "b", "c"], "version": 1}, "status": "exploring",
             "plan": {"mode": "brainstorm"}},
        ],
    )
    kinds = _types(events)
    assert kinds.count("TEXT_MESSAGE_START") == 1
    assert kinds.count("TEXT_MESSAGE_END") == 1
    content = [e for e in events if e["type"] == "TEXT_MESSAGE_CONTENT"]
    assert content and content[0]["delta"] == "Here are 3 angles."
    assert kinds[-1] == "RUN_FINISHED"


def test_error_mid_stream_still_finishes(monkeypatch):
    events = _drain(
        monkeypatch,
        [
            {"type": "reply", "delta": "starting"},
            RuntimeError("boom"),
        ],
    )
    kinds = _types(events)
    assert "RUN_ERROR" in kinds
    assert kinds[-1] == "RUN_FINISHED"
    # an opened message is closed before the error
    assert kinds.count("TEXT_MESSAGE_START") == kinds.count("TEXT_MESSAGE_END") == 1
    # the error message is generic, not a leaked stack trace
    err = next(e for e in events if e["type"] == "RUN_ERROR")
    assert "boom" not in err["message"]


def test_missing_user_message_is_a_clean_error(monkeypatch):
    async def fake_stream(**_kwargs):
        yield {"type": "final", "assistant_message": "", "artifact": {}, "status": "", "plan": {}}

    monkeypatch.setattr(agent, "astream_conversation", fake_stream)
    encoder = EventEncoder(accept="text/event-stream")
    empty = RunAgentInput(
        thread_id="t", run_id="r", state={}, messages=[], tools=[], context=[],
        forwarded_props={},
    )

    async def run():
        return [
            json.loads(line[5:].strip())
            for raw in [chunk async for chunk in agent._signal_events(empty, encoder)]
            for line in raw.splitlines()
            if line.startswith("data:")
        ]

    kinds = [e.get("type") for e in asyncio.run(run())]
    assert kinds[0] == "RUN_STARTED"
    assert "RUN_ERROR" in kinds
    assert kinds[-1] == "RUN_FINISHED"
