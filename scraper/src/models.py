"""Stage 4: the schema. A record that does not match never reaches books.json."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, field_validator

VALID_RATINGS = {"One", "Two", "Three", "Four", "Five"}


class Book(BaseModel):
    title: str
    product_url: str  # canonical URL = the record's identity
    price_text: str
    price_gbp: float
    availability_text: str
    rating_text: str
    description: Optional[str] = None  # the only optional field
    source_page: str
    fetched_at: str

    @field_validator("title", "price_text", "availability_text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @field_validator("product_url", "source_page")
    @classmethod
    def must_be_https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("must be an absolute https:// URL")
        return v

    @field_validator("price_gbp")
    @classmethod
    def price_not_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("price must not be negative")
        return v

    @field_validator("rating_text")
    @classmethod
    def known_rating(cls, v: str) -> str:
        if v not in VALID_RATINGS:
            raise ValueError(f"rating must be one of {sorted(VALID_RATINGS)}")
        return v
