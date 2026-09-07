# Signal

Signal is a **creative thinking-pad for content**. Bring a product, a feature,
or a rough idea; Signal helps you brainstorm angles, shape an outline, and write
the piece — a **LinkedIn post, a LinkedIn article, or a blog post**. A live
artifact evolves as you talk, and you can edit it and hand it back.

It is **not** a research agent, a publisher, or a general Q&A bot.

## What it does

- **Brainstorm** — 3-5 distinct angles for the piece, with a recommendation.
- **Draft** — a full post / article / blog post, streamed as it's written.
- **Revise** — a targeted change to the current draft, nothing else.
- **Critique** — an editor's read, with fixes added to the artifact's open
  questions.
- **Stays grounded** — uses only facts you've confirmed; flags unverified
  specifics as open questions instead of inventing them. For a product that
  doesn't exist yet, it proposes positioning and labels it `[assumption]`.

## Architecture (short)

```
user turn
  → interpret        (one structured TurnPlan, temperature 0)
  → route            (trusts the plan; deterministic safety check alongside)
  → brainstorm | draft | revise | critique | respond
  → artifact + reply streamed out; thread state saved to SQLite
```

Entry points in `backend/app.py`:

- `run_conversation(user_id, conversation_id, user_message)` — sync, one turn.
- `astream_conversation(...)` — async generator of `artifact` / `reply` /
  `status` / `final` events (used by the AG-UI adapter).

See [`system-design-note.md`](./system-design-note.md) for the full design and
[`overview.md`](./overview.md) for a one-pager.

## Stack

| Piece | Choice |
| --- | --- |
| Orchestration | LangGraph (`AsyncSqliteSaver` checkpointer) |
| LLM access | OpenRouter via `langchain-openai`, one factory in `backend/llm.py` |
| Generation | `backend/capabilities/writing.py` |
| Prompts | sectioned + versioned in `backend/prompts.py` (`THINKPAD_V3`) |
| Safety | `backend/policy.py` (publish/empty-draft only) |
| Web | `backend/agent.py` — AG-UI / CopilotKit-compatible SSE |
| Persistence | SQLite for threads, `data/memory/*.json` for per-user memory |
| Eval | pytest + DeepEval |

## Quick start

```bash
cd signal_v2
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env      # set OPENROUTER_API_KEY and OPENROUTER_MODEL
```

```python
from backend.app import run_conversation

result = run_conversation(
    user_id="demo-user",
    conversation_id="demo-thread",
    user_message="Help me brainstorm a LinkedIn post about our new caching layer.",
)
print(result["assistant_message"])
print(result["artifact"]["angles"])
```

CLI (prints the artifact after each turn):

```bash
python -m backend.terminal_chat
```

## Web app

```bash
uvicorn backend.agent:app --reload --port 8001   # AG-UI endpoint at /agent
cd frontend && npm run dev                        # http://localhost:3000/app
```

`SIGNAL_CORS_ORIGINS` controls which origins may call `/agent`
(default `http://localhost:3000,http://localhost:5173`). For a containerized
run, `docker compose up --build`.

> Note: the frontend artifact panel still reads the old `draft` / `requirements`
> shape. The backend emits the new `artifact.{angles,outline,body,open_questions,
> version}` (plus `draft` / `draft_version` mirrors for compatibility); updating
> the panel to the new shape is a separate frontend change.

## Environment

| Variable | Purpose |
| --- | --- |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | required for live runs |
| `SIGNAL_INTERPRET_TEMPERATURE` | turn interpreter temp (default 0.0) |
| `SIGNAL_DATA_DIR` | root for `signal.db` and `memory/` |
| `SIGNAL_DB_PATH` | override the checkpointer DB path |
| `SIGNAL_CORS_ORIGINS` | comma-separated allowed origins for `/agent` |
| `SIGNAL_HISTORY_WINDOW`, `SIGNAL_SUMMARIZE_AFTER` | transcript windowing |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY` | optional tracing |

## Testing

Deterministic (no key):

```bash
pytest -q tests/test_backend.py tests/test_streaming.py tests/test_agent_events.py
```

DeepEval / conversation suites (need a live OpenRouter key; skip otherwise):

```bash
pytest -q -s tests/test_conversations.py tests/test_product_deepeval.py tests/test_helpful_tone_deepeval.py
./run_deepeval_matrix.sh
```

DeepEval metrics are LLM-as-judge; scores vary across runs. Conversation tests
use **fixed user turns** and the real backend — the nondeterministic
`ConversationSimulator` is not used.

## Workflow rules

- **Formats:** LinkedIn post, LinkedIn article, blog post.
- **Grounding:** only user-confirmed facts; unverified specifics become
  `open_questions`; `[assumption]` framing is allowed for exploratory products;
  no external research.
- **Safety:** Signal never publishes, schedules, or sends; it says so plainly.
- **Questions:** at most one per turn, and only when it can't otherwise make
  progress.
