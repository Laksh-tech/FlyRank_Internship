"""End-to-end tests against a fake in-memory copy of the site. No network needed."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import main  # noqa: E402

BASE = "https://books.toscrape.com/catalogue/"


def book_html(n: int) -> str:
    return f"""<article class="product_page"><div class="row"><div class="col-sm-6 product_main">
    <h1>Book {n}</h1><p class="price_color">£{n}.50</p>
    <p class="instock availability"><i class="icon-ok"></i> In stock (5 available) </p>
    <p class="star-rating Four"></p></div></div>
    <div id="product_description" class="sub-header"><h2>Product Description</h2></div>
    <p>Description {n}</p></article>"""


def catalogue_html(page: int) -> str:
    items = "".join(
        f'<li><article class="product_pod"><h3><a href="book-{n}_{n}/index.html">x</a></h3></article></li>'
        for n in range((page - 1) * 20, page * 20)
    )
    # every page links to a next one, so only our own "stop at 3" rule ends the crawl
    return f'<ol>{items}</ol><ul class="pager"><li class="next"><a href="page-{page + 1}.html">next</a></li></ul>'


class FakeResponse:
    def __init__(self, status: int, text: str = ""):
        self.status_code, self.text, self.encoding = status, text, None


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.calls: list[str] = []

    def get(self, url, timeout=None):
        assert timeout, "every request must have a timeout"
        assert "FlyRank" in self.headers.get("User-Agent", ""), "every request must identify itself"
        self.calls.append(url)
        rel = url.removeprefix(BASE)
        if rel.startswith("page-"):
            return FakeResponse(200, catalogue_html(int(rel[5:-5])))
        if rel.startswith("book-"):
            return FakeResponse(200, book_html(int(rel.split("_")[1].split("/")[0])))
        if "server-error" in rel:
            return FakeResponse(503)
        return FakeResponse(404)


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    monkeypatch.setattr("fetch.time.sleep", lambda s: None)


def read(root: Path, name: str):
    return json.loads((root / "output" / name).read_text(encoding="utf-8"))


def test_clean_run_then_rerun_is_idempotent(tmp_path):
    first_session = FakeSession()
    report = main.run([], root=tmp_path, session=first_session)
    assert report["catalogue_pages"] == 3
    assert report["unique_urls"] == 60
    assert report["valid_records"] == 60
    assert report["failed_pages"] == 0
    first = read(tmp_path, "books.json")
    assert len(first) == 60
    assert all(isinstance(b["price_gbp"], float) for b in first)
    assert all(b["product_url"].startswith("https://") for b in first)
    assert not any("page-4" in u for u in first_session.calls), "must stop after page 3"

    second_session = FakeSession()
    report2 = main.run([], root=tmp_path, session=second_session)
    assert second_session.calls == [], "rerun should be served entirely from cache"
    assert report2["cache_hits"] == 63
    second = read(tmp_path, "books.json")
    assert second == first  # same 60 records, identical content


def test_broken_pages_are_skipped_and_reported(tmp_path):
    session = FakeSession()
    bad_404 = BASE + "not-a-real-book_0/index.html"
    bad_503 = BASE + "server-error_1/index.html"
    report = main.run([bad_404, bad_503], root=tmp_path, session=session)
    assert report["failed_pages"] == 2
    assert report["valid_records"] == 60  # the good records survived
    assert len(read(tmp_path, "books.json")) == 60
    assert session.calls.count(bad_404) == 1, "a 404 must not be retried"
    assert session.calls.count(bad_503) == 2, "a 5xx gets exactly one retry"
    assert {f["url"] for f in report["failures"]} == {bad_404, bad_503}


def test_duplicate_extra_url_counts_once(tmp_path):
    existing = BASE + "book-0_0/index.html"
    report = main.run([existing], root=tmp_path, session=FakeSession())
    assert report["unique_urls"] == 60
    assert report["valid_records"] == 60
