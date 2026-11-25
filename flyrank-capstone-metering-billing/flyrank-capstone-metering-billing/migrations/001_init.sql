-- 001_init: plans, tenants, subscriptions, idempotency ledger, usage events, webhook dedupe.
-- Usage is stored as integer counts; cost is stored as exact integer pico-USD. No floats anywhere.

CREATE TABLE plans (
  id                   TEXT PRIMARY KEY,
  name                 TEXT NOT NULL,
  api_calls_limit      INTEGER NOT NULL CHECK (api_calls_limit >= 0),
  ai_tokens_limit      INTEGER NOT NULL CHECK (ai_tokens_limit >= 0),
  monthly_price_cents  INTEGER NOT NULL CHECK (monthly_price_cents >= 0)
);

CREATE TABLE tenants (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  api_key_hash  TEXT NOT NULL UNIQUE,      -- sha256 of the API key; the key itself is never stored
  created_at    TEXT NOT NULL
);

-- One row per tenant: the plan they are on now and the status we mirror from Stripe.
CREATE TABLE subscriptions (
  tenant_id               TEXT PRIMARY KEY REFERENCES tenants(id),
  plan_id                 TEXT NOT NULL REFERENCES plans(id),
  status                  TEXT NOT NULL CHECK (status IN ('active', 'past_due', 'canceled')),
  stripe_customer_id      TEXT,
  stripe_subscription_id  TEXT,
  last_event_created      INTEGER NOT NULL DEFAULT 0,   -- Stripe event time; older events are ignored
  updated_at              TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_subscriptions_stripe_sub ON subscriptions(stripe_subscription_id)
  WHERE stripe_subscription_id IS NOT NULL;
CREATE UNIQUE INDEX idx_subscriptions_stripe_customer ON subscriptions(stripe_customer_id)
  WHERE stripe_customer_id IS NOT NULL;

-- The claim "this request already happened" plus the exact response we sent, so a retry mirrors it.
CREATE TABLE idempotency_keys (
  tenant_id        TEXT NOT NULL REFERENCES tenants(id),
  idempotency_key  TEXT NOT NULL,
  request_hash     TEXT NOT NULL,
  response_status  INTEGER NOT NULL,
  response_body    TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  PRIMARY KEY (tenant_id, idempotency_key)
);

-- The ledger. UNIQUE(tenant, key, type) is the database-level guarantee against double counting.
CREATE TABLE usage_events (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id            TEXT NOT NULL REFERENCES tenants(id),
  idempotency_key      TEXT NOT NULL,
  usage_type           TEXT NOT NULL CHECK (usage_type IN ('api_call', 'ai_tokens')),
  quantity             INTEGER NOT NULL CHECK (quantity > 0),
  input_tokens         INTEGER,
  cached_input_tokens  INTEGER,
  output_tokens        INTEGER,
  reasoning_tokens     INTEGER,
  cost_pico_usd        INTEGER NOT NULL CHECK (cost_pico_usd >= 0),
  period               TEXT NOT NULL,          -- 'YYYY-MM' (UTC) this event counts toward
  occurred_at          TEXT NOT NULL,
  UNIQUE (tenant_id, idempotency_key, usage_type)
);
CREATE INDEX idx_usage_tenant_period ON usage_events(tenant_id, period, usage_type);

-- Webhook dedupe: Stripe event ids we have already applied.
CREATE TABLE stripe_events (
  event_id      TEXT PRIMARY KEY,
  event_type    TEXT NOT NULL,
  processed_at  TEXT NOT NULL
);
