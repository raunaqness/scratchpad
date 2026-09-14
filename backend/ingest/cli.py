"""Terminal entrypoints for the ingest subsystem — same operations as
``api.py``, for testing without a frontend. The knowledge graph and the
15-article cap are account-wide (``user_id``), not per-run.

    python -m backend.ingest.cli discover-blog <user_id> <url>
    python -m backend.ingest.cli select <run_id> <url1,url2,...>
    python -m backend.ingest.cli start <run_id>
    python -m backend.ingest.cli status <run_id>
    python -m backend.ingest.cli library <user_id>
    python -m backend.ingest.cli query <user_id> "<question>"
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys

from backend.ingest import runs_store
from backend.ingest.blog import discover as blog_discover
from backend.ingest.blog import graph as blog_graph
from backend.ingest.models import MAX_SELECTED_ITEMS


async def cmd_discover_blog(user_id: str, url: str) -> None:
    remaining = max(0, MAX_SELECTED_ITEMS - await runs_store.ingested_count(user_id))
    candidates, method = blog_discover.discover(url)
    candidates = blog_discover.preselect(candidates, max_items=remaining)
    run_id = await runs_store.create_run("blog", url, user_id, max_items=remaining)
    await runs_store.save_candidates(run_id, candidates, method)
    print(f"run_id={run_id} discovery_method={method} candidates={len(candidates)} remaining_slots={remaining}")
    for c in candidates:
        mark = "x" if c.selected else " "
        print(f"  [{mark}] {c.title or '(no title)'}  {c.url}")
    print("\nrun: python -m backend.ingest.cli start", run_id, "(after optional select)")


async def cmd_select(run_id: int, urls_csv: str) -> None:
    run = await runs_store.get_run(run_id)
    if run is None:
        print("run not found")
        return
    urls = [u.strip() for u in urls_csv.split(",") if u.strip()]
    try:
        await runs_store.update_selection(run_id, urls, run.max_items)
    except runs_store.SelectionTooLarge as exc:
        print(f"error: {exc}")
        return
    print(f"selection updated: {len(urls)} items")


def cmd_start(run_id: int) -> None:
    """Blocks and streams the spider subprocess's own log output."""

    subprocess.run(
        [sys.executable, "-m", "backend.ingest.blog.spider_runner", "--run-id", str(run_id)],
        check=False,
    )


async def cmd_status(run_id: int) -> None:
    run = await runs_store.get_run(run_id)
    if run is None:
        print("run not found")
        return
    print(f"run_id={run.id} status={run.status}")
    for i in run.items:
        if i.selected:
            print(f"  {i.stage:12s} {i.url}" + (f"  ERROR: {i.error}" if i.error else ""))


async def cmd_library(user_id: str) -> None:
    items = await runs_store.library_items(user_id)
    print(f"{len(items)} / {MAX_SELECTED_ITEMS} articles ingested")
    for i in items:
        print(f"  [{i['id']}] {i['title'] or i['url']}  ({i['source_ref']})")


async def cmd_query(user_id: str, question: str) -> None:
    results = await blog_graph.query(user_id, question)
    for r in results:
        srcs = ", ".join(s["title"] or s["url"] for s in r["sources"])
        print(f"- {r['fact']}  (valid_at={r['valid_at']}, from: {srcs})")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("discover-blog")
    p.add_argument("user_id")
    p.add_argument("url")

    p = sub.add_parser("select")
    p.add_argument("run_id", type=int)
    p.add_argument("urls_csv")

    p = sub.add_parser("start")
    p.add_argument("run_id", type=int)

    p = sub.add_parser("status")
    p.add_argument("run_id", type=int)

    p = sub.add_parser("library")
    p.add_argument("user_id")

    p = sub.add_parser("query")
    p.add_argument("user_id")
    p.add_argument("question")

    args = parser.parse_args()

    if args.cmd == "discover-blog":
        asyncio.run(cmd_discover_blog(args.user_id, args.url))
    elif args.cmd == "select":
        asyncio.run(cmd_select(args.run_id, args.urls_csv))
    elif args.cmd == "start":
        cmd_start(args.run_id)
    elif args.cmd == "status":
        asyncio.run(cmd_status(args.run_id))
    elif args.cmd == "library":
        asyncio.run(cmd_library(args.user_id))
    elif args.cmd == "query":
        asyncio.run(cmd_query(args.user_id, args.question))


if __name__ == "__main__":
    main()
