"""Find candidate blog post URLs for one root URL, without fetching full content.

Tries, in order, the first that yields at least one candidate:
1. sitemap.xml (gives recency via <lastmod>)
2. RSS/Atom feed (gives recency via publish date, free titles)
3. same-domain link crawl of the root page (no recency signal — approximate)

Nothing here scrapes article bodies; that's ``spider_runner.py`` + ``extract.py``,
and only runs after the user confirms a selection.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import feedparser
import requests
from bs4 import BeautifulSoup

from backend.ingest.models import CANDIDATE_POOL_SIZE, Candidate, MAX_SELECTED_ITEMS

_TIMEOUT = 10
_USER_AGENT = "SignalIngestReader/0.1 (+https://github.com/raunaqness/signal_v2)"
_HEADERS = {"User-Agent": _USER_AGENT}

_SITEMAP_PATHS = ("/sitemap.xml", "/sitemap_index.xml")
_FEED_PATHS = ("/feed", "/feed/", "/rss.xml", "/atom.xml", "/rss")

# Path segments that are almost never an individual post.
_NOISE_SEGMENTS = re.compile(
    r"/(tag|tags|category|categories|author|authors|page|search|wp-content|wp-json|"
    r"feed|comments|login|signup|cart|account)(/|$)",
    re.IGNORECASE,
)
_POST_HINTS = re.compile(r"/(blog|posts?|news|articles?|insights?)/", re.IGNORECASE)
_DATE_SEGMENT = re.compile(r"/\d{4}/\d{1,2}(/\d{1,2})?/")


def discover(
    root_url: str, pool_size: int = CANDIDATE_POOL_SIZE
) -> tuple[list[Candidate], str]:
    """Returns (candidates, discovery_method). Candidates are newest-first
    where a date is known, otherwise in the order found.

    Sitemaps and feeds are often site-wide, not blog-specific — a sitemap for
    ``company.com/blog`` may list every page on the domain. Results are
    narrowed to the blog first (§below); an unscoped sitemap/feed on a large
    site would otherwise hand the user a confirmation list full of pricing
    and legal pages instead of posts.
    """

    for method, fn in (
        ("sitemap", _from_sitemap),
        ("rss", _from_feed),
        ("link_crawl", _from_link_crawl),
    ):
        candidates = fn(root_url)
        if candidates:
            candidates = _dedupe(_narrow_to_blog(root_url, candidates))
            return candidates[:pool_size], method
    return [], "link_crawl"


def _dedupe(candidates: list[Candidate]) -> list[Candidate]:
    seen: set[str] = set()
    out = []
    for c in candidates:
        if c.url in seen:
            continue
        seen.add(c.url)
        out.append(c)
    return out


def _narrow_to_blog(root_url: str, candidates: list[Candidate]) -> list[Candidate]:
    """Prefer URLs under the root's own path (e.g. ``/blog/...``); if that's
    too strict for this site's URL structure, fall back to the blog-shaped
    path heuristic used for the link-crawl fallback. Only widens to the full
    unscoped list if neither filter leaves anything — better an approximate
    list than an empty one."""

    root_path = urlparse(root_url).path.rstrip("/")
    if root_path:
        scoped = [c for c in candidates if urlparse(c.url).path.startswith(root_path + "/")]
        if scoped:
            return scoped

    hinted = [
        c for c in candidates
        if (_POST_HINTS.search(c.url) or _DATE_SEGMENT.search(c.url))
        and not _NOISE_SEGMENTS.search(c.url)
    ]
    return hinted or candidates


def preselect(
    candidates: list[Candidate],
    max_items: int = MAX_SELECTED_ITEMS,
    pool_size: int = CANDIDATE_POOL_SIZE,
) -> list[Candidate]:
    """Sets ``.selected`` on the candidates to review — never decides whether
    confirmation happens (it always does; see runs_store.save_candidates)."""

    pool = candidates[:pool_size]
    for i, c in enumerate(pool):
        c.selected = i < max_items
    return pool


def _same_domain(root_url: str, url: str) -> bool:
    return urlparse(url).netloc == urlparse(root_url).netloc


def _get(url: str) -> requests.Response | None:
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code == 200:
            return resp
    except requests.RequestException:
        pass
    return None


def _from_sitemap(root_url: str) -> list[Candidate] | None:
    for path in _SITEMAP_PATHS:
        resp = _get(urljoin(root_url, path))
        if resp is None:
            continue
        try:
            root = ElementTree.fromstring(resp.content)
        except ElementTree.ParseError:
            continue

        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        # Sitemap index: fetch a handful of the child sitemaps and aggregate.
        sub_sitemaps = [
            el.text for el in root.findall(".//sm:sitemap/sm:loc", ns) if el.text
        ]
        entries: list[Candidate] = []
        if sub_sitemaps:
            for sub_url in sub_sitemaps[:5]:
                sub_resp = _get(sub_url)
                if sub_resp is None:
                    continue
                try:
                    sub_root = ElementTree.fromstring(sub_resp.content)
                except ElementTree.ParseError:
                    continue
                entries.extend(_urls_from_sitemap_xml(sub_root, ns, root_url))
        else:
            entries.extend(_urls_from_sitemap_xml(root, ns, root_url))

        if entries:
            entries.sort(key=_sort_key, reverse=True)
            return entries
    return None


def _urls_from_sitemap_xml(
    root: ElementTree.Element, ns: dict[str, str], root_url: str
) -> list[Candidate]:
    out = []
    for url_el in root.findall(".//sm:url", ns):
        loc_el = url_el.find("sm:loc", ns)
        if loc_el is None or not loc_el.text:
            continue
        url = loc_el.text.strip()
        if not _same_domain(root_url, url) or _NOISE_SEGMENTS.search(url):
            continue
        lastmod_el = url_el.find("sm:lastmod", ns)
        published_at = _parse_date(lastmod_el.text) if lastmod_el is not None else None
        out.append(Candidate(url=url, published_at=published_at))
    return out


def _feed_urls(root_url: str) -> list[str]:
    """Absolute (domain-root) and path-relative feed candidates.

    ``urljoin(root_url, "/feed")`` is an absolute path — it discards any
    existing path on `root_url`, so a blog at ``company.com/blog`` (feed at
    ``company.com/blog/feed``, e.g. WordPress in a subdirectory) or
    ``medium.com/@user`` (feed at ``medium.com/feed/@user``, confirmed live)
    would never be found. Try both forms.
    """

    urls = [urljoin(root_url, path) for path in _FEED_PATHS]
    base = root_url if root_url.endswith("/") else root_url + "/"
    urls += [urljoin(base, path.lstrip("/")) for path in _FEED_PATHS]
    seen: set[str] = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _from_feed(root_url: str) -> list[Candidate] | None:
    for feed_url in _feed_urls(root_url):
        resp = _get(feed_url)
        if resp is None:
            continue
        parsed = feedparser.parse(resp.content)
        if not parsed.entries:
            continue
        entries = []
        for entry in parsed.entries:
            url = entry.get("link")
            if not url or not _same_domain(root_url, url):
                continue
            published_at = None
            if entry.get("published_parsed"):
                published_at = datetime(*entry["published_parsed"][:6], tzinfo=timezone.utc)
            entries.append(
                Candidate(url=url, title=entry.get("title"), published_at=published_at)
            )
        if entries:
            entries.sort(key=_sort_key, reverse=True)
            return entries
    return None


def _from_link_crawl(root_url: str) -> list[Candidate]:
    resp = _get(root_url)
    if resp is None:
        return []
    soup = BeautifulSoup(resp.text, "lxml")
    seen: set[str] = set()
    out: list[Candidate] = []
    for a in soup.find_all("a", href=True):
        url = urljoin(root_url, a["href"]).split("#")[0]
        if url in seen or not _same_domain(root_url, url):
            continue
        if _NOISE_SEGMENTS.search(url):
            continue
        if not (_POST_HINTS.search(url) or _DATE_SEGMENT.search(url)):
            continue
        seen.add(url)
        title = a.get_text(strip=True) or None
        out.append(Candidate(url=url, title=title))
    return out


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _sort_key(c: Candidate) -> datetime:
    return c.published_at or datetime.min.replace(tzinfo=timezone.utc)
