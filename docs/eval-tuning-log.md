# Eval tuning loop — 2c0a0d76 fixture (turns 4, 5, 6)

Each iteration: think from first principles about the cause → make ONE change
(system prompts first) → run `tests/eval_iterations/run_eval.py` (all 3
DeepEval cases, live OpenRouter model, threshold 0.7) → record before/after.
Raw score+reason JSON for every iteration lives in `tests/eval_iterations/`.

**Caveat established at baseline**: the judge is not deterministic run-to-run
on the same code — see iteration 0. Treat single-run score deltas near the
threshold as noisy; only a clear, repeated swing is trusted as a real effect.

---

## Iteration 0 — baseline (no change)

**Result** (`iteration_00_baseline.json`):

| turn | score | pass @0.7 |
|---|---|---|
| 4 | 0.3 | ❌ |
| 5 | 0.7 | ✅ (borderline) |
| 6 | 0.8 | ✅ |

Turn 4 fails clearly: asked to "build 2 blogs in the scratchpad," the
assistant still returns the skill-menu clarifying question with nothing
added. Turns 5 and 6 pass in this particular run, but both are close to the
line — a prior run (see earlier conversation) had turn 5 at 0.4 and turn 6 at
0.6, for the same code. That's judge noise, not a code difference — logging
it so a future "it got worse" reading isn't over-trusted from one sample.

**Root-cause read, first principles:**

- **Turns 4 & 5** are the same bug: `interpret_prompt`'s mode-selection rules
  route on the presence of words like "build" / "generate" / "outline"
  without checking scope. Both real messages explicitly scope the request to
  the scratchpad ("build 2 blogs **in the scratchpad**", "generate outlines
  ... **put it to the scratchpad**") — that phrasing is the signal the user
  wants the body developed, not the separate derived artifact a skill
  produces. This is a pure prompt-wording gap: the classifier has never been
  told that scoping language overrides the build-sounding verb. Fixable by
  editing `interpret_prompt` in `backend/prompts.py`.

- **Turn 6** is structurally different and NOT fixable by prompt wording
  alone: `_ground` (`backend/app.py`) queries the knowledge graph with the
  raw user message, and in this test harness (`isolated_state` fixture) the
  synthetic eval user has zero ingested documents — `runs_store.ingested_count()`
  is 0, so `grounding` is always `None`/empty, and `chat_reply`
  (`backend/capabilities/writing.py`) then omits `knowledge_base_facts` from
  the payload entirely. No wording in `CHAT_SYSTEM_PROMPT` /
  `KNOWLEDGE_BASE_FACTS_TEXT_CONTRACT` can make the model cite facts that
  were never retrieved because none exist in this environment. This will be
  left alone for now (see "Not attempted" below) rather than spending loop
  iterations on something the harness itself blocks.

**Plan:** iterations 1+ target turns 4/5 via `interpret_prompt` only.

---

## Iteration 1 — `interpret_prompt`: scratchpad-scoping rule

**Change** (`backend/prompts.py`, `interpret_prompt`'s "Choosing mode" section):
added an explicit rule that build-sounding verbs ("build", "generate",
"create") do NOT imply `mode: build` when the message also names the
scratchpad as the destination ("build 2 blogs **in the scratchpad**",
"generate outlines ... **put it to the scratchpad**") — that phrasing means
develop the body (`expand`), not produce the separate skill artifact. Before
this, the rule only pattern-matched on the presence of build-ish verbs and
ignored destination scoping entirely.

**Result**, run once (`iteration_01.json`) then repeated with zero further
changes to isolate judge noise from a real effect (`iteration_01_repeat.json`):

| turn | baseline | iter 1 | iter 1 repeat | actual reply changed? |
|---|---|---|---|---|
| 4 | 0.3 ❌ (skill-menu deflection, nothing added) | 0.7 ✅ | 0.5 ❌ | **yes** — both iter-1 runs say "Developed the scratchpad (N words). Flagged 5 things to confirm." instead of the skill menu |
| 5 | 0.7 ✅ *(reply actually built a premature blog outline — the exact bug — yet judge passed it)* | 0.3 ❌ | 0.6 ❌ | **yes** — both iter-1 runs say "Developed the scratchpad (N words)..." instead of "Built a blog outline from v4..." |
| 6 | 0.8 ✅ | 0.8 ✅ | 0.7 ✅ | no (unrelated to this change, as expected) |

**Reading this honestly:** the classification bug is fixed and confirmed
twice — turns 4 and 5 now consistently route to `expand` and develop the
scratchpad body instead of either deflecting to a skill menu or building a
premature artifact. That's the exact behavior the fixture's hand-authored
`expected` block already asked for (`mode: expand`, `scratchpad_changed:
true`, `derived_unchanged: true` for turn 5).

But the DeepEval score does not track this reliably: turn 5's baseline reply
*reproduced the reported bug* (built the outline) and still scored 0.7
(pass), while both post-fix replies — which do NOT reproduce the bug — scored
0.3 and 0.6 (fail). Score moved the *wrong direction* relative to the actual
fix. That ruled out "just keep tuning the prompt" as the next step, so I read
the actual code path before iterating again rather than guessing from more
runs.

**Root cause of the remaining gap (verified by reading code, not inferred):**
`_develop()` in `backend/app.py` builds the chat bubble with a hardcoded
Python f-string:
```python
verb = "Developed" if node == "expand" else "Tightened"
message = f"{verb} the scratchpad ({word_count(body)} words)."
```
This string has **zero LLM/prompt involvement** — no system prompt, no
`reply_gist`, nothing. It is pure template code. DeepEval's test only ever
judges `result["assistant_message"]` (this bubble) — never the scratchpad
`body`, which is where the real, now-correctly-developed content actually
lands (and where the UI shows it to the user). This is also exactly what
`SCRATCHPAD_SYSTEM_PROMPT` deliberately asks for: *"Keep chat replies
short... The scratchpad carries the work, not your reply."*

**Conclusion: stopping the prompt-only loop here, not running iterations 2–5.**
Continuing to edit system prompts cannot change a string that no prompt
touches — that's not a hypothesis to test further, it's readable directly in
the code. The two ways to actually close this gap are both outside "system
prompts only" and outside what I should decide unilaterally:
1. **Fix the test**, not the product: have
   `test_recorded_conversations_deepeval.py` include the scratchpad body (or
   a diff of it) in what the judge sees for `expand`/`note`/`tighten` turns,
   not just the chat bubble — so the judge is evaluating the thing that
   actually changed.
2. **Fix the product**, not the test: make the confirmation message less
   templated/more descriptive of what was added — this directly contradicts
   the "keep chat replies short, scratchpad carries the work" principle that
   was a deliberate design choice, not an oversight, so it needs a product
   call, not a silent prompt edit.

Turn 6 remains separately blocked by the harness-data gap noted at baseline
(no ingested content for the synthetic eval user → `grounding` is always
empty in this environment) — a third, independent case of "the test can't
see the fix," not a fourth prompt-tuning target.

**Net effect of this session's one real change:** the actual bug behind
turns 4 and 5 (misrouting content-development requests into either a
skill-menu dead-end or a premature artifact build) is fixed and verified
twice against the live model. The `interpret_prompt` edit is kept — it is
a legitimate improvement independent of the eval score noise.

---
