"""``/api/ingest`` — its own router, mounted separately from the chat/agent
routes in ``backend/agent.py``. Source-agnostic except for
``POST /blog/discover``; every other endpoint works the same regardless of
which source started the run.

The knowledge graph and the 15-article cap are **account-wide** — a run is
one blog-URL scrape attempt, but "what's in the graph" and "how many slots
are left" always look across every run a user has ever done (see
``runs_store.library_items`` / ``ingested_count``).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.ingest import runs_store
from backend.ingest.blog import discover as blog_discover
from backend.ingest.blog import graph as blog_graph
from backend.ingest.models import MAX_SELECTED_ITEMS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ingest", tags=["ingest"])

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class DiscoverBody(BaseModel):
    url: str
    user_id: str


class SelectionBody(BaseModel):
    selected_urls: list[str]


class QueryBody(BaseModel):
    question: str
    user_id: str


def _require_user_id(user_id: str) -> str:
    user_id = (user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    return user_id


@router.get("/library")
async def get_library(user_id: str) -> dict[str, Any]:
    """Everything the ingest page needs on load, in one call: the account's
    ingested articles, how many slots are left, and any run still in
    progress (so an in-flight scrape is never invisible after a refresh)."""

    user_id = _require_user_id(user_id)
    items = await runs_store.library_items(user_id)
    running_id = await runs_store.active_run_id(user_id)
    active_run = await runs_store.get_run(running_id) if running_id else None
    return {
        "count": len(items),
        "max": MAX_SELECTED_ITEMS,
        "remaining": max(0, MAX_SELECTED_ITEMS - len(items)),
        "items": [
            {
                "id": i["id"],
                "url": i["url"],
                "title": i["title"],
                "published_at": i["published_at"].isoformat() if i["published_at"] else None,
                "source_ref": i["source_ref"],
            }
            for i in items
        ],
        "active_run": _run_dict(active_run) if active_run else None,
    }


@router.delete("/items/{item_id}")
async def remove_item(item_id: int, user_id: str) -> dict[str, Any]:
    """Removes one article: from the graph (surgically — see
    blog/graph.py:remove), the stored file, and the DB row. Frees one slot
    under the account's cap."""

    user_id = _require_user_id(user_id)
    item = await runs_store.get_item_for_user(item_id, user_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item not found")

    if item["episode_uuid"]:
        await blog_graph.remove(user_id, item["episode_uuid"])
    from backend.ingest.blog import store as blog_store

    blog_store.delete_page(item["run_id"], item["url"])
    await runs_store.mark_item_removed(item_id)
    return await get_library(user_id)


@router.post("/blog/discover")
async def discover_blog(body: DiscoverBody) -> dict[str, Any]:
    """Always runs discovery and always lands on `awaiting_confirmation` —
    there is no count-based fast path to scraping. How many of the found
    candidates can be pre-selected/selected here is capped by however many
    slots this account has left under the account-wide 15-article limit,
    not a flat 15 every time."""

    user_id = _require_user_id(body.user_id)
    remaining = max(0, MAX_SELECTED_ITEMS - await runs_store.ingested_count(user_id))

    candidates, method = blog_discover.discover(body.url)
    candidates = blog_discover.preselect(candidates, max_items=remaining)

    run_id = await runs_store.create_run("blog", body.url, user_id, max_items=remaining)
    await runs_store.save_candidates(run_id, candidates, method)
    run = await runs_store.get_run(run_id)
    return _run_dict(run)


@router.patch("/runs/{run_id}/selection")
async def update_selection(run_id: int, body: SelectionBody) -> dict[str, Any]:
    run = await runs_store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        await runs_store.update_selection(run_id, body.selected_urls, run.max_items)
    except runs_store.SelectionTooLarge as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    run = await runs_store.get_run(run_id)
    return _run_dict(run)


@router.post("/runs/{run_id}/start")
async def start_run(run_id: int) -> dict[str, Any]:
    """Required in every case, even to accept the defaults as-is — no code
    path reaches `scraping` without this call."""

    run = await runs_store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.status != "awaiting_confirmation":
        raise HTTPException(
            status_code=409, detail=f"run is '{run.status}', not awaiting confirmation"
        )
    selected_count = sum(1 for i in run.items if i.selected)
    if selected_count == 0:
        raise HTTPException(status_code=400, detail="no items selected")
    if selected_count > run.max_items:
        raise HTTPException(
            status_code=400,
            detail=f"at most {run.max_items} items may be selected (account limit reached)",
        )

    await runs_store.set_run_status(run_id, "scraping")
    asyncio.create_task(_spawn_spider(run_id))
    return {"run_id": run_id, "status": "scraping"}


async def _spawn_spider(run_id: int) -> None:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "backend.ingest.blog.spider_runner", "--run-id", str(run_id),
        cwd=str(_PROJECT_ROOT),
    )
    return_code = await proc.wait()
    if return_code != 0:
        logger.error("spider_runner for run %s exited with code %s", run_id, return_code)
        await runs_store.set_run_status(run_id, "failed")


@router.get("/runs/{run_id}")
async def get_run(run_id: int) -> dict[str, Any]:
    run = await runs_store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return _run_dict(run)


@router.post("/query")
async def query_library(body: QueryBody) -> dict[str, Any]:
    """Searches the account's whole knowledge graph — everything ingested
    across every blog, not one run at a time."""

    user_id = _require_user_id(body.user_id)
    count = await runs_store.ingested_count(user_id)
    if count == 0:
        raise HTTPException(status_code=409, detail="nothing has been ingested yet")
    results = await blog_graph.query(user_id, body.question)
    return {"results": results}


def _run_dict(run) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "user_id": run.user_id,
        "source_type": run.source_type,
        "source_ref": run.source_ref,
        "discovery_method": run.discovery_method,
        "status": run.status,
        "max_items": run.max_items,
        "items": [
            {
                "url": i.url,
                "title": i.title,
                "published_at": i.published_at.isoformat() if i.published_at else None,
                "selected": i.selected,
                "stage": i.stage,
                "error": i.error,
            }
            for i in run.items
        ],
    }
