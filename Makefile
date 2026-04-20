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
