# Plan — Multi-agent Scratchpad: creative follow-ups + standalone skill generation

> **Status (2026-09-09):** Stages 1–6 implemented and deployed to dev on
> `feat/thinkpad-redesign` — commits `f2c3f63` (Creative agent) and `34688af`
> (standalone skills). 51 tests pass with a Postgres URL, 44 + 2 skipped without.
> Open decisions A/B/C were taken as the leans (legacy `build` kept, follow-ups
> as a `follow_up` tool call, fixed `kind` set). Not done: the deepeval quality
> rubrics (§9.2), the scrollback "inert buttons" refinement, prod deploy.

> **Branch:** all of this lands on **`feat/thinkpad-redesign`** (which holds the Google-auth BFF, the
> credit system, and the `infra/supabase/` stack). The working tree is currently on `main @ fda9b62`;
> check out `feat/thinkpad-redesign` before starting. Dev deployment only (`docker-compose.test.yml`,
> `dev-scratchpad.raunaqness.com`). Prod (`docker-compose.yml`) is untouched.

---

## 1. Context

Today the backend is a single LangGraph turn: `interpret` classifies the message into one of
`note / expand / tighten / brainstorm / critique / build / chat`, and one node does the work. The
three UI skill buttons ("Generate blog outline" etc.) don't call anything dedicated — they just
`thread.append("Generate a blog outline…")`, which rides the same chat turn and hits `build` mode.

We want to move toward the multi-agent design discussed in this thread, **incrementally**. This pass
does the two highest-value pieces without the full knowledge-graph rewrite:

1. **A Creative / Follow-up Agent** that runs automatically after any message that meaningfully
   changed the scratchpad, reads the *new* state, and proposes 3–5 next moves as **clickable buttons
   in the chat**. Read-only. Clicking a button sends its label verbatim as the next message.
2. **Skills become a standalone generation path** — one endpoint, parameterised by `skill_id`, input =
   the whole scratchpad, output = one finished artifact, higher temperature, its own credit, persisted
   thread-scoped and versioned per type in Postgres.

Everything else about the current turn (`interpret` + the six mode nodes + the short chat reply)
stays as-is this pass. The full entity/provenance knowledge-graph model, and the Artifact Planner,
are explicitly deferred to later passes.

### Already shipped (foundation this builds on)

- **Auth**: Google OAuth in the Next BFF; `forwarded_props.user_id` = Google `sub`; backend
  `_require_proxy` gated by `SIGNAL_SESSION_SECRET`.
- **Credits**: `backend/credits.py` + `backend/db.py` + `backend/migrations/0001_credits.sql` on
  self-hosted Supabase Postgres (`scratchpad` schema: `accounts`, `credit_ledger`, `redeem_codes`).
  `_credit_gate()` in `backend/agent.py` debits 1 credit per `/agent` turn. `GET /api/account`.
  Admin CLI `python -m backend.credits …`. Frontend: `CreditsChip`, `OutOfCreditsNotice`,
  `/api/account` BFF route.
- **Supabase**: `infra/supabase/` — trimmed self-hosted stack (`db`, `studio`, `meta`, `api-gw`,
  `auth`, `rest`), Studio at `http://127.0.0.1:8000`, reachable as `db:5432` on the
  `supabase_default` network which both app backends join.
- **Logging**: `x-logging` anchor (10m × 5) on both compose files.

---

## 2. Target architecture (this pass)

| Agent | Writes scratchpad? | Trigger | Model temp | Job |
|---|---|---|---|---|
| **Scratchpad Agent** *(existing, unchanged this pass)* | **Yes — only one** | every user message | cool (`OPENROUTER_TEMPERATURE`, interpret at 0) | Interpret the message, update the canonical scratchpad, stream a short chat reply. |
| **Creative / Follow-up Agent** *(new)* | No (read-only) | automatically, after a message that changed the scratchpad | hot (`OPENROUTER_TEMPERATURE_CREATIVE`) | Read the new scratchpad; emit 3–5 next-move buttons. |
| **Skill / Artifact Agent** *(existing skills, new path)* | No (read-only) | a skill button → `POST /api/artifacts/generate` | hot | Take the whole scratchpad; generate ONE finished artifact; persist a version. |
| *Artifact Planner* | — | *(later pass)* | — | Deferred. |

Model is `openai/gpt-4o-mini` for everything (already set in `.env`).

### Flow 1 — user types a message (1 credit)

```
user message
  → _credit_gate (debit 1)                       [backend/agent.py, existing]
  → Scratchpad Agent: interpret → mode node → stream reply + scratchpad snapshots
  → NEW: follow_up node
       artifact_changed(prior, current)?         [backend/versions.py:102, existing]
         no  → skip
         yes → emit status "Looking for follow-ups" (visible working state)
               → Creative Agent: follow_ups(scratchpad)   [backend/capabilities/writing.py, new]
               → emit {"type":"follow_ups","items":[…]}
  → agent.py renders items as a `follow_up` tool call (mirrors request_choice) before RUN_FINISHED
```

Clicking a follow-up button → `thread.append(label)` → Flow 1 again → another credit.

### Flow 2 — user clicks "Generate blog outline" (1 credit)

```
button → POST /api/artifacts/generate {thread_id, skill_id}     [new endpoint, NOT a chat turn]
  → _require_proxy
  → ensure_account + try_debit(1, reason="artifact", ref=f"{thread_id}:{skill_id}")   [backend/credits.py, existing]
        insufficient + enforce → 402, no generation, no debit
  → load scratchpad: aget_thread_state(thread_id)              [backend/app.py, existing]
  → Skill Agent: run_skill(skill, full_scratchpad)  streamed   [backend/capabilities/skills.py, existing – widened]
  → artifacts_store.save_artifact(thread_id, skill_id, body)   [backend/artifacts_store.py, new] → version N+1
  → SSE: {"type":"delta",…} … {"type":"done","artifact":{…}}
```

Scratchpad is **not** modified; the Creative Agent does **not** run.

---

## 3. Backend changes

### 3.1 Config & model — `backend/config.py`, `.env`, `.env.example`

- `config.py`: add `openrouter_temperature_creative: float = Field(default=1.0, alias="OPENROUTER_TEMPERATURE_CREATIVE")`.
- `.env` + `.env.example`: add `OPENROUTER_TEMPERATURE_CREATIVE=1.0`. (`OPENROUTER_MODEL` already `openai/gpt-4o-mini`.)
- No change to `backend/llm.py` — `get_chat_model(temperature=…)` already supports per-call temp and caches per `(streaming, temperature, tags)`.

### 3.2 Creative / Follow-up Agent

- **`backend/signal_models.py`** — add:
  - `FollowUp` (`label: str`, `kind: Literal["fact","perspective","tone","angle","direction","question"]`)
  - `FollowUps` (`items: list[FollowUp]`)
- **`backend/prompts.py`** — add `FOLLOWUP_SYSTEM_PROMPT` (full draft in §6).
- **`backend/capabilities/writing.py`** — add:
  ```python
  def follow_ups(scratchpad: dict[str, Any]) -> list[dict[str, Any]]:
      model = get_chat_model(temperature=settings.openrouter_temperature_creative,
                             tags=["signal:followup"])
      structured = model.with_structured_output(FollowUps)
      result = structured.invoke([SystemMessage(FOLLOWUP_SYSTEM_PROMPT),
                                  HumanMessage(render_scratchpad(scratchpad))])
      # normalise → list[{label, kind}], cap 5, drop empties; any error → []
  ```
  Reuses `render_scratchpad()` (already in this file) so the agent sees the same labelled
  plain-text view the ops use.
- **`backend/app.py`**:
  - `_emit_follow_ups(items)` → `_emit({"type": "follow_ups", "items": items})` (mirrors `_emit_choice`).
  - `_interpret` returns `prior_scratchpad` (the pre-turn `prior.model_dump()`) in state so the
    follow-up node can diff.
  - New node `_follow_up(state)`:
    - `if not artifact_changed(state.get("prior_scratchpad"), state.get("scratchpad")): return {}`
    - else: return `{"status": "thinking_followups"}` (drives the working indicator), then
      `_emit_follow_ups(follow_ups(state["scratchpad"]))`. **Never mutates canonical state.**
  - `_PROGRESS_LABELS["follow_up"] = "Looking for follow-ups"`.
  - Graph wiring in `_builder()`: change the `for node in (…): graph.add_edge(node, END)` loop so
    `note / expand / tighten / brainstorm / critique / respond → "follow_up"`, add
    `graph.add_node("follow_up", _follow_up)` and `graph.add_edge("follow_up", END)`. Keep
    `build → END` (no follow-ups after an artifact-only turn).
  - `astream_conversation`: add `"follow_ups"` to the custom-event allow-set that gets `yield`-ed;
    collect items locally; include `follow_ups` in the terminal `{"type": "final", …}`.
- **`backend/agent.py`** (`_signal_events`):
  - New `pending_follow_ups: list | None` alongside `pending_choice`.
  - Handle `kind == "follow_ups"` in the event loop → `pending_follow_ups = event["items"]`.
  - Handle the `status` event with `node == "follow_up"` → emit a progress `StateSnapshotEvent`
    carrying `follow_up_pending: true`.
  - `_follow_up_tool_events(encoder, items, message_id)` — copy of `_choice_tool_events` (agent.py:98)
    emitting `ToolCallStart/Args/End` with `toolCallName="follow_up"` and
    `delta=json.dumps({"items": items})`.
  - Flush `pending_follow_ups` right after the existing `pending_choice` flush, in **both** places
    (the `final`-event branch ~line 469 and the fallthrough ~line 512), before `RunFinishedEvent`.

### 3.3 Skill / Artifact Agent — storage + endpoint

- **`backend/migrations/0002_artifacts.sql`** (new) — applied automatically by the lifespan runner in
  `backend/agent.py` (`_lifespan` → `run_migrations()`):
  ```sql
  CREATE TABLE IF NOT EXISTS scratchpad.artifacts (
      id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
      thread_id  TEXT NOT NULL,
      skill_id   TEXT NOT NULL,
      version    INTEGER NOT NULL,
      body       TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      UNIQUE (thread_id, skill_id, version)
  );
  CREATE INDEX IF NOT EXISTS idx_artifacts_thread_skill
      ON scratchpad.artifacts (thread_id, skill_id, version DESC);
  ```
- **`backend/artifacts_store.py`** (new) — same asyncpg/pool style as `backend/credits.py`
  (uses `backend.db.get_pool`):
  - `save_artifact(thread_id, skill_id, body) -> dict` — `version = COALESCE(MAX(version),0)+1`
    for that `(thread_id, skill_id)`, insert, return the row.
  - `list_artifacts(thread_id) -> list[dict]` — all versions, newest first.
  - `get_artifact(artifact_id) -> dict | None`.
- **`backend/capabilities/skills.py`** — `run_skill(...)`:
  - run at `settings.openrouter_temperature_creative`.
  - feed the **whole** scratchpad: widen `_SNAPSHOT_KEYS` to add `tags`; keep `scratchpad_snapshot`
    as the filter, pass its result to the existing `render_scratchpad()`.
- **`backend/prompts.py`** — revise `_SKILL_BASE` for voice/creativity + "you get the whole
  scratchpad / one-shot / no chat / no mutation" (full draft in §6). SKILL.md craft-note files
  (`backend/skills/<id>/SKILL.md`) are left as-is this pass (review targets — see §7).
- **`backend/agent.py`** — new routes (both behind `_require_proxy`):
  - `POST /api/artifacts/generate` — body `{thread_id, skill_id}` + BFF-injected
    `user_id`/`user_email`/`user_name`. Resolve identity → if billable & `credits_active`:
    `credits.ensure_account` + `credits.try_debit(1, reason="artifact", ref=f"{thread_id}:{skill_id}")`;
    `None` + `credits_enforce` → `JSONResponse(402, {"error":"insufficient_credits"})`.
    Unknown `skill_id` → 404. Load `aget_thread_state(thread_id)`. `StreamingResponse` of
    `run_skill(...)` as `data: {"type":"delta","text":…}` per chunk, then
    `artifacts_store.save_artifact(...)`, then `data: {"type":"done","artifact":{…}}`.
  - `GET /api/artifacts?thread_id=` → `{"artifacts": [...]}`.
- The in-graph **`build` mode stays as a legacy path** (typed "turn this into a blog post" still
  works, still writes graph `derived` state). Accepted redundancy for this pass — see §8-A.

---

## 4. Frontend changes (`frontend/`)

- **`frontend/app/MyRuntimeProvider.tsx`** — wrap children in a new `ThreadIdContext.Provider`
  exposing `currentThreadId` so `SkillBar` / `WorkspacePanel` can address the thread.
- **`frontend/app/api/artifacts/generate/route.ts`** (new BFF) — `POST`, `runtime="nodejs"`,
  `dynamic="force-dynamic"`. Inject verified identity into the body + `x-signal-proxy-secret`
  header, proxy the SSE stream straight back (same shape as `frontend/app/api/agent/route.ts`).
  `401` when unauthenticated.
- **`frontend/app/api/artifacts/route.ts`** (new BFF) — `GET` → backend `/api/artifacts?thread_id=`.
- **`frontend/app/app/page.tsx`**:
  - `defineToolkit`: add `follow_up` → `FollowUpButtons` component. Renders `props.args.items` as a
    labelled block of buttons; click → `aui.thread.append(item.label)`. Buttons on any turn that
    isn't the latest render disabled (scrollback = inert).
  - `SignalState` type: add `follow_up_pending?: boolean`.
  - `FollowUpWorking`: a small "✨ looking for follow-ups…" line in `.app-chat` shown while
    `state.progress?.node === "follow_up"` / `follow_up_pending`.
  - `SkillBar`: buttons switch from `aui.thread.append(skill.prompt)` to
    `generateArtifact(skillId)` → `fetch("/api/artifacts/generate", {method:"POST", body:{thread_id, skill_id}})`,
    read the SSE, stream into local `generatedArtifacts` state, then `GET /api/artifacts` to hydrate.
  - `WorkspacePanel`: render tabs from `/api/artifacts` grouped by `skill_id` with a per-type
    **version selector**; live streaming preview during generation. `state.derived` from `build`
    mode still rendered as a fallback tab.
- **`frontend/app/globals.css`** — `.follow-up-card`, `.follow-up-btn`, `.follow-up-btn:disabled`,
  `.follow-up-working`, `.artifact-version-select`.

---

## 5. Credits — invariant

**1 user message = 1 credit**, regardless of how many agents run inside the turn (Scratchpad +
Creative both run under one `_credit_gate` debit). **1 artifact generation = 1 credit** (its own
`try_debit` in the new endpoint). No change to `backend/credits.py` logic; just a new `reason`
value `"artifact"` in the ledger.

---

## 6. System prompts — full drafts (review these)

### 6.1 Creative / Follow-up Agent — `FOLLOWUP_SYSTEM_PROMPT` (new, `backend/prompts.py`)

```text
# Role
You are the Follow-up Agent for Scratchpad — a fast, imaginative thinking partner that runs the
instant the canonical scratchpad has been updated.

You look at the scratchpad exactly as it stands right now and propose the 3–5 most useful *next
moves* the user could make. Each move is shown to the user as a button; its label is sent verbatim
as the user's next message if they click it.

You never modify the scratchpad. Your entire output is a short list of proposed messages.

# What makes a good follow-up
- It moves the thinking forward. It opens a door the user has not walked through yet — a sharper
  angle, a missing piece the eventual artifact will need, a stakeholder perspective they are not
  holding, a scope decision they are circling, a tension worth naming out loud.
- It is specific to THIS scratchpad. If the same suggestion would fit any project, it is too
  generic — cut it.
- It reads as a natural thing this user would type. Write it in their voice — first person or plain
  imperative: "Add that our buyers are technical founders", "Reframe this around switching cost, not
  features", "Make the tone blunter and less corporate", "What breaks if we sell to enterprise
  instead?".
- It is one move, not a paragraph. 4–14 words. No preamble, no "You could consider…".

# Hard rules
- Read the scratchpad's body, sources, angles, outline, and open_questions first. Do NOT restate,
  rephrase, or lightly extend anything already there. Suggest only what is absent.
- Never invent facts. A "fact" suggestion names the gap and asks the user to supply it ("Add the
  real cold-start latency if you have a number"). It never asserts a number, price, date, name, or
  result as true.
- Do not write the blog post / social post / campaign, or any fragment of finished copy. That is a
  different agent. You surface thinking moves only.
- Do not answer your own questions or resolve your own suggestions.
- Do not pad to reach five. Three excellent moves beat five weak ones.

# Spread
Across the 3–5 items, aim for a mix of kinds — never more than two of the same kind:
- fact        — a concrete detail the artifact will need and the scratchpad lacks
- perspective — a stakeholder lens or counter-view the user is not currently holding
- tone        — a deliberate voice or stance choice for the eventual artifact
- angle       — a sharper, more surprising way into the same material
- direction   — a scope or strategy fork worth deciding now
- question    — a provoking question that would change the work if answered

# Output
Return JSON only:
{"items": [{"label": "<the exact message text>", "kind": "fact|perspective|tone|angle|direction|question"}, ...]}
3 to 5 items. Nothing outside the JSON.
```

### 6.2 Skill / Artifact Agent — revised `_SKILL_BASE` (`backend/prompts.py`)

```text
# Role
You are the **{name}** skill — a specialist writer with exactly one job: turn the user's scratchpad
into ONE finished {name_lower}, ready to use as-is.

You are given the entire scratchpad: everything the user knows, believes, has decided, and wants.
You produce the artifact and nothing else. You do not chat, you do not ask questions, and you do
not change the scratchpad.

# Craft
{craft}

# Voice
This is the finished piece, not a draft and not notes. Commit to a point of view. Write with rhythm
and confidence. Cut anything that reads as hedged, templated, or corporate. A reader should not be
able to tell it was assembled from bullet points.

# Grounding
{grounding}
Where the scratchpad is genuinely missing something the {name_lower} needs, write around it or use a
short bracketed placeholder — never a fabricated specific.

# Output
Return only the {name_lower} as plain markdown. No preamble, no explanation of your choices, no code
fence, no JSON or {{...}} object.
```

`{craft}` = the body of `backend/skills/<skill_id>/SKILL.md`; `{grounding}` = `GROUNDING_CONTRACT`.

### 6.3 Scratchpad Agent — **unchanged this pass**

Lives in `backend/prompts.py`: `SCRATCHPAD_SYSTEM_PROMPT` (identity, used by `interpret` + as the
base of `CHAT_SYSTEM_PROMPT`), `interpret_prompt(...)` (the turn-router user message), and the
per-mode prompts `BRAINSTORM_/EXPAND_/TIGHTEN_/CRITIQUE_SYSTEM_PROMPT`. A future pass hardens these
into the deterministic knowledge-extraction agent from the design doc.

### 6.4 Artifact Planner — **not built**

When added: `ARTIFACT_PLANNER_SYSTEM_PROMPT` in `backend/prompts.py` + artifact specs in
`backend/skills/<id>/SKILL.md` frontmatter or a new `backend/skills/<id>/spec.json`.

---

## 7. Prompt location index (hand this back after implementation)

| Agent / job | Constant / file | Path |
|---|---|---|
| Scratchpad identity | `SCRATCHPAD_SYSTEM_PROMPT` | `backend/prompts.py` |
| Turn router (user msg) | `interpret_prompt()` | `backend/prompts.py` |
| Shared grounding rules | `GROUNDING_CONTRACT` | `backend/prompts.py` |
| Scratchpad ops | `BRAINSTORM_/EXPAND_/TIGHTEN_/CRITIQUE_SYSTEM_PROMPT` | `backend/prompts.py` |
| Grounding check | `GROUNDING_SYSTEM_PROMPT` | `backend/prompts.py` |
| Chat reply | `CHAT_SYSTEM_PROMPT` | `backend/prompts.py` |
| Rolling summary | inline string in `_rollup()` | `backend/app.py` |
| **Creative / Follow-up** | **`FOLLOWUP_SYSTEM_PROMPT`** *(new)* | `backend/prompts.py` |
| **Skill base** | **`_SKILL_BASE` + `skill_system_prompt()`** *(revised)* | `backend/prompts.py` |
| Per-skill craft notes | `SKILL.md` bodies | `backend/skills/blog_outline/`, `social_post/`, `marketing_campaign/` |
| Op craft notes | `SKILL.md` bodies | `backend/skills/brainstorming/`, `grounded-editing/`, `draft-validation/` |
| Canned replies | `PUBLISH_REPLY`, `DISALLOWED_REPLY`, `skill_menu_reply()` | `backend/prompts.py` |

---

## 8. Open decisions (flag before / during implementation)

- **A. `build` mode.** Keep it as a legacy chat path (writes graph `derived` state, *not* Postgres) —
  two artifact code paths for now, unify later? **Lean: keep both this pass.**
- **B. Follow-up transport.** Render as a `follow_up` tool call inline in the thread (mirrors the
  existing `request_choice` pattern) vs. a dedicated snapshot field + pinned component.
  **Lean: tool call.**
- **C. `kind` taxonomy.** Fixed set (`fact | perspective | tone | angle | direction | question`,
  drives a small icon) vs. free label only. **Lean: fixed set.**

---

## 9. Testing & evals

### 9.1 Deterministic (pytest, offline `FakeModelScript` — `tests/conftest.py`)

- **`tests/conftest.py`**: `FakeModelScript` gains a `signal:followup` entry returning a canned
  `FollowUps`; handle `with_structured_output(FollowUps)`.
- **`tests/test_backend.py`** (extend): `follow_up` node skipped when `artifact_changed()` is false
  (greeting / chat-only); runs on `note`/`expand`/etc.; `build` turn does not trigger it; a thrown
  error in `follow_ups()` still reaches `RUN_FINISHED`; ≤5 items.
- **`tests/test_artifacts.py`** (new, skipped without a reachable `SIGNAL_TEST_DATABASE_URL` — same
  guard as `tests/test_credits.py`): `save_artifact` version increments per `(thread_id, skill_id)`
  independently per type; `list_artifacts` ordering.
- **`tests/test_credits.py`** (extend): an `artifact` debit decrements the balance; `insufficient` +
  enforce blocks generation and writes **no** artifact row and **no** ledger row; `_require_proxy`
  → 403 without the secret; unknown `skill_id` → 404.
- **Regression**: full deterministic suite (`pytest tests/ --ignore=…deepeval… --ignore=test_conversations.py`)
  green after every stage; `tsc --noEmit` + `next build` clean.

### 9.2 LLM evals (deepeval, real model, opt-in — kept out of the fast suite like the existing `*_deepeval.py`)

~4 fixture scratchpads (thin / medium / rich; one exploratory, one existing-product) reused across both agents.

- **Creative agent** (`tests/test_followups_deepeval.py`, new) — `GEval` rubric: each item
  introduces something not already in the scratchpad; not generic; reads as a sendable user
  message; count 3–5.
- **Skill agents** (`tests/test_skills_deepeval.py`, extend) — grounding (no invented
  specifics; `[TK]`/`[assumption]` not silently resolved); shape (blog = title + lede + sections;
  social = 80–220 words, hook first line, ≤3 hashtags; campaign = the six sections); claims trace
  to the scratchpad.

### 9.3 Manual on dev (curl + browser)

1. `docker compose -f docker-compose.test.yml up -d --build` — backend runs `0002` on start
   (check logs for `db migrations applied: 0002_artifacts.sql`).
2. Raw SSE: a `note` turn shows the `follow_up` tool call + the "Looking for follow-ups" status; a
   chat-only turn shows neither.
3. `curl -XPOST localhost:8002/api/artifacts/generate -H 'x-signal-proxy-secret: …' -d '{"thread_id":"…","skill_id":"blog_outline","user_id":"…"}'`
   → SSE `delta`… `done`; a `scratchpad.artifacts` row; a `-1 artifact` ledger row; second run → version 2;
   `GET /api/artifacts?thread_id=` lists both.
4. Drain via `python -m backend.credits grant --email … --amount -<balance>` → generate returns 402,
   no row, no debit.
5. Browser (sign in on `dev-scratchpad.raunaqness.com`): follow-up buttons render inline, a click
   sends the message and runs a new turn; a skill button streams an artifact into a versioned tab;
   the credits chip ticks on both.
6. Clean test rows (`DELETE … WHERE thread_id LIKE 'test%'`), then commit.

---

## 10. Stage order

1. **Config** (§3.1) — trivial, unblocks the rest.
2. **Creative agent backend** (§3.2) — signal_models, prompt, capability, node, wiring, agent.py
   tool events. Deploy dev, verify raw SSE (§9.3 step 2).
3. **Creative agent frontend** (§4 — toolkit + working indicator + CSS). Deploy, verify in browser.
4. **Artifact storage + endpoint** (§3.3) — migration, `artifacts_store.py`, `run_skill` widen,
   `_SKILL_BASE`, routes. Deploy, verify via curl (§9.3 steps 3–4).
5. **Artifact frontend** (§4 — `ThreadIdContext`, BFF routes, `SkillBar`, `WorkspacePanel`, CSS).
   Deploy, verify in browser.
6. **Tests & evals** (§9.1, §9.2) alongside each stage; full suite green.
7. Commit on `feat/thinkpad-redesign`. Prod untouched.
