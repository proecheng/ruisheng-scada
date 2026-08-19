"""Regression checks for build-time and offline production Compose contracts."""

from __future__ import annotations

import json
import os
import re
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
COMPOSE_FILES = (ROOT / "docker-compose.prod.yml", ROOT / "deploy" / "docker-compose.prod.yml")
ENV_FILES = (ROOT / ".env.prod.example", ROOT / "deploy" / ".env.prod.example")
APPLICATION_SERVICES = ("migrate", "api", "gw", "web")
ENV_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
API_NOTIFICATION_VARIABLES = {
    "API_NOTIFICATION_WECHAT_ENABLED",
    "API_NOTIFICATION_EMAIL_ENABLED",
    "API_NOTIFICATION_EMAIL_HOST",
    "API_NOTIFICATION_EMAIL_PORT",
    "API_NOTIFICATION_EMAIL_USER",
    "API_NOTIFICATION_EMAIL_PASSWORD",
    "API_NOTIFICATION_EMAIL_TLS",
    "API_NOTIFICATION_SMS_ENABLED",
    "API_NOTIFICATION_SMS_ENDPOINT",
    "API_NOTIFICATION_SMS_API_KEY",
    "API_NOTIFICATION_VOICE_ENABLED",
    "API_NOTIFICATION_VOICE_ENDPOINT",
    "API_NOTIFICATION_VOICE_API_KEY",
    "API_NOTIFICATION_WORKER_BATCH",
    "API_NOTIFICATION_WORKER_CONCURRENCY",
    "API_NOTIFICATION_LEASE_SEC",
    "API_NOTIFICATION_PROVIDER_TIMEOUT_SEC",
    "API_NOTIFICATION_MAX_ATTEMPTS",
    "API_NOTIFICATION_EVENT_MAX_AGE_SEC",
}
GW_ALARM_VARIABLES = {"GW_ALARM_RELOAD_INTERVAL_SEC", "GW_RELATION_VALUE_MAX_AGE_SEC"}
WAL_PATH = "/var/lib/ruisheng-gw/wal"
WAL_MOUNT = f"- ruisheng-gw-wal:{WAL_PATH}"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _run_compose(compose_path: Path, env_path: Path, *args: str) -> str:
    command = [
        "docker",
        "compose",
        "--env-file",
        str(env_path),
        "-f",
        str(compose_path),
        *args,
    ]
    environment = os.environ.copy()
    for key in _parse_env(env_path):
        environment.pop(key, None)

    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            timeout=30,
        )
    except FileNotFoundError:
        pytest.fail("Docker CLI is required to validate production Compose files")
    except subprocess.TimeoutExpired:
        pytest.fail(f"Docker Compose timed out for {compose_path}")
    except subprocess.CalledProcessError as error:
        details = error.stderr.strip() or error.stdout.strip() or "no output"
        pytest.fail(f"Docker Compose failed for {compose_path}: {details}")
    return result.stdout


def _render_compose(
    compose_path: Path, env_path: Path, *, interpolate: bool = True
) -> dict[str, object]:
    args = ["config", "--format", "json"]
    if not interpolate:
        args.append("--no-interpolate")
    rendered = _run_compose(compose_path, env_path, *args)
    try:
        model = json.loads(rendered)
    except json.JSONDecodeError as error:
        pytest.fail(f"Docker Compose returned invalid JSON for {compose_path}: {error}")
    if not isinstance(model, dict):
        pytest.fail(f"Docker Compose returned a non-object model for {compose_path}")
    return model


def _normalize_compose(model: dict[str, object], deployment_field: str) -> dict[str, object]:
    normalized = deepcopy(model)
    services = normalized.get("services")
    if not isinstance(services, dict):
        pytest.fail("Rendered Compose model does not contain a services object")
    for service in services.values():
        if not isinstance(service, dict):
            pytest.fail("Rendered Compose model contains a non-object service")
        service.pop(deployment_field, None)
    return normalized


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(_read(path).splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        key, separator, value = raw_line.partition("=")
        if not separator or ENV_KEY_PATTERN.fullmatch(key) is None:
            raise ValueError(f"{path}:{line_number}: invalid environment entry")
        if key in values:
            raise ValueError(f"{path}:{line_number}: duplicate environment key {key}")
        values[key] = value
    return values


def _compose_variable_keys(compose_path: Path, env_path: Path) -> set[str]:
    output = _run_compose(compose_path, env_path, "config", "--variables")
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines or lines[0].split(maxsplit=1)[0] != "NAME":
        pytest.fail(f"Docker Compose returned an unexpected variables table for {compose_path}")
    return {line.split(maxsplit=1)[0] for line in lines[1:]}


@pytest.mark.parametrize("interpolate", (True, False))
def test_compose_models_are_equivalent_after_deployment_normalization(
    interpolate: bool,
) -> None:
    build_model = _render_compose(COMPOSE_FILES[0], ENV_FILES[0], interpolate=interpolate)
    offline_model = _render_compose(COMPOSE_FILES[1], ENV_FILES[1], interpolate=interpolate)

    assert _normalize_compose(build_model, "build") == _normalize_compose(
        offline_model, "pull_policy"
    )


def test_application_services_lock_deployment_fields_per_service() -> None:
    build_services = _render_compose(COMPOSE_FILES[0], ENV_FILES[0])["services"]
    offline_services = _render_compose(COMPOSE_FILES[1], ENV_FILES[1])["services"]

    for service_name in APPLICATION_SERVICES:
        assert "build" in build_services[service_name]
        assert "pull_policy" not in build_services[service_name]
        assert "build" not in offline_services[service_name]
        assert offline_services[service_name]["pull_policy"] == "never"


def test_production_environment_templates_are_exactly_equal() -> None:
    assert _parse_env(ENV_FILES[0]) == _parse_env(ENV_FILES[1])


@pytest.mark.parametrize(
    ("contents", "error"),
    (
        ("VALID=first\nVALID=second\n", "duplicate environment key VALID"),
        ("INVALID-KEY=value\n", "invalid environment entry"),
    ),
)
def test_dotenv_parser_rejects_duplicate_and_invalid_keys(
    tmp_path: Path, contents: str, error: str
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match=error):
        _parse_env(env_path)


def test_dotenv_parser_preserves_value_after_first_equals(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("TOKEN=left=right  \n", encoding="utf-8")

    assert _parse_env(env_path) == {"TOKEN": "left=right  "}


@pytest.mark.parametrize(("compose_path", "env_path"), zip(COMPOSE_FILES, ENV_FILES, strict=True))
def test_compose_variables_exactly_match_environment_template(
    compose_path: Path, env_path: Path
) -> None:
    assert _compose_variable_keys(compose_path, env_path) == set(_parse_env(env_path))


@pytest.mark.parametrize(("compose_path", "env_path"), zip(COMPOSE_FILES, ENV_FILES, strict=True))
def test_required_runtime_variables_are_on_their_owning_services(
    compose_path: Path, env_path: Path
) -> None:
    services = _render_compose(compose_path, env_path)["services"]

    assert set(services["api"]["environment"]) >= API_NOTIFICATION_VARIABLES
    assert set(services["gw"]["environment"]) >= GW_ALARM_VARIABLES


@pytest.mark.parametrize(("compose_path", "env_path"), zip(COMPOSE_FILES, ENV_FILES, strict=True))
def test_notification_provider_defaults_are_disabled_and_credentials_are_empty(
    compose_path: Path, env_path: Path
) -> None:
    values = _parse_env(env_path)
    api_environment = _render_compose(compose_path, env_path)["services"]["api"]["environment"]
    for key in (
        "API_NOTIFICATION_WECHAT_ENABLED",
        "API_NOTIFICATION_EMAIL_ENABLED",
        "API_NOTIFICATION_SMS_ENABLED",
        "API_NOTIFICATION_VOICE_ENABLED",
    ):
        assert values[key] == "false"
        assert api_environment[key] == "false"
    for key in (
        "API_NOTIFICATION_EMAIL_HOST",
        "API_NOTIFICATION_EMAIL_USER",
        "API_NOTIFICATION_EMAIL_PASSWORD",
        "API_NOTIFICATION_SMS_ENDPOINT",
        "API_NOTIFICATION_SMS_API_KEY",
        "API_NOTIFICATION_VOICE_ENDPOINT",
        "API_NOTIFICATION_VOICE_API_KEY",
    ):
        assert values[key] == ""
        assert api_environment[key] == ""


def test_compose_render_ignores_ambient_template_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_NOTIFICATION_EMAIL_ENABLED", "true")

    api_environment = _render_compose(COMPOSE_FILES[0], ENV_FILES[0])["services"]["api"][
        "environment"
    ]

    assert api_environment["API_NOTIFICATION_EMAIL_ENABLED"] == "false"


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
