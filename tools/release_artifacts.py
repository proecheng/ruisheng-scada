"""Build and verify immutable offline deployment candidates."""

from __future__ import annotations

import argparse
import base64
import binascii
import ctypes
import gzip
import hashlib
import importlib
import io
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import threading
import uuid
import zlib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Protocol, cast

try:
    import winreg
except ImportError:  # pragma: no cover - Windows-only system Git discovery
    winreg = None  # type: ignore[assignment]

CANDIDATE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}\Z")
PLATFORM_PATTERN = re.compile(
    r"(?P<os>[a-z0-9][a-z0-9._-]*)/(?P<architecture>[a-z0-9][a-z0-9._-]*)\Z"
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
GIT_OBJECT_ID_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
GIT_SHA1_OBJECT_ID_LENGTH = 40

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
FIXED_PACKAGE_FILES_V2 = {
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
    "site-modbus-probe.json.example",
    "site-serial-hardware.json.example",
    "site-serial.env.example",
    "site-serial.override.yml",
    "setup-customer.md",
    "install_serial_hardware_task.ps1",
    "probe_modbus_rtu.py",
    "run_modbus_probe.ps1",
    "serial_hardware_attach.ps1",
    "validate-network-boundary.py",
    "validate_serial_hardware.py",
    "verify-candidate.ps1",
    "verify-candidate.sh",
}
QUALIFICATION_TOOLCHAIN_ARCHIVE = "qualification-toolchain.tar.gz"
QUALIFICATION_TOOLCHAIN_FORMAT = "tar+gzip"
QUALIFICATION_TOOLCHAIN_MANIFEST = "qualification-toolchain-manifest.json"
QUALIFICATION_TOOLCHAIN_MEMBERS = (
    "tools/validate_device_point_profile.py",
    "schemas/point-profile/point-profile-v1.schema.json",
    "tools/release_artifacts.py",
    "tools/release_verification_receipt.py",
    "pyproject.toml",
    "uv.lock",
)
QUALIFICATION_TOOLCHAIN_ARTIFACT_TYPE = "ruisheng.qualification-toolchain"
QUALIFICATION_TOOLCHAIN_SCHEMA_VERSION = 1
SEMANTIC_VALIDATOR_ID = "ruisheng.device-point-profile-validator/v5"
FIXED_PACKAGE_FILES = FIXED_PACKAGE_FILES_V2 | {
    QUALIFICATION_TOOLCHAIN_ARCHIVE,
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
MANIFEST_SCHEMA_VERSION = 3
LEGACY_MANIFEST_SCHEMA_VERSION = 2
SUPPORTED_MANIFEST_SCHEMA_VERSIONS = (LEGACY_MANIFEST_SCHEMA_VERSION, MANIFEST_SCHEMA_VERSION)
SSHSIG_ARMOR_LINE_WIDTH = 70
MAX_QUALIFICATION_MEMBER_BYTES = 64 * 1024 * 1024
MAX_QUALIFICATION_RUNTIME_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_RELEASE_JSON_BYTES = 4 * 1024 * 1024
MAX_QUALIFICATION_TAR_TRAILING_ZERO_BLOCKS = tarfile.RECORDSIZE // tarfile.BLOCKSIZE + 1
MIN_QUALIFICATION_TAR_TRAILING_ZERO_BLOCKS = 2
QUALIFICATION_USTAR_NAME_BYTES = 100
MAX_QUALIFICATION_TAR_BYTES = (
    (len(QUALIFICATION_TOOLCHAIN_MEMBERS) + 1) * tarfile.BLOCKSIZE
    + len(QUALIFICATION_TOOLCHAIN_MEMBERS) * MAX_QUALIFICATION_MEMBER_BYTES
    + MAX_RELEASE_JSON_BYTES
    + MAX_QUALIFICATION_TAR_TRAILING_ZERO_BLOCKS * tarfile.BLOCKSIZE
)
MAX_QUALIFICATION_GZIP_BYTES = (
    MAX_QUALIFICATION_TAR_BYTES + MAX_QUALIFICATION_TAR_BYTES // 100 + 64 * 1024
)
MAX_DOCKER_ARCHIVE_MEMBERS = 32_768
MAX_DOCKER_ARCHIVE_MEMBER_BYTES = 8 * 1024 * 1024 * 1024
MAX_DOCKER_ARCHIVE_TOTAL_BYTES = 32 * 1024 * 1024 * 1024
MAX_DOCKER_DESCRIPTOR_REFERENCES = 32_768
MAX_DOCKER_METADATA_BYTES = 64 * 1024 * 1024
MAX_QUALIFICATION_RUNTIME_FILES = 32_768
MAX_QUALIFICATION_RUNTIME_FILE_BYTES = 512 * 1024 * 1024
QUALIFICATION_ENTRYPOINTS = {
    "receipt": "tools/release_verification_receipt.py",
    "validator": "tools/validate_device_point_profile.py",
}
QUALIFICATION_RUNTIME_ARTIFACT_TYPE = "ruisheng.qualification-runtime"
QUALIFICATION_RUNTIME_SCHEMA_VERSION = 1
QUALIFICATION_RUNTIME_PYTHON_VERSION = "3.11"
QUALIFICATION_RUNTIME_MANIFEST = "qualification-runtime-manifest.json"
POSIX_QUALIFICATION_RUNTIME_ROOT = Path("/opt/ruisheng/qualification-runtime")
POSIX_QUALIFICATION_RUNTIME_PYTHON = "bin/python3.11"
POSIX_QUALIFICATION_RUNTIME_DEPENDENCIES = "lib/python3.11/site-packages"
QUALIFICATION_ALLOWED_EXIT_CODES = {
    "receipt": frozenset({0}),
    "validator": frozenset({0, 2, 3}),
}
WINDOWS_GIT_EXECUTABLE_LINKS = (
    "mingw64/bin/git.exe",
    "mingw64/bin/git-receive-pack.exe",
    "mingw64/bin/git-upload-archive.exe",
    "mingw64/bin/git-upload-pack.exe",
)
WINDOWS_GIT_RUNTIME_SHA256 = {
    "mingw64/bin/git.exe": "fc0f1cae1304fcdcf4d0749f421c5ed21471efc856301f92f56d4b844be84363",
    "mingw64/bin/libiconv-2.dll": "ff31fa811f9c07cc7fdaa68c9e8bca3a7b4fdf6e0a079a58175ea58ba139c7ae",
    "mingw64/bin/libintl-8.dll": "7744fde3df3320fda0e3b599b4aa5349b1281f93d1e5c52865a52d0d3e4a7d39",
    "mingw64/bin/libpcre2-8-0.dll": "c135a87ed0f11eae8ffc4cb469671ff0b3f5d71fab5fb024e9b1e7241ca25b52",
    "mingw64/bin/libwinpthread-1.dll": "e271f374468d584905afcdf7da96a6adeb5ee15702b39869e2003b0f102f20c4",
    "mingw64/bin/zlib1.dll": "cb7ab3788d10940df874acd97b1821bbb5ee4a91f3eec11982bb5bf7a3c96443",
}
DOCKER_ENVIRONMENT_KEYS = frozenset(
    {
        "DOCKER_CONFIG",
        "DOCKER_CLI_PLUGIN_EXTRA_DIRS",
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "XDG_CONFIG_HOME",
    }
)

WINDOWS_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
WINDOWS_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
WINDOWS_SYNCHRONIZE = 0x00100000
WINDOWS_GENERIC_READ = 0x80000000
WINDOWS_GENERIC_WRITE = 0x40000000
WINDOWS_OPEN_ALWAYS = 4
WINDOWS_FILE_ATTRIBUTE_NORMAL = 0x00000080
WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000


class _WindowsIoCounters(ctypes.Structure):
    _fields_ = [
        ("read_operation_count", ctypes.c_uint64),
        ("write_operation_count", ctypes.c_uint64),
        ("other_operation_count", ctypes.c_uint64),
        ("read_transfer_count", ctypes.c_uint64),
        ("write_transfer_count", ctypes.c_uint64),
        ("other_transfer_count", ctypes.c_uint64),
    ]


class _WindowsJobBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("per_process_user_time_limit", ctypes.c_int64),
        ("per_job_user_time_limit", ctypes.c_int64),
        ("limit_flags", ctypes.c_uint32),
        ("minimum_working_set_size", ctypes.c_size_t),
        ("maximum_working_set_size", ctypes.c_size_t),
        ("active_process_limit", ctypes.c_uint32),
        ("affinity", ctypes.c_size_t),
        ("priority_class", ctypes.c_uint32),
        ("scheduling_class", ctypes.c_uint32),
    ]


class _WindowsJobExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("basic_limit_information", _WindowsJobBasicLimitInformation),
        ("io_info", _WindowsIoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory_used", ctypes.c_size_t),
        ("peak_job_memory_used", ctypes.c_size_t),
    ]


def _windows_kernel32() -> Any:
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _close_windows_native_handle(handle: int) -> None:
    kernel32 = _windows_kernel32()
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    if not close_handle(ctypes.c_void_p(handle)):
        raise ctypes.WinError(ctypes.get_last_error())


def _create_windows_kill_on_close_job() -> int:
    kernel32 = _windows_kernel32()
    create_job = kernel32.CreateJobObjectW
    create_job.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    create_job.restype = ctypes.c_void_p
    handle = create_job(None, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    job_handle = int(handle)
    information = _WindowsJobExtendedLimitInformation()
    information.basic_limit_information.limit_flags = WINDOWS_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    set_information = kernel32.SetInformationJobObject
    set_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    set_information.restype = ctypes.c_int
    if not set_information(
        ctypes.c_void_p(job_handle),
        WINDOWS_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        error = ctypes.WinError(ctypes.get_last_error())
        _close_windows_native_handle(job_handle)
        raise error
    return job_handle


def _create_windows_process_gate() -> tuple[int, str]:
    name = f"Local\\RuishengQualification-{uuid.uuid4()}"
    kernel32 = _windows_kernel32()
    create_event = kernel32.CreateEventW
    create_event.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
    create_event.restype = ctypes.c_void_p
    handle = create_event(None, 1, 0, name)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(handle), name


def _assign_windows_process_to_job(job_handle: int, process: subprocess.Popen[bytes]) -> None:
    process_handle = getattr(process, "_handle", None)
    if process_handle is None:  # pragma: no cover - Windows subprocess invariant
        raise OSError("isolated process has no native handle")
    kernel32 = _windows_kernel32()
    assign = kernel32.AssignProcessToJobObject
    assign.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    assign.restype = ctypes.c_int
    if not assign(ctypes.c_void_p(job_handle), ctypes.c_void_p(int(process_handle))):
        raise ctypes.WinError(ctypes.get_last_error())


def _signal_windows_process_gate(gate_handle: int) -> None:
    kernel32 = _windows_kernel32()
    set_event = kernel32.SetEvent
    set_event.argtypes = [ctypes.c_void_p]
    set_event.restype = ctypes.c_int
    if not set_event(ctypes.c_void_p(gate_handle)):
        raise ctypes.WinError(ctypes.get_last_error())


def _terminate_isolated_process_tree(
    process: subprocess.Popen[bytes], *, job_handle: int | None
) -> None:
    cleanup_error: OSError | None = None
    if os.name == "nt":
        if job_handle is not None:
            try:
                _close_windows_native_handle(job_handle)
            except OSError as error:
                cleanup_error = error
        if process.poll() is None:
            try:
                process.kill()
            except OSError as error:
                cleanup_error = cleanup_error or error
    else:
        kill_process_group = getattr(os, "killpg", None)
        sigkill = getattr(signal, "SIGKILL", None)
        if kill_process_group is None or sigkill is None:  # pragma: no cover - POSIX invariant
            raise ReleaseArtifactError("POSIX process-group termination is unavailable")
        try:
            kill_process_group(process.pid, sigkill)
        except ProcessLookupError:
            pass
        except OSError as error:
            cleanup_error = error
            if process.poll() is None:
                with suppress(OSError):
                    process.kill()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired as error:
        raise ReleaseArtifactError("isolated command process tree could not be reaped") from error
    if cleanup_error is not None:
        raise ReleaseArtifactError(
            f"cannot terminate isolated command process tree: {cleanup_error}"
        ) from cleanup_error


def candidate_tag_lock_name(candidate_id: str) -> str:
    return f".{candidate_id}.candidate-tags.lock"


@contextmanager
def candidate_tag_operation_lock(lock_directory: Path, candidate_id: str) -> Iterator[None]:
    lock_path = lock_directory / candidate_tag_lock_name(candidate_id)
    if os.name == "nt":
        kernel32 = _windows_kernel32()
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
            os.fspath(lock_path),
            WINDOWS_GENERIC_READ | WINDOWS_GENERIC_WRITE,
            0,
            None,
            WINDOWS_OPEN_ALWAYS,
            WINDOWS_FILE_ATTRIBUTE_NORMAL | WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle in (None, ctypes.c_void_p(-1).value):
            raise ReleaseArtifactError(f"candidate tag operation is already active: {candidate_id}")
        lock_handle = int(handle)
        try:
            observed = os.stat(lock_path, follow_symlinks=False)
            if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
                raise ReleaseArtifactError(
                    "candidate tag operation lock is not a private regular file"
                )
            yield
        finally:
            _close_windows_native_handle(lock_handle)
        return

    directory_descriptor: int | None = None
    lock_descriptor: int | None = None
    try:
        directory_descriptor = os.open(
            lock_directory,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        lock_descriptor = os.open(
            lock_path.name,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_descriptor,
        )
        observed = os.fstat(lock_descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
            or observed.st_uid != os.geteuid()  # type: ignore[attr-defined]
            or stat.S_IMODE(observed.st_mode) & 0o077
        ):
            raise ReleaseArtifactError("candidate tag operation lock is not a private regular file")
        fcntl = importlib.import_module("fcntl")
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ReleaseArtifactError(
                f"candidate tag operation is already active: {candidate_id}"
            ) from error
        yield
    except ReleaseArtifactError:
        raise
    except OSError as error:
        raise ReleaseArtifactError(
            f"candidate tag operation cannot be locked: {candidate_id}: {error}"
        ) from error
    finally:
        if lock_descriptor is not None:
            os.close(lock_descriptor)
        if directory_descriptor is not None:
            os.close(directory_descriptor)


def system_candidate_tag_lock_root() -> Path:
    lock_root = (
        Path(r"C:\ProgramData\Ruisheng\locks")
        if os.name == "nt"
        else Path("/var/lib/ruisheng/locks")
    )
    try:
        lock_root.mkdir(mode=0o700, exist_ok=True)
    except OSError as error:
        raise ReleaseArtifactError(
            f"cannot create host-global candidate tag lock root: {error}"
        ) from error
    return _validate_atomic_publish_root(lock_root.absolute())


def _is_docker_executable(value: str) -> bool:
    return Path(value).name.casefold() in {"docker", "docker.exe"}


def _is_git_executable(value: str) -> bool:
    return Path(value).name.casefold() in {"git", "git.exe"}


def _local_docker_endpoint() -> str:
    return "npipe:////./pipe/docker_engine" if os.name == "nt" else "unix:///var/run/docker.sock"


def _system_docker() -> Path:
    path = (
        Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
        if os.name == "nt"
        else Path("/usr/bin/docker")
    )
    if path.is_symlink() or not path.is_file():
        raise ReleaseArtifactError(f"fixed system Docker CLI is unavailable: {path}")
    _validate_fixed_system_tool(path)
    if os.name == "nt":
        _validate_windows_fixed_system_tool_permissions(path)
    return path


def _system_git() -> Path:  # noqa: PLR0912
    if os.name == "nt":
        assert winreg is not None
        try:
            install_roots: set[str] = set()
            access_modes = (
                winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
                winreg.KEY_READ | getattr(winreg, "KEY_WOW64_32KEY", 0),
            )
            for access in access_modes:
                try:
                    with winreg.OpenKey(
                        winreg.HKEY_LOCAL_MACHINE,
                        r"SOFTWARE\GitForWindows",
                        0,
                        access,
                    ) as key:
                        value, value_type = winreg.QueryValueEx(key, "InstallPath")
                except FileNotFoundError:
                    continue
                if value_type != winreg.REG_SZ or not isinstance(value, str) or not value.strip():
                    raise ReleaseArtifactError(
                        "machine GitForWindows InstallPath registry value is invalid"
                    )
                install_roots.add(os.path.normcase(os.path.abspath(value.strip())))
        except OSError as error:
            raise ReleaseArtifactError(
                f"cannot resolve machine GitForWindows installation: {error}"
            ) from error
        if len(install_roots) != 1:
            raise ReleaseArtifactError("machine GitForWindows installation is missing or ambiguous")
        install_root = Path(next(iter(install_roots)))
        path = install_root / WINDOWS_GIT_EXECUTABLE_LINKS[0]
        link_metadata = path.stat(follow_symlinks=False)
        if link_metadata.st_nlink != len(WINDOWS_GIT_EXECUTABLE_LINKS):
            raise ReleaseArtifactError(
                "fixed system Git executable hard-link set has an unexpected size"
            )
        for relative in WINDOWS_GIT_EXECUTABLE_LINKS:
            linked = install_root / relative
            if linked.is_symlink() or not linked.is_file():
                raise ReleaseArtifactError(
                    f"fixed system Git executable link is unavailable: {linked}"
                )
            if not os.path.samestat(link_metadata, linked.stat(follow_symlinks=False)):
                raise ReleaseArtifactError(
                    f"fixed system Git executable hard-link set is inconsistent: {linked}"
                )
        for relative, expected_digest in WINDOWS_GIT_RUNTIME_SHA256.items():
            runtime_file = install_root / relative
            if runtime_file.is_symlink() or not runtime_file.is_file():
                raise ReleaseArtifactError(
                    f"fixed system Git runtime file is unavailable: {runtime_file}"
                )
            if relative == WINDOWS_GIT_EXECUTABLE_LINKS[0]:
                expected_links = len(WINDOWS_GIT_EXECUTABLE_LINKS)
            else:
                counterpart = install_root / relative.replace(
                    "mingw64/bin/", "mingw64/libexec/git-core/", 1
                )
                if (
                    counterpart.is_symlink()
                    or not counterpart.is_file()
                    or not os.path.samestat(
                        runtime_file.stat(follow_symlinks=False),
                        counterpart.stat(follow_symlinks=False),
                    )
                ):
                    raise ReleaseArtifactError(
                        f"fixed system Git runtime hard-link set is inconsistent: {runtime_file}"
                    )
                expected_links = 2
            if _sha256_stable_file(runtime_file, expected_links=expected_links) != expected_digest:
                raise ReleaseArtifactError(
                    f"fixed system Git runtime file is not authenticated: {runtime_file}"
                )
            _validate_windows_fixed_system_tool_permissions(runtime_file)
    else:
        path = Path("/usr/bin/git")
    if path.is_symlink() or not path.is_file():
        raise ReleaseArtifactError(f"fixed system Git CLI is unavailable: {path}")
    _validate_fixed_system_tool(path)
    return path


class ReleaseArtifactError(RuntimeError):
    """Raised when a candidate violates the release artifact contract."""


@dataclass(frozen=True)
class CommandOutcome:
    stdout: str
    stderr: str
    returncode: int


class Runner(Protocol):
    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        input_bytes: bytes | None = None,
        timeout_seconds: float | None = None,
        inherit_environment: bool = True,
    ) -> str: ...

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
    ) -> CommandOutcome: ...

    def image_exists(self, image: str, *, cwd: Path) -> bool: ...

    def save_image(self, image: str, destination: Path, *, cwd: Path) -> None: ...


class SubprocessRunner:
    """Production command runner; tests inject a deterministic fake."""

    def __init__(self) -> None:
        self._docker_config_owner: tempfile.TemporaryDirectory[str] | None = None

    def _docker_config(self) -> Path:
        if self._docker_config_owner is None:
            self._docker_config_owner = tempfile.TemporaryDirectory(
                prefix="ruisheng-docker-client-"
            )
            directory = Path(self._docker_config_owner.name)
            os.chmod(directory, 0o700)
            config = directory / "config.json"
            config.write_text("{}\n", encoding="ascii", newline="\n")
            os.chmod(config, 0o600)
        return Path(self._docker_config_owner.name)

    def _command(self, args: Sequence[str]) -> list[str]:
        command = list(args)
        if not command:
            return command
        if _is_docker_executable(command[0]):
            return [
                str(_system_docker()),
                "--host",
                _local_docker_endpoint(),
                "--config",
                str(self._docker_config()),
                *command[1:],
            ]
        if _is_git_executable(command[0]):
            return [
                str(_system_git()),
                "--no-replace-objects",
                "-c",
                "core.fsmonitor=false",
                *command[1:],
            ]
        return command

    @staticmethod
    def _command_environment() -> dict[str, str]:
        command_env = os.environ.copy()
        for key in DOCKER_ENVIRONMENT_KEYS:
            command_env.pop(key, None)
        return command_env

    @staticmethod
    def _git_command_environment() -> dict[str, str]:
        command_env = {
            key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")
        }
        command_env["GIT_NO_REPLACE_OBJECTS"] = "1"
        return command_env

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        input_bytes: bytes | None = None,
        timeout_seconds: float | None = None,
        inherit_environment: bool = True,
    ) -> str:
        outcome = self.run_outcome(
            args,
            cwd=cwd,
            env=env,
            input_bytes=input_bytes,
            timeout_seconds=timeout_seconds,
            inherit_environment=inherit_environment,
        )
        if outcome.returncode != 0:
            details = (outcome.stderr or outcome.stdout or "no output").strip()
            raise ReleaseArtifactError(
                f"command failed ({outcome.returncode}): {' '.join(self._command(args))}: {details}"
            )
        return outcome.stdout.strip()

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
        command = self._command(args)
        is_docker = bool(args) and _is_docker_executable(args[0])
        is_git = bool(args) and _is_git_executable(args[0])
        if is_docker:
            command_env = self._command_environment() if inherit_environment else {}
        elif is_git:
            command_env = self._git_command_environment() if inherit_environment else {}
        else:
            command_env = os.environ.copy() if inherit_environment else {}
        if env:
            command_env.update(
                {
                    key: value
                    for key, value in env.items()
                    if (
                        (not is_docker or key.upper() not in DOCKER_ENVIRONMENT_KEYS)
                        and (not is_git or not key.upper().startswith("GIT_"))
                    )
                }
            )
        try:
            if isolate_process_tree:
                return self._run_isolated_outcome(
                    command,
                    cwd=cwd,
                    env=command_env,
                    input_bytes=input_bytes,
                    timeout_seconds=600 if timeout_seconds is None else timeout_seconds,
                )
            result = subprocess.run(
                command,
                cwd=cwd,
                env=command_env,
                check=False,
                capture_output=True,
                input=input_bytes,
                timeout=600 if timeout_seconds is None else timeout_seconds,
            )
        except FileNotFoundError as error:
            raise ReleaseArtifactError(f"required command not found: {command[0]}") from error
        except subprocess.TimeoutExpired as error:
            raise ReleaseArtifactError(f"command timed out: {' '.join(command)}") from error
        return CommandOutcome(
            stdout=result.stdout.decode("utf-8", errors="replace"),
            stderr=result.stderr.decode("utf-8", errors="replace"),
            returncode=result.returncode,
        )

    @staticmethod
    def _run_isolated_outcome(  # noqa: PLR0912, PLR0915
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        input_bytes: bytes | None,
        timeout_seconds: float,
    ) -> CommandOutcome:
        if input_bytes is not None:
            raise ReleaseArtifactError("isolated qualification does not accept standard input")
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            job_handle: int | None = None
            gate_handle: int | None = None
            process: subprocess.Popen[bytes] | None = None
            timed_out = False
            command_env = dict(env)
            try:
                if os.name == "nt":
                    job_handle = _create_windows_kill_on_close_job()
                    gate_handle, gate_name = _create_windows_process_gate()
                    command_env["RUISHENG_PROCESS_JOB_GATE"] = gate_name
                process = subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=command_env,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=os.name != "nt",
                )
                if os.name == "nt":
                    assert job_handle is not None and gate_handle is not None
                    _assign_windows_process_to_job(job_handle, process)
                    _signal_windows_process_gate(gate_handle)
                try:
                    process.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
            except FileNotFoundError:
                raise
            except OSError as error:
                raise ReleaseArtifactError(
                    f"cannot start isolated command: {' '.join(command)}: {error}"
                ) from error
            finally:
                cleanup_error: BaseException | None = None
                if process is not None:
                    owned_job_handle = job_handle
                    job_handle = None
                    try:
                        _terminate_isolated_process_tree(process, job_handle=owned_job_handle)
                    except BaseException as error:
                        cleanup_error = error
                if gate_handle is not None:
                    try:
                        _close_windows_native_handle(gate_handle)
                    except BaseException as error:
                        cleanup_error = cleanup_error or error
                if job_handle is not None:
                    try:
                        _close_windows_native_handle(job_handle)
                    except BaseException as error:
                        cleanup_error = cleanup_error or error
                if cleanup_error is not None:
                    raise cleanup_error

            if timed_out:
                raise ReleaseArtifactError(f"command timed out: {' '.join(command)}")
            assert process is not None
            stdout_file.seek(0)
            stderr_file.seek(0)
            return CommandOutcome(
                stdout=stdout_file.read().decode("utf-8", errors="replace"),
                stderr=stderr_file.read().decode("utf-8", errors="replace"),
                returncode=process.returncode,
            )

    def image_exists(self, image: str, *, cwd: Path) -> bool:
        try:
            result = subprocess.run(
                self._command(["docker", "image", "inspect", image, "--format", "{{json .Id}}"]),
                cwd=cwd,
                env=self._command_environment(),
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
                    self._command(["docker", "image", "save", image]),
                    cwd=cwd,
                    env=self._command_environment(),
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
class ArtifactIdentity:
    path: str
    sha256: str


@dataclass(frozen=True)
class QualificationToolchainDescriptor:
    path: str
    sha256: str
    format: str
    semantic_validator: str
    schema: ArtifactIdentity
    validator: ArtifactIdentity
    producer: ArtifactIdentity
    receipt_producer: ArtifactIdentity
    toolchain_manifest: ArtifactIdentity


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
    qualification_toolchain: QualificationToolchainDescriptor | None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if self.qualification_toolchain is None:
            del value["qualification_toolchain"]
        return value


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


WINDOWS_FIXED_SYSTEM_TOOL_VALIDATOR = r"""
$ErrorActionPreference = "Stop"
$current = Get-Item -Force -LiteralPath $env:RUISHENG_FIXED_SYSTEM_TOOL
$allowedSids = @(
    "S-1-5-18",
    "S-1-5-32-544",
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
) | Select-Object -Unique
$directUnsafeRights = [Security.AccessControl.FileSystemRights]::AppendData -bor
    [Security.AccessControl.FileSystemRights]::WriteData -bor
    [Security.AccessControl.FileSystemRights]::WriteAttributes -bor
    [Security.AccessControl.FileSystemRights]::WriteExtendedAttributes -bor
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
        throw "fixed system tool path contains a reparse point: $($current.FullName)"
    }
    $acl = Get-Acl -LiteralPath $current.FullName
    try {
        $ownerSid = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    } catch {
        throw "fixed system tool path has an unresolvable owner: $($current.FullName)"
    }
    if ($ownerSid -notin $allowedSids) {
        throw "fixed system tool path has an unapproved owner: $($current.FullName)"
    }
    $unsafeRights = if ($isDirect) { $directUnsafeRights } else { $ancestorUnsafeRights }
    foreach ($rule in $acl.Access) {
        if (($rule.PropagationFlags -band
                [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0 -or
            $rule.AccessControlType -ne "Allow" -or
            ($rule.FileSystemRights -band $unsafeRights) -eq 0) {
            continue
        }
        try {
            $sid = $rule.IdentityReference.Translate(
                [Security.Principal.SecurityIdentifier]
            ).Value
        } catch {
            throw "fixed system tool path has an unresolvable replacement identity: $($current.FullName)"
        }
        if ($sid -notin $allowedSids) {
            throw "fixed system tool path permits replacement by an unapproved identity: $($current.FullName)"
        }
    }
    $isDirect = $false
    $current = $current.Parent
}
"""


def _windows_system_directory() -> Path:
    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
    if length <= 0 or length >= len(buffer):
        raise ReleaseArtifactError("cannot resolve the Windows system directory")
    return Path(buffer.value)


def _validate_windows_fixed_system_tool_permissions(path: Path) -> None:
    system_directory = _windows_system_directory()
    powershell = system_directory / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if powershell.is_symlink() or not powershell.is_file():
        raise ReleaseArtifactError(f"fixed system PowerShell is unavailable: {powershell}")
    windows_root = system_directory.parent
    result = subprocess.run(
        [
            str(powershell),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            WINDOWS_FIXED_SYSTEM_TOOL_VALIDATOR,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        env={
            "COMSPEC": str(system_directory / "cmd.exe"),
            "PATHEXT": ".COM;.EXE;.BAT;.CMD",
            "RUISHENG_FIXED_SYSTEM_TOOL": str(path),
            "SYSTEMROOT": str(windows_root),
            "WINDIR": str(windows_root),
        },
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "no output").strip()
        raise ReleaseArtifactError(f"fixed system Git CLI failed owner/ACL validation: {details}")


def _system_ssh_keygen() -> Path:
    if os.name == "nt":
        path = _windows_system_directory() / "OpenSSH" / "ssh-keygen.exe"
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
    path = _windows_system_directory() / "WindowsPowerShell" / "v1.0" / "powershell.exe"
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


def _sha256_stable_file(path: Path, *, expected_links: int) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ReleaseArtifactError(
            f"cannot open authenticated system file {path}: {error}"
        ) from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != expected_links:
            raise ReleaseArtifactError(f"authenticated system file identity is invalid: {path}")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        after = os.fstat(descriptor)
        path_after = path.stat(follow_symlinks=False)
        if (
            _file_stat_identity(before) != _file_stat_identity(after)
            or _file_stat_identity(after) != _file_stat_identity(path_after)
            or not os.path.samestat(after, path_after)
        ):
            raise ReleaseArtifactError(f"authenticated system file changed while hashing: {path}")
        return digest.hexdigest()
    except OSError as error:
        raise ReleaseArtifactError(f"cannot authenticate system file {path}: {error}") from error
    finally:
        os.close(descriptor)


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


class _DuplicateJsonKeyError(ValueError):
    pass


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKeyError(f"duplicate JSON object key: {key!r}")
        value[key] = item
    return value


def _read_json_object_bytes(contents: bytes, *, label: str) -> dict[str, Any]:
    if len(contents) > MAX_RELEASE_JSON_BYTES:
        raise ReleaseArtifactError(
            f"invalid JSON file {label}: exceeds {MAX_RELEASE_JSON_BYTES}-byte limit"
        )
    try:
        value = json.loads(
            contents.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateJsonKeyError,
        RecursionError,
        MemoryError,
    ) as error:
        raise ReleaseArtifactError(f"invalid JSON file {label}: {error}") from error
    if not isinstance(value, dict):
        raise ReleaseArtifactError(f"JSON root must be an object: {label}")
    return value


def _canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _file_stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


@dataclass(frozen=True)
class _QualificationRuntime:
    root: Path
    python: Path
    dependency_root: Path
    strict: bool
    authenticated_uv_lock_sha256: str | None = None
    files: tuple[tuple[str, str], ...] = ()


def _validate_root_protected_posix_path(path: Path, *, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ReleaseArtifactError(f"{label} is unavailable: {path}: {error}") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise ReleaseArtifactError(f"{label} is linked: {path}")
    if metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise ReleaseArtifactError(f"{label} is not root protected: {path}")
    return metadata


def _hash_stable_runtime_file(path: Path, *, label: str) -> tuple[str, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ReleaseArtifactError(f"cannot open {label}: {path}: {error}") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > MAX_QUALIFICATION_RUNTIME_FILE_BYTES
        ):
            raise ReleaseArtifactError(f"{label} is not an allowed regular file: {path}")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        after = os.fstat(descriptor)
        try:
            path_after = path.stat(follow_symlinks=False)
        except OSError as error:
            raise ReleaseArtifactError(f"{label} changed while being read: {path}") from error
        if (
            _file_stat_identity(before) != _file_stat_identity(after)
            or _file_stat_identity(after) != _file_stat_identity(path_after)
            or not os.path.samestat(after, path_after)
        ):
            raise ReleaseArtifactError(f"{label} changed while being read: {path}")
        return digest.hexdigest(), after
    finally:
        os.close(descriptor)


def _qualification_runtime_expected_directories(files: Sequence[str]) -> set[str]:
    directories: set[str] = set()
    for relative in files:
        parent = PurePosixPath(relative).parent
        while parent != PurePosixPath("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _qualification_runtime_layout(
    root: Path,
    *,
    expected_files: set[str],
    expected_directories: set[str],
) -> tuple[set[str], set[str]]:
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = os.scandir(current)
        except OSError as error:
            raise ReleaseArtifactError(
                f"cannot enumerate qualification runtime: {current}: {error}"
            ) from error
        with entries:
            for entry in entries:
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                metadata = _validate_root_protected_posix_path(
                    path,
                    label=f"qualification runtime member {relative}",
                )
                if stat.S_ISDIR(metadata.st_mode):
                    if relative not in expected_directories:
                        raise ReleaseArtifactError("qualification runtime file allowlist mismatch")
                    actual_directories.add(relative)
                    pending.append(path)
                elif stat.S_ISREG(metadata.st_mode):
                    if relative not in expected_files:
                        raise ReleaseArtifactError("qualification runtime file allowlist mismatch")
                    actual_files.add(relative)
                else:
                    raise ReleaseArtifactError(
                        f"qualification runtime member is not a file or directory: {relative}"
                    )
    return actual_files, actual_directories


def _validate_posix_qualification_runtime(  # noqa: PLR0912, PLR0915
    root: Path,
    *,
    authenticated_uv_lock_sha256: str,
) -> _QualificationRuntime:
    if os.name == "nt":
        raise ReleaseArtifactError("POSIX qualification runtime validation is unavailable")
    if SHA256_PATTERN.fullmatch(authenticated_uv_lock_sha256) is None:
        raise ReleaseArtifactError("authenticated qualification uv.lock SHA-256 is invalid")
    root = root.absolute()
    for current in (root, *root.parents):
        metadata = _validate_root_protected_posix_path(
            current,
            label="qualification runtime path",
        )
        if not stat.S_ISDIR(metadata.st_mode):
            raise ReleaseArtifactError(f"qualification runtime path is not a directory: {current}")

    manifest_path = root / QUALIFICATION_RUNTIME_MANIFEST
    manifest_digest, manifest_metadata = _hash_stable_runtime_file(
        manifest_path,
        label="qualification runtime manifest",
    )
    if manifest_metadata.st_size > MAX_QUALIFICATION_RUNTIME_MANIFEST_BYTES:
        raise ReleaseArtifactError("qualification runtime manifest is too large")
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as error:
        raise ReleaseArtifactError(
            f"cannot read qualification runtime manifest: {error}"
        ) from error
    if hashlib.sha256(manifest_bytes).hexdigest() != manifest_digest:
        raise ReleaseArtifactError("qualification runtime manifest changed while being read")
    manifest = _read_json_object_bytes(manifest_bytes, label=str(manifest_path))
    if set(manifest) != {
        "artifact_type",
        "schema_version",
        "python_version",
        "uv_lock_sha256",
        "dependency_root",
        "files",
    }:
        raise ReleaseArtifactError("qualification runtime manifest keys mismatch")
    if (
        manifest["artifact_type"] != QUALIFICATION_RUNTIME_ARTIFACT_TYPE
        or type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != QUALIFICATION_RUNTIME_SCHEMA_VERSION
        or manifest["python_version"] != QUALIFICATION_RUNTIME_PYTHON_VERSION
        or manifest["uv_lock_sha256"] != authenticated_uv_lock_sha256
        or manifest["dependency_root"] != POSIX_QUALIFICATION_RUNTIME_DEPENDENCIES
    ):
        raise ReleaseArtifactError("qualification runtime manifest contract is invalid")
    file_values = manifest["files"]
    if (
        not isinstance(file_values, list)
        or not file_values
        or len(file_values) >= MAX_QUALIFICATION_RUNTIME_FILES
    ):
        raise ReleaseArtifactError("qualification runtime manifest files are invalid")

    expected_files = {QUALIFICATION_RUNTIME_MANIFEST}
    identities: list[tuple[str, str]] = []
    previous_path: str | None = None
    for identity in file_values:
        if (
            not isinstance(identity, dict)
            or set(identity) != {"path", "sha256"}
            or not isinstance(identity.get("path"), str)
            or not isinstance(identity.get("sha256"), str)
        ):
            raise ReleaseArtifactError("qualification runtime file identity is invalid")
        relative = _validate_relative_path(identity["path"])
        expected_digest = identity["sha256"]
        if SHA256_PATTERN.fullmatch(expected_digest) is None:
            raise ReleaseArtifactError("qualification runtime file identity is invalid")
        if previous_path is not None and previous_path >= relative:
            raise ReleaseArtifactError(
                "qualification runtime files are not in strict ordinal path order"
            )
        folded = relative.casefold()
        if (
            relative == QUALIFICATION_RUNTIME_MANIFEST
            or folded.endswith(".pth")
            or PurePosixPath(folded).name in {"pyvenv.cfg", "sitecustomize.py", "usercustomize.py"}
        ):
            raise ReleaseArtifactError(
                f"qualification runtime contains a forbidden file: {relative}"
            )
        if relative in expected_files:
            raise ReleaseArtifactError("qualification runtime contains a duplicate file path")
        expected_files.add(relative)
        identities.append((relative, expected_digest))
        previous_path = relative

    required = {
        POSIX_QUALIFICATION_RUNTIME_PYTHON,
        "lib/python3.11/encodings/__init__.py",
    }
    if not required.issubset(expected_files) or not any(
        relative.startswith(POSIX_QUALIFICATION_RUNTIME_DEPENDENCIES + "/")
        for relative, _digest in identities
    ):
        raise ReleaseArtifactError(
            "qualification runtime is not a self-contained Python 3.11 dependency closure"
        )
    expected_directories = _qualification_runtime_expected_directories(tuple(expected_files))
    actual_files, actual_directories = _qualification_runtime_layout(
        root,
        expected_files=expected_files,
        expected_directories=expected_directories,
    )
    if actual_files != expected_files or actual_directories != expected_directories:
        raise ReleaseArtifactError("qualification runtime file allowlist mismatch")

    observed: list[tuple[str, str]] = []
    for relative, expected_digest in identities:
        path = root / relative
        actual_digest, metadata = _hash_stable_runtime_file(
            path,
            label=f"qualification runtime file {relative}",
        )
        if metadata.st_uid != 0 or metadata.st_mode & 0o022:
            raise ReleaseArtifactError(
                f"qualification runtime file is not root protected: {relative}"
            )
        if actual_digest != expected_digest:
            raise ReleaseArtifactError(f"qualification runtime file SHA-256 mismatch: {relative}")
        observed.append((relative, actual_digest))

    python = root / POSIX_QUALIFICATION_RUNTIME_PYTHON
    if not os.access(python, os.X_OK):
        raise ReleaseArtifactError("qualification runtime Python is not executable")
    dependency_root = root / POSIX_QUALIFICATION_RUNTIME_DEPENDENCIES
    if not dependency_root.is_dir():
        raise ReleaseArtifactError("qualification runtime dependency_root is missing")
    return _QualificationRuntime(
        root=root,
        python=python,
        dependency_root=dependency_root,
        strict=True,
        authenticated_uv_lock_sha256=authenticated_uv_lock_sha256,
        files=((QUALIFICATION_RUNTIME_MANIFEST, manifest_digest), *observed),
    )


def _development_qualification_runtime() -> _QualificationRuntime:
    python = Path(sys.executable).resolve(strict=True)
    dependency_root = Path(sysconfig.get_path("purelib")).resolve(strict=True)
    if not dependency_root.is_dir():
        raise ReleaseArtifactError("development qualification dependency_root is unavailable")
    return _QualificationRuntime(
        root=Path(sys.prefix).resolve(strict=True),
        python=python,
        dependency_root=dependency_root,
        strict=False,
    )


def _qualification_environment(temporary_root: Path) -> dict[str, str]:
    environment = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }
    if os.name == "nt":
        system_directory = _windows_system_directory()
        windows_root = system_directory.parent
        environment.update(
            {
                "COMSPEC": str(system_directory / "cmd.exe"),
                "PATHEXT": ".COM;.EXE;.BAT;.CMD",
                "SYSTEMROOT": str(windows_root),
                "TEMP": str(temporary_root),
                "TMP": str(temporary_root),
                "WINDIR": str(windows_root),
            }
        )
    else:
        environment["TMPDIR"] = str(temporary_root)
    return environment


def _read_toolchain_source(root: Path, relative: str) -> bytes:
    source = root / relative
    if source.is_symlink() or not source.is_file():
        raise ReleaseArtifactError(
            f"qualification toolchain source is missing or linked: {relative}"
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as error:
        raise ReleaseArtifactError(
            f"cannot open qualification toolchain source {relative}: {error}"
        ) from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_QUALIFICATION_MEMBER_BYTES:
            raise ReleaseArtifactError(
                f"qualification toolchain source is not an allowed regular file: {relative}"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as source_stream:
            contents = source_stream.read(MAX_QUALIFICATION_MEMBER_BYTES + 1)
            source_stream.seek(0)
            repeated = source_stream.read(MAX_QUALIFICATION_MEMBER_BYTES + 1)
        after = os.fstat(descriptor)
        try:
            path_after = source.stat(follow_symlinks=False)
        except OSError as error:
            raise ReleaseArtifactError(
                f"qualification toolchain source changed while being read: {relative}"
            ) from error
        if (
            contents != repeated
            or len(contents) != before.st_size
            or _file_stat_identity(before) != _file_stat_identity(after)
            or _file_stat_identity(after) != _file_stat_identity(path_after)
            or not os.path.samestat(after, path_after)
        ):
            raise ReleaseArtifactError(
                f"qualification toolchain source changed while being read: {relative}"
            )
        return contents
    finally:
        os.close(descriptor)


def _read_committed_toolchain_source(
    root: Path,
    relative: str,
    *,
    source_commit: str,
    runner: Runner,
) -> bytes:
    if SOURCE_COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise ReleaseArtifactError("qualification toolchain source commit is invalid")
    git_relative = relative
    contents = _read_toolchain_source(root, relative)
    expected_object = runner.run(
        [
            "git",
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{source_commit}:{git_relative}",
        ],
        cwd=root,
    )
    if GIT_OBJECT_ID_PATTERN.fullmatch(expected_object) is None:
        raise ReleaseArtifactError(
            f"qualification toolchain Git object identity is invalid: {git_relative}"
        )
    object_payload = b"blob " + str(len(contents)).encode("ascii") + b"\0" + contents
    if len(expected_object) == GIT_SHA1_OBJECT_ID_LENGTH:
        actual_object = hashlib.sha1(  # noqa: S324 - Git SHA-1 object identity.
            object_payload,
            usedforsecurity=False,
        ).hexdigest()
    else:
        actual_object = hashlib.sha256(object_payload).hexdigest()
    if actual_object != expected_object:
        raise ReleaseArtifactError(
            f"qualification toolchain source does not match {source_commit}:{git_relative}"
        )
    return contents


def _deterministic_qualification_tar_info(name: str, size: int) -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    member.size = size
    member.mtime = 0
    member.uid = 0
    member.gid = 0
    member.uname = ""
    member.gname = ""
    member.mode = 0o644
    return member


def _add_deterministic_tar_member(archive: tarfile.TarFile, name: str, contents: bytes) -> None:
    member = _deterministic_qualification_tar_info(name, len(contents))
    archive.addfile(member, io.BytesIO(contents))


def _write_qualification_toolchain(
    root: Path,
    package: Path,
    *,
    source_commit: str,
    runner: Runner,
) -> QualificationToolchainDescriptor:
    producer_contents = _read_committed_toolchain_source(
        root,
        "tools/release_artifacts.py",
        source_commit=source_commit,
        runner=runner,
    )
    member_contents = {
        relative: (
            producer_contents
            if relative == "tools/release_artifacts.py"
            else _read_committed_toolchain_source(
                root,
                relative,
                source_commit=source_commit,
                runner=runner,
            )
        )
        for relative in QUALIFICATION_TOOLCHAIN_MEMBERS
    }
    member_identities = [
        {"path": relative, "sha256": hashlib.sha256(member_contents[relative]).hexdigest()}
        for relative in QUALIFICATION_TOOLCHAIN_MEMBERS
    ]
    toolchain_manifest_bytes = _canonical_json_bytes(
        {
            "artifact_type": QUALIFICATION_TOOLCHAIN_ARTIFACT_TYPE,
            "members": member_identities,
            "schema_version": QUALIFICATION_TOOLCHAIN_SCHEMA_VERSION,
            "semantic_validator": SEMANTIC_VALIDATOR_ID,
        }
    )
    archive_path = package / QUALIFICATION_TOOLCHAIN_ARCHIVE
    try:
        with (
            archive_path.open("xb") as raw_archive,
            gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=9,
                fileobj=raw_archive,
                mtime=0,
            ) as compressed,
            tarfile.open(
                fileobj=cast(BinaryIO, compressed), mode="w", format=tarfile.USTAR_FORMAT
            ) as archive,
        ):
            for relative in QUALIFICATION_TOOLCHAIN_MEMBERS:
                _add_deterministic_tar_member(archive, relative, member_contents[relative])
            _add_deterministic_tar_member(
                archive, QUALIFICATION_TOOLCHAIN_MANIFEST, toolchain_manifest_bytes
            )
    except (OSError, tarfile.TarError) as error:
        archive_path.unlink(missing_ok=True)
        raise ReleaseArtifactError(f"cannot create qualification toolchain: {error}") from error

    identities = {
        value["path"]: ArtifactIdentity(path=value["path"], sha256=value["sha256"])
        for value in member_identities
    }
    return QualificationToolchainDescriptor(
        path=QUALIFICATION_TOOLCHAIN_ARCHIVE,
        sha256=sha256_file(archive_path),
        format=QUALIFICATION_TOOLCHAIN_FORMAT,
        semantic_validator=SEMANTIC_VALIDATOR_ID,
        schema=identities["schemas/point-profile/point-profile-v1.schema.json"],
        validator=identities["tools/validate_device_point_profile.py"],
        producer=identities["tools/release_artifacts.py"],
        receipt_producer=identities["tools/release_verification_receipt.py"],
        toolchain_manifest=ArtifactIdentity(
            path=QUALIFICATION_TOOLCHAIN_MANIFEST,
            sha256=hashlib.sha256(toolchain_manifest_bytes).hexdigest(),
        ),
    )


def _validate_toolchain_identity(identity: ArtifactIdentity, *, expected_path: str) -> None:
    if identity.path != expected_path or SHA256_PATTERN.fullmatch(identity.sha256) is None:
        raise ReleaseArtifactError(
            f"qualification toolchain identity is invalid for {expected_path}"
        )


def _exact_qualification_tar_members(
    archive: tarfile.TarFile,
    expected: tuple[str, ...],
) -> tuple[tarfile.TarInfo, ...]:
    members: list[tarfile.TarInfo] = []
    for index, member in enumerate(archive):
        if index >= len(expected) or member.name != expected[index]:
            raise ReleaseArtifactError("qualification toolchain archive member allowlist mismatch")
        members.append(member)
    if len(members) != len(expected):
        raise ReleaseArtifactError("qualification toolchain archive member allowlist mismatch")
    return tuple(members)


def _qualification_ustar_octal(field: bytes, *, label: str) -> int:
    if not field or field[0] & 0x80:
        raise ReleaseArtifactError(f"qualification toolchain USTAR {label} is invalid")
    digits = field.rstrip(b"\0 ").lstrip(b" ")
    if not digits or any(value < ord("0") or value > ord("7") for value in digits):
        raise ReleaseArtifactError(f"qualification toolchain USTAR {label} is invalid")
    return int(digits, 8)


def _discard_qualification_ustar_payload(
    stream: gzip.GzipFile,
    size: int,
    *,
    require_zero: bool = False,
) -> None:
    remaining = size
    while remaining:
        chunk = stream.read(min(64 * 1024, remaining))
        if not chunk:
            raise ReleaseArtifactError("qualification toolchain USTAR payload is truncated")
        if len(chunk) > remaining:
            raise ReleaseArtifactError("qualification toolchain USTAR payload framing is invalid")
        if require_zero and any(chunk):
            raise ReleaseArtifactError(
                "qualification toolchain archive contains non-zero USTAR padding"
            )
        remaining -= len(chunk)


def _qualification_ustar_member_size(header: bytes, *, expected_name: str) -> int:
    expected_checksum = _qualification_ustar_octal(header[148:156], label="checksum")
    checksum_header = header[:148] + (b" " * 8) + header[156:]
    if sum(checksum_header) != expected_checksum:
        raise ReleaseArtifactError("qualification toolchain USTAR header checksum is invalid")
    if header[257:263] != b"ustar\0" or header[263:265] != b"00":
        raise ReleaseArtifactError("qualification toolchain archive is not strict USTAR")
    if header[156:157] != tarfile.REGTYPE:
        raise ReleaseArtifactError(
            "qualification toolchain archive contains a non-regular USTAR member"
        )

    encoded_name = expected_name.encode("ascii")
    if (
        len(encoded_name) > QUALIFICATION_USTAR_NAME_BYTES
        or header[:QUALIFICATION_USTAR_NAME_BYTES]
        != encoded_name.ljust(QUALIFICATION_USTAR_NAME_BYTES, b"\0")
        or header[345:500] != b"\0" * 155
    ):
        raise ReleaseArtifactError("qualification toolchain archive member allowlist mismatch")

    member_size = _qualification_ustar_octal(header[124:136], label="size")
    member_limit = (
        MAX_RELEASE_JSON_BYTES
        if expected_name == QUALIFICATION_TOOLCHAIN_MANIFEST
        else MAX_QUALIFICATION_MEMBER_BYTES
    )
    if member_size > member_limit:
        raise ReleaseArtifactError(
            f"qualification toolchain member is not an allowed regular file: {expected_name}"
        )
    expected_header = _deterministic_qualification_tar_info(expected_name, member_size).tobuf(
        format=tarfile.USTAR_FORMAT
    )
    if header != expected_header:
        raise ReleaseArtifactError(
            f"qualification toolchain USTAR header is not deterministic: {expected_name}"
        )
    return member_size


def _validate_single_qualification_gzip_member(raw_archive: BinaryIO) -> None:
    initial_position = raw_archive.tell()
    decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    expanded_bytes = 0
    try:
        while not decompressor.eof:
            compressed = raw_archive.read(64 * 1024)
            if not compressed:
                raise ReleaseArtifactError("qualification toolchain gzip member is truncated")
            pending = compressed
            while pending and not decompressor.eof:
                maximum_output = min(
                    64 * 1024,
                    MAX_QUALIFICATION_TAR_BYTES - expanded_bytes + 1,
                )
                expanded = decompressor.decompress(pending, maximum_output)
                expanded_bytes += len(expanded)
                if expanded_bytes > MAX_QUALIFICATION_TAR_BYTES:
                    raise ReleaseArtifactError(
                        "qualification toolchain expanded archive exceeds its byte budget"
                    )
                next_pending = decompressor.unconsumed_tail
                if next_pending == pending and not expanded:
                    raise ReleaseArtifactError(
                        "qualification toolchain gzip member made no progress"
                    )
                pending = next_pending
        if decompressor.unused_data or raw_archive.read(1):
            raise ReleaseArtifactError(
                "qualification toolchain archive must contain exactly one gzip member"
            )
    finally:
        raw_archive.seek(initial_position)


def _preflight_qualification_ustar_archive(
    raw_archive: BinaryIO,
    expected: tuple[str, ...],
) -> None:
    zero_block = b"\0" * tarfile.BLOCKSIZE
    try:
        initial_position = raw_archive.tell()
        raw_archive.seek(0, os.SEEK_END)
        archive_size = raw_archive.tell()
        raw_archive.seek(initial_position)
        if initial_position != 0 or archive_size > MAX_QUALIFICATION_GZIP_BYTES:
            raise ReleaseArtifactError(
                "qualification toolchain gzip archive exceeds its byte budget"
            )
        gzip_header = raw_archive.read(10)
        raw_archive.seek(initial_position)
        if gzip_header != b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff":
            raise ReleaseArtifactError("qualification toolchain gzip header is not canonical")
        _validate_single_qualification_gzip_member(raw_archive)

        with gzip.GzipFile(fileobj=raw_archive, mode="rb") as stream:
            member_blocks = 0
            for expected_name in expected:
                header = stream.read(tarfile.BLOCKSIZE)
                if len(header) != tarfile.BLOCKSIZE or header == zero_block:
                    raise ReleaseArtifactError(
                        "qualification toolchain archive member allowlist mismatch"
                    )
                member_size = _qualification_ustar_member_size(header, expected_name=expected_name)
                padded_size = (
                    (member_size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE * tarfile.BLOCKSIZE
                )
                _discard_qualification_ustar_payload(stream, member_size)
                _discard_qualification_ustar_payload(
                    stream,
                    padded_size - member_size,
                    require_zero=True,
                )
                member_blocks += 1 + padded_size // tarfile.BLOCKSIZE

            record_blocks = tarfile.RECORDSIZE // tarfile.BLOCKSIZE
            trailing_zero_blocks = MIN_QUALIFICATION_TAR_TRAILING_ZERO_BLOCKS + (
                -(member_blocks + MIN_QUALIFICATION_TAR_TRAILING_ZERO_BLOCKS) % record_blocks
            )
            if trailing_zero_blocks > MAX_QUALIFICATION_TAR_TRAILING_ZERO_BLOCKS:
                raise ReleaseArtifactError(
                    "qualification toolchain USTAR trailer exceeds its zero-block budget"
                )
            for _index in range(trailing_zero_blocks):
                block = stream.read(tarfile.BLOCKSIZE)
                if len(block) != tarfile.BLOCKSIZE or block != zero_block:
                    raise ReleaseArtifactError("qualification toolchain USTAR trailer is invalid")
            if stream.read(1):
                raise ReleaseArtifactError(
                    "qualification toolchain archive member allowlist mismatch"
                )
    except ReleaseArtifactError:
        raise
    except (EOFError, MemoryError, OSError, zlib.error) as error:
        raise ReleaseArtifactError(
            f"invalid qualification toolchain gzip/USTAR archive: {error}"
        ) from error


def _verify_qualification_toolchain(  # noqa: PLR0912, PLR0915
    package: Path,
    descriptor: QualificationToolchainDescriptor,
    sums: Mapping[str, str],
) -> None:
    if descriptor.path != QUALIFICATION_TOOLCHAIN_ARCHIVE:
        raise ReleaseArtifactError("qualification toolchain path is invalid")
    if descriptor.format != QUALIFICATION_TOOLCHAIN_FORMAT:
        raise ReleaseArtifactError("qualification toolchain format is invalid")
    if descriptor.semantic_validator != SEMANTIC_VALIDATOR_ID:
        raise ReleaseArtifactError("qualification toolchain semantic validator is invalid")
    if SHA256_PATTERN.fullmatch(descriptor.sha256) is None:
        raise ReleaseArtifactError("qualification toolchain SHA-256 is invalid")
    if sums.get(descriptor.path) != descriptor.sha256:
        raise ReleaseArtifactError("qualification toolchain descriptor/SHA256SUMS mismatch")
    expected_identity_paths = {
        "schema": "schemas/point-profile/point-profile-v1.schema.json",
        "validator": "tools/validate_device_point_profile.py",
        "producer": "tools/release_artifacts.py",
        "receipt_producer": "tools/release_verification_receipt.py",
        "toolchain_manifest": QUALIFICATION_TOOLCHAIN_MANIFEST,
    }
    for name, expected_path in expected_identity_paths.items():
        _validate_toolchain_identity(getattr(descriptor, name), expected_path=expected_path)

    archive_path = package / descriptor.path
    expected_members = (*QUALIFICATION_TOOLCHAIN_MEMBERS, QUALIFICATION_TOOLCHAIN_MANIFEST)
    try:
        with archive_path.open("rb") as raw_archive:
            _preflight_qualification_ustar_archive(raw_archive, expected_members)
            raw_archive.seek(0)
            with tarfile.open(fileobj=raw_archive, mode="r:gz") as archive:
                members = _exact_qualification_tar_members(archive, expected_members)
                contents: dict[str, bytes] = {}
                for member in members:
                    _validate_relative_path(member.name)
                    member_limit = (
                        MAX_RELEASE_JSON_BYTES
                        if member.name == QUALIFICATION_TOOLCHAIN_MANIFEST
                        else MAX_QUALIFICATION_MEMBER_BYTES
                    )
                    if not member.isfile() or member.size > member_limit:
                        raise ReleaseArtifactError(
                            f"qualification toolchain member is not an allowed regular file: "
                            f"{member.name}"
                        )
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ReleaseArtifactError(
                            f"qualification toolchain member cannot be read: {member.name}"
                        )
                    value = stream.read(member_limit + 1)
                    if len(value) != member.size:
                        raise ReleaseArtifactError(
                            f"qualification toolchain member size mismatch: {member.name}"
                        )
                    contents[member.name] = value
    except (OSError, tarfile.TarError) as error:
        raise ReleaseArtifactError(f"invalid qualification toolchain archive: {error}") from error

    manifest_bytes = contents[QUALIFICATION_TOOLCHAIN_MANIFEST]
    if hashlib.sha256(manifest_bytes).hexdigest() != descriptor.toolchain_manifest.sha256:
        raise ReleaseArtifactError("qualification toolchain manifest SHA-256 mismatch")
    toolchain_manifest = _read_json_object_bytes(
        manifest_bytes, label=f"{archive_path}:{QUALIFICATION_TOOLCHAIN_MANIFEST}"
    )
    if set(toolchain_manifest) != {
        "artifact_type",
        "members",
        "schema_version",
        "semantic_validator",
    }:
        raise ReleaseArtifactError("qualification toolchain manifest keys mismatch")
    if (
        toolchain_manifest["artifact_type"] != QUALIFICATION_TOOLCHAIN_ARTIFACT_TYPE
        or toolchain_manifest["schema_version"] != QUALIFICATION_TOOLCHAIN_SCHEMA_VERSION
        or type(toolchain_manifest["schema_version"]) is not int
        or toolchain_manifest["semantic_validator"] != SEMANTIC_VALIDATOR_ID
    ):
        raise ReleaseArtifactError("qualification toolchain manifest contract is invalid")
    member_values = toolchain_manifest["members"]
    if not isinstance(member_values, list) or len(member_values) != len(
        QUALIFICATION_TOOLCHAIN_MEMBERS
    ):
        raise ReleaseArtifactError("qualification toolchain manifest members are invalid")
    identities: dict[str, str] = {}
    for index, expected_path in enumerate(QUALIFICATION_TOOLCHAIN_MEMBERS):
        identity = member_values[index]
        if (
            not isinstance(identity, dict)
            or set(identity) != {"path", "sha256"}
            or identity.get("path") != expected_path
            or not isinstance(identity.get("sha256"), str)
            or SHA256_PATTERN.fullmatch(identity["sha256"]) is None
        ):
            raise ReleaseArtifactError(
                f"qualification toolchain manifest identity is invalid: {expected_path}"
            )
        actual_digest = hashlib.sha256(contents[expected_path]).hexdigest()
        if identity["sha256"] != actual_digest:
            raise ReleaseArtifactError(
                f"qualification toolchain member SHA-256 mismatch: {expected_path}"
            )
        identities[expected_path] = actual_digest
    for name, expected_path in expected_identity_paths.items():
        if name == "toolchain_manifest":
            continue
        if getattr(descriptor, name).sha256 != identities[expected_path]:
            raise ReleaseArtifactError(
                f"qualification toolchain descriptor identity mismatch: {expected_path}"
            )


def _extract_qualification_toolchain(
    package: Path,
    manifest: CandidateManifest,
    *,
    parent: Path,
) -> Path:
    descriptor = manifest.qualification_toolchain
    if descriptor is None:
        raise ReleaseArtifactError("candidate has no authenticated qualification toolchain")
    extraction = Path(tempfile.mkdtemp(prefix="ruisheng-qualification-", dir=parent))
    os.chmod(extraction, 0o700)
    expected = (*QUALIFICATION_TOOLCHAIN_MEMBERS, QUALIFICATION_TOOLCHAIN_MANIFEST)
    try:
        with (package / descriptor.path).open("rb") as raw_archive:
            _preflight_qualification_ustar_archive(raw_archive, expected)
            raw_archive.seek(0)
            with tarfile.open(fileobj=raw_archive, mode="r:gz") as archive:
                members = _exact_qualification_tar_members(archive, expected)
                for member in members:
                    member_limit = (
                        MAX_RELEASE_JSON_BYTES
                        if member.name == QUALIFICATION_TOOLCHAIN_MANIFEST
                        else MAX_QUALIFICATION_MEMBER_BYTES
                    )
                    if not member.isfile() or member.size > member_limit:
                        raise ReleaseArtifactError(
                            f"qualification toolchain member is not an allowed regular file: "
                            f"{member.name}"
                        )
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ReleaseArtifactError(
                            f"qualification toolchain member cannot be read: {member.name}"
                        )
                    destination = extraction / member.name
                    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with destination.open("xb") as output:
                        contents = stream.read(member_limit + 1)
                        if len(contents) != member.size:
                            raise ReleaseArtifactError(
                                f"qualification toolchain member size mismatch: {member.name}"
                            )
                        output.write(contents)
                    os.chmod(destination, 0o600)
        identities = {
            identity.path: identity.sha256
            for identity in (
                descriptor.schema,
                descriptor.validator,
                descriptor.producer,
                descriptor.receipt_producer,
                descriptor.toolchain_manifest,
            )
        }
        toolchain_manifest = _read_json_object(extraction / QUALIFICATION_TOOLCHAIN_MANIFEST)
        for identity in toolchain_manifest["members"]:
            identities[identity["path"]] = identity["sha256"]
        for relative in expected:
            if sha256_file(extraction / relative) != identities[relative]:
                raise ReleaseArtifactError(
                    f"extracted qualification toolchain member SHA-256 mismatch: {relative}"
                )
        return extraction
    except BaseException:
        shutil.rmtree(extraction, ignore_errors=True)
        raise


OCI_IMAGE_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
OCI_IMAGE_CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
IN_TOTO_MEDIA_TYPE = "application/vnd.in-toto+json"
CONTAINERD_SUBJECT_ANNOTATION = "io.containerd.manifest.subject"
IN_TOTO_PREDICATE_ANNOTATION = "in-toto.io/predicate-type"
IN_TOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v0.1"
SLSA_PROVENANCE_V1 = "https://slsa.dev/provenance/v1"
OCI_SCHEMA_VERSION = 2


def _bounded_docker_archive_members(archive: tarfile.TarFile, path: Path) -> list[tarfile.TarInfo]:
    members: list[tarfile.TarInfo] = []
    total_bytes = 0
    for member in archive:
        if len(members) >= MAX_DOCKER_ARCHIVE_MEMBERS:
            raise ReleaseArtifactError(f"archive has too many members: {path}")
        if member.size < 0 or member.size > MAX_DOCKER_ARCHIVE_MEMBER_BYTES:
            raise ReleaseArtifactError(
                f"archive member exceeds the byte budget: {path}:{member.name}"
            )
        total_bytes += member.size
        if total_bytes > MAX_DOCKER_ARCHIVE_TOTAL_BYTES:
            raise ReleaseArtifactError(f"archive exceeds the total byte budget: {path}")
        members.append(member)
    return members


_FORBIDDEN_TAR_EXTENSION_TYPES = frozenset(
    {
        tarfile.XHDTYPE,
        tarfile.XGLTYPE,
        tarfile.GNUTYPE_LONGNAME,
        tarfile.GNUTYPE_LONGLINK,
        tarfile.GNUTYPE_SPARSE,
        *((tarfile.SOLARIS_XHDTYPE,) if hasattr(tarfile, "SOLARIS_XHDTYPE") else ()),
    }
)


def _discard_tar_bytes(stream: BinaryIO, size: int, *, label: str) -> None:
    remaining = size
    while remaining:
        chunk = stream.read(min(1024 * 1024, remaining))
        if not chunk:
            raise ReleaseArtifactError(f"{label} is truncated")
        remaining -= len(chunk)


def _preflight_docker_tar_stream(
    stream: BinaryIO,
    *,
    label: str,
    maximum_members: int,
    maximum_member_bytes: int,
    maximum_total_bytes: int,
) -> None:
    """Bound raw tar headers before tarfile can allocate PAX/GNU extension payloads."""

    members = 0
    total_bytes = 0
    zero_blocks = 0
    while True:
        header = stream.read(tarfile.BLOCKSIZE)
        if not header:
            if zero_blocks >= MIN_QUALIFICATION_TAR_TRAILING_ZERO_BLOCKS:
                return
            raise ReleaseArtifactError(f"{label} has no complete tar trailer")
        if len(header) != tarfile.BLOCKSIZE:
            raise ReleaseArtifactError(f"{label} has a truncated tar header")
        if header == b"\0" * tarfile.BLOCKSIZE:
            zero_blocks += 1
            if zero_blocks >= MIN_QUALIFICATION_TAR_TRAILING_ZERO_BLOCKS:
                return
            continue
        if zero_blocks:
            raise ReleaseArtifactError(f"{label} has data after an incomplete tar trailer")
        try:
            member = tarfile.TarInfo.frombuf(
                header,
                encoding="utf-8",
                errors="surrogateescape",
            )
        except (tarfile.TarError, ValueError) as error:
            raise ReleaseArtifactError(f"{label} has an invalid tar header") from error
        if member.type in _FORBIDDEN_TAR_EXTENSION_TYPES:
            raise ReleaseArtifactError(f"{label} contains forbidden tar extension metadata")
        members += 1
        if members > maximum_members:
            raise ReleaseArtifactError(f"{label} has too many members")
        if member.size < 0 or member.size > maximum_member_bytes:
            raise ReleaseArtifactError(f"{label} member exceeds the byte budget")
        total_bytes += member.size
        if total_bytes > maximum_total_bytes:
            raise ReleaseArtifactError(f"{label} exceeds the total byte budget")
        padded_size = member.size + (-member.size % tarfile.BLOCKSIZE)
        _discard_tar_bytes(stream, padded_size, label=label)


def _preflight_docker_archive(path: Path) -> None:
    with gzip.open(path, mode="rb") as stream:
        _preflight_docker_tar_stream(
            cast(BinaryIO, stream),
            label=f"Docker image archive {path}",
            maximum_members=MAX_DOCKER_ARCHIVE_MEMBERS,
            maximum_member_bytes=MAX_DOCKER_ARCHIVE_MEMBER_BYTES,
            maximum_total_bytes=MAX_DOCKER_ARCHIVE_TOTAL_BYTES,
        )


def _read_archive_member_bytes(
    archive: tarfile.TarFile, member: tarfile.TarInfo, path: Path, *, label: str
) -> bytes:
    if not member.isfile() or member.size > MAX_RELEASE_JSON_BYTES:
        raise ReleaseArtifactError(f"archive {label} exceeds the JSON byte limit: {path}")
    stream = archive.extractfile(member)
    if stream is None:
        raise ReleaseArtifactError(f"archive {label} is not a regular file: {path}")
    contents = stream.read(MAX_RELEASE_JSON_BYTES + 1)
    if len(contents) != member.size:
        raise ReleaseArtifactError(f"archive {label} size is inconsistent: {path}")
    return contents


@dataclass
class _ArchiveInspection:
    archive: tarfile.TarFile
    path: Path
    members_by_name: Mapping[str, tarfile.TarInfo]
    references_seen: int = 0
    metadata_bytes_seen: int = 0
    blob_cache: dict[str, bytes | None] | None = None

    def __post_init__(self) -> None:
        self.blob_cache = {}

    def consume_reference(self) -> None:
        self.references_seen += 1
        if self.references_seen > MAX_DOCKER_DESCRIPTOR_REFERENCES:
            raise ReleaseArtifactError(f"archive descriptor reference budget exceeded: {self.path}")

    def consume_metadata_bytes(self, size: int) -> None:
        if size > MAX_DOCKER_METADATA_BYTES - self.metadata_bytes_seen:
            raise ReleaseArtifactError(f"archive metadata byte budget exceeded: {self.path}")
        self.metadata_bytes_seen += size


def _read_archive_sha256_blob(
    inspection: _ArchiveInspection,
    digest: object,
    *,
    label: str,
    allow_missing: bool = False,
) -> bytes | None:
    inspection.consume_reference()
    if not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        raise ReleaseArtifactError(f"archive {label} digest is invalid: {inspection.path}")
    blob_name = f"blobs/sha256/{digest.removeprefix('sha256:')}"
    assert inspection.blob_cache is not None
    if blob_name in inspection.blob_cache:
        cached = inspection.blob_cache[blob_name]
        if cached is None and not allow_missing:
            raise ReleaseArtifactError(
                f"archive {label} blob is missing: {inspection.path}:{blob_name}"
            )
        return cached
    member = inspection.members_by_name.get(blob_name)
    if member is None:
        inspection.blob_cache[blob_name] = None
        if allow_missing:
            return None
        raise ReleaseArtifactError(
            f"archive {label} blob is missing: {inspection.path}:{blob_name}"
        )
    if not member.isfile() or member.size > MAX_RELEASE_JSON_BYTES:
        raise ReleaseArtifactError(
            f"archive {label} exceeds the JSON byte limit: {inspection.path}"
        )
    inspection.consume_metadata_bytes(member.size)
    stream = inspection.archive.extractfile(member)
    if stream is None:
        raise ReleaseArtifactError(
            f"archive {label} blob is not a regular file: {inspection.path}:{blob_name}"
        )
    contents = stream.read(MAX_RELEASE_JSON_BYTES + 1)
    if len(contents) != member.size:
        raise ReleaseArtifactError(f"archive {label} size is inconsistent: {inspection.path}")
    if f"sha256:{hashlib.sha256(contents).hexdigest()}" != digest:
        raise ReleaseArtifactError(f"archive {label} digest mismatch: {inspection.path}")
    inspection.blob_cache[blob_name] = contents
    return contents


def _parse_archive_json_object(contents: bytes, path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(contents)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, MemoryError) as error:
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
    inspection: _ArchiveInspection,
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
            inspection,
            nested_digest,
            label="nested descriptor",
            # Docker 29 retains source index entries for platforms whose blobs
            # are not included in a selected-platform docker save archive.
            allow_missing=True,
        )
        if nested_bytes is None:
            continue
        assert isinstance(nested_digest, str)
        nested_value = _parse_archive_json_object(
            nested_bytes, inspection.path, label="nested descriptor"
        )
        nested_config = nested_value.get("config")
        if not isinstance(nested_config, dict):
            continue
        platform_value = nested.get("platform")
        if platform_value is not None and not isinstance(platform_value, dict):
            raise ReleaseArtifactError(
                f"archive nested descriptor platform is invalid: {inspection.path}"
            )
        if nested_config.get("digest") != config_digest:
            nested_config_bytes = _read_archive_sha256_blob(
                inspection,
                nested_config.get("digest"),
                label="nested config",
            )
            assert nested_config_bytes is not None
            nested_config_value = _parse_archive_json_object(
                nested_config_bytes, inspection.path, label="nested config"
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
                    f"archive contains an additional runnable descriptor: {inspection.path}"
                )
            continue
        if isinstance(platform_value, dict) and (
            platform_value.get("os") != config.get("os")
            or platform_value.get("architecture") != config.get("architecture")
        ):
            raise ReleaseArtifactError(
                f"archive nested descriptor platform mismatch: {inspection.path}"
            )
        matching_nested.append(nested_digest)
    if len(matching_nested) > 1:
        raise ReleaseArtifactError(f"archive main descriptor is not unique: {inspection.path}")
    return matching_nested[0] if matching_nested else None


def _validate_provenance_attachment(
    inspection: _ArchiveInspection,
    descriptor: dict[str, Any],
    descriptor_value: dict[str, Any],
    main_manifest_digest: str,
) -> None:
    if (
        descriptor.get("mediaType") != OCI_IMAGE_MANIFEST_MEDIA_TYPE
        or descriptor_value.get("schemaVersion") != OCI_SCHEMA_VERSION
        or descriptor_value.get("mediaType") != OCI_IMAGE_MANIFEST_MEDIA_TYPE
    ):
        raise ReleaseArtifactError(f"unsupported archive attachment: {inspection.path}")
    annotations = descriptor.get("annotations")
    subject = (
        annotations.get(CONTAINERD_SUBJECT_ANNOTATION) if isinstance(annotations, dict) else None
    )
    if subject != main_manifest_digest:
        raise ReleaseArtifactError(f"archive provenance subject mismatch: {inspection.path}")
    descriptor_platform = descriptor.get("platform")
    if descriptor_platform is not None and (
        not isinstance(descriptor_platform, dict)
        or descriptor_platform.get("os") != "unknown"
        or descriptor_platform.get("architecture") != "unknown"
    ):
        raise ReleaseArtifactError(
            f"archive provenance descriptor platform mismatch: {inspection.path}"
        )
    manifest_subject = descriptor_value.get("subject")
    if manifest_subject is not None and (
        not isinstance(manifest_subject, dict)
        or manifest_subject.get("digest") != main_manifest_digest
    ):
        raise ReleaseArtifactError(f"archive provenance subject mismatch: {inspection.path}")

    config_descriptor = descriptor_value.get("config")
    if (
        not isinstance(config_descriptor, dict)
        or config_descriptor.get("mediaType") != OCI_IMAGE_CONFIG_MEDIA_TYPE
    ):
        raise ReleaseArtifactError(f"archive provenance config is invalid: {inspection.path}")
    config_bytes = _read_archive_sha256_blob(
        inspection,
        config_descriptor.get("digest"),
        label="provenance config",
    )
    assert config_bytes is not None
    provenance_config = _parse_archive_json_object(
        config_bytes, inspection.path, label="provenance config"
    )
    if (
        provenance_config.get("os") != "unknown"
        or provenance_config.get("architecture") != "unknown"
    ):
        raise ReleaseArtifactError(
            f"archive provenance config platform mismatch: {inspection.path}"
        )

    layers = descriptor_value.get("layers")
    if not isinstance(layers, list) or len(layers) != 1:
        raise ReleaseArtifactError(f"archive provenance layers are invalid: {inspection.path}")
    for layer in layers:
        if not isinstance(layer, dict) or layer.get("mediaType") != IN_TOTO_MEDIA_TYPE:
            raise ReleaseArtifactError(
                f"archive provenance layer media type is invalid: {inspection.path}"
            )
        layer_annotations = layer.get("annotations")
        if (
            not isinstance(layer_annotations, dict)
            or layer_annotations.get(IN_TOTO_PREDICATE_ANNOTATION) != SLSA_PROVENANCE_V1
        ):
            raise ReleaseArtifactError(f"archive provenance layer is invalid: {inspection.path}")
        layer_bytes = _read_archive_sha256_blob(
            inspection,
            layer.get("digest"),
            label="provenance layer",
        )
        assert layer_bytes is not None
        statement = _parse_archive_json_object(
            layer_bytes, inspection.path, label="provenance layer"
        )
        _validate_slsa_provenance_statement(statement, inspection.path, main_manifest_digest)


def inspect_docker_archive(  # noqa: PLR0912, PLR0915
    path: Path, expected_reference: str
) -> ArchiveIdentity:
    try:
        _preflight_docker_archive(path)
        with tarfile.open(path, mode="r:gz") as archive:
            members = _bounded_docker_archive_members(archive, path)
            names = [member.name for member in members]
            if len(names) != len(set(names)):
                raise ReleaseArtifactError(f"archive contains duplicate members: {path}")
            members_by_name = {member.name: member for member in members}
            inspection = _ArchiveInspection(archive, path, members_by_name)
            for member in members:
                _validate_relative_path(member.name.rstrip("/") or member.name)
                if member.issym() or member.islnk():
                    raise ReleaseArtifactError(
                        f"archive contains a link member: {path}:{member.name}"
                    )
            manifest_member = members_by_name.get("manifest.json")
            if manifest_member is None:
                raise ReleaseArtifactError(f"archive is missing manifest.json: {path}")
            manifest_bytes = _read_archive_member_bytes(
                archive, manifest_member, path, label="manifest.json"
            )
            manifest_value = json.loads(manifest_bytes)
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
            config_member = members_by_name.get(config_name)
            if config_member is None:
                raise ReleaseArtifactError(f"archive config is missing: {path}:{config_name}")
            config_bytes = _read_archive_member_bytes(archive, config_member, path, label="config")
            try:
                config = json.loads(config_bytes)
            except json.JSONDecodeError as error:
                raise ReleaseArtifactError(f"archive config is invalid JSON: {path}") from error
            if not isinstance(config, dict):
                raise ReleaseArtifactError(f"archive config root is invalid: {path}")
            config_digest = f"sha256:{hashlib.sha256(config_bytes).hexdigest()}"
            image_id = config_digest
            if "index.json" in names:
                index_member = members_by_name["index.json"]
                index_bytes = _read_archive_member_bytes(
                    archive, index_member, path, label="index.json"
                )
                index_value = json.loads(index_bytes)
                descriptors = (
                    index_value.get("manifests") if isinstance(index_value, dict) else None
                )
                if not isinstance(descriptors, list) or not descriptors:
                    raise ReleaseArtifactError(
                        f"archive index must contain image descriptors: {path}"
                    )
                if len(descriptors) > MAX_DOCKER_DESCRIPTOR_REFERENCES:
                    raise ReleaseArtifactError(
                        f"archive descriptor reference budget exceeded: {path}"
                    )
                loaded_descriptors: list[
                    tuple[dict[str, Any], str, dict[str, Any], str | None]
                ] = []
                for descriptor in descriptors:
                    if not isinstance(descriptor, dict):
                        raise ReleaseArtifactError(f"archive descriptor is invalid: {path}")
                    descriptor_digest = descriptor.get("digest")
                    descriptor_bytes = _read_archive_sha256_blob(
                        inspection,
                        descriptor_digest,
                        label="descriptor",
                    )
                    assert descriptor_bytes is not None
                    assert isinstance(descriptor_digest, str)
                    descriptor_value = _parse_archive_json_object(
                        descriptor_bytes, path, label="descriptor"
                    )
                    main_manifest_digest = _resolve_main_manifest_digest(
                        inspection,
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
                            inspection,
                            descriptor,
                            descriptor_value,
                            main_manifest_digest,
                        )
    except (
        tarfile.TarError,
        gzip.BadGzipFile,
        json.JSONDecodeError,
        RecursionError,
        MemoryError,
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


def _inspect_loaded_candidate_image(
    image: ImageArtifact, runner: Runner, *, root: Path
) -> InspectedImage:
    expected = (image.image_id, image.os, image.architecture)
    inspected = inspect_image(image.image_id, runner, root=root)
    actual = (inspected.image_id, inspected.os, inspected.architecture)
    if actual != expected:
        raise ReleaseArtifactError(
            f"loaded image identity mismatch for {image.component}: "
            f"expected {expected}, got {actual}"
        )
    reference = inspect_image(image.candidate_reference, runner, root=root)
    reference_identity = (reference.image_id, reference.os, reference.architecture)
    if reference_identity != expected or image.candidate_reference not in reference.repo_tags:
        raise ReleaseArtifactError(
            f"loaded candidate reference mismatch for {image.component}: "
            f"expected {expected}, got {reference_identity}"
        )
    return inspected


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
    qualification_toolchain: QualificationToolchainDescriptor | None = None,
) -> str:
    value: dict[str, Any] = {
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
    if qualification_toolchain is not None:
        value["qualification_toolchain"] = asdict(qualification_toolchain)
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
    if manifest.qualification_toolchain is not None:
        toolchain = manifest.qualification_toolchain
        lines.extend(
            [
                "",
                "## Qualification Toolchain",
                "",
                f"- Archive: `{toolchain.path}`",
                f"- Format: `{toolchain.format}`",
                f"- SHA-256: `{toolchain.sha256}`",
                f"- Semantic validator: `{toolchain.semantic_validator}`",
                f"- Schema: `{toolchain.schema.path}` (`{toolchain.schema.sha256}`)",
                f"- Validator: `{toolchain.validator.path}` (`{toolchain.validator.sha256}`)",
                f"- Producer: `{toolchain.producer.path}` (`{toolchain.producer.sha256}`)",
                "- Receipt producer: "
                f"`{toolchain.receipt_producer.path}` "
                f"(`{toolchain.receipt_producer.sha256}`)",
                "- Toolchain manifest: "
                f"`{toolchain.toolchain_manifest.path}` "
                f"(`{toolchain.toolchain_manifest.sha256}`)",
            ]
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


def _artifact_identity_from_dict(value: object, *, label: str) -> ArtifactIdentity:
    if (
        not isinstance(value, dict)
        or set(value) != {"path", "sha256"}
        or not isinstance(value.get("path"), str)
        or not isinstance(value.get("sha256"), str)
    ):
        raise ReleaseArtifactError(f"MANIFEST.json {label} identity is invalid")
    return ArtifactIdentity(path=value["path"], sha256=value["sha256"])


def _qualification_descriptor_from_dict(value: object) -> QualificationToolchainDescriptor:
    required_keys = {
        "path",
        "sha256",
        "format",
        "semantic_validator",
        "schema",
        "validator",
        "producer",
        "receipt_producer",
        "toolchain_manifest",
    }
    if not isinstance(value, dict) or set(value) != required_keys:
        raise ReleaseArtifactError("MANIFEST.json qualification_toolchain keys mismatch")
    if not all(
        isinstance(value.get(name), str)
        for name in ("path", "sha256", "format", "semantic_validator")
    ):
        raise ReleaseArtifactError("MANIFEST.json qualification_toolchain fields are invalid")
    return QualificationToolchainDescriptor(
        path=value["path"],
        sha256=value["sha256"],
        format=value["format"],
        semantic_validator=value["semantic_validator"],
        schema=_artifact_identity_from_dict(value["schema"], label="schema"),
        validator=_artifact_identity_from_dict(value["validator"], label="validator"),
        producer=_artifact_identity_from_dict(value["producer"], label="producer"),
        receipt_producer=_artifact_identity_from_dict(
            value["receipt_producer"], label="receipt_producer"
        ),
        toolchain_manifest=_artifact_identity_from_dict(
            value["toolchain_manifest"], label="toolchain_manifest"
        ),
    )


def _manifest_from_dict(value: dict[str, Any]) -> CandidateManifest:
    base_keys = {
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
    schema_version = value.get("schema_version")
    required_keys = (
        base_keys | {"qualification_toolchain"}
        if schema_version == MANIFEST_SCHEMA_VERSION and not isinstance(schema_version, bool)
        else base_keys
    )
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
    qualification_toolchain = None
    if schema_version == MANIFEST_SCHEMA_VERSION and not isinstance(schema_version, bool):
        qualification_toolchain = _qualification_descriptor_from_dict(
            value["qualification_toolchain"]
        )
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
            qualification_toolchain=qualification_toolchain,
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
        or manifest.schema_version not in SUPPORTED_MANIFEST_SCHEMA_VERSIONS
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
    if manifest.schema_version == LEGACY_MANIFEST_SCHEMA_VERSION:
        if manifest.qualification_toolchain is not None:
            raise ReleaseArtifactError("manifest v2 forbids qualification_toolchain")
    else:
        descriptor = manifest.qualification_toolchain
        if descriptor is None:
            raise ReleaseArtifactError("manifest v3 requires qualification_toolchain")
        if descriptor.path != QUALIFICATION_TOOLCHAIN_ARCHIVE:
            raise ReleaseArtifactError("qualification toolchain path is invalid")
        if descriptor.format != QUALIFICATION_TOOLCHAIN_FORMAT:
            raise ReleaseArtifactError("qualification toolchain format is invalid")
        if descriptor.semantic_validator != SEMANTIC_VALIDATOR_ID:
            raise ReleaseArtifactError("qualification toolchain semantic validator is invalid")
        if SHA256_PATTERN.fullmatch(descriptor.sha256) is None:
            raise ReleaseArtifactError("qualification toolchain SHA-256 is invalid")
        for name, expected_path in {
            "schema": "schemas/point-profile/point-profile-v1.schema.json",
            "validator": "tools/validate_device_point_profile.py",
            "producer": "tools/release_artifacts.py",
            "receipt_producer": "tools/release_verification_receipt.py",
            "toolchain_manifest": QUALIFICATION_TOOLCHAIN_MANIFEST,
        }.items():
            _validate_toolchain_identity(getattr(descriptor, name), expected_path=expected_path)
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
        qualification_toolchain=manifest.qualification_toolchain,
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


def _expected_candidate_files(schema_version: int = MANIFEST_SCHEMA_VERSION) -> set[str]:
    if schema_version == LEGACY_MANIFEST_SCHEMA_VERSION:
        fixed_files = FIXED_PACKAGE_FILES_V2
    elif schema_version == MANIFEST_SCHEMA_VERSION:
        fixed_files = FIXED_PACKAGE_FILES
    else:
        raise ReleaseArtifactError("unsupported manifest schema_version")
    return fixed_files | {f"images/{component}.tar.gz" for component in COMPONENTS}


def _select_candidate_file_set(actual_files: set[str]) -> tuple[int, set[str]]:
    matches = [
        (schema_version, _expected_candidate_files(schema_version))
        for schema_version in SUPPORTED_MANIFEST_SCHEMA_VERSIONS
        if actual_files == _expected_candidate_files(schema_version)
    ]
    if len(matches) != 1:
        expected_v2 = _expected_candidate_files(LEGACY_MANIFEST_SCHEMA_VERSION)
        expected_v3 = _expected_candidate_files(MANIFEST_SCHEMA_VERSION)
        raise ReleaseArtifactError(
            "publisher authenticity FAILED: candidate file allowlist mismatch: does not match "
            "complete v2 or v3: "
            f"v2_missing={sorted(expected_v2 - actual_files)}, "
            f"v2_extra={sorted(actual_files - expected_v2)}, "
            f"v3_missing={sorted(expected_v3 - actual_files)}, "
            f"v3_extra={sorted(actual_files - expected_v3)}"
        )
    return matches[0]


@contextmanager
def _protected_candidate_snapshot(  # noqa: PLR0912, PLR0915
    package: Path, *, parent: Path | None = None
) -> Iterator[Path]:
    if package.is_symlink() or not package.is_dir():
        raise ReleaseArtifactError(
            "publisher authenticity FAILED: candidate directory is missing or linked"
        )
    package = package.resolve()
    actual_files = _package_file_set(package)
    _schema_version, expected_files = _select_candidate_file_set(actual_files)
    initial_identities: dict[str, tuple[int, int, int, int, int, int]] = {}
    initial_digests: dict[str, str] = {}
    try:
        for relative in sorted(expected_files):
            source = package / relative
            path_before = source.stat(follow_symlinks=False)
            if not stat.S_ISREG(path_before.st_mode) or path_before.st_nlink != 1:
                raise ReleaseArtifactError(
                    "publisher authenticity FAILED: candidate file is not a unique "
                    f"regular file: {relative}"
                )
            expected_identity = _file_stat_identity(path_before)
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(source, flags)
            try:
                before = os.fstat(descriptor)
                if _file_stat_identity(before) != expected_identity or not os.path.samestat(
                    before, path_before
                ):
                    raise ReleaseArtifactError(
                        "publisher authenticity FAILED: candidate file changed before "
                        f"snapshot: {relative}"
                    )
                digest = hashlib.sha256()
                read_size = 0
                with os.fdopen(descriptor, "rb", closefd=False) as source_stream:
                    for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                        read_size += len(chunk)
                after = os.fstat(descriptor)
                path_after = source.stat(follow_symlinks=False)
                if (
                    read_size != before.st_size
                    or _file_stat_identity(after) != expected_identity
                    or _file_stat_identity(path_after) != expected_identity
                    or not os.path.samestat(after, path_after)
                ):
                    raise ReleaseArtifactError(
                        "publisher authenticity FAILED: candidate file changed during "
                        f"snapshot initial scan: {relative}"
                    )
                initial_identities[relative] = expected_identity
                initial_digests[relative] = digest.hexdigest()
            finally:
                os.close(descriptor)
    except OSError as error:
        raise ReleaseArtifactError(
            "publisher authenticity FAILED: cannot complete initial candidate snapshot scan: "
            f"{error}"
        ) from error
    total_size = sum(identity[3] for identity in initial_identities.values())
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
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(source, flags)
                try:
                    opened = os.fstat(descriptor)
                    expected_identity = initial_identities[relative]
                    if (
                        not stat.S_ISREG(opened.st_mode)
                        or opened.st_nlink != 1
                        or _file_stat_identity(opened) != expected_identity
                    ):
                        raise ReleaseArtifactError(
                            "publisher authenticity FAILED: candidate file changed before "
                            f"snapshot: {relative}"
                        )
                    expected_size = expected_identity[3]
                    copied = 0
                    copied_digest = hashlib.sha256()
                    with (
                        os.fdopen(descriptor, "rb", closefd=False) as input_stream,
                        destination.open("xb") as output_stream,
                    ):
                        while copied < expected_size:
                            chunk = input_stream.read(min(1024 * 1024, expected_size - copied))
                            if not chunk:
                                break
                            output_stream.write(chunk)
                            copied_digest.update(chunk)
                            copied += len(chunk)
                        if copied != expected_size or input_stream.read(1):
                            raise ReleaseArtifactError(
                                "publisher authenticity FAILED: candidate file size changed "
                                f"during snapshot: {relative}"
                            )
                    if copied_digest.hexdigest() != initial_digests[relative]:
                        raise ReleaseArtifactError(
                            "publisher authenticity FAILED: candidate file content changed "
                            f"during snapshot: {relative}"
                        )
                    after = os.fstat(descriptor)
                    path_after = source.stat(follow_symlinks=False)
                    if (
                        not stat.S_ISREG(after.st_mode)
                        or after.st_nlink != 1
                        or _file_stat_identity(after) != expected_identity
                        or _file_stat_identity(path_after) != expected_identity
                        or not os.path.samestat(after, path_after)
                    ):
                        raise ReleaseArtifactError(
                            "publisher authenticity FAILED: candidate file changed during "
                            f"snapshot: {relative}"
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
    actual_files = _package_file_set(package)
    expected_schema_version, expected_files = _select_candidate_file_set(actual_files)
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
    if manifest.schema_version != expected_schema_version:
        raise ReleaseArtifactError(
            "publisher authenticity FAILED: manifest schema/file-set version mismatch"
        )
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
    if manifest.qualification_toolchain is not None:
        _verify_qualification_toolchain(package, manifest.qualification_toolchain, sums)
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
            _inspect_loaded_candidate_image(image, runner, root=snapshot)
        return manifest


def _git_head(root: Path, runner: Runner) -> str:
    source_commit = runner.run(
        ["git", "rev-parse", "--verify", "--end-of-options", "HEAD^{commit}"],
        cwd=root,
    )
    if SOURCE_COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise ReleaseArtifactError("git rev-parse did not return a full lowercase commit")
    return source_commit


def _git_state(root: Path, runner: Runner) -> tuple[str, str]:
    source_commit = _git_head(root, runner)
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
        source_deploy / "site-modbus-probe.json.example",
        package / "site-modbus-probe.json.example",
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
    shutil.copyfile(root / "tools" / "probe_modbus_rtu.py", package / "probe_modbus_rtu.py")
    shutil.copyfile(root / "tools" / "run_modbus_probe.ps1", package / "run_modbus_probe.ps1")
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
        source_commit = _git_head(root, runner)
    output_root = output_root.absolute()
    if output_root.is_symlink():
        raise ReleaseArtifactError("candidate publish root must not be linked")
    final_directory = output_root / candidate_id
    if final_directory.exists():
        raise ReleaseArtifactError(f"candidate ID already exists: {final_directory}")
    references = candidate_image_references(candidate_id)
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
    if lock_root is None:
        resolved_lock_root = system_candidate_tag_lock_root()
    else:
        resolved_lock_root = lock_root.absolute()
        try:
            resolved_lock_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ReleaseArtifactError(
                f"cannot create candidate lock directory: {resolved_lock_root}: {error}"
            ) from error
        resolved_lock_root = _validate_atomic_publish_root(resolved_lock_root)

    pending_lock = candidate_tag_operation_lock(resolved_lock_root, candidate_id)
    pending_lock.__enter__()
    temporary_directory: Path | None = None
    candidate_tags_owned = False
    published = False
    try:
        if final_directory.exists():
            raise ReleaseArtifactError(f"candidate ID already exists: {final_directory}")
        _ensure_candidate_tags_absent(references, runner, root=root)
        try:
            output_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise ReleaseArtifactError(f"cannot create candidate publish root: {error}") from error
        output_root = _validate_atomic_publish_root(output_root)
        final_directory = output_root / candidate_id
        if final_directory.exists():
            raise ReleaseArtifactError(f"candidate ID already exists: {final_directory}")
        temporary_directory = Path(
            tempfile.mkdtemp(prefix=f".{candidate_id}.tmp-", dir=final_directory.parent)
        )
        (temporary_directory / "images").mkdir()
        candidate_tags_owned = True
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
        qualification_toolchain = _write_qualification_toolchain(
            root,
            temporary_directory,
            source_commit=source_commit,
            runner=runner,
        )
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
                qualification_toolchain=qualification_toolchain,
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
            qualification_toolchain=qualification_toolchain,
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
        if temporary_directory is not None:
            shutil.rmtree(temporary_directory, ignore_errors=True)
        cleanup_errors = (
            _remove_candidate_tags(references, runner, root=root) if candidate_tags_owned else []
        )
        if cleanup_errors:
            error.add_note("candidate tag cleanup failed: " + "; ".join(cleanup_errors))
        raise
    finally:
        active_error = sys.exception()
        try:
            pending_lock.__exit__(*sys.exc_info())
        except BaseException as lock_error:
            if active_error is not None:
                active_error.add_note(
                    "candidate tag operation lock release failed: "
                    f"{resolved_lock_root}: {lock_error}"
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
                    "candidate tag operation lock release failed: "
                    f"{resolved_lock_root}: {lock_error}"
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
        elif args.command == "verify":
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
