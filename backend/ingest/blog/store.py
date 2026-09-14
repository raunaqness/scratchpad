"""Local file storage for scraped page content, under SIGNAL_DATA_DIR.

Run/item *state* (status, stage, selection) lives in Postgres — see
``backend/ingest/runs_store.py``. This module only persists the scraped
*content* itself, one JSON file per page, so it's easy to inspect and so
``graph.py`` can load it independently of the run's DB state.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from backend.config import settings
from backend.ingest.models import Page

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def domain_slug(url: str) -> str:
    """Graphiti's group_id only allows alphanumerics, dashes, underscores —
    no dots, so ``stripe.com`` becomes ``stripe-com``."""

    return _SLUG_RE.sub("-", urlparse(url).netloc.lower()).strip("-")


def _url_slug(url: str) -> str:
    path = urlparse(url).path.strip("/")
    slug = _SLUG_RE.sub("-", path.lower()).strip("-") or "index"
    return slug[:120]


def pages_dir(run_id: int) -> Path:
    d = settings.data_dir / "ingest" / f"run-{run_id}" / "pages"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_page(run_id: int, page: Page) -> Path:
    path = pages_dir(run_id) / f"{_url_slug(page.url)}.json"
    path.write_text(page.model_dump_json(indent=2))
    return path


def load_pages(run_id: int) -> list[Page]:
    d = pages_dir(run_id)
    pages = []
    for path in sorted(d.glob("*.json")):
        pages.append(Page.model_validate_json(path.read_text()))
    return pages


def delete_page(run_id: int, url: str) -> None:
    path = pages_dir(run_id) / f"{_url_slug(url)}.json"
    path.unlink(missing_ok=True)
