"""Deterministic backend tests — no remote model, scripted fakes only.

These pin the think-pad contract: brainstorm/draft/revise/critique modes, blog +
article formats, non-blocking grounding flags, a versioned live artifact, and
checkpointer-backed persistence.
"""

from __future__ import annotations

from backend.app import get_thread_state, run_conversation
from backend.artifact import Artifact, apply_client_edits
from backend.memory_store import load_memory
from backend.signal_models import Critique, GroundingNotes, TurnPlan
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


def test_subject_rename_rebases_the_artifact(isolated_state, install_models):
    script = install_models(
        FakeModelScript(
            plan=TurnPlan(
                mode="draft",
                topic="JBL Bluetooth speaker",
                format="linkedin_post",
                confirmed_facts=["360-degree sound", "20-hour battery"],
            ),
            draft="A LinkedIn post about the JBL Bluetooth speaker and its 360-degree sound.",
            grounding=["the JBL speaker is waterproof"],
        )
    )
    first = _run(
        "draft a post about the JBL Bluetooth speaker", conversation_id="rename"
    )
    assert first["artifact"]["topic"] == "JBL Bluetooth speaker"
    assert first["artifact"]["sources"]
    assert first["artifact"]["open_questions"]

    script.plan = TurnPlan(
        mode="revise",
        topic="Fujifilm XT20 camera",
        subject_changed=True,
        revise_instruction="this is actually about the Fujifilm XT20 camera",
    )
    script.revised = "A LinkedIn post about the Fujifilm XT20 camera."
    script._grounding = GroundingNotes(items=[])
    second = _run(
        "actually this is about the Fujifilm XT20 camera", conversation_id="rename"
    )

    art = second["artifact"]
    assert art["topic"] == "Fujifilm XT20 camera"
    assert art["title"] == "Fujifilm XT20 camera"
    # context scoped to the old subject is dropped
    assert art["sources"] == []
    assert art["open_questions"] == []
    assert "Rebased" in second["assistant_message"]


def test_subject_detail_added_does_not_rebase(isolated_state, install_models):
    script = install_models(
        FakeModelScript(
            plan=TurnPlan(
                mode="draft",
                topic="Widget Pro",
                format="linkedin_post",
                confirmed_facts=["ships in March"],
            ),
            draft="A post about Widget Pro shipping in March.",
            grounding=[],
        )
    )
    _run("draft a post about Widget Pro", conversation_id="detail")

    # adding detail to the same subject: subject_changed stays False
    script.plan = TurnPlan(
        mode="revise",
        topic="Widget Pro",
        revise_instruction="mention the price",
    )
    script.revised = "A post about Widget Pro, $99, shipping in March."
    result = _run("also mention it's $99", conversation_id="detail")

    assert result["artifact"]["sources"] == ["ships in March"]
    assert "Rebased" not in result["assistant_message"]


def test_append_version_truncates_and_caps():
    from backend.versions import MAX_VERSIONS, append_version

    versions: list = []
    for i in range(3):
        versions = append_version(versions, {"body": f"b{i}"}, f"m{i}")
    assert [v["artifact"]["body"] for v in versions] == ["b0", "b1", "b2"]

    # branching from seq 1 drops everything after it
    versions = append_version(versions, {"body": "bx"}, "mx", base_seq=1)
    assert [v["artifact"]["body"] for v in versions] == ["b0", "bx"]

    versions = []
    for i in range(MAX_VERSIONS + 10):
        versions = append_version(versions, {"body": str(i)}, "m")
    assert len(versions) == MAX_VERSIONS
    assert versions[0]["artifact"]["body"] == "10"
    assert versions[-1]["artifact"]["body"] == str(MAX_VERSIONS + 9)


def test_artifact_changed_ignores_version_field():
    from backend.versions import artifact_changed

    assert artifact_changed({"body": "a"}, {"body": "b"})
    assert artifact_changed({}, {"angles": ["x"]})
    assert not artifact_changed(
        {"body": "a", "version": 1}, {"body": "a", "version": 9}
    )


def test_version_history_grows_one_per_artifact_turn(isolated_state, install_models):
    script = install_models(
        FakeModelScript(
            plan=TurnPlan(mode="draft", topic="Widget", format="linkedin_post"),
            draft="Draft one about Widget.",
            grounding=[],
        )
    )
    r1 = _run("draft a post about Widget", conversation_id="vh")
    assert r1["head"] == 1
    assert [v["seq"] for v in r1["versions"]] == [1]
    assert "Widget" in r1["versions"][0]["body"]

    script.plan = TurnPlan(mode="revise", revise_instruction="tighten it")
    script.revised = "A tighter draft about Widget."
    r2 = _run("tighten it", conversation_id="vh")
    assert r2["head"] == 2
    assert [v["seq"] for v in r2["versions"]] == [1, 2]

    # a pure chat turn doesn't touch the artifact -> no new version
    script.plan = TurnPlan(mode="chat", reply_gist="answer the question")
    script.reply = "It helps you draft content."
    r3 = _run("what do you do?", conversation_id="vh")
    assert r3["head"] == 2


def test_editing_from_a_past_version_truncates_forward(isolated_state, install_models):
    script = install_models(
        FakeModelScript(
            plan=TurnPlan(mode="draft", topic="Widget", format="linkedin_post"),
            draft="V1 body.",
            grounding=[],
        )
    )
    _run("draft", conversation_id="br")

    script.plan = TurnPlan(mode="revise", revise_instruction="a")
    script.revised = "V2 body."
    _run("rev a", conversation_id="br")

    script.plan = TurnPlan(mode="revise", revise_instruction="b")
    script.revised = "V3 body."
    r3 = _run("rev b", conversation_id="br")
    assert [v["body"] for v in r3["versions"]] == ["V1 body.", "V2 body.", "V3 body."]

    # navigate back to v2, then edit: v3 is replaced, not appended
    script.plan = TurnPlan(mode="revise", revise_instruction="c")
    script.revised = "V3-prime body."
    r4 = _run("different change", conversation_id="br", base_version=2)
    assert r4["head"] == 3
    assert [v["body"] for v in r4["versions"]] == [
        "V1 body.",
        "V2 body.",
        "V3-prime body.",
    ]


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
