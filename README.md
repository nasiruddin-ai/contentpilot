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
| Prompt 5: Source management + secure URL fetching (SSRF protection) | Done |
| Prompt 6: Research engine (fetch, parse, clean, dedupe, store; Celery) | Done |
| Prompt 7: AI provider abstraction (AIProvider, AIService, validation, run logging) | Done, live-verified 2026-09-24 |
| Prompt 8: Topic detection and content opportunities | Done, live-verified 2026-09-24 |
| Prompt 9: AI content generation (hook, outline, draft, platform adaptation, quality check) | Done, live-verified 2026-09-24 |
| Prompt 10: Visual generation (image provider, storage, Pillow, carousels) | Done for rendered designs, live-verified 2026-09-24. AI-image types wait for a paid Gemini plan |
| Prompt 11: Content editor and calendar (edit, regenerate, approve, reject, schedule) | Done, live-verified 2026-09-24 |
| Prompt 12: Social OAuth and publishing adapters | LinkedIn and X built and tested against mocks of their documented APIs. Live tests pending: the LinkedIn app is "disabled" by LinkedIn; X API access is paid |
| Prompt 13: Scheduled publishing workers (retries, backoff, idempotency, notifications) | Done; beat sweep verified in Docker 2026-09-24 |
| Prompt 14: Analytics sync and learning loop | Next |

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
- Sources: CRUD at `/api/v1/sources` plus a live connectivity test, on an SSRF-safe fetcher (see below)
- Research engine: Celery workers collect articles from sources on a schedule, deduplicate and store them (see below)
- AI layer: provider-neutral `AIService` with Gemini, retries, validated JSON output and per-call cost logging (see below)
- Intelligence: AI research analysis, embeddings (pgvector), topic clustering, trend detection and grounded content opportunities (see below)
- Content generation: 4-stage writing pipeline with platform adaptation, deterministic quality checks and an AI editor (see below)
- Visuals: brand-styled quote cards, minimal graphics, infographics and carousels (PNG + LinkedIn-ready PDF) rendered with Pillow, stored behind a storage interface (see below)
- Editor and calendar: edit, AI revise (7 actions), approve, reject, version history with restore, scheduling (see below)
- LinkedIn: OAuth connect with encrypted tokens, publish now (text, image, carousel PDF as a document post) (see below)
- X: OAuth 2.0 with PKCE and automatic token refresh, text posts
- Scheduled publishing: every-minute sweep, retries with backoff, duplicate-safe failure handling, in-app notifications
- 315 pytest tests (the database/Redis ones need Docker running; they are skipped otherwise)

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

## Sources and safe fetching

| Endpoint | Notes |
|---|---|
| `GET /api/v1/sources?brand_id=...` | Sources for one of your brands |
| `POST /api/v1/sources` | `brand_id`, `name`, `url`, `source_type`, optional `fetch_frequency` (hourly, every_6_hours, daily, weekly), `active` |
| `GET` / `PATCH` / `DELETE /api/v1/sources/{id}` | Changing the URL re-validates it and resets status to `pending` |
| `POST /api/v1/sources/{id}/test` | Fetches now and previews it: feed items, or page title, excerpt and advertised RSS feeds. Updates `status` / `error_count` / `last_error`. 30 per 10 min per IP |

- Supported types now: `website`, `blog`, `rss`, `news`, `user_url`.
  - `youtube`, `reddit`, `search` and `api` need official API integrations, so they are refused for now.
  - For a YouTube channel, add its RSS feed as `rss`.
- Up to 50 sources per brand. URLs are normalized, so the same URL can't be added twice.

**SSRF protection** (`app/utils/urls.py`, `app/research/fetcher.py`), checked on creation and again on every fetch and every redirect hop:

- Only `http`/`https`, on ports 80/443. No usernames or passwords in the URL.
- Blocks localhost and internal suffixes (`.local`, `.internal`, ...).
- The host must resolve **only** to public IPs. Private, loopback, link-local/cloud-metadata, CGNAT, multicast and reserved ranges are refused, including IPv4 hidden inside IPv6.
- The connection goes to the exact IP that was checked; TLS still verifies the real hostname. This defeats DNS rebinding.
- Proxies from the environment are ignored.

Other fetch rules:

- robots.txt is honoured and cached for 24h.
- Only HTML and RSS/Atom/XML responses are accepted, up to 2 MB after decompression.
- 15s read / 5s connect timeouts and a 60s overall deadline.
- Retries 429/502/503/504 and network errors twice with exponential backoff.
- At most 30 requests per minute per website.

Extracted content is plain text only (trafilatura, with a BeautifulSoup fallback). Scripts and HTML are never stored.

## Research engine

Pipeline (spec section 21), in `app/research/pipeline.py` and `app/services/research_service.py`:

1. **Fetch**, with the SSRF-safe fetcher.
2. **Parse** feeds or pages.
3. **Clean** to plain text.
4. **Extract metadata**: title, author, published date, canonical URL, site name.
5. **Deduplicate.**
6. **Store** in `research_items`.

What each source type does:

- **Feeds:** the newest 20 entries.
  - Each new article's page is opened for its full text. If it can't be read, the feed summary is kept instead.
  - Articles already stored are never downloaded again.
  - Feed links go through the same SSRF checks. Tracking parameters (`utm_*`, `fbclid`, ...) are removed.
  - Article opening stops after 240s, so a run stays bounded.
- **Websites/blogs:** if the page advertises an RSS feed, the feed is used. Otherwise the page is stored as one snapshot and updated when its text changes.
  - `<link rel=canonical>` is only trusted when it's on the same site.
- **Deduplication** (section 26):
  - Same URL: unchanged is skipped, changed is updated.
  - Same text under any URL (SHA-256 of normalized text): skipped.
  - Near-duplicates: 64-bit SimHash within 3 bits, for texts of 2,000+ characters from the last 90 days. On shorter texts SimHash was measured to be unreliable.
  - Semantic duplicates need embeddings (Prompt 8).

Jobs:

- Every fetch is a `research_runs` row: queued, then running, then succeeded or failed, with found/new/updated/duplicate counts and the error.
- Celery beat runs `research.refresh_due_sources` every 5 minutes. It queues active sources whose `next_fetch_at` is due.
  - After success, the next fetch is one interval later.
  - After failures, the interval doubles each time, up to 16x.
  - New sources, and sources whose URL changed, are fetched at the next tick.
- A Redis lock stops the same source running twice at once.
- A source already in flight returns its existing run instead of queueing another.
- Running a finished run again does nothing, so redelivered messages are safe.

| Endpoint | Notes |
|---|---|
| `POST /api/v1/sources/{id}/fetch` | Queue one source (202). 30 per 10 min per IP |
| `POST /api/v1/research/run` | `{"brand_id"}`: queue every active source of the brand (202) |
| `GET /api/v1/research/runs?brand_id=` / `GET /api/v1/research/runs/{id}` | Job status and counts |
| `GET /api/v1/research?brand_id=&source_id=&q=&limit=&offset=` | Items, newest first; `q` searches titles and summaries |
| `GET /api/v1/research/{id}` | One item, including its full `clean_text` |

## AI layer

`app/ai/`: every AI call goes Router → Service → `AIService` → `AIProvider` (spec section 27). Nothing else talks to a vendor.

- **Provider:** Google Gemini through the [Interactions API](https://ai.google.dev/gemini-api/docs/interactions-overview), which Google recommends for new projects. Embeddings use `batchEmbedContents`.
  - Requests set `store: false`, so prompts aren't kept on Google's side.
  - Other providers can be added by implementing `AIProvider`. None are faked.
- **Model routing** (section 68): `ModelTier.FAST` uses `gemini-3.5-flash-lite` for summaries and extraction. `ModelTier.QUALITY` uses `gemini-3.8-flash` for writing. Embeddings use `gemini-embedding-2`, 768 dims, normalized. All can be changed via `GEMINI_*` env vars.
- **Fallback models:** if a model is overloaded, rate limited or times out after its retries, the call moves to a backup: `gemini-3.5-flash` for quality, `gemini-3.1-flash-lite` for fast. Set `GEMINI_*_FALLBACK_MODEL` to change them, or leave one empty to disable it.
  - Free-tier facts seen live on 2026-09-24: `gemini-3.8-flash` allows 5 requests per minute and was often overloaded (503).
  - Hidden "thinking" tokens count against `max_output_tokens` and are billed. About 400 were used for a one-sentence answer. Features should set `thinking_level` and leave generous output limits; a response cut off at the limit is an `AI_OUTPUT_TRUNCATED` error.
- **Structured output** (section 30): `generate_structured(schema=MyModel)` sends the JSON Schema and validates the reply with Pydantic.
  - An invalid reply gets one repair attempt, told what was wrong. The bad output isn't echoed back, since it may contain source text.
  - It then fails with `AI_INVALID_OUTPUT`.
- **Retries:** timeouts, network errors, 5xx and 429 are retried up to 3 attempts with exponential backoff and jitter. A provider `retryDelay` is honoured up to 20s; beyond that the call fails fast.
- **Errors** reach API clients in the standard envelope. Provider messages have the API key scrubbed out, because Google's errors quote it. Codes: `AI_NOT_CONFIGURED` (503), `AI_RATE_LIMITED` (429), others 502.
- **Logging** (section 69): every call is a row in `ai_runs`: user, brand, task, provider, model, input/output tokens (thinking counts as output), estimated USD cost, duration, attempts, status (`succeeded`, `failed`, `invalid_output`) and error.
  - Costs come from the list prices in `app/ai/pricing.py` (read 2026-09-24). Unknown models get `NULL`, never a guess.
- Image generation arrives with visuals (Prompt 10).

**Live check** (after setting `GOOGLE_AI_API_KEY` in `.env` and restarting):

```bash
docker compose up -d
docker compose exec worker python -m app.cli.check_ai
```

## Topics and content opportunities

Flow (spec section 35): research → AI analysis → topic clustering → trend detection → opportunity generation → validation → database. It is user-triggered (Copilot mode), so AI spend never runs by itself.

1. **Analysis** (fast model, 10 articles per call, at most 50 per run): each unanalyzed research item gets an AI summary, 1-3 topic labels, keywords and entities. It also gets a 768-dim embedding stored in pgvector, which will be used for search by meaning (RAG) later.
   - Article text is always wrapped in `<source>` blocks and declared untrusted. Text that tries to open or close a block is neutralized (spec section 40).
   - The model is shown the brand's existing topic labels and asked to reuse them. Live, this cut fragmentation from 47 topics to 24 for the same 35 articles.
2. **Clustering:** a label joins an existing topic if it has the same name or cosine similarity ≥ 0.86. That threshold was measured on 2026-09-24: rewordings scored 0.87-0.95, different topics 0.48-0.72.
3. **Trends**, computed from counts rather than asked of a model: articles in the last 7 days vs the 7 before, distinct sources, and first seen. Labels are `new`, `rising`, `recurring` or `steady`.
4. **Generation** (quality model, with fallback): the brand kit plus the top 8 trending topics, each with its 3 newest articles.
   - The rules forbid invented statistics, events or quotes, and forbid claiming projects, clients or results the brand kit doesn't mention. Case-study and story ideas are written as "share a story about…", never a made-up story.
5. **Validation:** an idea is dropped if its topic is unknown, if none of its cited sources were actually shown to the model, if it contains a banned word, or if it is a near-duplicate (cosine ≥ 0.90) of an idea from the last 90 days or of an earlier idea in the same run. Unknown platforms are filtered out and scores are clamped.
6. **Scores** (0-100): relevance and brand fit come from the model. Freshness is computed from the newest source's age (0 after 30 days). Novelty is computed from similarity to earlier ideas. Priority = 35% relevance + 25% brand fit + 20% freshness + 20% novelty.

| Endpoint | Notes |
|---|---|
| `POST /api/v1/opportunities/generate` | `{"brand_id", "count": 1-10}`. Queues an analyze-and-generate run (202); returns the in-flight run if one exists. 10 per hour per IP |
| `GET /api/v1/opportunities/runs?brand_id=` / `/runs/{id}` | Status, items analyzed, topics created, ideas created and rejected, error |
| `GET /api/v1/opportunities?brand_id=&status=&limit=&offset=` | Highest priority first; dismissed ideas hidden unless `status=dismissed` |
| `GET /api/v1/opportunities/{id}` | Includes its source research items |
| `PATCH /api/v1/opportunities/{id}` | `{"status": "new" / "saved" / "dismissed" / "used"}` |
| `GET /api/v1/topics?brand_id=` | Topics with trend signals |

Live cost on 2026-09-24 for 35 articles and 5 ideas: about $0.04 at paid list prices (free tier: $0), in about 60 seconds.

## Content generation

Pipeline (spec section 37), in `app/services/content_service.py`: opportunity → research context (its cited sources) → brand context → **plan** (3 hook options + chosen hook, key points each tied to a source, CTA, outline) → **master draft** → **platform adaptation** → **review** → posts.

- Plan, draft and adaptation use the quality model; the editor uses the fast model. That's 4 AI calls per generation.
- Adaptation rewrites for each platform rather than truncating (section 38). One call covers all requested platforms.
- Writer rules (section 39): original wording, only facts from the sources, no invented numbers, quotes, clients or results, the brand's tone and preferred words, no banned words, no clichés. Sources are `<source>` blocks marked untrusted.
- **Deterministic checks** (`app/services/content_quality.py`) run before and after the editor:
  - Platform limits: LinkedIn 3,000 characters and 5 hashtags. X 280 characters (each post for threads) and 2 hashtags. Instagram 2,200 characters and 30 hashtags. Facebook 63,206 characters. Reddit title 300 characters and no hashtags. YouTube title 100 characters, description 5,000.
  - Banned words.
  - A list of generic AI phrases.
  - **Numbers not found in the cited sources** (likely invented statistics).
- **Editor + brand check** (section 31): fixes what the checks found plus unsupported claims, filler and platform fit.
  - An edit is kept only if it doesn't add errors.
  - If the editor call fails, the posts are still saved with a `not_reviewed` warning.
- Posts are saved as `draft` with any remaining `quality_issues` (`error` / `warning`) for a human to review. The opportunity is marked `used`.

| Endpoint | Notes |
|---|---|
| `POST /api/v1/posts/generate` | `{"opportunity_id", "platforms"?}`; platforms default to the opportunity's recommendation. Queues a generation (202) and returns the in-flight one if it exists. 20 per hour per IP |
| `GET /api/v1/posts/generations/{id}` | Status, hook options, outline, master draft, error, `post_ids` |
| `GET /api/v1/posts?brand_id=&status=&platform=&limit=&offset=` | Newest first; archived hidden unless `status=archived`. Each post includes `full_text` exactly as it would be published |
| `GET /api/v1/posts/{id}` | Includes its source research items |
| `DELETE /api/v1/posts/{id}` | Not allowed once publishing or published |

Editing, regeneration, approve/reject and scheduling are Prompt 11.

Live on 2026-09-24: one idea → LinkedIn, X (270 characters) and Reddit posts in 52s, about $0.04 at paid list prices, no quality issues.
- On the free tier `gemini-3.8-flash` was rate-limited or overloaded on every writing call, so the fallback `gemini-3.5-flash` wrote everything, adding up to 15s per failed attempt.
- To skip those attempts on the free tier, set `GEMINI_QUALITY_MODEL=gemini-3.5-flash`. On a paid plan `gemini-3.8-flash` is the cheaper, better default.

## Visuals

Pipeline (spec section 42): post → visual agent (fast model writes the on-image copy and alt text) → layout, chosen by code → Pillow render in the brand kit → storage → brand check → linked to the post.

- **Rendered types (free):**
  - `quote_card`: the most quotable line, attributed to the brand.
  - `minimal_graphic`: headline and one supporting line.
  - `infographic`: title and 3-5 numbered points.
  - `carousel`: 3-8 slides. Hook slide, one idea per slide, then a CTA slide. Also exported as a multi-page PDF for LinkedIn document posts.
- **AI-image types** (`photo`, `illustration`, `cartoon`, `meme`, `product_showcase`) return `VISUAL_TYPE_NEEDS_IMAGE_MODEL`.
  - Gemini image models report "limit: 0" on the free tier (checked live 2026-09-24).
  - The Gemini image integration will be built and tested when a paid plan is available. It is not faked.
  - Any "limit: 0" reply is now `AI_NOT_IN_PLAN` (402). It is not retried, but it does fall back to the backup model.
- **Brand styling:**
  - Colours: the primary colour is the background. Text is white or near-black, whichever has the higher WCAG contrast. The accent is used unless it would be invisible on the background.
  - Fonts: the brand's heading/body fonts if installed, otherwise Inter, then DejaVu (both installed in the Docker image).
  - Text shrinks until it fits. If it still doesn't fit at the minimum size it is cut with "…" and flagged `text_truncated`.
  - Banned words are flagged as `banned_word` errors.
- **Sizes:** `1:1` 1080×1080, `4:5` 1080×1350, `16:9` 1600×900, `9:16` 1080×1920, `1.91:1` 1200×628.
  - Defaults: Instagram 4:5; LinkedIn 1:1, or 4:5 for carousels; X and YouTube 16:9; Facebook and Reddit 1:1.
- **Storage** (`app/storage`): an interface with `save`, `read`, `delete` and `url`.
  - `local` writes to `MEDIA_ROOT` (the `media_data` Docker volume, shared by API and workers) and the API serves it at `/media`.
  - Each version gets a fresh random folder, so regenerated images get new URLs. The previous version's files are deleted only after the new one is saved.
  - Keys are validated so a path can't escape the media root.
  - Cloudinary or S3/R2 can be added as another `Storage` implementation.
- **Known gap:** deleting a brand or user removes its database rows but not its media files yet. That cleanup is still to do.

| Endpoint | Notes |
|---|---|
| `POST /api/v1/visuals/generate` | `{"post_id", "visual_type", "aspect_ratio"?}`. Queued (202); returns the in-flight job for that post if there is one. 30 per hour per IP |
| `GET /api/v1/visuals/{id}` | Status, on-image copy, alt text, assets (slides, PDF, thumbnail), brand-check issues |
| `POST /api/v1/visuals/{id}/regenerate` | New copy and images, same visual ID |
| `GET /api/v1/visuals/{id}/download` | ZIP of the slides (and carousel PDF) |

Live on 2026-09-24: a 7-slide carousel (with a 7-page PDF) and a quote card for a Squareko post, in Inter with the brand colours, and no issues. The fast model writes the copy, so each visual is one cheap AI call.

## Editor and calendar

Editor actions (spec section 63), in `app/services/editor_service.py`:

| Action | Rule |
|---|---|
| Edit (`PATCH /posts/{id}`: hook, body, cta, hashtags) | Quality checks re-run. The post keeps its status, unless the edit introduces an error-level issue; then an approved or scheduled post goes back to draft and off the calendar |
| AI revise (`POST /posts/{id}/regenerate`) | Actions: `rewrite`, `shorten`, `expand`, `change_tone` (needs `instruction`), `new_hook`, `new_cta`, `custom` (needs `instruction`). One quality-model call using the post's sources and the brand kit. The result is always a **draft**: AI-written text needs a person's approval |
| Approve (`POST /posts/{id}/approve`) | Refused with `POST_HAS_ERRORS` while any error-level quality issue remains |
| Reject (`POST /posts/{id}/reject`, optional `reason`) | Archives the post and keeps the reason |
| Versions (`GET /posts/{id}/versions`, `POST /posts/{id}/versions/{vid}/restore`) | The previous content is saved before every edit, revision or restore, so every change can be undone |

- Only one AI revision can run per post at a time; track it with `GET /posts/revisions/{id}`.
- If the post is edited while a revision is running, the AI result is discarded (`failed`, "the post changed") rather than overwriting the edit.
- The editor's free-text instruction can't break out of its place in the prompt or override the writing rules.
- Posts that are publishing, published or archived can't be changed.

Calendar (spec sections 55, 58 and 62), in `app/services/calendar_service.py`. A post's `scheduled_at` **is** its calendar slot; there is no separate table to fall out of sync.

| Endpoint | Notes |
|---|---|
| `GET /calendar?brand_id=&start=&end=` | Scheduled and published posts in the range (ISO 8601 with timezone, at most 92 days), with visual thumbnails |
| `POST /calendar/items` | `{"post_id", "scheduled_at"}`. Only approved posts. The time needs a timezone, must be at least 1 minute ahead and at most a year ahead; it is stored in UTC |
| `PATCH /calendar/items/{post_id}` | Reschedule |
| `DELETE /calendar/items/{post_id}` | Unschedule; the post stays approved |

Scheduling only plans a post for now. Publishing it at that time comes with the social publishing adapters (Prompts 12-13).

Live on 2026-09-24:
- An approved LinkedIn post was shortened (365 characters) and then rewritten "playful and upbeat". Both came back as drafts with no quality issues, and both earlier versions were kept.
- After re-approval it was scheduled and appeared in the week's calendar.

## LinkedIn connection and publishing

Built from LinkedIn's current docs (read 2026-09-24): the 3-legged OAuth flow, the Posts API (`/rest/posts`, which replaced `ugcPosts`), the Images and Documents upload APIs, and the "little" text format.

- **Permissions:** `openid profile w_member_social`. These come from the self-serve products **Share on LinkedIn** and **Sign In with LinkedIn using OpenID Connect**, and allow posting to the connected member's **personal profile**. Company-page posting (`w_organization_social`) needs LinkedIn partner approval.
- **Tokens** last 60 days. Refresh tokens are only issued to approved LinkedIn partners, so members reconnect when a token expires.
  - The expiry date is stored. Expired or rejected (401) connections show as `reconnect_required`, and publishing is refused until the member reconnects.
- **Security:**
  - Tokens are Fernet-encrypted at rest (`TOKEN_ENCRYPTION_KEY`; comma-separate several keys to rotate) and never returned by the API.
  - The OAuth `state` is random, bound to the user and brand in Redis for 10 minutes, and single-use (GETDEL), so forged or replayed callbacks fail.
  - The callback page escapes everything it shows.
  - The access token is only sent to LinkedIn hosts, including when uploading media, and it is scrubbed from error messages.
- **Post text** is converted to LinkedIn's "little" format: reserved characters (`\ | { } @ [ ] ( ) < > # * _ ~`) are escaped and hashtags use `{hashtag|\#|Tag}`.
- **Media:** a carousel visual is uploaded as a **document** (its PDF), which LinkedIn shows as a swipeable carousel. A single-image visual is uploaded as an **image**, with its alt text. Posts without a visual are text posts.
- **`LINKEDIN_API_VERSION`** defaults to `202609`. LinkedIn retires versions about a year after release, so update it periodically.

| Endpoint | Notes |
|---|---|
| `GET /api/v1/social/accounts?brand_id=` | Connected accounts (name, status, token expiry; never tokens) |
| `POST /api/v1/social/linkedin/connect` | `{"brand_id"}` → `authorization_url`; open it in the browser |
| `GET /api/v1/social/linkedin/callback` | LinkedIn redirects here; shows a "Connected" page |
| `POST /api/v1/social/linkedin/disconnect` | `{"brand_id"}` |
| `POST /api/v1/publishing/{post_id}/publish` | Approved or scheduled LinkedIn posts without errors → `publishing` → `published` (with `external_post_id`) or `failed` (with `publish_error`) |

A post is never published twice: a job that runs again for a post that already has an `external_post_id` does nothing.

Automatic publishing at `scheduled_at`, with retries and backoff, is Prompt 13.

**LinkedIn app setup** (one time):
1. Go to https://www.linkedin.com/developers/apps, click **Create app**, and link it to your LinkedIn Page.
2. On the **Products** tab, add **Share on LinkedIn** and **Sign In with LinkedIn using OpenID Connect**.
3. On the **Auth** tab, add the redirect URL `http://localhost:8000/api/v1/social/linkedin/callback` (use your real `API_URL` in production).
4. Copy the Client ID and Client Secret into `.env` as `LINKEDIN_CLIENT_ID` and `LINKEDIN_CLIENT_SECRET`, then run `docker compose up -d --force-recreate api worker beat`.

## X connection

`app/integrations/x.py`: X's OAuth 2.0 authorization code flow **with PKCE**. The verifier is kept only in the single-use Redis state entry, and the challenge uses S256.

- Scopes: `tweet.read tweet.write users.read offline.access`.
- Access tokens last 2 hours. The refresh token (from `offline.access`) is used automatically when a token is within 5 minutes of expiry. X rotates refresh tokens, so the new one is always saved, under a row lock so two publishes can't spend the same one.
- Posts are text for now (`POST /2/tweets`), within the 280-character limit ContentPilot enforces. Images on X need X's media upload and come later.
- **Cost:** X has had no free API tier since February 2026. It is pay-per-use, about $0.015 per post or $0.20 per post with a link (third-party pricing guides, September 2026). An empty balance gives `NO_CREDITS`.
- **Setup:** at console.x.com, create an app. Under User authentication settings choose Read and write, Web App, and the callback `API_URL/api/v1/social/x/callback`. Put the **OAuth 2.0 Client ID and Client Secret** (not the API Key/Secret) in `.env` as `X_CLIENT_ID` and `X_CLIENT_SECRET`.

## Scheduled publishing and notifications

- **Sweep:** Celery beat runs `publishing.queue_due_posts` every minute. It moves `scheduled` posts whose `scheduled_at` has passed to `publishing` and queues them, using `SKIP LOCKED` so parallel sweeps don't collide.
- **One run per post:** each publish holds a Redis lock `lock:publish:{post_id}`, and a post that already has an `external_post_id` is never sent again.
- **Failure handling:** neither platform supports idempotency keys, so the rule is to never risk a duplicate.

| Failure | Could the post be live? | Action |
|---|---|---|
| Couldn't connect, 429, 502/503/504, or media upload failed | No | Retry after 1, 4, then 16 minutes; after 4 attempts fail with a notification |
| The create call timed out, the connection dropped, or it returned 500 | Maybe | `failed` with `PUBLISH_UNCERTAIN` and a notification asking the user to check their profile. No automatic retry |
| Login expired, no credits, permission denied, rejected | No | `failed` with the reason. An expired login also marks the account `reconnect_required` and sends a single reconnect notification |

- A post stuck in `publishing` for more than 30 minutes (for example, a crashed worker) is marked `PUBLISH_UNCERTAIN` rather than retried.
- Failed posts can be retried by hand with `POST /publishing/{id}/publish`.
- Published posts expose `published_url`.
- **Notifications** (in-app; email needs an email provider):
  - `GET /notifications?unread_only=`
  - `GET /notifications/unread-count`
  - `POST /notifications/{id}/read`
  - `POST /notifications/read-all`
  - Types: `post_published` (with a link), `publish_failed`, `publish_uncertain`, `reconnect_required`.

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
