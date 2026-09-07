# Signal — overview

Signal is a **creative thinking-pad for content**. You bring a product, a
feature, or a half-formed idea; Signal helps you brainstorm angles, shape an
outline, and write the piece — a LinkedIn post, a LinkedIn article, or a blog
post. A **live artifact** evolves as you talk, and you can edit it and hand it
back.

## Core loop

Each turn runs one pass through a LangGraph graph:

```
interpret ─► route ─► brainstorm | draft | revise | critique | respond ─► END
```

1. **interpret** — one structured-output call (temp 0) produces a `TurnPlan`
   (mode, format, product mode, new facts, constraints, one optional question).
   A deterministic safety check runs alongside it.
2. **route** — trusts the plan. No regex overrides.
3. **work node** — updates the shared `Artifact` (angles → outline → draft),
   streaming each version and, for the draft, the prose token-by-token.
4. **grounding** — a cheap non-blocking pass flags claims the confirmed
   `sources` don't support; they land in `open_questions`, they don't stop the
   draft.

## What changed from the old Signal

| Old | Now |
| --- | --- |
| One capability: "write one LinkedIn post" | brainstorm / draft / revise / critique |
| Blog posts hard-blocked | LinkedIn post, LinkedIn article, blog post |
| Refused until ≥3 product facts | works from whatever you have; `exploratory` products allowed with labelled `[assumption]`s |
| `_analyze`: ~200 lines of regex overrides on top of an LLM call | one trusted `TurnPlan`, thin router |
| Artifact = final post, produced once at the end | artifact updated every node, streamed |
| MemorySaver **and** conversation JSON | one SQLite checkpointer; JSON only for per-user memory |
| DeepAgents wrapper (planning + subagents disabled) | removed; plain LangGraph + structured output |
| Grounding = "product name appears in the draft" | claim-level flags in `open_questions` |
| One 180-word prohibition-list prompt reused everywhere | sectioned prompts, one per job, rules stated once, versioned (`THINKPAD_V3`) |

## Stack

Python · LangGraph (`AsyncSqliteSaver`) · OpenRouter via `langchain-openai` ·
AG-UI (`backend/agent.py`) · pytest + DeepEval.

## Run

```bash
pip install -r backend/requirements.txt
cp .env.example .env          # set OPENROUTER_API_KEY + OPENROUTER_MODEL
python -m backend.terminal_chat            # CLI
uvicorn backend.agent:app --port 8001      # AG-UI endpoint
```

```python
from backend.app import run_conversation
print(run_conversation(
    user_id="u", conversation_id="t",
    user_message="help me brainstorm a launch post for our new caching layer",
)["assistant_message"])
```

**Full design:** [`system-design-note.md`](./system-design-note.md)
