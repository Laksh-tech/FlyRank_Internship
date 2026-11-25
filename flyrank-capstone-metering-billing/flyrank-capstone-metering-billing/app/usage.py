"""Read path: roll usage events up into { used, limit, cost } for one tenant and month."""
from __future__ import annotations

import sqlite3
from datetime import datetime

from app import db, pricing
from app.metering import AI_TOKENS, API_CALL, current_period


def rollup(conn: sqlite3.Connection, tenant_id: str, now: datetime | None = None) -> dict:
    now = now or db.utcnow()
    period = current_period(now)

    sub = conn.execute(
        "SELECT s.plan_id, s.status, p.api_calls_limit, p.ai_tokens_limit "
        "FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE s.tenant_id = ?",
        (tenant_id,),
    ).fetchone()

    sums = {
        r["usage_type"]: r
        for r in conn.execute(
            "SELECT usage_type, SUM(quantity) AS qty, SUM(cost_pico_usd) AS pico, "
            "COALESCE(SUM(input_tokens), 0) AS inp, COALESCE(SUM(cached_input_tokens), 0) AS cached, "
            "COALESCE(SUM(output_tokens), 0) AS outp, COALESCE(SUM(reasoning_tokens), 0) AS reasoning "
            "FROM usage_events WHERE tenant_id = ? AND period = ? GROUP BY usage_type",
            (tenant_id, period),
        )
    }
    calls = sums.get(API_CALL)
    toks = sums.get(AI_TOKENS)
    calls_pico = int(calls["pico"]) if calls else 0
    toks_pico = int(toks["pico"]) if toks else 0

    return {
        "tenant_id": tenant_id,
        "period": period,
        "plan": sub["plan_id"],
        "status": sub["status"],
        "api_calls": {"used": int(calls["qty"]) if calls else 0, "limit": sub["api_calls_limit"]},
        "ai_tokens": {
            "used": int(toks["qty"]) if toks else 0,
            "limit": sub["ai_tokens_limit"],
            "breakdown": {
                "input_tokens": int(toks["inp"]) if toks else 0,
                "cached_input_tokens": int(toks["cached"]) if toks else 0,
                "output_tokens": int(toks["outp"]) if toks else 0,
                "reasoning_tokens": int(toks["reasoning"]) if toks else 0,
            },
        },
        # Each figure is the exact pico-USD sum rounded once; total is rounded from the exact sum.
        "cost_micro_usd": {
            "api_calls": pricing.pico_to_micro(calls_pico),
            "ai_tokens": pricing.pico_to_micro(toks_pico),
            "total": pricing.pico_to_micro(calls_pico + toks_pico),
        },
        "cost_usd_display": pricing.format_usd(pricing.pico_to_micro(calls_pico + toks_pico)),
    }
