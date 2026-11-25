"""Print a valid Stripe-Signature header for a payload file, so you can curl the webhook without Stripe.

    python scripts/sign_webhook.py payload.json            # uses STRIPE_WEBHOOK_SECRET from the environment/.env
    curl -X POST localhost:8080/webhooks/stripe -H "Stripe-Signature: $(python scripts/sign_webhook.py payload.json)" \
         --data-binary @payload.json

--data-binary matters: curl's -d strips newlines, which changes the bytes and breaks the signature.
(For real Stripe events use `stripe listen` + `stripe trigger` instead; this is for offline demos.)
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402,F401  (loads .env)
from app.stripe_sync import sign_payload  # noqa: E402

if len(sys.argv) != 2:
    sys.exit(__doc__)
secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
if not secret:
    sys.exit("STRIPE_WEBHOOK_SECRET is not set")
print(sign_payload(Path(sys.argv[1]).read_bytes(), secret))
