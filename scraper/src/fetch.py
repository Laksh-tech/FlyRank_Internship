"""Stage 1 + 5: polite fetching with a cache, timeout, status check and one retry."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

# Replace the link with your own public repo URL before you publish.
USER_AGENT = "FlyRankInternshipA9/1.0 (+https://github.com/Laksh-tech/scraper)"
TIMEOUT_SECONDS = 10
MIN_DELAY_SECONDS = 0.6  # the assignment requires at least 0.5 s between real requests
RETRY_WAIT_SECONDS = 2.0


class FetchError(Exception):
    """A page could not be fetched. The message says why."""


@dataclass
class FetchResult:
    url: str
    html: str
    fetched_at: str  # ISO 8601, UTC. For a cache hit this is when the file was first saved.
    from_cache: bool


def cache_name_for(url: str) -> str:
    """'https://books.toscrape.com/catalogue/page-1.html' -> 'catalogue-page-1.html'."""
    path = urlparse(url).path.strip("/")
    return path.replace("/", "-") or "index.html"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PoliteFetcher:
    def __init__(self, cache_dir: Path, delay: float = MIN_DELAY_SECONDS, session=None):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay = max(delay, 0.5)
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._last_request_at = 0.0
        # counters for the run report
        self.pages_fetched = 0
        self.cache_hits = 0

    # -- public ---------------------------------------------------------------
    def get(self, url: str) -> FetchResult:
        cache_file = self.cache_dir / cache_name_for(url)
        if cache_file.exists():
            html = cache_file.read_text(encoding="utf-8")
            self.cache_hits += 1
            print(f"CACHE HIT {url} ({len(html)} chars)")
            return FetchResult(url, html, _iso(cache_file.stat().st_mtime), True)

        html = self._download_with_one_retry(url)
        cache_file.write_text(html, encoding="utf-8")  # only successful 200s are cached
        self.pages_fetched += 1
        print(f"FETCH     {url} ({len(html)} chars)")
        return FetchResult(url, html, _iso(time.time()), False)

    # -- internals ------------------------------------------------------------
    def _wait_your_turn(self) -> None:
        wait = self._last_request_at + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _download_with_one_retry(self, url: str) -> str:
        try:
            return self._download(url)
        except _Retryable as first_problem:
            print(f"RETRY     {url} ({first_problem}) - waiting {RETRY_WAIT_SECONDS}s")
            time.sleep(RETRY_WAIT_SECONDS)
            try:
                return self._download(url)
            except _Retryable as second_problem:
                raise FetchError(f"{second_problem} (after one retry)") from None

    def _download(self, url: str) -> str:
        self._wait_your_turn()
        try:
            resp = self.session.get(url, timeout=TIMEOUT_SECONDS)
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise _Retryable(f"{type(exc).__name__}") from exc
        except requests.RequestException as exc:
            raise FetchError(f"request failed: {exc}") from exc

        status = resp.status_code
        if status == 200:
            resp.encoding = "utf-8"  # the sandbox serves UTF-8; stops '£' turning into 'Â£'
            return resp.text
        if status >= 500:
            raise _Retryable(f"HTTP {status}")
        # 404 = does not exist, 403 = the site said no. Asking again would not help.
        raise FetchError(f"HTTP {status}")


class _Retryable(Exception):
    """Internal: timeouts, connection problems and 5xx. Worth exactly one more try."""
