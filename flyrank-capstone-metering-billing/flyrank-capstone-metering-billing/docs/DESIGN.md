# Design: Usage Metering & Billing Engine (Phase 1 gate)

## Problem

A SaaS needs to answer three questions per customer (tenant): how much did they use this month, what does it
cost, and are they over their plan's limit? Bugs here cost real money (double charges, free unlimited access,
lost revenue), so correctness under retries and failures matters more than size.

## Scope

2 plans (Free, Pro), 2 usage types (API calls, AI tokens), 1 billable endpoint (`POST /generate`).
AI tokens are **simulated**: we meter numbers, we never call a model.

**Explicit non-goals (core):** invoicing, proration, overage billing, real payments (Stripe test mode only),
usage alerts. These are stretch goals and are not built.

## Data model (SQLite, schema in `migrations/`)

| Table | Purpose | Key constraints |
|---|---|---|
| `plans` | limits and price per plan | PK `id` |
| `tenants` | one row per customer; stores a **hash** of its API key | UNIQUE `api_key_hash` |
| `subscriptions` | current plan + status + Stripe ids, one row per tenant | PK `tenant_id`; status in active / past_due / canceled |
| `idempotency_keys` | ledger of accepted requests and the exact response sent | PK (`tenant_id`, `idempotency_key`) |
| `usage_events` | one row per billable thing a request did | UNIQUE (`tenant_id`, `idempotency_key`, `usage_type`); index on (`tenant_id`, `period`, `usage_type`) |
| `stripe_events` | webhook event ids already processed | PK `event_id` |

Every query that touches tenant data filters by `tenant_id`, and the tenant comes only from the API key,
never from a request body or URL.

## Money rules

- All money is an **integer**. No floats anywhere in pricing or storage.
- Prices are pinned in `app/config.py` as **micro-USD per million tokens** (and micro-USD per API call).
- Cost is computed in **pico-USD** (1e-12 USD): `tokens x price_per_million` is already an exact integer.
  Events store pico-USD; a rollup sums exact integers and rounds **once** (half up) to micro-USD. No per-event
  rounding drift.

## Token categories (convention follows Gemini's usage metadata)

| Field | Meaning |
|---|---|
| `input_tokens` | total prompt tokens, **including** cached ones |
| `cached_input_tokens` | the cached part of `input_tokens` (must be <= `input_tokens`) |
| `output_tokens` | visible output |
| `reasoning_tokens` | hidden thinking tokens, **billed as output** |

- fresh input = `input - cached`, billed at the input price
- cached input billed at the cheaper cached price
- output + reasoning billed at the output price
- quota counts **total tokens = input + output + reasoning** (cached is already inside input, so it is not
  added twice)

## Metering API contract and idempotency

`POST /generate` with headers `X-API-Key` and `Idempotency-Key` (required).

Inside one `BEGIN IMMEDIATE` transaction (so concurrent requests are serialized):

1. Key already seen for this tenant?
   - same request body hash -> return the **stored original response** (marked `Idempotent-Replay: true`),
     record nothing.
   - different body hash -> `409` (key reused for a different request).
2. Subscription `past_due` -> `402`.
3. For each usage type: `used_this_month + requested > limit` -> `429`. Nothing is recorded.
4. Otherwise insert the idempotency row and the usage events, build the response, store it. Commit.

A rejected request records nothing, so retrying it later is safe.

## Quota boundary rule (the one the evaluator will probe)

A request is **allowed if `used + requested <= limit`**. With a 1,000-call limit: call 999 is allowed (used
becomes 999), call 1,000 is allowed (used becomes 1,000), call 1,001 is rejected. A token request that would
cross the limit is rejected **whole** (no partial fill).

## Status codes

| Code | When |
|---|---|
| 429 | usage quota exceeded (any plan). Includes `Retry-After` (seconds until the next UTC month) and a hint to upgrade |
| 402 | payment problem: subscription is `past_due` |
| 409 | idempotency key reused with a different request |
| 400 | missing or invalid `Idempotency-Key`; forged or missing webhook signature |
| 401 | missing or unknown API key |
| 422 | invalid body (negative tokens, cached > input, ...) |

## Stripe sync (payment truth lives at Stripe; our DB mirrors verified events)

`POST /billing/checkout` creates a Checkout Session (subscription mode) carrying the tenant id in
`client_reference_id` and in the subscription metadata.
`POST /webhooks/stripe` runs: verify signature on the **raw body** (forged -> 400, nothing changes) ->
insert event id into `stripe_events` and apply the change in the same transaction (replay -> ignored).

| Event | Effect |
|---|---|
| `checkout.session.completed` | tenant -> Pro, active; store Stripe ids |
| `customer.subscription.updated` | map Stripe status: active/trialing -> active, past_due/unpaid -> past_due, canceled -> Free |
| `customer.subscription.deleted` | tenant -> Free, canceled |

Out-of-order delivery: an event older than the last applied one for a subscription is ignored.

## Layers

`api.py` (HTTP only, validation at the boundary) -> `metering.py` / `usage.py` / `stripe_sync.py` / `jobs.py` (business
logic, no web framework imports) -> `db.py` + `migrations/` (persistence).

## Background job (added after the brief's shared requirement #3)

`python -m app.jobs reconcile` asks Stripe for the current state of every stored subscription and repairs any
tenant whose plan/status drifted (a webhook we missed). It is the one slow, network-bound task, so it stays off the
request path. It retries 3 times with 1s/2s backoff, then logs an `ALERT:` line and exits with code 1. Repairs go
through the same out-of-order guard as webhooks. (A reconciliation job was listed as a stretch goal in section 9 of the
brief; a minimal version is built here because the shared requirements ask for at least one background job.)
