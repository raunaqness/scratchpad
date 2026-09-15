# Signal — Task List

Status: living backlog, written after the scratchpad mode-fix / eval-gap
report (see `docs/report-scratchpad-mode-fix-and-eval-gap.md` and
`docs/eval-tuning-log.md`) plus a follow-up review of the scratchpad's
verbatim-copy behavior and a live knowledge-base retrieval bug. Section 1
is the current priority; Sections 2-3 are explicitly deprioritized to the
backlog per product decision.

---

## Section 1 — High priority: product/retrieval fixes

### 1.1 Knowledge-base meta-question retrieval bug (new — confirmed live)

`_ground()` (`backend/app.py`) always queries `blog_graph.query()`
(Graphiti semantic fact-search over per-article episodes) with the raw
user message, for every chat turn. This is the wrong retrieval shape for
corpus-meta questions ("summarize my last 3 blogs", "what are your
sources for the blogs you've generated") — Graphiti returns individual
fact-edges ranked by similarity to the question text, and there is no
fact edge representing "this is one of your 3 most recent posts." When
nothing relevant comes back, `grounding` is empty, `chat_reply()` omits
`knowledge_base_facts` from the payload entirely (not empty — absent),
and `KNOWLEDGE_BASE_FACTS_TEXT_CONTRACT` (`backend/prompts.py`)
explicitly licenses "say you can't access the knowledge base" only when
the key is absent — so the model correctly (per its own contract) claims
it can't access blogs it actually has.

Confirmed live: "give me a summary of the last three blogs I've written"
→ "I can't summarize your blogs directly, but I'd be happy to help you
outline or analyze them if you provided some details or key facts from
each post." — even though the blogs are already ingested.

**Fix:**
- Extend `TurnPlan`/`interpret_prompt` (`backend/signal_models.py`,
  `backend/prompts.py`) to flag corpus-meta questions (e.g.
  `asks_about_knowledge_base: bool`), distinct from content questions.
- `_ground()` branches: for corpus-meta questions, pull from
  `runs_store.library_items(user_id)` (title + published_at, already
  sorted newest-first — currently only used by the ingest library UI,
  never wired into chat) instead of / in addition to `blog_graph.query()`.
- Keep the existing fact-search path for genuine content questions
  ("what did I say about China's AI investment").
- Same root cause as fixture turn 6's "what are your sources" complaint
  in the original report — one fix should resolve both.

### 1.2 Scratchpad structure — stop verbatim copy-paste, extract facts

Current behavior: `_note()` (`backend/app.py`) literally appends
`state["user_message"]` verbatim into `scratch.body`
(`f"{scratch.body}\n\n{text}".strip()`). `_expand`/`_tighten` produce
bigger prose paragraphs via LLM but still just write one big markdown
string into the same `body` field. `sources`/`open_questions` (already
separate list fields on the `Scratchpad` model, `backend/artifact.py`)
are barely populated except by `grounding_notes()`'s after-the-fact QA
pass and `brainstorm`'s angle/outline output.

**Done:** fixed a concrete live bug found while starting this work —
asking the system to *generate* multiple points ("add some key points
about why China is giving AI models for free") produced `note_text` as
one run-on sentence with inline "1. 2. 3." digits (not real markdown list
syntax), which correctly rendered as a single paragraph, not a list — a
content-formatting bug, not a frontend rendering bug. Fixed by adding an
explicit formatting contract to `interpret_prompt`'s `note_text` field
(`backend/prompts.py`): verbatim-raw-dump note requests still get
`note_text: null` (unchanged, still captured word-for-word — this was
already a deliberately tested behavior, see
`test_note_captures_raw_text_verbatim`); system-generated multi-point
content must use real `-`/`1.` list syntax, one item per line. Verified
against the live model on the exact reported message (before: run-on
sentence; after: real numbered markdown list) — see
`tests/test_note_formatting_live.py` (live, structural check, no
DeepEval judge needed) and `tests/test_backend.py::
test_note_uses_generated_note_text_when_the_user_asked_for_new_content`
(offline, pins the `note_text`-wins-over-raw-message plumbing).

**Done (2):** fixed a second, related live bug — an implausible quantity
request ("Can you add the top 500 points to the scratchpad about China
and AI?") resulted in the scratchpad body containing a rephrased echo of
the command itself ("Add the top 500 points about China and AI to the
scratchpad."), not real content. Root cause: `interpret` is a single
structured-output classification call also being asked to generate
open-ended content in `note_text`, and it sometimes silently gives up on
large counts — either returning `note_text: null` (falling through to
the raw message) or a `note_text` that just restates the request. This
confirmed the position from `docs/report-scratchpad-mode-fix-and-eval-gap.md`:
prompt wording alone lowers the failure rate but can't guarantee zero, so
this was fixed prompt-first, code-second:
- **Prompt** (`interpret_prompt`'s note_text contract, `backend/prompts.py`):
  added an explicit rule that an implausible count must still produce a
  reasonable number of real points (capped ~8-10), never `null` and never
  an echo of the request.
- **Code guardrail** (`backend/textutil.py`: new `looks_like_echo()` and
  `is_placeholder_only()`; wired into both `_note()` AND `_develop()`
  in `backend/app.py`, since the same failure surfaced through the
  `expand` path too — `interpret` sometimes routes "add N points" to
  `mode: expand` instead of `note`, and `_develop()`'s streaming
  generation degenerated to just a `[TK: confirm ...]` placeholder for
  the same implausible-count case, which passed the existing
  `check_output` empty-string check since it's technically non-empty).
  Both nodes now detect an echoed/placeholder-only result and reply
  asking for a smaller number instead of writing it to the scratchpad.
- Verified: live test flaked at ~1/6 runs before the `_develop()` fix
  (degenerate `[TK: confirm ...]`-only body slipping past `check_output`);
  5/5 clean after. See `tests/test_note_formatting_live.py::
  test_implausible_count_request_never_inserts_the_command_itself` (live)
  and `tests/test_backend.py::test_note_asks_for_a_smaller_number_...`,
  `test_note_catches_note_text_that_just_echoes_the_request`,
  `test_note_verbatim_dump_is_unaffected_by_the_generation_guardrail`,
  `test_looks_like_echo` (offline).

**Planned changes (still open):**
1. `_note()` stops raw-appending the user message *for the generated-
   content case* (the verbatim-dump case is intentionally preserved, see
   above). Go further than formatting: route generated content through
   an extraction step that also splits concrete facts into `sources`,
   speculative/unconfirmed bits into `open_questions`, rather than
   leaving everything as prose in `body`. Needs a new prompt
   (`backend/prompts.py`) and possibly a `TurnPlan` field for extracted
   facts (`backend/signal_models.py`).
2. Establish a bullet-structured Markdown convention for `body` (nested
   `- point / - subpoint (fact) / - subpoint (open question)`) instead
   of prose paragraphs — update `EXPAND_SYSTEM_PROMPT`,
   `TIGHTEN_SYSTEM_PROMPT`, and the new note-extraction prompt to
   produce this shape consistently.
3. Make `_expand`/`_tighten`/`_note` populate `sources`/`open_questions`
   as they go, not just via the after-the-fact grounding-notes pass —
   audit `backend/app.py` and `backend/capabilities/writing.py` for
   where facts surface.
4. (Optional follow-on, only if step 2 isn't expressive enough) Promote
   `sources: list[str]` → `list[{fact, source_article, confirmed_at}]`,
   mirroring the existing `grounded_sources` dict shape. Touches
   `backend/artifact.py` (model), `backend/versions.py`
   (`_COMPARE_KEYS`/`_ITEM_KEYS`), `apply_client_edits`, and frontend
   `page.tsx` types.
5. Verify frontend needs no change for steps 1-3 — nested bullets already
   render via the existing `MarkdownPreview` (`@assistant-ui/react` +
   `remark-gfm`, see `frontend/components/markdown-preview.tsx`).
   Explicit regression check once backend changes land, since the whole
   plan leans on this assumption.
6. Update `tests/test_recorded_conversations.py` / `tests/test_backend.py`
   mechanical assertions (e.g. the `note_verbatim_append` expectation) —
   step 1 deliberately breaks the current verbatim-append guarantee.
   Needs a decision on the new mechanical check (e.g. "fact appears in
   `sources`, not verbatim in `body`").

---

## Section 2 — Backlog: eval blind spot & mode-fix follow-ups

(Deprioritized per product decision; content unchanged from the original
report — see `docs/report-scratchpad-mode-fix-and-eval-gap.md`.)

1. **DeepEval judge should see the scratchpad body diff, not just the chat
   bubble.** `_develop()`'s confirmation message is a hardcoded template
   string (`f"{verb} the scratchpad ({word_count(body)} words)."`) with
   zero LLM/prompt involvement, so
   `tests/test_recorded_conversations_deepeval.py` is structurally blind
   to the actual content-development fix for any
   `note`/`expand`/`tighten`/`brainstorm` turn. Fix: append a diff of
   `scratchpad.body` (before → after) to what's judged, or split into two
   metrics (reply tone vs. content correctness).
2. **Confirmation-message verbosity.** Whether `_develop()`'s reply should
   say more than a word count. Directly contradicts the deliberate "keep
   chat replies short" principle in `SCRATCHPAD_SYSTEM_PROMPT` — needs a
   product decision, not a quiet prompt edit.
3. **Test harness seeding for knowledge-graph facts (fixture turn 6).**
   `isolated_state` (`tests/test_recorded_conversations_deepeval.py`)
   points at an empty tmp DB, so `_ground()`'s
   `ingested_count(user_id) > 0` gate is always false for the synthetic
   eval user; turn 6's `cites_knowledge_base_facts` expectation is
   untestable, structurally — not flaky, not low-scoring, unable to ever
   pass or fail. Fix: monkeypatch `blog_graph.query` /
   `runs_store.ingested_count` in the eval fixture to return canned facts
   matching the fixture's topic.
4. **Cheaper classification-only live check for turns 4/5.** Once the
   mode fix is confirmed, the remaining risk is narrower ("does
   `interpret` keep classifying scratchpad-scoped requests as
   `expand`"); consider a live-model, structured-output-only test
   asserting `plan.mode` directly instead of a full conversational GEval.
5. **Multi-run judge averaging for threshold noise.** Raw score variance
   between two identical-code runs was large (turn 4: 0.7 vs 0.5; turn 5:
   0.3 vs 0.6). Worth averaging N judge calls per case before asserting
   pass/fail near the 0.7 threshold.

---

## Section 3 — Backlog: new DeepEval multi-turn suites

Blocked on Section 1 landing (no point testing structure that doesn't
exist yet) and on Section 2 item 1 (judge needs to see the body diff to
have any signal).

- `tests/test_note_extraction_deepeval.py` — multi-turn: raw message →
  assert body is NOT a verbatim copy of the user message (mechanical
  check, no judge needed) + a `ConversationalGEval` that the note reads
  as an organized bullet and facts landed in `sources` rather than prose.
- `tests/test_scratchpad_scoping_deepeval.py` — successor to the fixed
  turns 4/5 bug: several "build X in the scratchpad" phrasings across a
  multi-turn conversation, asserting `mode == "expand"` plus a body-diff
  `ConversationalGEval` once Section 2 item 1 lands.
- `tests/test_multiturn_context_deepeval.py` — a longer (5+ turn)
  conversation exercising topic switches, `subject_changed`, and whether
  earlier facts persist/get contradicted — cross-turn memory/consistency
  rather than single-turn correctness.
