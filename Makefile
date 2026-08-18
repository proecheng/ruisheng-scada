.PHONY: up down logs test test-integration test-replay test-benchmark test-all cov lint fmt migrate seed

up:
	uv run task up

down:
	uv run task down

test:
	uv run task test

test-integration:
	uv run task test-integration

test-replay:
	uv run task test-replay

test-benchmark:
	uv run task test-benchmark

test-all:
	uv run task test-all

cov:
	uv run task cov

lint:
	uv run task lint

fmt:
	uv run task fmt

migrate:
	uv run task migrate

seed:
	uv run task seed

web-install:
	cd ruisheng-web && pnpm install

web-dev:
	cd ruisheng-web && pnpm dev

web-build:
	cd ruisheng-web && pnpm build

web-test:
	cd ruisheng-web && pnpm test

web-lint:
	cd ruisheng-web && pnpm lint

web-typecheck:
	cd ruisheng-web && pnpm typecheck

.PHONY: web-install web-dev web-build web-test web-lint web-typecheck
