# Signal System Design Note

## 1. Purpose and scope

Signal is a **creative thinking-pad for content**. One person works with it to
think through and write a single piece — a LinkedIn post, a LinkedIn article, or
a blog post — about a product, a feature, or an idea that may not exist yet.

Design goals:

1. Help the user *think*, not just format facts: propose angles, compare
   directions, outline, then draft.
2. Keep a **live artifact** that moves forward every turn and that the user can
   see and edit.
3. Stay grounded: use only what the user has confirmed; surface unverified
   specifics as questions rather than inventing them or refusing.
4. Support in-progress products with clearly-labelled assumptions.
5. Persist thread state durably; keep the safety boundary in code, not prompts.

Non-goals: research/browsing, publishing or scheduling, general Q&A.

## 2. Architecture

```
backend/
├── agent.py            AG-UI (SSE) adapter over astream_conversation
├── app.py              LangGraph graph + run_conversation / astream_conversation
├── artifact.py         Artifact model + client-edit merge
├── signal_models.py    TurnPlan, Critique, GroundingNotes, PolicyDecision
├── prompts.py          sectioned, versioned prompts (one per job)
├── capabilities/writing.py   brainstorm / write / revise / critique / grounding / chat_reply
├── llm.py              single cached OpenRouter chat-model factory
├── policy.py           deterministic safety checks
├── memory_store.py     per-user durable memory (JSON)
├── textutil.py         shared word-count / trim helpers
└── skills/             craft notes loaded into the writer/brainstorm prompts
```

### 2.1 The graph

```
START ─► interpret ─► (route) ─► brainstorm ─► END
                              ├─► draft      ─► END
                              ├─► revise     ─► END
                              ├─► critique   ─► END
                              └─► respond    ─► END
```

`interpret` calls the model once with `with_structured_output(TurnPlan)` at
`SIGNAL_INTERPRET_TEMPERATURE` (0.0). The router reads the validated plan and
picks a node — there are no regex re-classifications of the user's text. The
only deterministic override is `policy.preflight`, which sets
`safety_flag = "publish_request"` when the user asks Signal to post/publish/send
(Signal has no such integration).

Each work node returns an updated `Artifact` and a short assistant line.
`respond` handles chat, clarifying questions, and safety replies, and it streams
a real model reply for the chat case.

### 2.2 The artifact

`Artifact` (see `artifact.py`) carries the whole workspace:

| field | meaning |
| --- | --- |
| `kind` | `idea_board` → `outline` → `draft` |
| `format` | `linkedin_post` / `linkedin_article` / `blog_post` |
| `product_mode` | `existing` (strict grounding) / `exploratory` (labelled assumptions ok) |
| `angles`, `outline` | brainstorming surface |
| `body`, `sections` | draft surface |
| `sources` | facts the user has confirmed |
| `open_questions` | things to confirm + unverified claims flagged by grounding |
| `status`, `version`, `updated_at` | lifecycle |

`version` and `status` are server-owned. `apply_client_edits` merges a
client-edited artifact back in but only for `title / topic / body / outline /
angles / sections` — the client can rewrite the draft, not rewrite history.

### 2.3 Streaming

`astream_conversation` runs `graph.astream(..., stream_mode=["custom","updates"])`.
Nodes push through `get_stream_writer()`:

* `{"type": "artifact", "artifact": {...}}` — a new artifact version. The draft
  node emits partial bodies every ~24 tokens as it writes.
* `{"type": "reply", "delta": "..."}` — assistant reply tokens (real model
  tokens for chat/critique; the one-line status for work nodes).

`agent.py` turns those into AG-UI `STATE_SNAPSHOT` and `TEXT_MESSAGE_*` events.
Artifact snapshots are held while a text message is open so a message is never
split. `RUN_FINISHED` is always sent, including after an error (whose message is
generic — no stack traces on the wire). CORS origins come from
`SIGNAL_CORS_ORIGINS`.

### 2.4 Persistence

* **Thread state** — `AsyncSqliteSaver` at `SIGNAL_DB_PATH`
  (default `<SIGNAL_DATA_DIR>/signal.db`). Single source of truth for `turns`,
  `summary`, `artifact`, `plan`, `trajectory`. Replaces both the old
  `MemorySaver` and the old `data/conversations/*.json`.
* **Per-user memory** — `data/memory/<user_id>.json`, atomic writes. Only
  durable, explicitly-stated signal (`audience`, preferred `tone`). It is fed to
  the interpreter as context; it never auto-fills the artifact. Re-stating a
  fact is what puts it in `sources`.

### 2.5 Prompts (`prompts.py`)

* One prompt per job: `THINKPAD_SYSTEM_PROMPT`, `interpret_prompt`,
  `BRAINSTORM_SYSTEM_PROMPT`, `writer_system_prompt(fmt)` (×3 formats),
  `REVISE_SYSTEM_PROMPT`, `CRITIQUE_SYSTEM_PROMPT`, `GROUNDING_SYSTEM_PROMPT`,
  `CHAT_SYSTEM_PROMPT`.
* Sectioned (`# Role` / `# You do` / `# Grounding` / `# Output`), not a run-on
  sentence.
* Grounding rules are stated once in the system prompt; writer prompts point at
  "the shared grounding contract" instead of re-listing prohibitions.
* Each writer prompt has craft guidance + a good/weak micro-example and pulls a
  short skill file (`skills/*/SKILL.md`).
* Versioned: `MARKETING_AGENT_V1`, `MARKETING_AGENT_V2` kept for history,
  `THINKPAD_V3` current, with a `PROMPT_CHANGELOG`.

## 3. Grounding model

* `sources` is the only factual authority.
* `capabilities.writing.grounding_notes` is a cheap structured pass that lists
  specific claims (numbers, specs, prices, dates, quotes, results) not present in
  `sources`. Output goes to `open_questions`. **It never blocks a draft.**
* Exploratory products may carry `[assumption]` framing; the grounding pass is
  told to ignore clearly-labelled assumptions and `[TK: ...]` placeholders.
* The old "product name appears in the draft" check and the "≥3 facts or refuse"
  gate are gone.

## 4. Safety boundary

`policy.py` is deliberately tiny:

* `preflight(message)` → flags publish/send/schedule requests so `respond` can
  answer honestly. It does **not** block blog/article/brainstorm.
* `check_draft(text)` → a generated draft must be non-empty.

Everything else (tone, format choice, whether to ask a question) is behaviour,
shaped by prompts and the graph, not a refusal.

## 5. Tests

| Layer | File | Needs a key |
| --- | --- | --- |
| Unit + graph, scripted fake models | `tests/test_backend.py` | no |
| Streaming order (artifact → reply → final) | `tests/test_streaming.py` | no |
| AG-UI event well-formedness | `tests/test_agent_events.py` | no |
| Fixed-turn conversation metrics | `tests/test_conversations.py` | yes (skips) |
| Product-catalog scenarios | `tests/test_product_deepeval.py` | yes (skips) |
| Helpful-tone G-Eval | `tests/test_helpful_tone_deepeval.py` | yes (skips) |

`conftest.py` provides `FakeModelScript`, which maps a `get_chat_model` call to a
scripted response **by its tag** (`signal:interpret`, `signal:write`, …), so one
fake serves every call site in a graph run. `install_models` patches the factory
in `backend.llm`, `backend.app`, and `backend.capabilities.writing`.

The default `ConversationSimulator` is no longer used: it produced
non-actionable failures for a workflow this specific. Conversation tests use
fixed user turns and the real backend, judged by one focused metric each.

## 6. Known limitations / next steps

* `run_conversation` opens a fresh checkpointer connection per turn. Fine for
  local/eval; a long-lived process should hold one connection.
* Rolling summary (`_rollup`) is a single extra LLM call once a transcript passes
  `SIGNAL_SUMMARIZE_AFTER`; there is no eval on summary quality yet.
* `agent.py` emits back-compat `draft` / `draft_version` mirrors, but the
  existing `frontend/` artifact panel still reads the old `requirements` /
  `progress` shape and should be updated to `artifact.{angles,outline,body,
  open_questions,version}` (frontend work, out of scope for this change).
* No auth on `/agent`; `user_id` is taken from `forwarded_props.user_id` if the
  client sends it, else `agui-<thread_id>`.
