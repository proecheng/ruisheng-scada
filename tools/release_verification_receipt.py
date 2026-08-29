"""Produce signed, package-external release verification receipts."""

from __future__ import annotations

import argparse
import ast
import base64
import binascii
import configparser
import ctypes
import errno
import hashlib
import importlib
import json
import os
import re
import stat
import sys
import tarfile
import tempfile
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from tools import release_artifacts as release_artifacts_module
from tools import validate_device_point_profile as validator_module
from tools.release_artifacts import (
    COMPONENTS,
    MAX_DOCKER_ARCHIVE_MEMBER_BYTES,
    MAX_DOCKER_ARCHIVE_MEMBERS,
    MAX_DOCKER_ARCHIVE_TOTAL_BYTES,
    MAX_RELEASE_JSON_BYTES,
    PUBLISHER,
    SIGNATURE_FILE,
    SIGNATURE_NAMESPACE,
    SIGNED_OBJECT,
    CandidateManifest,
    ImageArtifact,
    ReleaseArtifactError,
    Runner,
    SubprocessRunner,
    _inspect_loaded_candidate_image,
    _load_release_trust,
    _package_file_set,
    _preflight_docker_archive,
    _preflight_docker_tar_stream,
    _protected_candidate_snapshot,
    _system_ssh_keygen,
    _system_trust_directory,
    _validate_atomic_publish_root,
    _validate_sshsig_file,
    _validate_system_trust_permissions,
    _verify_snapshot_contents,
    candidate_tag_operation_lock,
    inspect_image,
    sha256_file,
    system_candidate_tag_lock_root,
)
from tools.validate_device_point_profile import (
    ReleaseVerificationReceipt,
    canonical_json_bytes,
    release_receipt_check_digests,
    release_receipt_protected_snapshot_id,
    release_receipt_signature_message,
)

RECEIPT_ARTIFACT_TYPE = "ruisheng.release-verification-receipt"
RECEIPT_SIGNATURE_NAMESPACE = "ruisheng-release-verification-receipt-v1"
RECEIPT_FILE_SUFFIX = ".release-verification-receipt.json"
VERIFICATION_METHOD = "openssh-sha256sums-protected-snapshot/v1"
VERIFIER_TOOL_ID = "ruisheng.release-artifacts-receipt-producer/v1"
RECEIPT_SCHEMA_VERSION = 1
MAX_AGENT_IDENTITY_BYTES = 16 * 1024
MAX_VERIFIER_SOURCE_BYTES = 8 * 1024 * 1024
MAX_MIGRATION_FILE_BYTES = 2 * 1024 * 1024
MAX_MIGRATION_FILES = 4096
MAX_MIGRATION_TOTAL_BYTES = 64 * 1024 * 1024
MAX_LAYER_MEMBERS = 1_000_000
MAX_OVERLAY_DIRECTIVES = MAX_MIGRATION_FILES * 2
SSH_STRING_LENGTH_BYTES = 4
ED25519_PUBLIC_KEY_BYTES = 32
POSIX_AT_EMPTY_PATH = 0x1000
OS_O_TMPFILE = getattr(os, "O_TMPFILE", 0)
IMAGE_ALEMBIC_CONFIG = PurePosixPath("app/alembic.ini")
IMAGE_MIGRATION_ROOT = PurePosixPath("app/alembic/versions")
MIGRATION_METADATA_NAMES = frozenset({"revision", "down_revision", "branch_labels", "depends_on"})
MIGRATION_SAFE_IMPORT_PREFIXES = ("alembic", "collections.abc", "sqlalchemy")
MIGRATION_SAFE_IMPORT_MODULES = frozenset({"os"})
MIGRATION_SAFE_EAGER_ANNOTATION_NAMES = frozenset(
    {
        "bool",
        "bytes",
        "dict",
        "float",
        "frozenset",
        "int",
        "list",
        "object",
        "Sequence",
        "set",
        "str",
        "tuple",
        "type",
    }
)

WIN_GENERIC_READ = 0x80000000
WIN_GENERIC_WRITE = 0x40000000
WIN_DELETE = 0x00010000
WIN_FILE_LIST_DIRECTORY = 0x0001
WIN_FILE_ADD_FILE = 0x0002
WIN_FILE_TRAVERSE = 0x0020
WIN_FILE_READ_ATTRIBUTES = 0x0080
WIN_FILE_SHARE_READ = 0x00000001
WIN_FILE_SHARE_WRITE = 0x00000002
WIN_CREATE_NEW = 1
WIN_OPEN_ALWAYS = 4
WIN_OPEN_EXISTING = 3
WIN_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
WIN_FILE_ATTRIBUTE_NORMAL = 0x00000080
WIN_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
WIN_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
WIN_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
WIN_FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
WIN_FILE_RENAME_INFO_EX_CLASS = 22
WIN_FILE_DISPOSITION_INFO_CLASS = 4
WIN_ERROR_FILE_EXISTS = 80
WIN_ERROR_ALREADY_EXISTS = 183
WIN_ERROR_INVALID_PARAMETER = 87

SHA256_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
ALEMBIC_REVISION_PATTERN = re.compile(r"[A-Za-z0-9_]+\Z")


class _AnonymousPublishUnavailableError(RuntimeError):
    """Signals that safe anonymous publication is unsupported on this filesystem."""


class _ReceiptPublishedError(ReleaseArtifactError):
    """Signals that a complete POSIX receipt was linked before publication failed."""

    receipt_published = True

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.add_note(
            "complete published receipt retained; loaded candidate references were retained "
            "because publication durability or post-publication identity is indeterminate"
        )


class _WinFileAttributeTagInfo(ctypes.Structure):
    _fields_ = [("file_attributes", ctypes.c_uint32), ("reparse_tag", ctypes.c_uint32)]


class _WinFileRenameInfo(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("root_directory", ctypes.c_void_p),
        ("file_name_length", ctypes.c_uint32),
        ("file_name", ctypes.c_wchar * 1),
    ]


class _WinFileDispositionInfo(ctypes.Structure):
    _fields_ = [("delete_file", ctypes.c_ubyte)]


_VERIFIER_SOURCE_PATH_AT_LOAD = Path(__file__).resolve(strict=True)
_VERIFIER_SOURCE_STAT_AT_LOAD = _VERIFIER_SOURCE_PATH_AT_LOAD.stat()
_VERIFIER_SOURCE_SHA256_AT_LOAD = sha256_file(_VERIFIER_SOURCE_PATH_AT_LOAD)
if release_artifacts_module.__file__ is None:  # pragma: no cover - source module invariant
    raise RuntimeError("release artifacts module has no source path")
_RELEASE_ARTIFACTS_SOURCE_PATH_AT_LOAD = Path(release_artifacts_module.__file__).resolve(
    strict=True
)
_RELEASE_ARTIFACTS_SOURCE_STAT_AT_LOAD = _RELEASE_ARTIFACTS_SOURCE_PATH_AT_LOAD.stat()
_RELEASE_ARTIFACTS_SOURCE_SHA256_AT_LOAD = sha256_file(_RELEASE_ARTIFACTS_SOURCE_PATH_AT_LOAD)
if validator_module.__file__ is None:  # pragma: no cover - source module invariant
    raise RuntimeError("device point validator module has no source path")
_VALIDATOR_SOURCE_PATH_AT_LOAD = Path(validator_module.__file__).resolve(strict=True)
_VALIDATOR_SOURCE_STAT_AT_LOAD = _VALIDATOR_SOURCE_PATH_AT_LOAD.stat()
_VALIDATOR_SOURCE_SHA256_AT_LOAD = sha256_file(_VALIDATOR_SOURCE_PATH_AT_LOAD)


def _sha256_digest(path: Path) -> str:
    return "sha256:" + sha256_file(path)


def _canonical_sha256(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _source_stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _running_source_sha256(
    *,
    current_file: str | None,
    source_path_at_load: Path,
    source_stat_at_load: os.stat_result,
    source_sha256_at_load: str,
    label: str,
) -> str:
    if current_file is None:
        raise ReleaseArtifactError(f"running {label} source path is unavailable")
    try:
        current_path = Path(current_file).resolve(strict=True)
    except OSError as error:
        raise ReleaseArtifactError(f"cannot resolve running {label} source: {error}") from error
    if current_path != source_path_at_load:
        raise ReleaseArtifactError(f"running {label} source path changed after module load")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source_path_at_load, flags)
    except OSError as error:
        raise ReleaseArtifactError(f"cannot open running {label} source: {error}") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size > MAX_VERIFIER_SOURCE_BYTES
            or _source_stat_identity(before) != _source_stat_identity(source_stat_at_load)
        ):
            raise ReleaseArtifactError(f"running {label} source identity or metadata changed")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            first = source.read(MAX_VERIFIER_SOURCE_BYTES + 1)
            source.seek(0)
            second = source.read(MAX_VERIFIER_SOURCE_BYTES + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    digest = hashlib.sha256(first).hexdigest()
    if (
        first != second
        or len(first) != before.st_size
        or _source_stat_identity(before) != _source_stat_identity(after)
        or digest != source_sha256_at_load
    ):
        raise ReleaseArtifactError(f"running {label} source bytes changed after module load")
    return "sha256:" + digest


def _running_verifier_tool_sha256() -> str:
    return _running_source_sha256(
        current_file=__file__,
        source_path_at_load=_VERIFIER_SOURCE_PATH_AT_LOAD,
        source_stat_at_load=_VERIFIER_SOURCE_STAT_AT_LOAD,
        source_sha256_at_load=_VERIFIER_SOURCE_SHA256_AT_LOAD,
        label="producer",
    )


def _running_release_artifacts_sha256() -> str:
    return _running_source_sha256(
        current_file=release_artifacts_module.__file__,
        source_path_at_load=_RELEASE_ARTIFACTS_SOURCE_PATH_AT_LOAD,
        source_stat_at_load=_RELEASE_ARTIFACTS_SOURCE_STAT_AT_LOAD,
        source_sha256_at_load=_RELEASE_ARTIFACTS_SOURCE_SHA256_AT_LOAD,
        label="release artifacts",
    )


def _running_validator_sha256() -> str:
    return _running_source_sha256(
        current_file=validator_module.__file__,
        source_path_at_load=_VALIDATOR_SOURCE_PATH_AT_LOAD,
        source_stat_at_load=_VALIDATOR_SOURCE_STAT_AT_LOAD,
        source_sha256_at_load=_VALIDATOR_SOURCE_SHA256_AT_LOAD,
        label="device point validator",
    )


def _decode_ssh_string(value: bytes, offset: int = 0) -> tuple[bytes, int]:
    if len(value) - offset < SSH_STRING_LENGTH_BYTES:
        raise ReleaseArtifactError("receipt signing identity key blob is truncated")
    length = int.from_bytes(
        value[offset : offset + SSH_STRING_LENGTH_BYTES],
        "big",
    )
    start = offset + SSH_STRING_LENGTH_BYTES
    end = start + length
    if end > len(value):
        raise ReleaseArtifactError("receipt signing identity key blob is truncated")
    return value[start:end], end


def _read_agent_identity(path: Path) -> tuple[bytes, bytes]:
    if path.suffix.casefold() != ".pub" or path.is_symlink() or not path.is_file():
        raise ReleaseArtifactError(
            "receipt signing identity must be an agent-backed OpenSSH public key (.pub)"
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ReleaseArtifactError(f"cannot open receipt signing identity: {error}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_AGENT_IDENTITY_BYTES:
            raise ReleaseArtifactError("receipt signing identity is not an allowed regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            first = source.read(MAX_AGENT_IDENTITY_BYTES + 1)
            source.seek(0)
            second = source.read(MAX_AGENT_IDENTITY_BYTES + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        first != second
        or len(first) != before.st_size
        or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    ):
        raise ReleaseArtifactError("receipt signing identity changed while being read")
    if not first.endswith(b"\n") or b"\r" in first or b"\n" in first[:-1]:
        raise ReleaseArtifactError("receipt signing identity must be one canonical OpenSSH line")
    fields = first.removesuffix(b"\n").split(maxsplit=2)
    if len(fields) not in {2, 3} or fields[0] != b"ssh-ed25519":
        raise ReleaseArtifactError("receipt signing identity must contain one ssh-ed25519 key")
    try:
        key_blob = base64.b64decode(fields[1], validate=True)
    except binascii.Error as error:
        raise ReleaseArtifactError("receipt signing identity is not canonical base64") from error
    if base64.b64encode(key_blob) != fields[1]:
        raise ReleaseArtifactError("receipt signing identity is not canonical base64")
    key_type, offset = _decode_ssh_string(key_blob)
    public_key, offset = _decode_ssh_string(key_blob, offset)
    if (
        key_type != b"ssh-ed25519"
        or len(public_key) != ED25519_PUBLIC_KEY_BYTES
        or offset != len(key_blob)
    ):
        raise ReleaseArtifactError("receipt signing identity is not a canonical ssh-ed25519 key")
    return first, key_blob


def _validate_identifier(value: str, *, label: str) -> str:
    if IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ReleaseArtifactError(f"{label} is invalid")
    return value


def _verification_timestamp() -> str:
    """Capture verification completion time from the verifier's own clock."""

    instant = datetime.now(UTC)
    if instant.utcoffset() is None:  # pragma: no cover - datetime.now(UTC) contract
        raise ReleaseArtifactError("receipt verification clock did not return a timezone")
    return instant.astimezone(UTC).isoformat(timespec="seconds")


def _sshsig_binary_base64(signature_path: Path) -> str:
    _validate_sshsig_file(signature_path)
    value = signature_path.read_bytes()
    header = b"-----BEGIN SSH SIGNATURE-----\n"
    footer = b"-----END SSH SIGNATURE-----\n"
    try:
        binary = base64.b64decode(
            value[len(header) : -len(footer)].replace(b"\n", b""),
            validate=True,
        )
    except binascii.Error as error:  # pragma: no cover - validated by _validate_sshsig_file
        raise ReleaseArtifactError("receipt SSH signature is invalid base64") from error
    return base64.b64encode(binary).decode("ascii")


def _sign_receipt_message(
    *,
    output_directory: Path,
    candidate_id: str,
    identity_bytes: bytes,
    identity_key_blob: bytes,
    verifier_id: str,
    verifier_key_id: str,
    message: bytes,
    runner: Runner,
) -> dict[str, str]:
    ssh_keygen = _system_ssh_keygen()
    with tempfile.TemporaryDirectory(
        prefix=f".{candidate_id}.receipt-sign-", dir=output_directory
    ) as temporary_name:
        temporary = Path(temporary_name)
        os.chmod(temporary, 0o700)
        identity = temporary / "agent-identity.pub"
        message_path = temporary / "canonical-message"
        allowed_signers = temporary / "allowed-signers"
        identity.write_bytes(identity_bytes)
        message_path.write_bytes(message)
        allowed_signers.write_bytes(
            verifier_id.encode("ascii")
            + b" ssh-ed25519 "
            + base64.b64encode(identity_key_blob)
            + b"\n"
        )
        for path in (identity, message_path, allowed_signers):
            os.chmod(path, 0o600)
        runner.run(
            [
                str(ssh_keygen),
                "-Y",
                "sign",
                "-U",
                "-f",
                str(identity),
                "-n",
                RECEIPT_SIGNATURE_NAMESPACE,
                str(message_path),
            ],
            cwd=temporary,
        )
        signature_path = message_path.with_name(message_path.name + ".sig")
        if signature_path.is_symlink() or not signature_path.is_file():
            raise ReleaseArtifactError("receipt SSH signature was not created")
        signature_value = _sshsig_binary_base64(signature_path)
        runner.run(
            [
                str(ssh_keygen),
                "-Y",
                "verify",
                "-f",
                str(allowed_signers),
                "-I",
                verifier_id,
                "-n",
                RECEIPT_SIGNATURE_NAMESPACE,
                "-s",
                str(signature_path),
            ],
            cwd=temporary,
            input_bytes=message,
        )
    return {
        "algorithm": "OpenSSH-SSHSIG-Ed25519",
        "key_id": verifier_key_id,
        "namespace": RECEIPT_SIGNATURE_NAMESPACE,
        "value": signature_value,
    }


def _normalized_layer_path(name: str, *, archive_path: Path) -> PurePosixPath | None:
    if "\x00" in name or "\\" in name:
        raise ReleaseArtifactError(f"image layer path is invalid: {archive_path}:{name!r}")
    while name.startswith("./"):
        name = name[2:]
    name = name.rstrip("/")
    if not name or name == ".":
        return None
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReleaseArtifactError(f"image layer path is invalid: {archive_path}:{name!r}")
    return path


def _apply_overlay_deletions(
    files: dict[PurePosixPath, bytes],
    *,
    inherited_deletions: set[PurePosixPath],
    opaque_directories: set[PurePosixPath],
) -> None:
    for current in tuple(files):
        ancestry = (current, *current.parents)
        if any(
            ancestor in inherited_deletions or ancestor in opaque_directories
            for ancestor in ancestry
        ):
            del files[current]


def _tracked_image_path(path: PurePosixPath) -> bool:
    return path == IMAGE_ALEMBIC_CONFIG or (
        path.parent == IMAGE_MIGRATION_ROOT and path.suffix == ".py"
    )


def _validate_whiteout_member(
    member: tarfile.TarInfo,
    *,
    path: PurePosixPath,
    archive_path: Path,
    layer_name: str,
) -> None:
    target_name = path.name.removeprefix(".wh.")
    if target_name == "":
        raise ReleaseArtifactError(
            f"image layer whiteout has an empty target: {archive_path}:{layer_name}:{member.name}"
        )
    if target_name in {".", ".."}:
        raise ReleaseArtifactError(
            f"image layer whiteout has an invalid target: {archive_path}:{layer_name}:{member.name}"
        )
    if member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE} or member.size != 0:
        raise ReleaseArtifactError(
            "image layer whiteout is not a zero-length regular file: "
            f"{archive_path}:{layer_name}:{member.name}"
        )


def _apply_image_layer(  # noqa: PLR0912, PLR0915
    layer_stream: Any,
    *,
    archive_path: Path,
    layer_name: str,
    files: dict[PurePosixPath, bytes],
    migration_bytes_seen: list[int],
    expanded_bytes_seen: list[int],
    layer_members_seen: list[int],
    overlay_directives_seen: list[int],
) -> None:
    try:
        try:
            initial_position = layer_stream.tell()
            _preflight_docker_tar_stream(
                layer_stream,
                label=f"image layer {archive_path}:{layer_name}",
                maximum_members=min(MAX_LAYER_MEMBERS, MAX_DOCKER_ARCHIVE_MEMBERS),
                maximum_member_bytes=MAX_DOCKER_ARCHIVE_MEMBER_BYTES,
                maximum_total_bytes=MAX_DOCKER_ARCHIVE_TOTAL_BYTES,
            )
            layer_stream.seek(initial_position)
        except (AttributeError, OSError) as error:
            raise ReleaseArtifactError(
                f"image layer cannot be preflighted: {archive_path}:{layer_name}"
            ) from error
        with tarfile.open(fileobj=layer_stream, mode="r|*") as layer:
            relevant_names: set[PurePosixPath] = set()
            inherited_deletions: set[PurePosixPath] = set()
            opaque_directories: set[PurePosixPath] = set()
            layer_files: dict[PurePosixPath, bytes] = {}
            for member_count, member in enumerate(layer, start=1):
                layer_members_seen[0] += 1
                if layer_members_seen[0] > MAX_LAYER_MEMBERS:
                    raise ReleaseArtifactError(
                        f"image layers exceed the global member budget: {archive_path}"
                    )
                if member_count > min(MAX_LAYER_MEMBERS, MAX_DOCKER_ARCHIVE_MEMBERS):
                    raise ReleaseArtifactError(
                        f"image layer has too many members: {archive_path}:{layer_name}"
                    )
                if member.size < 0 or member.size > MAX_DOCKER_ARCHIVE_MEMBER_BYTES:
                    raise ReleaseArtifactError(
                        f"image layer member exceeds the byte budget: {archive_path}:{layer_name}"
                    )
                expanded_bytes_seen[0] += member.size
                if expanded_bytes_seen[0] > MAX_DOCKER_ARCHIVE_TOTAL_BYTES:
                    raise ReleaseArtifactError(
                        f"image layer exceeds the total byte budget: {archive_path}:{layer_name}"
                    )
                path = _normalized_layer_path(member.name, archive_path=archive_path)
                if path is None:
                    continue
                basename = path.name
                if basename.startswith(".wh."):
                    _validate_whiteout_member(
                        member,
                        path=path,
                        archive_path=archive_path,
                        layer_name=layer_name,
                    )
                    overlay_directives_seen[0] += 1
                    if overlay_directives_seen[0] > MAX_OVERLAY_DIRECTIVES:
                        raise ReleaseArtifactError(
                            f"image layers exceed the overlay directive budget: {archive_path}"
                        )
                    if basename == ".wh..wh..opq":
                        opaque_directories.add(path.parent)
                    else:
                        inherited_deletions.add(path.parent / basename.removeprefix(".wh."))
                    continue
                migration_related = (
                    path == IMAGE_MIGRATION_ROOT
                    or path in IMAGE_MIGRATION_ROOT.parents
                    or IMAGE_MIGRATION_ROOT in path.parents
                )
                if migration_related and (member.issym() or member.islnk()):
                    raise ReleaseArtifactError(
                        "image migration tree contains a link: "
                        f"{archive_path}:{layer_name}:{member.name}"
                    )
                if path in IMAGE_MIGRATION_ROOT.parents and not member.isdir():
                    raise ReleaseArtifactError(
                        "image migration ancestor is not a directory: "
                        f"{archive_path}:{layer_name}:{member.name}"
                    )
                if path == IMAGE_MIGRATION_ROOT and not member.isdir():
                    raise ReleaseArtifactError(
                        "image migration root is not a directory: "
                        f"{archive_path}:{layer_name}:{member.name}"
                    )
                if not _tracked_image_path(path):
                    continue
                if path in relevant_names:
                    raise ReleaseArtifactError(
                        "image layer repeats a migration path: "
                        f"{archive_path}:{layer_name}:{member.name}"
                    )
                relevant_names.add(path)
                if not member.isfile() or member.size > MAX_MIGRATION_FILE_BYTES:
                    raise ReleaseArtifactError(
                        "image migration input is not an allowed regular file: "
                        f"{archive_path}:{layer_name}:{member.name}"
                    )
                migration_bytes_seen[0] += member.size
                if migration_bytes_seen[0] > MAX_MIGRATION_TOTAL_BYTES:
                    raise ReleaseArtifactError(
                        f"image migration inputs exceed the byte budget: {archive_path}"
                    )
                source = layer.extractfile(member)
                if source is None:  # pragma: no cover - member.isfile contract
                    raise ReleaseArtifactError(
                        f"cannot read image migration input: {archive_path}:{member.name}"
                    )
                contents = source.read(MAX_MIGRATION_FILE_BYTES + 1)
                if len(contents) != member.size:
                    raise ReleaseArtifactError(
                        f"image migration input size changed: {archive_path}:{member.name}"
                    )
                layer_files[path] = contents
                if len(layer_files) > MAX_MIGRATION_FILES:
                    raise ReleaseArtifactError(
                        f"image contains too many migration files: {archive_path}"
                    )
            _apply_overlay_deletions(
                files,
                inherited_deletions=inherited_deletions,
                opaque_directories=opaque_directories,
            )
            files.update(layer_files)
            if (
                sum(
                    current.parent == IMAGE_MIGRATION_ROOT and current.suffix == ".py"
                    for current in files
                )
                > MAX_MIGRATION_FILES
            ):
                raise ReleaseArtifactError(
                    f"image contains too many migration files: {archive_path}"
                )
    except (tarfile.TarError, OSError, EOFError) as error:
        raise ReleaseArtifactError(
            f"cannot inspect image layer {archive_path}:{layer_name}: {error}"
        ) from error


def _image_migration_files(  # noqa: PLR0912
    archive_path: Path,
) -> dict[PurePosixPath, bytes]:
    files: dict[PurePosixPath, bytes] = {}
    migration_bytes_seen = [0]
    expanded_bytes_seen = [0]
    layer_members_seen = [0]
    overlay_directives_seen = [0]
    try:
        _preflight_docker_archive(archive_path)
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members: list[tarfile.TarInfo] = []
            total_bytes = 0
            for member in archive:
                if len(members) >= MAX_DOCKER_ARCHIVE_MEMBERS:
                    raise ReleaseArtifactError(
                        f"image archive has too many members: {archive_path}"
                    )
                if member.size < 0 or member.size > MAX_DOCKER_ARCHIVE_MEMBER_BYTES:
                    raise ReleaseArtifactError(
                        f"image archive member exceeds the byte budget: {archive_path}:{member.name}"
                    )
                total_bytes += member.size
                if total_bytes > MAX_DOCKER_ARCHIVE_TOTAL_BYTES:
                    raise ReleaseArtifactError(
                        f"image archive exceeds the total byte budget: {archive_path}"
                    )
                members.append(member)
            names = [member.name for member in members]
            if len(names) != len(set(names)):
                raise ReleaseArtifactError(
                    f"image archive contains duplicate members: {archive_path}"
                )
            by_name = {member.name: member for member in members}
            manifest_member = by_name.get("manifest.json")
            if (
                manifest_member is None
                or not manifest_member.isfile()
                or manifest_member.size > MAX_RELEASE_JSON_BYTES
            ):
                raise ReleaseArtifactError(
                    f"image archive manifest.json exceeds the JSON byte limit: {archive_path}"
                )
            manifest_stream = archive.extractfile(manifest_member)
            if manifest_stream is None:
                raise ReleaseArtifactError(
                    f"image archive has no readable manifest.json: {archive_path}"
                )
            manifest_bytes = manifest_stream.read(MAX_RELEASE_JSON_BYTES + 1)
            if len(manifest_bytes) != manifest_member.size:
                raise ReleaseArtifactError(
                    f"image archive manifest.json size is inconsistent: {archive_path}"
                )
            manifest = json.loads(manifest_bytes)
            if not isinstance(manifest, list) or len(manifest) != 1:
                raise ReleaseArtifactError(
                    f"image archive must contain exactly one image: {archive_path}"
                )
            entry = manifest[0]
            layers = entry.get("Layers") if isinstance(entry, dict) else None
            if (
                not isinstance(layers, list)
                or not layers
                or not all(isinstance(layer, str) and layer for layer in layers)
                or len(layers) != len(set(layers))
            ):
                raise ReleaseArtifactError(f"image archive layer list is invalid: {archive_path}")
            for layer_name in layers:
                try:
                    layer_member = by_name[layer_name]
                except KeyError as error:
                    raise ReleaseArtifactError(
                        f"image archive layer is missing: {archive_path}:{layer_name}"
                    ) from error
                if not layer_member.isfile():
                    raise ReleaseArtifactError(
                        f"image archive layer is not a regular file: {archive_path}:{layer_name}"
                    )
                layer_stream = archive.extractfile(layer_member)
                if layer_stream is None:  # pragma: no cover - layer_member.isfile contract
                    raise ReleaseArtifactError(
                        f"image archive layer cannot be read: {archive_path}:{layer_name}"
                    )
                _apply_image_layer(
                    layer_stream,
                    archive_path=archive_path,
                    layer_name=layer_name,
                    files=files,
                    migration_bytes_seen=migration_bytes_seen,
                    expanded_bytes_seen=expanded_bytes_seen,
                    layer_members_seen=layer_members_seen,
                    overlay_directives_seen=overlay_directives_seen,
                )
    except (
        tarfile.TarError,
        json.JSONDecodeError,
        RecursionError,
        MemoryError,
        OSError,
        EOFError,
    ) as error:
        raise ReleaseArtifactError(
            f"cannot statically inspect API image migrations {archive_path}: {error}"
        ) from error
    return files


def _validate_image_alembic_config(contents: bytes, *, archive_path: Path) -> None:
    try:
        text = contents.decode("utf-8")
        config = configparser.RawConfigParser(interpolation=None, strict=True)
        config.read_string(text)
        script_location = config.get("alembic", "script_location").strip()
        version_locations = config.get("alembic", "version_locations", fallback="").strip()
        recursive = config.getboolean("alembic", "recursive_version_locations", fallback=False)
        sourceless = config.getboolean("alembic", "sourceless", fallback=False)
    except (UnicodeDecodeError, configparser.Error, ValueError) as error:
        raise ReleaseArtifactError(
            f"API image alembic.ini cannot be validated: {archive_path}: {error}"
        ) from error
    if script_location != "alembic" or version_locations or recursive or sourceless:
        raise ReleaseArtifactError(
            "API image Alembic path policy is unsupported; expected script_location=alembic "
            "with default non-recursive source-only versions"
        )


def _migration_import_bindings(statement: ast.Import | ast.ImportFrom) -> Iterator[str]:
    if isinstance(statement, ast.Import):
        for imported in statement.names:
            yield imported.asname or imported.name.partition(".")[0]
        return
    for imported in statement.names:
        if imported.name != "*":
            yield imported.asname or imported.name


def _migration_import_is_safe(module: str) -> bool:
    return module in MIGRATION_SAFE_IMPORT_MODULES or any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in MIGRATION_SAFE_IMPORT_PREFIXES
    )


def _validate_migration_import(statement: ast.Import | ast.ImportFrom, *, path: str) -> None:
    bound_metadata = MIGRATION_METADATA_NAMES.intersection(_migration_import_bindings(statement))
    if bound_metadata:
        name = min(bound_metadata)
        raise ReleaseArtifactError(f"migration {path} import binds metadata name {name}")
    if isinstance(statement, ast.Import):
        modules = [imported.name for imported in statement.names]
    else:
        if (
            statement.level != 0
            or statement.module is None
            or any(imported.name == "*" for imported in statement.names)
        ):
            raise ReleaseArtifactError(f"migration {path} has an unsafe import")
        if statement.module == "__future__":
            if any(imported.name != "annotations" for imported in statement.names):
                raise ReleaseArtifactError(f"migration {path} has an unsafe future import")
            return
        modules = [statement.module]
    if not all(_migration_import_is_safe(module) for module in modules):
        raise ReleaseArtifactError(f"migration {path} imports outside the safe prefixes")


def _migration_uses_future_annotations(tree: ast.Module, *, path: str) -> bool:
    future_imports_allowed = True
    annotations_enabled = False
    for index, statement in enumerate(tree.body):
        if (
            index == 0
            and isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            continue
        if isinstance(statement, ast.ImportFrom) and statement.module == "__future__":
            if not future_imports_allowed:
                raise ReleaseArtifactError(f"migration {path} has a misplaced future import")
            _validate_migration_import(statement, path=path)
            annotations_enabled = annotations_enabled or any(
                imported.name == "annotations" for imported in statement.names
            )
            continue
        future_imports_allowed = False
    return annotations_enabled


def _eager_migration_annotation_is_safe(annotation: ast.expr) -> bool:
    pending = [annotation]
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Name):
            if node.id not in MIGRATION_SAFE_EAGER_ANNOTATION_NAMES:
                return False
            continue
        if isinstance(node, ast.Constant):
            if node.value is not None and not isinstance(node.value, str):
                return False
            continue
        if isinstance(node, ast.Subscript):
            if (
                not isinstance(node.value, ast.Name)
                or node.value.id not in MIGRATION_SAFE_EAGER_ANNOTATION_NAMES
            ):
                return False
            pending.append(node.slice)
            continue
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            pending.extend((node.left, node.right))
            continue
        if isinstance(node, ast.Tuple | ast.List):
            pending.extend(node.elts)
            continue
        return False
    return True


def _validate_eager_migration_annotation(annotation: ast.expr, *, path: str) -> None:
    if not _eager_migration_annotation_is_safe(annotation):
        raise ReleaseArtifactError(f"migration {path} has an executable import-time annotation")


def _function_annotations(
    statement: ast.FunctionDef | ast.AsyncFunctionDef,
) -> Iterator[ast.expr]:
    arguments = statement.args
    for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs):
        if argument.annotation is not None:
            yield argument.annotation
    if arguments.vararg is not None and arguments.vararg.annotation is not None:
        yield arguments.vararg.annotation
    if arguments.kwarg is not None and arguments.kwarg.annotation is not None:
        yield arguments.kwarg.annotation
    if statement.returns is not None:
        yield statement.returns


def _validate_migration_module_contract(  # noqa: PLR0912
    tree: ast.Module, *, path: str
) -> None:
    future_annotations = _migration_uses_future_annotations(tree, path=path)
    direct_metadata_stores = {
        id(target)
        for statement in tree.body
        for target in (
            statement.targets
            if isinstance(statement, ast.Assign)
            else [statement.target]
            if isinstance(statement, ast.AnnAssign)
            else []
        )
        if isinstance(target, ast.Name) and target.id in MIGRATION_METADATA_NAMES
    }
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Store | ast.Del)
            and node.id in MIGRATION_METADATA_NAMES
            and id(node) not in direct_metadata_stores
        ):
            raise ReleaseArtifactError(
                f"migration {path} modifies {node.id} outside its literal declaration"
            )

    for index, statement in enumerate(tree.body):
        if isinstance(statement, ast.Import | ast.ImportFrom):
            _validate_migration_import(statement, path=path)
            continue
        if isinstance(statement, ast.Expr):
            if (
                index == 0
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            ):
                continue
            raise ReleaseArtifactError(f"migration {path} has import-time expression execution")
        if isinstance(statement, ast.Assign | ast.AnnAssign):
            value = statement.value
            if value is None:
                raise ReleaseArtifactError(f"migration {path} has an uninitialized assignment")
            if isinstance(statement, ast.AnnAssign) and not future_annotations:
                _validate_eager_migration_annotation(statement.annotation, path=path)
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            if not targets or not all(isinstance(target, ast.Name) for target in targets):
                raise ReleaseArtifactError(
                    f"migration {path} has a non-name module assignment target"
                )
            try:
                ast.literal_eval(value)
            except (ValueError, TypeError, RecursionError) as error:
                raise ReleaseArtifactError(
                    f"migration {path} has a non-literal module assignment"
                ) from error
            continue
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            if statement.name in MIGRATION_METADATA_NAMES:
                raise ReleaseArtifactError(
                    f"migration {path} function binds metadata name {statement.name}"
                )
            if (
                statement.decorator_list
                or statement.args.defaults
                or any(default is not None for default in statement.args.kw_defaults)
                or getattr(statement, "type_params", [])
            ):
                raise ReleaseArtifactError(
                    f"migration {path} has import-time function construction"
                )
            if not future_annotations:
                for annotation in _function_annotations(statement):
                    _validate_eager_migration_annotation(annotation, path=path)
            continue
        raise ReleaseArtifactError(
            f"migration {path} has unsupported import-time statement {type(statement).__name__}"
        )


def _literal_module_assignment(tree: ast.Module, name: str, *, path: str) -> object:
    values: list[ast.expr] = []
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == name for target in statement.targets
            ):
                values.append(statement.value)
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == name
            and statement.value is not None
        ):
            values.append(statement.value)
    if len(values) != 1:
        raise ReleaseArtifactError(f"migration {path} must assign {name} exactly once")
    try:
        return ast.literal_eval(values[0])
    except (ValueError, TypeError, RecursionError) as error:
        raise ReleaseArtifactError(f"migration {path} has non-literal {name}") from error


def _migration_identity(contents: bytes, *, path: str) -> tuple[str, tuple[str, ...]]:
    try:
        tree = ast.parse(contents, filename=path)
    except (SyntaxError, ValueError, UnicodeDecodeError, RecursionError) as error:
        raise ReleaseArtifactError(f"migration source cannot be parsed: {path}: {error}") from error
    _validate_migration_module_contract(tree, path=path)
    revision = _literal_module_assignment(tree, "revision", path=path)
    down_revision = _literal_module_assignment(tree, "down_revision", path=path)
    branch_labels = _literal_module_assignment(tree, "branch_labels", path=path)
    depends_on = _literal_module_assignment(tree, "depends_on", path=path)
    if branch_labels is not None:
        raise ReleaseArtifactError(f"migration {path} has unsupported branch_labels")
    if depends_on is not None:
        raise ReleaseArtifactError(f"migration {path} has unsupported depends_on")
    if not isinstance(revision, str) or ALEMBIC_REVISION_PATTERN.fullmatch(revision) is None:
        raise ReleaseArtifactError(f"migration {path} has an invalid revision")
    if down_revision is None:
        parents: tuple[str, ...] = ()
    elif isinstance(down_revision, str):
        parents = (down_revision,)
    elif isinstance(down_revision, tuple | list) and all(
        isinstance(parent, str) for parent in down_revision
    ):
        parents = tuple(down_revision)
    else:
        raise ReleaseArtifactError(f"migration {path} has an invalid down_revision")
    if (
        (not parents and down_revision not in (None, (), []))
        or len(parents) != len(set(parents))
        or any(ALEMBIC_REVISION_PATTERN.fullmatch(parent) is None for parent in parents)
    ):
        raise ReleaseArtifactError(f"migration {path} has an invalid down_revision")
    return revision, parents


def _observed_alembic_head(api_archive: Path) -> str:
    files = _image_migration_files(api_archive)
    alembic_config = files.get(IMAGE_ALEMBIC_CONFIG)
    if alembic_config is None:
        raise ReleaseArtifactError("API image does not contain /app/alembic.ini")
    _validate_image_alembic_config(alembic_config, archive_path=api_archive)
    migration_sources = {
        path: contents
        for path, contents in files.items()
        if path.parent == IMAGE_MIGRATION_ROOT and path.suffix == ".py"
    }
    if not migration_sources:
        raise ReleaseArtifactError("API image contains no Alembic migration sources")
    revisions: dict[str, tuple[str, ...]] = {}
    for path, contents in sorted(migration_sources.items(), key=lambda item: str(item[0])):
        revision, parents = _migration_identity(contents, path="/" + str(path))
        if revision in revisions:
            raise ReleaseArtifactError(f"API image repeats Alembic revision {revision}")
        revisions[revision] = parents
    referenced = {parent for parents in revisions.values() for parent in parents}
    missing = sorted(referenced - revisions.keys())
    if missing:
        raise ReleaseArtifactError(f"API image Alembic graph has missing revisions: {missing}")
    heads = sorted(revisions.keys() - referenced)
    if len(heads) != 1:
        raise ReleaseArtifactError(f"expected exactly one API image Alembic head, got {heads}")

    children: dict[str, list[str]] = {revision: [] for revision in revisions}
    remaining_parents = {revision: len(parents) for revision, parents in revisions.items()}
    for child, parents in revisions.items():
        for parent in parents:
            children[parent].append(child)
    ready = [revision for revision, count in remaining_parents.items() if count == 0]
    visited = 0
    while ready:
        revision = ready.pop()
        visited += 1
        for child in children[revision]:
            remaining_parents[child] -= 1
            if remaining_parents[child] == 0:
                ready.append(child)
    if visited != len(revisions):
        raise ReleaseArtifactError("API image Alembic graph contains a cycle")
    return heads[0]


def _candidate_reference_preexists(image: ImageArtifact, runner: Runner, *, snapshot: Path) -> bool:
    if not runner.image_exists(image.candidate_reference, cwd=snapshot):
        return False
    inspected = inspect_image(image.candidate_reference, runner, root=snapshot)
    expected = (image.image_id, image.os, image.architecture)
    actual = (inspected.image_id, inspected.os, inspected.architecture)
    if actual != expected or image.candidate_reference not in inspected.repo_tags:
        raise ReleaseArtifactError(
            f"pre-existing candidate reference mismatch for {image.component}: "
            f"expected {expected}, got {actual}"
        )
    return True


def _remove_loaded_candidate_references(
    images: Sequence[ImageArtifact], runner: Runner, *, snapshot: Path
) -> list[str]:
    errors: list[str] = []
    for image in reversed(images):
        reference = image.candidate_reference
        try:
            if not runner.image_exists(reference, cwd=snapshot):
                continue
            inspected = inspect_image(reference, runner, root=snapshot)
            if inspected.image_id != image.image_id or reference not in inspected.repo_tags:
                raise ReleaseArtifactError(
                    f"loaded candidate reference changed before cleanup: {reference}"
                )
            runner.run(["docker", "image", "rm", "--force", reference], cwd=snapshot)
            if runner.image_exists(reference, cwd=snapshot):
                raise ReleaseArtifactError(
                    f"loaded candidate reference remains after cleanup: {reference}"
                )
        except (OSError, ReleaseArtifactError) as error:
            errors.append(f"{reference}: {error}")
    return errors


def _verified_images(
    snapshot: Path,
    manifest: CandidateManifest,
    runner: Runner,
    *,
    created_references: list[ImageArtifact],
) -> tuple[list[dict[str, str]], str]:
    preexisting = {
        image.component: _candidate_reference_preexists(image, runner, snapshot=snapshot)
        for image in manifest.images
    }
    created_references.extend(
        image for image in manifest.images if not preexisting[image.component]
    )
    for image in manifest.images:
        runner.run(
            ["docker", "image", "load", "--input", str(snapshot / image.archive)],
            cwd=snapshot,
        )
    observed: list[dict[str, str]] = []
    for image in manifest.images:
        inspected = _inspect_loaded_candidate_image(image, runner, root=snapshot)
        observed.append(
            {
                "component": image.component,
                "image_id": inspected.image_id,
                "archive_sha256": _sha256_digest(snapshot / image.archive),
                "os": inspected.os,
                "architecture": inspected.architecture,
            }
        )
    if [image["component"] for image in observed] != list(COMPONENTS):
        raise ReleaseArtifactError("receipt image set is incomplete")
    api_image = next(image for image in manifest.images if image.component == "api")
    observed_head = _observed_alembic_head(snapshot / api_image.archive)
    return observed, observed_head


def _receipt_document(
    *,
    snapshot: Path,
    manifest: CandidateManifest,
    trust_fingerprint: str,
    verifier_id: str,
    verifier_tool_sha256: str,
    runner: Runner,
    created_references: list[ImageArtifact],
) -> dict[str, Any]:
    descriptor = manifest.qualification_toolchain
    if descriptor is None:
        raise ReleaseArtifactError("candidate has no signed qualification toolchain descriptor")
    receipt_producer = getattr(descriptor, "receipt_producer", None)
    if receipt_producer is None:
        raise ReleaseArtifactError(
            "candidate descriptor has no signed release receipt producer identity"
        )
    expected_tool_sha256 = "sha256:" + receipt_producer.sha256
    actual_tool_sha256 = _running_verifier_tool_sha256()
    if actual_tool_sha256 != expected_tool_sha256:
        raise ReleaseArtifactError(
            "running verifier tool SHA-256 does not match the signed candidate descriptor"
        )
    if verifier_tool_sha256 != expected_tool_sha256:
        raise ReleaseArtifactError(
            "caller-expected verifier tool SHA-256 does not match the signed candidate descriptor"
        )
    if _running_release_artifacts_sha256() != "sha256:" + descriptor.producer.sha256:
        raise ReleaseArtifactError(
            "running release artifacts SHA-256 does not match the signed candidate descriptor"
        )
    if _running_validator_sha256() != "sha256:" + descriptor.validator.sha256:
        raise ReleaseArtifactError(
            "running device point validator SHA-256 does not match the signed candidate descriptor"
        )
    images, observed_head = _verified_images(
        snapshot,
        manifest,
        runner,
        created_references=created_references,
    )
    if observed_head != manifest.alembic_head:
        raise ReleaseArtifactError(
            "statically observed API image Alembic head does not match MANIFEST.json: "
            f"expected {manifest.alembic_head}, got {observed_head}"
        )
    verification_timestamp = _verification_timestamp()
    document: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "artifact_type": RECEIPT_ARTIFACT_TYPE,
        "receipt_id": "pending",
        "verification_method": VERIFICATION_METHOD,
        "verifier_id": verifier_id,
        "verifier_tool_id": VERIFIER_TOOL_ID,
        "verifier_tool_sha256": actual_tool_sha256,
        "verified_at": verification_timestamp,
        "protected_snapshot_id": "pending",
        "publisher_principal": PUBLISHER,
        "signature_namespace": SIGNATURE_NAMESPACE,
        "signed_object": SIGNED_OBJECT,
        "signature_file": SIGNATURE_FILE,
        "release_key_fingerprint": trust_fingerprint,
        "sha256sums_sha256": _sha256_digest(snapshot / SIGNED_OBJECT),
        "signature_file_sha256": _sha256_digest(snapshot / SIGNATURE_FILE),
        "manifest_sha256": _sha256_digest(snapshot / "MANIFEST.json"),
        "package_file_set_sha256": _canonical_sha256(sorted(_package_file_set(snapshot))),
        "candidate_id": manifest.candidate_id,
        "source_commit": manifest.source_commit,
        "logical_identity": manifest.logical_identity,
        "alembic_head": manifest.alembic_head,
        "observed_alembic_head": observed_head,
        "images": images,
        "checks": [],
    }
    snapshot_id = release_receipt_protected_snapshot_id(document)
    document["protected_snapshot_id"] = snapshot_id
    document["receipt_id"] = "receipt-" + snapshot_id.removeprefix("sha256:")
    document["checks"] = [
        {"check_id": check_id, "result": "PASS", "observed_sha256": digest}
        for check_id, digest in release_receipt_check_digests(document).items()
    ]
    return document


def _write_bytes_fully(descriptor: int, value: bytes) -> None:
    remaining = memoryview(value)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:  # pragma: no cover - os.write contract
            raise OSError("receipt write made no progress")
        remaining = remaining[written:]


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return os.path.samestat(left, right) and stat.S_IFMT(left.st_mode) == stat.S_IFMT(right.st_mode)


def _read_descriptor_bytes(descriptor: int, maximum: int) -> bytes:
    position = os.lseek(descriptor, 0, os.SEEK_CUR)
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        os.lseek(descriptor, position, os.SEEK_SET)


def _validate_retained_payload(
    descriptor: int,
    payload: bytes,
    *,
    expected_link_count: int,
) -> os.stat_result:
    before = os.fstat(descriptor)
    contents = _read_descriptor_bytes(descriptor, len(payload))
    after = os.fstat(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size != len(payload)
        or before.st_nlink != expected_link_count
        or _source_stat_identity(before) != _source_stat_identity(after)
        or contents != payload
    ):
        raise ReleaseArtifactError("retained receipt output identity or payload changed")
    return after


def _write_fsynced_payload(
    descriptor: int,
    payload: bytes,
    *,
    expected_link_count: int,
) -> os.stat_result:
    _write_bytes_fully(descriptor, payload)
    os.fsync(descriptor)
    return _validate_retained_payload(
        descriptor,
        payload,
        expected_link_count=expected_link_count,
    )


def _validate_posix_publish_directory(
    directory: Path,
    descriptor: int,
    expected: os.stat_result,
) -> None:
    try:
        retained = os.fstat(descriptor)
        current = directory.stat(follow_symlinks=False)
    except OSError as error:
        raise ReleaseArtifactError(f"publish directory identity changed: {error}") from error
    if (
        not stat.S_ISDIR(retained.st_mode)
        or not _same_file_identity(retained, expected)
        or not _same_file_identity(retained, current)
    ):
        raise ReleaseArtifactError("publish directory identity changed")


def _validate_posix_bound_file(
    descriptor: int,
    directory_descriptor: int,
    output_name: str,
    payload: bytes,
    *,
    expected_link_count: int = 1,
) -> None:
    retained = _validate_retained_payload(
        descriptor, payload, expected_link_count=expected_link_count
    )
    try:
        current = os.stat(
            output_name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except OSError as error:
        raise ReleaseArtifactError(f"retained receipt path identity changed: {error}") from error
    if not stat.S_ISREG(current.st_mode) or not _same_file_identity(retained, current):
        raise ReleaseArtifactError("retained receipt path identity changed")


def _posix_link_anonymous_no_replace(
    source_descriptor: int,
    destination_descriptor: int,
    output_name: str,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    link_at = libc.linkat
    link_at.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    link_at.restype = ctypes.c_int
    if (
        link_at(
            source_descriptor,
            b"",
            destination_descriptor,
            os.fsencode(output_name),
            POSIX_AT_EMPTY_PATH,
        )
        == 0
    ):
        return
    error_code = ctypes.get_errno()
    if error_code == errno.EEXIST:
        raise FileExistsError(error_code, "receipt already exists", output_name)
    unavailable = {
        errno.EINVAL,
        errno.ENOENT,
        errno.ENOSYS,
        errno.EPERM,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    if error_code in unavailable:
        raise _AnonymousPublishUnavailableError
    raise OSError(error_code, os.strerror(error_code), output_name)


def _unlink_posix_bound_name(
    descriptor: int,
    directory_descriptor: int,
    name: str,
) -> None:
    retained = os.fstat(descriptor)
    current = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    if not stat.S_ISREG(current.st_mode) or not _same_file_identity(retained, current):
        raise ReleaseArtifactError("named receipt temporary identity changed before cleanup")
    previous_links = retained.st_nlink
    os.unlink(name, dir_fd=directory_descriptor)
    after = os.fstat(descriptor)
    if not _same_file_identity(retained, after) or after.st_nlink != previous_links - 1:
        raise ReleaseArtifactError("named receipt temporary cleanup was not identity-bound")


def _publish_posix_anonymous(
    output: Path,
    payload: bytes,
    *,
    directory_descriptor: int,
    directory_identity: os.stat_result,
) -> None:
    if not OS_O_TMPFILE:
        raise _AnonymousPublishUnavailableError
    receipt_descriptor: int | None = None
    published = False
    try:
        temporary_flags = os.O_RDWR | OS_O_TMPFILE | getattr(os, "O_CLOEXEC", 0)
        try:
            receipt_descriptor = os.open(
                ".",
                temporary_flags,
                0o600,
                dir_fd=directory_descriptor,
            )
        except OSError as error:
            unavailable = {
                errno.EISDIR,
                errno.EINVAL,
                errno.ENOENT,
                errno.ENOSYS,
                errno.EPERM,
                getattr(errno, "ENOTSUP", errno.EINVAL),
                getattr(errno, "EOPNOTSUPP", errno.EINVAL),
            }
            if error.errno in unavailable:
                raise _AnonymousPublishUnavailableError from error
            raise
        created = os.fstat(receipt_descriptor)
        if not stat.S_ISREG(created.st_mode) or created.st_nlink != 0:
            raise ReleaseArtifactError(
                "anonymous receipt temporary is not an unlinked regular file"
            )
        _write_fsynced_payload(receipt_descriptor, payload, expected_link_count=0)
        _validate_posix_publish_directory(
            output.parent,
            directory_descriptor,
            directory_identity,
        )
        _posix_link_anonymous_no_replace(
            receipt_descriptor,
            directory_descriptor,
            output.name,
        )
        published = True
        _validate_posix_bound_file(
            receipt_descriptor,
            directory_descriptor,
            output.name,
            payload,
        )
        _validate_posix_publish_directory(
            output.parent,
            directory_descriptor,
            directory_identity,
        )
        _sync_bound_publish_directory(directory_descriptor)
        _validate_posix_bound_file(
            receipt_descriptor,
            directory_descriptor,
            output.name,
            payload,
        )
        _validate_posix_publish_directory(
            output.parent,
            directory_descriptor,
            directory_identity,
        )
    except FileExistsError as error:
        raise ReleaseArtifactError(
            f"release verification receipt already exists: {output}"
        ) from error
    except ReleaseArtifactError as error:
        if published:
            error.add_note(
                "complete published receipt retained because POSIX cannot safely unlink by name"
            )
            raise _ReceiptPublishedError(str(error)) from error
        raise
    except OSError as error:
        release_error = ReleaseArtifactError(
            f"cannot publish release verification receipt: {error}"
        )
        if published:
            release_error.add_note(
                "complete published receipt retained because POSIX cannot safely unlink by name"
            )
            raise _ReceiptPublishedError(str(release_error)) from error
        raise release_error from error
    finally:
        if receipt_descriptor is not None:
            os.close(receipt_descriptor)


def _publish_posix_named(
    output: Path,
    payload: bytes,
    *,
    directory_descriptor: int,
    directory_identity: os.stat_result,
) -> None:
    temporary_name = f".{output.name}.tmp-{uuid.uuid4().hex}"
    temporary_flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    receipt_descriptor: int | None = None
    temporary_exists = False
    published = False
    try:
        receipt_descriptor = os.open(
            temporary_name,
            temporary_flags,
            0o600,
            dir_fd=directory_descriptor,
        )
        temporary_exists = True
        created = os.fstat(receipt_descriptor)
        if not stat.S_ISREG(created.st_mode) or created.st_nlink != 1:
            raise ReleaseArtifactError("named receipt temporary is not a private regular file")
        _write_fsynced_payload(receipt_descriptor, payload, expected_link_count=1)
        _validate_posix_publish_directory(
            output.parent,
            directory_descriptor,
            directory_identity,
        )
        os.link(
            temporary_name,
            output.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        published = True
        _validate_posix_bound_file(
            receipt_descriptor,
            directory_descriptor,
            output.name,
            payload,
            expected_link_count=2,
        )
        _unlink_posix_bound_name(
            receipt_descriptor,
            directory_descriptor,
            temporary_name,
        )
        temporary_exists = False
        _validate_posix_bound_file(
            receipt_descriptor,
            directory_descriptor,
            output.name,
            payload,
        )
        _validate_posix_publish_directory(
            output.parent,
            directory_descriptor,
            directory_identity,
        )
        _sync_bound_publish_directory(directory_descriptor)
        _validate_posix_bound_file(
            receipt_descriptor,
            directory_descriptor,
            output.name,
            payload,
        )
        _validate_posix_publish_directory(
            output.parent,
            directory_descriptor,
            directory_identity,
        )
    except FileExistsError as error:
        raise ReleaseArtifactError(
            f"release verification receipt already exists: {output}"
        ) from error
    except ReleaseArtifactError as error:
        if published:
            error.add_note(
                "complete published receipt retained because POSIX cannot safely unlink by name"
            )
            raise _ReceiptPublishedError(str(error)) from error
        raise
    except OSError as error:
        release_error = ReleaseArtifactError(
            f"cannot publish release verification receipt: {error}"
        )
        if published:
            release_error.add_note(
                "complete published receipt retained because POSIX cannot safely unlink by name"
            )
            raise _ReceiptPublishedError(str(release_error)) from error
        raise release_error from error
    finally:
        if receipt_descriptor is not None and temporary_exists:
            active_error = sys.exception()
            try:
                _unlink_posix_bound_name(
                    receipt_descriptor,
                    directory_descriptor,
                    temporary_name,
                )
            except (OSError, ReleaseArtifactError) as cleanup_error:
                if active_error is not None:
                    active_error.add_note(
                        "named receipt temporary cleanup failed: " + str(cleanup_error)
                    )
                else:  # pragma: no cover - successful path removes the temporary above
                    raise
        if receipt_descriptor is not None:
            os.close(receipt_descriptor)


def _publish_posix_no_replace(output: Path, payload: bytes) -> None:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_descriptor: int | None = None
    try:
        directory_descriptor = os.open(output.parent, directory_flags)
        directory_identity = os.fstat(directory_descriptor)
        _validate_posix_publish_directory(
            output.parent,
            directory_descriptor,
            directory_identity,
        )
        try:
            _publish_posix_anonymous(
                output,
                payload,
                directory_descriptor=directory_descriptor,
                directory_identity=directory_identity,
            )
        except _AnonymousPublishUnavailableError:
            _validate_posix_publish_directory(
                output.parent,
                directory_descriptor,
                directory_identity,
            )
            _publish_posix_named(
                output,
                payload,
                directory_descriptor=directory_descriptor,
                directory_identity=directory_identity,
            )
    except ReleaseArtifactError:
        raise
    except OSError as error:
        raise ReleaseArtifactError(
            f"cannot publish release verification receipt: {error}"
        ) from error
    finally:
        if directory_descriptor is not None:
            os.close(directory_descriptor)


def _win_kernel32() -> Any:
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _win_create_handle(
    path: Path,
    *,
    desired_access: int,
    share_mode: int,
    creation_disposition: int,
    flags_and_attributes: int,
) -> int:
    kernel32 = _win_kernel32()
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        os.fspath(path),
        desired_access,
        share_mode,
        None,
        creation_disposition,
        flags_and_attributes,
        None,
    )
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(handle)


def _win_close_handle(handle: int) -> None:
    kernel32 = _win_kernel32()
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    if not close_handle(ctypes.c_void_p(handle)):
        raise ctypes.WinError(ctypes.get_last_error())


def _win_descriptor_handle(descriptor: int) -> int:
    msvcrt = importlib.import_module("msvcrt")
    return int(msvcrt.get_osfhandle(descriptor))


def _win_handle_attributes(handle: int) -> _WinFileAttributeTagInfo:
    kernel32 = _win_kernel32()
    get_information = kernel32.GetFileInformationByHandleEx
    get_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    get_information.restype = ctypes.c_int
    information = _WinFileAttributeTagInfo()
    if not get_information(
        ctypes.c_void_p(handle),
        WIN_FILE_ATTRIBUTE_TAG_INFO_CLASS,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return information


def _win_mark_handle_for_deletion(handle: int) -> None:
    kernel32 = _win_kernel32()
    set_information = kernel32.SetFileInformationByHandle
    set_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    set_information.restype = ctypes.c_int
    information = _WinFileDispositionInfo(1)
    if not set_information(
        ctypes.c_void_p(handle),
        WIN_FILE_DISPOSITION_INFO_CLASS,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        raise ctypes.WinError(ctypes.get_last_error())


def _win_rename_handle_no_replace(
    handle: int,
    output: Path,
) -> None:
    kernel32 = _win_kernel32()
    set_information = kernel32.SetFileInformationByHandle
    set_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    set_information.restype = ctypes.c_int
    encoded_name = os.fspath(output).encode("utf-16le")
    size = _WinFileRenameInfo.file_name.offset + len(encoded_name) + 2
    buffer = ctypes.create_string_buffer(size)
    information = _WinFileRenameInfo.from_buffer(buffer)
    information.flags = 0
    information.root_directory = None
    information.file_name_length = len(encoded_name)
    ctypes.memmove(
        ctypes.addressof(buffer) + _WinFileRenameInfo.file_name.offset,
        encoded_name,
        len(encoded_name),
    )
    if not set_information(
        ctypes.c_void_p(handle),
        WIN_FILE_RENAME_INFO_EX_CLASS,
        buffer,
        size,
    ):
        error_code = ctypes.get_last_error()
        if error_code in (WIN_ERROR_FILE_EXISTS, WIN_ERROR_ALREADY_EXISTS):
            raise FileExistsError(error_code, "receipt already exists", os.fspath(output))
        if error_code == WIN_ERROR_INVALID_PARAMETER:
            raise ReleaseArtifactError(
                "Windows retained-handle no-replace rename is unavailable; refusing path fallback"
            )
        raise ctypes.WinError(error_code)


def _sync_bound_publish_directory(descriptor: int) -> None:
    if os.name != "nt":
        os.fsync(descriptor)


def _validate_windows_bound_file(
    descriptor: int,
    path: Path,
    payload: bytes,
) -> None:
    retained = _validate_retained_payload(descriptor, payload, expected_link_count=1)
    try:
        current = path.stat(follow_symlinks=False)
    except OSError as error:
        raise ReleaseArtifactError(f"retained receipt path identity changed: {error}") from error
    attributes = int(getattr(current, "st_file_attributes", 0))
    if attributes & WIN_FILE_ATTRIBUTE_REPARSE_POINT or not _same_file_identity(retained, current):
        raise ReleaseArtifactError("retained receipt path identity changed")


def _open_windows_publish_directory(path: Path) -> int:
    handle = _win_create_handle(
        path,
        desired_access=(
            WIN_FILE_LIST_DIRECTORY
            | WIN_FILE_ADD_FILE
            | WIN_FILE_TRAVERSE
            | WIN_FILE_READ_ATTRIBUTES
        ),
        share_mode=WIN_FILE_SHARE_READ | WIN_FILE_SHARE_WRITE,
        creation_disposition=WIN_OPEN_EXISTING,
        flags_and_attributes=(WIN_FILE_FLAG_BACKUP_SEMANTICS | WIN_FILE_FLAG_OPEN_REPARSE_POINT),
    )
    transferred = False
    try:
        attributes = _win_handle_attributes(handle)
        if (
            not attributes.file_attributes & WIN_FILE_ATTRIBUTE_DIRECTORY
            or attributes.file_attributes & WIN_FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ReleaseArtifactError("publish directory is not a regular bound directory")
        msvcrt = importlib.import_module("msvcrt")
        descriptor = int(msvcrt.open_osfhandle(handle, os.O_RDONLY))
        transferred = True
        return descriptor
    finally:
        if not transferred:
            _win_close_handle(handle)


def _validate_windows_publish_directory(path: Path, descriptor: int) -> None:
    retained = os.fstat(descriptor)
    try:
        current = path.stat(follow_symlinks=False)
    except OSError as error:
        raise ReleaseArtifactError(f"publish directory identity changed: {error}") from error
    attributes = int(getattr(current, "st_file_attributes", 0))
    if attributes & WIN_FILE_ATTRIBUTE_REPARSE_POINT or not _same_file_identity(retained, current):
        raise ReleaseArtifactError("publish directory identity changed")


def _open_windows_receipt_temporary(output: Path) -> int:
    temporary = output.parent / f".{output.name}.tmp-{uuid.uuid4().hex}"
    handle = _win_create_handle(
        temporary,
        desired_access=WIN_GENERIC_READ | WIN_GENERIC_WRITE | WIN_DELETE,
        share_mode=WIN_FILE_SHARE_READ,
        creation_disposition=WIN_CREATE_NEW,
        flags_and_attributes=WIN_FILE_ATTRIBUTE_NORMAL,
    )
    descriptor: int | None = None
    try:
        msvcrt = importlib.import_module("msvcrt")
        descriptor = int(
            msvcrt.open_osfhandle(
                handle,
                os.O_RDWR | getattr(os, "O_BINARY", 0),
            )
        )
        handle = _win_descriptor_handle(descriptor)
        created = os.fstat(descriptor)
        current = temporary.stat(follow_symlinks=False)
        if not _same_file_identity(created, current):
            raise ReleaseArtifactError("created receipt temporary identity is not bound")
        return descriptor
    except BaseException:
        cleanup_handle = _win_descriptor_handle(descriptor) if descriptor is not None else handle
        try:
            _win_mark_handle_for_deletion(cleanup_handle)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            else:
                _win_close_handle(handle)
        raise


def _close_windows_receipt_descriptor(
    descriptor: int,
    *,
    delete: bool,
    active_error: BaseException | None,
) -> None:
    if delete:
        try:
            _win_mark_handle_for_deletion(_win_descriptor_handle(descriptor))
        except OSError as cleanup_error:
            if active_error is None:  # pragma: no cover - delete requires an active failure
                raise
            active_error.add_note("retained receipt handle cleanup failed: " + str(cleanup_error))
    os.close(descriptor)


def _publish_windows_no_replace(output: Path, payload: bytes) -> None:
    directory_descriptor: int | None = None
    file_descriptor: int | None = None
    completed = False
    published = False
    try:
        directory_descriptor = _open_windows_publish_directory(output.parent)
        _validate_windows_publish_directory(output.parent, directory_descriptor)
        file_descriptor = _open_windows_receipt_temporary(output)
        _write_fsynced_payload(file_descriptor, payload, expected_link_count=1)
        _validate_windows_publish_directory(output.parent, directory_descriptor)
        _win_rename_handle_no_replace(
            _win_descriptor_handle(file_descriptor),
            output,
        )
        published = True
        _validate_windows_bound_file(file_descriptor, output, payload)
        os.fsync(file_descriptor)
        _validate_windows_bound_file(file_descriptor, output, payload)
        _validate_windows_publish_directory(output.parent, directory_descriptor)
        _sync_bound_publish_directory(directory_descriptor)
        completed = True
    except FileExistsError as error:
        raise ReleaseArtifactError(
            f"release verification receipt already exists: {output}"
        ) from error
    except ReleaseArtifactError:
        raise
    except OSError as error:
        raise ReleaseArtifactError(
            f"cannot publish release verification receipt: {error}"
        ) from error
    finally:
        active_error = sys.exception()
        if file_descriptor is not None:
            _close_windows_receipt_descriptor(
                file_descriptor,
                delete=not completed,
                active_error=active_error,
            )
        if directory_descriptor is not None:
            os.close(directory_descriptor)
        if published and not completed and active_error is None:  # pragma: no cover - invariant
            raise ReleaseArtifactError("published receipt validation did not complete")


def _publish_no_replace(output: Path, payload: bytes) -> None:
    if not output.name or output.name in {".", ".."}:
        raise ReleaseArtifactError("release verification receipt output name is invalid")
    if os.name == "nt":
        _publish_windows_no_replace(output, payload)
    else:
        _publish_posix_no_replace(output, payload)


@contextmanager
def _candidate_operation_lock(lock_directory: Path, candidate_id: str) -> Iterator[None]:
    with candidate_tag_operation_lock(lock_directory, candidate_id):
        yield


def _system_candidate_operation_lock_root() -> Path:
    return system_candidate_tag_lock_root()


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def produce_release_verification_receipt(  # noqa: PLR0912, PLR0915
    *,
    package: Path,
    output_directory: Path,
    runner: Runner,
    trust_directory: Path,
    signing_identity: Path,
    verifier_id: str,
    verifier_key_id: str,
    verifier_tool_sha256: str,
    require_system_trust: bool = True,
) -> Path:
    """Verify one immutable candidate snapshot and publish its signed sidecar receipt."""

    verifier_id = _validate_identifier(verifier_id, label="verifier ID")
    verifier_key_id = _validate_identifier(verifier_key_id, label="verifier key ID")
    if SHA256_DIGEST_PATTERN.fullmatch(verifier_tool_sha256) is None:
        raise ReleaseArtifactError("verifier tool SHA-256 is invalid")
    identity_bytes, identity_key_blob = _read_agent_identity(signing_identity)
    output_directory = _validate_atomic_publish_root(output_directory.absolute())
    package = package.absolute()
    resolved_package = package.resolve()
    if _is_within(output_directory, resolved_package) or _is_within(
        resolved_package, output_directory
    ):
        raise ReleaseArtifactError(
            "release verification receipt directory and candidate must be path-disjoint"
        )
    trust = _load_release_trust(trust_directory)
    if require_system_trust:
        _validate_system_trust_permissions(trust)
        lock_directory = _system_candidate_operation_lock_root()
    else:
        lock_directory = output_directory
    output: Path | None = None
    receipt_published = False
    with _protected_candidate_snapshot(package, parent=output_directory) as snapshot:
        created_references: list[ImageArtifact] = []
        operation_lock: Any | None = None
        try:
            manifest = _verify_snapshot_contents(
                snapshot,
                runner,
                trust=trust,
                validate_compose=True,
            )
            pending_lock = _candidate_operation_lock(lock_directory, manifest.candidate_id)
            pending_lock.__enter__()
            operation_lock = pending_lock
            document = _receipt_document(
                snapshot=snapshot,
                manifest=manifest,
                trust_fingerprint=trust.fingerprint,
                verifier_id=verifier_id,
                verifier_tool_sha256=verifier_tool_sha256,
                runner=runner,
                created_references=created_references,
            )
            output = output_directory / f"{manifest.candidate_id}{RECEIPT_FILE_SUFFIX}"
            if os.path.lexists(output):
                raise ReleaseArtifactError(f"release verification receipt already exists: {output}")
            document["signature"] = _sign_receipt_message(
                output_directory=output_directory,
                candidate_id=manifest.candidate_id,
                identity_bytes=identity_bytes,
                identity_key_blob=identity_key_blob,
                verifier_id=verifier_id,
                verifier_key_id=verifier_key_id,
                message=release_receipt_signature_message(document),
                runner=runner,
            )
            receipt = ReleaseVerificationReceipt.model_validate(document)
            payload = canonical_json_bytes(receipt.model_dump(mode="json"))
            _publish_no_replace(output, payload)
            receipt_published = True
        except BaseException as error:
            cleanup_errors = []
            if not getattr(error, "receipt_published", False):
                cleanup_errors = _remove_loaded_candidate_references(
                    created_references,
                    runner,
                    snapshot=snapshot,
                )
            if cleanup_errors:
                error.add_note(
                    "loaded candidate reference cleanup failed: " + "; ".join(cleanup_errors)
                )
            raise
        finally:
            if operation_lock is not None:
                active_error = sys.exception()
                try:
                    operation_lock.__exit__(None, None, None)
                except BaseException as lock_error:
                    message = (
                        f"candidate operation lock release failed: {lock_directory}: {lock_error}"
                    )
                    if active_error is not None:
                        active_error.add_note(message)
                    elif receipt_published:
                        raise _ReceiptPublishedError(message) from lock_error
                    else:
                        raise ReleaseArtifactError(message) from lock_error
        # A successful receipt is also the authenticated load step used by the
        # target deployment workflow. Keep verified tags available to Compose.
        assert output is not None
        return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--signing-identity", type=Path, required=True)
    parser.add_argument("--verifier-id", required=True)
    parser.add_argument("--verifier-key-id", required=True)
    parser.add_argument("--verifier-tool-sha256", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        output = produce_release_verification_receipt(
            package=args.package,
            output_directory=args.output_directory,
            runner=SubprocessRunner(),
            trust_directory=_system_trust_directory(),
            signing_identity=args.signing_identity,
            verifier_id=args.verifier_id,
            verifier_key_id=args.verifier_key_id,
            verifier_tool_sha256=args.verifier_tool_sha256,
        )
    except ReleaseArtifactError as error:
        print(f"release verification receipt error: {error}", file=sys.stderr)
        for note in getattr(error, "__notes__", ()):
            print(f"release verification receipt note: {note}", file=sys.stderr)
        return 1
    print(f"Release verification receipt created: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
