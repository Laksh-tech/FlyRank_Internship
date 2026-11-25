"""Pinned constants (plans, prices) and environment-driven settings. No secrets live in this file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # python-dotenv is optional: without it, real environment variables still work
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# ---- plans (the single source of truth; copied into the `plans` table at startup) ----------------
PLANS = {
    "free": {"name": "Free", "api_calls_limit": 1_000, "ai_tokens_limit": 100_000, "monthly_price_cents": 0},
    "pro": {"name": "Pro", "api_calls_limit": 100_000, "ai_tokens_limit": 10_000_000, "monthly_price_cents": 2_000},
}

# ---- pricing, in micro-USD (1e-6 USD). Integers only. -----------------------------------------------
INPUT_MICRO_USD_PER_MTOK = 3_000_000          # $3.00  per 1M fresh input tokens
CACHED_INPUT_MICRO_USD_PER_MTOK = 300_000     # $0.30  per 1M cached input tokens (10x cheaper)
OUTPUT_MICRO_USD_PER_MTOK = 15_000_000        # $15.00 per 1M output tokens (reasoning bills the same)
API_CALL_MICRO_USD = 2_000                    # $0.002 per billable API call

# Sanity cap on a single request, so absurd numbers become a clean 422 instead of silent overflow.
MAX_TOKENS_PER_REQUEST = 10_000_000

# Demo tenants for `python -m app.seed`. These are throwaway demo keys, not secrets.
DEMO_TENANTS = {
    "alpha": {"name": "Alpha Inc (demo)", "api_key": "demo-key-alpha"},
    "beta": {"name": "Beta LLC (demo)", "api_key": "demo-key-beta"},
}


@dataclass(frozen=True)
class Settings:
    database_path: str = "billing.db"
    # repr=False: printing or logging a Settings object can never leak a secret
    stripe_secret_key: str = field(default="", repr=False)
    stripe_webhook_secret: str = field(default="", repr=False)
    stripe_pro_price_id: str = ""
    public_base_url: str = "http://localhost:8080"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_path=os.environ.get("DATABASE_PATH", "billing.db"),
            stripe_secret_key=os.environ.get("STRIPE_SECRET_KEY", ""),
            stripe_webhook_secret=os.environ.get("STRIPE_WEBHOOK_SECRET", ""),
            stripe_pro_price_id=os.environ.get("STRIPE_PRO_PRICE_ID", ""),
            public_base_url=os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/"),
        )


ROOT = Path(__file__).resolve().parent.parent
