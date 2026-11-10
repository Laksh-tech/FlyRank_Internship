import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from models import Book  # noqa: E402
from parse import dedupe, normalize, parse_book, parse_catalogue, parse_price  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
PRODUCT_URL = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
SOURCE = "https://books.toscrape.com/catalogue/page-1.html"
NOW = "2026-11-10T10:00:00Z"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_price_normalization():
    assert parse_price("£51.77") == 51.77
    assert parse_price("£1,024.50") == 1024.50
    assert parse_price("Â£13.99") == 13.99  # survives a mis-decoded pound sign
    assert parse_price("free!") is None
    assert parse_price(None) is None


def test_relative_urls_become_absolute():
    urls, next_url = parse_catalogue(load("catalogue_page.html"), SOURCE)
    assert urls[0] == "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
    assert all(u.startswith("https://") for u in urls)
    assert next_url == "https://books.toscrape.com/catalogue/page-2.html"


def test_duplicate_urls_are_removed_in_order():
    urls, _ = parse_catalogue(load("catalogue_page.html"), SOURCE)
    assert len(urls) == 3
    unique = dedupe(urls)
    assert len(unique) == 2
    assert unique[0].endswith("a-light-in-the-attic_1000/index.html")


def test_full_page_has_eight_keys_and_ignores_table_prices():
    raw = parse_book(load("book_full.html"), PRODUCT_URL, SOURCE, NOW)
    assert len(raw) == 8
    assert raw["title"] == "A Light in the Attic"  # whitespace trimmed
    assert raw["price_text"] == "£51.77"            # not the £99.99 in the table
    assert raw["availability_text"] == "In stock (22 available)"
    assert raw["rating_text"] == "Three"
    assert raw["description"].startswith("It's hard to imagine")
    assert "  " not in raw["description"]            # extra spaces collapsed
    Book(**normalize(raw))                           # passes the schema


def test_missing_description_is_null_not_invented():
    raw = parse_book(load("book_no_description.html"), PRODUCT_URL, SOURCE, NOW)
    assert raw["description"] is None
    assert "description" in raw                      # the key is still there
    book = Book(**normalize(raw))
    assert book.description is None
    assert book.price_gbp == 1024.50


def test_malformed_page_fails_validation_instead_of_slipping_through():
    raw = parse_book(load("book_malformed.html"), PRODUCT_URL, SOURCE, NOW)
    with pytest.raises(ValidationError) as info:
        Book(**normalize(raw))
    failing_fields = {e["loc"][0] for e in info.value.errors()}
    assert {"price_gbp", "rating_text", "availability_text"} <= failing_fields


def test_non_https_url_is_rejected():
    raw = parse_book(load("book_full.html"), "http://example.com/x", SOURCE, NOW)
    with pytest.raises(ValidationError):
        Book(**normalize(raw))
