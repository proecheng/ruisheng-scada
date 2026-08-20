"""Offline release artifact generation and verification contracts."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tarfile
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from tools.release_artifacts import (
    COMPONENTS,
    FIXED_PACKAGE_FILES,
    CandidateManifest,
    ReleaseArtifactError,
    build_candidate,
    candidate_image_references,
    compute_logical_identity,
    inspect_docker_archive,
    load_and_verify_images,
    render_manifest_markdown,
    verify_package,
)

ROOT = Path(__file__).parents[2]
COMMIT = "a" * 40
CANDIDATE_ID = "deploy-20260819.1"
PLATFORM = "linux/amd64"


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[tuple[tuple[str, ...], dict[str, str]]] = []
        self.images: dict[str, dict[str, object]] = {}
        self.configs: dict[str, bytes] = {}
        self.dirty = ""
        self.fail_save_component: str | None = None
        self.compose_image_override: list[str] | None = None
        self.compose_service_override: dict[str, dict[str, str]] | None = None
        self.image_inspect_errors: dict[str, str] = {}
        self.final_commit = COMMIT
        self.git_head_calls = 0
        self.loaded: list[str] = []
        self._add_source("timescale/timescaledb:2.16.1-pg15", "postgres")
        self._add_source("redis:7-alpine", "redis")

    def _add_source(self, reference: str, component: str, *, architecture: str = "amd64") -> None:
        config = json.dumps(
            {"architecture": architecture, "component": component, "os": "linux"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        image_id = f"sha256:{hashlib.sha256(config).hexdigest()}"
        repository = reference.rsplit(":", maxsplit=1)[0]
        self.images[reference] = {
            "Architecture": architecture,
            "Id": image_id,
            "Os": "linux",
            "RepoDigests": [f"{repository}@{image_id}"],
            "RepoTags": [reference],
        }
        self.configs[reference] = config

    def run(  # noqa: PLR0911, PLR0912
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
    ) -> str:
        del cwd
        command = tuple(str(arg) for arg in args)
        command_env = dict(env or {})
        self.commands.append((command, command_env))
        if command == ("git", "rev-parse", "HEAD"):
            self.git_head_calls += 1
            return COMMIT if self.git_head_calls == 1 else self.final_commit
        if command == ("git", "status", "--porcelain", "--untracked-files=no"):
            return self.dirty
        if command[:4] == ("docker", "image", "pull", "--platform"):
            if command[-1] not in self.images:
                raise ReleaseArtifactError(f"unknown source image: {command[-1]}")
            return command[-1]
        if command[:3] == ("docker", "image", "tag"):
            source, destination = command[3], command[4]
            source_value = self.images[source]
            tags = sorted({*source_value["RepoTags"], destination})
            self.images[destination] = {**source_value, "RepoTags": tags}
            self.configs[destination] = self.configs[source]
            return ""
        if command[:4] == ("docker", "image", "rm", "--force"):
            reference = command[4]
            self.images.pop(reference, None)
            self.configs.pop(reference, None)
            return ""
        if command[:2] == ("docker", "compose") and "build" in command:
            for component in ("api", "gw", "web"):
                reference = command_env[f"{component.upper()}_IMAGE"]
                self._add_source(reference, component)
            return ""
        if command[:3] == ("docker", "image", "inspect"):
            return json.dumps(self.images[command[3]], sort_keys=True)
        if len(command) >= 3 and command[1:3] == ("-m", "alembic"):
            assert command[0] == sys.executable
            return "0012_alarm_notification_runtime (head)"
        if command[:3] == ("docker", "version", "--format"):
            return "29.4.0/29.4.0"
        if command == ("docker", "compose", "version", "--short"):
            return "5.1.1"
        if command == ("git", "--version"):
            return "git version 2.51.0"
        if command[:3] == ("docker", "image", "load"):
            self.loaded.append(command[-1])
            return "Loaded"
        if command[:2] == ("docker", "compose") and command[-2:] == ("config", "--images"):
            if self.compose_image_override is not None:
                return "\n".join(self.compose_image_override)
            env_values = _read_env(Path(command[command.index("--env-file") + 1]))
            return "\n".join(
                (
                    env_values["POSTGRES_IMAGE"],
                    env_values["REDIS_IMAGE"],
                    env_values["API_IMAGE"],
                    env_values["API_IMAGE"],
                    env_values["GW_IMAGE"],
                    env_values["WEB_IMAGE"],
                )
            )
        if command[:2] == ("docker", "compose") and command[-3:] == (
            "config",
            "--format",
            "json",
        ):
            env_values = _read_env(Path(command[command.index("--env-file") + 1]))
            images = {
                "postgres": env_values["POSTGRES_IMAGE"],
                "redis": env_values["REDIS_IMAGE"],
                "migrate": env_values["API_IMAGE"],
                "api": env_values["API_IMAGE"],
                "gw": env_values["GW_IMAGE"],
                "web": env_values["WEB_IMAGE"],
            }
            if self.compose_service_override is not None:
                return json.dumps({"services": self.compose_service_override})
            return json.dumps(
                {
                    "services": {
                        name: {
                            "image": image,
                            "platform": env_values["TARGET_PLATFORM"],
                            "pull_policy": "never",
                        }
                        for name, image in images.items()
                    }
                }
            )
        raise AssertionError(f"unexpected fake command: {command}")

    def save_image(self, image: str, destination: Path, *, cwd: Path) -> None:
        del cwd
        component = image.split("/", maxsplit=1)[1].split(":", maxsplit=1)[0]
        if component == self.fail_save_component:
            destination.write_bytes(b"partial")
            raise ReleaseArtifactError(f"injected save failure: {component}")
        config = self.configs[image]
        image_id = hashlib.sha256(config).hexdigest()
        manifest = json.dumps(
            [{"Config": f"{image_id}.json", "Layers": [], "RepoTags": [image]}],
            sort_keys=True,
        ).encode()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(destination, "w:gz") as archive:
            _add_tar_bytes(archive, "manifest.json", manifest)
            _add_tar_bytes(archive, f"{image_id}.json", config)

    def image_exists(self, image: str, *, cwd: Path) -> bool:
        del cwd
        command = ("docker", "image", "inspect", image, "--format", "{{json .Id}}")
        self.commands.append((command, {}))
        if error := self.image_inspect_errors.get(image):
            raise ReleaseArtifactError(error)
        return image in self.images


def _add_tar_bytes(archive: tarfile.TarFile, name: str, contents: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(contents)
    member.mtime = 0
    archive.addfile(member, io.BytesIO(contents))


def _read_env(path: Path) -> dict[str, str]:
    return {
        key: value
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
        for key, separator, value in (line.partition("="),)
        if separator
    }


@pytest.fixture
def production_env(tmp_path: Path) -> Path:
    path = tmp_path / ".env.prod"
    path.write_text("SAFE=not-copied\n", encoding="utf-8")
    return path


def _build(
    tmp_path: Path,
    production_env: Path,
    runner: FakeRunner,
    *,
    candidate_id: str = CANDIDATE_ID,
) -> Path:
    return build_candidate(
        root=ROOT,
        output_root=tmp_path / "dist" / "deploy",
        candidate_id=candidate_id,
        target_platform=PLATFORM,
        env_file=production_env,
        postgres_source="timescale/timescaledb:2.16.1-pg15",
        redis_source="redis:7-alpine",
        runner=runner,
        lock_root=tmp_path / "candidate-locks",
    )


def test_build_candidate_closes_five_image_manifest_and_sha_contract(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()

    package = _build(tmp_path, production_env, runner)

    assert package == tmp_path / "dist" / "deploy" / CANDIDATE_ID
    assert {
        path.relative_to(package).as_posix() for path in package.rglob("*") if path.is_file()
    } == (FIXED_PACKAGE_FILES | {f"images/{component}.tar.gz" for component in COMPONENTS})
    manifest = verify_package(package, runner)
    assert tuple(image.component for image in manifest.images) == COMPONENTS
    assert len({image.archive for image in manifest.images}) == 5
    assert len({image.candidate_reference for image in manifest.images}) == 5
    assert len({image.image_id for image in manifest.images}) == 5
    assert manifest.source_commit == COMMIT
    assert manifest.alembic_head == "0012_alarm_notification_runtime"
    assert manifest.authenticity["status"] == "BLOCKED"
    assert (package / "MANIFEST.md").read_text(encoding="utf-8") == render_manifest_markdown(
        manifest
    )
    env_values = _read_env(package / ".env.prod.example")
    assert env_values["TARGET_PLATFORM"] == PLATFORM
    assert {
        env_values["POSTGRES_IMAGE"],
        env_values["REDIS_IMAGE"],
        env_values["API_IMAGE"],
        env_values["GW_IMAGE"],
        env_values["WEB_IMAGE"],
    } == set(candidate_image_references(CANDIDATE_ID).values())
    assert "SAFE=not-copied" not in (package / ".env.prod.example").read_text(encoding="utf-8")
    assert not list((tmp_path / "dist" / "deploy").glob(".*.tmp-*"))


def test_logical_identity_is_stable_for_the_same_immutable_inputs(
    tmp_path: Path, production_env: Path
) -> None:
    manifest = verify_package(_build(tmp_path, production_env, FakeRunner()), FakeRunner())

    first = compute_logical_identity(
        candidate_id=manifest.candidate_id,
        source_commit=manifest.source_commit,
        target_os=manifest.target_os,
        target_architecture=manifest.target_architecture,
        alembic_head=manifest.alembic_head,
        images=manifest.images,
    )
    second = compute_logical_identity(
        candidate_id=manifest.candidate_id,
        source_commit=manifest.source_commit,
        target_os=manifest.target_os,
        target_architecture=manifest.target_architecture,
        alembic_head=manifest.alembic_head,
        images=manifest.images,
    )

    assert first == second == manifest.logical_identity


def test_candidate_id_reuse_is_rejected_without_temporary_output(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)

    with pytest.raises(ReleaseArtifactError, match="candidate ID already exists"):
        _build(tmp_path, production_env, runner)

    assert package.is_dir()
    assert not list(package.parent.glob(".*.tmp-*"))


def test_candidate_tag_collision_is_rejected_before_staging_or_tagging(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    references = candidate_image_references(CANDIDATE_ID)
    runner._add_source(references["web"], "collision")

    with pytest.raises(ReleaseArtifactError, match="candidate image tag already exists.*web"):
        _build(tmp_path, production_env, runner)

    assert not (tmp_path / "dist" / "deploy").exists()
    inspected_references = [
        command[3]
        for command, _env in runner.commands
        if command[:3] == ("docker", "image", "inspect")
    ]
    assert inspected_references == list(references.values())
    assert not any(
        command[:3] == ("docker", "image", "tag")
        or (command[:2] == ("docker", "compose") and "build" in command)
        for command, _env in runner.commands
    )


def test_candidate_tag_inspect_error_is_not_treated_as_missing(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    reference = candidate_image_references(CANDIDATE_ID)["postgres"]
    runner.image_inspect_errors[reference] = "injected Docker daemon failure"

    with pytest.raises(ReleaseArtifactError, match="injected Docker daemon failure"):
        _build(tmp_path, production_env, runner)

    assert not (tmp_path / "dist" / "deploy").exists()


def test_concurrent_candidate_lock_is_rejected_without_tag_changes(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    output_root = tmp_path / "dist" / "deploy"
    output_root.mkdir(parents=True)
    lock_path = tmp_path / "candidate-locks" / f"{CANDIDATE_ID}.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("pid=123\n", encoding="ascii")

    with pytest.raises(ReleaseArtifactError, match="candidate build already in progress"):
        _build(tmp_path, production_env, runner)

    assert lock_path.is_file()
    assert not any(
        reference in runner.images
        for reference in candidate_image_references(CANDIDATE_ID).values()
    )


def test_candidate_lock_write_failure_removes_new_lock(
    tmp_path: Path, production_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner()

    def fail_lock_write(_descriptor: int, _value: bytes) -> int:
        raise OSError("injected lock write failure")

    monkeypatch.setattr(os, "write", fail_lock_write)
    with pytest.raises(ReleaseArtifactError, match="injected lock write failure"):
        _build(tmp_path, production_env, runner)

    lock_path = tmp_path / "candidate-locks" / f"{CANDIDATE_ID}.lock"
    assert not lock_path.exists()


def test_dirty_tracked_input_is_rejected_before_staging(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    runner.dirty = " M deploy/export-images.sh"

    with pytest.raises(ReleaseArtifactError, match="tracked release inputs are dirty"):
        _build(tmp_path, production_env, runner)

    assert not (tmp_path / "dist" / "deploy").exists()


def test_partial_archive_failure_removes_all_temporary_output(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    runner.fail_save_component = "gw"

    with pytest.raises(ReleaseArtifactError, match="injected save failure"):
        _build(tmp_path, production_env, runner)

    output_root = tmp_path / "dist" / "deploy"
    assert output_root.is_dir()
    assert list(output_root.iterdir()) == []
    assert not any(
        reference in runner.images
        for reference in candidate_image_references(CANDIDATE_ID).values()
    )


def test_tracked_inputs_changing_during_build_rejects_candidate_and_tags(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    runner.final_commit = "b" * 40

    with pytest.raises(ReleaseArtifactError, match="changed HEAD"):
        _build(tmp_path, production_env, runner)

    output_root = tmp_path / "dist" / "deploy"
    assert list(output_root.iterdir()) == []
    assert not any(
        reference in runner.images
        for reference in candidate_image_references(CANDIDATE_ID).values()
    )


def test_platform_mismatch_removes_temporary_output(tmp_path: Path, production_env: Path) -> None:
    runner = FakeRunner()
    runner._add_source("redis:7-alpine", "redis", architecture="arm64")

    with pytest.raises(ReleaseArtifactError, match="platform mismatch for redis"):
        _build(tmp_path, production_env, runner)

    assert not list((tmp_path / "dist" / "deploy").glob(".*.tmp-*"))


@pytest.mark.parametrize(
    ("mutation", "error"),
    (
        (
            lambda package: (package / "unexpected.txt").write_text("extra"),
            "file allowlist mismatch",
        ),
        (lambda package: (package / "images" / "web.tar.gz").unlink(), "file allowlist mismatch"),
        (
            lambda package: (package / "setup-customer.md").write_text("tampered"),
            "SHA-256 mismatch",
        ),
    ),
)
def test_verify_rejects_extra_missing_and_tampered_files(
    tmp_path: Path,
    production_env: Path,
    mutation: object,
    error: str,
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)
    mutation(package)  # type: ignore[operator]

    with pytest.raises(ReleaseArtifactError, match=error):
        verify_package(package, runner)


@pytest.mark.parametrize(
    "bad_line",
    (
        "0" * 64 + "  ../outside",
        "0" * 64 + "  /absolute",
        "0" * 64 + "  images\\web.tar.gz",
    ),
)
def test_verify_rejects_sha_path_escape(
    tmp_path: Path, production_env: Path, bad_line: str
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)
    with (package / "SHA256SUMS").open("a", encoding="utf-8") as sums:
        sums.write(bad_line + "\n")

    with pytest.raises(ReleaseArtifactError, match="unsafe package path"):
        verify_package(package, runner)


def test_archive_tag_collision_or_drift_is_rejected(tmp_path: Path) -> None:
    config = json.dumps({"architecture": "amd64", "os": "linux"}).encode()
    config_id = hashlib.sha256(config).hexdigest()
    path = tmp_path / "drift.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        _add_tar_bytes(
            archive,
            "manifest.json",
            json.dumps(
                [
                    {
                        "Config": f"{config_id}.json",
                        "Layers": [],
                        "RepoTags": ["ruisheng-candidate/api:wrong"],
                    }
                ]
            ).encode(),
        )
        _add_tar_bytes(archive, f"{config_id}.json", config)

    with pytest.raises(ReleaseArtifactError, match="RepoTags mismatch"):
        inspect_docker_archive(path, "ruisheng-candidate/api:expected")


def test_compose_image_drift_is_rejected(tmp_path: Path, production_env: Path) -> None:
    build_runner = FakeRunner()
    package = _build(tmp_path, production_env, build_runner)
    verify_runner = FakeRunner()
    verify_runner.compose_image_override = ["wrong/image:tag"] * 6

    with pytest.raises(ReleaseArtifactError, match="Compose image set mismatch"):
        verify_package(package, verify_runner)


@pytest.mark.parametrize("drift", ("image", "platform"))
def test_compose_service_mapping_and_platform_drift_are_rejected(
    tmp_path: Path, production_env: Path, drift: str
) -> None:
    build_runner = FakeRunner()
    package = _build(tmp_path, production_env, build_runner)
    verify_runner = FakeRunner()
    env_values = _read_env(package / ".env.prod.example")
    images = {
        "postgres": env_values["POSTGRES_IMAGE"],
        "redis": env_values["REDIS_IMAGE"],
        "migrate": env_values["API_IMAGE"],
        "api": env_values["API_IMAGE"],
        "gw": env_values["GW_IMAGE"],
        "web": env_values["WEB_IMAGE"],
    }
    verify_runner.compose_service_override = {
        name: {"image": image, "platform": PLATFORM, "pull_policy": "never"}
        for name, image in images.items()
    }
    if drift == "image":
        verify_runner.compose_service_override["postgres"]["image"] = images["redis"]
        verify_runner.compose_service_override["redis"]["image"] = images["postgres"]
    else:
        verify_runner.compose_service_override["web"]["platform"] = "linux/arm64"

    with pytest.raises(ReleaseArtifactError, match=f"Compose {drift} mismatch"):
        verify_package(package, verify_runner)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    (
        ("candidate_id", 123, "scalar fields have invalid types"),
        ("generated_at", "not-a-timestamp", "generated_at must be an ISO-8601 timestamp"),
        ("generated_at", "2026-08-19T10:00:00", "generated_at must include a timezone"),
    ),
)
def test_manifest_invalid_scalar_is_rejected_as_release_error(
    tmp_path: Path, production_env: Path, field: str, value: object, error: str
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)
    manifest_path = package / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    sums_path = package / "SHA256SUMS"
    sums = sums_path.read_text(encoding="utf-8").splitlines()
    sums_path.write_text(
        "\n".join(
            f"{hashlib.sha256(manifest_path.read_bytes()).hexdigest()}  MANIFEST.json"
            if line.endswith("  MANIFEST.json")
            else line
            for line in sums
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ReleaseArtifactError, match=error):
        verify_package(package, runner)


def test_load_verification_rejects_loaded_image_identity_drift(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)
    manifest = verify_package(package, runner)
    api_reference = candidate_image_references(CANDIDATE_ID)["api"]
    runner.images[api_reference] = {
        **runner.images[api_reference],
        "Id": "sha256:" + "f" * 64,
    }

    with pytest.raises(ReleaseArtifactError, match="loaded image identity mismatch for api"):
        load_and_verify_images(package, manifest, runner)

    assert len(runner.loaded) == 5


def test_generated_manifest_preserves_blocked_authenticity_language(
    tmp_path: Path, production_env: Path
) -> None:
    package = _build(tmp_path, production_env, FakeRunner())
    manifest_value = json.loads((package / "MANIFEST.json").read_text(encoding="utf-8"))
    markdown = (package / "MANIFEST.md").read_text(encoding="utf-8")

    assert manifest_value["authenticity"]["status"] == "BLOCKED"
    assert "CAP-1 and G0-03 remain **BLOCKED**" in markdown
    assert "signature" in manifest_value["authenticity"]["reason"].casefold()


def test_manifest_dataclass_has_only_expected_public_contract_fields() -> None:
    assert set(CandidateManifest.__dataclass_fields__) == {
        "alembic_head",
        "authenticity",
        "candidate_id",
        "generated_at",
        "images",
        "logical_identity",
        "schema_version",
        "source_commit",
        "target_architecture",
        "target_os",
        "tools",
    }
