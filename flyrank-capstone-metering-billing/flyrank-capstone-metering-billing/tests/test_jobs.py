import logging

from app import jobs
from app.stripe_sync import StripeError, handle_event


class FakeStripe:
    def __init__(self, remote):
        self.remote = remote  # {subscription_id: stripe subscription object or StripeError to raise}

    def retrieve_subscription(self, sub_id):
        value = self.remote[sub_id]
        if isinstance(value, Exception):
            raise value
        return value


def upgrade(conn, tenant="t_alpha", sub="sub_1"):
    handle_event(conn, {"id": f"evt_{tenant}", "type": "checkout.session.completed", "created": 1000, "data": {
        "object": {"mode": "subscription", "client_reference_id": tenant, "customer": f"cus_{tenant}",
                   "subscription": sub, "payment_status": "paid"}}})


def row(conn, tenant="t_alpha"):
    r = conn.execute("SELECT plan_id, status FROM subscriptions WHERE tenant_id=?", (tenant,)).fetchone()
    return (r["plan_id"], r["status"])


def test_reconcile_repairs_a_missed_cancellation_webhook(conn):
    upgrade(conn)
    assert row(conn) == ("pro", "active")
    stripe = FakeStripe({"sub_1": {"id": "sub_1", "customer": "cus_t_alpha", "status": "canceled"}})
    result = jobs.reconcile_subscriptions(conn, stripe)
    assert result == {"checked": 1, "fixed": ["t_alpha"]}
    assert row(conn) == ("free", "canceled")


def test_reconcile_repairs_a_missed_past_due_webhook(conn):
    upgrade(conn)
    stripe = FakeStripe({"sub_1": {"id": "sub_1", "customer": "cus_t_alpha", "status": "past_due"}})
    jobs.reconcile_subscriptions(conn, stripe)
    assert row(conn) == ("pro", "past_due")


def test_reconcile_leaves_matching_tenants_alone(conn):
    upgrade(conn)
    stripe = FakeStripe({"sub_1": {"id": "sub_1", "customer": "cus_t_alpha", "status": "active"}})
    assert jobs.reconcile_subscriptions(conn, stripe) == {"checked": 1, "fixed": []}
    assert row(conn) == ("pro", "active")
    assert row(conn, "t_beta") == ("free", "active"), "tenants without a Stripe subscription are never touched"


def test_subscription_gone_from_stripe_means_canceled(conn):
    upgrade(conn)
    stripe = FakeStripe({"sub_1": StripeError(404, "No such subscription")})
    assert jobs.reconcile_subscriptions(conn, stripe)["fixed"] == ["t_alpha"]
    assert row(conn) == ("free", "canceled")


def test_retry_succeeds_after_transient_failures_with_backoff():
    attempts, sleeps = [], []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("stripe is down")
        return "ok"

    assert jobs.run_with_retries("t", flaky, attempts=3, base_delay=1.0, sleep=sleeps.append) == (True, "ok")
    assert len(attempts) == 3 and sleeps == [1.0, 2.0]


def test_exhausted_retries_raise_an_alert(caplog):
    def always_fails():
        raise ConnectionError("stripe is down")

    with caplog.at_level(logging.ERROR, logger="jobs"):
        ok, result = jobs.run_with_retries("reconcile", always_fails, attempts=3, sleep=lambda s: None)
    assert (ok, result) == (False, None)
    assert any(r.levelno == logging.ERROR and r.getMessage().startswith("ALERT: job reconcile failed after 3 attempts")
               for r in caplog.records)
