# Signal — Known Issues & Proposed Solutions

A consolidated, standalone reference of open problems in the system as of
this writing — separate from `docs/tasklist.md` (which tracks prioritized
work items) so we have one place that captures *all* currently-known
issues, including ones not yet scheduled. Cross-references `tasklist.md`
where a fix is already planned/in-progress there.

---

## 1. Eval / test-suite issues

### 1.1 DeepEval judge is structurally blind to scratchpad content
`_develop()`'s chat-bubble confirmation (`backend/app.py`) is a hardcoded
template string (`"Developed the scratchpad (N words)."`) with zero
LLM/prompt involvement. `tests/test_recorded_conversations_deepeval.py`
only ever judges `result["assistant_message"]` — this fixed string — never
`scratchpad.body`, where the actual content lands. For every
`note`/`expand`/`tighten`/`brainstorm` turn (most of what the app does),
the judge is scoring the wrong artifact. Confirmed: a baseline reply that
reproduced a reported bug scored higher than a post-fix reply that didn't.

**Proposed fix:** append a diff of `scratchpad.body` (before → after) to
what the judge sees for these modes, or split into two metrics (reply
tone vs. content correctness) so they aren't blended into one score.
*(tasklist.md §2, item 1 — backlog)*

### 1.2 DeepEval judge score variance is large near the threshold
Two identical-code runs produced scores 0.7 vs 0.5, and 0.3 vs 0.6, on the
same case. Single-run pass/fail near the 0.7 threshold isn't trustworthy.

**Proposed fix:** average N (e.g. 3) judge calls per case before
asserting pass/fail, at least for threshold-adjacent cases.
*(tasklist.md §2, item 5 — backlog)*

### 1.3 Knowledge-graph citation eval (fixture turn 6) is structurally untestable
`isolated_state` in `test_recorded_conversations_deepeval.py` points at
an empty tmp DB every run. `_ground()` gates on
`runs_store.ingested_count(user_id) > 0`, which is always `0` for the
synthetic eval user — so `grounding` is always empty regardless of code
or prompt changes. The "cites knowledge-base facts" expectation can never
pass or meaningfully fail in this harness.

**Proposed fix:** monkeypatch `blog_graph.query` / `runs_store.ingested_count`
in the eval fixture to return canned facts matching the fixture's topic,
so `_ground()`'s downstream code path is actually exercised.
*(tasklist.md §2, item 3 — backlog)*

### 1.4 No live, cheap classification-only check for mode-routing regressions
The turns-4/5 mode-fix (scratchpad-scoping language overriding
build-sounding verbs) is currently only guarded by full conversational
`ConversationalGEval` tests, which are expensive and carry the blind spots
above. The actual risk surface is narrower: "does `interpret` keep
classifying scratchpad-scoped requests as `expand`."

**Proposed fix:** add a live-model, structured-output-only test that calls
`interpret` directly and asserts on `plan.mode`, sidestepping reply-text
judging entirely — cheaper and more targeted than a full GEval.
*(tasklist.md §2, item 4 — backlog)*

### 1.5 One live DeepEval test has confirmed classification-driven flakiness
`tests/test_note_formatting_live.py::test_multi_point_note_request_produces_real_markdown_list`
failed once during verification because `interpret` classified the exact
same message as `expand` instead of `note` on that run, routing through
prose generation instead of the list-formatting path the test checks.
Re-running 3/3 passed. This is the same "live-model non-determinism"
class of issue as 1.2, surfacing in a non-DeepEval live test too.

**Proposed fix:** either broaden the test's acceptance criteria to accept
either mode's valid output shape (similar to how
`test_implausible_count_request_never_inserts_the_command_itself` already
does this), or retry once on failure before asserting. Not yet actioned.

---

## 2. Retrieval / grounding issues

### 2.1 `_ground()` used a single retrieval shape for two different question types — FIXED
Previously, `_ground()` always ran `blog_graph.query()` (semantic
fact-search over per-article episodes) regardless of question type.
Corpus-meta questions ("summarize my last 3 blogs," "what have I written
about") have no matching fact edge in the graph, so this returned nothing
useful, and the model correctly (per its own contract) said it couldn't
access the knowledge base — even though it had one.

**Status: fixed.** `TurnPlan.asks_about_knowledge_base` (set by
`interpret`) now additively layers `runs_store.library_items()` (title +
date) on top of the existing semantic search when the question is a
corpus-meta question, rather than branching exclusively — so a
misclassified flag never costs the turn its real fact-search answer.
Verified live end-to-end. *(tasklist.md §1.1 — done)*

### 2.2 `_ground()`'s meta-question classifier is imperfect (accepted, mitigated)
During implementation, `asks_about_knowledge_base` was found to
sometimes fire `True` for genuine content questions too (e.g. "what did
I say about China's AI investment?"), not just true corpus-meta
questions. This was accepted rather than perfected, because the additive
(not exclusive-branch) design in 2.1 makes a false positive harmless —
worst case, a bit of extra unused context. Not a live bug, but the
classifier itself remains imprecise and could misfire in other ways not
yet tested.

**Proposed fix:** none planned currently; monitor for cases where the
extra context actually causes a worse reply (e.g. dilutes focus), not
just wasted retrieval.

### 2.3 No seeded/realistic knowledge-base data in any test harness
Every test that touches `_ground()`/`blog_graph` either skips (no DB),
monkeypatches minimal canned data (as in `tests/test_grounding.py`), or
runs against an empty graph. There's no shared fixture-seeding mechanism
for "a realistic set of ingested articles" that multiple tests could
reuse, which is part of why 1.3 above is unresolved.

**Proposed fix:** build a small, reusable seeding helper (fixture or
factory) once 1.3 is prioritized, rather than one-off monkeypatches per
test file.

---

## 3. Scratchpad content-generation issues

### 3.1 `note` mode verbatim-copied the user's own message — FIXED (formatting)
`_note()` appended `state["user_message"]` (or `note_text`) verbatim into
`body`. For genuine raw-content dumps ("jot this down: X"), this is
correct and intentionally preserved (tested,
`test_note_captures_raw_text_verbatim`). But when the user asked the
system to *generate* content ("add some key points about X"), the model
had no formatting contract and produced run-on prose with inline digits
("1. ... 2. ... 3. ...") that looked like a list but wasn't real markdown
— so it rendered as one paragraph, not a list, in the frontend.

**Status: fixed.** Added an explicit formatting contract to
`interpret_prompt`'s `note_text` field distinguishing "user's own raw
content → capture verbatim" from "system-generated content → must be
real `-`/`1.` list syntax, one item per line." Verified live.
*(tasklist.md §1.2 — done)*

### 3.2 Implausible-quantity requests caused silent content-generation failure — FIXED
"Can you add the top 500 points to the scratchpad about China and AI?"
resulted in the scratchpad body containing a rephrased echo of the
command itself, not real content. Root cause: `interpret` is a single
structured-output classification call also being asked to generate
open-ended content in one field (`note_text`), and it sometimes silently
gave up on large counts — returning `note_text: null` (falling through to
the raw message) or a `note_text` that just restated the request. The
same failure also surfaced through the `expand` path: `_develop()`'s
streaming generation sometimes degenerated to just a `[TK: confirm ...]`
placeholder, which passed the existing empty-string `check_output` check
since it's technically non-empty.

**Status: fixed**, prompt-first + code-second:
- Prompt: `interpret_prompt`'s note_text contract now requires a capped,
  real point list (~8-10) for large counts, never null/echo.
- Code guardrails (`backend/textutil.py`): `looks_like_echo()` (word-
  containment check catching restated requests) and
  `is_placeholder_only()` (catches a `[TK:...]`-only body), wired into
  both `_note()` and `_develop()`.
- Verified: live test flaked ~1/6 runs before the `_develop()` guardrail
  (degenerate placeholder body slipping through); 5/5 clean after.
*(tasklist.md §1.2 — done)*

### 3.3 `body` is still one big markdown string, not structured facts (open)
Beyond formatting, the scratchpad's substantive content still funnels
into a single `body: str` field. `sources`/`open_questions` (already
separate list fields on the `Scratchpad` model) are barely populated
except by `grounding_notes()`'s after-the-fact QA pass and `brainstorm`'s
angle/outline output — `note`/`expand`/`tighten` don't route facts into
them as they go.

**Proposed fix (multi-step, still open):**
1. Route generated content (not raw dumps) through an extraction step
   that splits concrete facts into `sources`, speculative bits into
   `open_questions`, rather than leaving everything as prose in `body`.
2. Establish a bullet-structured Markdown convention for `body` (nested
   `- point / - subpoint (fact) / - subpoint (open question)`) across
   `EXPAND_SYSTEM_PROMPT`/`TIGHTEN_SYSTEM_PROMPT`/the new extraction
   prompt.
3. Make `_expand`/`_tighten`/`_note` populate `sources`/`open_questions`
   consistently, not just via the after-the-fact grounding-notes pass.
4. (Optional) promote `sources: list[str]` → `list[{fact, source_article,
   confirmed_at}]`, mirroring the existing `grounded_sources` dict shape.
5. Verify the frontend needs no change (nested bullets already render via
   `MarkdownPreview`/`remark-gfm`) — explicit regression check once
   backend changes land.
6. Update `test_recorded_conversations.py`/`test_backend.py`'s
   `note_verbatim_append` mechanical assertion, since step 1 changes what
   "verbatim" means for the generated-content case.
*(tasklist.md §1.2, "Planned changes (still open)" — not yet started)*

### 3.4 Confirmation message is a fixed, uninformative template (open, contested)
`_develop()`'s chat bubble ("Developed the scratchpad (N words).") never
says what was actually added — this is *why* issue 1.1 exists, but also a
product-facing limitation on its own: the user gets no signal about what
changed without opening the scratchpad panel.

**Proposed fix:** make the confirmation message name what was added.
Explicitly **not a quiet prompt edit** — this directly contradicts
`SCRATCHPAD_SYSTEM_PROMPT`'s deliberate "keep chat replies short, the
scratchpad carries the work" principle, so it needs an explicit product
decision before implementing, not just an engineering fix.
*(tasklist.md §2, item 2 — backlog, deprioritized)*

---

## 4. UI / product-behavior issues (not yet automatable)

### 4.1 Workspace UI (scratchpad/derived tabs) may change on a plain chat turn
User feedback (fixture turn 6): "it changed the UI elements, the UI
should not do anything unless the scratchpad — blog outline etc are being
updated. these should be purely based on user action." Neither test layer
can check this — `DeepEval` only judges reply text, not UI/tab state.

**Proposed fix:** needs a browser-level (e.g. Playwright) test or manual
verification; not built yet. No engineering fix proposed until the test
gap is closed enough to verify one.

---

## 5. Guardrail coverage gaps

### 5.1 New code-level guardrails (3.2's fix) aren't reflected in `backend/guardrails.json`
`looks_like_echo()` and `is_placeholder_only()` are real runtime checks
now (in `backend/textutil.py`, wired into `backend/app.py`), but
`guardrails.json`'s declarative `rules` block (which currently documents
things like `use_only_user_provided_facts`, `ask_at_most_one_question_per_turn`)
doesn't mention them. Not a functional bug — the checks run regardless —
but it's a documentation/consistency gap: `guardrails.json` is meant to
be the readable index of policy, and it's now incomplete.

**Proposed fix:** add descriptive entries for the new guardrails to
`guardrails.json`'s `rules` block for consistency, even though (like the
existing entries) they'd be documentation rather than the enforcement
point itself.

### 5.2 `check_output`'s empty-string check has known blind spots (partially addressed)
`check_output()` only catches a fully empty/whitespace `body`. Before
3.2's fix, a `[TK: confirm ...]`-only body passed this check since it's
technically non-empty. `is_placeholder_only()` now catches that specific
shape when wired in via `_develop()`, but `check_output()` itself is
still just an empty-string check — the placeholder-detection logic lives
only in `_develop()`/`_note()`'s call sites, not in `policy.py` itself.

**Proposed fix (optional, low priority):** consider moving
`is_placeholder_only()` into `check_output()` directly so any future
caller of `check_output()` gets the stronger check automatically, instead
of relying on each call site to remember to add it separately.

### 5.3 No guardrail against unreasonable list/content-length requests in general
3.2's fix is scoped to the specific "note/expand generation gives up"
failure mode. There's no general-purpose guardrail (deterministic or
prompt-level) for other unreasonable requests that might cause similar
silent degeneration elsewhere (e.g. a skill build asked for an
implausible amount of content, or `brainstorm` asked for "50 angles").

**Proposed fix:** if this recurs in another node, generalize
`looks_like_echo`/`is_placeholder_only` into a shared "did generation
actually produce something real" check applied more broadly, rather than
re-solving it per node as it's discovered.

---

## Cross-reference: what's already tracked in `docs/tasklist.md`

| Issue here | tasklist.md location | Status |
|---|---|---|
| 2.1 | §1.1 | Done |
| 3.1 | §1.2 "Done" | Done |
| 3.2 | §1.2 "Done (2)" | Done |
| 3.3 | §1.2 "Planned changes" | Open |
| 3.4 | §2, item 2 | Backlog |
| 1.1 | §2, item 1 | Backlog |
| 1.2 | §2, item 5 | Backlog |
| 1.3 | §2, item 3 | Backlog |
| 1.4 | §2, item 4 | Backlog |

Issues 1.5, 2.2, 2.3, 4.1, and all of §5 are new — not yet in
`docs/tasklist.md`. Worth folding in during the next planning pass.

