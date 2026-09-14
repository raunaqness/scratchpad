# Plan — Ingestion subsystem (blog-URL source): discover → confirm → scrape → store → knowledge graph

**Companion to:** [`docs/prd-house-voice.md`](./prd-house-voice.md) (product PRD for House Voice,
"not an engineering spec"). This document is the engineering spec for the **Reader** piece of House
Voice — but built as the **first source** of a general-purpose **ingestion subsystem**, on its own
route namespace, since blog-URL scraping won't be the only way users feed material in (file upload,
paste-text are named future sources). Briefing synthesis, accept/reject-as-knowledge, and wiring into
the main chat's LangGraph turn stay **deferred** — see §8.

**Status:** draft for discussion, nothing built yet.

---

## 1. Understanding of the request

An ETL pipeline with **one uniform human checkpoint**, no branching business logic in the flow itself:

1. **Discover** — given one blog URL, always parse the page(s) for article links + titles. This step
   always runs, regardless of how many are found.
2. **Confirm** — always show the user what's about to be scraped, **every time**, whether there are 3
   candidates or 300. No "skip confirmation if ≤15" special case — one consistent path.
   - Hard cap stays: at most 15 selected for scraping. If more than 15 are found, show a larger pool
     (newest 50) with the newest 15 pre-checked; user adjusts within that cap.
3. **Extract** (E) — Scrapy fetches only the *confirmed* URLs; trafilatura pulls structured content.
4. **Transform/Load** (T/L) — normalize, write to local files, load into Graphiti.
5. This lives under its **own API namespace**, `/api/ingest`, separate from the main chat/agent
   routes — not because it's less important, but because it's a different kind of subsystem (batch,
   background, checkpointed) that will grow more entry points (upload a file, paste text) sharing the
   same run/confirm/progress model. Blog-URL scraping is the first of those, not the only one.

---

## 2. Scope

**In (this pass)**

- Discovery always runs first and always produces a candidate list for confirmation — no
  count-based branching.
- A persisted **ingestion run**, generalized so it's not blog-specific in its data model (a
  `source_type` column), with **blog** as the only implemented source this pass.
- Uniform selection/confirm step: candidates persisted, user must explicitly confirm (even if just
  "confirm the defaults") before anything is scraped. Cap: 15 selected, always.
- Scrapy fetch + trafilatura extraction of only the confirmed URLs.
- Per-item stage tracking in Postgres, live-updated — the progress visibility requirement.
- A minimal HTTP API under `/api/ingest`, separate from the main chat's routes, so this subsystem can
  be developed, deployed, and iterated on independently.
- Local JSON files for scraped content + Graphiti ingestion into an embedded knowledge graph.
- A CLI mirroring the API, for testing without any UI.

**Out (this pass — see §8)**

- The actual frontend (a dedicated ingestion UI/page, separate from the scratchpad composer) — API
  and data model are shaped for it, not built this pass.
- Other ingestion sources (file upload, paste-text) — the data model leaves room (`source_type`), but
  only `blog` is implemented now.
- Wiring into the main chat's LangGraph turn, credits gating, briefing synthesis, refresh/diff.

---

## 3. Where it lives

`backend/ingest/` — renamed from the earlier `backend/reader/` draft, restructured so source-agnostic
pieces (run/item persistence, the API router) sit apart from blog-specific scraping logic, which now
lives in its own subpackage. This is what makes "add a file-upload source later" additive rather than
a rewrite.

```
backend/ingest/
  __init__.py
  models.py            # IngestRun, IngestItem, Candidate — source-agnostic shapes
  runs_store.py          # Postgres: create run, save candidates, update selection, update stage, read run
  api.py                  # FastAPI router mounted at /api/ingest — source-agnostic endpoints
  cli.py                   # same operations, terminal-only
  requirements.txt          # scrapy, trafilatura, graphiti-core, falkordb, feedparser

  blog/                      # first source implementation
    __init__.py
    discover.py                # sitemap/RSS-first + link-heuristic fallback → candidate (url, title) pairs
    spider_runner.py             # subprocess: scrapes confirmed URLs, writes stage updates as it goes
    extract.py                    # per-page structured extraction (title/date/author/text)
    store.py                       # write/read JSON files (scraped content)
    graph.py                        # Graphiti ingestion + search

backend/migrations/
  0003_ingest.sql                   # scratchpad.ingest_runs, scratchpad.ingest_items
```

`backend/agent.py` mounts `backend/ingest/api.py`'s router at `/api/ingest`, alongside (not inside)
the existing `/api/agent`, `/api/account`, `/api/artifacts` routes — same FastAPI app, separate
prefix, no shared code path with the chat turn.

---

## 4. Data model & persistence (`runs_store.py`, `0003_ingest.sql`)

Generalized beyond blog scraping, so a future source only adds rows, not new tables:

```sql
CREATE TABLE IF NOT EXISTS scratchpad.ingest_runs (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id           TEXT,
    source_type       TEXT NOT NULL DEFAULT 'blog',   -- 'blog' this pass; 'file_upload' / 'paste_text' later
    source_ref        TEXT NOT NULL,                   -- the blog root URL for this source; a filename/paste id later
    discovery_method  TEXT,                             -- sitemap | rss | link_crawl (blog-specific; null for other sources)
    status            TEXT NOT NULL DEFAULT 'discovering',
        -- discovering -> awaiting_confirmation -> scraping -> ingesting -> done | failed | cancelled
    max_items         INTEGER NOT NULL DEFAULT 15,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scratchpad.ingest_items (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id       BIGINT NOT NULL REFERENCES scratchpad.ingest_runs(id) ON DELETE CASCADE,
    url          TEXT,                    -- populated for source_type='blog'; other sources may leave null
    title        TEXT,
    published_at TIMESTAMPTZ,
    selected     BOOLEAN NOT NULL DEFAULT false,
    stage        TEXT NOT NULL DEFAULT 'discovered',
        -- discovered -> queued -> fetching -> fetched -> extracting -> extracted
        --            -> storing -> stored -> ingesting -> ingested
        -- failed / skipped at any point
    error        TEXT,
    rank         INTEGER NOT NULL,        -- discovery order, for "newest 50" / "newest 15" display
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, url)
);

CREATE INDEX IF NOT EXISTS idx_ingest_items_run ON scratchpad.ingest_items (run_id, rank);
```

```python
# runs_store.py — all source-agnostic
async def create_run(source_type: str, source_ref: str, user_id: str | None) -> int: ...
async def save_candidates(run_id: int, candidates: list[Candidate], method: str | None) -> None: ...
async def get_run(run_id: int) -> IngestRun: ...                  # includes all items + stages
async def update_selection(run_id: int, selected_urls: list[str]) -> None:
    """Enforces the hard cap of 15 selected; raises if the caller sends more."""
async def set_run_status(run_id: int, status: str) -> None: ...
async def update_item_stage(run_id: int, url: str, stage: str, error: str | None = None) -> None: ...
```

`status = 'awaiting_confirmation'` is not a fast-path skip for small runs — **every** run passes
through it. The only thing that varies by candidate count is how the pool/pre-selection is built
(§5.2), never whether confirmation happens.

---

## 5. Pipeline (blog source)

```
root URL
  → blog/discover.py        candidate (url, title, published_at?) pairs — always runs, no fetching of full content
  → runs_store.create_run + save_candidates    status: discovering -> awaiting_confirmation
  → [user confirms/edits selection via API — always required]   status: awaiting_confirmation -> scraping
  → blog/spider_runner.py (subprocess)   scrapes only confirmed URLs, stage-updates Postgres as it goes
      → blog/extract.py     per page: title, date, author, body text, excerpt
      → blog/store.py         writes JSON file per page
  → blog/graph.py               Graphiti episode per stored page → embedded graph; status -> ingesting -> done
  → api.py / cli.py query        Graphiti hybrid search over the graph
```

### 5.1 Discovery (`blog/discover.py`) — unchanged in method, always runs

Sitemap → RSS → link-crawl heuristic, same as before, returning `(url, title, published_at?)` triples.
This step has no confirmation-skipping logic of its own — it always produces a candidate list and
hands it to the uniform selection rule below.

```python
def discover(root_url: str, pool_size: int = 50) -> tuple[list[Candidate], str]:
    """Returns (candidates, discovery_method). candidates ordered newest-first where dates exist."""
```

### 5.2 Pre-selection (applied uniformly, not a branch in the pipeline)

```python
def preselect(candidates: list[Candidate], max_items: int = 15, pool_size: int = 50) -> list[Candidate]:
    """
    Always returns a pool for the user to review — never auto-confirms.
    - len(candidates) <= max_items: pool = all of them, all pre-checked (defaults the user still confirms).
    - len(candidates) >  max_items: pool = newest `pool_size`, newest `max_items` pre-checked.
    Caller always persists as `awaiting_confirmation` and always waits for an explicit confirm call —
    same for 3 candidates as for 300.
    """
```

The distinction from the previous draft: this function only decides what's **pre-checked**, never
whether the human step happens. `POST /start` is required in every case (§6.1) — no code path
reaches `scraping` without it.

### 5.3–5.5 Scraping, storage, knowledge graph — unchanged from the previous draft

`blog/spider_runner.py` runs as a subprocess (Scrapy/Twisted can't share FastAPI's asyncio loop),
reads `selected = true` rows for the run, updates `ingest_items.stage` live at each step
(fetching → fetched → extracting → extracted → storing → stored, or failed) via a **synchronous**
Postgres client (`psycopg2`, not `asyncpg` — the subprocess runs Twisted's blocking `CrawlerProcess`,
which doesn't share a thread with an asyncio loop; `asyncio.run()` is only invoked afterward, once the
reactor has stopped, for the Graphiti ingestion step), then triggers `blog/graph.py` ingestion once
scraping completes. `blog/extract.py` (trafilatura) and `blog/store.py` (one JSON file per page under
`data/ingest/<domain-slug>/pages/`) are unchanged in mechanics from the earlier draft, just moved
under `blog/`.

**Correction on the graph backend** (verified while starting implementation, supersedes §9 of the
previous draft): "FalkorDB Lite" is not actually a pip-installable embedded server — the `falkordb`
Python package is a pure Redis-protocol client that always dials an already-running FalkorDB server
over `host:port`; there's no bundled zero-config binary graphiti_core spawns for you. Real FalkorDB
still means standing up a server (Docker, in practice). **Reverting to Kuzu** — genuinely embedded,
file-based, no server process, confirmed working against the installed `graphiti-core==0.30.2`.
Graphiti does mark Kuzu deprecated (works today, may be dropped in a future release) — accepted
tradeoff for a zero-infra v1; FalkorDB/Neo4j via Docker remain the upgrade path when that matters.

---

## 6. Interface

### 6.1 API (`api.py`) — mounted at `/api/ingest`, separate from the chat's routes

| Endpoint | Purpose |
|---|---|
| `POST /api/ingest/blog/discover` `{url}` | Always runs discovery + pre-selection, persists a run in `awaiting_confirmation`. Returns `{run_id, source_type: "blog", discovery_method, candidates: [{url, title, published_at, selected}]}`. |
| `PATCH /api/ingest/runs/{run_id}/selection` `{selected_urls}` | User's edits to the checklist. 400s if `len(selected_urls) > 15`. |
| `POST /api/ingest/runs/{run_id}/start` | **Always required**, even to accept the defaults as-is. Only this call moves a run out of `awaiting_confirmation`. Spawns `spider_runner`. |
| `GET /api/ingest/runs/{run_id}` | Full run + per-item stages — what the frontend polls for the progress list. |
| `GET /api/ingest/runs/{run_id}/stream` | SSE version, same pattern as the existing agent SSE in `backend/agent.py`. |
| `POST /api/ingest/runs/{run_id}/query` `{question}` | Graphiti search once `status = done`. |

Future sources add endpoints alongside `blog/discover` (e.g. `POST /api/ingest/upload`,
`POST /api/ingest/paste`) that produce the same `awaiting_confirmation` run shape, so
`selection` / `start` / the run/stage GET / `query` stay shared across every source — not
reimplemented per source.

### 6.2 CLI (`cli.py`)

```bash
python -m backend.ingest.cli discover-blog <url>                    # prints run_id + candidate table, status=awaiting_confirmation
python -m backend.ingest.cli select <run_id> <url1,url2,...>         # optional — edits the pre-checked defaults
python -m backend.ingest.cli start <run_id>                           # required in all cases; spawns spider_runner, prints stage updates
python -m backend.ingest.cli query <run_id> "<question>"               # Graphiti search
```

---

## 7. Orchestration: still no Redis/Celery

Unchanged from the previous draft: Postgres (`ingest_runs.status` + `ingest_items.stage`) is the
job/queue store at this scale; execution is a subprocess per run, spawned from a FastAPI background
task. `arq` (asyncio-native, Redis-backed) is the noted upgrade path if concurrent multi-user runs or
retry/backoff policies are needed later — not needed for v1.

---

## 8. Explicit non-goals this pass

- The frontend itself — this pass's plan is for a **dedicated ingestion UI area** (its own route,
  e.g. `frontend/app/ingest/...`), separate from the scratchpad composer, matching the API being on
  its own namespace. Not built this pass; `/api/ingest` is shaped so it can be.
- Other ingestion sources (file upload, paste-text) beyond the `source_type` column existing to
  support them later.
- LangGraph node, wiring into the main chat turn, credits gating.
- Briefing synthesis and accept/reject of *content* (House Voice's separate confirmation step, over
  the knowledge extracted — not the same as confirming which pages to scrape here).
- Refresh/diff, multi-run concurrency limits, retry/backoff policy.

**Next pass:** build the dedicated ingestion frontend against §6.1's API; later, wire a `blog` (and
eventually other-source) ingestion run into the product PRD's Reader sub-agent flow inside the main
chat, and add the briefing-synthesis step on top of the resulting knowledge graph.

---

## 9. Open questions / decisions made for the sake of leanness (flag if wrong)

- **Confirmation is now unconditional** — removed the ≤15-auto-select branch entirely; `preselect()`
  only affects what's pre-checked, `POST /start` is always a required, explicit call.
- **Renamed `backend/reader/` → `backend/ingest/`, with blog-specific code under `backend/ingest/blog/`**
  — reflects that this is a general ingestion subsystem with blog-URL scraping as its first source,
  per your ask. Table names generalized to `ingest_runs` / `ingest_items` with a `source_type` column.
- **New route namespace `/api/ingest`**, mounted separately from the existing chat/agent/artifacts
  routes in `backend/agent.py` — own router, no shared code path, no credits gate yet.
- **Candidate pool: newest 50 shown, newest 15 pre-checked** when over the cap — carried over,
  unchanged.
- **Postgres as the job/queue store, no Redis/Celery** — carried over, unchanged (§7).
- **FalkorDB Lite over Neo4j, not Kuzu (deprecated), not Qdrant (vector-only)** — carried over,
  unchanged.
- **Package under `backend/ingest/`, not a git submodule** — carried over, still flag if you want an
  actual separate repo.
