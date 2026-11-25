"""End-to-end: a real uvicorn server on a random port, real HTTP requests, one test per acceptance probe."""
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest
import uvicorn

from app import metering, usage
from app.api import create_app
from app.config import Settings
from app.pricing import TokenUsage
from app.stripe_sync import StripeError, sign_payload

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
SECRET = "whsec_api_test_secret"
ALPHA = {"X-API-Key": "key-alpha"}
BETA = {"X-API-Key": "key-beta"}


def _lower(headers):
    return {k.lower(): v for k, v in headers.items()}


class FakeStripe:
    """Stands in for api.stripe.com so tests never touch the network."""

    def __init__(self):
        self.calls, self.fail = [], False

    def create_checkout_session(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise StripeError(402, "Your card was declined")
        return {"id": "cs_test_fake", "url": "https://checkout.stripe.com/c/pay/cs_test_fake"}


class Server:
    def __init__(self, base, db_path, stripe):
        self.base, self.db_path, self.stripe = base, db_path, stripe

    def request(self, method, path, body=None, headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, _lower(r.headers), json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, _lower(e.headers), json.loads(e.read() or b"null")

    def generate(self, key, body=None, who=ALPHA, **extra):
        headers = {**who, **({"Idempotency-Key": key} if key else {}), **extra}
        return self.request("POST", "/generate", body if body is not None else {"prompt": "hi"}, headers)

    def usage(self, who=ALPHA):
        return self.request("GET", "/usage", headers=who)[2]

    def webhook(self, event, secret=SECRET, signature=None):
        raw = json.dumps(event).encode()
        header = signature if signature is not None else sign_payload(raw, secret)
        return self.request("POST", "/webhooks/stripe", raw=raw, headers={"Stripe-Signature": header})


def start(settings, stripe):
    config = uvicorn.Config(create_app(settings, now_fn=lambda: NOW, stripe_client=stripe),
                            host="127.0.0.1", port=0, log_level="warning")
    uv = uvicorn.Server(config)
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    for _ in range(200):
        if uv.started:
            break
        time.sleep(0.02)
    port = uv.servers[0].sockets[0].getsockname()[1]
    return uv, thread, Server(f"http://127.0.0.1:{port}", settings.database_path, stripe)


def stop(uv, thread):
    uv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def server(conn):
    db_path = conn.execute("PRAGMA database_list").fetchone()["file"]
    stripe = FakeStripe()
    settings = Settings(database_path=db_path, stripe_secret_key="sk_test_fake", stripe_webhook_secret=SECRET,
                        stripe_pro_price_id="price_pro_test", public_base_url="http://testserver")
    uv, thread, srv = start(settings, stripe)
    yield srv
    stop(uv, thread)


def checkout_completed(tenant="t_alpha", event_id="evt_co", sub="sub_1", created=None):
    return {"id": event_id, "type": "checkout.session.completed", "created": created or int(time.time()),
            "data": {"object": {"id": "cs_1", "mode": "subscription", "client_reference_id": tenant,
                                "customer": "cus_1", "subscription": sub, "payment_status": "paid"}}}


# ---- PROBE 1 ---------------------------------------------------------------------------------------------
def test_probe1_same_request_twice_one_event_and_mirrored_response(server):
    first = server.generate("order-42", {"prompt": "hello"})
    second = server.generate("order-42", {"prompt": "hello"})
    assert first[0] == second[0] == 200
    assert second[2] == first[2], "the retry must mirror the original response"
    assert "idempotent-replay" not in first[1] and second[1]["idempotent-replay"] == "true"
    assert server.usage()["api_calls"]["used"] == 1


def test_same_key_with_a_different_body_is_409(server):
    server.generate("k", {"prompt": "one"})
    status, _, body = server.generate("k", {"prompt": "two"})
    assert status == 409 and body["error"]["code"] == "idempotency_key_reused"


# ---- PROBE 2 ---------------------------------------------------------------------------------------------
def test_probe2_exact_quota_boundary_over_http(server, conn):
    for i in range(998):                       # fast-forward to 998 used through the service itself
        metering.record(conn, "t_alpha", f"seed{i}", {"i": i}, TokenUsage(), NOW)
    assert server.generate("n999")[0] == 200   # 999th call: allowed
    assert server.generate("n1000")[0] == 200  # 1,000th call: exactly AT the limit, still allowed
    status, headers, body = server.generate("n1001")   # one past the limit
    assert status == 429
    assert body["error"]["code"] == "quota_exceeded"
    assert "quota exceeded" in body["error"]["message"] and "Upgrade to Pro" in body["error"]["message"]
    assert int(headers["retry-after"]) > 0
    d = body["error"]["details"]
    assert (d["used"], d["limit"], d["usage_type"], d["plan"]) == (1000, 1000, "api_call", "free")
    assert server.usage()["api_calls"]["used"] == 1000, "the rejected request must record nothing"


def test_token_quota_returns_429_and_says_which_quota(server):
    big = {"prompt": "x", "usage": {"output_tokens": 100_001}}
    status, _, body = server.generate("big", big)
    assert status == 429 and body["error"]["details"]["usage_type"] == "ai_tokens"


# ---- PROBE 3 ---------------------------------------------------------------------------------------------
def test_probe3_checkout_then_webhook_flips_free_to_pro(server):
    assert server.usage()["plan"] == "free" and server.usage()["api_calls"]["limit"] == 1_000
    status, _, body = server.request("POST", "/billing/checkout", headers=ALPHA)
    assert status == 200 and body["checkout_url"].startswith("https://checkout.stripe.com/")
    call = server.stripe.calls[0]
    assert call["tenant_id"] == "t_alpha" and call["price_id"] == "price_pro_test"
    assert call["success_url"] == "http://testserver/billing/success"

    status, _, body = server.webhook(checkout_completed())
    assert (status, body["status"]) == (200, "processed")
    after = server.usage()
    assert after["plan"] == "pro" and after["status"] == "active"
    assert after["api_calls"]["limit"] == 100_000 and after["ai_tokens"]["limit"] == 10_000_000
    assert server.usage(BETA)["plan"] == "free", "another tenant must be untouched"


def test_checkout_when_already_pro_is_409_and_stripe_errors_are_502(server):
    server.stripe.fail = True
    assert server.request("POST", "/billing/checkout", headers=ALPHA)[0] == 502
    server.stripe.fail = False
    server.webhook(checkout_completed())
    assert server.request("POST", "/billing/checkout", headers=ALPHA)[0] == 409


# ---- PROBE 4 ---------------------------------------------------------------------------------------------
def test_probe4_forged_webhook_is_400_and_changes_nothing(server):
    forged = server.webhook(checkout_completed(), secret="whsec_attacker_guess")
    assert forged[0] == 400 and forged[2]["error"]["code"] == "invalid_signature"
    assert server.webhook(checkout_completed(), signature="t=1,v1=00")[0] == 400
    raw = json.dumps(checkout_completed()).encode()
    assert server.request("POST", "/webhooks/stripe", raw=raw)[0] == 400, "no signature header at all"
    assert server.usage()["plan"] == "free"


def test_probe4_real_event_replayed_is_processed_once(server):
    event = checkout_completed()
    first = server.webhook(event)
    second = server.webhook(event)
    assert (first[0], first[2]["status"]) == (200, "processed")
    assert (second[0], second[2]["status"]) == (200, "duplicate")
    # prove the replay did not re-apply: a later downgrade must stick
    cancel = {"id": "evt_cancel", "type": "customer.subscription.deleted", "created": int(time.time()) + 10,
              "data": {"object": {"id": "sub_1", "customer": "cus_1", "status": "canceled",
                                  "metadata": {"tenant_id": "t_alpha"}}}}
    server.webhook(cancel)
    server.webhook(event)  # replay of the old upgrade event after the cancellation
    assert server.usage()["plan"] == "free"


def test_past_due_webhook_makes_generate_return_402(server):
    server.webhook(checkout_completed())
    event = {"id": "evt_pd", "type": "customer.subscription.updated", "created": int(time.time()) + 5,
             "data": {"object": {"id": "sub_1", "customer": "cus_1", "status": "past_due",
                                 "metadata": {"tenant_id": "t_alpha"}}}}
    server.webhook(event)
    status, _, body = server.generate("blocked")
    assert status == 402 and body["error"]["code"] == "payment_required" and "past due" in body["error"]["message"]


def test_unconfigured_stripe_refuses_webhooks_and_checkout(conn):
    """No whsec_ set -> an empty HMAC key would make forgeries valid, so the endpoint must refuse everything."""
    db_path = conn.execute("PRAGMA database_list").fetchone()["file"]
    uv, thread, srv = start(Settings(database_path=db_path), FakeStripe())
    try:
        status, _, body = srv.webhook(checkout_completed(), secret="")
        assert status == 503 and body["error"]["code"] == "webhook_not_configured"
        assert srv.request("POST", "/billing/checkout", headers=ALPHA)[0] == 503
        assert srv.usage()["plan"] == "free"
    finally:
        stop(uv, thread)


# ---- PROBE 5 ---------------------------------------------------------------------------------------------
def test_probe5_cached_and_reasoning_rules_match_get_usage(server):
    body = {"prompt": "price me", "usage": {"input_tokens": 10_000, "cached_input_tokens": 4_000,
                                            "output_tokens": 2_000, "reasoning_tokens": 3_000}}
    assert server.generate("p5", body)[0] == 200
    u = server.usage()
    assert u["ai_tokens"]["used"] == 15_000, "cached tokens sit inside input: not counted twice"
    assert u["ai_tokens"]["breakdown"] == body["usage"]
    # fresh 6,000 x $3/M + cached 4,000 x $0.30/M + (output 2,000 + reasoning 3,000) x $15/M = $0.0942
    assert u["cost_micro_usd"] == {"api_calls": 2_000, "ai_tokens": 94_200, "total": 96_200}
    assert u["cost_usd_display"] == "$0.096200"


# ---- validation at the boundary: clean 4xx, never a 500 ------------------------------------------------------
@pytest.mark.parametrize("body,expected", [
    ({"prompt": ""}, 422), ({"prompt": 5}, 422), ({}, 422), ({"prompt": "x", "usage": "lots"}, 422),
    ({"prompt": "x", "usage": {"input_tokens": -1}}, 422),
    ({"prompt": "x", "usage": {"input_tokens": 10, "cached_input_tokens": 11}}, 422),
    ({"prompt": "x", "usage": {"output_tokens": 1.5}}, 422),
    ({"prompt": "x", "usage": {"output_tokens": True}}, 422),
    ({"prompt": "x", "usage": {"output_tokens": "5"}}, 422),
    ({"prompt": "x", "usage": {"output_tokesn": 5}}, 422),
    ({"prompt": "x", "usage": {"output_tokens": 10**30}}, 422),
])
def test_bad_bodies_are_422_and_record_nothing(server, body, expected):
    status, _, payload = server.generate("v", body)
    assert status == expected and payload["error"]["code"] == "validation_failed"
    assert server.usage()["api_calls"]["used"] == 0


def test_bad_headers_and_json(server):
    assert server.generate(None)[0] == 400                                   # no Idempotency-Key
    assert server.generate("has space")[0] == 400                            # invalid key characters
    assert server.generate("k" * 256)[0] == 400                              # too long
    assert server.generate("k", who={})[0] == 401                            # no API key
    assert server.generate("k", who={"X-API-Key": "nope"})[0] == 401         # unknown API key
    headers = {**ALPHA, "Idempotency-Key": "j1"}
    assert server.request("POST", "/generate", raw=b"{not json", headers=headers)[0] == 400
    assert server.request("POST", "/generate", raw=b"[1,2]", headers=headers)[0] == 400
    assert server.request("POST", "/generate", raw=b"", headers=headers)[0] == 400
    assert server.request("POST", "/generate", raw=b"x" * 200_000, headers=headers)[0] == 413
    assert server.request("GET", "/usage")[0] == 401
    assert server.request("DELETE", "/generate", headers=ALPHA)[0] == 405
    assert server.request("GET", "/nope")[0] == 404
    assert server.usage()["api_calls"]["used"] == 0


# ---- tenant isolation -------------------------------------------------------------------------------------------
def test_tenants_are_isolated_over_http(server):
    server.generate("a1", {"prompt": "x", "usage": {"output_tokens": 500}})
    server.generate("shared-key", who=BETA)          # the same idempotency key on another tenant is independent
    server.generate("shared-key", who=ALPHA)
    assert server.usage(ALPHA)["api_calls"]["used"] == 2
    assert server.usage(BETA)["api_calls"]["used"] == 1
    assert server.usage(ALPHA)["ai_tokens"]["used"] == 500 + 2_501
    assert server.usage(BETA)["ai_tokens"]["used"] == 2_501
