# DOL LCA import runbook

JobScout imports an official Department of Labor LCA disclosure CSV or XLSX into local PostgreSQL.
The file is processed locally; the importer does not download it or call any external service.

## Before importing

1. Download the disclosure file from the official DOL performance-data page.
2. Record the exact source URL and the DOL record-layout or release label.
3. Start PostgreSQL and apply migrations:

   ```bash
   docker compose up -d postgres
   cd services/api
   uv run alembic upgrade head
   ```

4. Confirm that the file is CSV, XLSX, or XLSM and includes an employer-name and case-status
   column. Worker-count and occupation columns are optional.

## Run the import

From `services/api`:

```bash
uv run python -m app.cli.import_lca \
  --file /absolute/path/to/official-file.xlsx \
  --fiscal-year 2025 \
  --source-url https://www.dol.gov/path/to/source \
  --record-layout-version FY2025-Q4
```

A successful command prints the accepted and total row counts, employer aggregate count, and import
ID. Running the same file for the same fiscal year again prints `already imported` and reuses the
existing import because the SHA-256 content hash matches.

Use an absolute host path when running the command locally. The normal API container does not mount
your Downloads folder.

## Inspect imports

With the API running:

```bash
curl http://127.0.0.1:8000/api/v1/ops/imports
```

The response includes fiscal year, source provenance, hash, layout version, status, row counts,
import time, and a stable error code when applicable.

## Matching and safety

Employer names are normalized for Unicode, case, whitespace, punctuation, and conservative trailing
corporate suffixes. Original legal names remain stored with the aggregates.

An LCA name contributes to a company summary only when it is a conservative exact normalized match
confirmed alongside the official company identity, or when the owner explicitly verifies the legal
entity in the company form. An AI-proposed or otherwise ambiguous mapping is displayed as unresolved
and contributes zero cases.

Historical certified LCA records are evidence about past filings. They do not guarantee that an
employer will sponsor a specific person or role, and JobScout is not legal or immigration advice.

## Common failures

- `FILE_NOT_FOUND`: use an absolute path and verify that the file still exists.
- `UNSUPPORTED_FILE_TYPE`: convert neither the content nor extension; download the official CSV or
  XLSX release.
- `MISSING_REQUIRED_COLUMNS`: verify the disclosure release and record-layout version.
- `INVALID_ENCODING`: CSV input must be UTF-8; prefer the official XLSX if the DOL CSV uses another
  encoding.
- `INVALID_HTTPS_URL`: `--source-url` must be the official HTTPS provenance URL.
- `IMPORT_FAILED`: inspect the file and database logs. Failed imports do not expose partial
  aggregates as successful.
