# Signal AG-UI frontend

This frontend connects assistant-ui to Signal's existing Python/LangGraph
workflow through an AG-UI adapter.

## Quick Start

### Local development

From the repository root, activate the existing Python environment and run:

```bash
cd signal_v2
source .venv/bin/activate
python -m uvicorn backend.agent:app --reload --port 8001
```

The adapter starts at `http://localhost:8001/agent` and loads Signal's root
`.env` file, including `OPENROUTER_API_KEY` and `OPENROUTER_MODEL`.

In a second terminal:

```bash
cd frontend
npm run dev
```

Open `http://localhost:3000` for the landing page or
`http://localhost:3000/app` for the writing room. The local `.env.local`
points the frontend at the AG-UI adapter on port 8001.

## Features

- AG-UI protocol integration via `@assistant-ui/react-ag-ui`
- Multi-thread support with "New Thread" button
- Custom browser alert tool demonstration
- Client-side tool execution
- Tool result rendering

## AG-UI adapter

The included `backend/agent.py` provides:

- Signal mode: Runs `run_conversation()` from the existing Python backend

### Endpoints

- `POST /agent` - AG-UI agent endpoint (SSE streaming)
- `GET /health` - Health check

## Related Documentation

- [assistant-ui Documentation](https://www.assistant-ui.com/docs)
- [AG-UI Protocol](https://docs.ag-ui.com)

## Docker

From the repository root, copy `.env.example` to `.env`, add the OpenRouter
credentials, and run:

```bash
docker compose up --build
```

The same landing page and writing room are then available on port 3000.
