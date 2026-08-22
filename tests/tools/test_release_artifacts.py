"""Offline release artifact generation and verification contracts."""

from __future__ import annotations

import base64
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
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tools import release_artifacts
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


def _fake_key_blob(seed: bytes = b"r" * 32) -> bytes:
    key_type = b"ssh-ed25519"
    return len(key_type).to_bytes(4, "big") + key_type + len(seed).to_bytes(4, "big") + seed


def _ssh_string(value: bytes) -> bytes:
    return len(value).to_bytes(4, "big") + value


def _write_test_sshsig(path: Path, payload: bytes, private_key: Ed25519PrivateKey) -> None:
    namespace = b"ruisheng-candidate-v1"
    hash_algorithm = b"sha512"
    signed_payload = (
        b"SSHSIG"
        + _ssh_string(namespace)
        + _ssh_string(b"")
        + _ssh_string(hash_algorithm)
        + _ssh_string(hashlib.sha512(payload).digest())
    )
    signature_blob = _ssh_string(b"ssh-ed25519") + _ssh_string(private_key.sign(signed_payload))
    public_blob = _fake_key_blob(private_key.public_key().public_bytes_raw())
    binary_signature = (
        b"SSHSIG"
        + (1).to_bytes(4, "big")
        + _ssh_string(public_blob)
        + _ssh_string(namespace)
        + _ssh_string(b"")
        + _ssh_string(hash_algorithm)
        + _ssh_string(signature_blob)
    )
    encoded = base64.b64encode(binary_signature)
    body = b"\n".join(encoded[index : index + 70] for index in range(0, len(encoded), 70))
    path.write_bytes(b"-----BEGIN SSH SIGNATURE-----\n" + body + b"\n-----END SSH SIGNATURE-----\n")


def _write_fake_release_trust(tmp_path: Path) -> tuple[Path, Path]:
    trust = tmp_path / "release-trust"
    trust.mkdir(exist_ok=True)
    blob = _fake_key_blob()
    encoded = base64.b64encode(blob).decode("ascii")
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode(
        "ascii"
    ).rstrip("=")
    (trust / "release-allowed-signers").write_text(
        f"ruisheng-release ssh-ed25519 {encoded}\n", encoding="ascii", newline="\n"
    )
    (trust / "release-key-fingerprint").write_text(
        fingerprint + "\n", encoding="ascii", newline="\n"
    )
    identity = tmp_path / "release-signing-identity.pub"
    identity.write_text(
        f"ssh-ed25519 {encoded} test-agent-backed-release\n",
        encoding="ascii",
        newline="\n",
    )
    return trust, identity


def _trust_for_package(package: Path) -> Path:
    return package.parents[2] / "release-trust"


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[tuple[tuple[str, ...], dict[str, str]]] = []
        self.images: dict[str, dict[str, object]] = {}
        self.configs: dict[str, bytes] = {}
        self.dirty = ""
        self.fail_save_component: str | None = None
        self.fail_signature = False
        self.compose_image_override: list[str] | None = None
        self.compose_service_override: dict[str, dict[str, str]] | None = None
        self.image_inspect_errors: dict[str, str] = {}
        self.final_commit = COMMIT
        self.git_head_calls = 0
        self.loaded: list[str] = []
        self.signed_payload: bytes | None = None
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
        input_bytes: bytes | None = None,
    ) -> str:
        command = tuple(str(arg) for arg in args)
        command_env = dict(env or {})
        self.commands.append((command, command_env))
        if Path(command[0]).name.casefold() in {"ssh-keygen", "ssh-keygen.exe"} and command[
            1:3
        ] == ("-Y", "sign"):
            if self.fail_signature:
                raise ReleaseArtifactError("injected signature failure")
            signed_object = Path(command[-1])
            self.signed_payload = signed_object.read_bytes()
            signed_object.with_name(signed_object.name + ".sig").write_bytes(
                b"-----BEGIN SSH SIGNATURE-----\nU1NIU0lHZmFrZQ==\n-----END SSH SIGNATURE-----\n"
            )
            return ""
        if Path(command[0]).name.casefold() in {"ssh-keygen", "ssh-keygen.exe"} and command[
            1:3
        ] == ("-Y", "verify"):
            if self.signed_payload is not None and input_bytes != self.signed_payload:
                raise ReleaseArtifactError("signature input mismatch")
            if (
                not (cwd / "SHA256SUMS.sig")
                .read_bytes()
                .startswith(b"-----BEGIN SSH SIGNATURE-----\n")
            ):
                raise ReleaseArtifactError("invalid fake signature")
            return "Good signature"
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
$start = $source.IndexOf('function Test-SafeRelativePath')
$end = $source.IndexOf('if ($Manifest.candidate_id')
if ($start -lt 0 -or $end -lt 0) { throw 'Archive function block not found' }
. ([scriptblock]::Create('function Fail([string]$Message) { throw "[verify] $Message" }' +
    [Environment]::NewLine + $source.Substring($start, $end - $start)))
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


@pytest.fixture(autouse=True)
def protected_publish_root(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pytest's temporary tree is deliberately shared; model the pre-provisioned release root.
    monkeypatch.setattr(
        release_artifacts, "_validate_atomic_publish_root", lambda path: path.resolve()
    )


def _build(
    tmp_path: Path,
    production_env: Path,
    runner: FakeRunner,
    *,
    candidate_id: str = CANDIDATE_ID,
) -> Path:
    trust, identity = _write_fake_release_trust(tmp_path)
    return build_candidate(
        root=ROOT,
        output_root=tmp_path / "dist" / "deploy",
        candidate_id=candidate_id,
        target_platform=PLATFORM,
        env_file=production_env,
        postgres_source="timescale/timescaledb:2.16.1-pg15",
        redis_source="redis:7-alpine",
        runner=runner,
        signing_identity=identity,
        trust_directory=trust,
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
    manifest = verify_package(package, runner, trust_directory=_trust_for_package(package))
    assert tuple(image.component for image in manifest.images) == COMPONENTS
    assert len({image.archive for image in manifest.images}) == 5
    assert len({image.candidate_reference for image in manifest.images}) == 5
    assert len({image.image_id for image in manifest.images}) == 5
    assert manifest.source_commit == COMMIT
    assert manifest.alembic_head == "0012_alarm_notification_runtime"
    assert manifest.schema_version == 2
    assert manifest.authenticity["status"] == "SIGNED"
    assert manifest.authenticity["publisher"] == "ruisheng-release"
    assert manifest.authenticity["namespace"] == "ruisheng-candidate-v1"
    assert "SHA256SUMS.sig" not in {
        line.split("  ", 1)[1]
        for line in (package / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    }
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
    package = _build(tmp_path, production_env, FakeRunner())
    manifest = verify_package(package, FakeRunner(), trust_directory=_trust_for_package(package))

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


def test_signature_failure_removes_staging_tags_and_lock(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    runner.fail_signature = True

    with pytest.raises(ReleaseArtifactError, match="injected signature failure"):
        _build(tmp_path, production_env, runner)

    output_root = tmp_path / "dist" / "deploy"
    assert list(output_root.iterdir()) == []
    assert not (tmp_path / "candidate-locks" / f"{CANDIDATE_ID}.lock").exists()
    assert not any(
        reference in runner.images
        for reference in candidate_image_references(CANDIDATE_ID).values()
    )


def test_signing_rejects_private_or_mismatched_identity(tmp_path: Path) -> None:
    trust, identity = _write_fake_release_trust(tmp_path)
    anchor = release_artifacts._load_release_trust(trust)
    package = tmp_path / "package"
    package.mkdir()
    (package / "SHA256SUMS").write_bytes(b"0" * 64 + b"  payload\n")
    private_identity = tmp_path / "release-signing-identity"
    private_identity.write_text("unencrypted private material", encoding="ascii")

    with pytest.raises(ReleaseArtifactError, match="agent-backed.*public key"):
        release_artifacts._sign_sha256sums(package, private_identity, anchor, FakeRunner())

    identity.write_text("ssh-ed25519 invalid mismatched-key\n", encoding="ascii")
    with pytest.raises(ReleaseArtifactError, match="does not match the approved"):
        release_artifacts._sign_sha256sums(package, identity, anchor, FakeRunner())


def test_build_rejects_linked_agent_identity_before_docker(
    tmp_path: Path, production_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust, _identity = _write_fake_release_trust(tmp_path)
    linked_identity = tmp_path / "linked-release-identity.pub"
    original_is_symlink = Path.is_symlink

    def report_identity_link(path: Path) -> bool:
        return path == linked_identity or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", report_identity_link)
    runner = FakeRunner()

    with pytest.raises(ReleaseArtifactError, match="signing identity is missing or linked"):
        build_candidate(
            root=ROOT,
            output_root=tmp_path / "dist" / "deploy",
            candidate_id=CANDIDATE_ID,
            target_platform=PLATFORM,
            env_file=production_env,
            postgres_source="timescale/timescaledb:2.16.1-pg15",
            redis_source="redis:7-alpine",
            runner=runner,
            signing_identity=linked_identity,
            trust_directory=trust,
            lock_root=tmp_path / "candidate-locks",
        )

    assert not runner.commands


def test_lock_cleanup_failure_rolls_back_published_candidate(
    tmp_path: Path,
    production_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = FakeRunner()
    final_directory = tmp_path / "dist" / "deploy" / CANDIDATE_ID
    lock_path = tmp_path / "candidate-locks" / f"{CANDIDATE_ID}.lock"
    original_unlink = os.unlink

    def fail_final_lock_cleanup(path: str | bytes, *args: object, **kwargs: object) -> None:
        if Path(path).name == lock_path.name:
            raise OSError("injected final lock cleanup failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", fail_final_lock_cleanup)
    with pytest.raises(ReleaseArtifactError, match="candidate build lock cleanup failed"):
        _build(tmp_path, production_env, runner)

    assert not final_directory.exists()
    assert lock_path.exists()
    assert not any(
        reference in runner.images
        for reference in candidate_image_references(CANDIDATE_ID).values()
    )


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="OpenSSH is unavailable")
def test_openssh_signature_binds_exact_sums_bytes_and_external_anchor(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_blob = _fake_key_blob(private_key.public_key().public_bytes_raw())
    public_key = base64.b64encode(public_blob).decode("ascii")
    trust = tmp_path / "trust"
    trust.mkdir()
    (trust / "release-allowed-signers").write_text(
        f"ruisheng-release ssh-ed25519 {public_key}\n",
        encoding="ascii",
        newline="\n",
    )
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(public_blob).digest()).decode(
        "ascii"
    ).rstrip("=")
    (trust / "release-key-fingerprint").write_text(
        fingerprint + "\n", encoding="ascii", newline="\n"
    )
    package = tmp_path / "package"
    package.mkdir()
    sums = package / "SHA256SUMS"
    original = b"0" * 64 + b"  payload\n"
    sums.write_bytes(original)
    anchor = release_artifacts._load_release_trust(trust)
    runner = release_artifacts.SubprocessRunner()

    signature = package / "SHA256SUMS.sig"
    _write_test_sshsig(signature, original, private_key)
    release_artifacts._verify_publisher_signature(package, anchor, runner)

    sums.write_bytes(original + b"\n")
    with pytest.raises(ReleaseArtifactError, match="publisher authenticity FAILED"):
        release_artifacts._verify_publisher_signature(package, anchor, runner)

    sums.write_bytes(original)
    original_signature = signature.read_bytes()
    signature.write_bytes(original_signature + b"tampered")
    with pytest.raises(ReleaseArtifactError, match="publisher authenticity FAILED"):
        release_artifacts._verify_publisher_signature(package, anchor, runner)
    signature.write_bytes(original_signature)

    encoded = b"".join(original_signature.splitlines()[1:-1])
    rewrapped = b"\n".join(encoded[index : index + 64] for index in range(0, len(encoded), 64))
    signature.write_bytes(
        b"-----BEGIN SSH SIGNATURE-----\n" + rewrapped + b"\n-----END SSH SIGNATURE-----\n"
    )
    with pytest.raises(ReleaseArtifactError, match="SSH signature armor is not canonical"):
        release_artifacts._verify_publisher_signature(package, anchor, runner)
    signature.write_bytes(original_signature)

    replacement = Ed25519PrivateKey.generate()
    replacement_blob = _fake_key_blob(replacement.public_key().public_bytes_raw())
    replacement_key = base64.b64encode(replacement_blob).decode("ascii")
    (trust / "release-allowed-signers").write_text(
        f"ruisheng-release ssh-ed25519 {replacement_key}\n",
        encoding="ascii",
        newline="\n",
    )
    replacement_fingerprint = "SHA256:" + base64.b64encode(
        hashlib.sha256(replacement_blob).digest()
    ).decode("ascii").rstrip("=")
    (trust / "release-key-fingerprint").write_text(
        replacement_fingerprint + "\n", encoding="ascii", newline="\n"
    )
    replacement_anchor = release_artifacts._load_release_trust(trust)
    with pytest.raises(ReleaseArtifactError, match="publisher authenticity FAILED"):
        release_artifacts._verify_publisher_signature(package, replacement_anchor, runner)


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
    runner.commands.clear()
    mutation(package)  # type: ignore[operator]

    with pytest.raises(ReleaseArtifactError, match=error):
        verify_package(package, runner, trust_directory=_trust_for_package(package))

    assert not any(command[0] == "docker" for command, _env in runner.commands)


def test_sign_and_verify_use_only_fixed_system_ssh_keygen(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)
    verify_package(package, runner, trust_directory=_trust_for_package(package))

    ssh_commands = [
        command
        for command, _env in runner.commands
        if len(command) >= 3
        and command[1:3] == ("-Y", "sign")
        or len(command) >= 3
        and command[1:3] == ("-Y", "verify")
    ]
    assert ssh_commands
    assert {command[0] for command in ssh_commands} == {str(release_artifacts._system_ssh_keygen())}
    sign_command = next(command for command in ssh_commands if command[1:3] == ("-Y", "sign"))
    assert "-U" in sign_command
    identity_argument = Path(sign_command[sign_command.index("-f") + 1])
    assert identity_argument.name == ".release-signing-identity.pub"
    assert not identity_argument.exists()
    assert not (package / identity_argument.name).exists()


def test_builder_requires_a_protected_atomic_publish_root() -> None:
    implementation = (ROOT / "tools" / "release_artifacts.py").read_text(encoding="utf-8")

    assert "def _validate_atomic_publish_root(output_root: Path) -> Path:" in implementation
    assert "WINDOWS_PUBLISH_ROOT_VALIDATOR" in implementation
    assert "publish root permits replacement by an unapproved identity" in implementation
    assert "output_root = _validate_atomic_publish_root(output_root)" in implementation
    assert "parent=output_root" in implementation


def test_verify_and_load_use_complete_snapshot_for_all_docker_calls(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)
    runner.commands.clear()

    load_and_verify_images(package, runner, trust_directory=_trust_for_package(package))

    docker_commands = [command for command, _env in runner.commands if command[0] == "docker"]
    assert docker_commands
    assert all(str(package) not in argument for command in docker_commands for argument in command)
    loaded_paths = [
        Path(command[-1])
        for command in docker_commands
        if command[:3] == ("docker", "image", "load")
    ]
    assert len(loaded_paths) == len(COMPONENTS)
    assert len({path.parents[1] for path in loaded_paths}) == 1


def test_windows_cli_verify_fails_closed_before_trust_or_docker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(release_artifacts.os, "name", "nt")

    result = release_artifacts.main(["verify", "candidate", "--load"])

    assert result == 1
    assert r"C:\ProgramData\Ruisheng\bin\verify-publisher.ps1" in capsys.readouterr().err


def test_manifest_is_hashed_before_untrusted_json_is_parsed(
    tmp_path: Path, production_env: Path
) -> None:
    build_runner = FakeRunner()
    package = _build(tmp_path, production_env, build_runner)

    class ManifestSwapRunner(FakeRunner):
        def run(self, args: Sequence[str], **kwargs: object) -> str:
            result = super().run(args, **kwargs)  # type: ignore[arg-type]
            command = tuple(str(argument) for argument in args)
            if Path(command[0]).name.casefold() in {"ssh-keygen", "ssh-keygen.exe"} and command[
                1:3
            ] == ("-Y", "verify"):
                (Path(kwargs["cwd"]) / "MANIFEST.json").write_text(
                    "{not-json", encoding="utf-8", newline="\n"
                )
            return result

    runner = ManifestSwapRunner()
    runner.signed_payload = (package / "SHA256SUMS").read_bytes()

    with pytest.raises(
        ReleaseArtifactError,
        match="publisher authenticity FAILED: SHA-256 mismatch for MANIFEST.json",
    ):
        verify_package(package, runner, trust_directory=_trust_for_package(package))

    assert not any(command[0] == "docker" for command, _env in runner.commands)


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
    with (package / "SHA256SUMS").open("a", encoding="utf-8", newline="\n") as sums:
        sums.write(bad_line + "\n")
    runner.signed_payload = (package / "SHA256SUMS").read_bytes()

    with pytest.raises(ReleaseArtifactError, match="unsafe package path"):
        verify_package(package, runner, trust_directory=_trust_for_package(package))


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
        verify_package(package, verify_runner, trust_directory=_trust_for_package(package))


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
        verify_package(package, verify_runner, trust_directory=_trust_for_package(package))


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
        newline="\n",
    )
    runner.signed_payload = sums_path.read_bytes()

    with pytest.raises(ReleaseArtifactError, match=error):
        verify_package(package, runner, trust_directory=_trust_for_package(package))


def test_load_verification_rejects_loaded_image_identity_drift(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)
    verify_package(package, runner, trust_directory=_trust_for_package(package))
    api_reference = candidate_image_references(CANDIDATE_ID)["api"]
    runner.images[api_reference] = {
        **runner.images[api_reference],
        "Id": "sha256:" + "f" * 64,
    }

    with pytest.raises(ReleaseArtifactError, match="loaded image identity mismatch for api"):
        load_and_verify_images(
            package,
            runner,
            trust_directory=_trust_for_package(package),
        )

    assert len(runner.loaded) == 5


def test_archive_change_before_load_is_rejected_before_any_docker_call(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)
    verify_package(package, runner, trust_directory=_trust_for_package(package))
    runner.commands.clear()
    runner.loaded.clear()
    (package / "images" / "web.tar.gz").write_bytes(b"changed after verification")

    with pytest.raises(ReleaseArtifactError, match="SHA-256 mismatch for images/web.tar.gz"):
        load_and_verify_images(
            package,
            runner,
            trust_directory=_trust_for_package(package),
        )

    assert runner.loaded == []
    assert not any(command[0] == "docker" for command, _env in runner.commands)


def test_generated_manifest_declares_signed_external_trust_contract(
    tmp_path: Path, production_env: Path
) -> None:
    package = _build(tmp_path, production_env, FakeRunner())
    manifest_value = json.loads((package / "MANIFEST.json").read_text(encoding="utf-8"))
    markdown = (package / "MANIFEST.md").read_text(encoding="utf-8")

    assert manifest_value["schema_version"] == 2
    assert manifest_value["authenticity"]["status"] == "SIGNED"
    assert manifest_value["authenticity"]["signed_object"] == "SHA256SUMS"
    assert manifest_value["authenticity"]["signature_file"] == "SHA256SUMS.sig"
    assert "external trust anchor" in markdown


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


def test_verify_cli_does_not_accept_a_caller_selected_trust_path() -> None:
    parser = release_artifacts._build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["verify", "candidate", "--trust-directory", "attacker-selected-trust"])
