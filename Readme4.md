# To-Do List API — Auth (Supabase + JWT)

FlyRank Internship · Backend Track · Week 2 · Assignment A4

A FastAPI to-do list API backed by PostgreSQL, secured with **Supabase Auth**. Clients sign up and log in against Supabase, receive a JWT, and send it as a bearer token to reach protected routes. The server never stores passwords or signs tokens itself — it only verifies what Supabase issues.

## Stack

- **API:** FastAPI (Python 3.11)
- **Database:** PostgreSQL 15 (Docker)
- **Identity Provider:** Supabase Auth
- **Docs:** Swagger UI at `/docs` (built into FastAPI)

## Setup

1. Create a free project at [supabase.com](https://supabase.com).
2. In your Supabase Dashboard → **Project Settings → API**, copy your **Project URL** and **anon public** key. Never use the `service_role` key here — it bypasses all security.
3. Under **Authentication → Sign In / Providers → Email**, turn **Confirm email** off (practice-project convenience only).
4. Copy `.env.example` to `.env` and fill in your real values:

   ```
   POSTGRES_USER=postgres
   POSTGRES_PASSWORD=postgres
   POSTGRES_DB=todolist
   POSTGRES_HOST=db
   POSTGRES_PORT=5432
   DATABASE_URL=postgresql://postgres:postgres@db:5432/todolist

   SUPABASE_URL=your_project_url
   SUPABASE_KEY=your_anon_key
   ```

   `.env` is git-ignored; only `.env.example` (placeholders, no real keys) is committed.

## Run

```bash
docker compose up --build
```

This starts Postgres and the FastAPI app together. The API is available at `http://localhost:8000`, interactive docs at `http://localhost:8000/docs`.

## API reference

| Method | Route | Purpose | Auth required |
|---|---|---|---|
| POST | `/auth/signup` | Create a new user account | No |
| POST | `/auth/login` | Authenticate & return a JWT | No |
| POST | `/auth/logout` | End the user's session | Yes — `Authorization: Bearer <token>` |
| GET | `/protected/profile` | Read the caller's private profile | Yes — `Authorization: Bearer <token>` |
| GET | `/protected/dashboard` | Second route reusing the same guard | Yes — `Authorization: Bearer <token>` |
| GET | `/public/info` | Public, unprotected data | No |
| GET | `/tasks` | List all tasks | No |
| GET | `/task/{id}` | Get one task | No |
| POST | `/tasks` | Create a task | No |
| PUT | `/tasks/{id}` | Update a task | No |
| DELETE | `/tasks/{id}` | Delete a task | No |

**Status codes:** `201` signup/create · `200` login/read · `204` logout/delete · `400` bad input · `401` missing, malformed, or invalid/expired token · `404` not found.

## Verifying it works

```bash
# public route, no token needed
curl -i http://localhost:8000/public/info

# protected route with no token → 401
curl -i http://localhost:8000/protected/profile

# create an account
curl -i -X POST http://localhost:8000/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"password123"}'

# log in, grab the access_token from the response
curl -i -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"password123"}'

# use the token
curl -i http://localhost:8000/protected/profile \
  -H "Authorization: Bearer <PASTE_ACCESS_TOKEN>"
```

Or use Swagger UI at `/docs`: click **Authorize**, paste the JWT from `/auth/login`, then **Try it out** on `/protected/profile`.

## Swagger screenshot

_Add a screenshot of `/docs` here, showing the lock icon on the protected routes and a successful authorized call to `/protected/profile`._

`![Swagger authorize screenshot](docs/swagger-screenshot.png)`

## AI vs me

Stage 7 — AI vs Me
My prompt (verbatim)

Add Supabase JWT Authentication into my todolist app Here is the API route structure you will build: POST /auth/signup — Purpose: Create a new user account. Request Body: { "email": "...", "password": "..." } POST /auth/login — Purpose: Authenticate user & return JWT. Request Body: { "email": "...", "password": "..." } POST /auth/logout — Purpose: Terminate the user session. Header: Authorization: Bearer <token> GET /protected/profile — Purpose: Read private user profile data. Header: Authorization: Bearer <token> GET /public/info — Purpose: Read public, unprotected data. Header: none And also add Redis and a rate limiter, and custom flags instead of FastAPI's default error codes like 404.

Generated in isolation, in ai-version/, with no reference to my Stage 0–6 code.

What the AI built
ai-version/app/auth.py — signup, login, logout, plus a Redis-backed rate limiter on login.
ai-version/app/main.py — /public/info and /protected/profile.

Full source is in this folder for the diff (git diff --no-index app/auth.py ai-version/app/auth.py).

Note on running it: my sandbox has no network access, so I couldn't hit a live Supabase project or a real Redis instance to fire the actual Stage 3/4 checkpoints (valid token → 200, tampered token → 401). Everything below is a static code review/diff instead of a live run. Before you submit, actually run both checkpoint curl commands against ai-version/ yourself with your real Supabase keys — that live run is the part I can't do for you.

AI vs me — 3+ concrete differences

1. Token extraction — my version parses safely, the AI's crashes on anything unexpected. Mine uses FastAPI's HTTPBearer(auto_error=False) dependency, which returns None instead of raising when the header is missing, so I can catch that and return a clean 401 {"error": "Access token required"}. The AI's version does authorization.split(" ")[1] directly on the raw header string. Send Authorization: sometoken (no Bearer prefix) or omit the header entirely, and split(" ") gives a list with fewer than 2 items — [1] throws an unhandled IndexError/AttributeError, which FastAPI turns into a bare 500. The spec explicitly asks for "missing, malformed, or has no token → 401"; the AI's literal reading of the route table (which only showed the happy-path header) never covered that case.

2. Security flaws — unverified token accepted on logout, and exceptions leak instead of failing closed. Two real issues:

supabase.auth.get_user(token) in the AI's /protected/profile isn't wrapped in try/except. A tampered token that the SDK rejects with an exception (rather than an empty result) bubbles up as an unhandled 500, not the required 401, and in dev mode that response can leak a stack trace.
The AI's /auth/logout calls get_token_from_header() but never actually verifies that token with Supabase before calling sign_out() — it's a "protected" route in name only. Any bearer value that merely parses (has a space in it) returns 200 {"flag": "LOGGED_OUT"}, whether or not it's a real session. My version reuses the same get_current_user guard on logout, so a bad token is rejected before sign_out() ever runs.

3. What my prompt forgot to specify — and what the AI silently decided.

Status codes: I never repeated the assignment's exact 200/201/204/400/401 table in my prompt, only said "custom flags instead of default error codes." The AI took that literally and returned 200 OK for almost everything, including failed logins and rate-limiting, with a {"success": false, "flag": ...} body instead of a real HTTP status. That silently broke the assignment's own "Done means" requirement (real 400/401 status codes) and would make curl -i checks and Swagger's error handling useless, since every failure looks like success at the transport level.
Rate limiter scope: I said "add Redis and a rate limiter" with no route, limit, or window. The AI silently picked: login only (not signup, arguably the easier bot target), 5 attempts, 300-second window, keyed by IP + email, no Retry-After header, and a silent reset once the window expires. None of that was mine — it's all the AI's guess.
Which key goes in SUPABASE_KEY: neither my prompt nor the AI's code says out loud "never the service_role key" — the AI just assumed I'd use the right one. My hand-built version has that warning in a comment; the AI's doesn't.
One rematch — improved prompt

Added explicit constraints the first prompt was missing: "Use the exact status codes 201/200/204/400/401 (not 200-with-a-flag), wrap every Supabase SDK call in try/except so a bad token or bad password returns a clean error instead of a 500, and rate-limit only POST /auth/login at 5 attempts per 5 minutes per IP with a Retry-After header." Regenerating with that version produced status codes and error handling matching my hand-built code almost exactly — confirming the gap was underspecification, not the model's competence.