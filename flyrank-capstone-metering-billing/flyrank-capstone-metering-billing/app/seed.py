"""Seed demo data:  python -m app.seed          (create demo tenants, safe to run twice)
                   python -m app.seed --reset  (also wipe their usage and put them back on Free)
"""
from __future__ import annotations

import sys

from app import config, db


def main(argv: list[str]) -> None:
    settings = config.Settings.from_env()
    conn = db.connect(settings.database_path)
    db.migrate(conn)
    db.ensure_plans(conn)
    reset = "--reset" in argv
    for slug, tenant in config.DEMO_TENANTS.items():
        tenant_id = f"t_{slug}"
        db.create_tenant(conn, tenant_id, tenant["name"], tenant["api_key"])
        if reset:
            with db.transaction(conn):
                conn.execute("DELETE FROM usage_events WHERE tenant_id = ?", (tenant_id,))
                conn.execute("DELETE FROM idempotency_keys WHERE tenant_id = ?", (tenant_id,))
                conn.execute(
                    "UPDATE subscriptions SET plan_id='free', status='active', stripe_customer_id=NULL, "
                    "stripe_subscription_id=NULL, last_event_created=0, updated_at=? WHERE tenant_id=?",
                    (db.utcnow_iso(), tenant_id),
                )
        print(f"tenant {tenant_id:<10} X-API-Key: {tenant['api_key']}")
    print(f"Seeded. Try:  curl -H 'X-API-Key: demo-key-alpha' {settings.public_base_url}/usage")
    conn.close()


if __name__ == "__main__":
    main(sys.argv[1:])
