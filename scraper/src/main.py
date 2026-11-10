"""The polite scraper: fetch -> extract -> normalize -> validate -> store -> report.

Run from the scraper/ folder:   python src/main.py
Test failure handling:          python src/main.py --extra-url https://books.toscrape.com/catalogue/not-a-real-book_0/index.html
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch import FetchError, PoliteFetcher  # noqa: E402
from models import Book  # noqa: E402
from parse import dedupe, normalize, parse_book, parse_catalogue  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
START_URL = "https://books.toscrape.com/catalogue/page-1.html"
MAX_CATALOGUE_PAGES = 3


def discover(fetcher: PoliteFetcher, failures: list[dict]) -> tuple[int, list[tuple[str, str]]]:
    """Stage 2. Follow the site's own 'next' links, at most 3 pages.

    Returns (pages visited, [(book_url, catalogue_page_it_was_found_on), ...]).
    """
    found: list[tuple[str, str]] = []
    page_url: str | None = START_URL
    pages = 0
    while page_url and pages < MAX_CATALOGUE_PAGES:
        try:
            result = fetcher.get(page_url)
        except FetchError as exc:
            failures.append({"url": page_url, "stage": "catalogue", "reason": str(exc)})
            break
        pages += 1
        urls, page_url = parse_catalogue(result.html, result.url)
        found.extend((u, result.url) for u in urls)
    return pages, found


def run(extra_urls: list[str], root: Path = ROOT, session=None) -> dict:
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    fetcher = PoliteFetcher(root / "cache", session=session)
    failures: list[dict] = []

    # Stage 2: find every book link, then drop duplicates (first sighting wins).
    catalogue_pages, found = discover(fetcher, failures)
    for url in extra_urls:  # deliberately broken URLs for the Stage 5 test
        found.append((url, "manual"))
    discovered = len(found)
    source_of: dict[str, str] = {}
    for url, source in found:
        source_of.setdefault(url, source)
    unique_urls = dedupe([u for u, _ in found])
    print(f"catalogue_pages={catalogue_pages}, discovered={discovered}, unique_urls={len(unique_urls)}")

    # Stage 3-5: each book is handled on its own, so one bad page cannot stop the run.
    valid: dict[str, dict] = {}  # keyed by canonical URL -> reruns can never duplicate
    errors: list[dict] = []
    detail_pages = 0
    for url in unique_urls:
        try:
            result = fetcher.get(url)
        except FetchError as exc:
            failures.append({"url": url, "stage": "detail", "reason": str(exc)})
            print(f"SKIPPED   {url}: {exc}")
            continue
        detail_pages += 1
        try:
            raw = parse_book(result.html, url, source_of[url], result.fetched_at)
            candidate = normalize(raw)
            valid[url] = Book(**candidate).model_dump()
        except ValidationError as exc:
            reasons = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
            errors.append({"product_url": url, "reasons": reasons, "raw": candidate})
        except Exception as exc:  # parser surprise: log it, keep going
            failures.append({"url": url, "stage": "parse", "reason": f"{type(exc).__name__}: {exc}"})
    print(f"detail_pages={detail_pages}")

    # Store. Files are rewritten each run, so the output is the same after a rerun.
    out = root / "output"
    out.mkdir(exist_ok=True)
    books = [valid[u] for u in sorted(valid)]
    (out / "books.json").write_text(json.dumps(books, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "errors.json").write_text(json.dumps(errors, indent=2, ensure_ascii=False), encoding="utf-8")

    report = {
        "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_seconds": round(time.monotonic() - t0, 2),
        "catalogue_pages": catalogue_pages,
        "unique_urls": len(unique_urls),
        "pages_fetched": fetcher.pages_fetched,
        "cache_hits": fetcher.cache_hits,
        "valid_records": len(books),
        "invalid_records": len(errors),
        "failed_pages": len(failures),
        "failures": failures,
    }
    (out / "run-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "failures"}))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Polite Books to Scrape scraper")
    parser.add_argument("--extra-url", action="append", default=[],
                        help="add a (probably broken) URL to the list to test failure handling")
    args = parser.parse_args()
    run(args.extra_url)


if __name__ == "__main__":
    main()
