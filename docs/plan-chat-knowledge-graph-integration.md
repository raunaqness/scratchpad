# Plan — Grounding the main chat in the ingest knowledge graph

**Companion to:** [`docs/plan-house-voice-reader.md`](./plan-house-voice-reader.md) (the ingest
subsystem this plugs into) and [`docs/prd-house-voice.md`](./prd-house-voice.md) (the product vision
this was always heading toward — "House Voice" informing the main writing collaborator).

**Status:** design only, nothing built. Written from a walkthrough of the actual current code
(`backend/app.py`, `backend/agent.py`, `backend/capabilities/writing.py`, `backend/ingest/`), not a
generic RAG writeup — every file/function named below exists today.

---

## 1. Where things stand today

Two systems that don't know about each other:

- **The chat** — one LangGraph turn per message (`backend/app.py`): `interpret` (a structured-output
  call that produces a `TurnPlan`) → `route` → a mode node (`note`/`expand`/`tighten`/`brainstorm`/
  `critique`/`build`/`respond`) → `follow_up` → `END`. Nothing in this path imports from
  `backend/ingest/` — confirmed by grep, not assumption.
- **The ingest knowledge graph** (`backend/ingest/blog/graph.py`) — one Graphiti/FalkorDB graph per
  **account** (keyed by `user_id`), already queryable via `graph.query(user_id, question) -> list[dict]`
  (hybrid vector + full-text search, LLM-reranked, article-sourced). Reached today only through the
  standalone `/ingest` page's Ask box.

## 2. Before → after flow

For writing test cases: what actually happens, step by step, on one chat message — today, and with
this integration. Same steps are marked unchanged; new/changed steps are marked accordingly.

### 2.1 Today (before)

- User types a message in the chat UI and sends it
- Backend receives it and runs `interpret`
  - reads the conversation so far + the current scratchpad
  - produces a `TurnPlan`: what mode this turn is (chat / note / expand / …), any new facts, etc.
  - **does not** know or check whether the account has an ingest knowledge base
- `route` picks a mode node based on the `TurnPlan`
- The mode node does its job using only:
  - the conversation history
  - the current scratchpad
  - (for `build`) the skill being run
- If it's a plain chat turn, `chat_reply()` streams a reply token-by-token using only the same inputs
- Reply finishes streaming to the user
- `follow_up` node runs (only if the scratchpad actually changed this turn)
  - reads the new scratchpad
  - streams 3-5 "next move" buttons
- Turn ends

- **Test-relevant fact:** no code path in this flow ever imports from or calls `backend/ingest/`. An
  account with a full 15-article knowledge base and an account with none behave identically in chat
  today — that's the regression to check for once the integration lands (existing chat behavior must
  stay byte-for-byte the same when there's nothing to ground with, or grounding is explicitly not
  triggered).

### 2.2 After the integration

- User types a message in the chat UI and sends it
- Backend receives it and runs `interpret` — **unchanged**
- **New step — `_ground` runs**, between `interpret` and `route`:
  - checks whether this account (`state["user_id"]`) has anything ingested
    - **no ingested data →** sets no grounding, adds no latency beyond the cheap existence check, rest
      of the turn proceeds exactly as in §2.1
    - **has ingested data →** calls the same knowledge-base search the standalone Ask box uses, with
      the user's message as the question
      - gets back zero or more facts, each with which article it came from
      - stores them on the turn's state (not persisted anywhere, this-turn-only)
- `route` picks a mode node — **unchanged** (routing logic itself doesn't consider grounding)
- The mode node does its job using:
  - the conversation history
  - the current scratchpad
  - **+ the grounding facts, if any were found and are relevant** (the model decides relevance when
    drafting — a non-empty grounding result does not force it into the reply)
- If it's a plain chat turn, `chat_reply()` streams a reply — **may now reference the grounded facts**
  if it used them; ignores them if they weren't relevant to the question
- Reply finishes streaming to the user
- **New step — sources shown, only if the reply actually drew on grounding facts:**
  - a small "grounded in: *[article title]*" line/chip appears under the reply
  - if grounding ran but found nothing relevant, or the account has no knowledge base, this step is
    skipped entirely — no empty/placeholder UI
- `follow_up` node runs — **unchanged**
- Turn ends

- **Test-relevant facts:**
  - Empty-library accounts: flow must be identical to §2.1, including latency (the existence check
    should be cheap enough not to be noticeable).
  - Non-empty library, irrelevant question: grounding step runs, finds nothing useful or the model
    ignores what it found, no sources line appears, reply content unaffected.
  - Non-empty library, relevant question: reply may include grounded content, sources line appears and
    names the correct article(s).
  - A failure inside `_ground` (e.g. the graph/DB is briefly unreachable) must never break or block the
    turn — same fail-open posture as `follow_up` — the reply still streams, just ungrounded.

## 3. What already exists that this reuses (the reason this is smaller than it sounds)

- **`SignalState` already carries `user_id`** (`backend/app.py:80`). Every node in the chat graph
  already has the account identity available — "which account's knowledge base" needs no new
  plumbing, just calling `blog_graph.query(state["user_id"], ...)` from inside a node.
- **`graph.query()` is already account-scoped and working**, verified live (§ prior sessions) — this
  plan is purely about *calling* it from a new place, not building retrieval.
- **The `follow_up` node (`backend/app.py:613`) is the exact structural precedent**: a node that runs
  read-only, wrapped in `try/except` so a failure never breaks the turn, streaming its own result via
  `_emit(...)` without touching canonical state. A grounding node looks the same shape.
- **The AG-UI "synthetic tool call" pattern (`backend/agent.py:105-151`)** is how `follow_ups` and
  `choice` reach the frontend as structured UI (not prose) — `_choice_tool_events` /
  `_follow_up_tool_events` build `ToolCallStartEvent`/`Args`/`End` triples the frontend's
  `defineToolkit` renders as custom components. Surfacing "grounded in these sources" follows the same
  recipe, not a new mechanism.

## 4. The four pieces, with the real integration point for each

### 3.1 Link chat to knowledge base

Nothing to build — `state["user_id"]` already is the link, and `graph.query(user_id, ...)` is already
scoped to "everything this account ingested." No thread-to-run association needed (the earlier,
now-abandoned per-run model would have needed this; the account-graph redesign already removed the
need).

### 3.2 A retrieval step inside the turn

New node, e.g. `_ground`, inserted **between `interpret` and the mode nodes** (not after, like
`follow_up` — the retrieved facts need to reach the node that drafts the reply, not run after it):

```
interpret ─► _ground ─► route ─► note | expand | ... | respond ─► follow_up ─► END
```

- Calls `blog_graph.query(state["user_id"], state["user_message"])`, catches all exceptions (grounding
  must never break a turn — same posture as `follow_up`), stores result as `state["grounding"]`
  (a new `SignalState` field: `list[dict] | None`).
- `chat_reply()` (`backend/capabilities/writing.py:235`) is the natural first consumer — it already
  takes a plain dict payload serialized to JSON and sent to the model
  (`{"recent_turns", "scratchpad", "reply_should_convey"}`); adding a `"knowledge_base_facts"` key when
  `state["grounding"]` is non-empty is a one-line change to that payload, with a prompt-level
  instruction ("use these facts if relevant, don't state them as certain if they're not, this reply
  should read as informed, not as reciting a source dump").
- `expand`/`brainstorm`/`tighten` are separate, smaller prompt changes if grounding should extend past
  plain chat replies — deliberately **not** in scope for a first pass (see §6).

### 3.3 When it should run

Two real options, not a foregone conclusion:

- **(a) Always, if the account has anything ingested.** Cheapest to build: `_ground` checks
  `runs_store.ingested_count(user_id) > 0` before bothering to call `graph.query()` at all — skips
  entirely for accounts with an empty library (the common case today), and otherwise runs every turn.
  Costs one extra `graph.query()` call (one embedding + one reranker LLM call) on every turn for
  accounts that *do* have a library, whether or not the question needed it.
- **(b) Only when the turn looks like it would benefit.** Extend `TurnPlan`
  (`backend/signal_models.py:29`) with a field like `needs_knowledge_base: bool`, decided by the
  *same* structured-output call `interpret` already makes (no second LLM call) — `interpret_prompt()`
  gains a line telling the model when to set it. Cheaper on average, but adds one more thing the
  interpret call has to get right, and a wrong "no" silently means grounding never fires for a turn
  that needed it.

Recommendation: start with (a) — it's simpler, and grounding is worth the small latency hit outright
when there's a library to draw from — the actual routing decision should live entirely in the "is
there data to check" call, not a second LLM judgment call about relevance up front. Revisit if
latency/cost data says otherwise once it's live.

### 3.4 Showing sources in the chat UI

Mirrors `_follow_up_tool_events` exactly:

- `_ground` emits `_emit({"type": "sources", "items": [...]})` when grounding actually contributed
  facts to the reply (not just "grounding ran" — only if the reply drew on it).
- `backend/agent.py`'s event loop gets a `pending_sources` alongside `pending_choice`/
  `pending_follow_ups`, flushed the same way, as a new synthetic tool call
  (`toolCallName="sources"`).
- Frontend: a new entry in `defineToolkit` (`frontend/app/app/page.tsx`, same file that renders
  `FollowUpButtons`) renders a small "Grounded in: *[article title]*, *[article title]*" line under the
  reply — same visual register as the existing follow-up chips, not a wall of raw facts. This is the
  product PRD's "always show sources, never a black box" principle carried into chat, not a new
  invention.

## 5. New/changed files, concretely

| File | Change |
|---|---|
| `backend/signal_models.py` | Optionally add `needs_knowledge_base: bool` to `TurnPlan` (only if going with §4.3 option (b)) |
| `backend/app.py` | New `SignalState["grounding"]` field; new `_ground` node; rewire `_builder()` to insert it between `interpret` and the routed mode nodes; `_respond`/mode nodes read `state.get("grounding")` |
| `backend/capabilities/writing.py` | `chat_reply()` payload gains `knowledge_base_facts` when present; prompt tweak in `CHAT_SYSTEM_PROMPT` on how to use them |
| `backend/ingest/blog/graph.py` | No change — `query(user_id, question)` is called as-is |
| `backend/agent.py` | New `pending_sources`, a `_source_tool_events()` (copy of `_follow_up_tool_events`), flushed at both existing flush points |
| `frontend/app/app/page.tsx` | New `defineToolkit` entry rendering the sources line, same pattern as `FollowUpButtons` |

## 6. Explicit non-goals for a first pass

- Grounding `expand`/`brainstorm`/`tighten`/`critique` — chat replies only, first pass.
- Any UI for turning grounding on/off per-thread ("don't use my knowledge base for this guest post") —
  the product PRD names this as a real future need, not required to ship the first version.
- Credits/cost gating on the extra `graph.query()` call.
- Any change to `backend/ingest/` itself — this plan only adds a caller, the knowledge graph and its
  API are already correct and untouched.

## 7. Open questions for you, not decided here

- Option (a) vs (b) in §4.3 — leaning (a), your call.
- Does a grounded reply need a visible "thinking" state in chat (like the existing "Looking for
  follow-ups" progress chip) while `_ground` runs, or should it be invisible/fast enough not to need
  one?
- Should grounding be limited to `mode == "chat"` turns only, or also fire on `note`/`brainstorm` where
  the user is still developing the piece and outside facts might genuinely help shape it?
