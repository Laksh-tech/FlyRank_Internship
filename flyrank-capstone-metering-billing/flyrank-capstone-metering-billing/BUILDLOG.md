# BUILDLOG: where AI helped, where it was wrong, what I changed

This file is graded for **honesty**, not perfection. The sections marked `TODO(you)` can only be written by you:
they are your own account of what you understood and changed. Please do not leave them as they are, and do not copy
mine into them.

## How this project was built

- **Phase 1 (design):** `docs/DESIGN.md`, `app/metering.py`, `app/usage.py`, `app/pricing.py`, `app/config.py`,
  `app/db.py`, `migrations/001_init.sql` and the metering/pricing tests were written in an earlier session.
  `TODO(you)`: say how much of that was yours and how much AI-assisted.
- **Phases 3-4 (Stripe, HTTP API, job, docs):** written by Claude (Anthropic's AI) in one session on 2026-11-25:
  `app/stripe_sync.py`, `app/api.py`, `app/jobs.py`, `app/seed.py`, `app/main.py`, the webhook/API/job tests, `README.md`,
  `EVIDENCE.md`, `capstone.yaml`, `.env.example`, `scripts/sign_webhook.py`.

## Where AI helped

- Wrote the first draft of the Stripe webhook signature check from Stripe's documented scheme (HMAC-SHA256 over
  `"<timestamp>.<raw body>"`, 5-minute tolerance) using only the standard library, because the Stripe SDK was not
  installable in the build environment.
- Designed the webhook transaction so the event id and the plan change commit together (a failed apply rolls back the id,
  so Stripe's retry is not skipped as a duplicate), and the stale-event guard for out-of-order delivery.
- Wrote the live-server HTTP tests (one per acceptance probe) and ran every probe with real `curl` for `EVIDENCE.md`.

## Where AI was wrong (this is the useful part, so keep it accurate)

1. **It overwrote existing files.** While starting Phase 3 the AI wrote `config.py`, `db.py`, `pricing.py` and
   `migrations/001_init.sql` without reading them first, destroying the Nov 10 originals (not in git). It noticed
   from the tool output, rebuilt them from the compiled `.pyc` files, the existing tests and `DESIGN.md`, and confirmed
   all 31 original tests pass. The rebuilt files are equivalent in behaviour but **not byte-identical** (comments and the
   exact SQL column order may differ; the Stripe columns on `subscriptions` are new). **Lesson: commit early; read before write.**
2. **An incompatible first design.** Before it discovered the existing design, the AI drafted a different one (cached tokens
   billed *outside* `input_tokens`, a different schema, extra `plans.py`/`errors.py`). Those two files were deleted and
   the existing convention (cached tokens are *part of* `input_tokens`) was kept.
3. **A test helper bug.** The first HTTP test failed because uvicorn lowercases response header names
   (`idempotent-replay`, not `Idempotent-Replay`). The service was right; the test was wrong.
4. **A real service bug found by a test.** 404/405 responses came back as plain text while every other error was JSON.
   Fixed in `app/api.py` with a handler for Starlette's own HTTP errors.
5. **Framework substitution.** The brief suggests FastAPI. It was not installed and could not be installed in the build
   environment, so the API uses Starlette (the framework FastAPI is built on) and uvicorn. See "What I changed" below.

## What was NOT verified

- The Stripe Checkout creation call and subscription lookup (`StripeClient`) were never run against the real
  api.stripe.com; they are tested only against a fake. `EVIDENCE.md` #8 and the real-event half of #9 are marked TODO for that reason.
- Not tested with PostgreSQL (the design uses SQLite).

## What I changed or decided myself

`TODO(you)`: list at least 3 concrete things. Examples of honest entries: "changed the Pro price to ...", "I disagreed with
returning 402 for past_due and chose ... because ...", "I rewrote X because I did not understand it", "I ran the Stripe
CLI steps and found that ...". If you changed nothing, say that plainly.

## Things I can explain without looking (checklist for the evaluator's "explain 2-3 lines" question)

`TODO(you)`: tick these only once true.

- [ ] Why `BEGIN IMMEDIATE` makes "check quota, then insert" safe under concurrency (`app/db.py`, `app/metering.py`).
- [ ] Why the webhook signature needs the **raw** body, and why an empty `whsec_` secret must refuse everything (`app/stripe_sync.py`).
- [ ] Why the event id and the plan update share one transaction (`handle_event`).
- [ ] Why costs are stored as pico-USD and rounded once at rollup (`app/pricing.py`, `test_no_rounding_drift_on_tiny_events`).
- [ ] Why `used + requested <= limit` means call #1,000 is allowed and #1,001 is not.
