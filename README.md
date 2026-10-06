# JobScout AI

JobScout AI helps international students find companies in an industry, save employers they care
about, and track new job openings. It uses AI to research companies, shows historical US sponsorship
records when available, and sends alerts for jobs that match your preferences. Each person runs their
own copy, with their saved companies and job history stored on their computer. Monitoring runs while
the computer is awake, online, and the services are running; historical sponsorship records do not
guarantee sponsorship for a particular role.

## Tech stack

- **Web:** Next.js, TypeScript, Tailwind CSS, and shadcn/ui.
- **Backend:** Python 3.12, FastAPI, Pydantic, SQLAlchemy, and Alembic.
- **Storage and background work:** PostgreSQL 16, Redis, Celery workers, and Celery Beat.
- **External services:** OpenAI Responses API for company research; optional Resend email delivery.
- **Development:** Docker Compose, pnpm, uv, Ruff, mypy, pytest, Vitest, and GitHub Actions.

## Setup

Run these commands in a shell on macOS, Linux, or Windows with WSL. Install Git and Docker Desktop
(or Docker Engine with Compose v2), and start Docker. Choose either the Docker or local development
launch method below.

1. Clone the repository and copy the configuration template:

   ```bash
   git clone https://github.com/wqying/JobScout-AI.git
   cd JobScout-AI
   cp .env.example .env
   ```

2. Set `OPENAI_API_KEY` in `.env` to enable AI discovery. New research uses your paid OpenAI API
   account; manual company tracking works without this key. The template includes the remaining
   defaults. Never commit `.env`.

3. **Docker:** build and run everything, with no host Node.js or Python installation required:

   ```bash
   docker compose up -d --wait postgres redis
   docker compose --profile tools run --build --rm migrate
   docker compose up --build -d web api worker beat
   ```

   **Local development (for developers who might be interested in contributing or developing locally):** install Node.js 22.13+, pnpm 11.24.0, Python 3.12, uv 0.12+, and Make.
   With Corepack installed, `corepack enable` and `corepack install` activate the repository's
   pinned pnpm version. Install dependencies and prepare the database:

   ```bash
   pnpm install --frozen-lockfile
   cd services/api
   uv sync --frozen --all-groups
   cd ../..
   docker compose up -d --wait postgres redis
   make migrate
   ```

   Start each process in a separate terminal, from the repository root:

   ```bash
   make api
   make web
   make worker
   make beat
   ```

4. Open <http://127.0.0.1:3000/onboarding> to create your local profile. The app runs at
   <http://127.0.0.1:3000>, and API documentation is at <http://127.0.0.1:8000/api/docs>.
   All published service ports are restricted to localhost.

For the Docker launch, stop services with `docker compose down`; the database volume is retained.
For local development, stop the four terminal processes as well. After pulling updates, repeat the
chosen dependency/build and migration steps before starting the app.

## Project structure

The repository keeps the browser interface, backend rules, and deployment configuration together so
changes across them can be reviewed and tested in one place.

| Path                                               | Role and reason                                                                                                                                    |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apps/web/`                                        | Pages, components, and frontend tests. Keeps browser interactions separate from backend processing.                                                |
| `services/api/app/api/`                            | HTTP routes and request handling. Routes delegate business rules to domain services.                                                               |
| `services/api/app/` domain folders                 | Discovery, companies, monitoring, jobs, notifications, and related features. Each area owns its rules and queries so related logic stays together. |
| `services/api/app/ai/`                             | AI clients, schemas, and workflows. Isolates external model calls and validates their output before application services use it.                   |
| `services/api/app/workers/`                        | Celery tasks and scheduling. Runs slow research and recurring work outside browser requests, using the same domain services as the API.            |
| `services/api/app/db/` and `services/api/alembic/` | Shared database models and versioned migrations. Keeps stored data consistent as the application changes.                                          |
| `services/api/tests/` and `apps/web/tests/`        | Backend and frontend checks. Fakes and fixtures let tests run without live external services.                                                      |
| `infra/docker/` and `compose.yaml`                 | Container builds and local service wiring. Gives each installation the same runtime layout.                                                        |
| `docs/`, `evals/`, and `DESIGN_DOC.md`             | Detailed references, AI evaluation datasets, and the implementation specification. Evaluation scorers and reports remain unfinished.               |

The main flow is **browser → FastAPI → domain services → PostgreSQL**. Celery workers use those same
services for background work, while Redis carries queued tasks and Beat schedules recurring work.
This keeps business rules out of both HTTP routes and task wrappers.

## Quality checks

For a local development installation, run from the repository root:

```bash
make check
pnpm typecheck
```

Database integration tests also need PostgreSQL and Redis running with migrations applied:

```bash
cd services/api
RUN_INTEGRATION_TESTS=1 uv run pytest tests/integration
```

Tests and CI use fakes and fixtures rather than live OpenAI, job sites, or email services.

More detail: [design specification](DESIGN_DOC.md), [local runbook](docs/runbooks/local-foundation.md),
[DOL import guide](docs/runbooks/dol-import.md), [API reference](docs/api.md),
[data model](docs/data-model.md), and [privacy](docs/privacy.md).
