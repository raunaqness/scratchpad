"""Shapes shared across ingestion sources and the API/CLI/store layers."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

# Account-wide, not per-run: the total number of articles one account may
# have ingested (and un-removed) across every blog at once. A single run's
# `max_items` is however many of this an account has left, computed at
# discover time — see backend/ingest/api.py:discover_blog.
MAX_SELECTED_ITEMS = 15
CANDIDATE_POOL_SIZE = 50


class Candidate(BaseModel):
    """One discovered item, before it's persisted as an ``IngestItem``."""

    url: str
    title: str | None = None
    published_at: datetime | None = None
    selected: bool = False


class IngestItem(BaseModel):
    """A persisted row from ``scratchpad.ingest_items``."""

    id: int
    run_id: int
    url: str | None
    title: str | None
    published_at: datetime | None
    selected: bool
    stage: str
    error: str | None
    rank: int


class IngestRun(BaseModel):
    """A persisted row from ``scratchpad.ingest_runs``, with its items."""

    id: int
    user_id: str | None
    source_type: str
    source_ref: str
    discovery_method: str | None
    status: str
    max_items: int
    items: list[IngestItem] = []


class Page(BaseModel):
    """Structured content extracted from one scraped page (blog source)."""

    url: str
    title: str | None
    published_at: str | None  # ISO date, if found
    author: str | None
    text: str
    excerpt: str
    word_count: int
    fetched_at: str  # ISO timestamp
