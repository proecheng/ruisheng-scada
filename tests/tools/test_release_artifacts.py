"""Offline release artifact generation and verification contracts."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
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
        if command in {
            ("git", "status", "--porcelain", "--untracked-files=no"),
            ("git", "status", "--porcelain", "--untracked-files=all"),
        }:
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


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _blob_name(digest: str) -> str:
    return f"blobs/sha256/{digest.removeprefix('sha256:')}"


def _write_docker_29_provenance_archive(  # noqa: PLR0912, PLR0915
    tmp_path: Path,
    *,
    mutation: str | None = None,
) -> tuple[Path, str, str]:
    reference = "ruisheng-candidate/web:docker29"
    image_config = _json_bytes({"architecture": "amd64", "os": "linux"})
    image_config_digest = _digest(image_config)
    image_manifest = _json_bytes(
        {
            "config": {"digest": image_config_digest},
            "layers": [],
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "schemaVersion": 2,
        }
    )
    image_manifest_digest = _digest(image_manifest)

    second_image_config = _json_bytes({"architecture": "arm64", "os": "linux"})
    second_image_config_digest = _digest(second_image_config)
    second_image_manifest = _json_bytes(
        {
            "config": {"digest": second_image_config_digest},
            "layers": [],
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "schemaVersion": 2,
        }
    )
    second_image_manifest_digest = _digest(second_image_manifest)

    nested_provenance_layer = _json_bytes(
        {
            "_type": "https://in-toto.io/Statement/v0.1",
            "predicateType": "https://slsa.dev/provenance/v1",
            "subject": [
                {
                    "name": "pkg:docker/ruisheng-build/web@docker29",
                    "digest": {"sha256": image_manifest_digest.removeprefix("sha256:")},
                }
            ],
            "predicate": {},
        }
    )
    nested_provenance_layer_digest = _digest(nested_provenance_layer)
    nested_provenance_config = _json_bytes(
        {"architecture": "unknown", "config": {}, "os": "unknown"}
    )
    nested_provenance_config_digest = _digest(nested_provenance_config)
    nested_provenance_manifest = _json_bytes(
        {
            "config": {
                "digest": nested_provenance_config_digest,
                "mediaType": "application/vnd.oci.image.config.v1+json",
            },
            "layers": [
                {
                    "annotations": {"in-toto.io/predicate-type": "https://slsa.dev/provenance/v1"},
                    "digest": nested_provenance_layer_digest,
                    "mediaType": "application/vnd.in-toto+json",
                }
            ],
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "schemaVersion": 2,
        }
    )
    nested_provenance_manifest_digest = _digest(nested_provenance_manifest)

    provenance_statement: dict[str, object] = {
        "_type": "https://in-toto.io/Statement/v0.1",
        "predicateType": "https://slsa.dev/provenance/v1",
        "subject": [
            {
                "name": "pkg:docker/ruisheng-candidate/web@docker29",
                "digest": {"sha256": image_manifest_digest.removeprefix("sha256:")},
            }
        ],
        "predicate": {},
    }
    if mutation == "empty_statement":
        provenance_statement = {}
    elif mutation == "wrong_statement_type":
        provenance_statement["_type"] = "https://in-toto.io/Statement/v1"
    elif mutation == "wrong_predicate_type":
        provenance_statement["predicateType"] = "https://example.invalid/predicate"
    elif mutation == "missing_predicate":
        del provenance_statement["predicate"]
    elif mutation == "wrong_statement_subject":
        provenance_statement["subject"] = [
            {
                "name": "pkg:docker/ruisheng-candidate/web@wrong",
                "digest": {"sha256": "f" * 64},
            }
        ]
    provenance_layer = _json_bytes(provenance_statement)
    provenance_layer_digest = _digest(provenance_layer)
    provenance_config = _json_bytes(
        {
            "architecture": "amd64" if mutation == "platform" else "unknown",
            "os": "unknown",
        }
    )
    provenance_config_digest = _digest(provenance_config)
    provenance_layer_descriptor = {
        "annotations": {"in-toto.io/predicate-type": "https://slsa.dev/provenance/v1"},
        "digest": provenance_layer_digest,
        "mediaType": (
            "application/octet-stream"
            if mutation == "layer_media_type"
            else "application/vnd.in-toto+json"
        ),
    }
    provenance_layers: object = [provenance_layer_descriptor]
    if mutation == "multiple_layers":
        provenance_layers = [provenance_layer_descriptor, provenance_layer_descriptor]
    elif mutation == "layers_object":
        provenance_layers = provenance_layer_descriptor
    provenance_manifest = _json_bytes(
        {
            "config": {
                "digest": provenance_config_digest,
                "mediaType": "application/vnd.oci.image.config.v1+json",
            },
            "layers": provenance_layers,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "schemaVersion": "2" if mutation == "schema_type" else 2,
        }
    )
    provenance_manifest_digest = _digest(provenance_manifest)
    source_manifests = [
        {
            "digest": image_manifest_digest,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "platform": {"architecture": "amd64", "os": "linux"},
        },
        {
            "digest": nested_provenance_manifest_digest,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "platform": {"architecture": "unknown", "os": "unknown"},
        },
    ]
    if mutation == "nested_second_main":
        source_manifests.insert(
            1,
            {
                "digest": second_image_manifest_digest,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "platform": {"architecture": "arm64", "os": "linux"},
            },
        )
    source_index = _json_bytes(
        {
            "manifests": source_manifests,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "schemaVersion": 2,
        }
    )
    source_index_digest = _digest(source_index)
    top_descriptors = [
        {
            "digest": source_index_digest,
            "mediaType": "application/vnd.oci.image.index.v1+json",
        },
        {
            "annotations": {
                "io.containerd.manifest.subject": (
                    f"sha256:{'f' * 64}" if mutation == "wrong_subject" else image_manifest_digest
                )
            },
            "digest": provenance_manifest_digest,
            "mediaType": (
                "application/octet-stream"
                if mutation == "unknown_attachment"
                else "application/vnd.oci.image.manifest.v1+json"
            ),
        },
    ]
    if mutation == "second_main":
        top_descriptors.append(
            {
                "digest": image_manifest_digest,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
            }
        )

    legacy_manifest = _json_bytes(
        [
            {
                "Config": _blob_name(image_config_digest),
                "Layers": [],
                "RepoTags": [reference],
            }
        ]
    )
    top_manifests: object = top_descriptors
    if mutation == "index_manifests_object":
        top_manifests = top_descriptors[0]
    top_index = _json_bytes(
        {
            "manifests": top_manifests,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "schemaVersion": 2,
        }
    )
    blobs = {
        _blob_name(image_config_digest): image_config,
        _blob_name(image_manifest_digest): image_manifest,
        _blob_name(nested_provenance_config_digest): nested_provenance_config,
        _blob_name(nested_provenance_layer_digest): nested_provenance_layer,
        _blob_name(nested_provenance_manifest_digest): nested_provenance_manifest,
        _blob_name(provenance_config_digest): provenance_config,
        _blob_name(provenance_layer_digest): provenance_layer,
        _blob_name(provenance_manifest_digest): provenance_manifest,
        _blob_name(source_index_digest): source_index,
    }
    if mutation == "nested_second_main":
        blobs[_blob_name(second_image_config_digest)] = second_image_config
        blobs[_blob_name(second_image_manifest_digest)] = second_image_manifest
    if mutation == "missing_descriptor_blob":
        del blobs[_blob_name(provenance_manifest_digest)]
    elif mutation == "descriptor_digest_tamper":
        blobs[_blob_name(provenance_manifest_digest)] = b"tampered"
    elif mutation == "missing_config_blob":
        del blobs[_blob_name(provenance_config_digest)]
    elif mutation == "config_digest_tamper":
        blobs[_blob_name(provenance_config_digest)] = b"tampered"
    elif mutation == "missing_blob":
        del blobs[_blob_name(provenance_layer_digest)]
    elif mutation == "digest_tamper":
        blobs[_blob_name(provenance_layer_digest)] = b"tampered"

    path = tmp_path / f"docker29-{mutation or 'valid'}.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        _add_tar_bytes(archive, "manifest.json", legacy_manifest)
        _add_tar_bytes(archive, "index.json", top_index)
        for name, contents in blobs.items():
            _add_tar_bytes(archive, name, contents)
    return path, reference, source_index_digest


INVALID_DOCKER29_ATTACHMENTS = (
    ("second_main", "main descriptor is not unique"),
    ("nested_second_main", "additional runnable descriptor"),
    ("wrong_subject", "provenance subject mismatch"),
    ("unknown_attachment", "unsupported archive attachment"),
    ("platform", "provenance config platform mismatch"),
    ("layer_media_type", "provenance layer media type is invalid"),
    ("multiple_layers", "provenance layers are invalid"),
    ("layers_object", "provenance layers are invalid"),
    ("schema_type", "unsupported archive attachment"),
    ("empty_statement", "provenance statement is invalid"),
    ("wrong_statement_type", "provenance statement is invalid"),
    ("wrong_predicate_type", "provenance statement is invalid"),
    ("missing_predicate", "provenance statement is invalid"),
    ("wrong_statement_subject", "provenance statement subject mismatch"),
    ("index_manifests_object", "index must contain image descriptors"),
    ("missing_descriptor_blob", "descriptor blob is missing"),
    ("descriptor_digest_tamper", "descriptor digest mismatch"),
    ("missing_config_blob", "provenance config blob is missing"),
    ("config_digest_tamper", "provenance config digest mismatch"),
    ("missing_blob", "provenance layer blob is missing"),
    ("digest_tamper", "provenance layer digest mismatch"),
)


def _run_powershell_archive_identity(
    path: Path, reference: str
) -> subprocess.CompletedProcess[str]:
    command = r"""
$source = Get-Content -Raw -LiteralPath $env:RS_VERIFY_SCRIPT
$start = $source.IndexOf('function Fail')
$end = $source.IndexOf('if ($Manifest.candidate_id')
if ($start -lt 0 -or $end -lt 0) { throw 'Archive function block not found' }
. ([scriptblock]::Create($source.Substring($start, $end - $start)))
Get-DockerArchiveIdentity $env:RS_ARCHIVE_PATH $env:RS_ARCHIVE_REFERENCE |
    ConvertTo-Json -Compress
"""
    environment = os.environ.copy()
    environment.update(
        {
            "RS_VERIFY_SCRIPT": str(ROOT / "deploy" / "verify-candidate.ps1"),
            "RS_ARCHIVE_PATH": str(path),
            "RS_ARCHIVE_REFERENCE": reference,
        }
    )
    return subprocess.run(
        [
            shutil.which("pwsh") or "pwsh",
            "-NoProfile",
            "-Command",
            command,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=30,
    )


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


def test_archive_accepts_docker_29_selected_platform_under_source_index(
    tmp_path: Path,
) -> None:
    reference = "ruisheng-candidate/postgres:expected"
    config = json.dumps({"architecture": "amd64", "os": "linux"}).encode()
    config_id = hashlib.sha256(config).hexdigest()
    child = json.dumps(
        {
            "schemaVersion": 2,
            "config": {"digest": f"sha256:{config_id}"},
            "layers": [],
        },
        sort_keys=True,
    ).encode()
    child_id = hashlib.sha256(child).hexdigest()
    source_index = json.dumps(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "digest": f"sha256:{child_id}",
                    "platform": {"architecture": "amd64", "os": "linux"},
                },
                {
                    "digest": f"sha256:{'f' * 64}",
                    "platform": {"architecture": "arm64", "os": "linux"},
                },
            ],
        },
        sort_keys=True,
    ).encode()
    source_index_id = hashlib.sha256(source_index).hexdigest()
    manifest = json.dumps(
        [
            {
                "Config": f"blobs/sha256/{config_id}",
                "Layers": [],
                "RepoTags": [reference],
            }
        ]
    ).encode()
    index = json.dumps({"manifests": [{"digest": f"sha256:{source_index_id}"}]}).encode()
    path = tmp_path / "multi-platform-source-index.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        _add_tar_bytes(archive, "manifest.json", manifest)
        _add_tar_bytes(archive, "index.json", index)
        _add_tar_bytes(archive, f"blobs/sha256/{config_id}", config)
        _add_tar_bytes(archive, f"blobs/sha256/{child_id}", child)
        _add_tar_bytes(archive, f"blobs/sha256/{source_index_id}", source_index)

    identity = inspect_docker_archive(path, reference)

    assert identity.image_id == f"sha256:{source_index_id}"
    assert identity.os == "linux"
    assert identity.architecture == "amd64"


def test_archive_accepts_docker_29_top_level_provenance_referrer(tmp_path: Path) -> None:
    path, reference, source_index_digest = _write_docker_29_provenance_archive(tmp_path)

    identity = inspect_docker_archive(path, reference)

    assert identity.image_id == source_index_digest
    assert identity.os == "linux"
    assert identity.architecture == "amd64"


@pytest.mark.parametrize(
    ("mutation", "error"),
    INVALID_DOCKER29_ATTACHMENTS,
)
def test_archive_rejects_invalid_docker_29_top_level_attachment(
    tmp_path: Path, mutation: str, error: str
) -> None:
    path, reference, _source_index_digest = _write_docker_29_provenance_archive(
        tmp_path, mutation=mutation
    )

    with pytest.raises(ReleaseArtifactError, match=error):
        inspect_docker_archive(path, reference)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
def test_powershell_accepts_docker_29_top_level_provenance_referrer(tmp_path: Path) -> None:
    path, reference, source_index_digest = _write_docker_29_provenance_archive(tmp_path)

    result = _run_powershell_archive_identity(path, reference)

    assert result.returncode == 0, result.stderr
    identity = json.loads(result.stdout)
    assert identity == {
        "ImageId": source_index_digest,
        "Os": "linux",
        "Architecture": "amd64",
    }


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(("mutation", "error"), INVALID_DOCKER29_ATTACHMENTS)
def test_powershell_rejects_invalid_docker_29_top_level_attachment(
    tmp_path: Path, mutation: str, error: str
) -> None:
    path, reference, _source_index_digest = _write_docker_29_provenance_archive(
        tmp_path, mutation=mutation
    )

    result = _run_powershell_archive_identity(path, reference)

    assert result.returncode != 0
    assert error.lower() in result.stderr.lower()


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
