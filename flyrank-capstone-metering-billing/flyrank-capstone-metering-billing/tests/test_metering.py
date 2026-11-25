import sqlite3
import threading
from datetime import datetime, timezone

import pytest

from app import db, metering, usage
from app.metering import IdempotencyConflict, PaymentRequired, QuotaExceeded
from app.pricing import TokenUsage

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
NO_TOKENS = TokenUsage()


def count(conn, table="usage_events", where="1=1"):
    return conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}").fetchone()["n"]


def call(conn, tenant, key, tokens=NO_TOKENS, payload=None, now=NOW):
    return metering.record(conn, tenant, key, payload if payload is not None else {"k": key}, tokens, now)


# ---- PROBE 1: idempotency ------------------------------------------------------------------
def test_same_key_twice_records_one_event_and_mirrors_response(conn):
    tokens = TokenUsage(input_tokens=100, output_tokens=50)
    first = call(conn, "t_alpha", "req-1", tokens, {"a": 1})
    second = call(conn, "t_alpha", "req-1", tokens, {"a": 1})
    assert first.replay is False and second.replay is True
    assert second.body == first.body and second.status == first.status
    assert count(conn, where="tenant_id='t_alpha'") == 2          # 1 api_call + 1 ai_tokens, not 4
    assert usage.rollup(conn, "t_alpha", NOW)["api_calls"]["used"] == 1


def test_fifty_retries_still_one_event(conn):
    for _ in range(50):
        call(conn, "t_alpha", "same-key", payload={"x": 1})
    assert usage.rollup(conn, "t_alpha", NOW)["api_calls"]["used"] == 1


def test_same_key_different_body_is_a_conflict(conn):
    call(conn, "t_alpha", "k", payload={"a": 1})
    with pytest.raises(IdempotencyConflict):
        call(conn, "t_alpha", "k", payload={"a": 2})
    assert usage.rollup(conn, "t_alpha", NOW)["api_calls"]["used"] == 1


def test_same_key_for_two_tenants_is_independent(conn):
    call(conn, "t_alpha", "shared-key")
    call(conn, "t_beta", "shared-key")
    assert usage.rollup(conn, "t_alpha", NOW)["api_calls"]["used"] == 1
    assert usage.rollup(conn, "t_beta", NOW)["api_calls"]["used"] == 1


def test_database_itself_refuses_duplicate_events(conn):
    call(conn, "t_alpha", "k")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO usage_events (tenant_id, idempotency_key, usage_type, quantity, cost_pico_usd, period, "
            "occurred_at) VALUES ('t_alpha', 'k', 'api_call', 1, 0, '2026-11', 'x')"
        )


def test_concurrent_retries_create_exactly_one_event(conn, tmp_path):
    """Eight threads fire the same request at once, each on its own connection to the same file."""
    path = conn.execute("PRAGMA database_list").fetchone()["file"]
    results, errors = [], []

    def worker():
        c = db.connect(path)
        try:
            results.append(call(c, "t_alpha", "race-key", payload={"r": 1}).replay)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert results.count(False) == 1 and results.count(True) == 7
    assert usage.rollup(conn, "t_alpha", NOW)["api_calls"]["used"] == 1


# ---- PROBE 2: quota boundary ----------------------------------------------------------------
def test_boundary_999_1000_1001(conn):
    for i in range(999):
        call(conn, "t_alpha", f"c{i}")
    assert usage.rollup(conn, "t_alpha", NOW)["api_calls"]["used"] == 999
    call(conn, "t_alpha", "c999")                                    # call #1000 sits exactly AT the limit: allowed
    assert usage.rollup(conn, "t_alpha", NOW)["api_calls"]["used"] == 1000
    with pytest.raises(QuotaExceeded) as info:                       # call #1001: rejected
        call(conn, "t_alpha", "c1000")
    err = info.value
    assert err.status_code == 429 and err.used == 1000 and err.limit == 1000
    assert "quota exceeded" in err.message and "Upgrade to Pro" in err.message
    assert err.retry_after > 0
    assert usage.rollup(conn, "t_alpha", NOW)["api_calls"]["used"] == 1000, "a rejected request records nothing"


def test_rejected_request_does_not_burn_its_idempotency_key(conn):
    for i in range(1000):
        call(conn, "t_alpha", f"c{i}")
    with pytest.raises(QuotaExceeded):
        call(conn, "t_alpha", "late")
    assert count(conn, "idempotency_keys", "idempotency_key='late'") == 0


def test_token_request_crossing_the_limit_is_rejected_whole(conn):
    call(conn, "t_alpha", "a", TokenUsage(output_tokens=99_000))
    call(conn, "t_alpha", "b", TokenUsage(output_tokens=1_000))      # 100,000 exactly: allowed
    with pytest.raises(QuotaExceeded) as info:
        call(conn, "t_alpha", "c", TokenUsage(output_tokens=1))
    assert info.value.usage_type == "ai_tokens"
    assert usage.rollup(conn, "t_alpha", NOW)["ai_tokens"]["used"] == 100_000


def test_token_quota_rejection_does_not_consume_an_api_call(conn):
    with pytest.raises(QuotaExceeded):
        call(conn, "t_alpha", "huge", TokenUsage(output_tokens=100_001))
    assert usage.rollup(conn, "t_alpha", NOW)["api_calls"]["used"] == 0


def test_tokens_count_input_output_and_reasoning_but_not_cached_twice(conn):
    call(conn, "t_alpha", "a", TokenUsage(input_tokens=1_000, cached_input_tokens=400, output_tokens=100, reasoning_tokens=50))
    assert usage.rollup(conn, "t_alpha", NOW)["ai_tokens"]["used"] == 1_150


def test_quota_resets_next_month_and_old_usage_is_kept(conn):
    for i in range(1000):
        call(conn, "t_alpha", f"c{i}")
    december = datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc)
    call(conn, "t_alpha", "dec-1", now=december)                     # new period, allowed
    assert usage.rollup(conn, "t_alpha", december)["api_calls"]["used"] == 1
    assert usage.rollup(conn, "t_alpha", NOW)["api_calls"]["used"] == 1000


def test_retry_after_counts_down_to_the_next_month():
    assert metering.seconds_until_next_period(datetime(2026, 11, 30, 23, 59, 0, tzinfo=timezone.utc)) == 60
    assert metering.seconds_until_next_period(datetime(2026, 12, 31, 23, 59, 0, tzinfo=timezone.utc)) == 60


# ---- 402 and plans ------------------------------------------------------------------------------
def test_past_due_subscription_gets_402(conn):
    conn.execute("UPDATE subscriptions SET status='past_due' WHERE tenant_id='t_alpha'")
    with pytest.raises(PaymentRequired) as info:
        call(conn, "t_alpha", "x")
    assert info.value.status_code == 402 and "past due" in info.value.message


def test_pro_plan_has_higher_limits(conn):
    conn.execute("UPDATE subscriptions SET plan_id='pro' WHERE tenant_id='t_alpha'")
    for i in range(1001):
        call(conn, "t_alpha", f"c{i}")
    assert usage.rollup(conn, "t_alpha", NOW)["api_calls"] == {"used": 1001, "limit": 100_000}


# ---- tenant isolation -------------------------------------------------------------------------------
def test_tenants_never_see_each_others_usage(conn):
    call(conn, "t_alpha", "a1", TokenUsage(output_tokens=500))
    beta = usage.rollup(conn, "t_beta", NOW)
    assert beta["api_calls"]["used"] == 0 and beta["ai_tokens"]["used"] == 0
    assert beta["cost_micro_usd"]["total"] == 0


# ---- PROBE 5: rollup matches the pinned pricing ----------------------------------------------------------
def test_rollup_cost_matches_hand_calculation(conn):
    call(conn, "t_alpha", "r1", TokenUsage(input_tokens=10_000, cached_input_tokens=4_000, output_tokens=2_000, reasoning_tokens=3_000))
    call(conn, "t_alpha", "r2")                                     # a plain API call, no tokens
    r = usage.rollup(conn, "t_alpha", NOW)
    assert r["api_calls"]["used"] == 2
    assert r["ai_tokens"]["used"] == 15_000
    assert r["ai_tokens"]["breakdown"] == {"input_tokens": 10_000, "cached_input_tokens": 4_000, "output_tokens": 2_000, "reasoning_tokens": 3_000}
    assert r["cost_micro_usd"] == {"api_calls": 4_000, "ai_tokens": 94_200, "total": 98_200}
    assert r["cost_usd_display"] == "$0.098200"
