"""HTTP layer only: parse + validate input, call the services, map results/exceptions to status codes.

No business rules live here. Every failure the client can cause is a clean 4xx with a JSON body that
says WHY; an unexpected bug becomes a generic 500 with nothing internal leaked.
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app import db, metering, stripe_sync, usage
from app.config import Settings
from app.pricing import InvalidTokenCounts, TokenUsage

MAX_BODY_BYTES = 100_000
IDEMPOTENCY_KEY_RE = re.compile(r"^[\x21-\x7e]{1,255}$")  # printable ASCII, no spaces, 1-255 chars
TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens")


class HttpError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None, headers: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.details, self.headers = details or {}, headers or {}


def error_response(status: int, code: str, message: str, details: dict | None = None, headers: dict | None = None):
    body = {"error": {"code": code, "message": message, "details": details or {}}}
    return JSONResponse(body, status_code=status, headers=headers)


# ---- input parsing (validation at the boundary) -------------------------------------------------
def parse_json_object(raw: bytes) -> dict:
    if len(raw) > MAX_BODY_BYTES:
        raise HttpError(413, "payload_too_large", f"Request body must be at most {MAX_BODY_BYTES} bytes.")
    try:
        data = json.loads(raw or b"null")
    except (ValueError, UnicodeDecodeError):
        raise HttpError(400, "invalid_json", "Request body must be valid JSON.") from None
    if not isinstance(data, dict):
        raise HttpError(400, "invalid_json", "Request body must be a JSON object.")
    return data


def parse_generate(raw: bytes) -> tuple[dict, TokenUsage]:
    """Returns (canonical payload used for the idempotency hash, validated TokenUsage)."""
    data = parse_json_object(raw)
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 10_000:
        raise HttpError(422, "validation_failed", "'prompt' must be a non-empty string of at most 10,000 characters.",
                        {"field": "prompt"})
    supplied = data.get("usage")
    if supplied is None:
        # AI usage is simulated: derive plausible numbers so the endpoint works with just a prompt
        counts = {"input_tokens": max(1, len(prompt) // 4), "cached_input_tokens": 0,
                  "output_tokens": 2_500, "reasoning_tokens": 0}
    elif isinstance(supplied, dict):
        unknown = set(supplied) - set(TOKEN_FIELDS)
        if unknown:
            raise HttpError(422, "validation_failed", f"Unknown usage field(s): {sorted(unknown)}.",
                            {"allowed": list(TOKEN_FIELDS)})
        counts = {name: supplied.get(name, 0) for name in TOKEN_FIELDS}
    else:
        raise HttpError(422, "validation_failed", "'usage' must be an object.", {"field": "usage"})
    tokens = TokenUsage(**counts)
    try:
        tokens.validate()
    except InvalidTokenCounts as exc:
        raise HttpError(422, "validation_failed", str(exc), {"field": "usage"}) from None
    return {"prompt": prompt, "usage": counts}, tokens


def idempotency_key(request: Request) -> str:
    key = request.headers.get("idempotency-key")
    if not key:
        raise HttpError(400, "idempotency_key_required",
                        "Send an 'Idempotency-Key' header (any unique string per logical request) so retries "
                        "can never be double counted.")
    if not IDEMPOTENCY_KEY_RE.match(key):
        raise HttpError(400, "idempotency_key_invalid",
                        "'Idempotency-Key' must be 1-255 printable ASCII characters with no spaces.")
    return key


# ---- the app -------------------------------------------------------------------------------------
def create_app(settings: Settings | None = None, now_fn=None, stripe_client=None) -> Starlette:
    settings = settings or Settings.from_env()
    now_fn = now_fn or db.utcnow
    stripe_client = stripe_client or stripe_sync.StripeClient(settings.stripe_secret_key)

    # make sure the schema and plan rows exist, so `run` works on a clean machine even before `seed`
    with _connection(settings) as conn:
        db.migrate(conn)
        db.ensure_plans(conn)

    def authenticate(conn, request: Request) -> str:
        api_key = request.headers.get("x-api-key")
        if not api_key:
            raise HttpError(401, "unauthorized", "Missing 'X-API-Key' header.")
        row = conn.execute("SELECT id FROM tenants WHERE api_key_hash = ?", (db.hash_api_key(api_key),)).fetchone()
        if row is None:
            raise HttpError(401, "unauthorized", "Unknown API key.")
        return row["id"]  # the tenant comes ONLY from the key, never from the body or URL

    # -- handlers: read the request on the event loop, do blocking DB work in a worker thread --
    async def health(request: Request):
        return JSONResponse({"status": "ok"})

    async def generate(request: Request):
        raw = await request.body()

        def work() -> JSONResponse:
            with _connection(settings) as conn:
                tenant_id = authenticate(conn, request)
                key = idempotency_key(request)
                payload, tokens = parse_generate(raw)
                try:
                    result = metering.record(conn, tenant_id, key, payload, tokens, now_fn())
                except metering.QuotaExceeded as exc:
                    raise HttpError(429, "quota_exceeded", exc.message, {
                        "usage_type": exc.usage_type, "plan": exc.plan, "used": exc.used,
                        "requested": exc.requested, "limit": exc.limit, "retry_after_seconds": exc.retry_after,
                        "upgrade": "POST /billing/checkout"}, {"Retry-After": str(exc.retry_after)}) from None
                except metering.PaymentRequired as exc:
                    raise HttpError(402, "payment_required", exc.message, {"action": "update payment method"}) from None
                except metering.IdempotencyConflict as exc:
                    raise HttpError(409, "idempotency_key_reused", exc.message) from None
                headers = {"Idempotent-Replay": "true"} if result.replay else {}
                return JSONResponse(result.body, status_code=result.status, headers=headers)

        return await run_in_threadpool(work)

    async def get_usage(request: Request):
        def work() -> JSONResponse:
            with _connection(settings) as conn:
                tenant_id = authenticate(conn, request)
                return JSONResponse(usage.rollup(conn, tenant_id, now_fn()))
        return await run_in_threadpool(work)

    async def checkout(request: Request):
        def work() -> JSONResponse:
            with _connection(settings) as conn:
                tenant_id = authenticate(conn, request)
                if not (settings.stripe_secret_key and settings.stripe_pro_price_id):
                    raise HttpError(503, "stripe_not_configured",
                                    "Set STRIPE_SECRET_KEY and STRIPE_PRO_PRICE_ID (Stripe test mode) in .env.")
                sub = conn.execute("SELECT plan_id, status, stripe_customer_id FROM subscriptions WHERE tenant_id = ?",
                                   (tenant_id,)).fetchone()
                if sub["plan_id"] == "pro" and sub["status"] == "active":
                    raise HttpError(409, "already_subscribed", "This tenant is already on the Pro plan.")
                try:
                    session = stripe_client.create_checkout_session(
                        tenant_id=tenant_id, price_id=settings.stripe_pro_price_id,
                        success_url=f"{settings.public_base_url}/billing/success",
                        cancel_url=f"{settings.public_base_url}/billing/cancel",
                        customer_id=sub["stripe_customer_id"])
                except stripe_sync.StripeError as exc:
                    raise HttpError(502, "stripe_error", f"Stripe error: {exc.message}") from None
                return JSONResponse({"checkout_url": session["url"], "session_id": session["id"]})
        return await run_in_threadpool(work)

    async def stripe_webhook(request: Request):
        raw = await request.body()  # RAW bytes: the signature covers exactly these bytes

        def work() -> JSONResponse:
            if not settings.stripe_webhook_secret:
                raise HttpError(503, "webhook_not_configured", "STRIPE_WEBHOOK_SECRET is not set.")
            try:
                stripe_sync.verify_signature(raw, request.headers.get("stripe-signature"), settings.stripe_webhook_secret)
            except stripe_sync.SignatureError as exc:
                raise HttpError(400, "invalid_signature", f"Webhook signature check failed: {exc}.") from None
            try:
                event = json.loads(raw)
            except ValueError:
                raise HttpError(400, "invalid_json", "Webhook body must be valid JSON.") from None
            try:
                with _connection(settings) as conn:
                    outcome = stripe_sync.handle_event(conn, event)
            except stripe_sync.BadEvent as exc:
                raise HttpError(400, "invalid_event", str(exc)) from None
            return JSONResponse({"received": True, "status": outcome})
        return await run_in_threadpool(work)

    async def billing_return(request: Request):
        return JSONResponse({"message": "Checkout finished. Your plan updates when Stripe's webhook arrives; "
                                        "check GET /usage in a few seconds."})

    async def on_http_error(request: Request, exc: HttpError):
        return error_response(exc.status, exc.code, exc.message, exc.details, exc.headers)

    async def on_framework_error(request: Request, exc: StarletteHTTPException):
        # Starlette's own 404 (no such route) and 405 (wrong method) get the same JSON shape as every other error
        codes = {404: "not_found", 405: "method_not_allowed"}
        return error_response(exc.status_code, codes.get(exc.status_code, "http_error"), str(exc.detail),
                              headers=getattr(exc, "headers", None))

    async def on_unexpected(request: Request, exc: Exception):
        return error_response(500, "internal_error", "Something went wrong on our side. Nothing was recorded.")

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/generate", generate, methods=["POST"]),
            Route("/usage", get_usage, methods=["GET"]),
            Route("/billing/checkout", checkout, methods=["POST"]),
            Route("/billing/success", billing_return, methods=["GET"]),
            Route("/billing/cancel", billing_return, methods=["GET"]),
            Route("/webhooks/stripe", stripe_webhook, methods=["POST"]),
        ],
        exception_handlers={HttpError: on_http_error, StarletteHTTPException: on_framework_error,
                            Exception: on_unexpected},
    )


@contextmanager
def _connection(settings: Settings):
    conn = db.connect(settings.database_path)
    try:
        yield conn
    finally:
        conn.close()
