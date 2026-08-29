"""Release verification receipt production contracts."""

from __future__ import annotations

import base64
import errno
import hashlib
import inspect
import io
import json
import os
import tarfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, BinaryIO, cast

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tools import release_artifacts, release_verification_receipt
from tools.release_artifacts import (
    COMPONENTS,
    CandidateManifest,
    CommandOutcome,
    ReleaseArtifactError,
    build_candidate,
    compute_logical_identity,
    render_manifest_markdown,
)
from tools.release_verification_receipt import (
    RECEIPT_FILE_SUFFIX,
    VERIFIER_TOOL_ID,
    produce_release_verification_receipt,
)
from tools.validate_device_point_profile import (
    ReleaseVerificationReceipt,
    ReleaseVerifierTrustKey,
    RuntimeTarget,
    TrustPolicy,
    _verify_release_receipt,
)

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "a" * 40
CANDIDATE_ID = "deploy-20260827.1"
ALEMBIC_HEAD = "0012_alarm_notification_runtime"
NOW = datetime(2026, 8, 27, 4, 0, tzinfo=UTC)
FAKE_SSH_KEYGEN = Path("C:/Windows/System32/OpenSSH/ssh-keygen.exe")


class FixedReceiptDateTime:
    @classmethod
    def now(cls, timezone: object) -> datetime:
        del cls
        assert timezone is UTC
        return NOW


def _ssh_string(value: bytes) -> bytes:
    return len(value).to_bytes(4, "big") + value


def _decode_ssh_string(value: bytes, offset: int) -> tuple[bytes, int]:
    length = int.from_bytes(value[offset : offset + 4], "big")
    start = offset + 4
    end = start + length
    if end > len(value):
        raise ValueError("truncated SSH string")
    return value[start:end], end


def _public_blob(key: Ed25519PrivateKey) -> bytes:
    raw = key.public_key().public_bytes_raw()
    return _ssh_string(b"ssh-ed25519") + _ssh_string(raw)


def _public_line(key: Ed25519PrivateKey, comment: str) -> bytes:
    encoded = base64.b64encode(_public_blob(key))
    return b"ssh-ed25519 " + encoded + b" " + comment.encode("ascii") + b"\n"


def _write_sshsig(
    path: Path,
    payload: bytes,
    key: Ed25519PrivateKey,
    namespace: str,
) -> None:
    namespace_bytes = namespace.encode("ascii")
    hash_algorithm = b"sha512"
    signed_payload = (
        b"SSHSIG"
        + _ssh_string(namespace_bytes)
        + _ssh_string(b"")
        + _ssh_string(hash_algorithm)
        + _ssh_string(hashlib.sha512(payload).digest())
    )
    signature_blob = _ssh_string(b"ssh-ed25519") + _ssh_string(key.sign(signed_payload))
    binary = (
        b"SSHSIG"
        + (1).to_bytes(4, "big")
        + _ssh_string(_public_blob(key))
        + _ssh_string(namespace_bytes)
        + _ssh_string(b"")
        + _ssh_string(hash_algorithm)
        + _ssh_string(signature_blob)
    )
    encoded = base64.b64encode(binary)
    body = b"\n".join(encoded[index : index + 70] for index in range(0, len(encoded), 70))
    path.write_bytes(b"-----BEGIN SSH SIGNATURE-----\n" + body + b"\n-----END SSH SIGNATURE-----\n")


def _read_armored_sshsig(path: Path) -> bytes:
    value = path.read_bytes()
    header = b"-----BEGIN SSH SIGNATURE-----\n"
    footer = b"-----END SSH SIGNATURE-----\n"
    if not value.startswith(header) or not value.endswith(footer):
        raise ValueError("invalid SSHSIG armor")
    return base64.b64decode(value[len(header) : -len(footer)].replace(b"\n", b""), validate=True)


def _verify_sshsig(
    path: Path,
    payload: bytes,
    expected_public_blob: bytes,
    expected_namespace: str,
) -> None:
    binary = _read_armored_sshsig(path)
    if not binary.startswith(b"SSHSIG") or int.from_bytes(binary[6:10], "big") != 1:
        raise ValueError("invalid SSHSIG header")
    public_blob, offset = _decode_ssh_string(binary, 10)
    namespace, offset = _decode_ssh_string(binary, offset)
    reserved, offset = _decode_ssh_string(binary, offset)
    hash_algorithm, offset = _decode_ssh_string(binary, offset)
    signature_blob, offset = _decode_ssh_string(binary, offset)
    if (
        offset != len(binary)
        or public_blob != expected_public_blob
        or namespace.decode("ascii") != expected_namespace
        or reserved
        or hash_algorithm != b"sha512"
    ):
        raise ValueError("SSHSIG contract mismatch")
    key_type, key_offset = _decode_ssh_string(public_blob, 0)
    raw_public_key, key_offset = _decode_ssh_string(public_blob, key_offset)
    signature_type, signature_offset = _decode_ssh_string(signature_blob, 0)
    raw_signature, signature_offset = _decode_ssh_string(signature_blob, signature_offset)
    if (
        key_type != b"ssh-ed25519"
        or signature_type != b"ssh-ed25519"
        or key_offset != len(public_blob)
        or signature_offset != len(signature_blob)
    ):
        raise ValueError("SSHSIG key contract mismatch")
    signed_payload = (
        b"SSHSIG"
        + _ssh_string(namespace)
        + _ssh_string(b"")
        + _ssh_string(b"sha512")
        + _ssh_string(hashlib.sha512(payload).digest())
    )
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(raw_public_key).verify(raw_signature, signed_payload)
    except InvalidSignature as error:
        raise ValueError("invalid SSHSIG signature") from error


def _key_from_identity(path: Path, keys: Mapping[bytes, Ed25519PrivateKey]) -> Ed25519PrivateKey:
    fields = path.read_bytes().rstrip(b"\n").split(maxsplit=2)
    blob = base64.b64decode(fields[1], validate=True)
    return keys[blob]


def _allowed_public_blob(path: Path) -> bytes:
    fields = path.read_bytes().rstrip(b"\n").split(maxsplit=2)
    return base64.b64decode(fields[2], validate=True)


class FakeRunner:
    """Deterministic Docker/OpenSSH runner with real Ed25519 SSHSIG payloads."""

    def __init__(self, keys: Mapping[bytes, Ed25519PrivateKey]) -> None:
        self.keys = dict(keys)
        self.commands: list[tuple[str, ...]] = []
        self.images: dict[str, dict[str, object]] = {}
        self.configs: dict[str, bytes] = {}
        self.loaded: list[Path] = []
        self.fail_receipt_signature = False
        self.fail_candidate_cleanup = False
        self.drift_component: str | None = None
        self.on_first_load: Callable[[], None] | None = None
        self.swap_api_tag_after_inspect = False
        self._load_hook_ran = False
        self._add_source("timescale/timescaledb:2.16.1-pg15", "postgres")
        self._add_source("redis:7-alpine", "redis")

    def _add_source(self, reference: str, component: str) -> None:
        config = json.dumps(
            {"architecture": "amd64", "component": component, "os": "linux"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        image_id = "sha256:" + hashlib.sha256(config).hexdigest()
        repository = reference.rsplit(":", maxsplit=1)[0]
        self.images[reference] = {
            "Architecture": "amd64",
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
        del cwd, timeout_seconds, inherit_environment
        command = tuple(str(arg) for arg in args)
        self.commands.append(command)
        if Path(command[0]).name.casefold() in {"ssh-keygen", "ssh-keygen.exe"}:
            namespace = command[command.index("-n") + 1]
            if command[1:3] == ("-Y", "sign"):
                if self.fail_receipt_signature and namespace.endswith("receipt-v1"):
                    raise ReleaseArtifactError("injected receipt signature failure")
                identity = Path(command[command.index("-f") + 1])
                target = Path(command[-1])
                _write_sshsig(
                    target.with_name(target.name + ".sig"),
                    target.read_bytes(),
                    _key_from_identity(identity, self.keys),
                    namespace,
                )
                return "Signing file"
            if command[1:3] == ("-Y", "verify"):
                allowed_signers = Path(command[command.index("-f") + 1])
                signature = Path(command[command.index("-s") + 1])
                assert input_bytes is not None
                _verify_sshsig(
                    signature,
                    input_bytes,
                    _allowed_public_blob(allowed_signers),
                    namespace,
                )
                return "Good signature"
        if command == ("git", "rev-parse", "HEAD"):
            return COMMIT
        if command[:4] == ("git", "rev-parse", "--verify", "--end-of-options"):
            if command[4] == "HEAD^{commit}":
                return COMMIT
            commit, relative = command[4].split(":", maxsplit=1)
            assert commit == COMMIT
            contents = (ROOT / relative).read_bytes()
            return hashlib.sha1(  # noqa: S324 - Git's object format uses SHA-1.
                b"blob " + str(len(contents)).encode() + b"\0" + contents,
                usedforsecurity=False,
            ).hexdigest()
        if command[:2] == ("git", "rev-parse") and ":" in command[2]:
            commit, relative = command[2].split(":", maxsplit=1)
            assert commit == COMMIT
            contents = (ROOT / relative).read_bytes()
            return hashlib.sha1(  # noqa: S324 - Git's object format uses SHA-1.
                b"blob " + str(len(contents)).encode() + b"\0" + contents,
                usedforsecurity=False,
            ).hexdigest()
        if command == ("git", "hash-object", "--stdin"):
            assert input_bytes is not None
            return hashlib.sha1(  # noqa: S324 - Git's object format uses SHA-1.
                b"blob " + str(len(input_bytes)).encode() + b"\0" + input_bytes,
                usedforsecurity=False,
            ).hexdigest()
        if command[:3] == ("git", "status", "--porcelain"):
            return ""
        if command[:4] == ("docker", "image", "pull", "--platform"):
            return command[-1]
        if command[:3] == ("docker", "image", "tag"):
            source, destination = command[3], command[4]
            source_value = self.images[source]
            source_tags = source_value["RepoTags"]
            assert isinstance(source_tags, list)
            self.images[destination] = {
                **source_value,
                "RepoTags": sorted({*(str(value) for value in source_tags), destination}),
            }
            self.configs[destination] = self.configs[source]
            return ""
        if command[:4] == ("docker", "image", "rm", "--force"):
            if self.fail_candidate_cleanup:
                raise ReleaseArtifactError("injected candidate tag cleanup failure")
            self.images.pop(command[4], None)
            self.configs.pop(command[4], None)
            return ""
        if command[:2] == ("docker", "compose") and "build" in command:
            assert env is not None
            for component in ("api", "gw", "web"):
                self._add_source(env[f"{component.upper()}_IMAGE"], component)
            return ""
        if command[:3] == ("docker", "image", "inspect"):
            requested_image_id = command[3]
            reference = requested_image_id
            if requested_image_id.startswith("sha256:"):
                matches = [
                    name
                    for name, config in self.configs.items()
                    if "sha256:" + hashlib.sha256(config).hexdigest() == requested_image_id
                ]
                reference = next(
                    (name for name in matches if name.startswith("ruisheng-candidate/")),
                    matches[0],
                )
            value = dict(self.images[reference])
            component = json.loads(self.configs[reference])["component"]
            assert isinstance(component, str)
            if component == self.drift_component:
                value["Id"] = "sha256:" + "f" * 64
            if component == "api" and self.swap_api_tag_after_inspect:
                self.images[reference] = {**value, "Id": "sha256:" + "e" * 64}
            return json.dumps(value, sort_keys=True)
        if len(command) >= 3 and command[1:3] == ("-m", "alembic"):
            return f"{ALEMBIC_HEAD} (head)"
        if command[:3] == ("docker", "version", "--format"):
            return "29.4.0/29.4.0"
        if command == ("docker", "compose", "version", "--short"):
            return "5.1.1"
        if command == ("git", "--version"):
            return "git version 2.51.0"
        if command[:3] == ("docker", "image", "load"):
            archive_path = Path(command[-1])
            self.loaded.append(archive_path)
            with tarfile.open(archive_path, "r:gz") as archive:
                manifest_stream = archive.extractfile("manifest.json")
                assert manifest_stream is not None
                manifest = json.load(manifest_stream)
                assert isinstance(manifest, list) and len(manifest) == 1
                entry = manifest[0]
                assert isinstance(entry, dict)
                config_name = entry["Config"]
                assert isinstance(config_name, str)
                config_stream = archive.extractfile(config_name)
                assert config_stream is not None
                config = config_stream.read()
                config_value = json.loads(config)
                image_id = "sha256:" + hashlib.sha256(config).hexdigest()
                for reference in entry["RepoTags"]:
                    self.images[reference] = {
                        "Architecture": config_value["architecture"],
                        "Id": image_id,
                        "Os": config_value["os"],
                        "RepoDigests": [],
                        "RepoTags": [reference],
                    }
                    self.configs[reference] = config
            if not self._load_hook_ran and self.on_first_load is not None:
                self._load_hook_ran = True
                self.on_first_load()
            return "Loaded image"
        if command[:2] == ("docker", "run"):
            raise AssertionError("receipt verification must not execute candidate image code")
        if command[:2] == ("docker", "compose") and command[-2:] == ("config", "--images"):
            values = _read_env(Path(command[command.index("--env-file") + 1]))
            return "\n".join(
                (
                    values["POSTGRES_IMAGE"],
                    values["REDIS_IMAGE"],
                    values["API_IMAGE"],
                    values["API_IMAGE"],
                    values["GW_IMAGE"],
                    values["WEB_IMAGE"],
                )
            )
        if command[:2] == ("docker", "compose") and command[-3:] == (
            "config",
            "--format",
            "json",
        ):
            values = _read_env(Path(command[command.index("--env-file") + 1]))
            references = {
                "postgres": values["POSTGRES_IMAGE"],
                "redis": values["REDIS_IMAGE"],
                "migrate": values["API_IMAGE"],
                "api": values["API_IMAGE"],
                "gw": values["GW_IMAGE"],
                "web": values["WEB_IMAGE"],
            }
            return json.dumps(
                {
                    "services": {
                        name: {
                            "image": image,
                            "platform": values["TARGET_PLATFORM"],
                            "pull_policy": "never",
                        }
                        for name, image in references.items()
                    }
                }
            )
        raise AssertionError(f"unexpected fake command: {command}")

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
    ) -> CommandOutcome:
        assert not isolate_process_tree
        return CommandOutcome(
            stdout=self.run(
                args,
                cwd=cwd,
                env=env,
                input_bytes=input_bytes,
                timeout_seconds=timeout_seconds,
                inherit_environment=inherit_environment,
            ),
            stderr="",
            returncode=0,
        )

    def save_image(self, image: str, destination: Path, *, cwd: Path) -> None:
        del cwd
        config = self.configs[image]
        image_id = hashlib.sha256(config).hexdigest()
        component = json.loads(config)["component"]
        layer_name = "migration-layer.tar" if component == "api" else None
        manifest = json.dumps(
            [
                {
                    "Config": f"{image_id}.json",
                    "Layers": [layer_name] if layer_name is not None else [],
                    "RepoTags": [image],
                }
            ],
            sort_keys=True,
        ).encode()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(destination, "w:gz") as archive:
            _add_tar_bytes(archive, "manifest.json", manifest)
            _add_tar_bytes(archive, f"{image_id}.json", config)
            if layer_name is not None:
                layer_buffer = io.BytesIO()
                with tarfile.open(fileobj=layer_buffer, mode="w") as layer:
                    _add_tar_bytes(
                        layer,
                        "app/alembic.ini",
                        b"[alembic]\nscript_location = alembic\n",
                    )
                    _add_tar_bytes(
                        layer,
                        f"app/alembic/versions/{ALEMBIC_HEAD}.py",
                        (
                            f'revision = "{ALEMBIC_HEAD}"\ndown_revision = None\n'
                            "branch_labels = None\ndepends_on = None\n"
                        ).encode("ascii"),
                    )
                _add_tar_bytes(archive, layer_name, layer_buffer.getvalue())

    def image_exists(self, image: str, *, cwd: Path) -> bool:
        del cwd
        return image in self.images


def _add_tar_bytes(archive: tarfile.TarFile, name: str, contents: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(contents)
    member.mtime = 0
    archive.addfile(member, io.BytesIO(contents))


def _layer_stream_with_member(member: tarfile.TarInfo, contents: bytes = b"") -> io.BytesIO:
    layer_buffer = io.BytesIO()
    with tarfile.open(fileobj=layer_buffer, mode="w") as layer:
        source = io.BytesIO(contents) if member.isfile() else None
        layer.addfile(member, source)
    layer_buffer.seek(0)
    return layer_buffer


def _oversized_tar_extension_stream(extension_type: bytes) -> io.BytesIO:
    member = tarfile.TarInfo("extension-metadata")
    member.type = extension_type
    member.size = release_artifacts.MAX_DOCKER_ARCHIVE_MEMBER_BYTES + 1
    member.mtime = 0
    member.uid = 0
    member.gid = 0
    member.uname = ""
    member.gname = ""
    member.mode = 0o644
    return io.BytesIO(member.tobuf(format=tarfile.GNU_FORMAT))


def _apply_test_image_layer(
    member: tarfile.TarInfo,
    *,
    contents: bytes = b"",
    files: dict[PurePosixPath, bytes] | None = None,
) -> tuple[dict[PurePosixPath, bytes], list[int]]:
    layer_files = {} if files is None else files
    overlay_directives_seen = [0]
    release_verification_receipt._apply_image_layer(
        _layer_stream_with_member(member, contents),
        archive_path=Path("api.tar.gz"),
        layer_name="layer.tar",
        files=layer_files,
        migration_bytes_seen=[0],
        expanded_bytes_seen=[0],
        layer_members_seen=[0],
        overlay_directives_seen=overlay_directives_seen,
    )
    return layer_files, overlay_directives_seen


def _write_static_api_archive(
    path: Path,
    layers: Sequence[Mapping[str, bytes]],
) -> None:
    config = json.dumps(
        {"architecture": "amd64", "component": "api", "os": "linux"},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    config_name = hashlib.sha256(config).hexdigest() + ".json"
    layer_values: list[tuple[str, bytes]] = []
    for index, entries in enumerate(layers):
        layer_buffer = io.BytesIO()
        with tarfile.open(fileobj=layer_buffer, mode="w") as layer:
            for name, contents in entries.items():
                _add_tar_bytes(layer, name, contents)
        layer_values.append((f"layer-{index}.tar", layer_buffer.getvalue()))
    manifest = json.dumps(
        [
            {
                "Config": config_name,
                "Layers": [name for name, _contents in layer_values],
                "RepoTags": ["ruisheng-candidate/test/api:fixture"],
            }
        ],
        sort_keys=True,
    ).encode()
    with tarfile.open(path, "w:gz") as archive:
        _add_tar_bytes(archive, "manifest.json", manifest)
        _add_tar_bytes(archive, config_name, config)
        for name, contents in layer_values:
            _add_tar_bytes(archive, name, contents)


def test_receipt_archive_rejects_oversized_manifest(tmp_path: Path) -> None:
    archive = tmp_path / "oversized-manifest.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        _add_tar_bytes(
            output,
            "manifest.json",
            b"[" + b" " * release_artifacts.MAX_RELEASE_JSON_BYTES + b"]",
        )

    with pytest.raises(ReleaseArtifactError, match="JSON byte limit"):
        release_verification_receipt._image_migration_files(archive)


@pytest.mark.parametrize(
    "extension_type",
    (tarfile.XHDTYPE, tarfile.GNUTYPE_LONGNAME),
    ids=("pax", "gnu-longname"),
)
def test_receipt_layer_preflight_rejects_oversized_extension_before_payload_read(
    monkeypatch: pytest.MonkeyPatch,
    extension_type: bytes,
) -> None:
    discarded: list[int] = []

    def record_discard(_stream: BinaryIO, size: int, *, label: str) -> None:
        del label
        discarded.append(size)

    monkeypatch.setattr(release_artifacts, "_discard_tar_bytes", record_discard)

    with pytest.raises(ReleaseArtifactError, match="forbidden tar extension metadata"):
        release_verification_receipt._apply_image_layer(
            _oversized_tar_extension_stream(extension_type),
            archive_path=Path("api.tar.gz"),
            layer_name="layer.tar",
            files={},
            migration_bytes_seen=[0],
            expanded_bytes_seen=[0],
            layer_members_seen=[0],
            overlay_directives_seen=[0],
        )

    assert discarded == []


def test_receipt_archive_rejects_duplicate_outer_members(tmp_path: Path) -> None:
    archive = tmp_path / "duplicate-members.tar.gz"
    manifest = b"[]"
    with tarfile.open(archive, "w:gz") as output:
        _add_tar_bytes(output, "manifest.json", manifest)
        _add_tar_bytes(output, "manifest.json", manifest)

    with pytest.raises(ReleaseArtifactError, match="duplicate members"):
        release_verification_receipt._image_migration_files(archive)


def test_receipt_layer_rejects_expanded_byte_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release_verification_receipt, "MAX_DOCKER_ARCHIVE_TOTAL_BYTES", 128)
    archive = tmp_path / "expanded-budget.tar.gz"
    _write_static_api_archive(
        archive,
        [{"unrelated-padding": b"x" * 256}],
    )

    with pytest.raises(ReleaseArtifactError, match="byte budget"):
        release_verification_receipt._image_migration_files(archive)


def test_receipt_layers_share_one_global_member_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "api.tar.gz"
    _write_static_api_archive(
        archive,
        [
            {"first": b"", "second": b""},
            {"third": b"", "fourth": b""},
        ],
    )
    monkeypatch.setattr(release_verification_receipt, "MAX_LAYER_MEMBERS", 3)

    with pytest.raises(ReleaseArtifactError, match="global member budget"):
        release_verification_receipt._image_migration_files(archive)


def test_receipt_layers_bound_overlay_directive_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "api.tar.gz"
    _write_static_api_archive(
        archive,
        [
            {
                "app/alembic/versions/.wh.first.py": b"",
                "app/alembic/versions/.wh.second.py": b"",
            }
        ],
    )
    monkeypatch.setattr(release_verification_receipt, "MAX_OVERLAY_DIRECTIVES", 1)

    with pytest.raises(ReleaseArtifactError, match="overlay directive budget"):
        release_verification_receipt._image_migration_files(archive)


def _read_env(path: Path) -> dict[str, str]:
    return {
        key: value
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
        for key, separator, value in (line.partition("="),)
        if separator
    }


def _write_release_trust(root: Path, key: Ed25519PrivateKey) -> tuple[Path, Path, str]:
    trust = root / "release-trust"
    trust.mkdir()
    blob = _public_blob(key)
    encoded = base64.b64encode(blob).decode("ascii")
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode(
        "ascii"
    ).rstrip("=")
    (trust / "release-allowed-signers").write_text(
        f"ruisheng-release ssh-ed25519 {encoded}\n",
        encoding="ascii",
        newline="\n",
    )
    (trust / "release-key-fingerprint").write_text(
        fingerprint + "\n", encoding="ascii", newline="\n"
    )
    identity = root / "publisher-agent.pub"
    identity.write_bytes(_public_line(key, "publisher-agent"))
    return trust, identity, fingerprint


@dataclass(frozen=True)
class CandidateFixture:
    package: Path
    manifest: CandidateManifest
    output: Path
    runner: FakeRunner
    trust: Path
    verifier_identity: Path
    verifier_key: Ed25519PrivateKey
    publisher_identity: Path
    publisher_fingerprint: str

    @property
    def verifier_tool_sha256(self) -> str:
        descriptor = self.manifest.qualification_toolchain
        assert descriptor is not None
        digest = descriptor.receipt_producer.sha256
        assert isinstance(digest, str)
        return "sha256:" + digest


def _non_lock_outputs(path: Path) -> list[Path]:
    return [entry for entry in path.iterdir() if not entry.name.endswith(".candidate-tags.lock")]


@pytest.fixture
def candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> CandidateFixture:
    monkeypatch.setattr(release_verification_receipt, "datetime", FixedReceiptDateTime)
    monkeypatch.setattr(
        release_artifacts, "_validate_atomic_publish_root", lambda path: path.resolve()
    )
    monkeypatch.setattr(
        release_verification_receipt,
        "_validate_atomic_publish_root",
        lambda path: path.resolve(),
    )
    monkeypatch.setattr(release_artifacts, "_system_ssh_keygen", lambda: FAKE_SSH_KEYGEN)
    monkeypatch.setattr(
        release_verification_receipt,
        "_system_ssh_keygen",
        lambda: FAKE_SSH_KEYGEN,
    )
    publisher_key = Ed25519PrivateKey.generate()
    verifier_key = Ed25519PrivateKey.generate()
    keys = {
        _public_blob(publisher_key): publisher_key,
        _public_blob(verifier_key): verifier_key,
    }
    runner = FakeRunner(keys)
    trust, publisher_identity, publisher_fingerprint = _write_release_trust(tmp_path, publisher_key)
    verifier_identity = tmp_path / "receipt-verifier-agent.pub"
    verifier_identity.write_bytes(_public_line(verifier_key, "receipt-verifier-agent"))
    env_file = tmp_path / ".env.prod"
    env_file.write_text("SAFE=not-copied\n", encoding="utf-8")
    package = build_candidate(
        root=ROOT,
        output_root=tmp_path / "candidates",
        candidate_id=CANDIDATE_ID,
        target_platform="linux/amd64",
        env_file=env_file,
        postgres_source="timescale/timescaledb:2.16.1-pg15",
        redis_source="redis:7-alpine",
        runner=runner,
        signing_identity=publisher_identity,
        trust_directory=trust,
        lock_root=tmp_path / "locks",
    )
    output = tmp_path / "receipts"
    output.mkdir()
    manifest = release_artifacts.verify_package(package, runner, trust_directory=trust)
    for image in manifest.images:
        runner.images.pop(image.candidate_reference, None)
        runner.configs.pop(image.candidate_reference, None)
    return CandidateFixture(
        package=package,
        manifest=manifest,
        output=output,
        runner=runner,
        trust=trust,
        verifier_identity=verifier_identity,
        verifier_key=verifier_key,
        publisher_identity=publisher_identity,
        publisher_fingerprint=publisher_fingerprint,
    )


def _produce(
    candidate: CandidateFixture,
    *,
    require_system_trust: bool = False,
) -> Path:
    return produce_release_verification_receipt(
        package=candidate.package,
        output_directory=candidate.output,
        runner=candidate.runner,
        trust_directory=candidate.trust,
        signing_identity=candidate.verifier_identity,
        verifier_id="protected-release-verifier",
        verifier_key_id="release-receipt-key",
        verifier_tool_sha256=candidate.verifier_tool_sha256,
        require_system_trust=require_system_trust,
    )


def _downgrade_candidate_to_v2(candidate: CandidateFixture) -> None:
    manifest_path = candidate.package / "MANIFEST.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    images = release_artifacts._manifest_from_dict(value).images
    value["schema_version"] = 2
    value.pop("qualification_toolchain")
    value["logical_identity"] = compute_logical_identity(
        candidate_id=value["candidate_id"],
        source_commit=value["source_commit"],
        target_os=value["target_os"],
        target_architecture=value["target_architecture"],
        alembic_head=value["alembic_head"],
        images=images,
    )
    parsed = release_artifacts._manifest_from_dict(value)
    manifest_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (candidate.package / "MANIFEST.md").write_text(
        render_manifest_markdown(parsed),
        encoding="utf-8",
        newline="\n",
    )
    (candidate.package / release_artifacts.QUALIFICATION_TOOLCHAIN_ARCHIVE).unlink()
    hashed = {
        path.relative_to(candidate.package).as_posix()
        for path in candidate.package.rglob("*")
        if path.is_file() and path.name not in {"SHA256SUMS", "SHA256SUMS.sig"}
    }
    release_artifacts._write_sha256sums(candidate.package, tuple(hashed))
    trust = release_artifacts._load_release_trust(candidate.trust)
    release_artifacts._sign_sha256sums(
        candidate.package,
        candidate.publisher_identity,
        trust,
        candidate.runner,
    )


def _validator_result(
    candidate: CandidateFixture,
    receipt_path: Path,
) -> tuple[ReleaseVerificationReceipt | None, Any]:
    contents = receipt_path.read_bytes()
    document = json.loads(contents)
    binding = {
        "receipt_id": document["receipt_id"],
        "path": f"release/{receipt_path.name}",
        "sha256": "sha256:" + hashlib.sha256(contents).hexdigest(),
        "size_bytes": len(contents),
    }
    images = {image["component"]: image["image_id"] for image in document["images"]}
    target = RuntimeTarget.model_validate(
        {
            "source_commit": document["source_commit"],
            "candidate_id": document["candidate_id"],
            "logical_identity": document["logical_identity"],
            "api_image_digest": images["api"],
            "gateway_image_digest": images["gw"],
            "alembic_head": document["alembic_head"],
            "release_verification_receipt": binding,
        }
    )
    public_key = base64.b64encode(candidate.verifier_key.public_key().public_bytes_raw()).decode(
        "ascii"
    )
    verifier_key = ReleaseVerifierTrustKey.model_validate(
        {
            "verifier_id": "protected-release-verifier",
            "key_id": "release-receipt-key",
            "public_key": public_key,
            "tool_id": VERIFIER_TOOL_ID,
            "tool_sha256": candidate.verifier_tool_sha256,
            "publisher_key_fingerprints": [candidate.publisher_fingerprint],
            "valid_from": "2026-08-27T00:00:00+00:00",
            "expires_at": "2026-08-28T00:00:00+00:00",
            "revocation_sequence": 0,
            "status": "active",
        }
    )
    policy = cast(
        TrustPolicy,
        SimpleNamespace(
            valid_from="2026-08-27T00:00:00+00:00",
            expires_at="2026-08-28T00:00:00+00:00",
            revocation_sequence=0,
            status="active",
            release_verifier_keys=[verifier_key],
        ),
    )
    return _verify_release_receipt(target, contents, policy)


def test_green_candidate_produces_validator_accepted_receipt(candidate: CandidateFixture) -> None:
    receipt_path = _produce(candidate)

    assert receipt_path == candidate.output / f"{CANDIDATE_ID}{RECEIPT_FILE_SUFFIX}"
    receipt = ReleaseVerificationReceipt.model_validate_json(receipt_path.read_bytes())
    assert receipt.verifier_tool_id == VERIFIER_TOOL_ID
    assert receipt.verifier_tool_sha256 == candidate.verifier_tool_sha256
    assert receipt.release_key_fingerprint == candidate.publisher_fingerprint
    assert [image.component for image in receipt.images] == list(COMPONENTS)
    assert len(candidate.runner.loaded) == 5
    assert not any(command[:2] == ("docker", "run") for command in candidate.runner.commands)
    assert not any(
        command[:4] == ("docker", "image", "rm", "--force") for command in candidate.runner.commands
    )
    assert all(
        candidate.runner.image_exists(image.candidate_reference, cwd=candidate.output)
        for image in candidate.manifest.images
    )
    verified, reason = _validator_result(candidate, receipt_path)
    assert verified is not None
    assert reason is None
    assert not list(candidate.output.glob(".*.tmp-*"))


def test_failed_receipt_retains_preexisting_tag_and_rolls_back_new_tags(
    candidate: CandidateFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_image = next(image for image in candidate.manifest.images if image.component == "api")
    candidate.runner.run(
        [
            "docker",
            "image",
            "load",
            "--input",
            str(candidate.package / api_image.archive),
        ],
        cwd=candidate.package,
    )
    monkeypatch.setattr(
        release_verification_receipt,
        "_observed_alembic_head",
        lambda _archive: "wrong_head",
    )

    with pytest.raises(ReleaseArtifactError, match="statically observed.*does not match"):
        _produce(candidate)

    assert candidate.runner.image_exists(api_image.candidate_reference, cwd=candidate.output)
    assert all(
        not candidate.runner.image_exists(image.candidate_reference, cwd=candidate.output)
        for image in candidate.manifest.images
        if image.component != "api"
    )


def test_candidate_reference_cleanup_failure_blocks_receipt(
    candidate: CandidateFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate.runner.fail_candidate_cleanup = True
    monkeypatch.setattr(
        release_verification_receipt,
        "_observed_alembic_head",
        lambda _archive: "wrong_head",
    )

    with pytest.raises(ReleaseArtifactError, match="statically observed.*does not match") as raised:
        _produce(candidate)

    assert any(
        "candidate reference cleanup failed" in note
        for note in getattr(raised.value, "__notes__", ())
    )
    assert _non_lock_outputs(candidate.output) == []


def test_default_verified_at_is_captured_after_runtime_checks(
    candidate: CandidateFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_offset = len(candidate.runner.commands)
    observed_at = datetime(2026, 8, 27, 5, 6, 7, tzinfo=UTC)

    class ObservedDateTime:
        @classmethod
        def now(cls, timezone: object) -> datetime:
            del cls
            assert timezone is UTC
            commands = candidate.runner.commands[command_offset:]
            inspections = [
                command[3] for command in commands if command[:3] == ("docker", "image", "inspect")
            ]
            assert inspections[: 2 * len(COMPONENTS)] == [
                value
                for image in candidate.manifest.images
                for value in (image.image_id, image.candidate_reference)
            ]
            assert inspections[2 * len(COMPONENTS) :] == []
            assert sum(command[:3] == ("docker", "image", "load") for command in commands) == len(
                COMPONENTS
            )
            assert sum(command[:2] == ("docker", "run") for command in commands) == 0
            assert not any(
                command[:4] == ("docker", "image", "rm", "--force") for command in commands
            )
            return observed_at

    monkeypatch.setattr(release_verification_receipt, "datetime", ObservedDateTime)

    receipt_path = produce_release_verification_receipt(
        package=candidate.package,
        output_directory=candidate.output,
        runner=candidate.runner,
        trust_directory=candidate.trust,
        signing_identity=candidate.verifier_identity,
        verifier_id="protected-release-verifier",
        verifier_key_id="release-receipt-key",
        verifier_tool_sha256=candidate.verifier_tool_sha256,
        require_system_trust=False,
    )

    document = json.loads(receipt_path.read_bytes())
    assert document["verified_at"] == observed_at.isoformat(timespec="seconds")


def test_receipt_api_does_not_accept_caller_supplied_verification_time() -> None:
    assert "verified_at" not in inspect.signature(produce_release_verification_receipt).parameters


def test_v2_candidate_is_rejected_before_image_load_or_receipt_signing(
    candidate: CandidateFixture,
) -> None:
    _downgrade_candidate_to_v2(candidate)
    command_offset = len(candidate.runner.commands)

    with pytest.raises(
        ReleaseArtifactError,
        match="no signed qualification toolchain descriptor",
    ):
        _produce(candidate)

    commands = candidate.runner.commands[command_offset:]
    assert not any(command[:3] == ("docker", "image", "load") for command in commands)
    assert not any(
        Path(command[0]).name.casefold() in {"ssh-keygen", "ssh-keygen.exe"}
        and command[1:3] == ("-Y", "sign")
        for command in commands
    )
    assert _non_lock_outputs(candidate.output) == []


def test_receipt_tampering_is_rejected_by_validator(candidate: CandidateFixture) -> None:
    receipt_path = _produce(candidate)
    document = json.loads(receipt_path.read_bytes())
    document["candidate_id"] = "tampered-candidate"
    tampered = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    receipt_path.write_bytes(tampered)

    _verified, reason = _validator_result(candidate, receipt_path)

    assert reason is not None
    assert reason.code == "RELEASE_VERIFICATION_RECEIPT_SIGNATURE_INVALID"


def test_authenticated_candidate_tampering_fails_without_receipt(
    candidate: CandidateFixture,
) -> None:
    (candidate.package / "nginx.conf").write_bytes(b"tampered")

    with pytest.raises(ReleaseArtifactError, match="SHA-256 mismatch for nginx.conf"):
        _produce(candidate)

    assert _non_lock_outputs(candidate.output) == []


@pytest.mark.parametrize(
    "output_location",
    ("inside-package", "contains-package"),
)
def test_candidate_and_receipt_output_must_be_bidirectionally_disjoint(
    candidate: CandidateFixture,
    output_location: str,
) -> None:
    if output_location == "inside-package":
        output = candidate.package / "receipts"
        output.mkdir()
    else:
        output = candidate.package.parent
    command_count = len(candidate.runner.commands)

    with pytest.raises(ReleaseArtifactError, match="candidate must be path-disjoint"):
        produce_release_verification_receipt(
            package=candidate.package,
            output_directory=output,
            runner=candidate.runner,
            trust_directory=candidate.trust,
            signing_identity=candidate.verifier_identity,
            verifier_id="protected-release-verifier",
            verifier_key_id="release-receipt-key",
            verifier_tool_sha256=candidate.verifier_tool_sha256,
            require_system_trust=False,
        )

    assert len(candidate.runner.commands) == command_count
    assert not (output / f"{CANDIDATE_ID}{RECEIPT_FILE_SUFFIX}").exists()


def test_wrong_statically_observed_migration_head_fails_closed(
    candidate: CandidateFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        release_verification_receipt,
        "_observed_alembic_head",
        lambda _archive: "wrong_head",
    )

    with pytest.raises(ReleaseArtifactError, match="statically observed.*does not match"):
        _produce(candidate)

    assert _non_lock_outputs(candidate.output) == []
    assert all(
        not candidate.runner.image_exists(image.candidate_reference, cwd=candidate.output)
        for image in candidate.manifest.images
    )


def test_migration_head_is_computed_from_static_image_layers(tmp_path: Path) -> None:
    archive = tmp_path / "api.tar.gz"
    _write_static_api_archive(
        archive,
        [
            {
                "app/alembic.ini": b"[alembic]\nscript_location = alembic\n",
                "app/alembic/versions/base.py": (
                    b'revision = "base"\ndown_revision = None\n'
                    b"branch_labels = None\ndepends_on = None\n"
                ),
                "app/alembic/versions/old_head.py": (
                    b'revision = "old_head"\ndown_revision = "base"\n'
                    b"branch_labels = None\ndepends_on = None\n"
                ),
            },
            {
                "app/alembic/versions/.wh.old_head.py": b"",
                "app/alembic/versions/new_head.py": (
                    b'revision = "new_head"\ndown_revision = "base"\n'
                    b"branch_labels = None\ndepends_on = None\n"
                ),
            },
        ],
    )

    assert release_verification_receipt._observed_alembic_head(archive) == "new_head"


@pytest.mark.parametrize("whiteout_first", (False, True))
@pytest.mark.parametrize("opaque", (False, True))
def test_layer_whiteout_only_removes_inherited_files(
    tmp_path: Path,
    whiteout_first: bool,
    opaque: bool,
) -> None:
    archive = tmp_path / "api.tar.gz"
    whiteout = "app/alembic/versions/.wh..wh..opq" if opaque else "app/alembic/versions/.wh.head.py"
    replacement = (
        b'revision = "replacement"\ndown_revision = None\nbranch_labels = None\ndepends_on = None\n'
    )
    entries = [(whiteout, b""), ("app/alembic/versions/head.py", replacement)]
    if not whiteout_first:
        entries.reverse()
    _write_static_api_archive(
        archive,
        [
            {
                "app/alembic.ini": b"[alembic]\nscript_location = alembic\n",
                "app/alembic/versions/head.py": (
                    b'revision = "inherited"\ndown_revision = None\n'
                    b"branch_labels = None\ndepends_on = None\n"
                ),
            },
            dict(entries),
        ],
    )

    assert release_verification_receipt._observed_alembic_head(archive) == "replacement"


@pytest.mark.parametrize(
    ("whiteout_name", "removed_name"),
    (
        ("app/alembic/versions/.wh.head.py", "head.py"),
        ("app/alembic/versions/.wh..wh..opq", "head.py"),
    ),
)
@pytest.mark.parametrize("member_type", (tarfile.REGTYPE, tarfile.AREGTYPE))
def test_layer_accepts_zero_length_regular_whiteout(
    whiteout_name: str,
    removed_name: str,
    member_type: bytes,
) -> None:
    removed = release_verification_receipt.IMAGE_MIGRATION_ROOT / removed_name
    member = tarfile.TarInfo(whiteout_name)
    member.type = member_type
    member.size = 0

    files, overlay_directives_seen = _apply_test_image_layer(
        member,
        files={removed: b"inherited"},
    )

    assert files == {}
    assert overlay_directives_seen == [1]


@pytest.mark.parametrize(
    ("whiteout_name", "message"),
    (
        ("app/alembic/versions/.wh.", "whiteout has an empty target"),
        ("app/alembic/versions/.wh..", "whiteout has an invalid target"),
        ("app/alembic/versions/.wh...", "whiteout has an invalid target"),
    ),
)
def test_layer_rejects_whiteout_with_noncanonical_target_before_overlay_mutation(
    whiteout_name: str,
    message: str,
) -> None:
    member = tarfile.TarInfo(whiteout_name)
    inherited = release_verification_receipt.IMAGE_MIGRATION_ROOT / "head.py"
    files = {inherited: b"inherited"}
    overlay_directives_seen = [0]

    with pytest.raises(ReleaseArtifactError, match=message):
        release_verification_receipt._apply_image_layer(
            _layer_stream_with_member(member),
            archive_path=Path("api.tar.gz"),
            layer_name="layer.tar",
            files=files,
            migration_bytes_seen=[0],
            expanded_bytes_seen=[0],
            layer_members_seen=[0],
            overlay_directives_seen=overlay_directives_seen,
        )

    assert files == {inherited: b"inherited"}
    assert overlay_directives_seen == [0]


@pytest.mark.parametrize(
    "whiteout_name",
    (
        "app/alembic/versions/.wh.head.py",
        "app/alembic/versions/.wh..wh..opq",
    ),
)
@pytest.mark.parametrize(
    ("member_type", "contents"),
    (
        (tarfile.SYMTYPE, b""),
        (tarfile.LNKTYPE, b""),
        (tarfile.DIRTYPE, b""),
        (tarfile.CHRTYPE, b""),
        (tarfile.BLKTYPE, b""),
        (tarfile.FIFOTYPE, b""),
        (tarfile.CONTTYPE, b""),
        (tarfile.GNUTYPE_SPARSE, b""),
        (tarfile.REGTYPE, b"payload"),
    ),
)
def test_layer_rejects_noncanonical_whiteout_before_overlay_mutation(
    whiteout_name: str,
    member_type: bytes,
    contents: bytes,
) -> None:
    member = tarfile.TarInfo(whiteout_name)
    member.type = member_type
    member.size = len(contents)
    if member_type in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
        member.linkname = "app/alembic/versions/head.py"
    inherited = release_verification_receipt.IMAGE_MIGRATION_ROOT / "head.py"
    files = {inherited: b"inherited"}
    overlay_directives_seen = [0]
    expected_error = (
        "forbidden tar extension metadata"
        if member_type == tarfile.GNUTYPE_SPARSE
        else "whiteout is not a zero-length regular file"
    )

    with pytest.raises(
        ReleaseArtifactError,
        match=expected_error,
    ):
        release_verification_receipt._apply_image_layer(
            _layer_stream_with_member(member, contents),
            archive_path=Path("api.tar.gz"),
            layer_name="layer.tar",
            files=files,
            migration_bytes_seen=[0],
            expanded_bytes_seen=[0],
            layer_members_seen=[0],
            overlay_directives_seen=overlay_directives_seen,
        )

    assert files == {inherited: b"inherited"}
    assert overlay_directives_seen == [0]


@pytest.mark.parametrize(
    ("extra_source", "message"),
    (
        (b'revision += "changed"\n', "modifies revision"),
        (b'if True:\n    revision = "changed"\n', "modifies revision"),
        (b'globals()["revision"] = "changed"\n', "non-name module assignment target"),
        (b'depends_on = "other"\n', "assign depends_on exactly once"),
    ),
)
def test_static_migration_contract_rejects_import_time_metadata_rewrites(
    tmp_path: Path,
    extra_source: bytes,
    message: str,
) -> None:
    archive = tmp_path / "api.tar.gz"
    _write_static_api_archive(
        archive,
        [
            {
                "app/alembic.ini": b"[alembic]\nscript_location = alembic\n",
                "app/alembic/versions/head.py": (
                    b'revision = "head"\ndown_revision = None\n'
                    b"branch_labels = None\ndepends_on = None\n" + extra_source
                ),
            }
        ],
    )

    with pytest.raises(ReleaseArtifactError, match=message):
        release_verification_receipt._observed_alembic_head(archive)


@pytest.mark.parametrize(
    "extra_source",
    (
        b"import os as revision\n",
        b"from os import revision\n",
        b"from os import environ as revision\n",
        b"def revision():\n    pass\n",
        b"async def down_revision():\n    pass\n",
    ),
)
def test_static_migration_contract_rejects_non_assignment_metadata_bindings(
    extra_source: bytes,
) -> None:
    source = (
        b'revision = "head"\ndown_revision = None\n'
        b"branch_labels = None\ndepends_on = None\n" + extra_source
    )

    with pytest.raises(ReleaseArtifactError, match="binds metadata name"):
        release_verification_receipt._migration_identity(source, path="/head.py")


def test_static_migration_contract_rejects_future_alias_metadata_binding() -> None:
    source = (
        b"from __future__ import annotations as revision\n"
        b'revision = "head"\ndown_revision = None\n'
        b"branch_labels = None\ndepends_on = None\n"
    )

    with pytest.raises(ReleaseArtifactError, match="binds metadata name revision"):
        release_verification_receipt._migration_identity(source, path="/head.py")


@pytest.mark.parametrize(
    "source",
    (
        (
            b'revision: payload() = "head"\ndown_revision = None\n'
            b"branch_labels = None\ndepends_on = None\n"
        ),
        (
            b'revision = "head"\ndown_revision = None\n'
            b"branch_labels = None\ndepends_on = None\n"
            b"def upgrade(value: payload()):\n    pass\n"
        ),
        (
            b'revision = "head"\ndown_revision = None\n'
            b"branch_labels = None\ndepends_on = None\n"
            b"def upgrade() -> payload():\n    pass\n"
        ),
    ),
)
def test_static_migration_contract_rejects_eager_executable_annotations(
    source: bytes,
) -> None:
    with pytest.raises(ReleaseArtifactError, match="executable import-time annotation"):
        release_verification_receipt._migration_identity(source, path="/head.py")


def test_static_migration_contract_accepts_deferred_annotations() -> None:
    source = (
        b"from __future__ import annotations\n"
        b'revision: payload() = "head"\ndown_revision = None\n'
        b"branch_labels = None\ndepends_on = None\n"
        b"def upgrade(value: payload()) -> payload():\n    pass\n"
    )

    assert release_verification_receipt._migration_identity(source, path="/head.py") == ("head", ())


def test_static_migration_contract_rejects_ineffective_future_annotations() -> None:
    source = (
        b'revision = "head"\n'
        b"from __future__ import annotations\n"
        b"down_revision: payload() = None\n"
        b"branch_labels = None\ndepends_on = None\n"
    )

    with pytest.raises(ReleaseArtifactError, match="misplaced future import"):
        release_verification_receipt._migration_identity(source, path="/head.py")


@pytest.mark.parametrize(
    "unsafe_import",
    (
        b"import pathlib\n",
        b"import sqlalchemy_attacker\n",
        b"from .payload import execute\n",
        b"from sqlalchemy import *\n",
    ),
)
def test_static_migration_contract_rejects_imports_outside_safe_prefixes(
    unsafe_import: bytes,
) -> None:
    source = (
        unsafe_import
        + b'revision = "head"\ndown_revision = None\n'
        + b"branch_labels = None\ndepends_on = None\n"
    )

    with pytest.raises(ReleaseArtifactError, match="unsafe import|safe prefixes"):
        release_verification_receipt._migration_identity(source, path="/head.py")


def test_static_migration_contract_handles_many_duplicate_metadata_assignments() -> None:
    source = (
        b'revision = "head"\ndown_revision = None\n'
        b"branch_labels = None\ndepends_on = None\n" + b'revision = "duplicate"\n' * 10_000
    )

    with pytest.raises(ReleaseArtifactError, match="assign revision exactly once"):
        release_verification_receipt._migration_identity(source, path="/head.py")


def test_static_migration_observation_matches_repository_graph(tmp_path: Path) -> None:
    archive = tmp_path / "api.tar.gz"
    entries = {
        "app/alembic.ini": (ROOT / "alembic.ini").read_bytes(),
        **{
            f"app/alembic/versions/{migration.name}": migration.read_bytes()
            for migration in sorted((ROOT / "alembic" / "versions").glob("*.py"))
        },
    }
    _write_static_api_archive(archive, [entries])

    assert release_verification_receipt._observed_alembic_head(archive) == ALEMBIC_HEAD


def test_static_migration_graph_rejects_multiple_heads(tmp_path: Path) -> None:
    archive = tmp_path / "api.tar.gz"
    _write_static_api_archive(
        archive,
        [
            {
                "app/alembic.ini": b"[alembic]\nscript_location = alembic\n",
                "app/alembic/versions/left.py": (
                    b'revision = "left"\ndown_revision = None\n'
                    b"branch_labels = None\ndepends_on = None\n"
                ),
                "app/alembic/versions/right.py": (
                    b'revision = "right"\ndown_revision = None\n'
                    b"branch_labels = None\ndepends_on = None\n"
                ),
            }
        ],
    )

    with pytest.raises(ReleaseArtifactError, match="exactly one API image Alembic head"):
        release_verification_receipt._observed_alembic_head(archive)


def test_static_migration_observation_rejects_redirected_alembic_path(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "api.tar.gz"
    _write_static_api_archive(
        archive,
        [
            {
                "app/alembic.ini": b"[alembic]\nscript_location = attacker\n",
                "app/alembic/versions/head.py": (
                    b'revision = "expected_head"\ndown_revision = None\n'
                ),
            }
        ],
    )

    with pytest.raises(ReleaseArtifactError, match="Alembic path policy is unsupported"):
        release_verification_receipt._observed_alembic_head(archive)


def test_loaded_image_identity_drift_fails_before_migration(candidate: CandidateFixture) -> None:
    candidate.runner.drift_component = "gw"

    with pytest.raises(ReleaseArtifactError, match="loaded image identity mismatch for gw"):
        _produce(candidate)

    assert not any(command[:2] == ("docker", "run") for command in candidate.runner.commands)
    assert _non_lock_outputs(candidate.output) == []


def test_candidate_reference_drift_fails_before_migration(
    candidate: CandidateFixture,
) -> None:
    candidate.runner.swap_api_tag_after_inspect = True

    with pytest.raises(
        ReleaseArtifactError,
        match="loaded candidate reference mismatch for api",
    ):
        _produce(candidate)

    assert not any(command[:2] == ("docker", "run") for command in candidate.runner.commands)
    assert _non_lock_outputs(candidate.output) == []


def test_descriptor_tool_hash_mismatch_fails_closed(candidate: CandidateFixture) -> None:
    with pytest.raises(ReleaseArtifactError, match="verifier tool SHA-256.*descriptor"):
        produce_release_verification_receipt(
            package=candidate.package,
            output_directory=candidate.output,
            runner=candidate.runner,
            trust_directory=candidate.trust,
            signing_identity=candidate.verifier_identity,
            verifier_id="protected-release-verifier",
            verifier_key_id="release-receipt-key",
            verifier_tool_sha256="sha256:" + "f" * 64,
            require_system_trust=False,
        )

    assert _non_lock_outputs(candidate.output) == []


def test_running_producer_source_change_after_load_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "release_verification_receipt.py"
    source.write_bytes(b"trusted producer bytes")
    monkeypatch.setattr(release_verification_receipt, "__file__", str(source))
    monkeypatch.setattr(
        release_verification_receipt,
        "_VERIFIER_SOURCE_PATH_AT_LOAD",
        source.resolve(),
    )
    monkeypatch.setattr(
        release_verification_receipt,
        "_VERIFIER_SOURCE_STAT_AT_LOAD",
        source.stat(),
    )
    monkeypatch.setattr(
        release_verification_receipt,
        "_VERIFIER_SOURCE_SHA256_AT_LOAD",
        hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    source.write_bytes(b"changed producer bytes")

    with pytest.raises(ReleaseArtifactError, match="producer source.*changed"):
        release_verification_receipt._running_verifier_tool_sha256()


def test_qualification_toolchain_source_same_length_concurrent_rewrite_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative = "tools/validate_device_point_profile.py"
    source = tmp_path / relative
    source.parent.mkdir(parents=True)
    original = b"authenticated qualification source\n"
    replacement = b"substituted qualification source!!\n"
    assert len(original) == len(replacement)
    source.write_bytes(original)
    actual_fdopen = os.fdopen
    read_count = 0

    class RewriteAfterFirstRead:
        def __init__(self, stream: BinaryIO) -> None:
            self.stream = stream

        def __enter__(self) -> RewriteAfterFirstRead:
            return self

        def __exit__(self, *args: object) -> None:
            self.stream.close()

        def read(self, size: int = -1) -> bytes:
            nonlocal read_count
            value = self.stream.read(size)
            read_count += 1
            if read_count == 1:
                source.write_bytes(replacement)
            return value

        def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
            return self.stream.seek(offset, whence)

    def mutating_fdopen(
        descriptor: int,
        mode: str,
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
        closefd: bool = True,
        opener: Callable[[str, int], int] | None = None,
    ) -> RewriteAfterFirstRead:
        del buffering, encoding, errors, newline, opener
        stream = cast(
            BinaryIO,
            actual_fdopen(descriptor, mode, buffering=0, closefd=closefd),
        )
        return RewriteAfterFirstRead(stream)

    monkeypatch.setattr(os, "fdopen", mutating_fdopen)

    with pytest.raises(ReleaseArtifactError, match="source changed while being read"):
        release_artifacts._read_toolchain_source(tmp_path, relative)

    assert read_count == 2
    assert source.read_bytes() == replacement


def test_tampered_actual_producer_cannot_claim_signed_expected_digest(
    candidate: CandidateFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tampered = tmp_path / "tampered" / "release_verification_receipt.py"
    tampered.parent.mkdir()
    tampered.write_bytes(b"malicious receipt producer")
    monkeypatch.setattr(release_verification_receipt, "__file__", str(tampered))
    monkeypatch.setattr(
        release_verification_receipt,
        "_VERIFIER_SOURCE_PATH_AT_LOAD",
        tampered.resolve(),
    )
    monkeypatch.setattr(
        release_verification_receipt,
        "_VERIFIER_SOURCE_STAT_AT_LOAD",
        tampered.stat(),
    )
    monkeypatch.setattr(
        release_verification_receipt,
        "_VERIFIER_SOURCE_SHA256_AT_LOAD",
        hashlib.sha256(tampered.read_bytes()).hexdigest(),
    )

    with pytest.raises(ReleaseArtifactError, match="running verifier tool SHA-256.*descriptor"):
        _produce(candidate)

    assert _non_lock_outputs(candidate.output) == []


def test_imported_release_verifier_dependency_drift_fails_closed(
    candidate: CandidateFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dependency = tmp_path / "release_artifacts.py"
    dependency.write_bytes(b"trusted dependency bytes")
    monkeypatch.setattr(release_artifacts, "__file__", str(dependency))
    monkeypatch.setattr(
        release_verification_receipt,
        "_RELEASE_ARTIFACTS_SOURCE_PATH_AT_LOAD",
        dependency.resolve(),
    )
    monkeypatch.setattr(
        release_verification_receipt,
        "_RELEASE_ARTIFACTS_SOURCE_STAT_AT_LOAD",
        dependency.stat(),
    )
    monkeypatch.setattr(
        release_verification_receipt,
        "_RELEASE_ARTIFACTS_SOURCE_SHA256_AT_LOAD",
        hashlib.sha256(dependency.read_bytes()).hexdigest(),
    )
    dependency.write_bytes(b"changed dependency bytes")

    with pytest.raises(ReleaseArtifactError, match="release artifacts source.*changed"):
        _produce(candidate)

    assert _non_lock_outputs(candidate.output) == []


def test_signature_failure_leaves_no_sidecar_or_half_file(candidate: CandidateFixture) -> None:
    candidate.runner.fail_receipt_signature = True

    with pytest.raises(ReleaseArtifactError, match="injected receipt signature failure"):
        _produce(candidate)

    assert _non_lock_outputs(candidate.output) == []


def test_output_collision_is_no_overwrite(candidate: CandidateFixture) -> None:
    output = candidate.output / f"{CANDIDATE_ID}{RECEIPT_FILE_SUFFIX}"
    output.write_bytes(b"pre-existing receipt")

    with pytest.raises(ReleaseArtifactError, match="receipt already exists"):
        _produce(candidate)

    assert output.read_bytes() == b"pre-existing receipt"
    assert _non_lock_outputs(candidate.output) == [output]


def test_output_collision_created_after_signing_is_no_overwrite(
    candidate: CandidateFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = candidate.output / f"{CANDIDATE_ID}{RECEIPT_FILE_SUFFIX}"
    original_sign = release_verification_receipt._sign_receipt_message

    def sign_then_collide(**kwargs: Any) -> dict[str, str]:
        signature = original_sign(**kwargs)
        output.write_bytes(b"concurrent receipt")
        return signature

    monkeypatch.setattr(release_verification_receipt, "_sign_receipt_message", sign_then_collide)

    with pytest.raises(ReleaseArtifactError, match="receipt already exists"):
        _produce(candidate)

    assert output.read_bytes() == b"concurrent receipt"
    assert _non_lock_outputs(candidate.output) == [output]


def test_partial_output_write_is_removed(
    candidate: CandidateFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def partial_write(descriptor: int, value: bytes) -> None:
        os.write(descriptor, value[:17])
        raise OSError("injected receipt write failure")

    monkeypatch.setattr(release_verification_receipt, "_write_bytes_fully", partial_write)

    with pytest.raises(ReleaseArtifactError, match="injected receipt write failure"):
        _produce(candidate)

    assert _non_lock_outputs(candidate.output) == []


def test_publish_failure_never_cleans_up_by_unbound_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"
    unlinks: list[Path] = []

    def partial_write(descriptor: int, value: bytes) -> None:
        os.write(descriptor, value[:17])
        raise OSError("injected retained-handle write failure")

    def record_unlink(path: Path, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        unlinks.append(path)
        raise AssertionError("path cleanup is forbidden")

    monkeypatch.setattr(release_verification_receipt, "_write_bytes_fully", partial_write)
    monkeypatch.setattr(Path, "unlink", record_unlink)

    with pytest.raises(ReleaseArtifactError, match="retained-handle write failure"):
        release_verification_receipt._publish_no_replace(output, b"complete receipt")

    assert unlinks == []
    assert not output.exists()


def test_publish_temporary_is_anonymous_or_cannot_be_replaced_while_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"
    original_write = release_verification_receipt._write_bytes_fully
    observed_names: list[Path] = []

    def attack_open_temporary(descriptor: int, value: bytes) -> None:
        observed_names.extend(tmp_path.glob(".*.tmp-*"))
        for temporary in observed_names:
            with pytest.raises(OSError):
                temporary.unlink()
            attacker = tmp_path / "attacker"
            attacker.write_bytes(b"attacker payload")
            with pytest.raises(OSError):
                os.replace(attacker, temporary)
            attacker.unlink(missing_ok=True)
        original_write(descriptor, value)

    monkeypatch.setattr(
        release_verification_receipt,
        "_write_bytes_fully",
        attack_open_temporary,
    )

    release_verification_receipt._publish_no_replace(output, b"complete receipt")

    if os.name == "nt":
        assert len(observed_names) == 1
    else:
        assert observed_names == []
    assert output.read_bytes() == b"complete receipt"


def test_posix_o_tmpfile_eisdir_uses_named_fallback_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_open(*_args: object, **_kwargs: object) -> int:
        raise OSError(errno.EISDIR, "filesystem does not support O_TMPFILE")

    monkeypatch.setattr(release_verification_receipt, "OS_O_TMPFILE", 0x410000)
    monkeypatch.setattr(os, "open", unavailable_open)

    with pytest.raises(release_verification_receipt._AnonymousPublishUnavailableError):
        release_verification_receipt._publish_posix_anonymous(
            tmp_path / "receipt.json",
            b"complete receipt",
            directory_descriptor=-1,
            directory_identity=tmp_path.stat(),
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX named fallback contract")
def test_posix_publish_falls_back_when_o_tmpfile_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"
    monkeypatch.setattr(release_verification_receipt, "OS_O_TMPFILE", 0)

    release_verification_receipt._publish_no_replace(output, b"complete receipt")

    assert output.read_bytes() == b"complete receipt"
    assert not list(tmp_path.glob(".*.tmp-*"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX named fallback contract")
def test_posix_publish_falls_back_when_at_empty_path_is_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"

    def deny_anonymous_link(*_args: object, **_kwargs: object) -> None:
        raise release_verification_receipt._AnonymousPublishUnavailableError

    monkeypatch.setattr(
        release_verification_receipt,
        "_posix_link_anonymous_no_replace",
        deny_anonymous_link,
    )

    release_verification_receipt._publish_no_replace(output, b"complete receipt")

    assert output.read_bytes() == b"complete receipt"
    assert not list(tmp_path.glob(".*.tmp-*"))


@pytest.mark.skipif(os.name != "nt", reason="Windows retained-handle flush contract")
def test_windows_publish_flushes_retained_file_after_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"
    original_fsync = os.fsync
    flush_observations: list[bool] = []

    def observe_fsync(descriptor: int) -> None:
        flush_observations.append(output.exists())
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", observe_fsync)

    release_verification_receipt._publish_no_replace(output, b"complete receipt")

    assert flush_observations == [False, True]
    assert output.read_bytes() == b"complete receipt"


def test_publish_parent_exchange_is_prevented_or_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "receipts"
    parent.mkdir()
    moved = tmp_path / "moved-receipts"
    output = parent / "receipt.json"
    original_write = release_verification_receipt._write_bytes_fully
    exchange_succeeded = False

    def exchange_parent(descriptor: int, value: bytes) -> None:
        nonlocal exchange_succeeded
        original_write(descriptor, value)
        try:
            os.rename(parent, moved)
            parent.mkdir()
            exchange_succeeded = True
        except OSError:
            exchange_succeeded = False

    monkeypatch.setattr(release_verification_receipt, "_write_bytes_fully", exchange_parent)

    if os.name == "nt":
        release_verification_receipt._publish_no_replace(output, b"complete receipt")
        assert not exchange_succeeded
        assert output.read_bytes() == b"complete receipt"
    else:
        with pytest.raises(ReleaseArtifactError, match="publish directory.*changed"):
            release_verification_receipt._publish_no_replace(output, b"complete receipt")
        assert exchange_succeeded
        assert not output.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX anonymous publication contract")
def test_posix_publish_detects_final_name_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"
    retained = tmp_path / "retained-receipt.json"

    def replace_after_publish(_descriptor: int) -> None:
        output.rename(retained)
        output.write_bytes(b"attacker payload")

    monkeypatch.setattr(
        release_verification_receipt,
        "_sync_bound_publish_directory",
        replace_after_publish,
    )

    with pytest.raises(ReleaseArtifactError, match="receipt path identity changed"):
        release_verification_receipt._publish_no_replace(output, b"complete receipt")

    assert retained.read_bytes() == b"complete receipt"
    assert output.read_bytes() == b"attacker payload"


def test_post_publish_sync_failure_leaves_no_partial_or_unbound_cleanup(
    candidate: CandidateFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fail_first_sync(_descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected receipt directory sync failure")

    monkeypatch.setattr(
        release_verification_receipt,
        "_sync_bound_publish_directory",
        fail_first_sync,
    )

    with pytest.raises(ReleaseArtifactError, match="injected receipt directory sync failure"):
        _produce(candidate)

    assert calls == 1
    output = candidate.output / f"{CANDIDATE_ID}{RECEIPT_FILE_SUFFIX}"
    if os.name == "nt":
        assert not output.exists()
        assert all(
            not candidate.runner.image_exists(image.candidate_reference, cwd=candidate.output)
            for image in candidate.manifest.images
        )
    else:
        ReleaseVerificationReceipt.model_validate_json(output.read_bytes())
        assert all(
            candidate.runner.image_exists(image.candidate_reference, cwd=candidate.output)
            for image in candidate.manifest.images
        )
    assert not list(candidate.output.glob(".*.tmp-*"))


def test_post_publish_lock_release_failure_retains_receipt_and_loaded_tags(
    candidate: CandidateFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextmanager
    def failing_release_lock(_lock_directory: Path, _candidate_id: str) -> Iterator[None]:
        try:
            yield
        finally:
            raise OSError("injected candidate lock release failure")

    monkeypatch.setattr(
        release_verification_receipt,
        "_candidate_operation_lock",
        failing_release_lock,
    )

    with pytest.raises(
        release_verification_receipt._ReceiptPublishedError,
        match="candidate operation lock release failed",
    ) as raised:
        _produce(candidate)

    output = candidate.output / f"{CANDIDATE_ID}{RECEIPT_FILE_SUFFIX}"
    ReleaseVerificationReceipt.model_validate_json(output.read_bytes())
    assert all(
        candidate.runner.image_exists(image.candidate_reference, cwd=candidate.output)
        for image in candidate.manifest.images
    )
    assert any("complete published receipt retained" in note for note in raised.value.__notes__)


def test_system_trust_uses_host_global_candidate_operation_lock(
    candidate: CandidateFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock_root = tmp_path / "host-global-locks"
    lock_root.mkdir()
    acquired: list[tuple[Path, str]] = []
    operation_lock = release_verification_receipt._candidate_operation_lock

    @contextmanager
    def recording_lock(lock_directory: Path, candidate_id: str) -> Iterator[None]:
        acquired.append((lock_directory, candidate_id))
        with operation_lock(lock_directory, candidate_id):
            yield

    monkeypatch.setattr(
        release_verification_receipt,
        "_validate_system_trust_permissions",
        lambda _trust: None,
    )
    monkeypatch.setattr(
        release_verification_receipt,
        "_system_candidate_operation_lock_root",
        lambda: lock_root,
    )
    monkeypatch.setattr(
        release_verification_receipt,
        "_candidate_operation_lock",
        recording_lock,
    )

    receipt_path = _produce(candidate, require_system_trust=True)

    assert receipt_path.is_file()
    assert acquired == [(lock_root, CANDIDATE_ID)]


def test_candidate_operation_lock_fails_closed_on_concurrent_holder(tmp_path: Path) -> None:
    with (
        release_verification_receipt._candidate_operation_lock(tmp_path, CANDIDATE_ID),
        pytest.raises(ReleaseArtifactError, match="already active"),
        release_verification_receipt._candidate_operation_lock(tmp_path, CANDIDATE_ID),
    ):
        pytest.fail("concurrent candidate operation lock was acquired")


def test_build_and_receipt_share_the_same_candidate_tag_lock(tmp_path: Path) -> None:
    with (
        release_artifacts.candidate_tag_operation_lock(tmp_path, CANDIDATE_ID),
        pytest.raises(ReleaseArtifactError, match="already active"),
        release_verification_receipt._candidate_operation_lock(tmp_path, CANDIDATE_ID),
    ):
        pytest.fail("receipt acquired the candidate tag lock held by the builder")

    expected = release_artifacts.candidate_tag_lock_name(CANDIDATE_ID)
    assert (tmp_path / expected).is_file()


def test_candidate_operation_lock_does_not_conflict_for_distinct_candidates(
    tmp_path: Path,
) -> None:
    with (
        release_verification_receipt._candidate_operation_lock(tmp_path, CANDIDATE_ID),
        release_verification_receipt._candidate_operation_lock(
            tmp_path,
            "deploy-20260827.2",
        ),
    ):
        pass


def test_candidate_operation_lock_is_released_after_exception(tmp_path: Path) -> None:
    with (
        pytest.raises(RuntimeError, match="injected operation failure"),
        release_verification_receipt._candidate_operation_lock(tmp_path, CANDIDATE_ID),
    ):
        raise RuntimeError("injected operation failure")

    with release_verification_receipt._candidate_operation_lock(tmp_path, CANDIDATE_ID):
        pass


def test_original_candidate_replacement_after_snapshot_cannot_change_receipt(
    candidate: CandidateFixture,
) -> None:
    manifest_digest_before = hashlib.sha256(
        (candidate.package / "MANIFEST.json").read_bytes()
    ).hexdigest()

    def replace_original_manifest() -> None:
        (candidate.package / "MANIFEST.json").write_bytes(b"replaced after protected snapshot")

    candidate.runner.on_first_load = replace_original_manifest

    receipt_path = _produce(candidate)

    receipt = ReleaseVerificationReceipt.model_validate_json(receipt_path.read_bytes())
    assert receipt.manifest_sha256 == "sha256:" + manifest_digest_before
    assert (
        candidate.package / "MANIFEST.json"
    ).read_bytes() == b"replaced after protected snapshot"
