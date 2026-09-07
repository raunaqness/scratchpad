"""Deterministic backend tests — no remote model, scripted fakes only.

These pin the think-pad contract: brainstorm/draft/revise/critique modes, blog +
article formats, non-blocking grounding flags, a versioned live artifact, and
checkpointer-backed persistence.
"""

from __future__ import annotations

from backend.app import get_thread_state, run_conversation
from backend.artifact import Artifact, apply_client_edits
from backend.memory_store import load_memory
from backend.signal_models import Critique, TurnPlan
from backend.textutil import dedupe, enforce_max_words, max_words, word_count
from tests.conftest import FakeModelScript


# --------------------------------------------------------------------------- #
# unit helpers
# --------------------------------------------------------------------------- #


def test_max_words_and_enforcement():
    assert max_words("under 100 words") == 100
    assert max_words("no more than 60 words") == 60
    assert max_words("keep it to 80 words") == 80
    assert max_words("medium length") is None
    assert enforce_max_words("one two three four", 2) == "one two"
    assert enforce_max_words("one two", 5) == "one two"
    assert word_count("a b c") == 3
    assert dedupe(["a", " a ", "", "b"]) == ["a", "b"]


def test_artifact_versioning_and_client_edits():
    art = Artifact(format="blog_post", topic="Widgets")
    assert art.version == 0 and art.status == "empty"
    bumped = art.touched(status="drafting")
    assert bumped.version == 1 and bumped.updated_at and bumped.status == "drafting"

    edited = apply_client_edits(bumped, {"body": "user wrote this", "version": 999})
    assert edited.body == "user wrote this"
    assert edited.version == 2  # server-owned, not the client's 999


# --------------------------------------------------------------------------- #
# graph behaviour
# --------------------------------------------------------------------------- #


def _run(user_message: str, *, conversation_id: str = "c1", user_id: str = "u1", **kw):
    return run_conversation(
        user_id=user_id,
        conversation_id=conversation_id,
        user_message=user_message,
        **kw,
    )


def test_brainstorm_turn_fills_the_board(isolated_state, install_models):
    install_models(
        FakeModelScript(
            plan=TurnPlan(
                mode="brainstorm", topic="faster cold starts", format="linkedin_post"
            ),
            angles=["Dev time saved - the hook", "Cost of idle compute - the hook"],
        )
    )
    result = _run("help me post about our faster cold starts")

    art = result["artifact"]
    assert result["mode"] == "brainstorm"
    assert art["kind"] == "idea_board"
    assert len(art["angles"]) == 2
    assert art["body"] == ""
    assert art["status"] == "exploring"
    assert art["version"] == 1


def test_blog_post_is_a_supported_format(isolated_state, install_models):
    install_models(
        FakeModelScript(
            plan=TurnPlan(mode="draft", format="blog_post", topic="Edge Functions"),
            draft="# Faster edge functions\n\nYour cold starts were the problem. Here's the fix.",
        )
    )
    result = _run("write a blog post about our edge functions launch")

    assert result["status"] == "refining"
    assert result["artifact"]["format"] == "blog_post"
    assert result["artifact"]["kind"] == "draft"
    assert result["artifact"]["body"].startswith("# Faster edge functions")


def test_draft_flags_unsupported_claims_without_blocking(isolated_state, install_models):
    install_models(
        FakeModelScript(
            plan=TurnPlan(mode="draft", topic="Widget Pro", format="linkedin_post"),
            draft="Widget Pro cuts latency to 12ms.",
            grounding=["'cuts latency to 12ms' is not in the confirmed sources"],
        )
    )
    result = _run("draft a linkedin post: Widget Pro is our new latency tool")

    assert result["status"] == "refining"  # flagged, not blocked
    assert any("12ms" in q for q in result["artifact"]["open_questions"])
    assert result["artifact"]["body"]


def test_exploratory_product_is_not_gated_on_facts(isolated_state, install_models):
    install_models(
        FakeModelScript(
            plan=TurnPlan(
                mode="brainstorm",
                product_mode="exploratory",
                topic="an AI notepad I'm dreaming up",
            ),
            angles=["The blank-page problem - [assumption] users freeze at the start"],
        )
    )
    result = _run("I have a rough idea for an AI notepad, help me think it through")

    assert result["status"] == "exploring"
    assert result["artifact"]["product_mode"] == "exploratory"
    assert result["artifact"]["angles"]
    assert result["pending_question"] is None  # it did NOT demand three facts


def test_revise_updates_the_draft_and_bumps_version(isolated_state, install_models):
    script = install_models(
        FakeModelScript(
            plan=TurnPlan(mode="draft", topic="Widget", format="linkedin_post"),
            draft="First pass about Widget, a little wordy and slow to start.",
            revised="Punchy Widget line. No throat-clearing.",
        )
    )
    first = _run("draft a post about Widget", conversation_id="rev")
    assert first["artifact"]["version"] == 1

    script.plan = TurnPlan(mode="revise", revise_instruction="make it punchier")
    second = _run("make it punchier", conversation_id="rev")

    assert second["artifact"]["version"] == 2
    assert second["artifact"]["body"] == "Punchy Widget line. No throat-clearing."
    assert second["status"] == "refining"


def test_publish_request_is_declined_with_no_side_effect(isolated_state, install_models):
    install_models(
        FakeModelScript(plan=TurnPlan(mode="chat", reply_gist="they asked to publish"))
    )
    result = _run("great, now publish this to LinkedIn for me")

    assert result["plan"]["safety_flag"] == "publish_request"
    assert "can't" in result["assistant_message"] or "cannot" in result["assistant_message"]
    assert result["artifact"]["body"] == ""
    assert result["status"] == "needs_input"


def test_clarifying_question_is_returned_verbatim(isolated_state, install_models):
    question = "Which angle - the cost story or the developer-time story?"
    install_models(
        FakeModelScript(plan=TurnPlan(mode="brainstorm", clarifying_question=question))
    )
    result = _run("help me with a post")

    assert result["assistant_message"] == question
    assert result["status"] == "needs_input"
    assert result["pending_question"] == question


def test_state_and_memory_persist_across_turns(isolated_state, install_models):
    script = install_models(
        FakeModelScript(
            plan=TurnPlan(
                mode="draft",
                topic="Widget",
                format="linkedin_post",
                audience="platform engineers",
            ),
            draft="A draft about Widget for platform engineers.",
            revised="A tighter draft about Widget.",
        )
    )
    _run(
        "draft a post about Widget for platform engineers",
        conversation_id="persist",
        user_id="mem-u",
    )
    script.plan = TurnPlan(mode="revise", revise_instruction="cut a sentence")
    _run("cut a sentence", conversation_id="persist", user_id="mem-u")

    state = get_thread_state("persist")
    assert len(state["turns"]) == 4  # user/assistant x2
    assert state["artifact"]["version"] >= 2

    memory = load_memory("mem-u")
    assert memory["facts"]["audience"]["value"] == "platform engineers"


def test_empty_writer_output_asks_for_more_not_an_empty_draft(isolated_state, install_models):
    install_models(
        FakeModelScript(
            plan=TurnPlan(mode="draft", topic="Widget", format="linkedin_post"),
            draft="   ",  # model returned nothing usable
        )
    )
    result = _run("draft something")

    assert result["status"] == "needs_input"
    assert result["artifact"]["body"] == ""
    assert "empty" in result["assistant_message"]


def test_critique_annotates_open_questions(isolated_state, install_models):
    script = install_models(
        FakeModelScript(
            plan=TurnPlan(mode="draft", topic="Widget", format="linkedin_post"),
            draft="A first Widget draft.",
            critique=Critique(
                summary="Hook is soft.", points=["Rewrite line 1", "Add a CTA"]
            ),
        )
    )
    _run("draft a post about Widget", conversation_id="crit")
    script.plan = TurnPlan(mode="critique")
    result = _run("what's weak about this?", conversation_id="crit")

    assert result["assistant_message"] == "Hook is soft."
    assert "Rewrite line 1" in result["artifact"]["open_questions"]
    assert "Add a CTA" in result["artifact"]["open_questions"]
