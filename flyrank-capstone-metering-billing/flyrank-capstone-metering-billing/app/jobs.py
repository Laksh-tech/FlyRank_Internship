"""Background job: reconcile our subscription mirror against Stripe's real state.

Webhooks can be missed (our server was down, Stripe gave up retrying). This job asks Stripe what each
subscription looks like NOW and fixes any tenant whose plan/status has drifted. Slow, network-bound
work like this belongs off the request path.

Run once:           python -m app.jobs reconcile
Run on a schedule:  python -m app.jobs reconcile --every 3600      (or call it from cron)
Exit code 1 and an ERROR log line starting with 'ALERT' mean it failed even after its retries.
"""
from __future__ import annotations

import logging
import sys
import time

from app import db
from app.config import Settings
from app.stripe_sync import STATUS_MAP, StripeClient, StripeError, apply_snapshot

log = logging.getLogger("jobs")


def run_with_retries(name: str, fn, attempts: int = 3, base_delay: float = 1.0, sleep=time.sleep):
    """Call fn(); on failure wait 1s, 2s, 4s... and try again. After the last failure, raise the alert.

    Returns (succeeded, result_or_None). The alert is an ERROR log line (hook a pager/Slack onto it).
    """
    for attempt in range(1, attempts + 1):
        try:
            return True, fn()
        except Exception as exc:  # noqa: BLE001 - a job must survive anything and report it
            log.warning("job %s attempt %d/%d failed: %s", name, attempt, attempts, exc)
            if attempt == attempts:
                log.error("ALERT: job %s failed after %d attempts: %s", name, attempts, exc)
                return False, None
            sleep(base_delay * 2 ** (attempt - 1))
    return False, None  # unreachable; keeps type checkers happy


def reconcile_subscriptions(conn, stripe: StripeClient) -> dict:
    rows = conn.execute(
        "SELECT tenant_id, plan_id, status, stripe_subscription_id FROM subscriptions "
        "WHERE stripe_subscription_id IS NOT NULL"
    ).fetchall()
    fixed = []
    for row in rows:
        try:
            remote = stripe.retrieve_subscription(row["stripe_subscription_id"])
        except StripeError as exc:
            if exc.status != 404:
                raise  # a real outage: let the retry wrapper deal with it
            remote = {"id": row["stripe_subscription_id"], "status": "canceled"}  # Stripe no longer has it
        expected = STATUS_MAP.get(remote.get("status"))
        if expected is not None and expected != (row["plan_id"], row["status"]):
            if apply_snapshot(conn, row["tenant_id"], remote) == "processed":
                fixed.append(row["tenant_id"])
                log.warning("reconciled %s: %s/%s -> %s/%s", row["tenant_id"], row["plan_id"], row["status"], *expected)
    return {"checked": len(rows), "fixed": fixed}


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if not argv or argv[0] != "reconcile":
        print("usage: python -m app.jobs reconcile [--every SECONDS]")
        return 2
    settings = Settings.from_env()
    if not settings.stripe_secret_key:
        print("STRIPE_SECRET_KEY is not set: nothing to reconcile against.")
        return 2
    every = int(argv[argv.index("--every") + 1]) if "--every" in argv else 0
    stripe = StripeClient(settings.stripe_secret_key)
    while True:
        def once():
            conn = db.connect(settings.database_path)
            try:
                return reconcile_subscriptions(conn, stripe)
            finally:
                conn.close()
        ok, result = run_with_retries("reconcile_subscriptions", once)
        if ok:
            log.info("reconcile finished: %s", result)
        if not every:
            return 0 if ok else 1
        time.sleep(every)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
