"""Regression checks for build-time and offline production Compose contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
COMPOSE_FILES = (ROOT / "docker-compose.prod.yml", ROOT / "deploy" / "docker-compose.prod.yml")
WAL_PATH = "/var/lib/ruisheng-gw/wal"
WAL_MOUNT = f"- ruisheng-gw-wal:{WAL_PATH}"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("compose_path", COMPOSE_FILES)
def test_application_images_are_named_and_migrate_reuses_api(compose_path: Path) -> None:
    compose = _read(compose_path)

    assert compose.count("image: ${API_IMAGE:-ruisheng-prod-api:latest}") == 2
    assert compose.count("image: ${GW_IMAGE:-ruisheng-prod-gw:latest}") == 1
    assert compose.count("image: ${WEB_IMAGE:-ruisheng-prod-web:latest}") == 1


def test_build_compose_keeps_local_build_contexts() -> None:
    compose = _read(ROOT / "docker-compose.prod.yml")

    assert compose.count("    build:\n") == 4
    assert compose.count("dockerfile: ruisheng-api/Dockerfile") == 2
    assert "dockerfile: ruisheng-gw/Dockerfile" in compose
    assert "context: ruisheng-web" in compose


def test_offline_compose_contains_no_build_keys() -> None:
    compose = _read(ROOT / "deploy" / "docker-compose.prod.yml")

    assert "build:" not in compose
    assert compose.count("pull_policy: never") == 4


@pytest.mark.parametrize("compose_path", COMPOSE_FILES)
def test_gateway_wal_is_explicitly_persistent(compose_path: Path) -> None:
    compose = _read(compose_path)

    assert f"GW_WAL_DIR: {WAL_PATH}" in compose
    assert WAL_MOUNT in compose
    assert "  ruisheng-gw-wal:\n" in compose


def test_export_script_saves_compose_resolved_images() -> None:
    script = (ROOT / "deploy" / "export-images.sh").read_text(encoding="utf-8")

    assert "pull postgres redis" in script
    assert "config --images" in script
    assert 'docker save "$image"' in script
    assert '--env-file "$ENV_FILE"' in script
    assert "declare -A SEEN_ARCHIVES=()" in script
    assert "Archive name collision" in script


@pytest.mark.parametrize("compose_path", COMPOSE_FILES)
def test_migration_receives_secrets_for_fail_fast_validation(compose_path: Path) -> None:
    compose = _read(compose_path)

    migrate = compose.split("  migrate:", 1)[1].split("\n  api:", 1)[0]
    for name in (
        "POSTGRES_PASSWORD",
        "RUISHENG_GW_PASSWORD",
        "RUISHENG_API_PASSWORD",
        "REDIS_PASSWORD",
        "JWT_SECRET",
    ):
        assert f"{name}: ${{{name}}}" in migrate


def test_migration_entrypoint_rejects_unsafe_or_placeholder_secrets() -> None:
    script = _read(ROOT / "scripts" / "entrypoint-migrate.sh")

    assert '"$value" == CHANGE_ME_*' in script
    assert "^[A-Za-z0-9._~-]+$" in script
    assert "${#value} -lt 16" in script
    assert "${#JWT_SECRET} -lt 32" in script


def test_role_migration_does_not_embed_passwords_in_dollar_quoted_blocks() -> None:
    migration = _read(
        ROOT
        / "alembic"
        / "versions"
        / "20260416_e74ffa548c2f_db_roles_ruisheng_gw_ruisheng_api_grants.py"
    )

    role_section = migration.split("# --- schema 级 GRANT", 1)[0]
    assert "DO $$" not in role_section
    assert "quote_literal(:password)" in role_section
