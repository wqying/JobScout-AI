# API

The versioned API prefix is `/api/v1`. Milestone 5 exposes:

- `GET /api/v1/health/live`: process liveness; does not touch dependencies.
- `GET /api/v1/health/ready`: verifies PostgreSQL and Redis, returning HTTP 503 when either is down.
- `GET`, `PUT /api/v1/profile`: read or upsert the installation's single local profile.
- `GET`, `PUT /api/v1/settings`: read or replace deterministic job-search preferences.
- `POST /api/v1/companies/resolve`: find a local company or create a reviewable proposal from
  a company name and HTTPS careers-page URL. This does not create a saved-company record.
- `POST /api/v1/companies/{company_id}/save`: explicitly confirm and idempotently save a proposal.
- `GET /api/v1/companies`: list saved companies with cursor pagination.
- `GET /api/v1/companies/{company_id}`: read a company preview and sponsorship summary.
- `GET /api/v1/companies/{company_id}/evidence`: list the company's stored evidence.
- `GET /api/v1/companies/{company_id}/jobs`: list that company's current and historical jobs.
- `GET /api/v1/jobs`: list jobs across saved companies, optionally filtered by active/closed status.
- `PATCH`, `DELETE /api/v1/saved-companies/{saved_company_id}`: pause, resume, change baseline
  notification behavior, or remove a saved company.
- `GET /api/v1/notifications`: list in-app alert history newest first, with cursor pagination, an
  optional `unread_only` filter, and the unread count.
- `POST /api/v1/notifications/{notification_id}/read`: mark one alert read. Repeating the call is
  safe and keeps the first read timestamp.
- `GET /api/v1/email/configuration`: report the requested mode, active adapter, readiness, and safe
  diagnostic code without returning the Resend key or notification address.
- `POST /api/v1/email/test`: queue one durable owner-requested test. Requires the JSON body
  `{ "acknowledge_external_email": true }`, returns HTTP 202, and refuses another test while one is
  pending or sending.
- `GET /api/v1/email/deliveries`: list email outbox history with cursor pagination.
- `GET /api/v1/email/deliveries/{outbox_id}`: read one delivery for UI status polling.
- `POST /api/v1/email/deliveries/{outbox_id}/retry`: requeue a failed or cancelled email and return
  HTTP 202. Pending, sending, and sent rows are not retryable.
- `GET /api/v1/ops/imports`: list DOL import provenance and status.
- `GET /api/v1/ops/source-runs`: list poll attempts and lifecycle counters.
- `POST /api/v1/ops/sources/{source_id}/poll`: lease and queue one supported source immediately.
- `POST /api/v1/ops/sources/{source_id}/pause`: stop recurring polls for one source.
- `POST /api/v1/discoveries`: validate, cache, quota-check, and queue an industry search. Returns
  HTTP 202 for a new run and for an immediately completed cache hit.
- `GET /api/v1/discoveries/daily-history`: list the current machine-local day's root searches
  newest first, including cached and nonterminal or failed runs, with cursor pagination.
- `GET /api/v1/discoveries` and `GET /api/v1/discoveries/{run_id}`: list runs and poll status,
  cache state, result count, safe failure code, and locally estimated API cost.
- `GET /api/v1/discoveries/{run_id}/results`: list ranked sourced results and every score component.
- `POST /api/v1/discoveries/{run_id}/results/{result_id}/hide`: hide a result only in this run.
- `POST /api/v1/discoveries/{run_id}/results/{result_id}/show`: restore a hidden result.
- `POST /api/v1/discoveries/{run_id}/save-selected`: idempotently save selected result IDs.
- `POST /api/v1/discoveries/{run_id}/save-all-monitorable`: save every visible result whose
  careers page has collection support.

Each discovery result reports careers evidence and collection capability independently.
`careers_url_status` is `evidence_verified`, `not_found`, or `rejected`; `careers_url_reason`
provides a stable diagnostic code; and `monitoring_support` is `structured`, `generic_verified`,
`generic_pending`, or `unsupported`. A verified own-domain careers URL is therefore still returned
while its generic HTML adapter awaits the first bounded resolution poll. The `sources` array
includes the run-local `source_id` used to tie the structured proposal back to the web-search
manifest.

`generic_verified` means the discovery-time resolver opened the page and counted individual job
links on it (DESIGN_DOC.md Section 13.7). `generic_pending` means the URL was cited on the right
domain but never opened -- the resolver was disabled, out of budget, or could not reach the page;
`careers_url_reason` says which. A page the resolver opened and found no openings on becomes
`unsupported` and is not registered as a pollable source, while its URL stays visible as evidence.

Interactive documentation is local at `/api/docs`. Errors use the stable
`{ "error": { "code", "message", "details" } }` envelope. Validation errors and unexpected server
errors are translated into the same safe shape; internal exception details are not returned.

## Manual resolution boundary

A manual proposal requires a company name and HTTPS careers-page URL; it never asks for a separate
official-website URL. The canonical careers source is checked before a new company is created, and
the response remains a proposal for owner review. Only the save endpoint changes it into a saved
company.

Provider detection is deterministic. Greenhouse, Lever, and Ashby URL patterns use their public
JSON feeds. A saved unrecognized HTTPS source enters `pending_resolution`; its first bounded poll
checks SSRF policy and `robots.txt`, then parses server-rendered job links only. A JavaScript-only or
otherwise unverifiable page becomes `unsupported` instead of implying active monitoring.

## Polling boundary

Celery Beat claims due sources with PostgreSQL row locks and expiring leases before queueing work.
Workers apply domain rate limits, conditional ETag/Last-Modified headers, bounded redirects,
timeouts, retries, and five-megabyte response limits. Every URL and redirect is DNS-resolved and
rejected when it targets a non-public address. Poll writes, snapshots, and lifecycle events commit
transactionally. Partial, failed, and not-modified runs never increment absence counts.

## Alerting boundary

Matching is deterministic: a job alerts only when its classified role type is selected, no excluded
keyword appears, a required keyword appears if any are configured, and the remote/location
preferences hold. No LLM call is spent per polled job.

Alerts are written through a transactional outbox. A poll's matching `discovered` and `reopened`
events become outbox rows inside that poll's transaction, so a rolled-back poll leaves no alert
behind, and delivery never blocks the poll. All matching jobs from one company and one poll share
one `event_group_key`, producing one in-app alert and at most one email. The first successful poll
of a newly saved company sets the alert baseline and stays silent unless the owner enabled
`Notify me about currently open jobs`. A paused saved company alerts about nothing.

A Beat task claims due rows with row locks, marks them `sending` under a short lease, and hands
each to a worker. An in-app record is unique per outbox row and a `sent` row is never redelivered.
Resend calls use an outbox-derived idempotency key plus a deterministic request fingerprint;
transient failures retry with bounded backoff up to five attempts, while permanent failures stop.
`EMAIL_DELIVERY_MODE` can explicitly select `resend` or `fake`; `auto` uses Resend only when
`RESEND_API_KEY` and `EMAIL_FROM` are both present. Tests force fake delivery and never contact a
live provider. Delivery history records the adapter that actually handled each attempt, and a sent
status means provider acceptance rather than confirmed inbox delivery.

When a source first reaches ten consecutive failures, the poll transaction creates one local
in-app source-health alert for that failure episode. It does not send an administrative email.

## Discovery cost boundary

An uncached `POST /discoveries` and `POST /discoveries/{id}/research-more` consume the daily quota
and queue OpenAI work. Research more is not an application payment gate. The UI places a concise
usage warning directly below the button, and the click sends the required
`acknowledge_additional_api_usage: true` field without opening a confirmation dialog.
Continuations bypass the cache.

The browser automatically adds `X-JobScout-Local-Day-Start` and
`X-JobScout-Local-Day-End` to both of those write requests and to
`GET /discoveries/daily-history`. Each value is a timezone-aware ISO instant corresponding to
12:00am at one edge of the browser machine's current local day. The API requires both headers,
normalizes them to UTC, and uses their half-open `[start,end)` range for both quota counting and
Daily history. It accepts 22- to 26-hour ranges so daylight-saving transitions work, and rejects a
missing, unordered, stale, or otherwise invalid pair with `LOCAL_DAY_CONTEXT_INVALID`. The API does
not accept, infer, or persist a configurable timezone.

The default limit is five uncached queries in that local-day range. Failed uncached work still
counts because it may have spent provider usage. A cache hit creates a root discovery run for Daily
history but no new `industry_queries` row, so it consumes no quota. Daily history returns root runs
only; Research more continuations stay attached to their original root. At the next machine-local
12:00am the browser requests the new range without deleting prior runs or results.

Cache identity for initial research includes the normalized query, country, both model IDs, and
prompt version. The default TTL is a rolling seven-day duration rather than a midnight reset. Each research run
stores up to 40 verified candidates. `GET /discoveries/{id}/results?offset=0&limit=20` pages across
the initial run and its successful continuations; the web app follows those pages automatically and
has no owner-operated Show more control. Reading persisted pages performs no AI call. The research worker
makes two Responses API calls: bounded web research followed by web-disabled strict-schema
normalization. Tests replace this client with a deterministic fake and never call paid services.

All API timestamps are timezone-aware absolute instants. The web app formats them with the
browser's system locale and clock. Server-side source intervals, leases, retries, cache expiry, and
outbox polling use UTC instants or elapsed durations rather than configurable wall-clock times; no
timezone setting is needed, and daylight-saving changes do not duplicate or skip an interval.
