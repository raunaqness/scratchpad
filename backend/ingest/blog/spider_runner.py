"""Scrapes a run's *selected* URLs and writes stage updates as it goes.

Runs as its own OS process (``python -m backend.ingest.blog.spider_runner
--run-id <id>``), spawned by the API's start endpoint (or the CLI). Two
things force this out of the main FastAPI process:

1. Scrapy's Twisted reactor can only start once per process and can't share
   a thread with FastAPI's asyncio loop.
2. It doubles as a literal "background process" per run, which is what the
   progress UI is meant to be pointed at.

Stage per item: queued -> fetching -> fetched -> extracting -> extracted ->
storing -> stored -> ingesting -> ingested, or failed at any step. This is a
bounded fetch of a known URL list (not an open crawl) — discovery already
happened, and the user already confirmed the selection.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

import scrapy
from scrapy.crawler import CrawlerProcess

from backend.ingest.blog import extract, graph, store, sync_db
from backend.ingest.models import MAX_SELECTED_ITEMS

logger = logging.getLogger(__name__)

_USER_AGENT = "SignalIngestReader/0.1 (+https://github.com/raunaqness/signal_v2)"


class _BlogSpider(scrapy.Spider):
    name = "ingest_blog"
    custom_settings = {
        "ROBOTSTXT_OBEY": True,
        "CLOSESPIDER_ITEMCOUNT": MAX_SELECTED_ITEMS,
        "DOWNLOAD_DELAY": 1,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "USER_AGENT": _USER_AGENT,
        "LOG_LEVEL": "WARNING",
    }

    def __init__(self, run_id: int, urls: list[str], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.run_id = run_id
        self.urls = urls

    async def start(self):
        # Scrapy 2.13+ entrypoint — the older `start_requests()` is no longer
        # called by the engine at all (confirmed empirically: overriding it
        # silently yielded zero requests under Scrapy 2.19).
        for url in self.urls:
            sync_db.update_item_stage(self.run_id, url, "fetching")
            yield scrapy.Request(url, callback=self.parse, errback=self.on_error)

    def parse(self, response):
        url = response.url
        sync_db.update_item_stage(self.run_id, url, "fetched")
        try:
            sync_db.update_item_stage(self.run_id, url, "extracting")
            fetched_at = datetime.now(timezone.utc).isoformat()
            page = extract.extract(url, response.text, fetched_at)
            if page is None:
                sync_db.update_item_stage(
                    self.run_id, url, "failed", "no article body found"
                )
                return
            sync_db.update_item_stage(self.run_id, url, "extracted")

            sync_db.update_item_stage(self.run_id, url, "storing")
            store.save_page(self.run_id, page)
            sync_db.update_item_stage(self.run_id, url, "stored")
            yield {"url": url}
        except Exception as exc:  # noqa: BLE001 - one bad page shouldn't kill the run
            logger.exception("failed processing %s", url)
            sync_db.update_item_stage(self.run_id, url, "failed", str(exc))

    def on_error(self, failure):
        url = failure.request.url
        sync_db.update_item_stage(self.run_id, url, "failed", str(failure.value))


def run(run_id: int) -> None:
    user_id = sync_db.get_run_user_id(run_id)
    items = sync_db.selected_items(run_id)
    urls = [i["url"] for i in items]
    if not urls or not user_id:
        sync_db.set_run_status(run_id, "failed")
        return

    sync_db.set_run_status(run_id, "scraping")
    process = CrawlerProcess(settings={})
    process.crawl(_BlogSpider, run_id=run_id, urls=urls)
    process.start()  # blocks until the crawl finishes; runs (and stops) the Twisted reactor

    pages = store.load_pages(run_id)
    stored_urls = {p.url for p in pages}
    # Anything that didn't come back as a stored page failed somewhere in the
    # spider (a bad response, no article body, etc.) without necessarily
    # raising here — mark it explicitly rather than silently calling it done.
    for url in urls:
        if url not in stored_urls:
            sync_db.update_item_stage(run_id, url, "failed", "not stored after scraping")

    if not pages:
        sync_db.set_run_status(run_id, "failed")
        return

    sync_db.set_run_status(run_id, "ingesting")
    for p in pages:
        sync_db.update_item_stage(run_id, p.url, "ingesting")
    try:
        episode_uuids = asyncio.run(graph.ingest(user_id, pages))
        for p in pages:
            uuid = episode_uuids.get(p.url)
            if uuid:
                sync_db.set_item_episode_uuid(run_id, p.url, uuid)
            sync_db.update_item_stage(run_id, p.url, "ingested")
        sync_db.set_run_status(run_id, "done")
    except Exception:  # noqa: BLE001
        logger.exception("graph ingestion failed for run %s", run_id)
        for p in pages:
            sync_db.update_item_stage(run_id, p.url, "failed", "graph ingestion failed")
        sync_db.set_run_status(run_id, "failed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=int, required=True)
    args = parser.parse_args()
    run(args.run_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
    sys.exit(0)
