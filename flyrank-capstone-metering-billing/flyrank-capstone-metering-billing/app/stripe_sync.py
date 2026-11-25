"""Stripe integration: checkout creation, webhook signature check, and event -> plan/status sync.

No web framework in here. Payment truth lives at Stripe; our DB only mirrors VERIFIED events.
Written against Stripe's documented wire format with the standard library, so there is no SDK to install.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from app import db

SIGNATURE_TOLERANCE_SECONDS = 300  # Stripe's default: reject signatures older than 5 minutes (replay defence)
STRIPE_API = "https://api.stripe.com/v1"


# ---- signature verification -------------------------------------------------------------------
class SignatureError(Exception):
    pass


def sign_payload(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """Build a valid Stripe-Signature header. Used by tests and scripts/sign_webhook.py."""
    ts = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def verify_signature(payload: bytes, header: str | None, secret: str,
                     tolerance: int = SIGNATURE_TOLERANCE_SECONDS, now: float | None = None) -> None:
    """Raise SignatureError unless `header` proves Stripe signed exactly these raw bytes.

    Stripe signs  "<timestamp>.<raw body>"  with HMAC-SHA256 using the endpoint's whsec_ secret.
    It must be the RAW body: re-serialising parsed JSON changes the bytes and breaks the signature.
    """
    if not secret:  # an empty secret would make forgeries trivially valid, so refuse outright
        raise SignatureError("webhook secret is not configured")
    if not header:
        raise SignatureError("missing Stripe-Signature header")
    timestamp, candidates = None, []
    for part in header.split(","):
        name, _, value = part.strip().partition("=")
        if name == "t":
            timestamp = value
        elif name == "v1":
            candidates.append(value)
    if timestamp is None or not timestamp.isdigit() or not candidates:
        raise SignatureError("malformed Stripe-Signature header")
    current = time.time() if now is None else now
    if abs(current - int(timestamp)) > tolerance:
        raise SignatureError("timestamp outside the tolerance window")
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, c) for c in candidates):  # constant-time comparison
        raise SignatureError("signature does not match")


# ---- outbound calls (Checkout) ----------------------------------------------------------------
class StripeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class StripeClient:
    """Just the one call we need. Injectable in tests so no network is ever touched."""

    def __init__(self, secret_key: str, urlopen=urllib.request.urlopen):
        self._secret_key = secret_key
        self._urlopen = urlopen

    def create_checkout_session(self, tenant_id: str, price_id: str, success_url: str, cancel_url: str,
                                customer_id: str | None = None) -> dict:
        form = {
            "mode": "subscription",
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": tenant_id,
            # metadata on the subscription lets later webhooks find the tenant without a lookup table
            "subscription_data[metadata][tenant_id]": tenant_id,
        }
        if customer_id:
            form["customer"] = customer_id
        return self._call("POST", "/checkout/sessions", form, f"checkout-{tenant_id}-{uuid.uuid4().hex}")

    def retrieve_subscription(self, subscription_id: str) -> dict:
        """Stripe's current view of one subscription (used by the reconciliation job)."""
        return self._call("GET", f"/subscriptions/{urllib.parse.quote(subscription_id, safe='')}")

    def _call(self, method: str, path: str, form: dict | None = None, idempotency_key: str | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._secret_key}"}
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(
            f"{STRIPE_API}{path}",
            data=urllib.parse.urlencode(form).encode() if form is not None else None,
            method=method,
            headers=headers,
        )
        try:
            with self._urlopen(request, timeout=10) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read())["error"]["message"]
            except Exception:  # noqa: BLE001
                message = "Stripe rejected the request"
            raise StripeError(exc.code, message) from None
        except urllib.error.URLError:
            raise StripeError(0, "Could not reach Stripe") from None


# ---- inbound webhooks -------------------------------------------------------------------------
SUBSCRIPTION_EVENTS = {
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
}
HANDLED_EVENTS = {"checkout.session.completed"} | SUBSCRIPTION_EVENTS

# Stripe subscription status -> (our plan, our status). None = not a state change we act on.
STATUS_MAP = {
    "active": ("pro", "active"),
    "trialing": ("pro", "active"),
    "past_due": ("pro", "past_due"),
    "unpaid": ("pro", "past_due"),
    "canceled": ("free", "canceled"),
    "incomplete_expired": ("free", "canceled"),
}


class BadEvent(ValueError):
    """The (already signature-verified) payload is not a usable Stripe event."""


def handle_event(conn: sqlite3.Connection, event: dict) -> str:
    """Apply one verified event exactly once. Returns a short outcome word for the HTTP response.

    The event id is inserted and the plan change applied in ONE transaction. If applying fails, the id
    is rolled back too, so Stripe's retry is processed instead of being wrongly skipped as a duplicate.
    """
    try:
        event_id, event_type, created, obj = event["id"], event["type"], event["created"], event["data"]["object"]
    except (KeyError, TypeError):
        raise BadEvent("event must have id, type, created and data.object") from None
    if not (isinstance(event_id, str) and isinstance(event_type, str) and isinstance(obj, dict)
            and isinstance(created, int) and not isinstance(created, bool)):
        raise BadEvent("event fields have the wrong types")
    if event_type not in HANDLED_EVENTS:
        return "ignored"

    with db.transaction(conn):
        inserted = conn.execute(
            "INSERT OR IGNORE INTO stripe_events (event_id, event_type, processed_at) VALUES (?, ?, ?)",
            (event_id, event_type, db.utcnow_iso()),
        )
        if inserted.rowcount == 0:
            return "duplicate"
        if event_type == "checkout.session.completed":
            return _on_checkout_completed(conn, obj, created)
        return _on_subscription_event(conn, obj, created, deleted=event_type.endswith(".deleted"))


def apply_snapshot(conn: sqlite3.Connection, tenant_id: str, sub: dict, now: int | None = None) -> str:
    """Apply Stripe's CURRENT view of a subscription (reconciliation), not a webhook event.

    It is stamped with the current time, so the same out-of-order guard applies: it can only overwrite
    state that is older than this moment.
    """
    mapped = STATUS_MAP.get(sub.get("status"))
    if mapped is None:
        return "ignored"
    with db.transaction(conn):
        return _apply(conn, tenant_id, sub.get("customer"), sub["id"], mapped[0], mapped[1],
                      int(time.time()) if now is None else now)


def _tenant_exists(conn: sqlite3.Connection, tenant_id) -> bool:
    return bool(tenant_id) and conn.execute("SELECT 1 FROM tenants WHERE id = ?", (tenant_id,)).fetchone() is not None


def _on_checkout_completed(conn, session: dict, created: int) -> str:
    tenant_id = session.get("client_reference_id") or (session.get("metadata") or {}).get("tenant_id")
    if not _tenant_exists(conn, tenant_id):
        return "ignored_unknown_tenant"
    if session.get("mode") != "subscription" or not session.get("subscription"):
        return "ignored"
    if session.get("payment_status") not in ("paid", "no_payment_required"):
        return "ignored_unpaid"  # async payment still pending: a later subscription event will settle it
    return _apply(conn, tenant_id, session.get("customer"), session["subscription"], "pro", "active", created)


def _on_subscription_event(conn, sub: dict, created: int, deleted: bool) -> str:
    sub_id = sub.get("id")
    tenant_id = (sub.get("metadata") or {}).get("tenant_id")
    if not _tenant_exists(conn, tenant_id):  # fall back to the Stripe customer id we stored at checkout
        row = conn.execute("SELECT tenant_id FROM subscriptions WHERE stripe_customer_id = ?",
                           (sub.get("customer"),)).fetchone()
        tenant_id = row["tenant_id"] if row else None
    if not tenant_id or not sub_id:
        return "ignored_unknown_tenant"
    mapped = STATUS_MAP["canceled"] if deleted else STATUS_MAP.get(sub.get("status"))
    if mapped is None:
        return "ignored"
    return _apply(conn, tenant_id, sub.get("customer"), sub_id, mapped[0], mapped[1], created)


def _apply(conn, tenant_id: str, customer_id, sub_id: str, plan_id: str, status: str, created: int) -> str:
    current = conn.execute(
        "SELECT stripe_subscription_id, last_event_created FROM subscriptions WHERE tenant_id = ?", (tenant_id,)
    ).fetchone()
    if current is None:
        return "ignored_unknown_tenant"
    if current["stripe_subscription_id"] == sub_id and current["last_event_created"] > created:
        return "stale"  # Stripe does not guarantee delivery order: never let an older event overwrite a newer one
    if status != "active" or plan_id != "pro":
        # a cancel/past_due for an OLD subscription must not downgrade a newer one
        if current["stripe_subscription_id"] not in (None, sub_id):
            return "ignored_other_subscription"
    conn.execute(
        "UPDATE subscriptions SET plan_id = ?, status = ?, stripe_customer_id = COALESCE(?, stripe_customer_id), "
        "stripe_subscription_id = ?, last_event_created = ?, updated_at = ? WHERE tenant_id = ?",
        (plan_id, status, customer_id, sub_id, created, db.utcnow_iso(), tenant_id),
    )
    return "processed"
