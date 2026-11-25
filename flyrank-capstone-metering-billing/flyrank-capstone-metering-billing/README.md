# Usage Metering & Billing Engine

FlyRank Internship · Backend Track · Capstone.

A small backend service that answers the three questions every SaaS must answer per customer (tenant):
**how much have they used, what does it cost, and have they hit their plan limit?**
Correctness under retries, boundaries and replayed webhooks matters more than size, so the scope is deliberately small:
2 plans, 2 usage types, 1 billable endpoint, Stripe in **test mode** only.

## What it does

| Concern | How |
|---|---|
| **Metering** | `POST /generate` records one `api_call` event and one `ai_tokens` event. The `Idempotency-Key` header makes a retried request count **once**; the retry gets the original response back. |
| **Quotas** | Before recording, `used + requested <= limit` is checked inside one write-locked transaction. Over the limit: `429` (quota) or `402` (payment problem), with a message that says why. |
| **Cost** | `GET /usage` rolls the month up to `{ used, limit, cost }`. AI tokens are priced per category (fresh input, cached input, output+reasoning), in exact integers. |
| **Stripe** | `POST /billing/checkout` starts a Checkout Session. `POST /webhooks/stripe` verifies the signature, ignores duplicate events, and updates the tenant's plan/status. |
| **Background job** | `python -m app.jobs reconcile` compares our subscriptions with Stripe's and repairs any missed webhook (3 retries, then an `ALERT` log line + exit code 1). |

## Architecture

```
Client ──► POST /generate  (X-API-Key, Idempotency-Key)
             │  api.py       validate input → clean 4xx, never a 500
             ▼
           metering.record()      one BEGIN IMMEDIATE transaction:
             1. key seen before?   same body → replay the stored response (no new event)
                                   different body → 409
             2. subscription past_due?  → 402
             3. used + requested > limit?  → 429 + Retry-After   (nothing recorded)
             4. insert idempotency row + usage events, store the response → commit
                                     │
GET /usage ◄── usage.rollup() ◄──────┘  sums events → { used, limit, cost }

Customer ──► Stripe Checkout (test mode) ──► subscription created at Stripe
Stripe ──signed webhook──► POST /webhooks/stripe
             │  stripe_sync.verify_signature()   forged / stale / unsigned → 400, nothing changes
             │  stripe_events insert + plan update in ONE transaction   (replay → "duplicate")
             ▼
           subscriptions table  (tenant plan + status mirror Stripe's verified events)

Background: app/jobs.py reconcile ──► Stripe API ──► repairs drift from missed webhooks
```

Layers: `api.py` (HTTP only) → `metering.py` / `usage.py` / `stripe_sync.py` / `jobs.py` (business logic, no web
framework) → `db.py` + `migrations/` (persistence). Full reasoning: [docs/DESIGN.md](docs/DESIGN.md).

## Plans and quotas

| Plan | API calls / month | AI tokens / month | Price |
|---|---|---|---|
| Free | 1,000 | 100,000 | $0 |
| Pro | 100,000 | 10,000,000 | $20 / month |

Both are defined once in `app/config.py` and copied into the `plans` table at startup. The month is a **calendar
month in UTC**; quotas reset on the 1st. AI-token quota counts `input + output + reasoning` tokens (cached tokens are
part of input, so they are not counted twice).

## Pricing (pinned in `app/config.py`, integers only)

| Item | Price |
|---|---|
| Fresh input tokens | $3.00 / 1M |
| Cached input tokens | $0.30 / 1M (10× cheaper) |
| Output tokens | $15.00 / 1M |
| Reasoning tokens | billed as output: $15.00 / 1M |
| API call | $0.002 each |

Money is stored and computed as integers, never floats. Costs are exact **pico-USD** per event and rounded **once**
(half up) to micro-USD when rolled up, so thousands of tiny events cannot drift.
Worked example (the one in `EVIDENCE.md`): 10,000 input (4,000 cached) + 2,000 output + 3,000 reasoning
= 6,000×$3/M + 4,000×$0.30/M + 5,000×$15/M = **$0.0942**.

### Quota boundary rule (stated exactly)

A request is allowed if `used + requested <= limit`. With a 1,000-call limit: call 999 ✔, call **1,000 ✔ (exactly at the
limit)**, call 1,001 ✘ `429`. A token request that would cross the limit is rejected **whole** (no partial fill), and a
rejected request records nothing, so retrying it later is safe.

| Status | Meaning |
|---|---|
| `429` | usage quota exceeded (any plan). Has `Retry-After` and an upgrade hint |
| `402` | payment problem: the Stripe subscription is `past_due` / `unpaid` |
| `409` | an Idempotency-Key was reused with a different request body |
| `400` / `401` / `413` / `422` | bad header or JSON · bad/missing API key · body too big · invalid values |

## Run it (clean machine, ~2 minutes)

Requires Python 3.10+.

```bash
git clone <your-repo-url> && cd flyrank-capstone-metering-billing
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # Stripe values are only needed for checkout/webhooks

python -m app.seed       # SEED: creates two demo tenants (use --reset to wipe their usage)
python -m app.main       # RUN:  serves http://localhost:8080
```

Try it (demo keys are printed by the seed step):

```bash
curl -s -X POST localhost:8080/generate -H 'X-API-Key: demo-key-alpha' -H 'Idempotency-Key: order-1' \
     -H 'Content-Type: application/json' -d '{"prompt":"hello","usage":{"input_tokens":100,"output_tokens":2500}}'
curl -s localhost:8080/usage -H 'X-API-Key: demo-key-alpha'
```

Run the tests (no network, no Stripe account needed): `python -m pytest -q`

### Stripe test mode (checkout + webhooks)

1. In the Stripe **test** dashboard, create a product "Pro" with a $20/month recurring price. Copy the `price_...` id.
2. Put `STRIPE_SECRET_KEY` (`sk_test_...`) and `STRIPE_PRO_PRICE_ID` in `.env`.
3. Install the Stripe CLI, run `stripe login`, then `stripe listen --forward-to localhost:8080/webhooks/stripe`.
   Copy the `whsec_...` it prints into `STRIPE_WEBHOOK_SECRET` in `.env` and restart the server.
4. `curl -X POST localhost:8080/billing/checkout -H 'X-API-Key: demo-key-alpha'` → open the returned `checkout_url`,
   pay with card `4242 4242 4242 4242`, any future expiry/CVC.
5. Within seconds `GET /usage` shows `"plan": "pro"`. Replay with `stripe events resend <evt_id>`: it is processed once.

No Stripe CLI handy? `scripts/sign_webhook.py` signs a payload offline so you can `curl` the webhook yourself.

## Data model

`plans`, `tenants` (API key stored as a SHA-256 hash), `subscriptions` (one row per tenant), `idempotency_keys`
(the request claim + original response), `usage_events` (the ledger; `UNIQUE(tenant, key, type)` makes double counting
impossible even if application code were wrong), `stripe_events` (processed webhook ids). Schema is in
`migrations/001_init.sql`. Every query that touches tenant data filters by `tenant_id`, and the tenant comes **only**
from the API key.

## Limitations (honest)

- **SQLite, not Postgres.** Fine for this scope (a single writer, atomic via `BEGIN IMMEDIATE`), but a multi-instance
  deployment needs Postgres and row-level locking.
- **`POST /generate` is a dummy endpoint** and AI token counts are simulated or client-supplied. A real system would
  take them from the model provider's response.
- **No invoicing, proration or overage billing.** Going Free → Pro changes limits immediately and charges nothing extra
  for a partial month.
- **A downgrade does not erase usage.** A tenant who used 5,000 calls on Pro and falls back to Free is simply over the
  Free quota (`429`) until the month resets.
- **The Stripe client is hand-written** with the standard library (checkout creation and subscription lookup). It follows
  Stripe's documented API but has only been exercised against a fake in the tests; see `EVIDENCE.md` for what was
  verified against real Stripe.
- **Webhook ordering** is handled by event timestamp, so two events created in the same second apply in arrival order.
- **The reconcile job** is run by hand or cron; there is no built-in scheduler process.
