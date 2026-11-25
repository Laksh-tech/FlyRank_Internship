import json

import pytest

from app import metering, stripe_sync, usage
from app.pricing import TokenUsage
from app.stripe_sync import BadEvent, SignatureError, handle_event, sign_payload, verify_signature

SECRET = "whsec_unit_test_secret"
BODY = b'{"id":"evt_1","type":"checkout.session.completed"}'


# ---- signature verification (PROBE 4, unit level) -------------------------------------------------
def test_valid_signature_passes():
    verify_signature(BODY, sign_payload(BODY, SECRET), SECRET)


@pytest.mark.parametrize("make_header", [
    lambda: sign_payload(BODY, "whsec_wrong"),                       # signed with the wrong secret
    lambda: sign_payload(BODY + b" ", SECRET),                       # body changed after signing
    lambda: sign_payload(BODY, SECRET, timestamp=1_000_000_000),     # ancient timestamp = replayed signature
    lambda: "",                                                      # header missing
    lambda: "garbage",                                               # header malformed
    lambda: "t=abc,v1=deadbeef",                                     # timestamp not a number
])
def test_forged_or_broken_signatures_are_rejected(make_header):
    with pytest.raises(SignatureError):
        verify_signature(BODY, make_header(), SECRET)


def test_empty_secret_never_verifies_anything():
    """An unset whsec_ would otherwise let anyone forge events by signing with an empty key."""
    with pytest.raises(SignatureError):
        verify_signature(BODY, sign_payload(BODY, ""), "")


def test_reserialised_json_breaks_the_signature():
    """Why the raw body matters: same JSON content, different bytes, different signature."""
    header = sign_payload(BODY, SECRET)
    reformatted = json.dumps(json.loads(BODY), indent=2).encode()
    with pytest.raises(SignatureError):
        verify_signature(reformatted, header, SECRET)


# ---- event handling ----------------------------------------------------------------------------------
def checkout_event(event_id="evt_co_1", tenant="t_alpha", created=1000, sub="sub_1", cust="cus_1", paid="paid"):
    return {"id": event_id, "type": "checkout.session.completed", "created": created, "data": {"object": {
        "id": "cs_test_1", "mode": "subscription", "client_reference_id": tenant, "customer": cust,
        "subscription": sub, "payment_status": paid}}}


def sub_event(kind, status, event_id, created, tenant="t_alpha", sub="sub_1", cust="cus_1"):
    return {"id": event_id, "type": f"customer.subscription.{kind}", "created": created, "data": {"object": {
        "id": sub, "customer": cust, "status": status, "metadata": {"tenant_id": tenant}}}}


def state(conn, tenant="t_alpha"):
    r = conn.execute("SELECT plan_id, status, stripe_customer_id, stripe_subscription_id FROM subscriptions "
                     "WHERE tenant_id = ?", (tenant,)).fetchone()
    return (r["plan_id"], r["status"], r["stripe_customer_id"], r["stripe_subscription_id"])


def test_checkout_completed_upgrades_to_pro_and_raises_limits(conn):
    assert usage.rollup(conn, "t_alpha")["api_calls"]["limit"] == 1_000
    assert handle_event(conn, checkout_event()) == "processed"
    assert state(conn) == ("pro", "active", "cus_1", "sub_1")
    r = usage.rollup(conn, "t_alpha")
    assert r["plan"] == "pro" and r["api_calls"]["limit"] == 100_000 and r["ai_tokens"]["limit"] == 10_000_000


def test_replayed_event_is_processed_once(conn):
    assert handle_event(conn, checkout_event()) == "processed"
    conn.execute("UPDATE subscriptions SET status='past_due' WHERE tenant_id='t_alpha'")  # change something
    assert handle_event(conn, checkout_event()) == "duplicate"
    assert state(conn)[1] == "past_due", "the replay must not re-apply and undo the change"
    assert conn.execute("SELECT COUNT(*) AS n FROM stripe_events").fetchone()["n"] == 1


def test_failed_apply_rolls_back_the_event_id_so_stripes_retry_works(conn, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database exploded mid-update")
    monkeypatch.setattr(stripe_sync, "_apply", boom)
    with pytest.raises(RuntimeError):
        handle_event(conn, checkout_event())
    assert conn.execute("SELECT COUNT(*) AS n FROM stripe_events").fetchone()["n"] == 0
    monkeypatch.undo()
    assert handle_event(conn, checkout_event()) == "processed", "the retry must NOT be skipped as a duplicate"
    assert state(conn)[0] == "pro"


def test_past_due_blocks_metering_with_402_then_recovery_unblocks(conn):
    handle_event(conn, checkout_event(created=1000))
    handle_event(conn, sub_event("updated", "past_due", "evt_2", created=1100))
    assert state(conn)[:2] == ("pro", "past_due")
    with pytest.raises(metering.PaymentRequired):
        metering.record(conn, "t_alpha", "k1", {"a": 1}, TokenUsage())
    handle_event(conn, sub_event("updated", "active", "evt_3", created=1200))
    assert metering.record(conn, "t_alpha", "k2", {"a": 1}, TokenUsage()).status == 200


def test_trialing_counts_as_active(conn):
    handle_event(conn, sub_event("created", "trialing", "evt_t", created=1000))
    assert state(conn)[:2] == ("pro", "active")


@pytest.mark.parametrize("kind,status", [("updated", "canceled"), ("deleted", "active")])
def test_cancellation_returns_tenant_to_free(conn, kind, status):
    handle_event(conn, checkout_event(created=1000))
    handle_event(conn, sub_event(kind, status, "evt_cancel", created=2000))
    assert state(conn)[:2] == ("free", "canceled")
    assert usage.rollup(conn, "t_alpha")["api_calls"]["limit"] == 1_000


def test_out_of_order_events_never_overwrite_newer_state(conn):
    handle_event(conn, checkout_event(created=1000))
    assert handle_event(conn, sub_event("updated", "canceled", "evt_new", created=3000)) == "processed"
    # an OLDER 'active' update shows up late
    assert handle_event(conn, sub_event("updated", "active", "evt_old", created=2000)) == "stale"
    assert state(conn)[:2] == ("free", "canceled")


def test_cancelling_an_old_subscription_does_not_downgrade_a_new_one(conn):
    handle_event(conn, checkout_event(created=1000, sub="sub_old"))
    handle_event(conn, checkout_event(event_id="evt_co_2", created=2000, sub="sub_new"))
    out = handle_event(conn, sub_event("deleted", "canceled", "evt_del_old", created=2500, sub="sub_old"))
    assert out == "ignored_other_subscription"
    assert state(conn)[:2] == ("pro", "active") and state(conn)[3] == "sub_new"


def test_unpaid_checkout_does_not_upgrade(conn):
    assert handle_event(conn, checkout_event(paid="unpaid")) == "ignored_unpaid"
    assert state(conn)[:2] == ("free", "active")


def test_unknown_tenant_and_unhandled_types_change_nothing(conn):
    assert handle_event(conn, checkout_event(tenant="t_ghost")) == "ignored_unknown_tenant"
    other = {"id": "evt_x", "type": "invoice.paid", "created": 1, "data": {"object": {}}}
    assert handle_event(conn, other) == "ignored"
    assert state(conn)[:2] == ("free", "active")


def test_subscription_event_finds_tenant_by_stripe_customer_id(conn):
    handle_event(conn, checkout_event(cust="cus_known", created=1000))
    no_metadata = sub_event("updated", "past_due", "evt_nm", created=1500, cust="cus_known")
    no_metadata["data"]["object"]["metadata"] = {}
    assert handle_event(conn, no_metadata) == "processed"
    assert state(conn)[1] == "past_due"


def test_one_tenants_event_never_touches_another(conn):
    handle_event(conn, checkout_event(tenant="t_alpha"))
    assert state(conn, "t_beta")[:2] == ("free", "active")


@pytest.mark.parametrize("bad", [{}, {"id": "e"}, {"id": 5, "type": "x", "created": 1, "data": {"object": {}}},
                                 {"id": "e", "type": "x", "created": "now", "data": {"object": {}}},
                                 {"id": "e", "type": "x", "created": 1, "data": {"object": []}}])
def test_malformed_events_raise_badevent(conn, bad):
    with pytest.raises(BadEvent):
        handle_event(conn, bad)
