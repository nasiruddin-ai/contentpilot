# ContentPilot AI

AI content operating system: niche research → opportunities → original posts → visuals → review → calendar.
The full build spec is `ContentPilot_AI_Python_FastAPI_Build_Specification.md`; work proceeds one milestone at a time.

## Status

| Step (spec §86) | State |
|---|---|
| Prompt 1: architecture foundation | Done |
| Prompt 2: SQLAlchemy models + Alembic (User, Brand) | Done |
| Prompt 3: authentication and authorization | Done (email verification + password reset wait for an email provider) |
| Prompt 4: Brand management and Brand Kit | Done (logo is a URL until file storage lands) |
| Prompt 5: Source management + secure URL fetching (SSRF protection) | Next |

What exists now:

- FastAPI app factory (`backend/app/main.py`) with CORS, `/docs` (off in production)
- `GET /health` (liveness) and `GET /health/ready` (checks Postgres + Redis, 503 if either is down)
- Standard error envelope (`{"success": false, "error": {code, message, request_id}}`), no stack traces to clients
- `X-Request-ID` on every response; structured JSON logs with request/job context
- Typed settings from env / `.env` (`backend/app/core/config.py`); secrets are `SecretStr`
- SQLAlchemy 2.x: async engine for the API, sync session for Celery, shared `Base` with naming conventions
- Async Redis client
- Celery app with JSON-only serialization, late acks, time limits, per-task log lines, and a `system.ping` task
- Docker: `api`, `worker`, `beat`, `postgres` (pgvector), `redis`
- `users` and `brands` tables (`backend/app/models/`) with Alembic migrations (`backend/migrations/`)
- Auth: register, login, refresh, logout, `GET /api/v1/users/me`, and a `CurrentUser` dependency for protected routes (see below)
- Brands: CRUD at `/api/v1/brands` with the full brand kit and content pillar mix (see below)
- 76 pytest tests (the database/Redis ones need Docker running; they are skipped otherwise)

## Run with Docker (recommended)

Requires Docker Desktop.

```bash
cp .env.example .env
docker compose up --build
```

The `migrate` service applies database migrations on every start; the API waits for it.

- API: http://localhost:8000/docs
- Readiness: http://localhost:8000/health/ready should return `"status": "ok"`

## Run without Docker

You need Postgres and Redis running somewhere; point `DATABASE_URL` and `REDIS_URL` in `.env` at them.

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows; use: source .venv/bin/activate on macOS/Linux
pip install -r requirements-dev.txt

uvicorn app.main:app --reload                               # API
celery -A app.workers.celery_app worker --loglevel=info --pool=solo   # worker (--pool=solo on Windows)
celery -A app.workers.celery_app beat --loglevel=info       # scheduler
```

## Authentication

| Endpoint | Notes |
|---|---|
| `POST /api/v1/auth/register` | name, email, password (10-128 chars). Signs the user in. 5 per hour per IP |
| `POST /api/v1/auth/login` | 20 per 15 min per IP, 5 per 15 min per email |
| `POST /api/v1/auth/refresh` | Rotates the refresh token |
| `POST /api/v1/auth/logout` | Ends the session |
| `GET /api/v1/users/me` | Current user, 401 if not signed in |

How it works:

- Passwords are hashed with Argon2id.
- Tokens live only in HTTP-only cookies, never in response bodies or JavaScript.
  - `cp_access`: 15-minute signed JWT, `SameSite=Lax`.
  - `cp_refresh`: 30-day random token, stored only as a SHA-256 hash, sent only to `/api/v1/auth`, `SameSite=Strict`.
- Every refresh revokes the old token. If a revoked token is used again, the whole session is ended (stolen-token detection).
- Disabled users are locked out immediately, even with a valid access token.
- State-changing requests from an `Origin` not in `CORS_ORIGINS`/`APP_URL`/`API_URL` get 403 (CSRF defense on top of SameSite).
- Stale refresh tokens are purged nightly at 03:00 UTC by Celery beat.
- `JWT_SECRET` is required; staging/production refuse to start unless it is at least 32 characters.

To protect a route: add a `user: CurrentUser` parameter (from `app.api.deps`).

**Deployment note:** the frontend and API must share a registrable domain (e.g. `app.example.com` and `api.example.com`), or the frontend must proxy `/api` to the API. Otherwise browsers won't send the cookies.

Not built yet: email verification and password reset. Both need an email provider, which the spec schedules with notifications.

## Brands and brand kit

| Endpoint | Notes |
|---|---|
| `GET /api/v1/brands` | The signed-in user's brands, oldest first |
| `POST /api/v1/brands` | Only `name` is required |
| `GET /api/v1/brands/{id}` | |
| `PATCH /api/v1/brands/{id}` | Changes only the fields sent; `null` clears optional fields |
| `DELETE /api/v1/brands/{id}` | Also deletes its content pillars |

Brand kit fields: website, industry, description, audience, market, goals, tone, visual style, logo URL, primary/secondary/accent colors (`#RRGGBB`, stored uppercase), heading/body fonts, preferred words, banned words.

- List fields are trimmed and de-duplicated case-insensitively. Tone, goals and visual style allow 20 items each; the word lists allow 200. Each item can be up to 60 characters.
- A word can't be both preferred and banned.
- **Content pillars** (spec section 36) are a percentage mix across: educational, opinion, story, how_to, case_study, comparison, industry_insight, faq, behind_the_scenes, promotion, community.
  - The weights must total 100.
  - If omitted on create, the brand gets the spec's default mix (40/20/15/15/10). Send `[]` for no preference.
  - On PATCH, `content_pillars` replaces the whole mix.
- Other users' brands always return 404, never 403, so their existence isn't revealed.

## Database migrations

Postgres from Docker is on **port 5433** and Redis on **6380** on your PC (5432 and 6379 are used by other software on this machine).

```bash
cd backend
alembic revision --autogenerate -m "describe the change"   # after changing a model; review the file it creates
alembic upgrade head                                       # apply
alembic downgrade -1                                       # undo the last one
```

Never change the database schema by hand.

## Tests

```bash
cd backend
pytest
```

Database tests use a separate `contentpilot_test` database (created automatically) and never touch dev data. Without Postgres running they are skipped.

## Layout

```text
backend/app/
  api/        health.py, v1/ (feature routers land here)
  core/       config, database, redis, logging, middleware, errors
  services/   business logic (routes stay thin)
  workers/    celery_app.py + task modules
  models/ schemas/ ai/ research/ integrations/ utils/   (filled in by later milestones)
frontend/     Next.js app (not started)
```
