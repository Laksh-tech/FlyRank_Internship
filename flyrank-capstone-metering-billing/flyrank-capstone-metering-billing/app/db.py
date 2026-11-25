"""SQLite connection, transactions and a tiny migration runner."""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.config import PLANS

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def connect(path) -> sqlite3.Connection:
    # isolation_level=None = autocommit: we issue BEGIN/COMMIT ourselves, so every transaction is explicit.
    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE takes the write lock up front, so check-then-insert cannot interleave."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply every migrations/*.sql not yet applied, each one atomically. Returns the names applied."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    done = {row["name"] for row in conn.execute("SELECT name FROM schema_migrations")}
    applied = []
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if sql_file.name in done:
            continue
        conn.executescript(
            "BEGIN;\n"
            + sql_file.read_text(encoding="utf-8")
            + f"\nINSERT INTO schema_migrations (name, applied_at) VALUES ('{sql_file.name}', '{utcnow_iso()}');\n"
            + "COMMIT;"
        )
        applied.append(sql_file.name)
    return applied


def ensure_plans(conn: sqlite3.Connection) -> None:
    """Plans are reference data pinned in config; make the table match it."""
    with transaction(conn):
        for plan_id, p in PLANS.items():
            conn.execute(
                "INSERT INTO plans (id, name, api_calls_limit, ai_tokens_limit, monthly_price_cents) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
                "api_calls_limit=excluded.api_calls_limit, ai_tokens_limit=excluded.ai_tokens_limit, "
                "monthly_price_cents=excluded.monthly_price_cents",
                (plan_id, p["name"], p["api_calls_limit"], p["ai_tokens_limit"], p["monthly_price_cents"]),
            )


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def create_tenant(conn: sqlite3.Connection, tenant_id: str, name: str, api_key: str) -> None:
    """Create a tenant on the Free plan. Safe to call twice (idempotent)."""
    now = utcnow_iso()
    with transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO tenants (id, name, api_key_hash, created_at) VALUES (?, ?, ?, ?)",
            (tenant_id, name, hash_api_key(api_key), now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO subscriptions (tenant_id, plan_id, status, updated_at) VALUES (?, 'free', 'active', ?)",
            (tenant_id, now),
        )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
