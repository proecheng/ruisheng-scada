.PHONY: up down logs test cov lint fmt migrate seed

up:
	uv run task up

down:
	uv run task down

test:
	uv run task test

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
