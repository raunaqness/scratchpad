# Signal

Signal is a constrained LinkedIn-post assistant. It turns product information
you explicitly supply into a grounded LinkedIn draft — then supports edit and
validate cycles. It is intentionally **not** a research agent, publisher, blog
writer, or general Q&A bot.

## What it does

1. Collects confirmed product facts from the conversation (needs a product name
   and at least three distinct facts before drafting).
2. Generates a LinkedIn post from those facts only (no external research).
3. Supports iterative draft lifecycle: create → validate → edit → validate.
4. Persists conversation, memory, draft history, validation, and trajectory as
   local JSON.
5. Evaluates behavior with deterministic backend tests and DeepEval conversation
   metrics.

## Architecture (short)

```text
User message
    → guardrails
    → LLM turn analysis (OpenRouter)
    → merge confirmed memory + requirements
    → LangGraph route: respond | generate | edit | validate
    → social_media capability (create / edit post)
    → draft validation
    → persist JSON + return assistant message + structured result
```

Entry point: `run_conversation(user_id, conversation_id, user_message)` in
`backend/app.py`.

For a deeper walkthrough, see [`system-design-note.md`](./system-design-note.md).
For a one-page summary, see [`overview.md`](./overview.md).

## Stack

| Piece | Choice |
| --- | --- |
| Orchestration | LangGraph |
| LLM access | OpenRouter via `langchain-openai` |
| Writing | `backend/capabilities/social_media.py` |
| Config | `backend/config.py` + `.env` |
| Prompts | versioned in `backend/prompts.py` |
| Policy | `backend/guardrails.json` |
| Persistence | `data/conversations/`, `data/memory/` |
| Eval | pytest + DeepEval |

## Quick start

```bash
cd signal_v2
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
# set OPENROUTER_API_KEY and OPENROUTER_MODEL in .env
```

Minimal usage:

```python
from backend.app import run_conversation

result = run_conversation(
    user_id="demo-user",
    conversation_id="demo-thread",
    user_message=(
        "Write a LinkedIn post for Fujifilm X100VI. "
        "Facts: compact body, 40.2MP sensor, hybrid viewfinder."
    ),
)
print(result["assistant_message"])
```

## Web app

Run the AG-UI adapter and frontend in separate terminals:

```bash
uvicorn backend.agent:app --reload --port 8001
cd frontend && npm run dev
```

The landing page is at `http://localhost:3000`; the writing room is at
`http://localhost:3000/app`. For a containerized run, use
`docker compose up --build`.

## Environment

Copy [`.env.example`](./.env.example). Required for live runs:

| Variable | Purpose |
| --- | --- |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `OPENROUTER_MODEL` | Model id (default in example: `anthropic/claude-sonnet-4.6`) |

Useful optional vars:

| Variable | Purpose |
| --- | --- |
| `OPENROUTER_TEMPERATURE` | Generation temperature |
| `OPENROUTER_MAX_TOKENS` | Max completion tokens |
| `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` | LangSmith traces |
| `SIGNAL_DATA_DIR` | Override local JSON data root |

## Project layout

```text
signal_v2/
├── backend/
│   ├── app.py                  # LangGraph app + run_conversation()
│   ├── config.py               # Settings from env
│   ├── prompts.py              # System prompt contract
│   ├── guardrails.json         # Allowed / blocked capabilities
│   └── capabilities/
│       └── social_media.py     # LinkedIn create + edit
├── data/
│   ├── conversations/          # Per-conversation transcripts + drafts
│   └── memory/                 # Per-user confirmed memory
├── tests/
│   ├── test_backend.py         # Deterministic tests (no remote model)
│   ├── test_conversations.py   # DeepEval ConversationSimulator scenarios
│   ├── test_product_deepeval.py
│   └── test_product_scenarios.py
├── scenario.json               # Simulator goldens
├── product_scenarios.json      # Fixed product scenarios
├── product_deepeval_scenarios.json
├── run_deepeval_matrix.sh      # Bundled DeepEval pytest run + artifacts
├── system-design-note.md       # Full system design
└── overview.md                 # One-page summary
```

## Workflow rules

**Required before drafting**

- Product name
- At least three distinct product facts

**Optional unless the user specifies them**

- Tone, audience, CTA, max word count

**Blocked by guardrails**

- Blog posts, email campaigns, direct publishing, unrelated general questions

**Grounding**

- Only use explicit user-provided / confirmed facts
- Do not invent benefits, specs, or claims
- Do not research the product externally

## Testing

### Deterministic backend (no remote LLM)

```bash
pytest -q tests/test_backend.py
```

These cover requirements extraction, fact thresholds, grounding helpers,
routing, and persistence with fake models.

### DeepEval / conversation suites

Needs a valid OpenRouter key (and whatever DeepEval judge config you use):

```bash
pytest -q -s tests/test_conversations.py
pytest -q -s tests/test_product_deepeval.py
pytest -q -s tests/test_product_scenarios.py
```

Or run the matrix helper (writes artifacts under `.deepeval-runs/`):

```bash
./run_deepeval_matrix.sh
```

**Note:** DeepEval metrics are LLM-as-judge. Scores can vary across runs.
Default `ConversationSimulator` scenarios are exploratory; fixed-turn / product
scenarios are better for workflow acceptance. See
[`conversation-simulator-diagnostics.md`](./conversation-simulator-diagnostics.md).

## Status and known limits

- Draft create/edit/validate works and persists locally.
- Requirements are deterministic; analyzer/generator output is not.
- Validation gates request completeness, product-name anchoring, and word count;
  literal fact-by-fact inclusion is diagnostic, not a hard gate yet.
- Local JSON is fine for MVP / local eval, not multi-writer production storage.

See **Known design risks** and **Recommended evolution** in
[`system-design-note.md`](./system-design-note.md).

## Related docs

| Doc | Contents |
| --- | --- |
| [`overview.md`](./overview.md) | One-page summary of this README |
| [`system-design-note.md`](./system-design-note.md) | Full architecture and eval design |
| [`conversation-simulator-diagnostics.md`](./conversation-simulator-diagnostics.md) | Simulator mode guidance |
| [`goal-accuracy-failure-report.md`](./goal-accuracy-failure-report.md) | GoalAccuracy failure notes |
