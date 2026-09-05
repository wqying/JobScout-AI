# Local foundation runbook

## Readiness is failing

1. Run `docker compose ps` and confirm PostgreSQL and Redis are healthy.
2. Run `docker compose logs api postgres redis` and look for a safe error code.
3. Confirm `DATABASE_URL` uses `postgres` inside Compose and `127.0.0.1` for host commands.
4. Run `make migrate`; migrations are intentionally not run during API startup.

## A worker stopped

Compose uses `restart: unless-stopped`. Redis task state is disposable; PostgreSQL remains the
durable source of truth. Restart with `docker compose up -d worker beat`.

Celery tasks create database connections through the worker-specific `NullPool` session factory.
This is required because each synchronous task wrapper uses a fresh `asyncio.run()` event loop;
pooled `asyncpg` connections cannot safely cross those loops.

## A discovery run remains queued

A run should normally move from `queued` to `running` within seconds. If it remains queued for more
than one minute, inspect `docker compose logs worker api` and the Redis/worker container status.
Uncaught task failures are recorded as terminal `failed` runs when PostgreSQL remains reachable.
JobScout does not automatically retry discovery because an ambiguous failure could repeat paid API
usage; start a new search only after resolving the reported error.

## The laptop slept

Services resume after the Docker runtime and network recover. Later monitoring milestones will make
overdue sources immediately eligible, but a job that appeared and disappeared entirely during
downtime cannot be recovered.
