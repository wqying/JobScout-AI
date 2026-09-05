# JobScout AI

JobScout AI is a private, local-first company-discovery and job-monitoring system for international
students. This repository currently completes **Milestone 6: source recovery and alternate
observations** from [`DESIGN_DOC.md`](DESIGN_DOC.md). Telemetry, the operations page, and the load
test remain deferred to Milestone 7.

The application includes a Next.js web app, FastAPI, PostgreSQL 16, Redis, Celery worker and Beat,
the complete V1 SQLAlchemy/Alembic schema, localhost-only Compose networking, local profile and
filter settings, a saved-company catalog with explicit confirmation, deterministic careers-page
detection, an idempotent DOL importer, and a two-call OpenAI Responses workflow with source
validation, cost traces, caching, and daily quotas. Saved supported sources are polled by Celery
with database leases, per-domain Redis rate limits, conditional requests, lifecycle snapshots, and
failure-safe closure rules. Matching new openings become grouped in-app and optional email alerts
through a transactional outbox with bounded retries. SmartRecruiters is supported alongside
Greenhouse, Lever, and Ashby. Blocked or broken sources can be replaced with a confirmed structured
provider URL; unsupported sources can use local review reminders or direct local `.eml` imports.

The browser uses the operating system's clock for all displayed times and calendar-day behavior.
There is no JobScout timezone preference: Daily history and the uncached-search quota roll over at
12:00am on the local machine.

## Prerequisites

- Docker Desktop with Compose v2
- Node.js 22.13 or newer
- pnpm 11.24.0
- Python 3.12
- uv 0.12 or newer

The repository pins pnpm in `package.json` and Python packages in `services/api/uv.lock`. With a
recent Corepack, enable the pinned pnpm version with:

```bash
corepack enable
corepack install
```

If an older Corepack reports a package-signing-key error, either update Corepack or run pnpm through
the pinned package without changing project files:

```bash
npx -y pnpm@11.24.0 --version
```

## First setup

1. Copy the environment template and add your server-side OpenAI API key.

   ```bash
   cp .env.example .env
   ```

   Open `.env` and set:

   ```text
   OPENAI_API_KEY=your-key-here
   ```

   The configured `gpt-5.6-luna` defaults favor lower cost. Model IDs and pricing rates remain
   environment configuration so they can be updated without changing domain code. Never commit
   `.env`.

2. Install locked dependencies.

   ```bash
   pnpm install --frozen-lockfile
   cd services/api
   uv sync --frozen --all-groups
   cd ../..
   ```

3. Start PostgreSQL and Redis, then run the migration explicitly.

   ```bash
   docker compose up -d postgres redis
   cd services/api
   uv run alembic upgrade head
   cd ../..
   ```

   A container-only migration command is also available:

   ```bash
   docker compose --profile tools run --rm migrate
   ```

4. Start the application stack.

   ```bash
   docker compose up --build web api worker beat
   ```

5. Open <http://127.0.0.1:3000>. API documentation is local at
   <http://127.0.0.1:8000/api/docs>.

The web, API, PostgreSQL, and Redis host ports are published only on `127.0.0.1`. Containers bind to
their private bridge interfaces so they can communicate; `ALLOW_REMOTE_ACCESS=true` inside Compose
acknowledges that process-level binding while the host boundary remains loopback-only. This flag
does not add authentication.

## Run services without application containers

This is convenient while coding:

```bash
docker compose up -d postgres redis
make migrate
```

Use separate terminals:

```bash
make api
make web
make worker
make beat
```

The browser sends `/api/*` to Next.js, which rewrites the request to local FastAPI. Every minute,
Beat claims due career sources in PostgreSQL before dispatching them to workers. An active lease
prevents another scheduler or worker from fetching the same source during that interval. A separate
minute Beat entry also enqueues due in-app review reminders without fetching their careers pages.

## Use discovery and monitoring

Open <http://127.0.0.1:3000/onboarding> to create the one local profile and configure deterministic
role, keyword, location, and work-arrangement preferences. Then open
<http://127.0.0.1:3000/discover> and search for an industry such as `gaming companies`.

The API immediately returns a queued run. The Celery worker performs bounded web research and one
strictly structured normalization call. Research sources receive stable run-local IDs, and the
model selects a careers source by ID; deterministic code resolves it back to the authoritative URL,
validates company identity, and classifies URL evidence separately from monitoring support. Invalid
candidates are skipped without failing valid ones. The worker computes the documented score and
stores up to 40 verified results per research run. The result page automatically loads all persisted
result pages, displays every score component and evidence source, explains missing or rejected careers evidence, and allows
saving one company, saving every visible company with a verified careers source, or reversibly hiding a result. A hidden result remains as a compact row with a **Show result**
button and an explicit success message. There is no separate Show more results control because every
stored result is rendered automatically without an OpenAI request. **Research more companies**
remains available on the completed result page and shows a one-line warning below the control that the
owner's API key may incur additional usage; clicking queues a normal uncached continuation without
a confirmation dialog. It is not a JobScout payment or premium feature.

The compact **Daily history** panel beneath the industry search bar remembers every root search
started during the current machine-local day, including cached, queued, running, succeeded, and
failed searches. Select a row to reopen its persisted result page and save companies without
rerunning research. The panel updates as active searches finish and switches to a new empty day at
the next local 12:00am; older searches and results remain stored.

Cost protections are enabled by default:

- at most five new uncached research runs per machine-local calendar day;
- successful searches cached for seven days by normalized query, country, model, and prompt;
- a cache hit performs no OpenAI request and consumes no new tokens;
- failed uncached runs count because they may already have spent provider usage;
- each Research more continuation bypasses the cache and counts as one uncached run;
- continuations ask OpenAI to exclude prior companies and deterministically reject repeated
  official domains within the same discovery series;
- at most four built-in web-search calls and bounded output tokens per Responses request;
- input/output tokens, web-search calls, and an environment-rate-based cost estimate stored in
  `ai_runs` and shown after completion.

OpenAI prices can change. Keep the three `OPENAI_*_COST_*` values in `.env` aligned with the current
pricing page; they affect local estimates, not the provider invoice.

The browser automatically sends the UTC instants corresponding to the start and end of its current
local day; no timezone name is entered or stored. These same boundaries drive both Daily history
and the daily quota, including 23- and 25-hour daylight-saving days. Database timestamps, seven-day
cache expiry, source-poll intervals, leases, and retry backoff remain timezone-aware UTC instants or
elapsed durations. The UI formats those instants in the browser's current system time. Background
polling therefore remains reliable across daylight-saving changes and continues while the browser
tab is closed, provided the local services and laptop are running.

You can still open <http://127.0.0.1:3000/companies> to add a company manually:

1. Enter a company name and its HTTPS careers-page URL. No official-website URL is required.
2. Greenhouse, Lever, Ashby, and SmartRecruiters URLs are detected deterministically; other HTTPS
   careers pages receive one bounded generic-source check after saving.
3. Review the proposal and monitoring status.
4. Optionally enable first-poll alerts if you want matching jobs already on the careers page to
   alert immediately, then select **Confirm and save**. Resolving a proposal alone never saves it.

Saving a supported company makes its source eligible for an immediate first poll. Greenhouse,
Lever, Ashby, and SmartRecruiters use their public job-board APIs. An unrecognized official careers
page enters a bounded generic check: JobScout reads `robots.txt`, parses server-rendered links without
executing JavaScript, and marks a page unsupported when it cannot verify stable job links. It does
not use a hidden browser fallback.

The first successful or partial synchronization imports current jobs and establishes the company's
alert baseline. It does not create alerts unless you opt in. Later full polls create auditable
`discovered`, `updated`, `closed`, and `reopened` events. A missing job closes only after two
consecutive successful full polls; failed, partial, and HTTP 304 polls never count as an absence.

Open <http://127.0.0.1:3000/jobs> to inspect jobs across saved companies, or select **View source
health and jobs** on a saved company. Pausing or removing a saved company stops future polling while
retaining local job history. Monitoring works only while the laptop is awake, online, and the API,
worker, Beat, PostgreSQL, and Redis services are running. The company detail page also provides a
**Poll now** action for an owner-requested manual check.

## Recover blocked or unsupported sources

Open a saved company's detail page at <http://127.0.0.1:3000/companies>. Source cards distinguish
structured automatic monitoring, partial generic monitoring, assisted email imports, reminder-only
sources, and retired history.

For **Replace monitoring source**, paste a direct Greenhouse, Lever, Ashby, or SmartRecruiters board
URL. You may opt into alerts for matching jobs already present on its first poll; otherwise the first
poll is a quiet baseline. **Preview replacement** only normalizes the URL, detects the supported
provider, and checks canonical ownership—it makes no external request and changes nothing. **Confirm
replacement** repeats those checks and schedules the replacement. If JobScout creates or reuses a
different source row, it keeps the old row as non-polling **Retired history** instead of deleting its
poll and job audit trail.

Generic-source failures remain explicit:

- `ROBOTS_DISALLOWED` means fetched rules explicitly disallow the careers path.
- `ROBOTS_TXT_HTTP_401` or `ROBOTS_TXT_HTTP_403` means the site denied access to `robots.txt`.
- `ROBOTS_FETCH_FAILED` means `robots.txt` returned a retryable `429` or `5xx` response.
- `URL_DNS_FAILED`, `SOURCE_TIMEOUT`, and `SOURCE_NETWORK_ERROR` are retryable reachability failures.

JobScout never treats a temporary reachability failure as permission to crawl and never fetches a
careers page after a policy disallow, robots access denial, or retryable robots retrieval error.

For an employer alert you already received, use **Import an employer job alert** on the company
detail page. Upload one raw `.eml` file no larger than 1 MiB. The browser sends it as
`message/rfc822`; the server parses text locally, ignores attachments and remote resources, extracts
job-like HTTP(S) links, and immediately records a partial observation. The upload itself is the
explicit owner action—there is no second candidate preview and no typed manual-job form. Imported
observations may add, update, or reopen jobs but cannot close a missing job. Reimporting the same
message ID or content hash returns the existing import without duplicating jobs or alerts. JobScout
retains a bounded text excerpt, minimal message metadata, and the content hash, not the raw message or
attachments.

For sites that need a human check, use the source card's **Manual review reminder** date/time picker.
The browser interprets that wall time with this machine's current timezone and sends an absolute
instant; no timezone preference is stored. Beat checks due reminders once per minute while the local
services are running. The Alerts page shows the reminder in local system time and provides **Open
careers page**, **Mark checked**, and **Dismiss**. Each schedule version creates at most one in-app
reminder and never fetches the careers site or sends a reminder email.

## Receive alerts

A job alerts only when it passes your deterministic preferences: its role type is selected, no
excluded keyword appears, a required keyword appears if you configured any, and your
work-arrangement and location rules hold. No AI call is spent per polled job, and a non-matching job
is still collected and visible under `/jobs`.

Open <http://127.0.0.1:3000/alerts> to read alert history, see exactly why each job matched, inspect
email delivery attempts, retry a terminal delivery, send an owner-requested test email, and mark
alerts read. The same page lists scheduled and due manual-review reminders. Two rules keep alerts
honest:

- Jobs imported by the first poll of a newly saved company are the baseline and stay silent, unless
  you enable **Notify me about currently open jobs** in Settings or on the saved company.
- A paused saved company alerts about nothing.

All matching jobs from one company and one poll are grouped into one in-app alert and at most one
email. Every 30 seconds Beat claims due outbox rows and hands each to a worker. A sent row is never
redelivered, and Resend requests use a stable outbox key plus a request fingerprint so immediate
crash retries reuse the same provider request. Resend retains idempotency keys for 24 hours;
transient provider failures retry with bounded backoff up to five attempts before failing
permanently.

Email is off until you enable it in <http://127.0.0.1:3000/settings> and supply a notification
address. Delivery then depends on server configuration:

- `EMAIL_DELIVERY_MODE=resend` with both `RESEND_API_KEY` and `EMAIL_FROM` selects live Resend
  delivery and fails closed if either credential is missing.
- `EMAIL_DELIVERY_MODE=fake` records structured deliveries in the worker log without a network
  call. `auto` selects Resend only when both values exist; otherwise it selects fake. Tests force
  fake mode regardless of credentials.

After changing these values, recreate the API, worker, and Beat containers. The Alerts page reports
the active adapter without exposing the API key. **Send test email** requires an explicit browser
action, creates a durable outbox row, and then shows `pending`, `sending`, `accepted`, `failed`, or
`cancelled`. `Accepted by Resend` is provider acceptance; only the receiving inbox can prove final
delivery. JobScout does not configure or consume Resend webhooks.

An outbox row whose settings no longer permit sending is `cancelled`, never reported as delivered.
Inspect delivery state directly when needed:

```bash
docker compose exec postgres psql -U jobscout -d jobscout \
  -c "SELECT channel, status, delivery_adapter, attempt_count, last_error_code, sent_at FROM notification_outbox ORDER BY created_at DESC LIMIT 20;"
```

To load historical sponsorship evidence, download an official DOL LCA disclosure CSV or XLSX, keep
PostgreSQL running, and execute:

```bash
cd services/api
uv run python -m app.cli.import_lca \
  --file /absolute/path/to/official-file.xlsx \
  --fiscal-year 2025 \
  --source-url https://www.dol.gov/path/to/source \
  --record-layout-version FY2025-Q4
```

The importer validates its layout, processes rows incrementally, aggregates legal employer names,
and uses the file SHA-256 hash to make repeated imports idempotent. Historical records are shown
only after a conservative exact match or an owner-confirmed legal-entity mapping. They do not
guarantee sponsorship for a specific role. See the [DOL import runbook](docs/runbooks/dol-import.md).

## Health and recovery

```bash
curl http://127.0.0.1:8000/api/v1/health/live
curl http://127.0.0.1:8000/api/v1/health/ready
docker compose ps
docker compose logs api worker beat
```

Liveness checks only the API process. Readiness checks PostgreSQL and Redis and returns HTTP 503 if
either is unavailable. PostgreSQL is durable in the `jobscout-postgres` volume; Redis can be
recreated. Compose restarts long-running services unless they were intentionally stopped.

Monitoring works only while the laptop is awake, online, Docker is running, and the services are
healthy. Overdue sources become eligible when services resume, but JobScout cannot recover a
posting that appears and disappears entirely during downtime.

## Database migrations

Migrations never run implicitly at API startup.

```bash
cd services/api
uv run alembic current
uv run alembic upgrade head
uv run alembic check
```

To create a future migration after changing SQLAlchemy models:

```bash
uv run alembic revision --autogenerate -m "describe the schema change"
```

Review generated migrations before applying them. The full migration chain builds the V1 domain
schema from an empty PostgreSQL database and supports downgrade/re-upgrade.

## Quality checks

Run the same credential-free checks used by CI:

```bash
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test
pnpm build

cd services/api
uv run ruff format --check .
uv run ruff check .
uv run mypy app
uv run pytest
uv run alembic check
```

Database integration tests are opt-in locally because they require PostgreSQL:

```bash
RUN_INTEGRATION_TESTS=1 uv run pytest tests/integration
```

CI starts disposable PostgreSQL and Redis services and never calls live ATS sites, DOL downloads,
OpenAI, or Resend.

## Environment variables

See [`.env.example`](.env.example) for every public configuration name:

- `DATABASE_URL` and `REDIS_URL` are required for readiness and background processes.
- `WEB_HOST` and `API_HOST` default to loopback. A non-loopback value is refused unless
  `ALLOW_REMOTE_ACCESS=true`.
- OpenAI model, request-bound, and cost-estimate variables configure Milestone 3. A new live search
  requires `OPENAI_API_KEY`; cached reads, manual companies, and tests do not call OpenAI.
- `EMAIL_DELIVERY_MODE` is `auto`, `fake`, or `resend`. Use explicit `resend` for live delivery;
  `RESEND_API_KEY` and a verified `EMAIL_FROM` sender are then required. Tests and CI force fake
  delivery and never contact Resend.
- No server secret is exposed through a `NEXT_PUBLIC_*` variable.

Never commit `.env` or credentials.

## Troubleshooting

- **Corepack cannot verify pnpm:** update Corepack or use `npx -y pnpm@11.24.0` for the documented
  pnpm commands.
- **Readiness says PostgreSQL/Redis is unavailable:** run `docker compose ps`, then inspect
  `docker compose logs postgres redis api`; confirm you migrated the database.
- **Port 3000, 8000, 5432, or 6379 is occupied:** stop the conflicting local process. Do not change
  bindings to `0.0.0.0` as a shortcut.
- **The database is empty after startup:** run migrations explicitly; API startup does not create
  tables.
- **A worker does not receive heartbeats:** confirm Redis is healthy and that both `worker` and
  `beat` are running with the same `REDIS_URL`.
- **A source remains pending or becomes unsupported:** inspect its company detail page and worker
  logs. The page distinguishes an explicit robots disallow/access denial from a retryable robots,
  DNS, timeout, or network failure. Generic pages must expose stable links in server-rendered HTML;
  JavaScript-only sites are intentionally unsupported. Use a direct supported ATS board or schedule
  a manual review instead of trying to bypass the site.
- **A replacement URL is rejected:** use a direct Greenhouse, Lever, Ashby, or SmartRecruiters board
  URL for the same saved company. Generic company pages are not accepted by source repair, previews
  expire after 15 minutes, and a canonical board already owned by another company is rejected.
- **An `.eml` import fails:** export the original raw message, keep it at or below 1 MiB, and upload it
  from the matching saved-company page. Forwarded text, mailbox credentials, multipart web forms,
  and typed manual jobs are not accepted by this workflow.
- **A review reminder does not appear when due:** confirm the worker and Beat services are running.
  Review reminders are in-app only, checked every minute, and displayed with the browser machine's
  current system clock.
- **A job did not close after disappearing once:** this is expected. JobScout requires two
  consecutive successful full polls and ignores absences during partial, failed, and unchanged
  conditional polls.
- **A matching job did not alert:** the first poll of a newly saved company is the silent baseline.
  Check that the company is active, that your role/keyword/location preferences accept the job, and
  the worker log's `job_alert_filtered` lines for the rejection code.
- **An alert exists but no email arrived:** open Alerts and inspect the delivery adapter, status,
  attempt count, and safe error code. Confirm email alerts and the notification address in Settings,
  `EMAIL_DELIVERY_MODE=resend`, and a verified `EMAIL_FROM`; then inspect worker logs. `Accepted by
Resend` is not proof of inbox delivery, so also check spam and the Resend dashboard.
- **Search says OpenAI is not configured:** confirm the API and worker both received
  `OPENAI_API_KEY`, `OPENAI_RESEARCH_MODEL`, and `OPENAI_STRUCTURED_MODEL`, then restart them.
- **A search fails after being queued:** inspect `docker compose logs worker api`; the UI shows a
  stable error code while detailed exceptions remain in local server logs.
- **A search remains queued for more than a minute:** this is not normal. Confirm Redis and the
  worker are running, then inspect `docker compose logs worker api`. Worker task failures are
  persisted as `failed`; JobScout does not automatically retry a possibly paid research request.

See [the local runbook](docs/runbooks/local-foundation.md),
[DOL import runbook](docs/runbooks/dol-import.md), [API reference](docs/api.md),
[data-model notes](docs/data-model.md), and [privacy boundary](docs/privacy.md) for more detail.

## Product caveat

JobScout is informational and is not legal or immigration advice. Historical H1-B sponsorship
evidence does not guarantee sponsorship for any company or role.
