"""Offline release artifact generation and verification contracts."""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO, cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tools import qualification_bootstrap, release_artifacts
from tools.release_artifacts import (
    COMPONENTS,
    FIXED_PACKAGE_FILES,
    FIXED_PACKAGE_FILES_V2,
    QUALIFICATION_TOOLCHAIN_ARCHIVE,
    QUALIFICATION_TOOLCHAIN_FORMAT,
    QUALIFICATION_TOOLCHAIN_MANIFEST,
    QUALIFICATION_TOOLCHAIN_MEMBERS,
    SEMANTIC_VALIDATOR_ID,
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
        self.image_id_inspect_overrides: dict[str, str] = {}
        self.final_commit = COMMIT
        self.git_head_calls = 0
        self.git_blob_override: str | None = None
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

    def run(  # noqa: PLR0911, PLR0912, PLR0915
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        input_bytes: bytes | None = None,
        timeout_seconds: float | None = None,
        inherit_environment: bool = True,
    ) -> str:
        del timeout_seconds, inherit_environment
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
        if command == (
            "git",
            "rev-parse",
            "--verify",
            "--end-of-options",
            "HEAD^{commit}",
        ):
            self.git_head_calls += 1
            return COMMIT if self.git_head_calls == 1 else self.final_commit
        if (
            command[:4] == ("git", "rev-parse", "--verify", "--end-of-options")
            and ":" in command[4]
        ):
            commit, relative = command[4].split(":", maxsplit=1)
            assert commit == COMMIT
            if self.git_blob_override is not None:
                return self.git_blob_override
            source = ROOT / relative
            contents = source.read_bytes()
            return hashlib.sha1(  # noqa: S324 - Git's object format uses SHA-1.
                b"blob " + str(len(contents)).encode() + b"\0" + contents,
                usedforsecurity=False,
            ).hexdigest()
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
            requested_reference = command[3]
            reference = requested_reference
            if requested_reference.startswith("sha256:"):
                matches = [
                    name
                    for name, config in self.configs.items()
                    if "sha256:" + hashlib.sha256(config).hexdigest() == requested_reference
                ]
                if not matches:
                    raise ReleaseArtifactError(
                        f"fake Docker daemon has no image object: {requested_reference}"
                    )
                reference = next(
                    (name for name in matches if name.startswith("ruisheng-candidate/")),
                    matches[0],
                )
                value = {
                    **self.images[reference],
                    "Id": self.image_id_inspect_overrides.get(
                        requested_reference, requested_reference
                    ),
                }
            else:
                value = self.images[reference]
            return json.dumps(value, sort_keys=True)
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


class QualificationRunner(FakeRunner):
    def __init__(self) -> None:
        super().__init__()
        self.qualification_commands: list[tuple[tuple[str, ...], Path]] = []
        self.forced_qualification_outcome: release_artifacts.CommandOutcome | None = None

    def run_outcome(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        input_bytes: bytes | None = None,
        timeout_seconds: float | None = None,
        inherit_environment: bool = True,
        isolate_process_tree: bool = False,
    ) -> release_artifacts.CommandOutcome:
        command = tuple(str(arg) for arg in args)
        assert len(command) >= 14
        assert Path(command[0]).resolve() == Path(sys.executable).resolve()
        assert command[1:7] == ("-I", "-B", "-S", "-X", "utf8", "-c")
        assert input_bytes is None
        assert not inherit_environment
        assert isolate_process_tree
        self.commands.append((command, dict(env or {})))
        self.qualification_commands.append((command, cwd))
        if self.forced_qualification_outcome is not None:
            return self.forced_qualification_outcome
        return release_artifacts.SubprocessRunner._run_isolated_outcome(
            command,
            cwd=cwd,
            env={} if env is None else dict(env),
            input_bytes=input_bytes,
            timeout_seconds=timeout_seconds or 30,
        )


def _add_tar_bytes(archive: tarfile.TarFile, name: str, contents: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(contents)
    member.mtime = 0
    archive.addfile(member, io.BytesIO(contents))


def _write_canonical_qualification_archive(path: Path, contents: Mapping[str, bytes]) -> None:
    with (
        path.open("wb") as raw_archive,
        gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=9,
            fileobj=raw_archive,
            mtime=0,
        ) as compressed,
        tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive,
    ):
        for name in (*QUALIFICATION_TOOLCHAIN_MEMBERS, QUALIFICATION_TOOLCHAIN_MANIFEST):
            _add_tar_bytes(archive, name, contents[name])


def _canonical_gzip_bytes(contents: bytes) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(
        filename="",
        mode="wb",
        compresslevel=9,
        fileobj=output,
        mtime=0,
    ) as compressed:
        compressed.write(contents)
    return output.getvalue()


def _oversized_tar_extension_header(extension_type: bytes) -> bytes:
    member = tarfile.TarInfo("extension-metadata")
    member.type = extension_type
    member.size = release_artifacts.MAX_DOCKER_ARCHIVE_MEMBER_BYTES + 1
    member.mtime = 0
    member.uid = 0
    member.gid = 0
    member.uname = ""
    member.gname = ""
    member.mode = 0o644
    return member.tobuf(format=tarfile.GNU_FORMAT)


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
$end = $source.IndexOf('Assert-ManifestValueTypes $Manifest')
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


def _run_powershell_logical_identity(
    manifest_path: Path,
    *,
    script_relative: str = "deploy/verify-candidate.ps1",
    start_marker: str = "function Get-Sha256Bytes",
    end_marker: str = "Assert-ManifestValueTypes $Manifest",
) -> subprocess.CompletedProcess[str]:
    command = r"""
$source = Get-Content -Raw -LiteralPath $env:RS_VERIFY_SCRIPT
$start = $source.IndexOf($env:RS_START_MARKER)
$end = $source.IndexOf($env:RS_END_MARKER, $start)
if ($start -lt 0 -or $end -lt 0) { throw 'Manifest identity function block not found' }
. ([scriptblock]::Create('function Fail([string]$Message) { throw "[verify] $Message" }' +
    [Environment]::NewLine + $source.Substring($start, $end - $start)))
$manifest = Get-Content -Raw -LiteralPath $env:RS_MANIFEST_PATH | ConvertFrom-Json
Get-ManifestLogicalIdentity $manifest ([int]$manifest.schema_version)
"""
    environment = os.environ.copy()
    environment.update(
        {
            "RS_VERIFY_SCRIPT": str(ROOT / script_relative),
            "RS_MANIFEST_PATH": str(manifest_path),
            "RS_START_MARKER": start_marker,
            "RS_END_MARKER": end_marker,
        }
    )
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=30,
    )


def _run_powershell_publisher_snapshot_mutation(
    tmp_path: Path, mutation: str
) -> subprocess.CompletedProcess[str]:
    command = r"""
$source = Get-Content -Raw -LiteralPath $env:RS_VERIFY_SCRIPT
$startMarker = '# BEGIN candidate snapshot identity helpers'
$endMarker = '# END candidate snapshot identity helpers'
$start = $source.IndexOf($startMarker)
$end = $source.IndexOf($endMarker, $start)
if ($start -lt 0 -or $end -lt 0) { throw 'Snapshot identity helper block not found' }
$end += $endMarker.Length
$fail = 'function Fail([string]$Message) { throw ("[publisher] " + $Message) }' +
    [Environment]::NewLine
. ([scriptblock]::Create($fail + $source.Substring($start, $end - $start)))
[byte[]]$original = [Text.Encoding]::ASCII.GetBytes('original')
[IO.File]::WriteAllBytes($env:RS_SOURCE_PATH, $original)
$identity = Get-CandidateSourceIdentity $env:RS_SOURCE_PATH 'sample.bin'
switch ($env:RS_MUTATION) {
    'rewrite' {
        [IO.File]::WriteAllBytes(
            $env:RS_SOURCE_PATH, [Text.Encoding]::ASCII.GetBytes('rewritte')
        )
        [IO.File]::SetLastWriteTimeUtc(
            $env:RS_SOURCE_PATH, [DateTime]::FromFileTimeUtc($identity.LastWriteTime)
        )
    }
    'replace' {
        Move-Item -LiteralPath $env:RS_SOURCE_PATH -Destination $env:RS_MOVED_PATH
        [IO.File]::WriteAllBytes(
            $env:RS_SOURCE_PATH, [Text.Encoding]::ASCII.GetBytes('attacker')
        )
        [IO.File]::SetCreationTimeUtc(
            $env:RS_SOURCE_PATH, [DateTime]::FromFileTimeUtc($identity.CreationTime)
        )
        [IO.File]::SetLastWriteTimeUtc(
            $env:RS_SOURCE_PATH, [DateTime]::FromFileTimeUtc($identity.LastWriteTime)
        )
    }
    default { throw 'unsupported test mutation' }
}
Copy-CandidateFileToSnapshot `
    $env:RS_SOURCE_PATH $env:RS_DESTINATION_PATH 'sample.bin' $identity
"""
    environment = os.environ.copy()
    environment.update(
        {
            "RS_VERIFY_SCRIPT": str(ROOT / "tools" / "release_trust" / "verify-publisher.ps1"),
            "RS_SOURCE_PATH": str(tmp_path / "source.bin"),
            "RS_MOVED_PATH": str(tmp_path / "moved.bin"),
            "RS_DESTINATION_PATH": str(tmp_path / "snapshot.bin"),
            "RS_MUTATION": mutation,
        }
    )
    return subprocess.run(
        [shutil.which("pwsh") or "pwsh", "-NoProfile", "-Command", command],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=30,
    )


def _run_powershell_qualification_invocation(
    tmp_path: Path, mode: str
) -> subprocess.CompletedProcess[str]:
    command = r"""
$source = Get-Content -Raw -LiteralPath $env:RS_VERIFY_SCRIPT
$start = $source.IndexOf('function Get-QualificationInvocation')
$end = $source.IndexOf('function Invoke-AuthenticatedQualification', $start)
if ($start -lt 0 -or $end -lt 0) { throw 'Qualification invocation block not found' }
$fail = 'function Fail([string]$Message) { throw ("[publisher] " + $Message) }' +
    [Environment]::NewLine
. ([scriptblock]::Create($fail + $source.Substring($start, $end - $start)))
$QualificationProfilePath = $env:RS_PROFILE_PATH
$QualificationEvidencePath = $env:RS_EVIDENCE_PATH
$QualificationRootPath = $env:RS_ROOT_PATH
$QualificationTrustPolicyPath = $env:RS_POLICY_PATH
$QualificationOutputDirectory = $env:RS_OUTPUT_PATH
$QualificationSigningIdentity = $env:RS_SIGNING_IDENTITY
$QualificationVerifierId = 'protected-release-verifier'
$QualificationVerifierKeyId = 'release-receipt-key'
$FreshnessVerifierId = 'ruisheng.protected-release-publisher.windows.v1'
$manifest = [pscustomobject]@{
    logical_identity = ('sha256:' + ('b' * 64))
    qualification_toolchain = [pscustomobject]@{
        receipt_producer = [pscustomobject]@{ sha256 = ('a' * 64) }
    }
}
$freshness = $null
if ($env:RS_MODE -ceq 'ValidatorProfile') {
    $freshness = [pscustomobject]@{
        TrustRootSnapshot = [pscustomobject]@{
            Path = $env:RS_TRUST_ROOT_SNAPSHOT
            ExpectedSha256 = ('c' * 64)
        }
        ProfileSnapshot = [pscustomobject]@{ Path = $env:RS_PROFILE_SNAPSHOT }
        PolicySnapshot = [pscustomobject]@{ Path = $env:RS_POLICY_SNAPSHOT }
        ConfigSnapshot = [pscustomobject]@{
            Path = $env:RS_CONFIG_SNAPSHOT
            ExpectedSha256 = ('f' * 64)
        }
        Attestation = [pscustomobject]@{
            Path = $env:RS_ATTESTATION_SNAPSHOT
            ExpectedSha256 = ('9' * 64)
        }
        Challenge = ('d' * 43)
        RequestedAt = '2026-08-30T00:00:00+00:00'
        Verifier = [pscustomobject]@{ ExpectedSha256 = ('e' * 64) }
    }
}
Get-QualificationInvocation $env:RS_MODE $manifest $env:RS_PACKAGE_PATH $freshness |
    ConvertTo-Json -Compress
"""
    environment = os.environ.copy()
    environment.update(
        {
            "RS_VERIFY_SCRIPT": str(ROOT / "tools" / "release_trust" / "verify-publisher.ps1"),
            "RS_MODE": mode,
            "RS_PROFILE_PATH": str(tmp_path / "profile.json"),
            "RS_EVIDENCE_PATH": str(tmp_path / "legacy.json"),
            "RS_ROOT_PATH": str(tmp_path / "evidence"),
            "RS_POLICY_PATH": str(tmp_path / "policy.json"),
            "RS_OUTPUT_PATH": str(tmp_path / "receipts"),
            "RS_SIGNING_IDENTITY": str(tmp_path / "release-receipt.pub"),
            "RS_PACKAGE_PATH": str(tmp_path / "candidate"),
            "RS_TRUST_ROOT_SNAPSHOT": str(tmp_path / "freshness" / "trust-root.json"),
            "RS_PROFILE_SNAPSHOT": str(tmp_path / "freshness" / "profile.json"),
            "RS_POLICY_SNAPSHOT": str(tmp_path / "freshness" / "trust-policy.json"),
            "RS_CONFIG_SNAPSHOT": str(tmp_path / "freshness" / "provider-config.json"),
            "RS_ATTESTATION_SNAPSHOT": str(tmp_path / "freshness" / "attestation.json"),
        }
    )
    return subprocess.run(
        [shutil.which("pwsh") or "pwsh", "-NoProfile", "-Command", command],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=30,
    )


def _run_powershell_freshness_context_failure(
    tmp_path: Path, failure: str
) -> subprocess.CompletedProcess[str]:
    provider = tmp_path / "provider.ps1"
    config = tmp_path / "provider-config.json"
    trust_root = tmp_path / "trust-root.json"
    config.write_text("{}", encoding="ascii")
    trust_root.write_text("{}", encoding="ascii")
    if failure == "untrusted":
        provider.write_text("exit 0", encoding="ascii")
    command = r"""
$source = Get-Content -Raw -LiteralPath $env:RS_VERIFY_SCRIPT
$start = $source.IndexOf('function New-PublisherFreshnessContext')
$end = $source.IndexOf('function Invoke-AuthenticatedQualification', $start)
if ($start -lt 0 -or $end -lt 0) { throw 'Freshness context function not found' }
function Fail([string]$Message) { throw $Message }
function Set-ProtectedSnapshotAcl([string]$Path) {}
function Assert-ProtectedAcl(
    [string]$Path, [string]$Label, [switch]$AllowTrustedInstaller
) {}
function Assert-ProtectedAncestors(
    [string]$Path, [string]$Label, [switch]$AllowTrustedInstaller
) {}
function Open-LockedFreshnessFile(
    [string]$Path, [string]$Label, [long]$MaximumBytes, [switch]$RequireProtected
) {
    if ($Label -ceq 'fixed freshness provider') { throw 'provider is untrusted' }
    throw 'unexpected freshness open'
}
. ([scriptblock]::Create($source.Substring($start, $end - $start)))
$FreshnessProviderPath = $env:RS_PROVIDER
$FreshnessProviderConfigPath = $env:RS_CONFIG
$FreshnessTrustRootPath = $env:RS_TRUST_ROOT
$MaxFreshnessProviderBytes = 1MB
$MaxReleaseJsonBytes = 4MB
$result = New-PublisherFreshnessContext `
    ([pscustomobject]@{ logical_identity = ('sha256:' + ('a' * 64)) }) `
    $env:RS_FRESHNESS_ROOT
[Console]::Out.Write([string]$result.ExitCode)
"""
    environment = os.environ.copy()
    environment.update(
        {
            "RS_VERIFY_SCRIPT": str(ROOT / "tools" / "release_trust" / "verify-publisher.ps1"),
            "RS_PROVIDER": str(provider),
            "RS_CONFIG": str(config),
            "RS_TRUST_ROOT": str(trust_root),
            "RS_FRESHNESS_ROOT": str(tmp_path / "freshness"),
        }
    )
    return subprocess.run(
        [shutil.which("pwsh") or "pwsh", "-NoProfile", "-Command", command],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=30,
    )


def _run_powershell_freshness_dispatch(
    tmp_path: Path, *, context_exit: int, preflight_exit: int
) -> subprocess.CompletedProcess[str]:
    command = r"""
$source = Get-Content -Raw -LiteralPath $env:RS_VERIFY_SCRIPT
$start = $source.IndexOf('    if ($QualificationMode -eq "ValidatorProfile") {')
$end = $source.IndexOf('$CandidateVerifier = Join-Path', $start)
if ($start -lt 0 -or $end -lt 0) { throw 'ValidatorProfile dispatch block not found' }
$block = $source.Substring($start, $end - $start)
$lastClose = $block.LastIndexOf('}')
if ($lastClose -lt 0) { throw 'ValidatorProfile dispatch close not found' }
$block = $block.Remove($lastClose, 1)
function New-PublisherFreshnessContext([object]$Manifest, [string]$Root) {
    return [pscustomobject]@{
        ExitCode = [int]$env:RS_CONTEXT_EXIT
        Context = [pscustomobject]@{ Locks = @() }
    }
}
function Get-FreshnessPreflightInvocation([object]$Manifest, [object]$Context) {
    return [pscustomobject]@{ Entrypoint = 'preflight'; Arguments = @() }
}
function Get-QualificationInvocation {
    Add-Content -LiteralPath $env:RS_MARKER -Value 'qualification'
    return [pscustomobject]@{ Entrypoint = 'qualify'; Arguments = @() }
}
function Invoke-AuthenticatedQualification {
    Add-Content -LiteralPath $env:RS_MARKER -Value 'process'
    $decision = switch ([int]$env:RS_PREFLIGHT_EXIT) {
        0 { 'EXACT' }
        2 { 'BLOCKED' }
        3 { 'INVALID' }
    }
    return [pscustomobject]@{
        ExitCode = [int]$env:RS_PREFLIGHT_EXIT
        StandardOutput = ('{"decision":"' + $decision + '","reason_code":"TEST"}')
        StandardError = ''
    }
}
function Assert-FreshnessLocksUnchanged([object]$Context) {}
$QualificationMode = 'ValidatorProfile'
$QualificationExtractionRoot = $env:RS_EXTRACTION
$Manifest = [pscustomobject]@{}
$QualificationContents = @{}
$PackageRoot = $env:RS_PACKAGE
$FreshnessContext = $null
. ([scriptblock]::Create($block))
"""
    marker = tmp_path / "qualification-calls.txt"
    environment = os.environ.copy()
    environment.update(
        {
            "RS_VERIFY_SCRIPT": str(ROOT / "tools" / "release_trust" / "verify-publisher.ps1"),
            "RS_CONTEXT_EXIT": str(context_exit),
            "RS_PREFLIGHT_EXIT": str(preflight_exit),
            "RS_MARKER": str(marker),
            "RS_EXTRACTION": str(tmp_path / "extraction"),
            "RS_PACKAGE": str(tmp_path / "package"),
        }
    )
    return subprocess.run(
        [shutil.which("pwsh") or "pwsh", "-NoProfile", "-Command", command],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=30,
    )


def _write_minimal_qualification_runtime(
    tmp_path: Path,
) -> tuple[Path, dict[str, object], str]:
    runtime = tmp_path / "qualification-runtime"
    files = {
        "Lib/encodings/__init__.py": b"# isolated encodings package\n",
        "Lib/site-packages/dependency.py": b"VALUE = 'authenticated'\n",
        "python.exe": b"MZ-fake-python-3.11\n",
        "python311.dll": b"MZ-fake-python311-dll\n",
    }
    for relative, content in files.items():
        path = runtime / Path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    uv_lock_sha256 = "a" * 64
    manifest: dict[str, object] = {
        "artifact_type": "ruisheng.qualification-runtime",
        "schema_version": 1,
        "python_version": "3.11",
        "uv_lock_sha256": uv_lock_sha256,
        "dependency_root": "Lib/site-packages",
        "files": [
            {
                "path": relative,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for relative, content in sorted(files.items())
        ],
    }
    _write_qualification_runtime_manifest(runtime, manifest)
    return runtime, manifest, uv_lock_sha256


def _write_qualification_runtime_manifest(runtime: Path, manifest: Mapping[str, object]) -> None:
    (runtime / "qualification-runtime-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=True, separators=(",", ":")),
        encoding="utf-8",
        newline="\n",
    )


def _add_qualification_runtime_file(
    runtime: Path,
    manifest: dict[str, object],
    relative: str,
    content: bytes,
) -> None:
    path = runtime / Path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    identities = cast(list[dict[str, str]], manifest["files"])
    identities.append({"path": relative, "sha256": hashlib.sha256(content).hexdigest()})
    identities.sort(key=lambda item: item["path"])
    _write_qualification_runtime_manifest(runtime, manifest)


def _run_powershell_qualification_runtime(
    runtime: Path,
    uv_lock_sha256: str,
    *,
    action: str = "verify",
    lock_target: str = "python.exe",
    fail_guard: str = "",
) -> subprocess.CompletedProcess[str]:
    command = r"""
$ErrorActionPreference = 'Stop'
$source = Get-Content -Raw -LiteralPath $env:RS_VERIFY_SCRIPT
$identityStart = $source.IndexOf('# BEGIN candidate snapshot identity helpers')
$identityEndMarker = '# END candidate snapshot identity helpers'
$identityEnd = $source.IndexOf($identityEndMarker, $identityStart)
$exactStart = $source.IndexOf('function Assert-ExactProperties')
$exactEnd = $source.IndexOf('function ConvertTo-PythonCanonicalJson', $exactStart)
    $runtimeStart = $source.IndexOf('$MaxQualificationRuntimeFiles = [Int64]32768')
$runtimeEnd = $source.IndexOf('function Get-QualificationInvocation', $runtimeStart)
if ($identityStart -lt 0 -or $identityEnd -lt 0 -or $exactStart -lt 0 -or
    $exactEnd -lt 0 -or $runtimeStart -lt 0 -or $runtimeEnd -lt 0) {
    throw 'Qualification runtime helper block not found'
}
$identityEnd += $identityEndMarker.Length
function Fail([string]$Message) { throw ("[publisher] " + $Message) }
function Assert-ProtectedAcl(
    [string]$Path, [string]$Label, [switch]$AllowTrustedInstaller
) {
    if ($env:RS_FAIL_GUARD -eq 'acl') { Fail "unsafe ACL: $Label" }
}
function Assert-ProtectedAncestors(
    [string]$Path, [string]$Label, [switch]$AllowTrustedInstaller
) {
    if ($env:RS_FAIL_GUARD -eq 'ancestor') { Fail "unsafe ancestor: $Label" }
}
. ([scriptblock]::Create($source.Substring(
    $identityStart, $identityEnd - $identityStart
)))
. ([scriptblock]::Create($source.Substring($exactStart, $exactEnd - $exactStart)))
$runtimeBlock = $source.Substring($runtimeStart, $runtimeEnd - $runtimeStart)
$runtimeBlock = $runtimeBlock.Replace(
    '"C:\ProgramData\Ruisheng\runtime"', '$env:RS_RUNTIME_ROOT'
)
. ([scriptblock]::Create($runtimeBlock))

$runtime = $null
$lockPath = $null
$writerWasBlocked = $false
try {
    $runtime = Open-ProtectedSystemPython $env:RS_UV_LOCK_SHA256
    Assert-ProtectedQualificationRuntimeUnchanged $runtime
    if ($env:RS_ACTION -eq 'lock') {
        $target = @($runtime.Locks | Where-Object {
            $_.Relative -ceq $env:RS_LOCK_TARGET
        })[0]
        if ($null -eq $target) { throw 'qualification runtime lock target is missing' }
        $lockPath = $target.Path
        try {
            $writer = [IO.File]::Open(
                $target.Path, [IO.FileMode]::Open, [IO.FileAccess]::Write,
                [IO.FileShare]::None
            )
            $writer.Dispose()
            throw 'qualification runtime lock did not block a writer'
        } catch {
            if ($_.Exception.Message -like '*lock did not block*') { throw }
            $writerWasBlocked = $true
        }
    }
} finally {
    if ($null -ne $runtime) {
        foreach ($lock in $runtime.Locks) {
            if ($null -ne $lock.Stream) { $lock.Stream.Dispose() }
        }
    }
}
if ($env:RS_ACTION -eq 'lock') {
    if (-not $writerWasBlocked) { throw 'qualification runtime writer was not blocked' }
    $writer = [IO.File]::Open(
        $lockPath, [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::None
    )
    $writer.Dispose()
    [Console]::Out.Write('LOCKED')
} else {
    [Console]::Out.Write('VERIFIED')
}
"""
    environment = os.environ.copy()
    environment.update(
        {
            "RS_VERIFY_SCRIPT": str(ROOT / "tools" / "release_trust" / "verify-publisher.ps1"),
            "RS_RUNTIME_ROOT": str(runtime),
            "RS_UV_LOCK_SHA256": uv_lock_sha256,
            "RS_ACTION": action,
            "RS_LOCK_TARGET": lock_target,
            "RS_FAIL_GUARD": fail_guard,
        }
    )
    return subprocess.run(
        [shutil.which("pwsh") or "pwsh", "-NoProfile", "-Command", command],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=30,
    )


def _run_powershell_qualification_runtime_budget(action: str) -> subprocess.CompletedProcess[str]:
    command = r"""
$ErrorActionPreference = 'Stop'
$source = Get-Content -Raw -LiteralPath $env:RS_VERIFY_SCRIPT
$runtimeStart = $source.IndexOf('$MaxQualificationRuntimeFiles = [Int64]32768')
$runtimeEnd = $source.IndexOf('function Get-QualificationInvocation', $runtimeStart)
if ($runtimeStart -lt 0 -or $runtimeEnd -lt 0) {
    throw 'Qualification runtime budget block not found'
}
function Fail([string]$Message) { throw ("[publisher] " + $Message) }
. ([scriptblock]::Create($source.Substring(
    $runtimeStart, $runtimeEnd - $runtimeStart
)))

switch ($env:RS_ACTION) {
    'file-count-boundary' {
        # 32,767 listed members plus the runtime manifest is 32,768 actual files.
        $files = [object[]]::new(32767)
        Assert-QualificationRuntimeManifestFileCount $files
    }
    'file-count' {
        # The runtime manifest itself counts toward the 32,768-file ceiling.
        $files = [object[]]::new(32768)
        Assert-QualificationRuntimeManifestFileCount $files
    }
    'single-file' {
        [void](Add-QualificationRuntimeFileBytes `
            0 536870913 'qualification runtime synthetic file')
    }
    'aggregate' {
        [void](Add-QualificationRuntimeFileBytes `
            34359738367 2 'qualification runtime synthetic file')
    }
    'int64-overflow' {
        [void](Add-QualificationRuntimeFileBytes `
            ([Int64]::MaxValue) 1 'qualification runtime synthetic file')
    }
    'directory' {
        $MaxQualificationRuntimeDirectories = [Int64]2
        $directories = [Collections.Generic.HashSet[string]]::new(
            [StringComparer]::Ordinal
        )
        $members = [Collections.Generic.Dictionary[string, object]]::new(
            [StringComparer]::OrdinalIgnoreCase
        )
        foreach ($relative in @('one', 'two', 'three')) {
            Add-ExpectedQualificationRuntimeDirectory $directories $members $relative
        }
    }
    'path' {
        [void](Resolve-QualificationRuntimePath `
            'C:\runtime' ('a' * 4097) 'qualification runtime file path')
    }
    default { throw 'unsupported qualification runtime budget action' }
}
[Console]::Out.Write('ACCEPTED')
"""
    environment = os.environ.copy()
    environment.update(
        {
            "RS_VERIFY_SCRIPT": str(ROOT / "tools" / "release_trust" / "verify-publisher.ps1"),
            "RS_ACTION": action,
        }
    )
    return subprocess.run(
        [shutil.which("pwsh") or "pwsh", "-NoProfile", "-Command", command],
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


def _rewrite_authenticated_manifest(
    package: Path,
    runner: FakeRunner,
    value: dict[str, object],
    *,
    render_markdown: bool = True,
) -> None:
    manifest_path = package / "MANIFEST.json"
    manifest_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if render_markdown:
        parsed = release_artifacts._manifest_from_dict(value)
        (package / "MANIFEST.md").write_text(
            render_manifest_markdown(parsed), encoding="utf-8", newline="\n"
        )
    hashed = {
        path.relative_to(package).as_posix()
        for path in package.rglob("*")
        if path.is_file() and path.name not in {"SHA256SUMS", "SHA256SUMS.sig"}
    }
    release_artifacts._write_sha256sums(package, tuple(hashed))
    runner.signed_payload = (package / "SHA256SUMS").read_bytes()


def _downgrade_package_to_v2(package: Path, runner: FakeRunner) -> None:
    value = json.loads((package / "MANIFEST.json").read_text(encoding="utf-8"))
    images = release_artifacts._manifest_from_dict(value).images
    value["schema_version"] = 2
    value.pop("qualification_toolchain")
    value["logical_identity"] = compute_logical_identity(
        candidate_id=str(value["candidate_id"]),
        source_commit=str(value["source_commit"]),
        target_os=str(value["target_os"]),
        target_architecture=str(value["target_architecture"]),
        alembic_head=str(value["alembic_head"]),
        images=images,
    )
    (package / QUALIFICATION_TOOLCHAIN_ARCHIVE).unlink()
    _rewrite_authenticated_manifest(package, runner, value)


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
    assert manifest.schema_version == 3
    assert manifest.qualification_toolchain is not None
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
        qualification_toolchain=manifest.qualification_toolchain,
    )
    second = compute_logical_identity(
        candidate_id=manifest.candidate_id,
        source_commit=manifest.source_commit,
        target_os=manifest.target_os,
        target_architecture=manifest.target_architecture,
        alembic_head=manifest.alembic_head,
        images=manifest.images,
        qualification_toolchain=manifest.qualification_toolchain,
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
    lock_root = tmp_path / "candidate-locks"
    lock_root.mkdir(parents=True)

    with (
        release_artifacts.candidate_tag_operation_lock(lock_root, CANDIDATE_ID),
        pytest.raises(ReleaseArtifactError, match="candidate tag operation is already active"),
    ):
        _build(tmp_path, production_env, runner)

    assert (lock_root / release_artifacts.candidate_tag_lock_name(CANDIDATE_ID)).is_file()
    assert not any(
        reference in runner.images
        for reference in candidate_image_references(CANDIDATE_ID).values()
    )


def test_build_uses_host_global_candidate_tag_lock_by_default(
    tmp_path: Path, production_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner()
    trust, identity = _write_fake_release_trust(tmp_path)
    lock_root = tmp_path / "host-global-locks"
    lock_root.mkdir()
    observed: list[Path] = []

    def system_lock_root() -> Path:
        observed.append(lock_root)
        return lock_root

    monkeypatch.setattr(release_artifacts, "system_candidate_tag_lock_root", system_lock_root)
    package = build_candidate(
        root=ROOT,
        output_root=tmp_path / "dist" / "deploy",
        candidate_id=CANDIDATE_ID,
        target_platform=PLATFORM,
        env_file=production_env,
        postgres_source="timescale/timescaledb:2.16.1-pg15",
        redis_source="redis:7-alpine",
        runner=runner,
        signing_identity=identity,
        trust_directory=trust,
    )

    assert package.is_dir()
    assert observed == [lock_root]
    assert (lock_root / release_artifacts.candidate_tag_lock_name(CANDIDATE_ID)).is_file()


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


def test_signature_failure_removes_staging_tags_and_releases_lock(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    runner.fail_signature = True

    with pytest.raises(ReleaseArtifactError, match="injected signature failure"):
        _build(tmp_path, production_env, runner)

    output_root = tmp_path / "dist" / "deploy"
    assert list(output_root.iterdir()) == []
    lock_root = tmp_path / "candidate-locks"
    lock_path = lock_root / release_artifacts.candidate_tag_lock_name(CANDIDATE_ID)
    assert lock_path.is_file()
    with release_artifacts.candidate_tag_operation_lock(lock_root, CANDIDATE_ID):
        pass
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


def test_candidate_tag_locks_do_not_conflict_for_distinct_candidates(
    tmp_path: Path,
) -> None:
    with (
        release_artifacts.candidate_tag_operation_lock(tmp_path, CANDIDATE_ID),
        release_artifacts.candidate_tag_operation_lock(tmp_path, "deploy-20260827.2"),
    ):
        pass


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


def test_subprocess_runner_pins_local_docker_and_rejects_caller_environment_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docker = tmp_path / "docker.exe"
    docker.write_bytes(b"fixed docker")
    captured: dict[str, object] = {}

    def fake_run(command: Sequence[str], **kwargs: object) -> SimpleNamespace:
        captured["command"] = list(command)
        captured["env"] = dict(cast(Mapping[str, str], kwargs["env"]))
        return SimpleNamespace(stdout=b"ok\n", stderr=b"", returncode=0)

    monkeypatch.setattr(release_artifacts, "_system_docker", lambda: docker)
    monkeypatch.setattr(release_artifacts.subprocess, "run", fake_run)
    for key in release_artifacts.DOCKER_ENVIRONMENT_KEYS:
        monkeypatch.setenv(key, "inherited-attacker-value")

    runner = release_artifacts.SubprocessRunner()
    result = runner.run(
        ["docker", "version"],
        cwd=tmp_path,
        env={
            "SAFE_VALUE": "preserved",
            "DOCKER_HOST": "tcp://attacker:2375",
            "docker_context": "attacker-context",
            "XDG_CONFIG_HOME": str(tmp_path / "attacker-config"),
        },
    )

    command = cast(list[str], captured["command"])
    environment = cast(dict[str, str], captured["env"])
    assert result == "ok"
    assert command[:5] == [
        str(docker),
        "--host",
        release_artifacts._local_docker_endpoint(),
        "--config",
        str(runner._docker_config()),
    ]
    assert command[5:] == ["version"]
    assert environment["SAFE_VALUE"] == "preserved"
    assert not (release_artifacts.DOCKER_ENVIRONMENT_KEYS & set(environment))
    assert not (
        {key.casefold() for key in release_artifacts.DOCKER_ENVIRONMENT_KEYS}
        & {key.casefold() for key in environment}
    )
    assert (runner._docker_config() / "config.json").read_bytes() == b"{}\n"


def test_subprocess_runner_closes_git_environment_and_disables_replace_objects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_git = tmp_path / "trusted" / "git.exe"
    attacker_git = tmp_path / "attacker" / "git.cmd"
    fixed_git.parent.mkdir()
    attacker_git.parent.mkdir()
    fixed_git.write_bytes(b"fixed system git")
    attacker_git.write_text("exit /b 99\n", encoding="ascii")
    captured: dict[str, object] = {}

    def fake_run(command: Sequence[str], **kwargs: object) -> SimpleNamespace:
        captured["command"] = list(command)
        captured["env"] = dict(cast(Mapping[str, str], kwargs["env"]))
        return SimpleNamespace(stdout=b"a" * 40 + b"\n", stderr=b"", returncode=0)

    monkeypatch.setattr(release_artifacts, "_system_git", lambda: fixed_git)
    monkeypatch.setattr(release_artifacts.subprocess, "run", fake_run)
    monkeypatch.setenv("PATH", str(attacker_git.parent))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "attacker-repository"))
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(tmp_path / "attacker-objects"))

    runner = release_artifacts.SubprocessRunner()
    result = runner.run(
        ["git", "rev-parse", f"{COMMIT}:tools/release_artifacts.py"],
        cwd=tmp_path,
        env={
            "SAFE_VALUE": "preserved",
            "git_work_tree": str(tmp_path / "attacker-worktree"),
            "GIT_NO_REPLACE_OBJECTS": "0",
        },
    )

    environment = cast(dict[str, str], captured["env"])
    assert result == "a" * 40
    assert captured["command"] == [
        str(fixed_git),
        "--no-replace-objects",
        "-c",
        "core.fsmonitor=false",
        "rev-parse",
        f"{COMMIT}:tools/release_artifacts.py",
    ]
    assert str(attacker_git) not in cast(list[str], captured["command"])
    assert environment["SAFE_VALUE"] == "preserved"
    assert environment["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert not any(
        key.upper().startswith("GIT_") and key != "GIT_NO_REPLACE_OBJECTS" for key in environment
    )


def test_windows_git_contract_declares_only_install_root_hard_links_and_runtime_files() -> None:
    assert release_artifacts.WINDOWS_GIT_EXECUTABLE_LINKS == (
        "mingw64/bin/git.exe",
        "mingw64/bin/git-receive-pack.exe",
        "mingw64/bin/git-upload-archive.exe",
        "mingw64/bin/git-upload-pack.exe",
    )
    assert set(release_artifacts.WINDOWS_GIT_RUNTIME_SHA256) == {
        "mingw64/bin/git.exe",
        "mingw64/bin/libiconv-2.dll",
        "mingw64/bin/libintl-8.dll",
        "mingw64/bin/libpcre2-8-0.dll",
        "mingw64/bin/libwinpthread-1.dll",
        "mingw64/bin/zlib1.dll",
    }
    assert all(
        release_artifacts.SHA256_PATTERN.fullmatch(digest) is not None
        for digest in release_artifacts.WINDOWS_GIT_RUNTIME_SHA256.values()
    )


@pytest.mark.skipif(os.name != "nt", reason="Docker Desktop contract is Windows-only")
def test_windows_docker_receives_direct_acl_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    docker = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
    acl_validated: list[Path] = []
    monkeypatch.setattr(Path, "is_symlink", lambda _path: False)
    monkeypatch.setattr(Path, "is_file", lambda path: path == docker)
    monkeypatch.setattr(release_artifacts, "_validate_fixed_system_tool", lambda _path: None)
    monkeypatch.setattr(
        release_artifacts,
        "_validate_windows_fixed_system_tool_permissions",
        acl_validated.append,
    )

    assert release_artifacts._system_docker() == docker
    assert acl_validated == [docker]


@pytest.mark.skipif(os.name != "nt", reason="Git for Windows contract is Windows-only")
def test_fixed_windows_git_is_authenticated_acl_validated_and_operational(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acl_validated: list[Path] = []
    monkeypatch.setattr(
        release_artifacts,
        "_validate_windows_fixed_system_tool_permissions",
        acl_validated.append,
    )
    git = release_artifacts._system_git()

    assert git == Path(r"C:\Git\mingw64\bin\git.exe")
    assert acl_validated == [
        Path(r"C:\Git") / relative for relative in release_artifacts.WINDOWS_GIT_RUNTIME_SHA256
    ]
    assert (
        release_artifacts.SubprocessRunner()
        .run(["git", "--version"], cwd=ROOT)
        .startswith("git version 2.52.0.windows.")
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


def _write_minimal_snapshot_candidate(tmp_path: Path) -> Path:
    package = tmp_path / "snapshot-candidate"
    for relative in release_artifacts._expected_candidate_files(2):
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((relative + "\n").encode("ascii"))
    return package


def test_protected_snapshot_rejects_same_length_concurrent_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _write_minimal_snapshot_candidate(tmp_path)
    source = package / ".env.prod.example"
    original = source.read_bytes()
    replacement = b"x" * (len(original) - 1) + b"\n"
    assert replacement != original
    assert len(replacement) == len(original)
    actual_fdopen = os.fdopen
    rewritten = False

    class RewriteAfterFirstRead:
        def __init__(self, stream: BinaryIO) -> None:
            self.stream = stream

        def __enter__(self) -> RewriteAfterFirstRead:
            return self

        def __exit__(self, *args: object) -> None:
            self.stream.close()

        def read(self, size: int = -1) -> bytes:
            nonlocal rewritten
            value = self.stream.read(size)
            if not rewritten:
                metadata = source.stat()
                source.write_bytes(replacement)
                os.utime(
                    source,
                    ns=(metadata.st_atime_ns, metadata.st_mtime_ns),
                )
                rewritten = True
            return value

    def mutating_fdopen(
        descriptor: int,
        mode: str,
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
        closefd: bool = True,
        opener: object = None,
    ) -> RewriteAfterFirstRead:
        del buffering, encoding, errors, newline, opener
        stream = cast(BinaryIO, actual_fdopen(descriptor, mode, buffering=0, closefd=closefd))
        return RewriteAfterFirstRead(stream)

    monkeypatch.setattr(os, "fdopen", mutating_fdopen)

    with (
        pytest.raises(
            ReleaseArtifactError,
            match="candidate file content changed during snapshot",
        ),
        release_artifacts._protected_candidate_snapshot(package),
    ):
        pytest.fail("a concurrently rewritten snapshot must not be exposed")

    assert rewritten
    assert source.read_bytes() == replacement


def test_protected_snapshot_rejects_same_length_path_replacement_before_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _write_minimal_snapshot_candidate(tmp_path)
    source = package / ".env.prod.example"
    retained = tmp_path / "retained-original"
    original = source.read_bytes()
    replacement = b"y" * (len(original) - 1) + b"\n"
    assert replacement != original
    assert len(replacement) == len(original)
    actual_open = os.open
    replaced = False

    def replacing_open(
        path: str | bytes | os.PathLike[str],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal replaced
        if not replaced and Path(os.fsdecode(path)) == source:
            source.replace(retained)
            source.write_bytes(replacement)
            replaced = True
        if dir_fd is None:
            return actual_open(path, flags, mode)
        return actual_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", replacing_open)

    with (
        pytest.raises(ReleaseArtifactError, match="candidate file changed before snapshot"),
        release_artifacts._protected_candidate_snapshot(package),
    ):
        pytest.fail("a path-replaced snapshot must not be exposed")

    assert replaced
    assert retained.read_bytes() == original
    assert source.read_bytes() == replacement


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


def test_archive_rejects_oversized_manifest_before_json_allocation(tmp_path: Path) -> None:
    path = tmp_path / "oversized-manifest.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        _add_tar_bytes(
            archive,
            "manifest.json",
            b"[" + b" " * release_artifacts.MAX_RELEASE_JSON_BYTES + b"]",
        )

    with pytest.raises(ReleaseArtifactError, match="JSON byte limit"):
        inspect_docker_archive(path, "ruisheng-candidate/api:expected")


@pytest.mark.parametrize(
    "extension_type",
    (tarfile.XHDTYPE, tarfile.GNUTYPE_LONGNAME),
    ids=("pax", "gnu-longname"),
)
def test_archive_preflight_rejects_oversized_extension_metadata_before_payload_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extension_type: bytes,
) -> None:
    path = tmp_path / "oversized-extension.tar.gz"
    path.write_bytes(_canonical_gzip_bytes(_oversized_tar_extension_header(extension_type)))
    discarded: list[int] = []

    def record_discard(_stream: BinaryIO, size: int, *, label: str) -> None:
        del label
        discarded.append(size)

    monkeypatch.setattr(release_artifacts, "_discard_tar_bytes", record_discard)

    with pytest.raises(ReleaseArtifactError, match="forbidden tar extension metadata"):
        inspect_docker_archive(path, "ruisheng-candidate/api:expected")

    assert discarded == []


def test_archive_rejects_oversized_config_before_json_allocation(tmp_path: Path) -> None:
    reference = "ruisheng-candidate/api:expected"
    config_name = "oversized.json"
    manifest = json.dumps([{"Config": config_name, "Layers": [], "RepoTags": [reference]}]).encode()
    path = tmp_path / "oversized-config.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        _add_tar_bytes(archive, "manifest.json", manifest)
        _add_tar_bytes(
            archive,
            config_name,
            b"{" + b" " * release_artifacts.MAX_RELEASE_JSON_BYTES + b"}",
        )

    with pytest.raises(ReleaseArtifactError, match="JSON byte limit"):
        inspect_docker_archive(path, reference)


def test_archive_rejects_member_count_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release_artifacts, "MAX_DOCKER_ARCHIVE_MEMBERS", 2)
    path = tmp_path / "too-many-members.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for index in range(3):
            _add_tar_bytes(archive, f"member-{index}", b"")

    with pytest.raises(ReleaseArtifactError, match="too many members"):
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
        ("schema_version", 3.0, "unsupported manifest schema_version"),
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


def test_load_verification_rejects_candidate_reference_drift(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)
    manifest = verify_package(package, runner, trust_directory=_trust_for_package(package))
    api_reference = candidate_image_references(CANDIDATE_ID)["api"]
    runner.images[api_reference] = {
        **runner.images[api_reference],
        "Id": "sha256:" + "f" * 64,
    }

    with pytest.raises(
        ReleaseArtifactError,
        match="loaded candidate reference mismatch for api",
    ):
        load_and_verify_images(
            package,
            runner,
            trust_directory=_trust_for_package(package),
        )

    assert len(runner.loaded) == 5
    requested_ids = {
        command[3]
        for command, _env in runner.commands
        if command[:3] == ("docker", "image", "inspect") and command[3].startswith("sha256:")
    }
    api_image_id = next(image.image_id for image in manifest.images if image.component == "api")
    assert api_image_id in requested_ids
    assert any(
        command[:4] == ("docker", "image", "inspect", api_reference)
        for command, _env in runner.commands
    )


def test_load_verification_rejects_daemon_returning_wrong_id_for_requested_object(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)
    manifest = verify_package(package, runner, trust_directory=_trust_for_package(package))
    api_image_id = next(image.image_id for image in manifest.images if image.component == "api")
    runner.image_id_inspect_overrides[api_image_id] = "sha256:" + "f" * 64

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

    assert manifest_value["schema_version"] == 3
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
        "qualification_toolchain",
    }


def test_verify_cli_does_not_accept_a_caller_selected_trust_path() -> None:
    parser = release_artifacts._build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["verify", "candidate", "--trust-directory", "attacker-selected-trust"])


def test_v3_build_embeds_deterministic_exact_qualification_toolchain(
    tmp_path: Path, production_env: Path
) -> None:
    first_runner = FakeRunner()
    first = _build(tmp_path, production_env, first_runner)
    second = _build(
        tmp_path,
        production_env,
        FakeRunner(),
        candidate_id="deploy-20260819.2",
    )

    manifest = verify_package(first, first_runner, trust_directory=_trust_for_package(first))
    assert manifest.schema_version == 3
    descriptor = manifest.qualification_toolchain
    assert descriptor is not None
    assert descriptor.path == QUALIFICATION_TOOLCHAIN_ARCHIVE
    assert descriptor.format == QUALIFICATION_TOOLCHAIN_FORMAT
    assert descriptor.semantic_validator == SEMANTIC_VALIDATOR_ID
    archive_path = first / QUALIFICATION_TOOLCHAIN_ARCHIVE
    assert descriptor.sha256 == hashlib.sha256(archive_path.read_bytes()).hexdigest()
    assert archive_path.read_bytes() == (second / QUALIFICATION_TOOLCHAIN_ARCHIVE).read_bytes()
    sums = {
        relative: digest
        for digest, relative in (
            line.split("  ", maxsplit=1)
            for line in (first / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        )
    }
    assert descriptor.sha256 == sums[QUALIFICATION_TOOLCHAIN_ARCHIVE]

    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        assert tuple(member.name for member in members) == (
            *QUALIFICATION_TOOLCHAIN_MEMBERS,
            QUALIFICATION_TOOLCHAIN_MANIFEST,
        )
        assert all(member.isfile() for member in members)
        assert all(
            (member.mtime, member.uid, member.gid, member.uname, member.gname, member.mode)
            == (0, 0, 0, "", "", 0o644)
            for member in members
        )
        contents = {
            member.name: archive.extractfile(member).read()  # type: ignore[union-attr]
            for member in members
        }
    toolchain_manifest = json.loads(contents[QUALIFICATION_TOOLCHAIN_MANIFEST])
    assert set(toolchain_manifest) == {
        "artifact_type",
        "members",
        "schema_version",
        "semantic_validator",
    }
    assert toolchain_manifest["schema_version"] == 1
    assert toolchain_manifest["semantic_validator"] == SEMANTIC_VALIDATOR_ID
    assert [member["path"] for member in toolchain_manifest["members"]] == list(
        QUALIFICATION_TOOLCHAIN_MEMBERS
    )
    for member in toolchain_manifest["members"]:
        assert set(member) == {"path", "sha256"}
        assert member["sha256"] == hashlib.sha256(contents[member["path"]]).hexdigest()
    assert descriptor.schema.path == "schemas/point-profile/point-profile-v1.schema.json"
    assert descriptor.validator.path == "tools/validate_device_point_profile.py"
    assert descriptor.producer.path == "tools/release_artifacts.py"
    assert descriptor.receipt_producer.path == "tools/release_verification_receipt.py"
    assert descriptor.toolchain_manifest.path == QUALIFICATION_TOOLCHAIN_MANIFEST
    assert not any(command[:2] == ("git", "hash-object") for command, _env in first_runner.commands)


def test_v3_build_rejects_toolchain_source_that_does_not_match_source_commit(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    runner.git_blob_override = "f" * 40
    trust, identity = _write_fake_release_trust(tmp_path)

    with pytest.raises(
        ReleaseArtifactError,
        match="qualification toolchain source does not match",
    ):
        build_candidate(
            root=ROOT,
            output_root=tmp_path / "dist" / "deploy",
            candidate_id=CANDIDATE_ID,
            target_platform=PLATFORM,
            env_file=production_env,
            postgres_source="timescale/timescaledb:2.16.1-pg15",
            redis_source="redis:7-alpine",
            runner=runner,
            signing_identity=identity,
            trust_directory=trust,
            lock_root=tmp_path / "candidate-locks",
        )


def test_trusted_bootstrap_executes_authenticated_archived_validator(
    tmp_path: Path, production_env: Path
) -> None:
    runner = QualificationRunner()
    package = _build(tmp_path, production_env, runner)

    outcome = qualification_bootstrap.execute_authenticated_qualification_tool(
        package,
        runner,
        trust_directory=_trust_for_package(package),
        tool="validator",
        tool_arguments=("schema",),
    )

    expected = json.loads(
        (ROOT / "schemas/point-profile/point-profile-v1.schema.json").read_text(encoding="utf-8")
    )
    assert outcome.returncode == 0
    assert outcome.stderr == ""
    assert json.loads(outcome.stdout) == expected
    assert len(runner.qualification_commands) == 1
    command, extraction = runner.qualification_commands[0]
    assert command[1:7] == ("-I", "-B", "-S", "-X", "utf8", "-c")
    assert Path(command[13]).name == "validate_device_point_profile.py"
    assert Path(command[13]).is_relative_to(extraction)
    assert extraction != ROOT
    assert not extraction.exists()


def test_qualification_rejects_candidate_supplied_launcher_before_execution(
    tmp_path: Path, production_env: Path
) -> None:
    runner = QualificationRunner()
    package = _build(tmp_path, production_env, runner)
    marker = tmp_path / "candidate-code-executed"
    (package / "qualification-launcher.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )

    with pytest.raises(ReleaseArtifactError, match="file allowlist mismatch"):
        qualification_bootstrap.execute_authenticated_qualification_tool(
            package,
            runner,
            trust_directory=_trust_for_package(package),
            tool="validator",
            tool_arguments=("schema",),
        )

    assert not marker.exists()
    assert runner.qualification_commands == []


def test_qualification_archive_header_scan_stops_at_first_extra_member() -> None:
    expected = (*QUALIFICATION_TOOLCHAIN_MEMBERS, QUALIFICATION_TOOLCHAIN_MANIFEST)

    class HeaderSource:
        observed = 0

        def __iter__(self) -> Iterator[tarfile.TarInfo]:
            for name in (*expected, "unexpected"):
                self.observed += 1
                yield tarfile.TarInfo(name)
            raise AssertionError("scanner consumed headers after the first disallowed member")

    source = HeaderSource()
    with pytest.raises(ReleaseArtifactError, match="member allowlist mismatch"):
        release_artifacts._exact_qualification_tar_members(cast(tarfile.TarFile, source), expected)

    assert source.observed == len(expected) + 1


@pytest.mark.parametrize(
    "extension_type",
    (tarfile.XHDTYPE, tarfile.GNUTYPE_LONGNAME),
    ids=("pax", "gnu-longname"),
)
def test_qualification_ustar_preflight_rejects_huge_extension_before_payload_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extension_type: bytes,
) -> None:
    member = tarfile.TarInfo(QUALIFICATION_TOOLCHAIN_MEMBERS[0])
    member.type = extension_type
    member.size = (1 << 33) - 1
    member.mtime = 0
    member.uid = 0
    member.gid = 0
    member.uname = ""
    member.gname = ""
    member.mode = 0o644
    archive_path = tmp_path / QUALIFICATION_TOOLCHAIN_ARCHIVE
    archive_path.write_bytes(_canonical_gzip_bytes(member.tobuf(format=tarfile.USTAR_FORMAT)))
    archive_digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    descriptor = release_artifacts.QualificationToolchainDescriptor(
        path=QUALIFICATION_TOOLCHAIN_ARCHIVE,
        sha256=archive_digest,
        format=QUALIFICATION_TOOLCHAIN_FORMAT,
        semantic_validator=SEMANTIC_VALIDATOR_ID,
        schema=release_artifacts.ArtifactIdentity(
            path="schemas/point-profile/point-profile-v1.schema.json", sha256="a" * 64
        ),
        validator=release_artifacts.ArtifactIdentity(
            path="tools/validate_device_point_profile.py", sha256="b" * 64
        ),
        producer=release_artifacts.ArtifactIdentity(
            path="tools/release_artifacts.py", sha256="c" * 64
        ),
        receipt_producer=release_artifacts.ArtifactIdentity(
            path="tools/release_verification_receipt.py", sha256="d" * 64
        ),
        toolchain_manifest=release_artifacts.ArtifactIdentity(
            path=QUALIFICATION_TOOLCHAIN_MANIFEST, sha256="e" * 64
        ),
    )

    observed_reads: list[int] = []
    original_read = gzip.GzipFile.read

    def read_once(self: gzip.GzipFile, size: int = -1) -> bytes:
        observed_reads.append(size)
        if len(observed_reads) > 1:
            raise AssertionError("extension payload was read before its type was rejected")
        return original_read(self, size)

    monkeypatch.setattr(gzip.GzipFile, "read", read_once)

    with pytest.raises(
        ReleaseArtifactError,
        match="non-regular USTAR member",
    ):
        release_artifacts._verify_qualification_toolchain(
            tmp_path,
            descriptor,
            {QUALIFICATION_TOOLCHAIN_ARCHIVE: archive_digest},
        )

    assert observed_reads == [tarfile.BLOCKSIZE]


def test_qualification_ustar_preflight_rejects_nonzero_member_padding(tmp_path: Path) -> None:
    expected = (*QUALIFICATION_TOOLCHAIN_MEMBERS, QUALIFICATION_TOOLCHAIN_MANIFEST)
    archive_path = tmp_path / QUALIFICATION_TOOLCHAIN_ARCHIVE
    _write_canonical_qualification_archive(
        archive_path,
        dict.fromkeys(expected, b"x"),
    )
    expanded = bytearray(gzip.decompress(archive_path.read_bytes()))
    expanded[tarfile.BLOCKSIZE + 1] = 1
    with (
        archive_path.open("wb") as raw_archive,
        gzip.GzipFile(
            filename="",
            mode="wb",
            compresslevel=9,
            fileobj=raw_archive,
            mtime=0,
        ) as compressed,
    ):
        compressed.write(expanded)

    with (
        archive_path.open("rb") as raw_archive,
        pytest.raises(ReleaseArtifactError, match="non-zero USTAR padding"),
    ):
        release_artifacts._preflight_qualification_ustar_archive(raw_archive, expected)


def _write_powershell_qualification_ustar_fixture(
    path: Path, *, extension_type: bytes | None
) -> None:
    expected = (*QUALIFICATION_TOOLCHAIN_MEMBERS, QUALIFICATION_TOOLCHAIN_MANIFEST)
    if extension_type is None:
        with (
            path.open("xb") as raw_archive,
            gzip.GzipFile(filename="", mode="wb", fileobj=raw_archive, mtime=0) as compressed,
            tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive,
        ):
            for name in expected:
                member = tarfile.TarInfo(name)
                member.size = 1
                member.mtime = 0
                member.uid = 0
                member.gid = 0
                member.uname = ""
                member.gname = ""
                member.mode = 0o644
                archive.addfile(member, io.BytesIO(b"x"))
        return

    member = tarfile.TarInfo(expected[0])
    member.type = extension_type
    member.size = 1024 * 1024
    member.mtime = 0
    member.uid = 0
    member.gid = 0
    member.uname = ""
    member.gname = ""
    member.mode = 0o644
    path.write_bytes(_canonical_gzip_bytes(member.tobuf(format=tarfile.USTAR_FORMAT)))


def _run_powershell_qualification_ustar_preflight(
    archive_path: Path, *, script_relative: str
) -> subprocess.CompletedProcess[str]:
    command = r"""
$source = Get-Content -Raw -LiteralPath $env:RS_VERIFY_SCRIPT
$start = $source.IndexOf('function Test-ZeroUstarRange')
$end = $source.IndexOf($env:RS_END_MARKER, $start)
if ($start -lt 0 -or $end -lt 0) { throw 'Qualification USTAR block not found' }
function Fail([string]$Message) { throw $Message }
. ([scriptblock]::Create($source.Substring($start, $end - $start)))
[string[]]$expected = @(
    'tools/validate_device_point_profile.py',
    'tools/trust_root_freshness.py',
    'schemas/point-profile/point-profile-v1.schema.json',
    'tools/release_artifacts.py',
    'tools/release_verification_receipt.py',
    'pyproject.toml',
    'uv.lock',
    'qualification-toolchain-manifest.json'
)
$limits = @{}
foreach ($name in $expected) { $limits[$name] = 64MB }
$limits['qualification-toolchain-manifest.json'] = 4MB
Assert-CanonicalQualificationUstarArchive $env:RS_ARCHIVE_PATH $expected $limits
[Console]::Out.Write('VERIFIED')
"""
    end_marker = (
        "function Read-TarEntries"
        if script_relative == "deploy/verify-candidate.ps1"
        else "function Test-QualificationToolchain"
    )
    environment = os.environ.copy()
    environment.update(
        {
            "RS_VERIFY_SCRIPT": str(ROOT / script_relative),
            "RS_ARCHIVE_PATH": str(archive_path),
            "RS_END_MARKER": end_marker,
        }
    )
    return subprocess.run(
        [shutil.which("pwsh") or "pwsh", "-NoProfile", "-Command", command],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=30,
    )


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(
    "script_relative",
    ("deploy/verify-candidate.ps1", "tools/release_trust/verify-publisher.ps1"),
)
@pytest.mark.parametrize(
    ("extension_type", "accepted"),
    ((None, True), (tarfile.XHDTYPE, False), (tarfile.GNUTYPE_LONGNAME, False)),
    ids=("canonical", "pax", "gnu-longname"),
)
def test_powershell_qualification_ustar_preflight_rejects_extension_headers(
    tmp_path: Path,
    script_relative: str,
    extension_type: bytes | None,
    accepted: bool,
) -> None:
    archive_path = tmp_path / (
        Path(script_relative).stem
        + "-"
        + (extension_type.decode("ascii") if extension_type else "ok")
    )
    _write_powershell_qualification_ustar_fixture(archive_path, extension_type=extension_type)

    result = _run_powershell_qualification_ustar_preflight(
        archive_path, script_relative=script_relative
    )

    if accepted:
        assert result.returncode == 0, result.stderr or result.stdout
        assert result.stdout == "VERIFIED"
    else:
        assert result.returncode != 0
        assert "noncanonical USTAR header" in (result.stderr + result.stdout)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(
    "script_relative",
    ("deploy/verify-candidate.ps1", "tools/release_trust/verify-publisher.ps1"),
)
def test_powershell_qualification_ustar_preflight_rejects_extra_zero_block(
    tmp_path: Path,
    script_relative: str,
) -> None:
    archive_path = tmp_path / f"{Path(script_relative).stem}-extra-zero-block.tar.gz"
    _write_powershell_qualification_ustar_fixture(archive_path, extension_type=None)
    expanded = gzip.decompress(archive_path.read_bytes())
    archive_path.write_bytes(_canonical_gzip_bytes(expanded + bytes(tarfile.BLOCKSIZE)))

    result = _run_powershell_qualification_ustar_preflight(
        archive_path, script_relative=script_relative
    )

    assert result.returncode != 0
    assert "excessive trailing blocks" in (result.stderr + result.stdout)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(
    "script_relative",
    ("deploy/verify-candidate.ps1", "tools/release_trust/verify-publisher.ps1"),
)
def test_powershell_qualification_ustar_preflight_rejects_gzip_extended_flag(
    tmp_path: Path,
    script_relative: str,
) -> None:
    archive_path = tmp_path / f"{Path(script_relative).stem}-gzip-extra-flag.tar.gz"
    _write_powershell_qualification_ustar_fixture(archive_path, extension_type=None)
    encoded = bytearray(archive_path.read_bytes())
    encoded[3] = 0x04
    archive_path.write_bytes(encoded)

    result = _run_powershell_qualification_ustar_preflight(
        archive_path, script_relative=script_relative
    )

    assert result.returncode != 0
    assert "gzip header is not canonical" in (result.stderr + result.stdout)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(
    "script_relative",
    ("deploy/verify-candidate.ps1", "tools/release_trust/verify-publisher.ps1"),
)
@pytest.mark.parametrize("offset", (4, 8, 9), ids=("mtime", "xfl", "os"))
def test_powershell_qualification_ustar_preflight_rejects_noncanonical_gzip_header(
    tmp_path: Path,
    script_relative: str,
    offset: int,
) -> None:
    archive_path = tmp_path / f"{Path(script_relative).stem}-gzip-header-{offset}.tar.gz"
    _write_powershell_qualification_ustar_fixture(archive_path, extension_type=None)
    encoded = bytearray(archive_path.read_bytes())
    encoded[offset] ^= 1
    archive_path.write_bytes(encoded)

    result = _run_powershell_qualification_ustar_preflight(
        archive_path, script_relative=script_relative
    )

    assert result.returncode != 0
    assert "gzip header is not canonical" in (result.stderr + result.stdout)


def test_python_qualification_ustar_preflight_rejects_second_empty_gzip_member(
    tmp_path: Path,
) -> None:
    expected = (*QUALIFICATION_TOOLCHAIN_MEMBERS, QUALIFICATION_TOOLCHAIN_MANIFEST)
    archive_path = tmp_path / QUALIFICATION_TOOLCHAIN_ARCHIVE
    _write_canonical_qualification_archive(
        archive_path,
        dict.fromkeys(expected, b"x"),
    )
    archive_path.write_bytes(archive_path.read_bytes() + _canonical_gzip_bytes(b""))

    with (
        archive_path.open("rb") as raw_archive,
        pytest.raises(ReleaseArtifactError, match="exactly one gzip member"),
    ):
        release_artifacts._preflight_qualification_ustar_archive(raw_archive, expected)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(
    "script_relative",
    ("deploy/verify-candidate.ps1", "tools/release_trust/verify-publisher.ps1"),
)
def test_powershell_qualification_ustar_preflight_rejects_second_empty_gzip_member(
    tmp_path: Path,
    script_relative: str,
) -> None:
    archive_path = tmp_path / f"{Path(script_relative).stem}-second-member.tar.gz"
    _write_powershell_qualification_ustar_fixture(archive_path, extension_type=None)
    archive_path.write_bytes(archive_path.read_bytes() + _canonical_gzip_bytes(b""))

    result = _run_powershell_qualification_ustar_preflight(
        archive_path, script_relative=script_relative
    )

    assert result.returncode != 0
    assert "exactly one gzip member" in (result.stderr + result.stdout)


def test_docker_descriptor_reference_budget_is_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release_artifacts, "MAX_DOCKER_DESCRIPTOR_REFERENCES", 2)
    inspection = release_artifacts._ArchiveInspection(
        cast(tarfile.TarFile, SimpleNamespace()),
        tmp_path / "image.tar.gz",
        {},
    )

    assert (
        release_artifacts._read_archive_sha256_blob(
            inspection, "sha256:" + "a" * 64, label="descriptor", allow_missing=True
        )
        is None
    )
    assert (
        release_artifacts._read_archive_sha256_blob(
            inspection, "sha256:" + "b" * 64, label="descriptor", allow_missing=True
        )
        is None
    )
    with pytest.raises(ReleaseArtifactError, match="reference budget exceeded"):
        release_artifacts._read_archive_sha256_blob(
            inspection, "sha256:" + "c" * 64, label="descriptor", allow_missing=True
        )


def test_docker_metadata_budget_is_checked_before_blob_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contents = b"{}"
    digest = f"sha256:{hashlib.sha256(contents).hexdigest()}"
    blob_name = _blob_name(digest)
    member = SimpleNamespace(name=blob_name, size=len(contents), isfile=lambda: True)
    extraction_attempted = False

    def extractfile(_member: object) -> BinaryIO:
        nonlocal extraction_attempted
        extraction_attempted = True
        return io.BytesIO(contents)

    monkeypatch.setattr(release_artifacts, "MAX_DOCKER_METADATA_BYTES", 1)
    inspection = release_artifacts._ArchiveInspection(
        cast(tarfile.TarFile, SimpleNamespace(extractfile=extractfile)),
        tmp_path / "image.tar.gz",
        {blob_name: cast(tarfile.TarInfo, member)},
    )

    with pytest.raises(ReleaseArtifactError, match="metadata byte budget exceeded"):
        release_artifacts._read_archive_sha256_blob(inspection, digest, label="descriptor")

    assert not extraction_attempted


def test_python_candidate_verifiers_bound_cached_docker_metadata() -> None:
    release_source = (ROOT / "tools" / "release_artifacts.py").read_text(encoding="utf-8")
    deploy_source = (ROOT / "deploy" / "verify-candidate.sh").read_text(encoding="utf-8")

    assert "MAX_DOCKER_METADATA_BYTES = 64 * 1024 * 1024" in release_source
    assert "inspection.consume_metadata_bytes(member.size)" in release_source
    assert "MAX_DOCKER_METADATA_BYTES = 64 * 1024 * 1024" in deploy_source
    assert "blob_cache = DockerBlobCache()" in deploy_source
    assert "MAX_DOCKER_METADATA_BYTES - blob_cache.metadata_bytes" in deploy_source


def test_windows_fixed_tool_acl_never_trusts_authenticated_users_for_replacement() -> None:
    validator = release_artifacts.WINDOWS_FIXED_SYSTEM_TOOL_VALIDATOR

    assert '"S-1-5-11"' not in validator
    assert '"S-1-5-18"' in validator
    assert '"S-1-5-32-544"' in validator


@pytest.mark.parametrize("exit_code", (2, 3))
def test_trusted_bootstrap_preserves_validator_decision_exit_codes(
    tmp_path: Path,
    production_env: Path,
    exit_code: int,
) -> None:
    runner = QualificationRunner()
    runner.forced_qualification_outcome = release_artifacts.CommandOutcome(
        stdout='{"decision":"BLOCKED"}\n' if exit_code == 2 else '{"decision":"INVALID"}\n',
        stderr="",
        returncode=exit_code,
    )
    package = _build(tmp_path, production_env, runner)

    outcome = qualification_bootstrap.execute_authenticated_qualification_tool(
        package,
        runner,
        trust_directory=_trust_for_package(package),
        tool="validator",
        tool_arguments=("validate", str(tmp_path / "profile.json")),
    )

    assert outcome.returncode == exit_code
    assert json.loads(outcome.stdout)["decision"] in {"BLOCKED", "INVALID"}


def test_trusted_bootstrap_rejects_unexpected_validator_exit_code(
    tmp_path: Path,
    production_env: Path,
) -> None:
    runner = QualificationRunner()
    runner.forced_qualification_outcome = release_artifacts.CommandOutcome(
        stdout="",
        stderr="validator crashed\n",
        returncode=1,
    )
    package = _build(tmp_path, production_env, runner)

    with pytest.raises(ReleaseArtifactError, match=r"qualification command failed \(1\)"):
        qualification_bootstrap.execute_authenticated_qualification_tool(
            package,
            runner,
            trust_directory=_trust_for_package(package),
            tool="validator",
            tool_arguments=("schema",),
        )


def test_windows_system_trust_bootstrap_directs_callers_to_protected_publisher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = QualificationRunner()
    monkeypatch.setattr(qualification_bootstrap, "os", SimpleNamespace(name="nt"))
    with pytest.raises(
        ReleaseArtifactError,
        match="Windows system qualification requires the protected PowerShell publisher",
    ):
        qualification_bootstrap.execute_authenticated_qualification_tool(
            tmp_path / "candidate",
            runner,
            trust_directory=tmp_path / "trust",
            tool="validator",
            tool_arguments=("schema",),
            require_system_trust=True,
        )

    assert runner.commands == []


def test_system_trust_launcher_selects_manifest_bound_posix_runtime(
    tmp_path: Path,
    production_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = QualificationRunner()
    runner.forced_qualification_outcome = release_artifacts.CommandOutcome(
        stdout='{"decision":"BLOCKED"}\n',
        stderr="",
        returncode=2,
    )
    package = _build(tmp_path, production_env, runner)
    protected = tmp_path / "protected"
    protected.mkdir()
    runtime = release_artifacts._QualificationRuntime(
        root=tmp_path / "fixed-runtime",
        python=Path(sys.executable),
        dependency_root=tmp_path / "fixed-runtime" / "lib/python3.11/site-packages",
        strict=True,
        authenticated_uv_lock_sha256=hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        files=(("bin/python3.11", "a" * 64),),
    )
    observed: list[tuple[Path, str]] = []

    def fake_validate_runtime(root: Path, *, authenticated_uv_lock_sha256: str) -> object:
        observed.append((root, authenticated_uv_lock_sha256))
        return runtime

    monkeypatch.setattr(
        qualification_bootstrap, "_validate_system_trust_permissions", lambda trust: None
    )
    monkeypatch.setattr(qualification_bootstrap, "_system_protected_workdir", lambda: protected)
    monkeypatch.setattr(
        qualification_bootstrap,
        "_validate_posix_qualification_runtime",
        fake_validate_runtime,
    )
    monkeypatch.setattr(qualification_bootstrap, "os", SimpleNamespace(name="posix"))

    outcome = qualification_bootstrap.execute_authenticated_qualification_tool(
        package,
        runner,
        trust_directory=_trust_for_package(package),
        tool="validator",
        tool_arguments=("validate", str(tmp_path / "profile.json")),
        require_system_trust=True,
    )

    assert outcome.returncode == 2
    assert len(observed) == 2
    assert observed[0][0] == qualification_bootstrap.POSIX_QUALIFICATION_RUNTIME_ROOT
    assert observed[1][0] == runtime.root
    assert observed[0][1] == hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest()
    command, _cwd = runner.qualification_commands[0]
    assert command[1:7] == ("-I", "-B", "-S", "-X", "utf8", "-c")
    assert command[8] == "1"
    assert command[9:12] == (
        str(runtime.root),
        str(runtime.python),
        str(runtime.dependency_root),
    )


def test_posix_runtime_manifest_is_exact_and_uv_lock_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    members = {
        "bin/python3.11": b"fixed python",
        "lib/python3.11/encodings/__init__.py": b"# encodings\n",
        "lib/python3.11/site-packages/pydantic/__init__.py": b"# dependency\n",
    }
    identities = []
    for relative, contents in members.items():
        path = runtime / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        identities.append({"path": relative, "sha256": hashlib.sha256(contents).hexdigest()})
    identities.sort(key=lambda item: item["path"])
    uv_lock_sha256 = "b" * 64
    manifest = {
        "artifact_type": release_artifacts.QUALIFICATION_RUNTIME_ARTIFACT_TYPE,
        "schema_version": release_artifacts.QUALIFICATION_RUNTIME_SCHEMA_VERSION,
        "python_version": release_artifacts.QUALIFICATION_RUNTIME_PYTHON_VERSION,
        "uv_lock_sha256": uv_lock_sha256,
        "dependency_root": release_artifacts.POSIX_QUALIFICATION_RUNTIME_DEPENDENCIES,
        "files": identities,
    }
    (runtime / release_artifacts.QUALIFICATION_RUNTIME_MANIFEST).write_text(
        json.dumps(manifest, sort_keys=True),
        encoding="utf-8",
    )

    real_os = release_artifacts.os
    real_hash_runtime_file = release_artifacts._hash_stable_runtime_file

    class PosixOsProxy:
        name = "posix"

        def __getattr__(self, name: str) -> object:
            if name == "access":
                return lambda path, mode: True
            return getattr(real_os, name)

    def fake_root_protection(path: Path, *, label: str) -> SimpleNamespace:
        del label
        metadata = path.lstat()
        return SimpleNamespace(st_mode=metadata.st_mode & ~0o022, st_uid=0)

    def fake_hash_runtime_file(path: Path, *, label: str) -> tuple[str, SimpleNamespace]:
        digest, metadata = real_hash_runtime_file(path, label=label)
        return digest, SimpleNamespace(
            st_mode=metadata.st_mode & ~0o022,
            st_size=metadata.st_size,
            st_uid=0,
        )

    monkeypatch.setattr(release_artifacts, "os", PosixOsProxy())
    monkeypatch.setattr(
        release_artifacts,
        "_validate_root_protected_posix_path",
        fake_root_protection,
    )
    monkeypatch.setattr(
        release_artifacts,
        "_hash_stable_runtime_file",
        fake_hash_runtime_file,
    )

    resolved = release_artifacts._validate_posix_qualification_runtime(
        runtime,
        authenticated_uv_lock_sha256=uv_lock_sha256,
    )
    assert resolved.strict
    assert resolved.python == runtime / release_artifacts.POSIX_QUALIFICATION_RUNTIME_PYTHON
    assert resolved.dependency_root == (
        runtime / release_artifacts.POSIX_QUALIFICATION_RUNTIME_DEPENDENCIES
    )

    with pytest.raises(ReleaseArtifactError, match="manifest contract is invalid"):
        release_artifacts._validate_posix_qualification_runtime(
            runtime,
            authenticated_uv_lock_sha256="c" * 64,
        )


def test_candidate_archived_producer_has_no_qualification_entrypoint() -> None:
    parser = release_artifacts._build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["qualify", "candidate", "validator", "schema"])
    assert not hasattr(release_artifacts, "execute_authenticated_qualification_tool")
    assert qualification_bootstrap.is_package_external()
    assert "tools/qualification_bootstrap.py" not in QUALIFICATION_TOOLCHAIN_MEMBERS


@pytest.mark.parametrize("timeout_seconds", (0.2, 5.0), ids=("timeout", "normal-exit"))
def test_isolated_runner_terminates_descendants_on_every_exit(
    tmp_path: Path,
    timeout_seconds: float,
) -> None:
    marker = tmp_path / f"descendant-{timeout_seconds}.txt"
    gate = r"""
import os
if os.name == "nt":
    import ctypes
    name = os.environ.pop("RUISHENG_PROCESS_JOB_GATE")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenEventW(0x00100000, False, name)
    if not handle or kernel32.WaitForSingleObject(handle, 30000) != 0:
        raise SystemExit(91)
    kernel32.CloseHandle(handle)
"""
    child = (
        "import time; from pathlib import Path; time.sleep(1); "
        f"Path({str(marker)!r}).write_text('escaped', encoding='ascii')"
    )
    parent_delay = "time.sleep(10)" if timeout_seconds < 1 else "None"
    parent = (
        gate
        + "\nimport subprocess, sys, time\n"
        + f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        + parent_delay
        + "\n"
    )
    runner = release_artifacts.SubprocessRunner()

    if timeout_seconds < 1:
        with pytest.raises(ReleaseArtifactError, match="command timed out"):
            runner.run_outcome(
                [sys.executable, "-c", parent],
                cwd=tmp_path,
                timeout_seconds=timeout_seconds,
                isolate_process_tree=True,
            )
    else:
        outcome = runner.run_outcome(
            [sys.executable, "-c", parent],
            cwd=tmp_path,
            timeout_seconds=timeout_seconds,
            isolate_process_tree=True,
        )
        assert outcome.returncode == 0

    time.sleep(1.2)
    assert not marker.exists()


def test_trusted_bootstrap_rejects_tampered_toolchain_before_execution(
    tmp_path: Path, production_env: Path
) -> None:
    runner = QualificationRunner()
    package = _build(tmp_path, production_env, runner)
    archive = package / QUALIFICATION_TOOLCHAIN_ARCHIVE
    archive.write_bytes(archive.read_bytes() + b"tampered")

    with pytest.raises(ReleaseArtifactError, match="SHA-256 mismatch"):
        qualification_bootstrap.execute_authenticated_qualification_tool(
            package,
            runner,
            trust_directory=_trust_for_package(package),
            tool="validator",
            tool_arguments=("schema",),
        )

    assert runner.qualification_commands == []


def test_v2_candidate_remains_accepted_and_forbids_v3_descriptor(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)
    _downgrade_package_to_v2(package, runner)

    manifest = verify_package(package, runner, trust_directory=_trust_for_package(package))

    assert manifest.schema_version == 2
    assert manifest.qualification_toolchain is None
    assert {
        path.relative_to(package).as_posix() for path in package.rglob("*") if path.is_file()
    } == (FIXED_PACKAGE_FILES_V2 | {f"images/{component}.tar.gz" for component in COMPONENTS})

    value = json.loads((package / "MANIFEST.json").read_text(encoding="utf-8"))
    value["qualification_toolchain"] = {"attacker": "selected"}
    _rewrite_authenticated_manifest(package, runner, value, render_markdown=False)
    with pytest.raises(ReleaseArtifactError, match="MANIFEST.json keys mismatch"):
        verify_package(package, runner, trust_directory=_trust_for_package(package))


def test_v2_candidate_cannot_execute_a_qualification_tool(
    tmp_path: Path,
    production_env: Path,
) -> None:
    runner = QualificationRunner()
    package = _build(tmp_path, production_env, runner)
    _downgrade_package_to_v2(package, runner)
    runner.commands.clear()
    runner.qualification_commands.clear()

    with pytest.raises(
        ReleaseArtifactError,
        match="candidate has no authenticated qualification toolchain",
    ):
        qualification_bootstrap.execute_authenticated_qualification_tool(
            package,
            runner,
            trust_directory=_trust_for_package(package),
            tool="validator",
            tool_arguments=("schema",),
        )

    assert runner.qualification_commands == []
    assert not any(
        Path(command[0]).name.casefold() in {"ssh-keygen", "ssh-keygen.exe"}
        or command[:2] == ("docker", "compose")
        or command[:3] == ("docker", "image", "inspect")
        for command, _environment in runner.commands
    )


@pytest.mark.parametrize("direction", ("v3-files-v2-manifest", "v2-files-v3-manifest"))
def test_verifier_rejects_mixed_v2_v3_file_sets_before_docker(
    tmp_path: Path, production_env: Path, direction: str
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)
    toolchain_bytes = (package / QUALIFICATION_TOOLCHAIN_ARCHIVE).read_bytes()
    if direction == "v3-files-v2-manifest":
        _downgrade_package_to_v2(package, runner)
        (package / QUALIFICATION_TOOLCHAIN_ARCHIVE).write_bytes(toolchain_bytes)
    else:
        (package / QUALIFICATION_TOOLCHAIN_ARCHIVE).unlink()

    runner.commands.clear()
    with pytest.raises(ReleaseArtifactError, match="allowlist mismatch|complete v2 or v3"):
        verify_package(package, runner, trust_directory=_trust_for_package(package))
    assert not any(command[0] == "docker" for command, _env in runner.commands)


def test_v3_manifest_without_descriptor_is_rejected(tmp_path: Path, production_env: Path) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)
    value = json.loads((package / "MANIFEST.json").read_text(encoding="utf-8"))
    value.pop("qualification_toolchain")
    _rewrite_authenticated_manifest(package, runner, value, render_markdown=False)

    with pytest.raises(ReleaseArtifactError, match="MANIFEST.json keys mismatch"):
        verify_package(package, runner, trust_directory=_trust_for_package(package))


def test_v3_descriptor_tamper_is_rejected_after_authenticated_hashes(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)
    value = json.loads((package / "MANIFEST.json").read_text(encoding="utf-8"))
    value["qualification_toolchain"]["semantic_validator"] = "attacker-validator/v1"
    _rewrite_authenticated_manifest(package, runner, value)

    with pytest.raises(ReleaseArtifactError, match="semantic validator"):
        verify_package(package, runner, trust_directory=_trust_for_package(package))


def test_v3_toolchain_digest_is_bound_into_logical_identity(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)
    value = json.loads((package / "MANIFEST.json").read_text(encoding="utf-8"))
    value["qualification_toolchain"]["sha256"] = "f" * 64
    _rewrite_authenticated_manifest(package, runner, value)

    with pytest.raises(
        ReleaseArtifactError,
        match="logical_identity does not match its immutable inputs",
    ):
        verify_package(package, runner, trust_directory=_trust_for_package(package))


def test_powershell_recomputes_the_same_v2_and_v3_logical_identity(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)

    for schema_version in (3, 2):
        manifest = json.loads((package / "MANIFEST.json").read_text(encoding="utf-8"))
        assert manifest["schema_version"] == schema_version
        results = (
            _run_powershell_logical_identity(package / "MANIFEST.json"),
            _run_powershell_logical_identity(
                package / "MANIFEST.json",
                script_relative="tools/release_trust/verify-publisher.ps1",
                start_marker="function Get-QualificationSha256",
                end_marker="Test-QualificationToolchain $Manifest",
            ),
        )
        for result in results:
            assert result.returncode == 0, result.stderr or result.stdout
            assert result.stdout.strip() == manifest["logical_identity"]
        if schema_version == 3:
            _downgrade_package_to_v2(package, runner)


def test_v3_toolchain_member_tamper_is_rejected_even_when_outer_hash_is_resigned(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)
    archive_path = package / QUALIFICATION_TOOLCHAIN_ARCHIVE
    with tarfile.open(archive_path, "r:gz") as archive:
        contents = {
            member.name: archive.extractfile(member).read()  # type: ignore[union-attr]
            for member in archive.getmembers()
        }
    contents["tools/validate_device_point_profile.py"] += b"\n# tampered\n"
    _write_canonical_qualification_archive(archive_path, contents)

    value = json.loads((package / "MANIFEST.json").read_text(encoding="utf-8"))
    value["qualification_toolchain"]["sha256"] = hashlib.sha256(
        archive_path.read_bytes()
    ).hexdigest()
    parsed = release_artifacts._manifest_from_dict(value)
    value["logical_identity"] = compute_logical_identity(
        candidate_id=parsed.candidate_id,
        source_commit=parsed.source_commit,
        target_os=parsed.target_os,
        target_architecture=parsed.target_architecture,
        alembic_head=parsed.alembic_head,
        images=parsed.images,
        qualification_toolchain=parsed.qualification_toolchain,
    )
    _rewrite_authenticated_manifest(package, runner, value)

    with pytest.raises(ReleaseArtifactError, match="toolchain member SHA-256 mismatch"):
        verify_package(package, runner, trust_directory=_trust_for_package(package))


def test_v3_toolchain_rejects_float_schema_version_when_outer_hash_is_resigned(
    tmp_path: Path, production_env: Path
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)
    archive_path = package / QUALIFICATION_TOOLCHAIN_ARCHIVE
    with tarfile.open(archive_path, "r:gz") as archive:
        contents = {
            member.name: archive.extractfile(member).read()  # type: ignore[union-attr]
            for member in archive.getmembers()
        }
    internal = json.loads(contents[QUALIFICATION_TOOLCHAIN_MANIFEST])
    internal["schema_version"] = 1.0
    contents[QUALIFICATION_TOOLCHAIN_MANIFEST] = _json_bytes(internal)
    _write_canonical_qualification_archive(archive_path, contents)

    value = json.loads((package / "MANIFEST.json").read_text(encoding="utf-8"))
    value["qualification_toolchain"]["sha256"] = hashlib.sha256(
        archive_path.read_bytes()
    ).hexdigest()
    value["qualification_toolchain"]["toolchain_manifest"]["sha256"] = hashlib.sha256(
        contents[QUALIFICATION_TOOLCHAIN_MANIFEST]
    ).hexdigest()
    parsed = release_artifacts._manifest_from_dict(value)
    value["logical_identity"] = compute_logical_identity(
        candidate_id=parsed.candidate_id,
        source_commit=parsed.source_commit,
        target_os=parsed.target_os,
        target_architecture=parsed.target_architecture,
        alembic_head=parsed.alembic_head,
        images=parsed.images,
        qualification_toolchain=parsed.qualification_toolchain,
    )
    _rewrite_authenticated_manifest(package, runner, value)

    with pytest.raises(ReleaseArtifactError, match="toolchain manifest contract is invalid"):
        verify_package(package, runner, trust_directory=_trust_for_package(package))


def test_v3_toolchain_rejects_duplicate_manifest_keys_when_outer_hash_is_resigned(
    tmp_path: Path,
    production_env: Path,
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)
    archive_path = package / QUALIFICATION_TOOLCHAIN_ARCHIVE
    with tarfile.open(archive_path, "r:gz") as archive:
        contents = {
            member.name: archive.extractfile(member).read()  # type: ignore[union-attr]
            for member in archive.getmembers()
        }
    original = contents[QUALIFICATION_TOOLCHAIN_MANIFEST]
    duplicate = original.replace(
        b'  "artifact_type": "ruisheng.qualification-toolchain",\n',
        b'  "artifact_type": "attacker",\n  "artifact_type": "ruisheng.qualification-toolchain",\n',
        1,
    )
    assert duplicate != original
    contents[QUALIFICATION_TOOLCHAIN_MANIFEST] = duplicate
    _write_canonical_qualification_archive(archive_path, contents)

    value = json.loads((package / "MANIFEST.json").read_text(encoding="utf-8"))
    value["qualification_toolchain"]["sha256"] = hashlib.sha256(
        archive_path.read_bytes()
    ).hexdigest()
    value["qualification_toolchain"]["toolchain_manifest"]["sha256"] = hashlib.sha256(
        duplicate
    ).hexdigest()
    parsed = release_artifacts._manifest_from_dict(value)
    value["logical_identity"] = compute_logical_identity(
        candidate_id=parsed.candidate_id,
        source_commit=parsed.source_commit,
        target_os=parsed.target_os,
        target_architecture=parsed.target_architecture,
        alembic_head=parsed.alembic_head,
        images=parsed.images,
        qualification_toolchain=parsed.qualification_toolchain,
    )
    _rewrite_authenticated_manifest(package, runner, value)

    with pytest.raises(ReleaseArtifactError, match="duplicate JSON object key"):
        verify_package(package, runner, trust_directory=_trust_for_package(package))


def test_v3_toolchain_rejects_oversized_internal_manifest_before_json_parse(
    tmp_path: Path,
    production_env: Path,
) -> None:
    runner = FakeRunner()
    package = _build(tmp_path, production_env, runner)
    archive_path = package / QUALIFICATION_TOOLCHAIN_ARCHIVE
    with tarfile.open(archive_path, "r:gz") as archive:
        contents = {
            member.name: archive.extractfile(member).read()  # type: ignore[union-attr]
            for member in archive.getmembers()
        }
    oversized = b'{"value":"' + b"x" * release_artifacts.MAX_RELEASE_JSON_BYTES + b'"}'
    contents[QUALIFICATION_TOOLCHAIN_MANIFEST] = oversized
    _write_canonical_qualification_archive(archive_path, contents)

    value = json.loads((package / "MANIFEST.json").read_text(encoding="utf-8"))
    value["qualification_toolchain"]["sha256"] = hashlib.sha256(
        archive_path.read_bytes()
    ).hexdigest()
    value["qualification_toolchain"]["toolchain_manifest"]["sha256"] = hashlib.sha256(
        oversized
    ).hexdigest()
    parsed = release_artifacts._manifest_from_dict(value)
    value["logical_identity"] = compute_logical_identity(
        candidate_id=parsed.candidate_id,
        source_commit=parsed.source_commit,
        target_os=parsed.target_os,
        target_architecture=parsed.target_architecture,
        alembic_head=parsed.alembic_head,
        images=parsed.images,
        qualification_toolchain=parsed.qualification_toolchain,
    )
    _rewrite_authenticated_manifest(package, runner, value)

    with pytest.raises(ReleaseArtifactError, match="not an allowed regular file"):
        verify_package(package, runner, trust_directory=_trust_for_package(package))


@pytest.mark.parametrize(
    "payload",
    (
        b'{"artifact_type":"first","artifact_type":"second"}',
        b'{"members":[{"path":"first","path":"second"}]}',
    ),
)
def test_release_json_loader_rejects_duplicate_keys_at_every_depth(payload: bytes) -> None:
    with pytest.raises(ReleaseArtifactError, match="duplicate JSON object key"):
        release_artifacts._read_json_object_bytes(payload, label="authenticated.json")


def test_release_json_loader_rejects_oversized_input() -> None:
    payload = b'{"value":"' + b"x" * release_artifacts.MAX_RELEASE_JSON_BYTES + b'"}'

    with pytest.raises(ReleaseArtifactError, match="byte limit"):
        release_artifacts._read_json_object_bytes(payload, label="oversized.json")


def test_release_json_loader_rejects_excessive_nesting() -> None:
    payload = b'{"value":' + b"[" * 2_000 + b"0" + b"]" * 2_000 + b"}"

    with pytest.raises(ReleaseArtifactError, match="invalid JSON file"):
        release_artifacts._read_json_object_bytes(payload, label="nested.json")


def test_release_json_loader_fails_closed_on_memory_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_memory_error(*_args: object, **_kwargs: object) -> object:
        raise MemoryError("injected parser exhaustion")

    monkeypatch.setattr(release_artifacts.json, "loads", raise_memory_error)

    with pytest.raises(ReleaseArtifactError, match="injected parser exhaustion"):
        release_artifacts._read_json_object_bytes(b"{}", label="exhausted.json")


def test_all_candidate_verifiers_declare_closed_v2_v3_toolchain_contract() -> None:
    for relative in (
        "deploy/verify-candidate.ps1",
        "deploy/verify-candidate.sh",
        "tools/release_trust/verify-publisher.ps1",
        "tools/release_trust/verify-publisher.sh",
    ):
        script = (ROOT / relative).read_text(encoding="utf-8")
        assert QUALIFICATION_TOOLCHAIN_ARCHIVE in script
        assert QUALIFICATION_TOOLCHAIN_MANIFEST in script
        assert SEMANTIC_VALIDATOR_ID in script
        assert "complete v2 or v3" in script
        assert "receipt_producer" in script
        assert "logical_identity does not match its immutable inputs" in script.lower()
        if relative.endswith(".sh"):
            assert 'type(manifest.get("schema_version")) is not int' in script


def test_all_candidate_verifiers_limit_internal_manifest_before_allocation() -> None:
    expected_snippets = {
        "deploy/verify-candidate.sh": (
            "if member.name == toolchain_manifest_name",
            "contents[member.name] = stream.read(member_limit + 1)",
        ),
        "tools/release_trust/verify-publisher.sh": (
            "if member.name == internal_name",
            "contents[member.name] = stream.read(member_limit + 1)",
        ),
        "deploy/verify-candidate.ps1": (
            "$MaximumBytesByName.ContainsKey($Name)",
            "$InternalName = $MaxReleaseJsonBytes",
        ),
        "tools/release_trust/verify-publisher.ps1": (
            "[string]$Entry.Name -ceq $InternalName",
            "$Entry.Length -gt $MemberLimit",
        ),
    }
    for relative, snippets in expected_snippets.items():
        script = (ROOT / relative).read_text(encoding="utf-8")
        for snippet in snippets:
            assert snippet in script


def test_posix_publisher_qualification_bootstrap_is_closed_and_compilable() -> None:
    script = (ROOT / "tools" / "release_trust" / "verify-publisher.sh").read_text(encoding="utf-8")
    here_doc_marker = "<<'PY'\n"

    assert script.count(here_doc_marker) == 1
    embedded, terminator, remainder = script.split(here_doc_marker, maxsplit=1)[1].partition(
        "\nPY\n"
    )
    assert terminator == "\nPY\n"
    assert not remainder.strip()
    compile(embedded, "verify-publisher.sh::<authenticated-python>", "exec")

    for mode in ("ValidatorSchema", "ValidatorProfile", "ValidatorLegacy", "Receipt"):
        assert mode in script
    assert "qualification-launcher.py" not in script
    assert '"--publisher-freshness-config"' not in script
    assert (
        'pathlib.Path(\n    "/etc/ruisheng/trust/point-profile-freshness-provider.json"\n)'
        in script
    )
    assert 'pathlib.Path("/opt/ruisheng/qualification-runtime")' in script
    assert 'pathlib.Path(\n    "/run/ruisheng/receipt-signing-agent.sock"\n)' in script
    assert '"PYTHONDONTWRITEBYTECODE": "1"' in script
    assert '"PYTHONNOUSERSITE": "1"' in script
    assert "env=environment" in script
    assert (
        '"-I",\n                "-B",\n                "-S",\n                "-X",\n                "utf8"'
        in script
    )

    authenticated = script.index(
        'print("[publisher] VERIFIED: publisher signature and complete candidate hashes passed")'
    )
    qualification_dispatch = script.index('if qualification_mode != "None":', authenticated)
    qualification_execution = script.index(
        "qualification_exit_code = execute_authenticated_qualification(", qualification_dispatch
    )
    assert authenticated < qualification_dispatch < qualification_execution


def test_posix_publisher_qualification_bootstrap_binds_resource_and_timeout_guards() -> None:
    script = (ROOT / "tools" / "release_trust" / "verify-publisher.sh").read_text(encoding="utf-8")

    assert "start_new_session=True" in script
    assert "os.killpg(outcome.pid, signal.SIGKILL)" in script
    assert "outcome.wait(timeout=30)" in script
    assert "if initial_position != 0 or archive_size > MAX_QUALIFICATION_GZIP_BYTES:" in script
    assert 'if gzip_header != b"\\x1f\\x8b\\x08\\x00\\x00\\x00\\x00\\x00\\x02\\xff":' in script
    assert "validate_single_qualification_gzip_member(raw_archive)" in script
    assert "zlib.error," in script

    process_wait = script.index("outcome.wait(timeout=timeout_seconds)")
    cleanup_finally = script.index("finally:", process_wait)
    group_termination = script.index("os.killpg(outcome.pid, signal.SIGKILL)", cleanup_finally)
    bounded_reap = script.index("outcome.wait(timeout=30)", group_termination)
    timeout_failure = script.index(
        'fail("authenticated qualification tool timed out")', bounded_reap
    )
    assert process_wait < cleanup_finally < group_termination < bounded_reap < timeout_failure

    budget_check = script.index("if len(directories) >= MAX_QUALIFICATION_RUNTIME_DIRECTORIES:")
    directory_add = script.index("directories.add(directory)", budget_check)
    assert budget_check < directory_add


def _posix_publisher_embedded_source() -> str:
    script = (ROOT / "tools" / "release_trust" / "verify-publisher.sh").read_text(encoding="utf-8")
    return script.split("<<'PY'\n", maxsplit=1)[1].partition("\nPY\n")[0]


def _load_posix_publisher_helpers(*names: str) -> dict[str, object]:
    import ast

    source = _posix_publisher_embedded_source()
    tree = ast.parse(source)
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.Import | ast.ImportFrom)
        or isinstance(node, ast.FunctionDef)
        and node.name in names
    ]
    namespace: dict[str, object] = {}
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), "publisher-helpers", "exec"), namespace
    )
    return namespace


def test_posix_linux_process_identity_parses_spaces_and_parentheses_in_comm() -> None:
    helpers = _load_posix_publisher_helpers("_linux_process_starttime")
    fields = ["S", *("0" for _ in range(18)), "987654", "0"]
    contents = "123 (provider ) worker) " + " ".join(fields)

    assert helpers["_linux_process_starttime"](contents) == 987654  # type: ignore[operator]


@pytest.mark.parametrize("failure", ("missing", "untrusted"))
def test_posix_freshness_provider_unavailable_or_untrusted_is_blocked(
    tmp_path: Path, failure: str
) -> None:
    provider = tmp_path / "provider"
    config = tmp_path / "provider-config.json"
    trust_root = tmp_path / "trust-root.json"
    config.write_text("{}", encoding="ascii")
    trust_root.write_text("{}", encoding="ascii")
    if failure == "untrusted":
        provider.write_text("provider", encoding="ascii")
    run_root = tmp_path / "run"
    run_root.mkdir()
    helpers = _load_posix_publisher_helpers("prepare_freshness_context")

    def protected(path: Path, _label: str) -> None:
        if path == provider:
            raise ValueError("provider is untrusted")

    helpers.update(
        {
            "FRESHNESS_PROVIDER": provider,
            "FRESHNESS_PROVIDER_CONFIG": config,
            "FRESHNESS_TRUST_ROOT": trust_root,
            "run_root": run_root,
            "protected_with_ancestors": protected,
            "_run_freshness_provider": lambda *_args: (_ for _ in ()).throw(
                AssertionError("unavailable provider must not execute")
            ),
        }
    )

    exit_code, _context = helpers["prepare_freshness_context"](  # type: ignore[operator]
        {"logical_identity": "sha256:" + "a" * 64}
    )

    assert exit_code == 2


def test_posix_freshness_snapshot_instability_fails_before_provider_execution(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider"
    config = tmp_path / "provider-config.json"
    trust_root = tmp_path / "trust-root.json"
    profile = tmp_path / "profile.json"
    policy = tmp_path / "policy.json"
    verifier = tmp_path / "verify-publisher.sh"
    for path in (provider, config, trust_root, profile, policy, verifier):
        path.write_text("{}", encoding="ascii")
    run_root = tmp_path / "run"
    run_root.mkdir()
    helpers = _load_posix_publisher_helpers("prepare_freshness_context")
    provider_called = False

    def lock_snapshot(
        _source: Path,
        _destination: Path,
        label: str,
        locks: list[object],
        _maximum_bytes: int,
        executable_snapshot: bool = False,
    ) -> bytes:
        del executable_snapshot
        locks.append(label)
        if label == "qualification profile":
            return json.dumps(
                {"profile_id": "profile-a", "payload_sha256": "sha256:" + "b" * 64}
            ).encode("ascii")
        return b"{}"

    def run_provider(*_args: object) -> int:
        nonlocal provider_called
        provider_called = True
        return 0

    helpers.update(
        {
            "FRESHNESS_PROVIDER": provider,
            "FRESHNESS_PROVIDER_CONFIG": config,
            "FRESHNESS_TRUST_ROOT": trust_root,
            "FRESHNESS_VERIFIER_ID": "publisher-v1",
            "MAX_RELEASE_JSON_BYTES": 4 * 1024 * 1024,
            "run_root": run_root,
            "verifier_input": verifier,
            "qualification_profile_path": str(profile),
            "qualification_trust_policy_path": str(policy),
            "protected_with_ancestors": lambda *_args: None,
            "_lock_snapshot_source": lock_snapshot,
            "_validate_freshness_locks": lambda _locks: (_ for _ in ()).throw(
                ValueError("snapshot changed")
            ),
            "_run_freshness_provider": run_provider,
            "strict_json_loads": json.loads,
            "absolute_qualification_argument": lambda value, _label: value,
        }
    )

    exit_code, _context = helpers["prepare_freshness_context"](  # type: ignore[operator]
        {"logical_identity": "sha256:" + "a" * 64}
    )

    assert exit_code == 3
    assert not provider_called


@pytest.mark.parametrize(
    ("preflight_exit", "decision"),
    ((2, "BLOCKED"), (3, "INVALID")),
)
def test_posix_freshness_preflight_failure_never_starts_qualification(
    tmp_path: Path, preflight_exit: int, decision: str
) -> None:
    helpers = _load_posix_publisher_helpers("execute_authenticated_qualification")
    run_root = tmp_path / "run"
    extraction = tmp_path / "extraction"
    run_root.mkdir()
    extraction.mkdir()
    process_calls: list[str] = []

    def run_process(*args: object, **_kwargs: object) -> tuple[int, bytes, bytes]:
        process_calls.append(str(args[4]))
        report = json.dumps({"decision": decision, "reason_code": "TEST"}).encode("ascii")
        return preflight_exit, report, b""

    helpers.update(
        {
            "qualification_mode": "ValidatorProfile",
            "run_root": run_root,
            "extract_authenticated_qualification_toolchain": lambda _identities: extraction,
            "validate_extracted_qualification": lambda *_args: (),
            "validate_posix_qualification_runtime": lambda _digest: (
                "runtime",
                "python",
                "dependencies",
            ),
            "prepare_freshness_context": lambda _manifest: (0, {"locks": []}),
            "freshness_preflight_invocation": lambda *_args: ("preflight", []),
            "_run_authenticated_qualification_process": run_process,
            "_validate_freshness_locks": lambda _locks: None,
            "_close_freshness_locks": lambda _locks: None,
            "strict_json_loads": json.loads,
            "qualification_invocation": lambda *_args: (_ for _ in ()).throw(
                AssertionError("qualification must not be constructed")
            ),
        }
    )

    result = helpers["execute_authenticated_qualification"](  # type: ignore[operator]
        {"logical_identity": "sha256:" + "a" * 64}, {"uv.lock": "a" * 64}
    )

    assert result == preflight_exit
    assert process_calls == ["preflight"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX nonblocking special-file contract")
def test_posix_freshness_open_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "attestation.fifo"
    os.mkfifo(fifo)
    helpers = _load_posix_publisher_helpers("_open_freshness_regular")

    started = time.monotonic()
    with pytest.raises(ValueError, match="bounded regular file"):
        helpers["_open_freshness_regular"](fifo, "freshness attestation", 1024)  # type: ignore[operator]

    assert time.monotonic() - started < 1


@pytest.mark.skipif(os.name != "posix", reason="Linux provider descendant containment")
def test_posix_freshness_provider_reaps_setsid_daemon(tmp_path: Path) -> None:
    marker = tmp_path / "escaped.txt"
    provider = tmp_path / "provider"
    provider.write_text(
        "#!/usr/bin/python3\n"
        "import os,pathlib,time\n"
        "if os.fork() == 0:\n"
        " pathlib.Path('/proc/self/comm').write_text('worker ) spaced\\n'); "
        "os.setsid(); time.sleep(0.5); pathlib.Path("
        + repr(str(marker))
        + ").write_text('escaped')\n"
        "raise SystemExit(0)\n",
        encoding="ascii",
    )
    provider.chmod(0o500)
    names = (
        "_linux_process_starttime",
        "_linux_process_identity",
        "_linux_direct_children",
        "_capture_linux_descendants",
        "_terminate_and_reap_linux_descendants",
        "_enable_linux_child_subreaper",
        "_run_freshness_provider",
    )
    helpers = _load_posix_publisher_helpers(*names)
    helpers["run_root"] = tmp_path
    helpers["FRESHNESS_PROVIDER_TIMEOUT_SECONDS"] = 2

    result = helpers["_run_freshness_provider"](provider, [])  # type: ignore[operator]
    time.sleep(0.75)

    assert result == 0
    assert not marker.exists()


def test_posix_freshness_provider_uses_locked_snapshots_and_full_cleanup() -> None:
    script = _posix_publisher_embedded_source()

    assert 'getattr(os, "O_NONBLOCK", 0)' in script
    assert (
        '"protected publisher verifier",\n            locks,\n            64 * 1024 * 1024'
        in script
    )
    assert (
        '"fixed freshness provider",\n                locks,\n                512 * 1024 * 1024'
        in script
    )
    assert "verifier_input.read_bytes()" not in script
    assert "[str(provider_snapshot), *arguments]" in script
    assert "_enable_linux_child_subreaper()" in script
    assert "_capture_linux_descendants" in script
    assert "_terminate_and_reap_linux_descendants" in script
    stable_recheck = script.index("_validate_freshness_locks(locks)")
    assert "provider_exit_code = _run_freshness_provider(provider_snapshot, arguments)" in script
    provider_start = script.index(
        "provider_exit_code = _run_freshness_provider(provider_snapshot, arguments)"
    )
    assert stable_recheck < provider_start
    assert 'request.get("verifier_id") != FRESHNESS_VERIFIER_ID' in script
    assert 'request.get("verifier_tool_sha256") != verifier_tool_sha256' in script
    provider_failure = script.index("if provider_exit_code != 0:")
    validator_process = script.index("outcome = subprocess.Popen(", provider_failure)
    qualification_invocation = script.index("qualification_invocation(", provider_failure)
    assert provider_failure < qualification_invocation < validator_process
    assert 'extraction / "tools/trust_root_freshness.py"' in script
    preflight_run = script.index("preflight, preflight_stdout")
    preflight_failure = script.index("if preflight != 0:", preflight_run)
    validator_invocation = script.index(
        "entrypoint, arguments, allowed_exit_codes = qualification_invocation(",
        preflight_failure,
    )
    assert preflight_run < preflight_failure < validator_invocation


def test_posix_freshness_context_hashes_the_locked_provider_config() -> None:
    script = _posix_publisher_embedded_source()

    provider_lock = script.index("_lock_snapshot_source(\n                FRESHNESS_PROVIDER,")
    config_lock = script.index(
        "config_contents = _lock_snapshot_source(\n            FRESHNESS_PROVIDER_CONFIG,",
        provider_lock,
    )
    context_digest = script.index(
        '"config_snapshot_sha256": (\n'
        '                "sha256:" + hashlib.sha256(config_contents).hexdigest()',
        config_lock,
    )

    assert provider_lock < config_lock < context_digest


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
def test_windows_freshness_timestamp_never_rounds_into_next_second() -> None:
    script = ROOT / "tools" / "release_trust" / "verify-publisher.ps1"
    command = r"""
$source = Get-Content -Raw -LiteralPath $env:RS_VERIFY_SCRIPT
$start = $source.IndexOf('function Get-CanonicalUtcTimestamp')
$end = $source.IndexOf('function Invoke-FixedFreshnessProvider', $start)
if ($start -lt 0 -or $end -lt 0) { throw 'timestamp helper not found' }
. ([scriptblock]::Create($source.Substring($start, $end - $start)))
$nearBoundary = [DateTimeOffset]::new(2026, 8, 30, 23, 59, 59, [TimeSpan]::Zero).AddTicks(9999999)
[Console]::Out.Write((Get-CanonicalUtcTimestamp $nearBoundary))
"""
    environment = os.environ.copy()
    environment["RS_VERIFY_SCRIPT"] = str(script)
    result = subprocess.run(
        [shutil.which("pwsh") or "pwsh", "-NoProfile", "-Command", command],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout == "2026-08-30T23:59:59.999999+00:00"


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize("failure", ("missing", "untrusted"))
def test_windows_freshness_provider_unavailable_or_untrusted_is_blocked(
    tmp_path: Path, failure: str
) -> None:
    result = _run_powershell_freshness_context_failure(tmp_path, failure)

    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout == "2"


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(
    ("context_exit", "preflight_exit", "expected_exit", "expected_process_calls"),
    (
        (2, 0, 2, 0),
        (0, 2, 2, 1),
        (0, 3, 3, 1),
    ),
)
def test_windows_freshness_failure_never_starts_qualification(
    tmp_path: Path,
    context_exit: int,
    preflight_exit: int,
    expected_exit: int,
    expected_process_calls: int,
) -> None:
    result = _run_powershell_freshness_dispatch(
        tmp_path,
        context_exit=context_exit,
        preflight_exit=preflight_exit,
    )
    marker = tmp_path / "qualification-calls.txt"
    calls = marker.read_text(encoding="utf-8").splitlines() if marker.exists() else []

    assert result.returncode == expected_exit, result.stderr or result.stdout
    assert calls == ["process"] * expected_process_calls
    assert "qualification" not in calls


def _find_test_bash() -> str | None:
    candidates: list[Path] = []
    discovered = shutil.which("bash")
    if discovered is not None:
        candidates.append(Path(discovered))
    git = shutil.which("git")
    if git is not None:
        git_root = Path(git).resolve().parent.parent
        candidates.extend((git_root / "bin" / "bash.exe", git_root / "usr" / "bin" / "bash.exe"))
    candidates.append(Path("C:/Git/bin/bash.exe"))

    observed: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in observed or not resolved.is_file():
            continue
        observed.add(resolved)
        probe = subprocess.run(
            [str(resolved), "--noprofile", "--norc", "-c", "exit 0"],
            check=False,
            capture_output=True,
            timeout=10,
        )
        if probe.returncode == 0:
            return str(resolved)
    return None


@pytest.mark.parametrize(
    ("arguments", "expected_error"),
    (
        (
            ("--qualification-profile-path", "attacker.py"),
            "qualification-only parameters require an explicit qualification mode",
        ),
        (
            (
                "--qualification-mode",
                "ValidatorSchema",
                "--qualification-profile-path",
                "attacker.py",
            ),
            "ValidatorSchema does not accept additional qualification parameters",
        ),
        (
            ("--qualification-mode", "Receipt"),
            "Receipt requires only output, signing identity, verifier ID, and verifier key ID",
        ),
        (
            (
                "--qualification-mode",
                "ValidatorSchema",
                "--qualification-mode",
                "ValidatorLegacy",
            ),
            "duplicate qualification mode",
        ),
    ),
)
def test_posix_publisher_rejects_open_ended_qualification_parameter_sets(
    arguments: tuple[str, ...], expected_error: str
) -> None:
    bash = _find_test_bash()
    if bash is None:
        pytest.skip("a usable Bash runtime is unavailable")
    script = ROOT / "tools" / "release_trust" / "verify-publisher.sh"

    result = subprocess.run(
        [bash, str(script), "missing-candidate", *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )

    output = result.stderr + result.stdout
    assert result.returncode != 0
    assert expected_error in output
    assert "/usr/bin/python3 is required" not in output


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
def test_windows_publisher_bootstrap_parses_without_errors() -> None:
    script = ROOT / "tools" / "release_trust" / "verify-publisher.ps1"
    command = r"""
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
    $env:RS_VERIFY_SCRIPT, [ref]$tokens, [ref]$errors
) | Out-Null
if ($errors.Count -ne 0) {
    $errors | ForEach-Object { Write-Error $_.Message }
    exit 1
}
"""
    environment = os.environ.copy()
    environment["RS_VERIFY_SCRIPT"] = str(script)

    result = subprocess.run(
        [shutil.which("pwsh") or "pwsh", "-NoProfile", "-Command", command],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    (
        ("rewrite", "candidate file content changed during snapshot"),
        ("replace", "identity or metadata changed"),
    ),
)
def test_windows_publisher_snapshot_rejects_identity_preserving_size_attacks(
    tmp_path: Path, mutation: str, expected_error: str
) -> None:
    result = _run_powershell_publisher_snapshot_mutation(tmp_path, mutation)

    assert result.returncode != 0
    assert expected_error in (result.stderr + result.stdout)
    assert not (tmp_path / "snapshot.bin").exists()


def test_windows_publisher_qualification_mode_is_a_closed_external_bootstrap() -> None:
    script = (ROOT / "tools" / "release_trust" / "verify-publisher.ps1").read_text(encoding="utf-8")

    assert (
        '[ValidateSet("None", "ValidatorSchema", "ValidatorProfile", "ValidatorLegacy", "Receipt")]'
    ) in script
    assert "QualificationArguments" not in script
    assert "--trust-directory" not in script
    assert '"tools/validate_device_point_profile.py"' in script
    assert '"tools/release_verification_receipt.py"' in script
    assert (
        '"sha256:$($AuthenticatedManifest.qualification_toolchain.receipt_producer.sha256)"'
    ) in script
    assert "Open-ProtectedSystemPython" in script
    assert r"C:\ProgramData\Ruisheng\runtime" in script
    assert "qualification-runtime-manifest.json" in script
    assert "Program Files\\Python31x\\python.exe" not in script
    assert '"-I", "-B", "-S", "-X", "utf8"' in script
    assert "sys.version_info[:2] != (3, 11)" in script
    assert "qualification dependency_root was not isolated for bootstrap" in script
    assert "ArgumentList.Add" in script
    assert "unsupported qualification entrypoint" in script
    bootstrap_start = script.index("$Bootstrap = @'\n") + len("$Bootstrap = @'\n")
    bootstrap_end = script.index("\n'@", bootstrap_start)
    compile(
        script[bootstrap_start:bootstrap_end],
        "verify-publisher.ps1::<qualification-bootstrap>",
        "exec",
    )
    assert 'Entrypoint = "tools/trust_root_freshness.py"' in script
    assert '"--provider-config-snapshot", $FreshnessContext.ConfigSnapshot.Path' in script
    assert "$AttestationValue.request.verifier_id -cne $FreshnessVerifierId" in script
    assert (
        "$AttestationValue.request.verifier_tool_sha256 -cne `\n"
        '                "sha256:$($Verifier.ExpectedSha256)"'
    ) in script
    freshness_recheck = script.index("Assert-FreshnessLocksUnchanged $Context")
    provider_start = script.index("$ProviderExitCode = Invoke-FixedFreshnessProvider")
    assert freshness_recheck < provider_start
    dispatch = script.index('if ($QualificationMode -eq "ValidatorProfile") {', bootstrap_end)
    preflight = script.index("$PreflightResult = Invoke-AuthenticatedQualification", dispatch)
    preflight_exit = script.index("if ($PreflightResult.ExitCode -ne 0)", preflight)
    validator_construct = script.index("$Invocation = Get-QualificationInvocation", preflight_exit)
    validator_start = script.index(
        "$QualificationResult = Invoke-AuthenticatedQualification", validator_construct
    )
    assert dispatch < preflight < preflight_exit < validator_construct < validator_start
    job_create = script.index("[Ruisheng.ReleaseTrust.KillOnCloseJob]::Create($JobName)")
    process_start = script.index("$Process = [Diagnostics.Process]::Start($Start)", job_create)
    job_assignment = script.index("$Job.Assign($Process.SafeHandle)", process_start)
    gate_release = script.index("$Gate.Set()", job_assignment)
    process_exit = script.index("WaitForProcessExit(", gate_release)
    job_termination = script.index("$Job.Terminate(1)", process_exit)
    bounded_collection = script.index(
        "30000 - [int]$CleanupClock.ElapsedMilliseconds", job_termination
    )
    runtime_recheck = script.index(
        "Assert-ProtectedQualificationRuntimeUnchanged $Runtime", bounded_collection
    )
    runtime_disposal = script.index("foreach ($RuntimeLock in $Runtime.Locks)", runtime_recheck)
    assert job_create < process_start < job_assignment < gate_release < process_exit
    assert process_exit < job_termination < bounded_collection < runtime_recheck < runtime_disposal
    assert "open_event(0x00100000, False, gate_name)" in script
    assert "process assignment to qualification job was not effective" in script
    assert '$Start.Environment["RUISHENG_QUALIFICATION_GATE"] = $GateName' in script
    assert "RUISHENG_QUALIFICATION_COMPLETE_${Code}" in script
    assert "RUISHENG_QUALIFICATION_HOLD" in script
    assert "DescendantProcessSet]::Capture" in script


def _run_windows_publisher_process_containment(
    tmp_path: Path, *, mode: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    marker = tmp_path / f"windows-publisher-descendant-{mode}.txt"
    child = tmp_path / "qualification-descendant.py"
    child.write_text(
        "import os,pathlib,sys,time\n"
        "pathlib.Path(sys.argv[1]+'.ppid').write_text(str(os.getppid()))\n"
        "time.sleep(1)\n"
        "pathlib.Path(sys.argv[1]).write_text('escaped', encoding='ascii')\n",
        encoding="ascii",
    )
    parent = tmp_path / "gated-qualification-parent.py"
    parent.write_text(
        "import ctypes,os,subprocess,sys,time\n"
        "gate_name=os.environ.pop('RUISHENG_QUALIFICATION_GATE')\n"
        "kernel32=ctypes.WinDLL('kernel32',use_last_error=True)\n"
        "gate=kernel32.OpenEventW(0x00100000,False,gate_name)\n"
        "assert gate and kernel32.WaitForSingleObject(gate,30000)==0\n"
        "assert kernel32.CloseHandle(gate)\n"
        "subprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]])\n"
        "if sys.argv[3]=='timeout': time.sleep(60)\n"
        "completion=kernel32.OpenEventW("
        "0x0002,False,os.environ['RUISHENG_QUALIFICATION_COMPLETE_0'])\n"
        "hold=kernel32.OpenEventW("
        "0x00100000,False,os.environ['RUISHENG_QUALIFICATION_HOLD'])\n"
        "assert completion and hold and kernel32.SetEvent(completion)\n"
        "kernel32.WaitForSingleObject(hold,0xffffffff)\n",
        encoding="ascii",
    )
    command = r"""
function Fail([string]$Message) { throw $Message }
$source = Get-Content -Raw -LiteralPath $env:RS_VERIFY_SCRIPT
$begin = $source.IndexOf('# BEGIN qualification process containment helpers')
$end = $source.IndexOf('# END qualification process containment helpers', $begin)
if ($begin -lt 0 -or $end -lt 0) { throw 'qualification containment block not found' }
. ([scriptblock]::Create($source.Substring($begin, $end - $begin)))
$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName = $env:RS_PYTHON
$start.UseShellExecute = $false
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
foreach ($argument in @($env:RS_PARENT, $env:RS_CHILD, $env:RS_MARKER, $env:RS_MODE)) {
    [void]$start.ArgumentList.Add($argument)
}
$start.Environment.Clear()
$timeout = if ($env:RS_MODE -ceq 'timeout') { 200 } else { 30000 }
$result = Invoke-GatedQualificationProcess $start $timeout
if ($result.StandardError) { [Console]::Error.Write($result.StandardError) }
[Console]::Out.Write(
    "EXIT=$($result.ExitCode);CAPTURE=$($result.CapturedDescendantCount);" +
    "LATE=$($result.LateDescendantCount);ROOT=$($result.RootProcessId)"
)
"""
    environment = os.environ.copy()
    environment.update(
        {
            "RS_VERIFY_SCRIPT": str(ROOT / "tools/release_trust/verify-publisher.ps1"),
            "RS_PYTHON": sys.executable,
            "RS_PARENT": str(parent),
            "RS_CHILD": str(child),
            "RS_MARKER": str(marker),
            "RS_MODE": mode,
        }
    )
    result = subprocess.run(
        [shutil.which("pwsh") or "pwsh", "-NoProfile", "-Command", command],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=45,
    )
    return result, marker


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None,
    reason="Windows PowerShell 7 process containment contract",
)
@pytest.mark.parametrize("mode", ("normal", "timeout"))
def test_windows_publisher_terminates_qualification_descendants_on_every_exit(
    tmp_path: Path, mode: str
) -> None:
    result, marker = _run_windows_publisher_process_containment(tmp_path, mode=mode)

    if mode == "normal":
        assert result.returncode == 0, result.stderr or result.stdout
        assert result.stdout.startswith("EXIT=0;"), result.stderr
    else:
        assert result.returncode != 0
        assert "qualification tool timed out" in (result.stderr + result.stdout)
    time.sleep(1.5)
    ppid_path = Path(str(marker) + ".ppid")
    details = result.stdout + result.stderr
    if ppid_path.exists():
        details += ";CHILD_PPID=" + ppid_path.read_text()
    assert not marker.exists(), details


def test_windows_publisher_qualification_runtime_budgets_precede_allocation_and_hashing() -> None:
    script = (ROOT / "tools" / "release_trust" / "verify-publisher.ps1").read_text(encoding="utf-8")
    assert "$MaxQualificationRuntimeFiles = [Int64]32768" in script
    assert "$MaxQualificationRuntimeDirectories = [Int64]32768" in script
    assert "$MaxQualificationRuntimeFileBytes = [Int64]536870912" in script
    assert "$MaxQualificationRuntimeTotalBytes = [Int64]34359738368" in script
    assert "$MaxQualificationRuntimePathBytes = [Int64]4096" in script

    runtime_start = script.index("function Open-ProtectedSystemPython")
    count_guard = script.index(
        "Assert-QualificationRuntimeManifestFileCount $Manifest.files", runtime_start
    )
    expected_files_allocation = script.index(
        "$ExpectedFiles = [Collections.Generic.HashSet[string]]::new", count_guard
    )
    layout_preflight = script.index("Assert-QualificationRuntimeLayout", count_guard)
    runtime_lock_allocation = script.index(
        "$RuntimeFileLocks = [Collections.Generic.List[object]]::new()", layout_preflight
    )
    aggregate_guard = script.index(
        "$TotalBytes = Add-QualificationRuntimeFileBytes", runtime_lock_allocation
    )
    hash_loop = script.index("foreach ($Lock in $RuntimeFileLocks)", aggregate_guard)
    first_runtime_hash = script.index("Get-LockedFileSha256 $Lock.Stream", hash_loop)
    assert count_guard < expected_files_allocation < layout_preflight
    assert layout_preflight < runtime_lock_allocation < aggregate_guard < first_runtime_hash

    directory_helper = script.index("function Add-ExpectedQualificationRuntimeDirectory")
    directory_guard = script.index(
        "$ExpectedDirectories.Count -ge $MaxQualificationRuntimeDirectories",
        directory_helper,
    )
    directory_allocation = script.index("$CaseInsensitiveMembers.Add(", directory_guard)
    assert directory_guard < directory_allocation


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
def test_windows_publisher_accepts_32768_total_runtime_files() -> None:
    result = _run_powershell_qualification_runtime_budget("file-count-boundary")

    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout == "ACCEPTED"


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(
    ("action", "expected_error"),
    (
        ("file-count", "qualification runtime manifest files are invalid"),
        ("single-file", "qualification runtime synthetic file exceeds its byte limit"),
        ("aggregate", "qualification runtime exceeds its aggregate byte limit"),
        ("int64-overflow", "qualification runtime exceeds its aggregate byte limit"),
        ("directory", "qualification runtime contains too many directories"),
        ("path", "qualification runtime file path is not a canonical relative path"),
    ),
)
def test_windows_publisher_rejects_qualification_runtime_budget_overflow(
    action: str, expected_error: str
) -> None:
    result = _run_powershell_qualification_runtime_budget(action)

    assert result.returncode != 0
    assert expected_error in (result.stderr + result.stdout)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(
    ("action", "lock_target", "marker"),
    (
        ("verify", "python.exe", "VERIFIED"),
        ("lock", "qualification-runtime-manifest.json", "LOCKED"),
        ("lock", "python.exe", "LOCKED"),
        ("lock", "Lib/site-packages/dependency.py", "LOCKED"),
    ),
)
def test_windows_publisher_accepts_only_the_manifest_bound_fixed_runtime(
    tmp_path: Path, action: str, lock_target: str, marker: str
) -> None:
    runtime, _, uv_lock_sha256 = _write_minimal_qualification_runtime(tmp_path)

    result = _run_powershell_qualification_runtime(
        runtime, uv_lock_sha256, action=action, lock_target=lock_target
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout == marker


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("python_version", "3.12"),
        ("uv_lock_sha256", "b" * 64),
        ("dependency_root", "../outside"),
        ("files", {"path": "python.exe", "sha256": "a" * 64}),
    ),
)
def test_windows_publisher_rejects_invalid_qualification_runtime_manifest_contract(
    tmp_path: Path, field: str, value: object
) -> None:
    runtime, manifest, uv_lock_sha256 = _write_minimal_qualification_runtime(tmp_path)
    manifest[field] = value
    _write_qualification_runtime_manifest(runtime, manifest)

    result = _run_powershell_qualification_runtime(runtime, uv_lock_sha256)

    assert result.returncode != 0
    assert "qualification runtime manifest contract is invalid" in (result.stderr + result.stdout)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    (
        ("manifest-extra-key", "qualification runtime manifest keys mismatch"),
        ("file-extra-key", "qualification runtime file identity keys mismatch"),
        ("unordered-files", "qualification runtime files are not in strict ordinal path order"),
    ),
)
def test_windows_publisher_rejects_noncanonical_qualification_runtime_manifest(
    tmp_path: Path, mutation: str, expected_error: str
) -> None:
    runtime, manifest, uv_lock_sha256 = _write_minimal_qualification_runtime(tmp_path)
    if mutation == "manifest-extra-key":
        manifest["unexpected"] = True
    else:
        identities = cast(list[dict[str, object]], manifest["files"])
        if mutation == "file-extra-key":
            identities[0]["unexpected"] = True
        elif mutation == "unordered-files":
            identities.reverse()
        else:  # pragma: no cover - parametrization is closed above
            raise AssertionError(f"unsupported mutation: {mutation}")
    _write_qualification_runtime_manifest(runtime, manifest)

    result = _run_powershell_qualification_runtime(runtime, uv_lock_sha256)

    assert result.returncode != 0
    assert expected_error in (result.stderr + result.stdout)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    (
        ("extra-file", "qualification runtime file allowlist mismatch"),
        ("missing-file", "qualification runtime file allowlist mismatch"),
        ("empty-directory", "qualification runtime file allowlist mismatch"),
        ("same-length-rewrite", "qualification runtime file SHA-256 mismatch"),
    ),
)
def test_windows_publisher_rejects_qualification_runtime_layout_or_content_drift(
    tmp_path: Path, mutation: str, expected_error: str
) -> None:
    runtime, _, uv_lock_sha256 = _write_minimal_qualification_runtime(tmp_path)
    dependency = runtime / "Lib" / "site-packages" / "dependency.py"
    if mutation == "extra-file":
        (runtime / "attacker.py").write_bytes(b"unmanifested\n")
    elif mutation == "missing-file":
        dependency.unlink()
    elif mutation == "empty-directory":
        (runtime / "unused").mkdir()
    elif mutation == "same-length-rewrite":
        dependency.write_bytes(b"X" * dependency.stat().st_size)
    else:  # pragma: no cover - parametrization is closed above
        raise AssertionError(f"unsupported mutation: {mutation}")

    result = _run_powershell_qualification_runtime(runtime, uv_lock_sha256)

    assert result.returncode != 0
    assert expected_error in (result.stderr + result.stdout)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
def test_windows_publisher_rejects_case_ambiguous_qualification_runtime_directories(
    tmp_path: Path,
) -> None:
    runtime, manifest, uv_lock_sha256 = _write_minimal_qualification_runtime(tmp_path)
    _add_qualification_runtime_file(
        runtime, manifest, "lib/site-packages/attacker.py", b"ATTACK = True\n"
    )

    result = _run_powershell_qualification_runtime(runtime, uv_lock_sha256)

    assert result.returncode != 0
    assert "qualification runtime contains a case-insensitive path collision" in (
        result.stderr + result.stdout
    )


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize("relative", ("attacker.pth", "pyvenv.cfg"))
def test_windows_publisher_rejects_python_path_injection_files(
    tmp_path: Path, relative: str
) -> None:
    runtime, manifest, uv_lock_sha256 = _write_minimal_qualification_runtime(tmp_path)
    _add_qualification_runtime_file(runtime, manifest, relative, b"import site\n")

    result = _run_powershell_qualification_runtime(runtime, uv_lock_sha256)

    assert result.returncode != 0
    assert f"qualification runtime contains a forbidden file: {relative}" in (
        result.stderr + result.stdout
    )


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(
    ("configuration", "expected_error"),
    (
        (b"import site\n", "qualification runtime Python _pth must not import site"),
        (
            b"Lib/site-packages\n",
            "qualification runtime dependency_root must be added only by the bootstrap",
        ),
    ),
)
def test_windows_publisher_rejects_unsafe_python_pth_configuration(
    tmp_path: Path, configuration: bytes, expected_error: str
) -> None:
    runtime, manifest, uv_lock_sha256 = _write_minimal_qualification_runtime(tmp_path)
    _add_qualification_runtime_file(runtime, manifest, "python311._pth", configuration)

    result = _run_powershell_qualification_runtime(runtime, uv_lock_sha256)

    assert result.returncode != 0
    assert expected_error in (result.stderr + result.stdout)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
def test_windows_publisher_rejects_hard_linked_qualification_runtime_files(
    tmp_path: Path,
) -> None:
    runtime, manifest, uv_lock_sha256 = _write_minimal_qualification_runtime(tmp_path)
    python = runtime / "python.exe"
    dll = runtime / "python311.dll"
    dll.unlink()
    os.link(python, dll)
    for identity in cast(list[dict[str, str]], manifest["files"]):
        if identity["path"] == "python311.dll":
            identity["sha256"] = hashlib.sha256(python.read_bytes()).hexdigest()
    _write_qualification_runtime_manifest(runtime, manifest)

    result = _run_powershell_qualification_runtime(runtime, uv_lock_sha256)

    assert result.returncode != 0
    assert "qualification runtime file has multiple hard links" in (result.stderr + result.stdout)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(
    ("guard", "expected_error"),
    (("acl", "unsafe ACL"), ("ancestor", "unsafe ancestor")),
)
def test_windows_publisher_checks_runtime_acl_and_ancestors(
    tmp_path: Path, guard: str, expected_error: str
) -> None:
    runtime, _, uv_lock_sha256 = _write_minimal_qualification_runtime(tmp_path)

    result = _run_powershell_qualification_runtime(runtime, uv_lock_sha256, fail_guard=guard)

    assert result.returncode != 0
    assert expected_error in (result.stderr + result.stdout)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
def test_windows_publisher_rejects_runtime_reparse_points(tmp_path: Path) -> None:
    runtime, _, uv_lock_sha256 = _write_minimal_qualification_runtime(tmp_path)
    target = tmp_path / "junction-target"
    target.mkdir()
    junction = runtime / "junction"
    created = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(target)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if created.returncode != 0:
        pytest.skip(created.stderr or created.stdout or "cannot create test junction")
    try:
        result = _run_powershell_qualification_runtime(runtime, uv_lock_sha256)
        assert result.returncode != 0
        assert "fixed qualification runtime contains a reparse point" in (
            result.stderr + result.stdout
        )
    finally:
        junction.rmdir()


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(
    ("mode", "entrypoint", "argument_prefix"),
    (
        ("ValidatorSchema", "tools/validate_device_point_profile.py", ["schema"]),
        (
            "ValidatorProfile",
            "tools/trust_root_freshness.py",
            ["qualify"],
        ),
        (
            "ValidatorLegacy",
            "tools/validate_device_point_profile.py",
            ["validate-legacy"],
        ),
        ("Receipt", "tools/release_verification_receipt.py", None),
    ),
)
def test_windows_publisher_builds_only_fixed_qualification_invocations(
    tmp_path: Path,
    mode: str,
    entrypoint: str,
    argument_prefix: list[str] | None,
) -> None:
    result = _run_powershell_qualification_invocation(tmp_path, mode)

    assert result.returncode == 0, result.stderr or result.stdout
    invocation = json.loads(result.stdout)
    assert invocation["Entrypoint"] == entrypoint
    arguments = invocation["Arguments"]
    if argument_prefix is not None:
        assert arguments[: len(argument_prefix)] == argument_prefix
        if mode == "ValidatorProfile":
            assert arguments == [
                "qualify",
                str(tmp_path / "freshness" / "profile.json"),
                "--trust-policy",
                str(tmp_path / "freshness" / "trust-policy.json"),
                "--trust-root-snapshot",
                str(tmp_path / "freshness" / "trust-root.json"),
                "--provider-config-snapshot",
                str(tmp_path / "freshness" / "provider-config.json"),
                "--attestation",
                str(tmp_path / "freshness" / "attestation.json"),
                "--challenge",
                "d" * 43,
                "--requested-at",
                "2026-08-30T00:00:00+00:00",
                "--candidate-logical-identity",
                "sha256:" + "b" * 64,
                "--expected-trust-root-snapshot-sha256",
                "sha256:" + "c" * 64,
                "--expected-provider-config-snapshot-sha256",
                "sha256:" + "f" * 64,
                "--expected-attestation-sha256",
                "sha256:" + "9" * 64,
                "--evidence-root",
                str((tmp_path / "evidence").resolve()),
            ]
    else:
        assert arguments == [
            str((tmp_path / "candidate").resolve()),
            "--output-directory",
            str((tmp_path / "receipts").resolve()),
            "--signing-identity",
            str((tmp_path / "release-receipt.pub").resolve()),
            "--verifier-id",
            "protected-release-verifier",
            "--verifier-key-id",
            "release-receipt-key",
            "--verifier-tool-sha256",
            "sha256:" + "a" * 64,
        ]
    assert "--trust-directory" not in arguments


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(
    ("arguments", "expected_error"),
    (
        (
            ("-QualificationProfilePath", "attacker.py"),
            "qualification-only parameters require an explicit qualification mode",
        ),
        (
            (
                "-QualificationMode",
                "ValidatorSchema",
                "-QualificationProfilePath",
                "attacker.py",
            ),
            "ValidatorSchema does not accept additional qualification parameters",
        ),
        (
            ("-QualificationMode", "Receipt"),
            "Receipt requires only output, signing identity, verifier ID, and verifier key ID",
        ),
    ),
)
def test_windows_publisher_rejects_open_ended_qualification_parameter_sets(
    arguments: tuple[str, ...], expected_error: str
) -> None:
    script = ROOT / "tools" / "release_trust" / "verify-publisher.ps1"

    result = subprocess.run(
        [
            shutil.which("pwsh") or "pwsh",
            "-NoProfile",
            "-File",
            str(script),
            "missing-candidate",
            *arguments,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )

    assert result.returncode != 0
    assert expected_error in (result.stderr + result.stdout)
