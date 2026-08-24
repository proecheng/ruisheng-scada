"""Build and verify immutable offline deployment candidates."""

from __future__ import annotations

import argparse
import base64
import binascii
import ctypes
import gzip
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

CANDIDATE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}\Z")
PLATFORM_PATTERN = re.compile(
    r"(?P<os>[a-z0-9][a-z0-9._-]*)/(?P<architecture>[a-z0-9][a-z0-9._-]*)\Z"
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")

COMPONENTS = ("postgres", "redis", "api", "gw", "web")
SERVICES = ("postgres", "redis", "migrate", "api", "gw", "web")
APP_COMPONENTS = ("api", "gw", "web")
API_SERVICE_REFERENCE_COUNT = 2
IMAGE_ENV_KEYS = {
    "postgres": "POSTGRES_IMAGE",
    "redis": "REDIS_IMAGE",
    "api": "API_IMAGE",
    "gw": "GW_IMAGE",
    "web": "WEB_IMAGE",
}
FIXED_PACKAGE_FILES = {
    ".env.prod.example",
    "MANIFEST.json",
    "MANIFEST.md",
    "SHA256SUMS",
    "SHA256SUMS.sig",
    "docker-compose.prod.yml",
    "nginx.conf",
    "site-acceptance-profile.md.example",
    "site-health-acl.conf.example",
    "site-network.override.yml",
    "site-serial-hardware.json.example",
    "site-serial.env.example",
    "site-serial.override.yml",
    "setup-customer.md",
    "install_serial_hardware_task.ps1",
    "serial_hardware_attach.ps1",
    "validate-network-boundary.py",
    "validate_serial_hardware.py",
    "verify-candidate.ps1",
    "verify-candidate.sh",
}
HASHED_FIXED_FILES = FIXED_PACKAGE_FILES - {"SHA256SUMS", "SHA256SUMS.sig"}

PUBLISHER = "ruisheng-release"
SIGNATURE_NAMESPACE = "ruisheng-candidate-v1"
SIGNATURE_SCHEME = "openssh-sshsig"
SIGNATURE_KEY_TYPE = "ssh-ed25519"
SIGNED_OBJECT = "SHA256SUMS"
SIGNATURE_FILE = "SHA256SUMS.sig"
ALLOWED_SIGNERS_FILE = "release-allowed-signers"
FINGERPRINT_FILE = "release-key-fingerprint"
FINGERPRINT_PATTERN = re.compile(r"SHA256:[A-Za-z0-9+/]{43}\Z")
SSH_STRING_LENGTH_BYTES = 4
ED25519_PUBLIC_KEY_BYTES = 32
MANIFEST_SCHEMA_VERSION = 2
SSHSIG_ARMOR_LINE_WIDTH = 70


class ReleaseArtifactError(RuntimeError):
    """Raised when a candidate violates the release artifact contract."""


class Runner(Protocol):
    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        input_bytes: bytes | None = None,
    ) -> str: ...

    def image_exists(self, image: str, *, cwd: Path) -> bool: ...

    def save_image(self, image: str, destination: Path, *, cwd: Path) -> None: ...


class SubprocessRunner:
    """Production command runner; tests inject a deterministic fake."""

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        input_bytes: bytes | None = None,
    ) -> str:
        command_env = os.environ.copy()
        if env:
            command_env.update(env)
        try:
            result = subprocess.run(
                list(args),
                cwd=cwd,
                env=command_env,
                check=True,
                capture_output=True,
                input=input_bytes,
                timeout=600,
            )
        except FileNotFoundError as error:
            raise ReleaseArtifactError(f"required command not found: {args[0]}") from error
        except subprocess.TimeoutExpired as error:
            raise ReleaseArtifactError(f"command timed out: {' '.join(args)}") from error
        except subprocess.CalledProcessError as error:
            details = error.stderr or error.stdout or b"no output"
            if isinstance(details, bytes):
                details = details.decode("utf-8", errors="replace")
            details = details.strip()
            raise ReleaseArtifactError(
                f"command failed ({error.returncode}): {' '.join(args)}: {details}"
            ) from error
        return result.stdout.decode("utf-8", errors="replace").strip()

    def image_exists(self, image: str, *, cwd: Path) -> bool:
        try:
            result = subprocess.run(
                ["docker", "image", "inspect", image, "--format", "{{json .Id}}"],
                cwd=cwd,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
        except FileNotFoundError as error:
            raise ReleaseArtifactError("required command not found: docker") from error
        except subprocess.TimeoutExpired as error:
            raise ReleaseArtifactError(
                f"candidate image tag preflight timed out: {image}"
            ) from error
        if result.returncode == 0:
            return True
        details = (result.stderr or result.stdout or "no output").strip()
        if re.search(r"\bno such (?:image|object)\b", details, flags=re.IGNORECASE):
            return False
        raise ReleaseArtifactError(
            f"candidate image tag preflight failed ({result.returncode}) for {image}: {details}"
        )

    def save_image(self, image: str, destination: Path, *, cwd: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryFile() as stderr_file:
                process = subprocess.Popen(
                    ["docker", "image", "save", image],
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=stderr_file,
                )
                if process.stdout is None:  # pragma: no cover - subprocess invariant
                    raise ReleaseArtifactError("docker image save did not expose stdout")
                process_stdout = process.stdout
                copy_errors: list[BaseException] = []
                destination_created = threading.Event()

                def stream_archive() -> None:
                    try:
                        with (
                            process_stdout,
                            destination.open("xb") as raw_output,
                            gzip.GzipFile(fileobj=raw_output, mode="wb") as compressed,
                        ):
                            destination_created.set()
                            shutil.copyfileobj(process_stdout, compressed)
                    except BaseException as error:  # surfaced on the calling thread below
                        copy_errors.append(error)

                copy_thread = threading.Thread(target=stream_archive, daemon=True)
                copy_thread.start()
                try:
                    return_code = process.wait(timeout=600)
                except subprocess.TimeoutExpired as error:
                    process.kill()
                    process.wait()
                    copy_thread.join()
                    if destination_created.is_set():
                        destination.unlink(missing_ok=True)
                    raise ReleaseArtifactError(
                        f"docker image save timed out for {image}"
                    ) from error
                copy_thread.join()
                if copy_errors:
                    if destination_created.is_set():
                        destination.unlink(missing_ok=True)
                    if isinstance(copy_errors[0], FileExistsError):
                        raise ReleaseArtifactError(f"archive already exists: {destination}")
                    raise ReleaseArtifactError(
                        f"failed to compress Docker image archive for {image}: {copy_errors[0]}"
                    ) from copy_errors[0]
                if return_code != 0:
                    stderr_file.seek(0)
                    details = stderr_file.read().decode("utf-8", errors="replace").strip()
                    destination.unlink(missing_ok=True)
                    raise ReleaseArtifactError(
                        f"docker image save failed ({return_code}) for {image}: "
                        f"{details or 'no output'}"
                    )
        except FileNotFoundError as error:
            raise ReleaseArtifactError("required command not found: docker") from error


@dataclass(frozen=True)
class ImageArtifact:
    component: str
    source_reference: str
    repo_digest: str | None
    candidate_reference: str
    image_id: str
    os: str
    architecture: str
    archive: str
    sha256: str


@dataclass(frozen=True)
class CandidateManifest:
    schema_version: int
    candidate_id: str
    source_commit: str
    generated_at: str
    target_os: str
    target_architecture: str
    alembic_head: str
    logical_identity: str
    tools: dict[str, str]
    authenticity: dict[str, str]
    images: tuple[ImageArtifact, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InspectedImage:
    image_id: str
    os: str
    architecture: str
    repo_digests: tuple[str, ...]
    repo_tags: tuple[str, ...]


@dataclass(frozen=True)
class ArchiveIdentity:
    image_id: str
    os: str
    architecture: str
    repo_tags: tuple[str, ...]


@dataclass(frozen=True)
class ReleaseTrustAnchor:
    directory: Path
    allowed_signers: Path
    fingerprint_file: Path
    fingerprint: str
    public_key_line: bytes
    allowed_signers_bytes: bytes


def _decode_ssh_string(value: bytes, offset: int = 0) -> tuple[bytes, int]:
    if len(value) - offset < SSH_STRING_LENGTH_BYTES:
        raise ReleaseArtifactError("release public key blob is truncated")
    length = int.from_bytes(value[offset : offset + SSH_STRING_LENGTH_BYTES], "big")
    start = offset + SSH_STRING_LENGTH_BYTES
    end = start + length
    if length > len(value) - start:
        raise ReleaseArtifactError("release public key blob is truncated")
    return value[start:end], end


def _load_release_trust(trust_directory: Path) -> ReleaseTrustAnchor:
    if trust_directory.is_symlink() or not trust_directory.is_dir():
        raise ReleaseArtifactError("release trust path must be a regular external directory")
    allowed_signers = trust_directory / ALLOWED_SIGNERS_FILE
    fingerprint_file = trust_directory / FINGERPRINT_FILE
    for path in (allowed_signers, fingerprint_file):
        if path.is_symlink() or not path.is_file():
            raise ReleaseArtifactError(f"release trust file is missing or linked: {path.name}")
    try:
        allowed_bytes = allowed_signers.read_bytes()
        fingerprint_bytes = fingerprint_file.read_bytes()
    except OSError as error:
        raise ReleaseArtifactError(f"cannot read release trust anchor: {error}") from error
    try:
        allowed_line = allowed_bytes.decode("ascii")
        fingerprint_line = fingerprint_bytes.decode("ascii")
    except UnicodeDecodeError as error:
        raise ReleaseArtifactError("release trust anchor must contain ASCII only") from error
    allowed_match = re.fullmatch(
        rf"{re.escape(PUBLISHER)} {re.escape(SIGNATURE_KEY_TYPE)} ([A-Za-z0-9+/]+={{0,2}})\n",
        allowed_line,
    )
    if allowed_match is None:
        raise ReleaseArtifactError(
            "release-allowed-signers must contain exactly the approved principal and one "
            "ssh-ed25519 key"
        )
    try:
        key_blob = base64.b64decode(allowed_match.group(1), validate=True)
    except binascii.Error as error:
        raise ReleaseArtifactError("release public key is not valid base64") from error
    key_type, offset = _decode_ssh_string(key_blob)
    public_key, offset = _decode_ssh_string(key_blob, offset)
    if (
        key_type != SIGNATURE_KEY_TYPE.encode("ascii")
        or len(public_key) != ED25519_PUBLIC_KEY_BYTES
        or offset != len(key_blob)
    ):
        raise ReleaseArtifactError("release public key is not a canonical ssh-ed25519 key")
    derived_fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key_blob).digest()).decode(
        "ascii"
    ).rstrip("=")
    fingerprint_match = re.fullmatch(r"(SHA256:[A-Za-z0-9+/]{43})\n", fingerprint_line)
    if fingerprint_match is None:
        raise ReleaseArtifactError("release-key-fingerprint must contain one SHA256 fingerprint")
    fingerprint = fingerprint_match.group(1)
    if fingerprint != derived_fingerprint:
        raise ReleaseArtifactError("release trust fingerprint does not match allowed-signers")
    return ReleaseTrustAnchor(
        directory=trust_directory.absolute(),
        allowed_signers=allowed_signers.absolute(),
        fingerprint_file=fingerprint_file.absolute(),
        fingerprint=fingerprint,
        public_key_line=(f"{SIGNATURE_KEY_TYPE} {allowed_match.group(1)}\n".encode("ascii")),
        allowed_signers_bytes=allowed_bytes,
    )


def _ensure_external_trust(package: Path, trust: ReleaseTrustAnchor) -> None:
    package = package.resolve()
    trust_directory = trust.directory.resolve()
    if trust_directory == package or package in trust_directory.parents:
        raise ReleaseArtifactError("release trust anchor must be outside the candidate package")


def _validate_fixed_system_tool(path: Path) -> None:
    for current in (path, *path.parents):
        if current.is_symlink() or not current.exists():
            raise ReleaseArtifactError(f"fixed system tool path is missing or linked: {current}")
        if os.name != "nt":
            metadata = current.stat()
            if metadata.st_uid != 0 or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                raise ReleaseArtifactError(
                    f"fixed system tool path has unsafe ownership or permissions: {current}"
                )


def _system_ssh_keygen() -> Path:
    if os.name == "nt":
        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
        if length <= 0 or length >= len(buffer):
            raise ReleaseArtifactError("cannot resolve the Windows system directory")
        path = Path(buffer.value) / "OpenSSH" / "ssh-keygen.exe"
    else:
        path = Path("/usr/bin/ssh-keygen")
    if path.is_symlink() or not path.is_file():
        raise ReleaseArtifactError(f"fixed system OpenSSH ssh-keygen is unavailable: {path}")
    _validate_fixed_system_tool(path)
    return path


def _validate_system_trust_permissions(trust: ReleaseTrustAnchor) -> None:
    expected = _system_trust_directory().absolute()
    if os.path.normcase(str(trust.directory)) != os.path.normcase(str(expected)):
        raise ReleaseArtifactError("system verification must use the fixed release trust path")
    if os.name == "nt":
        return
    for path in (
        trust.directory,
        trust.allowed_signers,
        trust.fingerprint_file,
        *trust.directory.parents,
    ):
        if path.is_symlink():
            raise ReleaseArtifactError(f"system release trust path is linked: {path}")
        metadata = path.stat()
        if metadata.st_uid != 0 or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ReleaseArtifactError(
                f"system release trust path has unsafe ownership or permissions: {path}"
            )


def _system_protected_workdir() -> Path:
    if os.name == "nt":
        raise ReleaseArtifactError(
            "Windows verification must use the ACL-validating external "
            r"C:\ProgramData\Ruisheng\bin\verify-publisher.ps1 bootstrap"
        )
    workdir = Path("/var/lib/ruisheng/work")
    try:
        workdir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as error:
        raise ReleaseArtifactError(
            f"cannot create protected candidate snapshot directory: {error}"
        ) from error
    for path in (workdir, *workdir.parents):
        if path.is_symlink() or not path.is_dir():
            raise ReleaseArtifactError(f"protected candidate snapshot path is linked: {path}")
        metadata = path.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise ReleaseArtifactError(
                f"protected candidate snapshot path is not root protected: {path}"
            )
    try:
        os.chmod(workdir, 0o700)
    except OSError as error:
        raise ReleaseArtifactError(
            f"cannot protect candidate snapshot directory: {error}"
        ) from error
    return workdir


WINDOWS_PUBLISH_ROOT_VALIDATOR = r"""
$ErrorActionPreference = "Stop"
$current = Get-Item -Force -LiteralPath $env:RUISHENG_PUBLISH_ROOT
$allowedSids = @(
    [Security.Principal.WindowsIdentity]::GetCurrent().User.Value,
    "S-1-5-18",
    "S-1-5-32-544",
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
) | Select-Object -Unique
$directUnsafeRights = [Security.AccessControl.FileSystemRights]::CreateFiles -bor
    [Security.AccessControl.FileSystemRights]::CreateDirectories -bor
    [Security.AccessControl.FileSystemRights]::AppendData -bor
    [Security.AccessControl.FileSystemRights]::WriteData -bor
    [Security.AccessControl.FileSystemRights]::WriteAttributes -bor
    [Security.AccessControl.FileSystemRights]::WriteExtendedAttributes -bor
    [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
    [Security.AccessControl.FileSystemRights]::Delete -bor
    [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
    [Security.AccessControl.FileSystemRights]::TakeOwnership
$ancestorUnsafeRights = [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
    [Security.AccessControl.FileSystemRights]::Delete -bor
    [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
    [Security.AccessControl.FileSystemRights]::TakeOwnership
$isDirect = $true
while ($null -ne $current) {
    if (($current.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "publish root path is linked: $($current.FullName)"
    }
    $acl = Get-Acl -LiteralPath $current.FullName
    $ownerSid = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($ownerSid -notin $allowedSids) {
        throw "publish root has an unapproved owner: $($current.FullName)"
    }
    $unsafeRights = if ($isDirect) { $directUnsafeRights } else { $ancestorUnsafeRights }
    foreach ($rule in $acl.Access) {
        if (($rule.PropagationFlags -band
                [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0) {
            continue
        }
        if ($rule.AccessControlType -ne "Allow" -or
            ($rule.FileSystemRights -band $unsafeRights) -eq 0) {
            continue
        }
        try {
            $sid = $rule.IdentityReference.Translate(
                [Security.Principal.SecurityIdentifier]
            ).Value
        } catch {
            throw "publish root has an unresolvable replacement identity: $($current.FullName)"
        }
        if ($sid -notin $allowedSids) {
            throw "publish root permits replacement by an unapproved identity: $($current.FullName)"
        }
    }
    $isDirect = $false
    $current = $current.Parent
}
"""


def _windows_system_powershell() -> Path:
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if length <= 0 or length >= len(buffer):
        raise ReleaseArtifactError("cannot resolve the Windows system directory")
    path = Path(buffer.value) / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if path.is_symlink() or not path.is_file():
        raise ReleaseArtifactError(f"fixed system PowerShell is unavailable: {path}")
    _validate_fixed_system_tool(path)
    return path


def _validate_atomic_publish_root(output_root: Path) -> Path:
    if output_root.is_symlink() or not output_root.is_dir():
        raise ReleaseArtifactError("candidate publish root must be a regular directory")
    for path in (output_root, *output_root.parents):
        if path.is_symlink() or not path.is_dir():
            raise ReleaseArtifactError(f"candidate publish root path is missing or linked: {path}")
    if os.name == "nt":
        powershell = _windows_system_powershell()
        system_root = str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows")).resolve())
        result = subprocess.run(
            [
                str(powershell),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                WINDOWS_PUBLISH_ROOT_VALIDATOR,
            ],
            capture_output=True,
            check=False,
            env={
                "PATH": str(powershell.parent),
                "RUISHENG_PUBLISH_ROOT": str(output_root),
                "SYSTEMROOT": system_root,
                "WINDIR": system_root,
            },
            timeout=30,
        )
        if result.returncode != 0:
            details = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
            raise ReleaseArtifactError(
                "candidate publish root is not protected from replacement"
                + (f": {details}" if details else "")
            )
    else:
        trusted_uids = {0, os.geteuid()}  # type: ignore[attr-defined]
        for path in (output_root, *output_root.parents):
            metadata = path.stat()
            if metadata.st_uid not in trusted_uids or metadata.st_mode & 0o022:
                raise ReleaseArtifactError(
                    f"candidate publish root has unsafe ownership or permissions: {path}"
                )
    return output_root.resolve()


def _validate_sshsig_file(path: Path) -> None:
    try:
        value = path.read_bytes()
    except OSError as error:
        raise ReleaseArtifactError(f"cannot read SSH signature: {error}") from error
    header = b"-----BEGIN SSH SIGNATURE-----\n"
    footer = b"-----END SSH SIGNATURE-----\n"
    if not value.startswith(header) or not value.endswith(footer):
        raise ReleaseArtifactError("SSH signature armor is not canonical")
    body = value[len(header) : -len(footer)]
    lines = body.splitlines()
    if not lines:
        raise ReleaseArtifactError("SSH signature armor is not canonical")
    try:
        decoded = base64.b64decode(b"".join(lines), validate=True)
    except binascii.Error as error:
        raise ReleaseArtifactError("SSH signature armor is invalid base64") from error
    if not decoded.startswith(b"SSHSIG"):
        raise ReleaseArtifactError("SSH signature payload is invalid")
    encoded = base64.b64encode(decoded)
    canonical = (
        header
        + b"\n".join(
            encoded[offset : offset + SSHSIG_ARMOR_LINE_WIDTH]
            for offset in range(0, len(encoded), SSHSIG_ARMOR_LINE_WIDTH)
        )
        + b"\n"
        + footer
    )
    if value != canonical:
        raise ReleaseArtifactError("SSH signature armor is not canonical")


def _verify_publisher_signature(package: Path, trust: ReleaseTrustAnchor, runner: Runner) -> bytes:
    sums_path = package / SIGNED_OBJECT
    signature_path = package / SIGNATURE_FILE
    if sums_path.is_symlink() or not sums_path.is_file():
        raise ReleaseArtifactError("publisher authenticity FAILED: SHA256SUMS is missing or linked")
    if signature_path.is_symlink() or not signature_path.is_file():
        raise ReleaseArtifactError(
            "publisher authenticity FAILED: SHA256SUMS.sig is missing or linked"
        )
    try:
        _validate_sshsig_file(signature_path)
    except ReleaseArtifactError as error:
        raise ReleaseArtifactError(f"publisher authenticity FAILED: {error}") from error
    try:
        signed_bytes = sums_path.read_bytes()
    except OSError as error:
        raise ReleaseArtifactError(
            f"publisher authenticity FAILED: cannot read SHA256SUMS: {error}"
        ) from error
    anchor_copy = package.parent / f".approved-allowed-signers-{uuid.uuid4().hex}"
    try:
        with anchor_copy.open("xb") as output:
            output.write(trust.allowed_signers_bytes)
        os.chmod(anchor_copy, 0o600)
        ssh_keygen = _system_ssh_keygen()
        runner.run(
            [
                str(ssh_keygen),
                "-Y",
                "verify",
                "-f",
                str(anchor_copy),
                "-I",
                PUBLISHER,
                "-n",
                SIGNATURE_NAMESPACE,
                "-s",
                str(signature_path),
            ],
            cwd=package,
            input_bytes=signed_bytes,
        )
    except (OSError, ReleaseArtifactError) as error:
        raise ReleaseArtifactError(f"publisher authenticity FAILED: {error}") from error
    finally:
        anchor_copy.unlink(missing_ok=True)
    return signed_bytes


def _sign_sha256sums(
    package: Path, signing_identity: Path, trust: ReleaseTrustAnchor, runner: Runner
) -> None:
    if signing_identity.is_symlink() or not signing_identity.is_file():
        raise ReleaseArtifactError("signing identity is missing or linked")
    if signing_identity.suffix.casefold() != ".pub":
        raise ReleaseArtifactError(
            "signing identity must be an agent-backed OpenSSH public key (.pub)"
        )
    try:
        identity_bytes = signing_identity.read_bytes()
    except OSError as error:
        raise ReleaseArtifactError(f"cannot read signing identity: {error}") from error
    identity_fields = identity_bytes.removesuffix(b"\n").split(maxsplit=2)
    if (
        len(identity_fields) not in {2, 3}
        or not identity_bytes.endswith(b"\n")
        or b"\n" in identity_bytes[:-1]
        or b"\r" in identity_bytes
        or identity_fields[:2] != trust.public_key_line.rstrip(b"\n").split()
    ):
        raise ReleaseArtifactError(
            "signing identity does not match the approved agent-backed release key"
        )
    signature_path = package / SIGNATURE_FILE
    identity_snapshot = package / ".release-signing-identity.pub"
    signature_path.unlink(missing_ok=True)
    identity_snapshot.unlink(missing_ok=True)
    try:
        with identity_snapshot.open("xb") as snapshot:
            snapshot.write(identity_bytes)
        os.chmod(identity_snapshot, 0o600)
        ssh_keygen = _system_ssh_keygen()
        runner.run(
            [
                str(ssh_keygen),
                "-Y",
                "sign",
                "-U",
                "-f",
                str(identity_snapshot),
                "-n",
                SIGNATURE_NAMESPACE,
                str(package / SIGNED_OBJECT),
            ],
            cwd=package,
        )
    finally:
        identity_snapshot.unlink(missing_ok=True)
    if signature_path.is_symlink() or not signature_path.is_file():
        raise ReleaseArtifactError("ssh-keygen did not create SHA256SUMS.sig")
    _verify_publisher_signature(package, trust, runner)


def validate_candidate_id(candidate_id: str) -> str:
    if CANDIDATE_ID_PATTERN.fullmatch(candidate_id) is None:
        raise ReleaseArtifactError(
            "candidate ID must be 1-63 lowercase letters, digits, dots, underscores, or hyphens"
        )
    return candidate_id


def parse_target_platform(value: str) -> tuple[str, str]:
    match = PLATFORM_PATTERN.fullmatch(value)
    if match is None:
        raise ReleaseArtifactError("target platform must use the form os/architecture")
    return match.group("os"), match.group("architecture")


def candidate_image_references(candidate_id: str) -> dict[str, str]:
    validate_candidate_id(candidate_id)
    return {component: f"ruisheng-candidate/{component}:{candidate_id}" for component in COMPONENTS}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_relative_path(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise ReleaseArtifactError(f"unsafe package path: {value!r}")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ReleaseArtifactError(f"unsafe package path: {value!r}")
    return value


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        contents = path.read_bytes()
    except OSError as error:
        raise ReleaseArtifactError(f"invalid JSON file {path}: {error}") from error
    return _read_json_object_bytes(contents, label=str(path))


def _read_json_object_bytes(contents: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(contents.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseArtifactError(f"invalid JSON file {label}: {error}") from error
    if not isinstance(value, dict):
        raise ReleaseArtifactError(f"JSON root must be an object: {label}")
    return value


OCI_IMAGE_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
OCI_IMAGE_CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
IN_TOTO_MEDIA_TYPE = "application/vnd.in-toto+json"
CONTAINERD_SUBJECT_ANNOTATION = "io.containerd.manifest.subject"
IN_TOTO_PREDICATE_ANNOTATION = "in-toto.io/predicate-type"
IN_TOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v0.1"
SLSA_PROVENANCE_V1 = "https://slsa.dev/provenance/v1"
OCI_SCHEMA_VERSION = 2


def _read_archive_sha256_blob(
    archive: tarfile.TarFile,
    path: Path,
    digest: object,
    *,
    label: str,
    allow_missing: bool = False,
) -> bytes | None:
    if not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        raise ReleaseArtifactError(f"archive {label} digest is invalid: {path}")
    blob_name = f"blobs/sha256/{digest.removeprefix('sha256:')}"
    try:
        member = archive.getmember(blob_name)
    except KeyError as error:
        if allow_missing:
            return None
        raise ReleaseArtifactError(
            f"archive {label} blob is missing: {path}:{blob_name}"
        ) from error
    stream = archive.extractfile(member)
    if stream is None:
        raise ReleaseArtifactError(
            f"archive {label} blob is not a regular file: {path}:{blob_name}"
        )
    contents = stream.read()
    if f"sha256:{hashlib.sha256(contents).hexdigest()}" != digest:
        raise ReleaseArtifactError(f"archive {label} digest mismatch: {path}")
    return contents


def _parse_archive_json_object(contents: bytes, path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(contents)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseArtifactError(f"archive {label} is invalid JSON: {path}") from error
    if not isinstance(value, dict):
        raise ReleaseArtifactError(f"archive {label} root is invalid: {path}")
    return value


def _validate_slsa_provenance_statement(
    statement: dict[str, Any], path: Path, main_manifest_digest: str
) -> None:
    subjects = statement.get("subject")
    if (
        statement.get("_type") != IN_TOTO_STATEMENT_TYPE
        or statement.get("predicateType") != SLSA_PROVENANCE_V1
        or not isinstance(statement.get("predicate"), dict)
        or not isinstance(subjects, list)
        or not subjects
    ):
        raise ReleaseArtifactError(f"archive provenance statement is invalid: {path}")
    expected_subject = main_manifest_digest.removeprefix("sha256:")
    subject_digests: list[str] = []
    for subject in subjects:
        digest = subject.get("digest") if isinstance(subject, dict) else None
        name = subject.get("name") if isinstance(subject, dict) else None
        sha256 = digest.get("sha256") if isinstance(digest, dict) else None
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
        ):
            raise ReleaseArtifactError(f"archive provenance statement is invalid: {path}")
        subject_digests.append(sha256)
    if expected_subject not in subject_digests:
        raise ReleaseArtifactError(f"archive provenance statement subject mismatch: {path}")


def _resolve_main_manifest_digest(
    archive: tarfile.TarFile,
    path: Path,
    descriptor_digest: str,
    descriptor_value: dict[str, Any],
    config_digest: str,
    config: dict[str, Any],
) -> str | None:
    descriptor_config = descriptor_value.get("config")
    if isinstance(descriptor_config, dict) and descriptor_config.get("digest") == config_digest:
        return descriptor_digest

    nested_descriptors = descriptor_value.get("manifests")
    if not isinstance(nested_descriptors, list):
        return None
    matching_nested: list[str] = []
    for nested in nested_descriptors:
        nested_digest = nested.get("digest") if isinstance(nested, dict) else None
        nested_bytes = _read_archive_sha256_blob(
            archive,
            path,
            nested_digest,
            label="nested descriptor",
            # Docker 29 retains source index entries for platforms whose blobs
            # are not included in a selected-platform docker save archive.
            allow_missing=True,
        )
        if nested_bytes is None:
            continue
        assert isinstance(nested_digest, str)
        nested_value = _parse_archive_json_object(nested_bytes, path, label="nested descriptor")
        nested_config = nested_value.get("config")
        if not isinstance(nested_config, dict):
            continue
        platform_value = nested.get("platform")
        if platform_value is not None and not isinstance(platform_value, dict):
            raise ReleaseArtifactError(f"archive nested descriptor platform is invalid: {path}")
        if nested_config.get("digest") != config_digest:
            nested_config_bytes = _read_archive_sha256_blob(
                archive,
                path,
                nested_config.get("digest"),
                label="nested config",
            )
            assert nested_config_bytes is not None
            nested_config_value = _parse_archive_json_object(
                nested_config_bytes, path, label="nested config"
            )
            attachment_platform = (
                nested_config_value.get("os"),
                nested_config_value.get("architecture"),
            )
            descriptor_platform = (
                (platform_value.get("os"), platform_value.get("architecture"))
                if isinstance(platform_value, dict)
                else ("unknown", "unknown")
            )
            if attachment_platform != ("unknown", "unknown") or descriptor_platform != (
                "unknown",
                "unknown",
            ):
                raise ReleaseArtifactError(
                    f"archive contains an additional runnable descriptor: {path}"
                )
            continue
        if isinstance(platform_value, dict) and (
            platform_value.get("os") != config.get("os")
            or platform_value.get("architecture") != config.get("architecture")
        ):
            raise ReleaseArtifactError(f"archive nested descriptor platform mismatch: {path}")
        matching_nested.append(nested_digest)
    if len(matching_nested) > 1:
        raise ReleaseArtifactError(f"archive main descriptor is not unique: {path}")
    return matching_nested[0] if matching_nested else None


def _validate_provenance_attachment(
    archive: tarfile.TarFile,
    path: Path,
    descriptor: dict[str, Any],
    descriptor_value: dict[str, Any],
    main_manifest_digest: str,
) -> None:
    if (
        descriptor.get("mediaType") != OCI_IMAGE_MANIFEST_MEDIA_TYPE
        or descriptor_value.get("schemaVersion") != OCI_SCHEMA_VERSION
        or descriptor_value.get("mediaType") != OCI_IMAGE_MANIFEST_MEDIA_TYPE
    ):
        raise ReleaseArtifactError(f"unsupported archive attachment: {path}")
    annotations = descriptor.get("annotations")
    subject = (
        annotations.get(CONTAINERD_SUBJECT_ANNOTATION) if isinstance(annotations, dict) else None
    )
    if subject != main_manifest_digest:
        raise ReleaseArtifactError(f"archive provenance subject mismatch: {path}")
    descriptor_platform = descriptor.get("platform")
    if descriptor_platform is not None and (
        not isinstance(descriptor_platform, dict)
        or descriptor_platform.get("os") != "unknown"
        or descriptor_platform.get("architecture") != "unknown"
    ):
        raise ReleaseArtifactError(f"archive provenance descriptor platform mismatch: {path}")
    manifest_subject = descriptor_value.get("subject")
    if manifest_subject is not None and (
        not isinstance(manifest_subject, dict)
        or manifest_subject.get("digest") != main_manifest_digest
    ):
        raise ReleaseArtifactError(f"archive provenance subject mismatch: {path}")

    config_descriptor = descriptor_value.get("config")
    if (
        not isinstance(config_descriptor, dict)
        or config_descriptor.get("mediaType") != OCI_IMAGE_CONFIG_MEDIA_TYPE
    ):
        raise ReleaseArtifactError(f"archive provenance config is invalid: {path}")
    config_bytes = _read_archive_sha256_blob(
        archive,
        path,
        config_descriptor.get("digest"),
        label="provenance config",
    )
    assert config_bytes is not None
    provenance_config = _parse_archive_json_object(config_bytes, path, label="provenance config")
    if (
        provenance_config.get("os") != "unknown"
        or provenance_config.get("architecture") != "unknown"
    ):
        raise ReleaseArtifactError(f"archive provenance config platform mismatch: {path}")

    layers = descriptor_value.get("layers")
    if not isinstance(layers, list) or len(layers) != 1:
        raise ReleaseArtifactError(f"archive provenance layers are invalid: {path}")
    for layer in layers:
        if not isinstance(layer, dict) or layer.get("mediaType") != IN_TOTO_MEDIA_TYPE:
            raise ReleaseArtifactError(f"archive provenance layer media type is invalid: {path}")
        layer_annotations = layer.get("annotations")
        if (
            not isinstance(layer_annotations, dict)
            or layer_annotations.get(IN_TOTO_PREDICATE_ANNOTATION) != SLSA_PROVENANCE_V1
        ):
            raise ReleaseArtifactError(f"archive provenance layer is invalid: {path}")
        layer_bytes = _read_archive_sha256_blob(
            archive,
            path,
            layer.get("digest"),
            label="provenance layer",
        )
        assert layer_bytes is not None
        statement = _parse_archive_json_object(layer_bytes, path, label="provenance layer")
        _validate_slsa_provenance_statement(statement, path, main_manifest_digest)


def inspect_docker_archive(  # noqa: PLR0912, PLR0915
    path: Path, expected_reference: str
) -> ArchiveIdentity:
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)):
                raise ReleaseArtifactError(f"archive contains duplicate members: {path}")
            for member in members:
                _validate_relative_path(member.name.rstrip("/") or member.name)
                if member.issym() or member.islnk():
                    raise ReleaseArtifactError(
                        f"archive contains a link member: {path}:{member.name}"
                    )
            try:
                manifest_member = archive.getmember("manifest.json")
            except KeyError as error:
                raise ReleaseArtifactError(f"archive is missing manifest.json: {path}") from error
            manifest_stream = archive.extractfile(manifest_member)
            if manifest_stream is None:
                raise ReleaseArtifactError(f"archive manifest.json is not a regular file: {path}")
            manifest_value = json.load(manifest_stream)
            if not isinstance(manifest_value, list) or len(manifest_value) != 1:
                raise ReleaseArtifactError(f"archive must contain exactly one image: {path}")
            entry = manifest_value[0]
            if not isinstance(entry, dict):
                raise ReleaseArtifactError(f"archive manifest entry is invalid: {path}")
            repo_tags = entry.get("RepoTags")
            if repo_tags != [expected_reference]:
                raise ReleaseArtifactError(
                    f"archive RepoTags mismatch for {path}: expected [{expected_reference!r}], "
                    f"got {repo_tags!r}"
                )
            config_name = entry.get("Config")
            if not isinstance(config_name, str):
                raise ReleaseArtifactError(f"archive config path is invalid: {path}")
            _validate_relative_path(config_name)
            try:
                config_member = archive.getmember(config_name)
            except KeyError as error:
                raise ReleaseArtifactError(
                    f"archive config is missing: {path}:{config_name}"
                ) from error
            config_stream = archive.extractfile(config_member)
            if config_stream is None:
                raise ReleaseArtifactError(
                    f"archive config is not a regular file: {path}:{config_name}"
                )
            config_bytes = config_stream.read()
            try:
                config = json.loads(config_bytes)
            except json.JSONDecodeError as error:
                raise ReleaseArtifactError(f"archive config is invalid JSON: {path}") from error
            if not isinstance(config, dict):
                raise ReleaseArtifactError(f"archive config root is invalid: {path}")
            config_digest = f"sha256:{hashlib.sha256(config_bytes).hexdigest()}"
            image_id = config_digest
            if "index.json" in names:
                index_stream = archive.extractfile("index.json")
                if index_stream is None:
                    raise ReleaseArtifactError(f"archive index.json is not a regular file: {path}")
                index_value = json.load(index_stream)
                descriptors = (
                    index_value.get("manifests") if isinstance(index_value, dict) else None
                )
                if not isinstance(descriptors, list) or not descriptors:
                    raise ReleaseArtifactError(
                        f"archive index must contain image descriptors: {path}"
                    )
                loaded_descriptors: list[
                    tuple[dict[str, Any], str, dict[str, Any], str | None]
                ] = []
                for descriptor in descriptors:
                    if not isinstance(descriptor, dict):
                        raise ReleaseArtifactError(f"archive descriptor is invalid: {path}")
                    descriptor_digest = descriptor.get("digest")
                    descriptor_bytes = _read_archive_sha256_blob(
                        archive,
                        path,
                        descriptor_digest,
                        label="descriptor",
                    )
                    assert descriptor_bytes is not None
                    assert isinstance(descriptor_digest, str)
                    descriptor_value = _parse_archive_json_object(
                        descriptor_bytes, path, label="descriptor"
                    )
                    main_manifest_digest = _resolve_main_manifest_digest(
                        archive,
                        path,
                        descriptor_digest,
                        descriptor_value,
                        config_digest,
                        config,
                    )
                    loaded_descriptors.append(
                        (
                            descriptor,
                            descriptor_digest,
                            descriptor_value,
                            main_manifest_digest,
                        )
                    )

                main_descriptors = [
                    loaded for loaded in loaded_descriptors if loaded[3] is not None
                ]
                if len(main_descriptors) != 1:
                    raise ReleaseArtifactError(f"archive main descriptor is not unique: {path}")
                _main_descriptor, image_id, _main_value, main_manifest_digest = main_descriptors[0]
                assert main_manifest_digest is not None
                for descriptor, _digest, descriptor_value, resolved in loaded_descriptors:
                    if resolved is None:
                        _validate_provenance_attachment(
                            archive,
                            path,
                            descriptor,
                            descriptor_value,
                            main_manifest_digest,
                        )
    except (
        tarfile.TarError,
        gzip.BadGzipFile,
        json.JSONDecodeError,
        EOFError,
        OSError,
    ) as error:
        raise ReleaseArtifactError(f"invalid Docker image archive {path}: {error}") from error

    image_os = config.get("os")
    architecture = config.get("architecture")
    if not isinstance(image_os, str) or not isinstance(architecture, str):
        raise ReleaseArtifactError(f"archive config omits OS/architecture: {path}")
    return ArchiveIdentity(
        image_id=image_id,
        os=image_os,
        architecture=architecture,
        repo_tags=(expected_reference,),
    )


def inspect_image(reference: str, runner: Runner, *, root: Path) -> InspectedImage:
    raw = runner.run(["docker", "image", "inspect", reference, "--format", "{{json .}}"], cwd=root)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ReleaseArtifactError(
            f"docker returned invalid image metadata for {reference}"
        ) from error
    if not isinstance(value, dict):
        raise ReleaseArtifactError(f"docker returned non-object image metadata for {reference}")
    image_id = value.get("Id")
    image_os = value.get("Os")
    architecture = value.get("Architecture")
    repo_digests = value.get("RepoDigests") or []
    repo_tags = value.get("RepoTags") or []
    if not isinstance(image_id, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        raise ReleaseArtifactError(f"image {reference} has an invalid ID: {image_id!r}")
    if not isinstance(image_os, str) or not isinstance(architecture, str):
        raise ReleaseArtifactError(f"image {reference} omits OS/architecture")
    if not isinstance(repo_digests, list) or not all(
        isinstance(item, str) for item in repo_digests
    ):
        raise ReleaseArtifactError(f"image {reference} has invalid RepoDigests")
    if not isinstance(repo_tags, list) or not all(isinstance(item, str) for item in repo_tags):
        raise ReleaseArtifactError(f"image {reference} has invalid RepoTags")
    return InspectedImage(
        image_id=image_id,
        os=image_os,
        architecture=architecture,
        repo_digests=tuple(sorted(repo_digests)),
        repo_tags=tuple(sorted(repo_tags)),
    )


def _repository_name(reference: str) -> str:
    without_digest = reference.split("@", maxsplit=1)[0]
    last_slash = without_digest.rfind("/")
    last_colon = without_digest.rfind(":")
    if last_colon > last_slash:
        return without_digest[:last_colon]
    return without_digest


def _matching_repo_digest(source_reference: str, inspected: InspectedImage) -> str | None:
    repository = _repository_name(source_reference)
    prefix = f"{repository}@sha256:"
    return next((item for item in inspected.repo_digests if item.startswith(prefix)), None)


def compute_logical_identity(
    *,
    candidate_id: str,
    source_commit: str,
    target_os: str,
    target_architecture: str,
    alembic_head: str,
    images: Sequence[ImageArtifact],
) -> str:
    value = {
        "alembic_head": alembic_head,
        "candidate_id": candidate_id,
        "images": [
            {
                "candidate_reference": image.candidate_reference,
                "component": image.component,
                "image_id": image.image_id,
                "repo_digest": image.repo_digest,
                "source_reference": image.source_reference,
            }
            for image in images
        ],
        "source_commit": source_commit,
        "target_architecture": target_architecture,
        "target_os": target_os,
    }
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode()).hexdigest()}"


def render_manifest_markdown(manifest: CandidateManifest) -> str:
    lines = [
        "# Offline Deployment Candidate",
        "",
        f"- Candidate ID: `{manifest.candidate_id}`",
        f"- Logical identity: `{manifest.logical_identity}`",
        f"- Source commit: `{manifest.source_commit}`",
        f"- Target platform: `{manifest.target_os}/{manifest.target_architecture}`",
        f"- Alembic head: `{manifest.alembic_head}`",
        f"- Generated at: `{manifest.generated_at}`",
        "",
        "## Generation Tools",
        "",
    ]
    lines.extend(f"- {name}: `{version}`" for name, version in sorted(manifest.tools.items()))
    lines.extend(
        [
            "",
            "## Images",
            "",
            "| Component | Source | RepoDigest | Candidate tag | Image ID | Platform | Archive | SHA-256 |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for image in manifest.images:
        lines.append(
            "| {component} | `{source}` | `{digest}` | `{candidate}` | `{image_id}` | "
            "`{image_os}/{architecture}` | `{archive}` | `{sha256}` |".format(
                component=image.component,
                source=image.source_reference,
                digest=image.repo_digest or "N/A",
                candidate=image.candidate_reference,
                image_id=image.image_id,
                image_os=image.os,
                architecture=image.architecture,
                archive=image.archive,
                sha256=image.sha256,
            )
        )
    lines.extend(
        [
            "",
            "## Authenticity Gate",
            "",
            f"- Status declared by manifest: `{manifest.authenticity['status']}`",
            f"- Scheme: `{manifest.authenticity['scheme']}`",
            f"- Publisher: `{manifest.authenticity['publisher']}`",
            f"- Namespace: `{manifest.authenticity['namespace']}`",
            f"- Key type: `{manifest.authenticity['key_type']}`",
            f"- Key fingerprint: `{manifest.authenticity['key_fingerprint']}`",
            f"- Signed object: `{manifest.authenticity['signed_object']}`",
            f"- Signature file: `{manifest.authenticity['signature_file']}`",
            "",
            "`SIGNED` is a package declaration. Only verification against the approved external "
            "trust anchor establishes publisher authenticity as `VERIFIED`.",
            "",
        ]
    )
    return "\n".join(lines)


def _replace_env_values(template: str, replacements: Mapping[str, str]) -> str:
    found: set[str] = set()
    output: list[str] = []
    for line in template.splitlines():
        key, separator, _value = line.partition("=")
        if separator and key in replacements:
            if key in found:
                raise ReleaseArtifactError(f"environment template contains duplicate key: {key}")
            output.append(f"{key}={replacements[key]}")
            found.add(key)
        else:
            output.append(line)
    missing = set(replacements) - found
    if missing:
        raise ReleaseArtifactError(
            f"environment template is missing release keys: {', '.join(sorted(missing))}"
        )
    return "\n".join(output) + "\n"


def _write_sha256sums(package: Path, paths: Sequence[str]) -> None:
    lines = [f"{sha256_file(package / relative)}  {relative}" for relative in sorted(paths)]
    (package / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _parse_sha256sums_bytes(value: bytes) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ReleaseArtifactError(f"cannot decode SHA256SUMS: {error}") from error
    if not text.endswith("\n") or "\r" in text:
        raise ReleaseArtifactError("SHA256SUMS must use canonical LF line endings")
    lines = text.removesuffix("\n").split("\n")
    for line_number, line in enumerate(lines, start=1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None:
            raise ReleaseArtifactError(f"invalid SHA256SUMS entry at line {line_number}")
        digest, relative = match.groups()
        _validate_relative_path(relative)
        if relative in values:
            raise ReleaseArtifactError(f"duplicate SHA256SUMS path: {relative}")
        values[relative] = digest
    return values


def _parse_sha256sums(path: Path) -> dict[str, str]:
    try:
        value = path.read_bytes()
    except OSError as error:
        raise ReleaseArtifactError(f"cannot read SHA256SUMS: {error}") from error
    return _parse_sha256sums_bytes(value)


def _manifest_from_dict(value: dict[str, Any]) -> CandidateManifest:
    required_keys = {
        "schema_version",
        "candidate_id",
        "source_commit",
        "generated_at",
        "target_os",
        "target_architecture",
        "alembic_head",
        "logical_identity",
        "tools",
        "authenticity",
        "images",
    }
    if set(value) != required_keys:
        raise ReleaseArtifactError(
            f"MANIFEST.json keys mismatch: expected {sorted(required_keys)}, got {sorted(value)}"
        )
    images_value = value["images"]
    if not isinstance(images_value, list):
        raise ReleaseArtifactError("MANIFEST.json images must be a list")
    image_keys = {field.name for field in ImageArtifact.__dataclass_fields__.values()}
    images: list[ImageArtifact] = []
    for index, image_value in enumerate(images_value):
        if not isinstance(image_value, dict) or set(image_value) != image_keys:
            raise ReleaseArtifactError(f"MANIFEST.json image {index} has invalid keys")
        try:
            images.append(ImageArtifact(**image_value))
        except TypeError as error:
            raise ReleaseArtifactError(f"MANIFEST.json image {index} is invalid") from error
    try:
        return CandidateManifest(
            schema_version=value["schema_version"],
            candidate_id=value["candidate_id"],
            source_commit=value["source_commit"],
            generated_at=value["generated_at"],
            target_os=value["target_os"],
            target_architecture=value["target_architecture"],
            alembic_head=value["alembic_head"],
            logical_identity=value["logical_identity"],
            tools=value["tools"],
            authenticity=value["authenticity"],
            images=tuple(images),
        )
    except TypeError as error:
        raise ReleaseArtifactError("MANIFEST.json has invalid field types") from error


def _validate_manifest(manifest: CandidateManifest) -> None:  # noqa: PLR0912, PLR0915
    string_fields = (
        manifest.candidate_id,
        manifest.source_commit,
        manifest.generated_at,
        manifest.target_os,
        manifest.target_architecture,
        manifest.alembic_head,
        manifest.logical_identity,
    )
    if not all(isinstance(value, str) for value in string_fields):
        raise ReleaseArtifactError("manifest scalar fields have invalid types")
    if (
        not isinstance(manifest.schema_version, int)
        or isinstance(manifest.schema_version, bool)
        or manifest.schema_version != MANIFEST_SCHEMA_VERSION
    ):
        raise ReleaseArtifactError("unsupported manifest schema_version")
    try:
        generated_at = datetime.fromisoformat(manifest.generated_at)
    except ValueError as error:
        raise ReleaseArtifactError("manifest generated_at must be an ISO-8601 timestamp") from error
    if generated_at.utcoffset() is None:
        raise ReleaseArtifactError("manifest generated_at must include a timezone offset")
    validate_candidate_id(manifest.candidate_id)
    if SOURCE_COMMIT_PATTERN.fullmatch(manifest.source_commit) is None:
        raise ReleaseArtifactError("manifest source_commit must be a full lowercase Git commit")
    parse_target_platform(f"{manifest.target_os}/{manifest.target_architecture}")
    if not isinstance(manifest.alembic_head, str) or not manifest.alembic_head:
        raise ReleaseArtifactError("manifest alembic_head is missing")
    if (
        not isinstance(manifest.tools, dict)
        or not manifest.tools
        or not all(
            isinstance(key, str) and isinstance(value, str) and value
            for key, value in manifest.tools.items()
        )
    ):
        raise ReleaseArtifactError("manifest tools are invalid")
    if not isinstance(manifest.authenticity, dict):
        raise ReleaseArtifactError("manifest authenticity is invalid")
    expected_authenticity = {
        "status": "SIGNED",
        "scheme": SIGNATURE_SCHEME,
        "publisher": PUBLISHER,
        "namespace": SIGNATURE_NAMESPACE,
        "key_type": SIGNATURE_KEY_TYPE,
        "signed_object": SIGNED_OBJECT,
        "signature_file": SIGNATURE_FILE,
    }
    if set(manifest.authenticity) != {*expected_authenticity, "key_fingerprint"} or any(
        manifest.authenticity.get(key) != value for key, value in expected_authenticity.items()
    ):
        raise ReleaseArtifactError("manifest authenticity contract is invalid")
    fingerprint = manifest.authenticity.get("key_fingerprint")
    if not isinstance(fingerprint, str) or FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
        raise ReleaseArtifactError("manifest release key fingerprint is invalid")
    if tuple(image.component for image in manifest.images) != COMPONENTS:
        raise ReleaseArtifactError(
            "manifest must contain postgres, redis, api, gw, and web in order"
        )
    expected_references = candidate_image_references(manifest.candidate_id)
    seen_references: set[str] = set()
    seen_ids: set[str] = set()
    seen_archives: set[str] = set()
    for image in manifest.images:
        image_string_fields = (
            image.component,
            image.source_reference,
            image.candidate_reference,
            image.image_id,
            image.os,
            image.architecture,
            image.archive,
            image.sha256,
        )
        if not all(isinstance(value, str) for value in image_string_fields) or not (
            image.repo_digest is None or isinstance(image.repo_digest, str)
        ):
            raise ReleaseArtifactError(
                f"manifest image fields have invalid types for {image.component!r}"
            )
        if image.candidate_reference != expected_references[image.component]:
            raise ReleaseArtifactError(f"candidate reference mismatch for {image.component}")
        if image.candidate_reference in seen_references:
            raise ReleaseArtifactError(
                f"duplicate candidate reference: {image.candidate_reference}"
            )
        if image.image_id in seen_ids:
            raise ReleaseArtifactError(f"duplicate image ID: {image.image_id}")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", image.image_id) is None:
            raise ReleaseArtifactError(f"invalid image ID for {image.component}")
        expected_archive = f"images/{image.component}.tar.gz"
        if image.archive != expected_archive:
            raise ReleaseArtifactError(f"archive path mismatch for {image.component}")
        _validate_relative_path(image.archive)
        if image.archive in seen_archives:
            raise ReleaseArtifactError(f"duplicate archive path: {image.archive}")
        if SHA256_PATTERN.fullmatch(image.sha256) is None:
            raise ReleaseArtifactError(f"invalid archive SHA-256 for {image.component}")
        if (image.os, image.architecture) != (
            manifest.target_os,
            manifest.target_architecture,
        ):
            raise ReleaseArtifactError(f"platform mismatch for {image.component}")
        if (
            image.repo_digest is not None
            and re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", image.repo_digest) is None
        ):
            raise ReleaseArtifactError(f"invalid RepoDigest for {image.component}")
        seen_references.add(image.candidate_reference)
        seen_ids.add(image.image_id)
        seen_archives.add(image.archive)
    expected_identity = compute_logical_identity(
        candidate_id=manifest.candidate_id,
        source_commit=manifest.source_commit,
        target_os=manifest.target_os,
        target_architecture=manifest.target_architecture,
        alembic_head=manifest.alembic_head,
        images=manifest.images,
    )
    if manifest.logical_identity != expected_identity:
        raise ReleaseArtifactError("manifest logical_identity does not match its immutable inputs")


def _package_file_set(package: Path) -> set[str]:
    if package.is_symlink() or not package.is_dir():
        raise ReleaseArtifactError(f"candidate package is not a regular directory: {package}")
    files: set[str] = set()
    for entry in package.rglob("*"):
        relative = entry.relative_to(package).as_posix()
        _validate_relative_path(relative)
        if entry.is_symlink():
            raise ReleaseArtifactError(f"candidate package contains a symlink: {relative}")
        if entry.is_file():
            files.add(relative)
        elif entry.is_dir():
            if relative != "images":
                raise ReleaseArtifactError(
                    f"candidate package contains an extra directory: {relative}"
                )
        else:
            raise ReleaseArtifactError(
                f"candidate package contains a non-regular entry: {relative}"
            )
    return files


def _validate_compose(package: Path, manifest: CandidateManifest, runner: Runner) -> None:
    compose = package / "docker-compose.prod.yml"
    env_file = package / ".env.prod.example"
    base_command = [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(compose),
    ]
    image_output = runner.run([*base_command, "config", "--images"], cwd=package)
    resolved_images = [line.strip() for line in image_output.splitlines() if line.strip()]
    expected_images = [image.candidate_reference for image in manifest.images]
    if len(resolved_images) != len(SERVICES):
        raise ReleaseArtifactError(
            f"Compose must resolve six service image references, got {len(resolved_images)}"
        )
    if set(resolved_images) != set(expected_images):
        raise ReleaseArtifactError(
            f"Compose image set mismatch: expected {sorted(expected_images)}, "
            f"got {sorted(set(resolved_images))}"
        )
    api_reference = candidate_image_references(manifest.candidate_id)["api"]
    if resolved_images.count(api_reference) != API_SERVICE_REFERENCE_COUNT:
        raise ReleaseArtifactError(
            "Compose migrate/api services must share the API candidate image"
        )
    config_output = runner.run([*base_command, "config", "--format", "json"], cwd=package)
    try:
        config = json.loads(config_output)
    except json.JSONDecodeError as error:
        raise ReleaseArtifactError("Docker Compose returned invalid JSON") from error
    services = config.get("services") if isinstance(config, dict) else None
    if not isinstance(services, dict) or set(services) != set(SERVICES):
        raise ReleaseArtifactError("candidate Compose service set is invalid")
    references = candidate_image_references(manifest.candidate_id)
    expected_service_images = {
        "postgres": references["postgres"],
        "redis": references["redis"],
        "migrate": references["api"],
        "api": references["api"],
        "gw": references["gw"],
        "web": references["web"],
    }
    expected_platform = f"{manifest.target_os}/{manifest.target_architecture}"
    for name, service in services.items():
        if not isinstance(service, dict):
            raise ReleaseArtifactError(f"Compose service is not an object: {name}")
        if service.get("image") != expected_service_images[name]:
            raise ReleaseArtifactError(f"candidate Compose image mismatch for service: {name}")
        if service.get("platform") != expected_platform:
            raise ReleaseArtifactError(f"candidate Compose platform mismatch for service: {name}")
        if "build" in service:
            raise ReleaseArtifactError(f"candidate Compose service contains build: {name}")
        if service.get("pull_policy") != "never":
            raise ReleaseArtifactError(f"candidate Compose service can pull: {name}")


def _expected_candidate_files() -> set[str]:
    return FIXED_PACKAGE_FILES | {f"images/{component}.tar.gz" for component in COMPONENTS}


@contextmanager
def _protected_candidate_snapshot(  # noqa: PLR0912, PLR0915
    package: Path, *, parent: Path | None = None
) -> Iterator[Path]:
    if package.is_symlink() or not package.is_dir():
        raise ReleaseArtifactError(
            "publisher authenticity FAILED: candidate directory is missing or linked"
        )
    package = package.resolve()
    expected_files = _expected_candidate_files()
    actual_files = _package_file_set(package)
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        extra = sorted(actual_files - expected_files)
        raise ReleaseArtifactError(
            "publisher authenticity FAILED: candidate file allowlist mismatch: "
            f"missing={missing}, extra={extra}"
        )
    initial_sizes: dict[str, int] = {}
    for relative in sorted(expected_files):
        source = package / relative
        if source.is_symlink():
            raise ReleaseArtifactError(
                f"publisher authenticity FAILED: candidate file changed or linked: {relative}"
            )
        metadata = source.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ReleaseArtifactError(
                f"publisher authenticity FAILED: candidate file is not regular: {relative}"
            )
        initial_sizes[relative] = metadata.st_size
    total_size = sum(initial_sizes.values())
    snapshot_parent = Path(parent) if parent is not None else Path(tempfile.gettempdir())
    reserve = max(64 * 1024 * 1024, total_size // 10)
    if shutil.disk_usage(snapshot_parent).free < total_size + reserve:
        raise ReleaseArtifactError(
            "publisher authenticity FAILED: insufficient free space for protected candidate "
            "snapshot"
        )
    temporary = tempfile.mkdtemp(prefix="ruisheng-verified-candidate-", dir=snapshot_parent)
    snapshot = Path(temporary)
    try:
        os.chmod(snapshot, 0o700)
        (snapshot / "images").mkdir(mode=0o700)
        try:
            for relative in sorted(expected_files):
                source = package / relative
                destination = snapshot / relative
                if source.is_symlink() or not source.is_file():
                    raise ReleaseArtifactError(
                        f"publisher authenticity FAILED: candidate file changed or linked: "
                        f"{relative}"
                    )
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(source, flags)
                try:
                    opened = os.fstat(descriptor)
                    expected_size = initial_sizes[relative]
                    if not stat.S_ISREG(opened.st_mode) or opened.st_size != expected_size:
                        raise ReleaseArtifactError(
                            "publisher authenticity FAILED: candidate file changed before "
                            f"snapshot: {relative}"
                        )
                    copied = 0
                    with (
                        os.fdopen(descriptor, "rb", closefd=False) as input_stream,
                        destination.open("xb") as output_stream,
                    ):
                        while copied < expected_size:
                            chunk = input_stream.read(min(1024 * 1024, expected_size - copied))
                            if not chunk:
                                break
                            output_stream.write(chunk)
                            copied += len(chunk)
                        if copied != expected_size or input_stream.read(1):
                            raise ReleaseArtifactError(
                                "publisher authenticity FAILED: candidate file size changed "
                                f"during snapshot: {relative}"
                            )
                    os.chmod(destination, 0o600)
                finally:
                    os.close(descriptor)
        except OSError as error:
            raise ReleaseArtifactError(
                f"publisher authenticity FAILED: cannot create protected candidate snapshot: "
                f"{error}"
            ) from error
        yield snapshot
    finally:
        if snapshot.exists():
            try:
                shutil.rmtree(snapshot)
            except OSError as error:
                active_error = sys.exception()
                if active_error is None:
                    raise ReleaseArtifactError(
                        f"protected candidate snapshot cleanup failed: {snapshot}: {error}"
                    ) from error
                active_error.add_note(
                    f"protected candidate snapshot cleanup failed: {snapshot}: {error}"
                )


def _verify_snapshot_contents(  # noqa: PLR0912
    package: Path,
    runner: Runner,
    *,
    trust: ReleaseTrustAnchor,
    validate_compose: bool,
) -> CandidateManifest:
    package = package.resolve()
    _ensure_external_trust(package, trust)
    signed_sums_bytes = _verify_publisher_signature(package, trust, runner)
    sums = _parse_sha256sums_bytes(signed_sums_bytes)
    expected_files = _expected_candidate_files()
    actual_files = _package_file_set(package)
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        extra = sorted(actual_files - expected_files)
        raise ReleaseArtifactError(
            f"publisher authenticity FAILED: candidate file allowlist mismatch: "
            f"missing={missing}, extra={extra}"
        )
    expected_hashed_files = expected_files - {"SHA256SUMS", "SHA256SUMS.sig"}
    if set(sums) != expected_hashed_files:
        missing = sorted(expected_hashed_files - set(sums))
        extra = sorted(set(sums) - expected_hashed_files)
        raise ReleaseArtifactError(
            f"publisher authenticity FAILED: SHA256SUMS allowlist mismatch: "
            f"missing={missing}, extra={extra}"
        )
    manifest_path = package / "MANIFEST.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ReleaseArtifactError(
            "publisher authenticity FAILED: candidate package is missing a regular MANIFEST.json"
        )
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as error:
        raise ReleaseArtifactError(
            f"publisher authenticity FAILED: cannot read MANIFEST.json: {error}"
        ) from error
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    if manifest_digest != sums["MANIFEST.json"]:
        raise ReleaseArtifactError(
            "publisher authenticity FAILED: SHA-256 mismatch for MANIFEST.json: "
            f"expected {sums['MANIFEST.json']}, got {manifest_digest}"
        )
    for relative, expected_digest in sums.items():
        if relative == "MANIFEST.json":
            continue
        actual_digest = sha256_file(package / relative)
        if actual_digest != expected_digest:
            raise ReleaseArtifactError(
                f"publisher authenticity FAILED: SHA-256 mismatch for {relative}: "
                f"expected {expected_digest}, got {actual_digest}"
            )
    manifest = _manifest_from_dict(
        _read_json_object_bytes(manifest_bytes, label=str(manifest_path))
    )
    _validate_manifest(manifest)
    if manifest.authenticity["key_fingerprint"] != trust.fingerprint:
        raise ReleaseArtifactError(
            "publisher authenticity FAILED: manifest fingerprint does not match approved trust"
        )
    expected_markdown = render_manifest_markdown(manifest)
    if (package / "MANIFEST.md").read_text(encoding="utf-8") != expected_markdown:
        raise ReleaseArtifactError("MANIFEST.md does not match MANIFEST.json")
    for image in manifest.images:
        if sums[image.archive] != image.sha256:
            raise ReleaseArtifactError(f"manifest/SHA256SUMS mismatch for {image.archive}")
        archive_identity = inspect_docker_archive(
            package / image.archive, image.candidate_reference
        )
        expected_identity = (image.image_id, image.os, image.architecture)
        actual_identity = (
            archive_identity.image_id,
            archive_identity.os,
            archive_identity.architecture,
        )
        if actual_identity != expected_identity:
            raise ReleaseArtifactError(
                f"archive identity mismatch for {image.component}: "
                f"expected {expected_identity}, got {actual_identity}"
            )
    if validate_compose:
        _validate_compose(package, manifest, runner)
    return manifest


def verify_package(
    package: Path,
    runner: Runner,
    *,
    trust_directory: Path,
    require_system_trust: bool = False,
) -> CandidateManifest:
    trust = _load_release_trust(trust_directory)
    snapshot_parent: Path | None = None
    if require_system_trust:
        _validate_system_trust_permissions(trust)
        snapshot_parent = _system_protected_workdir()
    with _protected_candidate_snapshot(package, parent=snapshot_parent) as snapshot:
        return _verify_snapshot_contents(snapshot, runner, trust=trust, validate_compose=True)


def load_and_verify_images(
    package: Path,
    runner: Runner,
    *,
    trust_directory: Path,
    require_system_trust: bool = False,
) -> CandidateManifest:
    trust = _load_release_trust(trust_directory)
    snapshot_parent: Path | None = None
    if require_system_trust:
        _validate_system_trust_permissions(trust)
        snapshot_parent = _system_protected_workdir()
    with _protected_candidate_snapshot(package, parent=snapshot_parent) as snapshot:
        manifest = _verify_snapshot_contents(snapshot, runner, trust=trust, validate_compose=True)
        for image in manifest.images:
            runner.run(
                [
                    "docker",
                    "image",
                    "load",
                    "--input",
                    str(snapshot / image.archive),
                ],
                cwd=snapshot,
            )
        for image in manifest.images:
            inspected = inspect_image(image.candidate_reference, runner, root=snapshot)
            expected = (image.image_id, image.os, image.architecture)
            actual = (inspected.image_id, inspected.os, inspected.architecture)
            if actual != expected:
                raise ReleaseArtifactError(
                    f"loaded image identity mismatch for {image.component}: "
                    f"expected {expected}, got {actual}"
                )
            if image.candidate_reference not in inspected.repo_tags:
                raise ReleaseArtifactError(
                    f"loaded image tag is missing for {image.component}: "
                    f"{image.candidate_reference}"
                )
        return manifest


def _git_state(root: Path, runner: Runner) -> tuple[str, str]:
    source_commit = runner.run(["git", "rev-parse", "HEAD"], cwd=root)
    if SOURCE_COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise ReleaseArtifactError("git rev-parse did not return a full lowercase commit")
    dirty = runner.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=root)
    if dirty:
        raise ReleaseArtifactError("tracked release inputs are dirty; commit or revert them first")
    return source_commit, dirty


def _alembic_head(root: Path, runner: Runner) -> str:
    output = runner.run([sys.executable, "-m", "alembic", "heads"], cwd=root)
    heads = [
        match.group(1)
        for line in output.splitlines()
        if (match := re.fullmatch(r"([A-Za-z0-9_]+) \(head\)", line.strip())) is not None
    ]
    if len(heads) != 1:
        raise ReleaseArtifactError(f"expected exactly one Alembic head, got {heads}")
    return heads[0]


def _tool_versions(root: Path, runner: Runner) -> dict[str, str]:
    return {
        "docker": runner.run(
            ["docker", "version", "--format", "{{.Client.Version}}/{{.Server.Version}}"],
            cwd=root,
        ),
        "docker_compose": runner.run(["docker", "compose", "version", "--short"], cwd=root),
        "git": runner.run(["git", "--version"], cwd=root),
        "python": platform.python_version(),
        "release_artifacts": "1",
    }


def _write_manifests(package: Path, manifest: CandidateManifest) -> None:
    serialized = json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    (package / "MANIFEST.json").write_text(serialized, encoding="utf-8", newline="\n")
    (package / "MANIFEST.md").write_text(
        render_manifest_markdown(manifest), encoding="utf-8", newline="\n"
    )


def _copy_candidate_files(root: Path, package: Path, replacements: Mapping[str, str]) -> None:
    source_deploy = root / "deploy"
    shutil.copyfile(source_deploy / "docker-compose.prod.yml", package / "docker-compose.prod.yml")
    shutil.copyfile(
        source_deploy / "site-health-acl.conf.example", package / "site-health-acl.conf.example"
    )
    shutil.copyfile(
        source_deploy / "site-network.override.yml", package / "site-network.override.yml"
    )
    shutil.copyfile(
        source_deploy / "site-serial-hardware.json.example",
        package / "site-serial-hardware.json.example",
    )
    shutil.copyfile(source_deploy / "site-serial.env.example", package / "site-serial.env.example")
    shutil.copyfile(
        source_deploy / "site-serial.override.yml", package / "site-serial.override.yml"
    )
    shutil.copyfile(source_deploy / "setup-customer.md", package / "setup-customer.md")
    shutil.copyfile(source_deploy / "verify-candidate.sh", package / "verify-candidate.sh")
    shutil.copyfile(source_deploy / "verify-candidate.ps1", package / "verify-candidate.ps1")
    shutil.copyfile(root / "ruisheng-web" / "nginx.conf", package / "nginx.conf")
    shutil.copyfile(
        root
        / "docs"
        / "superpowers"
        / "specs"
        / "spec-plan-5-customer-deployment-acceptance"
        / "site-acceptance-profile.md",
        package / "site-acceptance-profile.md.example",
    )
    shutil.copyfile(
        root / "tools" / "validate_network_boundary.py", package / "validate-network-boundary.py"
    )
    shutil.copyfile(
        root / "tools" / "validate_serial_hardware.py", package / "validate_serial_hardware.py"
    )
    shutil.copyfile(
        root / "tools" / "serial_hardware_attach.ps1", package / "serial_hardware_attach.ps1"
    )
    shutil.copyfile(
        root / "tools" / "install_serial_hardware_task.ps1",
        package / "install_serial_hardware_task.ps1",
    )
    template = (source_deploy / ".env.prod.example").read_text(encoding="utf-8")
    candidate_replacements = dict(replacements)
    # The site env is copied outside the immutable candidate; Compose resolves
    # this bind source relative to the candidate's Compose file directory.
    candidate_replacements["WEB_HEALTH_ACL_FILE"] = "../site/site-health-acl.conf"
    (package / ".env.prod.example").write_text(
        _replace_env_values(template, candidate_replacements), encoding="utf-8", newline="\n"
    )


def _ensure_candidate_tags_absent(
    references: Mapping[str, str], runner: Runner, *, root: Path
) -> None:
    for component in COMPONENTS:
        reference = references[component]
        if runner.image_exists(reference, cwd=root):
            raise ReleaseArtifactError(
                f"candidate image tag already exists for {component}: {reference}"
            )


def _remove_candidate_tags(
    references: Mapping[str, str], runner: Runner, *, root: Path
) -> list[str]:
    errors: list[str] = []
    for component in COMPONENTS:
        reference = references[component]
        try:
            if runner.image_exists(reference, cwd=root):
                runner.run(["docker", "image", "rm", "--force", reference], cwd=root)
        except ReleaseArtifactError as error:
            errors.append(f"{reference}: {error}")
    return errors


def build_candidate(  # noqa: PLR0912, PLR0915
    *,
    root: Path,
    output_root: Path,
    candidate_id: str,
    target_platform: str,
    env_file: Path,
    postgres_source: str,
    redis_source: str,
    runner: Runner,
    signing_identity: Path,
    trust_directory: Path,
    check_clean: bool = True,
    prebuilt_app_sources: Mapping[str, str] | None = None,
    pull_base_images: bool = True,
    lock_root: Path | None = None,
) -> Path:
    candidate_id = validate_candidate_id(candidate_id)
    target_os, target_architecture = parse_target_platform(target_platform)
    root = root.resolve()
    trust = _load_release_trust(trust_directory)
    if signing_identity.is_symlink():
        raise ReleaseArtifactError("signing identity is missing or linked")
    signing_identity = signing_identity.resolve()
    env_file = env_file.resolve()
    if not env_file.is_file():
        raise ReleaseArtifactError(f"production environment file does not exist: {env_file}")
    if check_clean:
        source_commit, _dirty = _git_state(root, runner)
    else:
        source_commit = runner.run(["git", "rev-parse", "HEAD"], cwd=root)
        if SOURCE_COMMIT_PATTERN.fullmatch(source_commit) is None:
            raise ReleaseArtifactError("git rev-parse did not return a full lowercase commit")
    output_root = output_root.absolute()
    if output_root.is_symlink():
        raise ReleaseArtifactError("candidate publish root must not be linked")
    final_directory = output_root / candidate_id
    if final_directory.exists():
        raise ReleaseArtifactError(f"candidate ID already exists: {final_directory}")
    references = candidate_image_references(candidate_id)
    _ensure_candidate_tags_absent(references, runner, root=root)
    try:
        output_root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ReleaseArtifactError(f"cannot create candidate publish root: {error}") from error
    output_root = _validate_atomic_publish_root(output_root)
    final_directory = output_root / candidate_id
    if final_directory.exists():
        raise ReleaseArtifactError(f"candidate ID already exists: {final_directory}")
    resolved_lock_root = (
        lock_root or Path(tempfile.gettempdir()) / "ruisheng-release-artifact-locks"
    ).resolve()
    try:
        resolved_lock_root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ReleaseArtifactError(
            f"cannot create candidate lock directory: {resolved_lock_root}: {error}"
        ) from error
    lock_path = resolved_lock_root / f"{candidate_id}.lock"
    try:
        lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise ReleaseArtifactError(
            f"candidate build already in progress or requires stale-lock cleanup: {lock_path}"
        ) from error
    except OSError as error:
        raise ReleaseArtifactError(
            f"cannot create candidate build lock: {lock_path}: {error}"
        ) from error
    try:
        try:
            os.write(lock_descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        finally:
            os.close(lock_descriptor)
    except BaseException as error:
        lock_path.unlink(missing_ok=True)
        if isinstance(error, OSError):
            raise ReleaseArtifactError(
                f"cannot initialize candidate build lock: {lock_path}: {error}"
            ) from error
        raise
    try:
        if final_directory.exists():
            raise ReleaseArtifactError(f"candidate ID already exists: {final_directory}")
        _ensure_candidate_tags_absent(references, runner, root=root)
    except BaseException:
        lock_path.unlink(missing_ok=True)
        raise
    try:
        temporary_directory = Path(
            tempfile.mkdtemp(prefix=f".{candidate_id}.tmp-", dir=final_directory.parent)
        )
    except BaseException:
        lock_path.unlink(missing_ok=True)
        raise
    compose_env = {
        "TARGET_PLATFORM": target_platform,
        **{IMAGE_ENV_KEYS[name]: reference for name, reference in references.items()},
    }
    source_references = {
        "postgres": postgres_source,
        "redis": redis_source,
        "api": f"docker-build://ruisheng-api/Dockerfile@{source_commit}",
        "gw": f"docker-build://ruisheng-gw/Dockerfile@{source_commit}",
        "web": f"docker-build://ruisheng-web/Dockerfile@{source_commit}",
    }
    published = False
    try:
        (temporary_directory / "images").mkdir()
        for component, source in (("postgres", postgres_source), ("redis", redis_source)):
            if pull_base_images:
                runner.run(
                    ["docker", "image", "pull", "--platform", target_platform, source], cwd=root
                )
            runner.run(["docker", "image", "tag", source, references[component]], cwd=root)
        if prebuilt_app_sources is None:
            runner.run(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    str(env_file),
                    "-f",
                    str(root / "docker-compose.prod.yml"),
                    "build",
                    "--pull",
                    "api",
                    "gw",
                    "web",
                ],
                cwd=root,
                env=compose_env,
            )
        else:
            if set(prebuilt_app_sources) != set(APP_COMPONENTS):
                raise ReleaseArtifactError("prebuilt app source set must be api, gw, and web")
            for component in APP_COMPONENTS:
                source = prebuilt_app_sources[component]
                source_references[component] = source
                runner.run(["docker", "image", "tag", source, references[component]], cwd=root)

        inspected_images: dict[str, InspectedImage] = {}
        for component in COMPONENTS:
            inspected = inspect_image(references[component], runner, root=root)
            if (inspected.os, inspected.architecture) != (target_os, target_architecture):
                raise ReleaseArtifactError(
                    f"platform mismatch for {component}: expected {target_platform}, "
                    f"got {inspected.os}/{inspected.architecture}"
                )
            if references[component] not in inspected.repo_tags:
                raise ReleaseArtifactError(
                    f"candidate tag did not resolve after build/tag: {references[component]}"
                )
            inspected_images[component] = inspected

        partial_images: list[ImageArtifact] = []
        for component in COMPONENTS:
            archive_relative = f"images/{component}.tar.gz"
            archive_path = temporary_directory / archive_relative
            runner.save_image(references[component], archive_path, cwd=root)
            inspected = inspected_images[component]
            archived = inspect_docker_archive(archive_path, references[component])
            expected = (inspected.image_id, inspected.os, inspected.architecture)
            actual = (archived.image_id, archived.os, archived.architecture)
            if actual != expected:
                raise ReleaseArtifactError(
                    f"exported archive identity mismatch for {component}: expected {expected}, got {actual}"
                )
            partial_images.append(
                ImageArtifact(
                    component=component,
                    source_reference=source_references[component],
                    repo_digest=_matching_repo_digest(source_references[component], inspected),
                    candidate_reference=references[component],
                    image_id=inspected.image_id,
                    os=inspected.os,
                    architecture=inspected.architecture,
                    archive=archive_relative,
                    sha256=sha256_file(archive_path),
                )
            )

        replacements = {key: compose_env[key] for key in compose_env}
        _copy_candidate_files(root, temporary_directory, replacements)
        alembic_head = _alembic_head(root, runner)
        images = tuple(partial_images)
        manifest = CandidateManifest(
            schema_version=MANIFEST_SCHEMA_VERSION,
            candidate_id=candidate_id,
            source_commit=source_commit,
            generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
            target_os=target_os,
            target_architecture=target_architecture,
            alembic_head=alembic_head,
            logical_identity=compute_logical_identity(
                candidate_id=candidate_id,
                source_commit=source_commit,
                target_os=target_os,
                target_architecture=target_architecture,
                alembic_head=alembic_head,
                images=images,
            ),
            tools=_tool_versions(root, runner),
            authenticity={
                "status": "SIGNED",
                "scheme": SIGNATURE_SCHEME,
                "publisher": PUBLISHER,
                "namespace": SIGNATURE_NAMESPACE,
                "key_type": SIGNATURE_KEY_TYPE,
                "key_fingerprint": trust.fingerprint,
                "signed_object": SIGNED_OBJECT,
                "signature_file": SIGNATURE_FILE,
            },
            images=images,
        )
        _write_manifests(temporary_directory, manifest)
        hashed_files = HASHED_FIXED_FILES | {image.archive for image in images}
        _write_sha256sums(temporary_directory, tuple(hashed_files))
        _sign_sha256sums(temporary_directory, signing_identity, trust, runner)
        with _protected_candidate_snapshot(
            temporary_directory, parent=output_root
        ) as verified_snapshot:
            _verify_snapshot_contents(verified_snapshot, runner, trust=trust, validate_compose=True)
            if check_clean:
                final_commit, _dirty = _git_state(root, runner)
                if final_commit != source_commit:
                    raise ReleaseArtifactError(
                        "tracked release inputs changed HEAD while the candidate was being built"
                    )
            shutil.rmtree(temporary_directory)
            os.replace(verified_snapshot, final_directory)
        published = True
        return final_directory
    except BaseException as error:
        shutil.rmtree(temporary_directory, ignore_errors=True)
        cleanup_errors = _remove_candidate_tags(references, runner, root=root)
        if cleanup_errors:
            error.add_note("candidate tag cleanup failed: " + "; ".join(cleanup_errors))
        raise
    finally:
        active_error = sys.exception()
        try:
            lock_path.unlink(missing_ok=True)
        except OSError as lock_error:
            if active_error is not None:
                active_error.add_note(
                    f"candidate build lock cleanup failed: {lock_path}: {lock_error}"
                )
            else:
                rollback_errors: list[str] = []
                if published:
                    try:
                        shutil.rmtree(final_directory)
                    except OSError as rollback_error:
                        rollback_errors.append(
                            f"candidate directory rollback failed: {rollback_error}"
                        )
                    rollback_errors.extend(_remove_candidate_tags(references, runner, root=root))
                release_error = ReleaseArtifactError(
                    f"candidate build lock cleanup failed: {lock_path}: {lock_error}"
                )
                if rollback_errors:
                    release_error.add_note("; ".join(rollback_errors))
                raise release_error from lock_error


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="build an offline deployment candidate")
    build.add_argument("--candidate-id", required=True)
    build.add_argument("--target-platform", required=True)
    build.add_argument("--env-file", type=Path, required=True)
    build.add_argument("--output-root", type=Path, required=True)
    build.add_argument("--postgres-source", default="timescale/timescaledb:2.16.1-pg15")
    build.add_argument("--redis-source", default="redis:7-alpine")
    build.add_argument("--signing-identity", type=Path, required=True)
    build.add_argument("--trust-directory", type=Path, required=True)
    verify = subparsers.add_parser("verify", help="verify a candidate without starting services")
    verify.add_argument("package", type=Path)
    verify.add_argument("--load", action="store_true", help="load and inspect the five images")
    return parser


def _system_trust_directory() -> Path:
    if os.name == "nt":
        return Path("C:/ProgramData/Ruisheng/trust")
    return Path("/etc/ruisheng/trust")


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    runner = SubprocessRunner()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.command == "build":
            destination = build_candidate(
                root=root,
                output_root=args.output_root,
                candidate_id=args.candidate_id,
                target_platform=args.target_platform,
                env_file=args.env_file,
                postgres_source=args.postgres_source,
                redis_source=args.redis_source,
                runner=runner,
                signing_identity=args.signing_identity,
                trust_directory=args.trust_directory,
            )
            print(f"Candidate created: {destination}")
        else:
            if os.name == "nt":
                raise ReleaseArtifactError(
                    "Windows verification must use the ACL-validating external "
                    r"C:\ProgramData\Ruisheng\bin\verify-publisher.ps1 bootstrap"
                )
            if args.load:
                manifest = load_and_verify_images(
                    args.package,
                    runner,
                    trust_directory=_system_trust_directory(),
                    require_system_trust=True,
                )
            else:
                manifest = verify_package(
                    args.package,
                    runner,
                    trust_directory=_system_trust_directory(),
                    require_system_trust=True,
                )
            print(
                f"Publisher authenticity VERIFIED and integrity verified for "
                f"{manifest.candidate_id}."
            )
    except ReleaseArtifactError as error:
        print(f"release artifact error: {error}", file=sys.stderr)
        for note in getattr(error, "__notes__", ()):  # Notes are absent from str(error).
            print(f"release artifact note: {note}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
