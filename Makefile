# Local development. Run `make` to see the targets.

-include .env
export

WHYDIFF_PG_PORT ?= 5433
WHYDIFF_PG_URI ?= postgresql://whydiff:whydiff@localhost:$(WHYDIFF_PG_PORT)/whydiff
WHYDIFF_TEST_PG_URI ?= postgresql://whydiff:whydiff@localhost:$(WHYDIFF_PG_PORT)/whydiff_test

.DEFAULT_GOAL := help
.PHONY: help setup up down reset logs psql test test-fast lint fmt demo

help: ## show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  make %-10s %s\n", $$1, $$2}'

setup: ## create .venv with all dev dependencies, and .env if missing
	uv sync --group dev
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example")

up: ## start Postgres and wait until it is healthy
	docker compose up -d --wait

down: ## stop the databases (data is kept)
	docker compose down

reset: ## delete all database data and start fresh (re-runs the init scripts)
	docker compose down -v
	docker compose up -d --wait

logs: ## follow database logs
	docker compose logs -f

psql: ## open psql in the demo database
	docker compose exec postgres psql -U whydiff -d whydiff

test: ## run all tests, including Postgres (needs `make up`)
	uv run pytest

test-fast: ## run tests without Postgres
	WHYDIFF_TEST_PG_URI= uv run pytest

lint: ## ruff, formatting check and mypy, as in CI
	uv run ruff check src tests
	uv run ruff format --check src tests
	uv run mypy

fmt: ## format code and fix lint issues
	uv run ruff format src tests
	uv run ruff check --fix src tests

demo: ## diff the demo tables orders and orders_v2
	-uv run whydiff diff $(WHYDIFF_PG_URI) public.orders $(WHYDIFF_PG_URI) public.orders_v2 --key id
