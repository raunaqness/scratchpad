# Signal AG-UI frontend

This frontend connects assistant-ui to Signal's existing Python/LangGraph
workflow through an AG-UI adapter.

## Quick Start

### Using CLI (Recommended)

```bash
npx assistant-ui@latest create my-app --example with-ag-ui
cd my-app
```

### 1. Start the Signal AG-UI adapter

From the repository root, activate the existing Python environment and run:

```bash
cd frontend
../.venv/bin/python server/agent.py
```

The adapter starts at `http://localhost:8001/agent` and loads Signal's root
`.env` file, including `OPENROUTER_API_KEY` and `OPENROUTER_MODEL`.

### 2. Configure Environment

Create `.env.local`:

```
NEXT_PUBLIC_AGUI_AGENT_URL=http://localhost:8001/agent
```

### 3. Run the Frontend

```bash
pnpm dev
```

## Features

- AG-UI protocol integration via `@assistant-ui/react-ag-ui`
- Multi-thread support with "New Thread" button
- Custom browser alert tool demonstration
- Client-side tool execution
- Tool result rendering

## Backend Agent

The included `server/agent.py` provides:

- Signal mode: Runs `run_conversation()` from the existing Python backend

### Endpoints

- `POST /agent` - AG-UI agent endpoint (SSE streaming)
- `GET /health` - Health check

## Related Documentation

- [assistant-ui Documentation](https://www.assistant-ui.com/docs)
- [AG-UI Protocol](https://docs.ag-ui.com)
