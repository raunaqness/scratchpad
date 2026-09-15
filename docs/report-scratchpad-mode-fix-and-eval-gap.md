# Report: scratchpad build/expand misclassification, and a DeepEval blind spot

Status: fix applied and verified against the live model; eval scoring issue
diagnosed but not yet resolved. Written as a brainstorming reference, not a
finished conclusion — see "Open questions" at the end.

## Background — how we got here

This session built a pipeline to turn real, feedback-annotated conversations
from the `/app` UI into regression tests:

1. An always-open feedback box in `/app` lets the user leave a comment on any
   turn. Comments are stored as Langfuse `user_feedback` scores keyed by a
   deterministic per-turn trace id (`backend/tracing.py`).
2. `backend/export_conversation.py` (wrapped by `scripts/export-conversation.sh`)
   exports a full thread — every turn's user message, assistant reply,
   internal trajectory, and any feedback — into
   `tests/fixtures/conversations/<conversation_id>.json`.
3. A human then adds an `expected` block to each turn: what *should* have
   happened. For turns where feedback exists, the feedback text itself is the
   spec — not paraphrased, not reinterpreted.
4. Two test layers read the same fixture:
   - `tests/test_recorded_conversations.py` — deterministic, replays against
     an offline fake LLM with `interpret`'s output pinned to the turn's
     historical `mode`. Can only check mechanical state changes (did the
     scratchpad version bump, did angles populate, etc.) — it can never test
     whether `interpret` would classify the raw message the same way for
     real, because the test force-feeds the mode.
   - `tests/test_recorded_conversations_deepeval.py` — live-model, LLM-judged
     (`ConversationalGEval` via DeepEval, OpenRouter). Used for exactly the
     turns where the real bug is in live classification or reply content —
     the deterministic layer explicitly skips these and points here instead.

One real fixture exists so far:
`tests/fixtures/conversations/2c0a0d76-e5d4-4956-8ba6-bdc6375271ce.json`, 7
turns, a real conversation about summarizing China/AI news into two blog
posts. Turns 4, 5, 6 are tagged `"testable_via": "deepeval"`.

## The three flagged turns, verbatim

**Turn 4** — user: *"can you build 2 blogs in the scratchpad, keep them short
for now"*
Original reply: *"I can build a few things from your scratchpad — Blog
outline (blog_outline); Social post (social_post); Marketing campaign
(marketing_campaign). Which one?"* — nothing added to the scratchpad.
User's feedback: *"I asked the system to generate 2 blogs, and put these in
the scratch pad to get started. Instead I got this message as response...and
nothing new added to the scratch pad"*

**Turn 5** — user: *"can you generate outlines for now and the put it to the
scratchpad"*
Original reply: *"Built a blog outline from v9 — see the Blog outline tab. 5
claims in it aren't in your sources — listed on the tab."*
User's feedback: *"The system generated a Blog outline - instead it should
have only worked on the scratchpad. It should have waited for the user to
click on the 'Blog outline' button to generate the blog outline."*

**Turn 6** — user: *"what are your sources for the blogs you've generated?"*
Original reply: *"The sources for the blog outlines are based on the angles
and topics discussed in the scratchpad, but specific citations or references
have not been provided yet..."*
User's feedback (two comments): *"...it changed the UI elements, the UI
should not do anything unless the scratchpad - blog outline etc are being
updated. these should be purely based on user action"* and *"...since we are
have a knowledge graph - it should have cited any facts take from there
only."*

## The bug behind turns 4 and 5 — confirmed and fixed

Both turns are the same root cause: `interpret_prompt` in
`backend/prompts.py` chose `mode: build` whenever the message contained
build-sounding verbs ("build", "generate", "outline", "campaign", etc.),
without checking *where the user wanted the result to land*. Both real
messages explicitly scope the request to the scratchpad — "build 2 blogs
**in the scratchpad**", "generate outlines ... **put it to the
scratchpad**" — which should mean "develop the body" (`mode: expand`), not
"produce the separate artifact a skill builds" (`mode: build`).

**Fix applied** (`backend/prompts.py`, `interpret_prompt`'s "Choosing mode"
section): added an explicit rule that scratchpad-scoping language overrides
build-sounding verbs — see the current diff in git history on this file for
the exact wording.

**Verification**: ran the live-model DeepEval scenario for both turns twice
(a fresh run, then an immediate repeat with zero further code changes, to
separate judge noise from a real behavior change). In all post-fix runs, the
reply for both turns changed from the buggy pattern to
`"Developed the scratchpad (N words). Flagged 5 things to confirm."` — i.e.
`interpret` now correctly classifies both as `expand`. This matches the
fixture's own hand-authored `expected` block for these turns exactly
(`mode: expand`, `scratchpad_changed: true`, and for turn 5,
`derived_unchanged: true`). This is confirmed by reading the actual
`assistant_message` text across runs, not by trusting the DeepEval score
(see next section for why the score can't be trusted here).

## The eval blind spot — the score didn't track the fix

Raw results, threshold 0.7, `ConversationalGEval`:

| turn | baseline (pre-fix) | post-fix run 1 | post-fix run 2 (repeat, no code change) |
|---|---|---|---|
| 4 | 0.3 ❌ | 0.7 ✅ | 0.5 ❌ |
| 5 | 0.7 ✅ *(reply reproduced the exact reported bug)* | 0.3 ❌ | 0.6 ❌ |
| 6 | 0.8 ✅ | 0.8 ✅ | 0.7 ✅ (unaffected by this fix, as expected) |

The damning data point: turn 5's **baseline** reply — which built a
premature blog outline, i.e. reproduced the literal bug the user
complained about — scored 0.7 and **passed**. Both **post-fix** replies,
which do not reproduce the bug, scored 0.3 and 0.6 and **failed**. The score
moved in the wrong direction relative to the real fix. That ruled out "the
judge is just noisy near the threshold, run it again" as a full
explanation, and prompted reading the implementation directly instead of
running more iterations.

**Root cause, found by reading code, not by guessing:**
`_develop()` in `backend/app.py` (used by both `_expand` and `_tighten`)
builds the chat-bubble confirmation message like this:

```python
verb = "Developed" if node == "expand" else "Tightened"
message = _rebase_note(state, scratch) + f"{verb} the scratchpad ({word_count(body)} words)."
if notes:
    message += f" Flagged {len(notes)} thing{'s' if len(notes) != 1 else ''} to confirm."
```

This string is a hardcoded Python template — no LLM call, no system prompt,
no `reply_gist` input, nothing. It is deliberately terse by design:
`SCRATCHPAD_SYSTEM_PROMPT` states outright, *"Keep chat replies short...
The scratchpad carries the work, not your reply."* The actual developed
content (the real fix) lands in the scratchpad `body`, which the UI shows
separately.

`tests/test_recorded_conversations_deepeval.py`'s `test_case` is built from
`Turn(role="assistant", content=result["assistant_message"])` — i.e. it only
ever sees this fixed-template bubble. It never sees the scratchpad body. So
for any turn whose real fix lands in the scratchpad (which is every
`note`/`expand`/`tighten`/`brainstorm` turn — most of what this app does),
the DeepEval judge is structurally judging the wrong artifact. No prompt
edit can change a string that no prompt touches, so further "system prompt
only" tuning iterations were abandoned as pointless (this was meant to run
as a 5-iteration loop; stopped after iteration 1 once this was confirmed by
reading `_develop()`, rather than mechanically burning 4 more paid live-API
iterations against a proven wall).

## Turn 6 — a separate, independent problem

Turn 6's citation complaint is unrelated to the above and was not addressed
by this fix. Two layered issues:

1. **Retrieval query construction** (`_ground()` in `backend/app.py`) passes
   the raw user message straight into `blog_graph.query(user_id,
   state["user_message"], num_results=5)`. "What are your sources for the
   blogs you've generated?" is a meta-question about the conversation, not
   content-bearing text about China/AI — it's plausibly a poor semantic-search
   query regardless of what's ingested, which would explain why the
   production reply read like it had nothing to cite even though the user
   had, per turn 0, ingested "older blogs" on the topic.
2. **Test harness has no ingested data at all for the synthetic eval user.**
   `isolated_state` (in `tests/test_recorded_conversations_deepeval.py`)
   points `settings.data_dir`/`settings.db_path` at a fresh empty `tmp_path`
   DB every run. `_ground()` gates on
   `runs_store.ingested_count(user_id) > 0` before querying at all — for the
   eval user this is always `0`, so `grounding` is always `None`/empty,
   unconditionally, no matter what code or prompt changes are made. Turn 6's
   "cites_knowledge_base_facts" expectation is currently **untestable** in
   this harness, full stop — not low-scoring, not flaky, structurally unable
   to ever pass or meaningfully fail.

Turn 6 also carries a second complaint — the workspace UI (scratchpad/derived
tabs) shouldn't change on a plain chat turn — that neither test layer can
check at all (DeepEval only judges reply text; this needs a browser-level
test, which doesn't exist yet).

## Open questions / paths forward (for brainstorming)

1. **Should the DeepEval test see the scratchpad body, not just the chat
   bubble?** E.g. append a diff of `scratchpad.body` (before → after) to
   what's judged for expand/note/tighten-mode turns. This would make the
   test actually measure what changed. Risk: conflates "does the reply text
   read well" with "did the content development go well" — may need two
   separate assertions/metrics instead of one blended judgment.
2. **Should the confirmation message itself say more?** E.g. name what was
   added instead of just a word count. This directly contradicts the
   existing, deliberate "keep chat replies short" principle in
   `SCRATCHPAD_SYSTEM_PROMPT` — would need to be a product decision, not a
   quiet prompt edit, and risks making replies feel bloated/templated in
   normal use just to satisfy a test.
3. **Turn 6's retrieval query**: should `_ground()` use something other than
   the raw last message when querying the knowledge graph for a meta/context
   question — e.g. the scratchpad's `topic`, or a blend of recent turns? Is
   there a cheap way to detect "this message is asking *about* the knowledge
   base / sources" versus "this message's content should itself be
   embedded"?
4. **Turn 6's test harness gap**: to make this turn testable at all, the
   eval would need to seed the isolated test DB with ingested content that
   plausibly matches what the real user had (China/AI articles), so
   `_ground()` has something real to retrieve. Is that worth building, or
   should turn 6 be reclassified/deprioritized until there's a broader
   fixture-seeding mechanism?
5. **Should turns 4/5 even stay in the DeepEval layer** now that the
   classification bug is fixed? Their remaining risk is now "does `interpret`
   keep classifying scratchpad-scoped requests as `expand`" — which is a
   narrower, more mechanical question that might tolerate a cheaper/more
   deterministic check (e.g. a live-model classification-only test that
   asserts on `plan.mode` directly, sidestepping the reply-text judging
   problem entirely) rather than a full conversational GEval.
6. **General judge reliability**: even ignoring the structural blind spot,
   raw score variance between two identical-code runs was large (turn 4:
   0.7 vs 0.5; turn 5: 0.3 vs 0.6). Worth deciding whether single-run
   pass/fail is ever trustworthy near the 0.7 threshold, or whether the
   suite should average multiple judge calls per case before asserting.

## Relevant files

- `backend/prompts.py` — `interpret_prompt` (the fix), `SCRATCHPAD_SYSTEM_PROMPT`
  (the "keep replies short" principle in tension with option 2 above)
- `backend/app.py` — `_develop()` (hardcoded confirmation message), `_ground()`
  (knowledge-graph retrieval query), `_route()` (mode → node dispatch)
- `backend/capabilities/writing.py` — `chat_reply()` (turn 6's actual LLM call)
- `tests/test_recorded_conversations_deepeval.py` — the live-model test layer
  with the blind spot
- `tests/test_recorded_conversations.py` — the deterministic layer (can't
  test any of this — see its module docstring)
- `tests/fixtures/conversations/2c0a0d76-e5d4-4956-8ba6-bdc6375271ce.json` —
  the source fixture, all `expected` blocks and verbatim feedback
- `tests/eval_iterations/` — raw JSON score/reason dumps for baseline, fix,
  and repeat runs; `run_eval.py` is the standalone (non-pytest) runner used
  to capture score+reason for every case including passes, since pytest's
  default output hides those
- `docs/eval-tuning-log.md` — the turn-by-turn tuning log this report
  summarizes
