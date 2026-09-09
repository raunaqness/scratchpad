"""Deterministic backend tests — no remote model, scripted fakes only.

These pin the Scratchpad contract: note / expand / tighten / brainstorm /
critique ops on a freeform scratchpad, the `build` path (skills → derived
artifacts, isolated from the scratchpad and its version list), non-blocking
grounding, the linear version history, and checkpointer persistence.
"""

from __future__ import annotations

from backend.app import get_thread_state, run_conversation
from backend.artifact import Scratchpad, apply_client_edits
from backend.memory_store import load_memory
from backend.signal_models import Critique, GroundingNotes, TurnPlan
from backend.textutil import dedupe, enforce_max_words, max_words, word_count
from tests.conftest import FakeModelScript


# --------------------------------------------------------------------------- #
# unit helpers
# --------------------------------------------------------------------------- #


def test_textutil_helpers():
    assert max_words("under 100 words") == 100
    assert enforce_max_words("one two three four", 2) == "one two"
    assert word_count("a b c") == 3
    assert dedupe(["a", " a ", "", "b"]) == ["a", "b"]


def test_scratchpad_versioning_and_client_edits():
    sp = Scratchpad(topic="Widgets")
    assert sp.version == 0 and sp.status == "empty"
    bumped = sp.touched(status="developing")
    assert bumped.version == 1 and bumped.updated_at and bumped.status == "developing"

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


def _note(script: FakeModelScript, msg: str, *, cid: str) -> dict:
    script.plan = TurnPlan(mode="note")
    return _run(msg, conversation_id=cid)


def test_note_captures_raw_text_verbatim(isolated_state, install_models):
    install_models(FakeModelScript(plan=TurnPlan(mode="note")))
    result = _run("Idea: faster cold starts for edge functions")

    sp = result["scratchpad"]
    assert result["mode"] == "note"
    assert sp["body"] == "Idea: faster cold starts for edge functions"
    assert sp["status"] == "notes"
    assert sp["version"] == 1
    assert result["head"] == 1
    assert result["derived"] == []


def test_expand_develops_the_body_and_flags_claims(isolated_state, install_models):
    script = install_models(
        FakeModelScript(
            plan=TurnPlan(mode="note"),
            expanded="Cold starts hurt developer flow. They also waste idle spend. [TK: confirm the number]",
            grounding=["'wastes idle spend' has no figure in sources"],
        )
    )
    _note(script, "cold starts idea", cid="ex")

    script.plan = TurnPlan(mode="expand")
    result = _run("flesh this out", conversation_id="ex")

    sp = result["scratchpad"]
    assert sp["body"].startswith("Cold starts hurt developer flow")
    assert sp["status"] == "developing"
    assert any("idle spend" in q for q in sp["open_questions"])  # flagged, not blocked
    assert result["head"] == 2


def test_expand_unwraps_a_json_or_fenced_model_reply(isolated_state, install_models):
    # A model that answers with a ```json envelope instead of plain markdown
    # must not leave raw JSON in the scratchpad body.
    script = install_models(
        FakeModelScript(
            plan=TurnPlan(mode="note"),
            expanded='```json\n{"body": "# Cold starts\\n\\nReal developed prose."}\n```',
            grounding=[],
        )
    )
    _note(script, "cold starts idea", cid="jw")

    script.plan = TurnPlan(mode="expand")
    result = _run("flesh this out", conversation_id="jw")

    body = result["scratchpad"]["body"]
    assert body == "# Cold starts\n\nReal developed prose."
    assert "{" not in body and "```" not in body


def test_build_unwraps_a_fenced_skill_reply(isolated_state, install_models):
    script = install_models(
        FakeModelScript(
            plan=TurnPlan(mode="note"),
            skill_output='```markdown\n# Outline\n\n- one\n- two\n```',
            grounding=[],
        )
    )
    _note(script, "some notes to build from", cid="jb")

    script.plan = TurnPlan(mode="build", skill_id="blog_outline")
    result = _run("turn this into a blog outline", conversation_id="jb")

    tab = result["derived"][-1]
    assert tab["body"] == "# Outline\n\n- one\n- two"
    assert "```" not in tab["body"]


def test_tighten_edits_the_existing_body(isolated_state, install_models):
    script = install_models(
        FakeModelScript(plan=TurnPlan(mode="note"), tightened="Cold starts kill flow. Fixed.")
    )
    _note(script, "a wordy first note about cold starts that rambles a bit", cid="ti")

    script.plan = TurnPlan(mode="tighten", edit_instruction="cut it down")
    result = _run("cut it down", conversation_id="ti")

    assert result["scratchpad"]["body"] == "Cold starts kill flow. Fixed."
    assert result["scratchpad"]["status"] == "developing"


def test_brainstorm_fills_the_board_without_a_draft(isolated_state, install_models):
    install_models(
        FakeModelScript(
            plan=TurnPlan(mode="brainstorm", topic="faster cold starts"),
            angles=["Dev time saved - the lens", "Cost of idle compute - the lens"],
        )
    )
    result = _run("help me think about faster cold starts")

    sp = result["scratchpad"]
    assert result["mode"] == "brainstorm"
    assert len(sp["angles"]) == 2
    assert sp["body"] == ""
    assert sp["status"] == "notes"


def test_critique_annotates_open_questions(isolated_state, install_models):
    script = install_models(
        FakeModelScript(
            plan=TurnPlan(mode="note"),
            critique=Critique(summary="A thread is forming.", points=["Name the reader", "Cut the hedge"]),
        )
    )
    _note(script, "some notes about a launch", cid="crit")

    script.plan = TurnPlan(mode="critique")
    result = _run("what's weak here?", conversation_id="crit")

    assert result["assistant_message"] == "A thread is forming."
    assert "Name the reader" in result["scratchpad"]["open_questions"]
    assert "Cut the hedge" in result["scratchpad"]["open_questions"]


# --------------------------------------------------------------------------- #
# build (skills → derived artifacts)
# --------------------------------------------------------------------------- #


def test_build_produces_a_derived_tab_from_the_scratchpad(isolated_state, install_models):
    script = install_models(
        FakeModelScript(
            plan=TurnPlan(mode="note"),
            skill_output="# Cold starts\n\nA working title, a lede, and sections.",
            grounding=[],
        )
    )
    _note(script, "cold starts notes", cid="bd")

    script.plan = TurnPlan(mode="build", skill_id="blog_outline")
    result = _run("turn this into a blog outline", conversation_id="bd")

    assert result["mode"] == "build" and result["skill_id"] == "blog_outline"
    assert len(result["derived"]) == 1
    tab = result["derived"][0]
    assert tab["skill_id"] == "blog_outline"
    assert tab["skill_name"] == "Blog outline"
    assert tab["body"].startswith("# Cold starts")
    assert tab["from_version"] == 1


def test_build_does_not_touch_the_scratchpad_or_its_versions(isolated_state, install_models):
    script = install_models(
        FakeModelScript(plan=TurnPlan(mode="note"), skill_output="## A campaign plan", grounding=[])
    )
    _note(script, "a rough idea", cid="iso")

    script.plan = TurnPlan(mode="build", skill_id="marketing_campaign")
    before = get_thread_state("iso")["scratchpad"]
    result = _run("build a campaign", conversation_id="iso")
    after = result["scratchpad"]

    assert after["version"] == before["version"]  # scratchpad untouched
    assert after["body"] == before["body"]
    assert result["head"] == 1  # no new version from a build


def test_rebuilding_a_skill_replaces_its_tab(isolated_state, install_models):
    script = install_models(
        FakeModelScript(plan=TurnPlan(mode="note"), skill_output="post v1", grounding=[])
    )
    _note(script, "some notes", cid="rb")

    script.plan = TurnPlan(mode="build", skill_id="social_post")
    r1 = _run("make a social post", conversation_id="rb")
    assert len(r1["derived"]) == 1
    first_id = r1["derived"][0]["id"]

    # change the scratchpad, then rebuild the same skill
    script.plan = TurnPlan(mode="expand")
    script.expanded = "some notes, expanded"
    _run("develop it", conversation_id="rb")

    script.plan = TurnPlan(mode="build", skill_id="social_post")
    script.skill_output = "post v2"
    r2 = _run("make a social post again", conversation_id="rb")
    assert len(r2["derived"]) == 1  # replaced, not appended
    assert r2["derived"][0]["id"] == first_id
    assert r2["derived"][0]["body"] == "post v2"

    # a different skill adds a second tab
    script.plan = TurnPlan(mode="build", skill_id="blog_outline")
    script.skill_output = "# Outline"
    r3 = _run("now a blog outline", conversation_id="rb")
    assert [d["skill_id"] for d in r3["derived"]] == ["social_post", "blog_outline"]


def test_build_with_unknown_skill_asks_which(isolated_state, install_models):
    script = install_models(FakeModelScript(plan=TurnPlan(mode="note")))
    _note(script, "some notes", cid="unk")

    script.plan = TurnPlan(mode="build", skill_id=None)
    result = _run("build something from this", conversation_id="unk")

    assert result["derived"] == []
    assert result["status"] == "needs_input"
    assert "Which one" in result["assistant_message"]


def test_build_flags_unsupported_claims_on_the_output_not_the_scratchpad(
    isolated_state, install_models
):
    script = install_models(
        FakeModelScript(
            plan=TurnPlan(mode="note", confirmed_facts=["ships next week"]),
            skill_output="Launch post: our tool is 10x faster and costs $9.",
            grounding=["'10x faster' and '$9' are not in sources"],
        )
    )
    _note(script, "launch notes", cid="gnd")

    script.plan = TurnPlan(mode="build", skill_id="social_post")
    result = _run("make a social post", conversation_id="gnd")

    tab = result["derived"][0]
    assert any("10x" in q for q in tab["open_questions"])
    assert result["scratchpad"]["open_questions"] == []  # scratchpad untouched


# --------------------------------------------------------------------------- #
# safety / persistence
# --------------------------------------------------------------------------- #


def test_publish_request_is_declined_with_no_side_effect(isolated_state, install_models):
    install_models(FakeModelScript(plan=TurnPlan(mode="chat", reply_gist="they asked to publish")))
    result = _run("great, now publish this to LinkedIn for me")

    assert result["plan"]["safety_flag"] == "publish_request"
    assert "can't" in result["assistant_message"] or "cannot" in result["assistant_message"]
    assert result["scratchpad"]["body"] == ""
    assert result["status"] == "needs_input"


def test_clarifying_question_is_returned_verbatim(isolated_state, install_models):
    question = "Which thread - the cost story or the developer-time story?"
    install_models(FakeModelScript(plan=TurnPlan(mode="brainstorm", clarifying_question=question)))
    result = _run("help me think")

    assert result["assistant_message"] == question
    assert result["status"] == "needs_input"
    assert result["pending_question"] == question


def test_empty_op_output_asks_for_more(isolated_state, install_models):
    script = install_models(
        FakeModelScript(plan=TurnPlan(mode="note"), expanded="   ")
    )
    _note(script, "a note", cid="empty")
    script.plan = TurnPlan(mode="expand")
    result = _run("develop it", conversation_id="empty")

    assert result["status"] == "needs_input"
    assert "empty" in result["assistant_message"]


def test_state_and_memory_persist_across_turns(isolated_state, install_models):
    script = install_models(
        FakeModelScript(
            plan=TurnPlan(mode="note", audience="platform engineers"),
            expanded="A developed note for platform engineers.",
        )
    )
    _run("jot: a note for platform engineers", conversation_id="persist", user_id="mem-u")

    script.plan = TurnPlan(mode="expand")
    _run("develop it", conversation_id="persist", user_id="mem-u")

    state = get_thread_state("persist")
    assert len(state["turns"]) == 4  # user/assistant x2
    assert state["scratchpad"]["version"] >= 2

    memory = load_memory("mem-u")
    assert memory["facts"]["audience"]["value"] == "platform engineers"


# --------------------------------------------------------------------------- #
# subject rename / rebase
# --------------------------------------------------------------------------- #


def test_subject_rename_rebases_the_scratchpad(isolated_state, install_models):
    script = install_models(
        FakeModelScript(
            plan=TurnPlan(mode="note", topic="JBL speaker", confirmed_facts=["20-hour battery"]),
            grounding=[],
        )
    )
    first = _run("notes about the JBL speaker", conversation_id="rn")
    assert first["scratchpad"]["topic"] == "JBL speaker"
    assert first["scratchpad"]["sources"]

    script.plan = TurnPlan(
        mode="expand",
        topic="Fujifilm XT20",
        subject_changed=True,
        edit_instruction="this is actually about the Fujifilm XT20",
    )
    script.expanded = "A developed note about the Fujifilm XT20."
    second = _run("actually this is about the Fujifilm XT20", conversation_id="rn")

    sp = second["scratchpad"]
    assert sp["topic"] == "Fujifilm XT20"
    assert sp["title"] == "Fujifilm XT20"
    assert sp["sources"] == []
    assert "Rebased" in second["assistant_message"]


def test_added_detail_does_not_rebase(isolated_state, install_models):
    script = install_models(
        FakeModelScript(
            plan=TurnPlan(mode="note", topic="Widget Pro", confirmed_facts=["ships in March"]),
            grounding=[],
        )
    )
    _run("notes about Widget Pro", conversation_id="dt")

    script.plan = TurnPlan(mode="expand", topic="Widget Pro", edit_instruction="add the price")
    script.expanded = "Widget Pro, $99, ships in March."
    result = _run("also it's $99", conversation_id="dt")

    assert result["scratchpad"]["sources"] == ["ships in March"]
    assert "Rebased" not in result["assistant_message"]


# --------------------------------------------------------------------------- #
# version history (unit + integration)
# --------------------------------------------------------------------------- #


def test_append_version_truncates_and_caps():
    from backend.versions import MAX_VERSIONS, append_version

    versions: list = []
    for i in range(3):
        versions = append_version(versions, {"body": f"b{i}"}, f"m{i}")
    assert [v["artifact"]["body"] for v in versions] == ["b0", "b1", "b2"]

    versions = append_version(versions, {"body": "bx"}, "mx", base_seq=1)
    assert [v["artifact"]["body"] for v in versions] == ["b0", "bx"]

    versions = []
    for i in range(MAX_VERSIONS + 10):
        versions = append_version(versions, {"body": str(i)}, "m")
    assert len(versions) == MAX_VERSIONS
    assert versions[0]["artifact"]["body"] == "10"


def test_artifact_changed_ignores_version_field():
    from backend.versions import artifact_changed

    assert artifact_changed({"body": "a"}, {"body": "b"})
    assert artifact_changed({}, {"angles": ["x"]})
    assert not artifact_changed({"body": "a", "version": 1}, {"body": "a", "version": 9})


def test_version_history_grows_one_per_scratchpad_turn(isolated_state, install_models):
    script = install_models(
        FakeModelScript(plan=TurnPlan(mode="note"), expanded="Developed.", grounding=[])
    )
    r1 = _run("jot: first idea", conversation_id="vh")
    assert r1["head"] == 1

    script.plan = TurnPlan(mode="expand")
    r2 = _run("develop it", conversation_id="vh")
    assert r2["head"] == 2

    # a pure chat turn does not touch the scratchpad
    script.plan = TurnPlan(mode="chat", reply_gist="answer")
    script.reply = "It's a scratchpad for ideas."
    r3 = _run("what is this?", conversation_id="vh")
    assert r3["head"] == 2

    # a build turn does not touch the scratchpad version list
    script.plan = TurnPlan(mode="build", skill_id="blog_outline")
    r4 = _run("make an outline", conversation_id="vh")
    assert r4["head"] == 2


def test_editing_from_a_past_version_truncates_forward(isolated_state, install_models):
    script = install_models(
        FakeModelScript(plan=TurnPlan(mode="note"), grounding=[])
    )
    _run("jot: v1", conversation_id="br")

    script.plan = TurnPlan(mode="expand")
    script.expanded = "V2 body."
    _run("develop", conversation_id="br")

    script.expanded = "V3 body."
    r3 = _run("develop more", conversation_id="br")
    assert [v["body"] for v in r3["versions"]] == ["jot: v1", "V2 body.", "V3 body."]

    script.expanded = "V3-prime body."
    r4 = _run("a different change", conversation_id="br", base_version=2)
    assert r4["head"] == 3
    assert [v["body"] for v in r4["versions"]] == ["jot: v1", "V2 body.", "V3-prime body."]


# --------------------------------------------------------------------------- #
# creative follow-up agent
# --------------------------------------------------------------------------- #


def test_follow_ups_after_a_scratchpad_change(isolated_state, install_models):
    install_models(FakeModelScript(plan=TurnPlan(mode="note")))
    result = _run("Idea: faster cold starts", conversation_id="fu1")
    labels = [f["label"] for f in result["follow_ups"]]
    assert len(labels) == 3
    assert all(isinstance(x, str) and x for x in labels)
    assert {f["kind"] for f in result["follow_ups"]} <= {
        "fact", "perspective", "tone", "angle", "direction", "question"
    }


def test_no_follow_ups_on_a_chat_turn(isolated_state, install_models):
    install_models(FakeModelScript(plan=TurnPlan(mode="chat")))
    result = _run("hey there", conversation_id="fu2")
    assert result["mode"] == "chat"
    assert result["follow_ups"] == []


def test_no_follow_ups_on_a_build_turn(isolated_state, install_models):
    script = install_models(
        FakeModelScript(plan=TurnPlan(mode="note"), grounding=[])
    )
    _run("jot: a product idea", conversation_id="fu3")
    script.plan = TurnPlan(mode="build", skill_id="blog_outline")
    result = _run("make an outline", conversation_id="fu3")
    assert result["follow_ups"] == []


def test_follow_up_failure_does_not_break_the_turn(
    isolated_state, install_models, monkeypatch
):
    install_models(FakeModelScript(plan=TurnPlan(mode="note")))

    def _boom(_scratchpad):
        raise RuntimeError("creative agent down")

    monkeypatch.setattr("backend.app.follow_ups", _boom)
    result = _run("Idea: a resilient turn", conversation_id="fu4")
    assert result["mode"] == "note"
    assert result["scratchpad"]["body"] == "Idea: a resilient turn"
    assert result["follow_ups"] == []
