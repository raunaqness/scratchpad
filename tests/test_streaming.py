"""The turn must actually stream: scratchpad versions, derived artifacts, and
reply tokens, in order."""

from __future__ import annotations

import asyncio

from backend.app import astream_conversation
from backend.signal_models import TurnPlan
from tests.conftest import FakeModelScript


def _collect(**kwargs) -> list[dict]:
    async def run() -> list[dict]:
        return [event async for event in astream_conversation(**kwargs)]

    return asyncio.run(run())


def test_expand_streams_scratchpad_then_reply_then_final(isolated_state, install_models):
    script = install_models(FakeModelScript(plan=TurnPlan(mode="note"), grounding=[]))
    _collect(user_id="s", conversation_id="s", user_message="jot: a starting idea")

    script.plan = TurnPlan(mode="expand")
    script.expanded = "A" * 200  # long enough to force mid-stream snapshots
    events = _collect(user_id="s", conversation_id="s", user_message="develop it")
    kinds = [e["type"] for e in events]

    assert kinds[-1] == "final"
    assert "scratchpad" in kinds and "reply" in kinds
    # reply tokens come after the scratchpad is built (no interleave)
    assert max(i for i, k in enumerate(kinds) if k == "scratchpad") < min(
        i for i, k in enumerate(kinds) if k == "reply"
    )
    bodies = [e["scratchpad"]["body"] for e in events if e["type"] == "scratchpad"]
    assert bodies == sorted(bodies, key=len)
    assert events[-1]["scratchpad"]["body"] == "A" * 200


def test_build_streams_a_derived_artifact(isolated_state, install_models):
    script = install_models(
        FakeModelScript(plan=TurnPlan(mode="note"), skill_output="B" * 200, grounding=[])
    )
    _collect(user_id="s", conversation_id="b1", user_message="jot: a starting idea")

    script.plan = TurnPlan(mode="build", skill_id="blog_outline")
    events = _collect(user_id="s", conversation_id="b1", user_message="make a blog outline")
    kinds = [e["type"] for e in events]

    assert "derived" in kinds
    derived_events = [e for e in events if e["type"] == "derived"]
    # streamed bodies grow toward the final one, sharing one id
    ids = {e["derived"]["id"] for e in derived_events}
    assert len(ids) == 1
    assert derived_events[-1]["derived"]["body"] == "B" * 200
    # the final event carries the derived list, no new scratchpad version
    assert events[-1]["derived"] and events[-1]["derived"][-1]["skill_id"] == "blog_outline"
    assert events[-1]["head"] == 1


def test_brainstorm_emits_a_choice_for_the_angles(isolated_state, install_models):
    install_models(
        FakeModelScript(
            plan=TurnPlan(mode="brainstorm", topic="Widget"),
            angles=["Angle A - lens one", "Angle B - lens two", "Angle C - lens three"],
        )
    )
    events = _collect(user_id="s", conversation_id="c1", user_message="think about Widget")
    choices = [e for e in events if e["type"] == "ui_choice"]
    assert len(choices) == 1
    assert [o["label"] for o in choices[0]["options"]] == [
        "Angle A - lens one",
        "Angle B - lens two",
        "Angle C - lens three",
    ]


def test_note_turn_emits_no_choice(isolated_state, install_models):
    install_models(FakeModelScript(plan=TurnPlan(mode="note")))
    events = _collect(user_id="s", conversation_id="c2", user_message="jot this down")
    assert not [e for e in events if e["type"] == "ui_choice"]


def test_chat_streams_real_reply_tokens(isolated_state, install_models):
    install_models(
        FakeModelScript(
            plan=TurnPlan(mode="chat", reply_gist="answer the question"),
            reply="Scratchpad helps you work through ideas and build things from them.",
        )
    )
    events = _collect(user_id="s", conversation_id="s2", user_message="what do you do?")
    reply = "".join(e["delta"] for e in events if e["type"] == "reply")
    assert reply.startswith("Scratchpad helps you")
    assert len([e for e in events if e["type"] == "reply"]) > 1
