# Signal — overview

Signal is a small, constrained **LinkedIn post assistant**. You give it confirmed
product facts; it drafts (and can edit/validate) a LinkedIn post. It does not
research products, publish content, write blogs, or answer general questions.

## Core loop

Each call to `run_conversation()` is one turn:

1. **Load** conversation + memory JSON  
2. **Guard** out-of-scope requests (`guardrails.json`)  
3. **Analyze** the turn with an OpenRouter LLM  
4. **Merge** new facts into confirmed memory  
5. **Require** a product name + ≥3 facts before writing  
6. **Route** (LangGraph): clarify · generate · edit · validate  
7. **Write** via `capabilities/social_media.py` when ready  
8. **Validate** the draft, then **persist** transcript, draft history, and trajectory  

## Why this shape

- Factual grounding stays in application state, not only in the model.  
- The LLM proposes turn analysis; the app owns requirements, routing, and storage.  
- Guardrails sit in front of the model so policy is not left to prompting alone.

## Stack

Python · LangGraph · OpenRouter · local JSON under `data/` · pytest + DeepEval.

## Run

```bash
pip install -r backend/requirements.txt
cp .env.example .env   # set OPENROUTER_API_KEY + OPENROUTER_MODEL
```

```python
from backend.app import run_conversation
print(run_conversation("user", "thread", "Write a post for X… with three facts.")["assistant_message"])
```

## Tests

| Suite | Role |
| --- | --- |
| `tests/test_backend.py` | Deterministic, no remote model |
| DeepEval / scenario tests | Conversation quality (nondeterministic judges) |
| `./run_deepeval_matrix.sh` | Bundled DeepEval run + artifacts |

## Limits (current)

Simulator-driven DeepEval tests can drift; fixed turns are better for acceptance.
Validation hard-gates completeness and word count, not every fact phrase.
Local JSON is MVP storage, not multi-process production.

**Full design:** [`system-design-note.md`](./system-design-note.md) · **Setup detail:** [`README.md`](./README.md)
