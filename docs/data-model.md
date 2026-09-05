# Data model

The initial Alembic migration creates the complete V1 data contract from Section 8 of
`DESIGN_DOC.md`. SQLAlchemy declarations live in `services/api/app/db/models.py` and Alembic owns
schema changes. UUIDs identify records, timestamps are timezone-aware, enum-like values use named
check constraints, and domain idempotency keys use database unique constraints.

No table is created automatically at API startup. Run migrations explicitly.

Milestone 2 begins using these existing schema groups:

- `owner_profiles` and `app_settings` hold the single local user's identity and deterministic
  filters. `app_settings` has no timezone column: the browser supplies its current local-day
  boundaries when calendar-day behavior is needed, and those boundaries are not persisted as a
  user preference.
- `companies`, `company_aliases`, and `company_evidence` hold canonical identity and provenance.
- `career_sources` stores a deduplicated, versioned provider resolution separately from the saved
  decision.
- `saved_companies` is the explicit owner-confirmation boundary. Repeating save updates the same
  row instead of creating another one.
- `immigration_dataset_imports` records source URL, file hash, record-layout version, counts, and
  failure status.
- `lca_employer_yearly_stats` stores per-fiscal-year aggregates rather than raw applicant records.
- `company_legal_entities` separates exact and owner-verified mappings from unresolved AI
  proposals. Only conservative exact matches and owner-verified mappings contribute to displayed
  sponsorship totals.

The importer commits its aggregate rows and success status together, so a partial import is never
visible as successful. Its `(dataset_type, fiscal_year, source_sha256)` uniqueness rule makes the
same source file idempotent.

Milestone 3 activates `industry_queries`, `discovery_runs`, `discovery_results`,
`company_industries`, and `ai_runs`. `industry_queries` owns the seven-day cache key and expiry.
Every request still receives its own `discovery_runs` row; a cache hit clones ranked score rows and
records `cache_hit=true`, while making no AI run. `ai_runs` stores redacted inputs and outputs,
source manifests, token counts, repair attempts, timestamps, and estimated cost. Migration
`2f1a3c7d9e11` adds the cache marker and bounded requested limit to discovery runs.

Daily history requires no history table or stored timezone/day bucket. It filters series-root
`discovery_runs.created_at` with the browser-supplied half-open UTC instant range for the current
machine-local day, so cached, queued, running, succeeded, and failed root runs can all appear while
continuations remain grouped under their root. The daily uncached-search quota applies that exact
same range to `industry_queries.created_at`: failed uncached queries count, while cache hits create
no query row and do not. Midnight rollover changes the selected range and never deletes older rows.
The cache's `expires_at` is still a rolling elapsed seven-day lifetime.

All persisted timestamps are timezone-aware absolute instants. Source schedules, leases, retry
backoff, outbox polling, and cache expiry compare UTC instants or elapsed durations; they are not
recomputed at local midnight. Local formatting and local calendar boundaries belong to the browser,
which avoids coupling database correctness to a manually configured timezone or daylight-saving
transition.

Careers provenance does not require another table or migration. Each `company_industries` evidence
JSON object stores the resolved manifest `source_id`, exact verified careers URL, URL status, stable
reason code, and monitoring-support classification. The API synthesizes the new fields for older
cached evidence rows so existing local data remains readable.
