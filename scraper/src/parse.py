"""Stages 2-4: turn HTML into links and raw records, then normalize raw strings."""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

RATING_WORDS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}


def _text(node) -> str | None:
    if node is None:
        return None
    text = " ".join(node.get_text(" ", strip=True).split())  # collapse stray whitespace
    return text or None


# ---- Stage 2: discovery -------------------------------------------------------
def parse_catalogue(html: str, page_url: str) -> tuple[list[str], str | None]:
    """Return (absolute book URLs on this page, absolute 'next' URL or None)."""
    soup = BeautifulSoup(html, "html.parser")
    book_urls = [
        urljoin(page_url, a["href"])  # never glue strings together
        for a in soup.select("article.product_pod h3 a[href]")
    ]
    next_link = soup.select_one("li.next a[href]")
    next_url = urljoin(page_url, next_link["href"]) if next_link else None
    return book_urls, next_url


def dedupe(urls: list[str]) -> list[str]:
    """Remove duplicates but keep the original order."""
    return list(dict.fromkeys(urls))


# ---- Stage 3: raw extraction --------------------------------------------------
def parse_book(html: str, product_url: str, source_page: str, fetched_at: str) -> dict:
    """Extract the eight raw fields. Missing values become None, never invented text."""
    soup = BeautifulSoup(html, "html.parser")
    # Aim at the product area only. The page also has a table with other prices.
    product = soup.select_one("article.product_page div.product_main") or soup

    rating_p = product.select_one("p.star-rating")
    rating_text = None
    if rating_p is not None:
        classes = [c for c in rating_p.get("class", []) if c != "star-rating"]
        rating_text = classes[0] if classes else None

    # The description paragraph sits right after the "Product Description" header.
    description = _text(soup.select_one("#product_description + p"))

    return {
        "title": _text(product.select_one("h1")),
        "product_url": product_url,
        "price_text": _text(product.select_one("p.price_color")),
        "availability_text": _text(product.select_one("p.availability")),
        "rating_text": rating_text,
        "description": description,
        "source_page": source_page,
        "fetched_at": fetched_at,
    }


# ---- Stage 4: normalization ---------------------------------------------------
def parse_price(price_text: str | None) -> float | None:
    """'£51.77' -> 51.77. Returns None if no number is found (validation will catch it)."""
    if not price_text:
        return None
    match = re.search(r"\d+(?:,\d{3})*(?:\.\d+)?", price_text)
    return float(match.group().replace(",", "")) if match else None


def normalize(raw: dict) -> dict:
    """Raw record -> candidate clean record. Raw values are kept next to clean ones."""
    clean = dict(raw)
    clean["price_gbp"] = parse_price(raw.get("price_text"))
    return clean
