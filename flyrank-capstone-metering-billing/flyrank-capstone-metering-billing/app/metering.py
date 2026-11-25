"""MeterService: exactly-once usage recording plus quota enforcement.

No web framework in here. The HTTP layer maps these results and exceptions to status codes.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from app import db, pricing
from app.pricing import TokenUsage

API_CALL = "api_call"
AI_TOKENS = "ai_tokens"


class MeteringError(Exception):
    status_code = 500


class PaymentRequired(MeteringError):
    status_code = 402

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class QuotaExceeded(MeteringError):
    status_code = 429

    def __init__(self, usage_type: str, used: int, requested: int, limit: int, retry_after: int, plan: str):
        self.usage_type, self.used, self.requested, self.limit = usage_type, used, requested, limit
        self.retry_after, self.plan = retry_after, plan
        remaining = max(limit - used, 0)
        self.message = (
            f"Monthly {usage_type} quota exceeded on the {plan} plan: used {used} of {limit}, "
            f"this request needs {requested} but only {remaining} remain. "
            f"Quota resets in {retry_after} seconds. Upgrade to Pro for higher limits."
        )
        super().__init__(self.message)


class IdempotencyConflict(MeteringError):
    status_code = 409

    def __init__(self, key: str):
        self.message = f"Idempotency-Key '{key}' was already used with a different request body."
        super().__init__(self.message)


@dataclass
class MeterResult:
    status: int
    body: dict
    replay: bool  # True when this is the stored response of an earlier identical request


def current_period(now: datetime) -> str:
    return now.strftime("%Y-%m")


def seconds_until_next_period(now: datetime) -> int:
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    next_start = now.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
    return max(int((next_start - now).total_seconds()), 1)


def request_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def used_in_period(conn: sqlite3.Connection, tenant_id: str, period: str, usage_type: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) AS used FROM usage_events "
        "WHERE tenant_id = ? AND period = ? AND usage_type = ?",
        (tenant_id, period, usage_type),
    ).fetchone()
    return int(row["used"])


def record(
    conn: sqlite3.Connection,
    tenant_id: str,
    idempotency_key: str,
    payload: dict,
    tokens: TokenUsage,
    now: datetime | None = None,
) -> MeterResult:
    """One billable action = one API call event, plus one ai_tokens event if tokens were used.

    Order inside a single write-locked transaction:
      1. seen this key before?  same body -> replay stored response; different body -> 409
      2. subscription past_due? -> 402
      3. would any quota be crossed? -> 429 (nothing recorded)
      4. insert ledger row + events, store the response
    """
    tokens.validate()
    now = now or db.utcnow()
    period = current_period(now)
    body_hash = request_hash(payload)

    with db.transaction(conn):
        prior = conn.execute(
            "SELECT request_hash, response_status, response_body FROM idempotency_keys "
            "WHERE tenant_id = ? AND idempotency_key = ?",
            (tenant_id, idempotency_key),
        ).fetchone()
        if prior is not None:
            if prior["request_hash"] != body_hash:
                raise IdempotencyConflict(idempotency_key)
            return MeterResult(prior["response_status"], json.loads(prior["response_body"]), replay=True)

        sub = conn.execute(
            "SELECT s.plan_id, s.status, p.api_calls_limit, p.ai_tokens_limit "
            "FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE s.tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        if sub is None:
            raise MeteringError("tenant has no subscription")
        if sub["status"] == "past_due":
            raise PaymentRequired(
                "Payment required: your subscription is past due. Update your payment method to continue."
            )

        wanted = [(API_CALL, 1, sub["api_calls_limit"])]
        if tokens.total_tokens > 0:
            wanted.append((AI_TOKENS, tokens.total_tokens, sub["ai_tokens_limit"]))

        for usage_type, qty, limit in wanted:
            used = used_in_period(conn, tenant_id, period, usage_type)
            if used + qty > limit:  # exactly AT the limit is still allowed; one over is not
                raise QuotaExceeded(usage_type, used, qty, limit, seconds_until_next_period(now), sub["plan_id"])

        events = [(API_CALL, 1, None, pricing.api_call_cost_pico(1))]
        if tokens.total_tokens > 0:
            events.append((AI_TOKENS, tokens.total_tokens, tokens, pricing.token_cost_pico(tokens)))

        total_pico = sum(e[3] for e in events)
        body = {
            "idempotency_key": idempotency_key,
            "period": period,
            "recorded": {
                "api_calls": 1,
                "ai_tokens": tokens.total_tokens,
                "cost_micro_usd": pricing.pico_to_micro(total_pico),
            },
            "message": "Usage recorded.",
        }

        occurred_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute(
            "INSERT INTO idempotency_keys (tenant_id, idempotency_key, request_hash, response_status, "
            "response_body, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (tenant_id, idempotency_key, body_hash, 200, json.dumps(body, sort_keys=True), occurred_at),
        )
        for usage_type, qty, tok, cost_pico in events:
            conn.execute(
                "INSERT INTO usage_events (tenant_id, idempotency_key, usage_type, quantity, input_tokens, "
                "cached_input_tokens, output_tokens, reasoning_tokens, cost_pico_usd, period, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tenant_id, idempotency_key, usage_type, qty,
                    tok.input_tokens if tok else None,
                    tok.cached_input_tokens if tok else None,
                    tok.output_tokens if tok else None,
                    tok.reasoning_tokens if tok else None,
                    cost_pico, period, occurred_at,
                ),
            )
        return MeterResult(200, body, replay=False)
