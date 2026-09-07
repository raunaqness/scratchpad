"""The turn must actually stream: artifact versions and reply tokens, in order."""

from __future__ import annotations

import asyncio

from backend.app import astream_conversation
from backend.signal_models import TurnPlan
from tests.conftest import FakeModelScript


def _collect(**kwargs) -> list[dict]:
    async def run() -> list[dict]:
        return [event async for event in astream_conversation(**kwargs)]

    return asyncio.run(run())


def test_draft_streams_artifact_then_reply_then_final(isolated_state, install_models):
    install_models(
        FakeModelScript(
            plan=TurnPlan(mode="draft", topic="Widget", format="linkedin_post"),
            draft="A" * 200,  # long enough to force mid-stream artifact snapshots
        )
    )
    events = _collect(
        user_id="s", conversation_id="s", user_message="draft a post about Widget"
    )
    kinds = [e["type"] for e in events]

    assert kinds[-1] == "final"
    assert "artifact" in kinds
    assert "reply" in kinds
    # reply tokens come after the artifact is built (no interleave to split the message)
    assert max(i for i, k in enumerate(kinds) if k == "artifact") < min(
        i for i, k in enumerate(kinds) if k == "reply"
    )
    # the streamed artifact bodies grow toward the final body
    bodies = [e["artifact"]["body"] for e in events if e["type"] == "artifact"]
    assert bodies == sorted(bodies, key=len)
    assert events[-1]["artifact"]["body"] == "A" * 200


def test_brainstorm_emits_a_choice_for_the_angles(isolated_state, install_models):
    install_models(
        FakeModelScript(
            plan=TurnPlan(mode="brainstorm", topic="Widget"),
            angles=[
                "Angle A - hook one",
                "Angle B - hook two",
                "Angle C - hook three",
            ],
        )
    )
    events = _collect(
        user_id="s", conversation_id="c1", user_message="brainstorm angles for Widget"
    )
    choices = [e for e in events if e["type"] == "ui_choice"]
    assert len(choices) == 1
    assert [o["label"] for o in choices[0]["options"]] == [
        "Angle A - hook one",
        "Angle B - hook two",
        "Angle C - hook three",
    ]
    assert choices[0]["question"]
    # the choice is offered before the turn is finalized
    assert [e["type"] for e in events].index("ui_choice") < len(events) - 1


def test_draft_turn_emits_no_choice(isolated_state, install_models):
    install_models(
        FakeModelScript(
            plan=TurnPlan(mode="draft", topic="Widget", format="linkedin_post")
        )
    )
    events = _collect(
        user_id="s", conversation_id="c2", user_message="draft a post about Widget"
    )
    assert not [e for e in events if e["type"] == "ui_choice"]


def test_chat_streams_real_reply_tokens(isolated_state, install_models):
    install_models(
        FakeModelScript(
            plan=TurnPlan(mode="chat", reply_gist="answer the question"),
            reply="Signal helps you brainstorm and draft content, keeping a live artifact.",
        )
    )
    events = _collect(user_id="s", conversation_id="s2", user_message="what do you do?")
    reply = "".join(e["delta"] for e in events if e["type"] == "reply")
    assert reply.startswith("Signal helps you brainstorm")
    assert len([e for e in events if e["type"] == "reply"]) > 1  # actually chunked
