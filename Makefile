.PHONY: bootstrap up down migrate api web worker beat lint format-check test build check

bootstrap:
	corepack enable
	pnpm install --frozen-lockfile
	cd services/api && uv sync --frozen --all-groups

up:
	docker compose up --build

down:
	docker compose down

migrate:
	cd services/api && uv run alembic upgrade head

api:
	cd services/api && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

web:
	pnpm --filter @jobscout/web dev

worker:
	cd services/api && uv run celery -A app.workers.celery_app worker --loglevel=INFO

beat:
	cd services/api && uv run celery -A app.workers.celery_app beat --loglevel=INFO

lint:
	pnpm lint
	cd services/api && uv run ruff check . && uv run mypy app

format-check:
	pnpm format:check
	cd services/api && uv run ruff format --check .

test:
	pnpm test
	cd services/api && uv run pytest

build:
	pnpm build

check: format-check lint test build

