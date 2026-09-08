"""AG-UI adapter: the event stream must always be well-formed.

No network, no graph — ``astream_conversation`` is replaced with a scripted
async generator so we can assert the shape of what ``_signal_events`` emits.
"""

from __future__ import annotations

import asyncio
import json

import backend.agent as agent
from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder


def _input() -> RunAgentInput:
    return RunAgentInput(
        thread_id="t-1",
        run_id="r-1",
        state={},
        messages=[{"id": "m0", "role": "user", "content": "jot down an idea"}],
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
            {"type": "status", "status": "developing", "node": "expand"},
            {"type": "scratchpad", "scratchpad": {"body": "", "version": 0}},
            {"type": "reply", "delta": "Developed "},
            {"type": "reply", "delta": "the notes."},
            {"type": "final", "assistant_message": "Developed the notes.",
             "scratchpad": {"body": "hello", "version": 1}, "derived": [],
             "status": "developing", "plan": {}},
        ],
    )
    kinds = _types(events)

    assert kinds[0] == "RUN_STARTED"
    assert kinds[-1] == "RUN_FINISHED"
    assert kinds.count("TEXT_MESSAGE_START") == 1
    assert kinds.count("TEXT_MESSAGE_END") == 1
    assert kinds.count("TEXT_MESSAGE_CONTENT") == 2
    assert "STATE_SNAPSHOT" in kinds
    assert "RUN_ERROR" not in kinds

    start, end = kinds.index("TEXT_MESSAGE_START"), kinds.index("TEXT_MESSAGE_END")
    assert not any(k == "STATE_SNAPSHOT" for k in kinds[start:end])


def test_final_without_streamed_reply_still_emits_one_message(monkeypatch):
    events = _drain(
        monkeypatch,
        [
            {"type": "scratchpad", "scratchpad": {"body": "", "version": 0}},
            {"type": "final", "assistant_message": "Put 3 angles on the board.",
             "scratchpad": {"angles": ["a", "b", "c"], "version": 1}, "derived": [],
             "status": "notes", "plan": {"mode": "brainstorm"}},
        ],
    )
    kinds = _types(events)
    assert kinds.count("TEXT_MESSAGE_START") == 1
    assert kinds.count("TEXT_MESSAGE_END") == 1
    content = [e for e in events if e["type"] == "TEXT_MESSAGE_CONTENT"]
    assert content and content[0]["delta"] == "Put 3 angles on the board."
    assert kinds[-1] == "RUN_FINISHED"


def test_derived_artifact_rides_along_in_snapshots(monkeypatch):
    d1 = {"id": "d-1", "skill_id": "blog_outline", "skill_name": "Blog outline",
          "title": "Blog outline", "body": "# partial", "from_version": 1, "open_questions": []}
    d2 = {**d1, "body": "# full outline"}
    events = _drain(
        monkeypatch,
        [
            {"type": "status", "status": "developing", "node": "build"},
            {"type": "scratchpad", "scratchpad": {"body": "notes", "version": 1}},
            {"type": "derived", "derived": d1},
            {"type": "derived", "derived": d2},
            {"type": "reply", "delta": "Built a blog outline."},
            {"type": "final", "assistant_message": "Built a blog outline.",
             "scratchpad": {"body": "notes", "version": 1}, "derived": [d2],
             "status": "developing", "plan": {"mode": "build"}},
        ],
    )
    snapshots = [e["snapshot"] for e in events if e["type"] == "STATE_SNAPSHOT"]
    # mid-stream: one tab, latest body, scratchpad preserved
    mid = [s for s in snapshots if s.get("processing") and s.get("derived")][-1]
    assert len(mid["derived"]) == 1
    assert mid["derived"][0]["body"] == "# full outline"
    assert mid["scratchpad"]["body"] == "notes"  # not blown away
    assert mid["active_tab"] == "d-1"

    final = snapshots[-1]
    assert final["processing"] is False
    assert final["derived"][0]["body"] == "# full outline"


def test_ui_choice_becomes_a_request_choice_tool_call(monkeypatch):
    events = _drain(
        monkeypatch,
        [
            {"type": "status", "status": "notes", "node": "brainstorm"},
            {"type": "scratchpad", "scratchpad": {"angles": ["a", "b"], "version": 1}},
            {"type": "reply", "delta": "Put 2 angles on the board."},
            {"type": "ui_choice", "id": "angle-v1", "question": "Which angle?",
             "options": [{"id": "0", "label": "a"}, {"id": "1", "label": "b"}]},
            {"type": "final", "assistant_message": "Put 2 angles on the board.",
             "scratchpad": {"angles": ["a", "b"], "version": 1}, "derived": [],
             "status": "notes", "plan": {"mode": "brainstorm"}},
        ],
    )
    kinds = _types(events)
    assert kinds.count("TOOL_CALL_START") == 1
    assert "TOOL_CALL_ARGS" in kinds and "TOOL_CALL_END" in kinds
    assert kinds.index("TEXT_MESSAGE_END") < kinds.index("TOOL_CALL_START")
    assert kinds.index("TOOL_CALL_END") < kinds.index("RUN_FINISHED")

    start = next(e for e in events if e["type"] == "TOOL_CALL_START")
    assert start["toolCallName"] == "request_choice"
    payload = json.loads(next(e for e in events if e["type"] == "TOOL_CALL_ARGS")["delta"])
    assert [o["label"] for o in payload["options"]] == ["a", "b"]


def test_progress_rides_along_in_snapshots(monkeypatch):
    events = _drain(
        monkeypatch,
        [
            {"type": "status", "status": "developing", "node": "expand"},
            {"type": "scratchpad", "scratchpad": {"body": "", "version": 0}},
            {"type": "reply", "delta": "Developed it."},
            {"type": "final", "assistant_message": "Developed it.",
             "scratchpad": {"body": "hi", "version": 1}, "derived": [],
             "status": "developing", "plan": {}},
        ],
    )
    snapshots = [e["snapshot"] for e in events if e["type"] == "STATE_SNAPSHOT"]
    mid = [s for s in snapshots if s.get("processing")][-1]
    assert mid["progress"]["label"] == "Developing the notes"
    assert mid["progress"]["done"] is False
    assert mid["progress"]["steps"] == ["expand"]

    final = snapshots[-1]
    assert final["processing"] is False
    assert final["progress"]["done"] is True
    assert final["progress"]["label"] == "Done"


def test_error_mid_stream_ends_on_a_terminal_run_error(monkeypatch):
    events = _drain(monkeypatch, [{"type": "reply", "delta": "starting"}, RuntimeError("boom")])
    kinds = _types(events)
    # RUN_ERROR is terminal — it is last, and nothing (esp. RUN_FINISHED) follows
    assert kinds[-1] == "RUN_ERROR"
    assert "RUN_FINISHED" not in kinds
    assert kinds.count("TEXT_MESSAGE_START") == kinds.count("TEXT_MESSAGE_END") == 1
    # a non-processing snapshot precedes it so the panel stops spinning
    last_snap = [e["snapshot"] for e in events if e["type"] == "STATE_SNAPSHOT"][-1]
    assert last_snap["processing"] is False
    err = next(e for e in events if e["type"] == "RUN_ERROR")
    assert "boom" not in err["message"]


def test_missing_user_message_is_a_clean_error(monkeypatch):
    async def fake_stream(**_kwargs):
        yield {"type": "final", "assistant_message": "", "scratchpad": {}, "derived": [],
               "status": "", "plan": {}}

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
    assert kinds[-1] == "RUN_ERROR"
    assert "RUN_FINISHED" not in kinds
