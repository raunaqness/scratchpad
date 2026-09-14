"""Turn raw scraped HTML into a structured Page. Pure functions, no I/O."""

from __future__ import annotations

import trafilatura

from backend.ingest.models import Page

_EXCERPT_LEN = 300


def extract(url: str, html: str, fetched_at: str) -> Page | None:
    """Returns None if no discernible article body was found — nav/index pages
    that slipped through discovery don't get stored and don't count toward the
    selected-page total."""

    text = trafilatura.extract(html, url=url, favor_precision=True)
    if not text or not text.strip():
        return None

    metadata = trafilatura.extract_metadata(html, default_url=url)
    title = metadata.title if metadata else None
    author = metadata.author if metadata else None
    published_at = metadata.date if metadata else None

    return Page(
        url=url,
        title=title,
        published_at=published_at,
        author=author,
        text=text,
        excerpt=_excerpt(text),
        word_count=len(text.split()),
        fetched_at=fetched_at,
    )


def _excerpt(text: str, n: int = _EXCERPT_LEN) -> str:
    text = text.strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"
