"""Regression checks for build-time and offline production Compose contracts."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
COMPOSE_FILES = (ROOT / "docker-compose.prod.yml", ROOT / "deploy" / "docker-compose.prod.yml")
ENV_FILES = (ROOT / ".env.prod.example", ROOT / "deploy" / ".env.prod.example")
PRODUCTION_DOCS = (ROOT / "README.md", ROOT / "deploy" / "setup-customer.md")
SEED_FILES = tuple(sorted((ROOT / "seeds").glob("*.sql")))
APPLICATION_SERVICES = ("migrate", "api", "gw", "web")
ALL_SERVICES = ("postgres", "redis", *APPLICATION_SERVICES)
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
    keys = {line.split(maxsplit=1)[0] for line in lines[1:]}
    # WEB_HEALTH_ACL_FILE is consumed by the site override, which is rendered
    # together with the immutable base Compose during deployment.
    if "WEB_HEALTH_ACL_FILE" in _parse_env(env_path):
        keys.add("WEB_HEALTH_ACL_FILE")
    return keys


def _seed_document_forbidden_values() -> set[str]:
    quoted_values = {
        value
        for seed_file in SEED_FILES
        for value in re.findall(r"'([^'\r\n]+)'", _read(seed_file))
    }
    return {
        value
        for value in quoted_values
        if "demo" in value.casefold()
        or "@" in value
        or re.fullmatch(r"1[3-9][0-9]{9}", value) is not None
    }


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
    for service_name in ("postgres", "redis"):
        assert "build" not in build_services[service_name]
        assert "pull_policy" not in build_services[service_name]
    for service_name in ALL_SERVICES:
        assert offline_services[service_name]["pull_policy"] == "never"


def test_production_environment_templates_are_exactly_equal() -> None:
    build_values = _parse_env(ENV_FILES[0])
    offline_values = _parse_env(ENV_FILES[1])
    # Compose resolves bind sources relative to the first -f file, so the two
    # equivalent templates intentionally use different root-relative spellings.
    build_values["WEB_HEALTH_ACL_FILE"] = "<site-health-acl>"
    offline_values["WEB_HEALTH_ACL_FILE"] = "<site-health-acl>"
    assert build_values == offline_values


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
def test_all_images_are_explicit_and_migrate_reuses_api(compose_path: Path) -> None:
    compose = _read(compose_path)

    assert compose.count("image: ${POSTGRES_IMAGE:?POSTGRES_IMAGE must be set}") == 1
    assert compose.count("image: ${REDIS_IMAGE:?REDIS_IMAGE must be set}") == 1
    assert compose.count("image: ${API_IMAGE:?API_IMAGE must be set}") == 2
    assert compose.count("image: ${GW_IMAGE:?GW_IMAGE must be set}") == 1
    assert compose.count("image: ${WEB_IMAGE:?WEB_IMAGE must be set}") == 1
    assert compose.count("platform: ${TARGET_PLATFORM:?TARGET_PLATFORM must be set}") == 6
    assert ":latest" not in compose
    assert "redis:7-alpine" not in compose


def test_build_compose_keeps_local_build_contexts() -> None:
    compose = _read(ROOT / "docker-compose.prod.yml")

    assert compose.count("    build:\n") == 4
    assert compose.count("dockerfile: ruisheng-api/Dockerfile") == 2
    assert "dockerfile: ruisheng-gw/Dockerfile" in compose
    assert "context: ruisheng-web" in compose


def test_offline_compose_contains_no_build_keys() -> None:
    compose = _read(ROOT / "deploy" / "docker-compose.prod.yml")

    assert "build:" not in compose
    assert compose.count("pull_policy: never") == 6


@pytest.mark.parametrize("compose_path", COMPOSE_FILES)
def test_gateway_wal_is_explicitly_persistent(compose_path: Path) -> None:
    compose = _read(compose_path)

    assert f"GW_WAL_DIR: {WAL_PATH}" in compose
    assert WAL_MOUNT in compose
    assert "  ruisheng-gw-wal:\n" in compose


def test_export_script_delegates_atomic_candidate_generation() -> None:
    script = (ROOT / "deploy" / "export-images.sh").read_text(encoding="utf-8")

    assert "Usage: $0 <candidate-id> <target-platform>" in script
    assert "tools/release_artifacts.py build" in script
    assert '--candidate-id "$CANDIDATE_ID"' in script
    assert '--target-platform "$TARGET_PLATFORM"' in script
    assert '--env-file "$ENV_FILE"' in script
    assert '--output-root "$PROJECT_ROOT/dist/deploy"' in script
    assert "docker save" not in script


def test_environment_templates_have_release_placeholders_without_mutable_runtime_tags() -> None:
    expected = {
        "TARGET_PLATFORM": "linux/amd64",
        "POSTGRES_IMAGE": "ruisheng-candidate/postgres:SET_BY_EXPORT",
        "REDIS_IMAGE": "ruisheng-candidate/redis:SET_BY_EXPORT",
        "API_IMAGE": "ruisheng-candidate/api:SET_BY_EXPORT",
        "GW_IMAGE": "ruisheng-candidate/gw:SET_BY_EXPORT",
        "WEB_IMAGE": "ruisheng-candidate/web:SET_BY_EXPORT",
    }
    for env_path in ENV_FILES:
        values = _parse_env(env_path)
        assert {key: values[key] for key in expected} == expected
        assert not any(":latest" in value for value in values.values())
        assert "redis:7-alpine" not in values.values()


def test_verifiers_preserve_integrity_before_load_and_authenticity_block() -> None:
    bash = _read(ROOT / "deploy" / "verify-candidate.sh")
    powershell = _read(ROOT / "deploy" / "verify-candidate.ps1")
    guide = _read(ROOT / "deploy" / "setup-customer.md")

    assert bash.index("SHA-256, and archive identities passed") < bash.index("docker image load")
    assert powershell.index("SHA-256, and archive identities passed") < powershell.index(
        "docker image load"
    )
    for document in (bash, powershell, guide):
        assert "CAP-1/G0-03" in document
        assert "BLOCKED" in document
    assert "不要编辑" in guide
    assert "站点 Compose override" in guide
    assert guide.count("verify-candidate.sh . ../site/.env.prod") == 2
    assert "verify-candidate.ps1 . ..\\site\\.env.prod" in guide
    assert "verify-candidate.ps1 . $Site" in guide
    assert "exit 2" in bash
    assert "exit 2" in powershell
    assert "sorted(keys - seen)" in guide
    assert "Where-Object { -not $Seen.ContainsKey($_) }" in guide
    for line in guide.splitlines():
        if line.startswith("docker compose"):
            assert "--env-file" in line


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


def test_production_migration_entrypoint_only_runs_alembic() -> None:
    script = _read(ROOT / "scripts" / "entrypoint-migrate.sh")

    assert "alembic upgrade head" in script
    assert "run_seeds" not in script
    assert "seeds/" not in script
    assert "Running seeds" not in script


def test_api_image_keeps_workspace_and_migration_assets_without_demo_seeds() -> None:
    dockerfile = _read(ROOT / "ruisheng-api" / "Dockerfile")

    assert "COPY ruisheng-shared/ ruisheng-shared/" in dockerfile
    assert "COPY ruisheng-api/ ruisheng-api/" in dockerfile
    assert "COPY tools/pcap_gen/ tools/pcap_gen/" in dockerfile
    assert "COPY alembic/ alembic/" in dockerfile
    assert "COPY alembic.ini ./" in dockerfile
    assert "COPY scripts/ scripts/" in dockerfile
    assert "uv sync --package ruisheng-api --no-dev --frozen" in dockerfile
    assert "COPY seeds/" not in dockerfile
    assert "COPY tools/ tools/" not in dockerfile


@pytest.mark.parametrize(("compose_path", "env_path"), zip(COMPOSE_FILES, ENV_FILES, strict=True))
def test_services_use_the_same_migration_entrypoint_and_wait_for_success(
    compose_path: Path, env_path: Path
) -> None:
    services = _render_compose(compose_path, env_path)["services"]

    assert services["migrate"]["entrypoint"] == ["bash", "scripts/entrypoint-migrate.sh"]
    for service_name in ("api", "gw"):
        assert services[service_name]["depends_on"]["migrate"] == {
            "condition": "service_completed_successfully",
            "required": True,
        }


def test_demo_seed_is_available_only_through_explicit_development_paths() -> None:
    project = tomllib.loads(_read(ROOT / "pyproject.toml"))
    makefile = _read(ROOT / "Makefile")
    dev_api = _read(ROOT / "tools" / "dev_api.ps1")
    ci_web = _read(ROOT / ".github" / "workflows" / "ci-web.yml")
    seed_runner = _read(ROOT / "tools" / "run_seeds.py")

    assert project["tool"]["taskipy"]["tasks"]["seed"] == "python tools/run_seeds.py"
    assert "seed:\n\tuv run task seed" in makefile
    assert "uv run python tools/run_seeds.py" in dev_api
    assert "uv run python tools/run_seeds.py" in ci_web
    assert ci_web.count("- 'tools/run_seeds.py'") == 2
    assert "本地开发/测试" in seed_runner
    assert "生产 bootstrap 不调用" in seed_runner


@pytest.mark.parametrize("document", PRODUCTION_DOCS)
def test_production_docs_do_not_publish_seed_identities_or_claim_bootstrap(
    document: Path,
) -> None:
    contents = _read(document)
    folded_contents = contents.casefold()

    assert _seed_document_forbidden_values()
    for forbidden_value in _seed_document_forbidden_values():
        assert forbidden_value.casefold() not in folded_contents
    assert "默认账号" not in contents
    assert "首次登录后" not in contents
    assert "写入初始演示数据" not in contents
    assert "管理员引导和凭据交接尚未交付" in contents
    assert "B-02 不解除 G0-05/CAP-2" in contents


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
