"""Read one SQL Server MDF data page and recover legacy point candidates.

The extractor is deliberately narrow and read-only.  It never attaches the MDF, scans
for other pages, or writes the source.  The supported page shape is the one frozen in
the Plan 5 B-08 evidence ledger.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import importlib
import json
import math
import ntpath
import os
import secrets
import stat
import struct
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, cast

PAGE_SIZE = 8192
PAGE_HEADER_SIZE = 96
DEFAULT_PAGE_NUMBER = 258
EXPECTED_PAGE_FILE_OFFSET = DEFAULT_PAGE_NUMBER * PAGE_SIZE
EXPECTED_PAGE_SHA256 = "56CFC72733B60C6C7CF321330F04F13A8A2883B7F742D8DA9A3B58F9EC4E5BD7"
EXPECTED_SOURCE_SIZE = 1_362_690_048
EXPECTED_SOURCE_SHA256 = "32BF2EAA2379B8D4B5BE58A3FFC833C245D0CEE8203CE97908D4E944D1C87A28"
EXPECTED_PAGE_HEADER_VERSION = 1
EXPECTED_PAGE_TYPE = 1
EXPECTED_DATABASE_FILE_ID = 1
EXPECTED_SLOT_COUNT = 46
EXPECTED_FIXED_LENGTH = 92
EXPECTED_COLUMN_COUNT = 21
EXPECTED_RECORD_STATUS = b"\x30\x00"
EXPECTED_MODEL_COUNTS = {"BCMM": 6, "CBMM": 40}
FC_READ_COILS = 1
FC_READ_HOLDING_REGISTERS = 3
EXPECTED_FUNCTION_CODES = {FC_READ_COILS, FC_READ_HOLDING_REGISTERS}
VARIABLE_FIELD_NAMES = ("DevType", "PointName", "UserPointName", "ValueType", "PointUint")
EXPECTED_VARIABLE_FIELD_COUNT = len(VARIABLE_FIELD_NAMES)
EXPECTED_VARIABLE_COLUMN_COUNTS = (EXPECTED_VARIABLE_FIELD_COUNT - 1, EXPECTED_VARIABLE_FIELD_COUNT)
CONTROL_CHARACTER_LIMIT = 32
MAX_POINT_NUMBER = 65_535
MAX_UPDATE_INTERVAL_SECONDS = 86_400
MAX_SUPPORTING_EVIDENCE_BYTES = 1024 * 1024
SOURCE_LOGICAL_PATH = "DataBase/DataBase/ModBus.mdf"
ALGORITHM_VERSION = "b08-mdf-page-258-v3"
PARSER_LOGICAL_PATH = "tools/extract_legacy_mdf_points.py"
B06_EVIDENCE_FILES = {
    "probe": (
        "docs/superpowers/specs/evidence/b06-20260827/"
        "modbus-probe-execute-20260827T1314+0800.jsonl",
        "E39CFC742F724E864686595635373832BDE48C10831AB08F6118E7CB376E489E",
    ),
    "runner": (
        "docs/superpowers/specs/evidence/b06-20260827/"
        "modbus-runner-9ec05b61-3081-49bd-8020-55fb78a9dcd7.jsonl",
        "E0A1459363E34D33A5D4B783C0F40D1FA8300F9F1556F2E9C41F9A0238B656DC",
    ),
}
EVIDENCE_SOURCE_FILES: dict[str, dict[str, Any]] = {
    "legacy_csharp_database": {
        "path": "济南大学开发软件/WEB/ModBusWeb20210811/IOTWeb/DataBase.cs",
        "sha256": "BBA66F6F4A88121E52C5BDBCAFB0D110F86FAE7CC63B5F1CDF124EA807DA2AD2",
        "evidence_ids": ["LEGACY_CSHARP_FIELD_MAPPING", "LEGACY_CSHARP_FORMULA_PATHS"],
        "locators": [
            {"purpose": "PointData field declaration", "line_start": 108, "line_end": 131},
            {
                "purpose": "database field mapping and load formula",
                "line_start": 1349,
                "line_end": 1379,
            },
            {"purpose": "history decode and scaling formula", "line_start": 1614, "line_end": 1668},
            {"purpose": "alternate history formula", "line_start": 2913, "line_end": 2930},
        ],
    },
    "legacy_server_database": {
        "path": "ModBusServer20210908/ModBusServer20210908/ModBusServer/DataBase.cs",
        "sha256": "692A06CCDEBB9C059DFCA81AD1A8F5DE2BF9818555C66F309C89CD4890D56FAB",
        "evidence_ids": ["LEGACY_CSHARP_FIELD_MAPPING", "LEGACY_CSHARP_FORMULA_PATHS"],
        "locators": [
            {"purpose": "PointData field declaration", "line_start": 73, "line_end": 100},
            {
                "purpose": "database field mapping and load formula",
                "line_start": 891,
                "line_end": 920,
            },
        ],
    },
    "legacy_server_runtime": {
        "path": "ModBusServer20210908/ModBusServer20210908/ModBusServer/ModBusServer.cs",
        "sha256": "DE6FA2B3136A5496CA125BC5721FE848B1B890D4D33096007C52C9FE119A68D6",
        "evidence_ids": ["LEGACY_CSHARP_FORMULA_PATHS"],
        "locators": [
            {"purpose": "signed 16-bit realtime formula", "line_start": 1765, "line_end": 1773},
            {
                "purpose": "unsigned 16-bit realtime formula",
                "line_start": 1776,
                "line_end": 1784,
            },
            {"purpose": "32-bit realtime formula", "line_start": 1788, "line_end": 1798},
        ],
    },
    "current_point_api_contract": {
        "path": "ruisheng-api/src/ruisheng_api/api/schemas/points.py",
        "sha256": "67D3359866E554192ECBCB8C4081C6035C9D904D2D0FEB449857273A9A6A8E0A",
        "evidence_ids": ["CURRENT_POINT_API_CONTRACT"],
        "locators": [
            {
                "purpose": "allowed function/value types and RBit contract",
                "line_start": 7,
                "line_end": 33,
            },
            {"purpose": "point create schema", "line_start": 57, "line_end": 84},
        ],
    },
    "current_point_api_routes": {
        "path": "ruisheng-api/src/ruisheng_api/api/points.py",
        "sha256": "3C4B46C135164D07F2D98D0DA0F530876138D170D53131759F918AB707437347",
        "evidence_ids": ["CURRENT_POINT_API_MUTATION_PATHS"],
        "locators": [
            {"purpose": "point mutation fields", "line_start": 28, "line_end": 61},
            {
                "purpose": "point import/update transaction paths",
                "line_start": 180,
                "line_end": 225,
            },
        ],
    },
    "current_device_api_routes": {
        "path": "ruisheng-api/src/ruisheng_api/api/devices.py",
        "sha256": "26F8DBFD2FA6D639D7BB1A45A071C6ED9CAEACD5248F5184DD3D95E4E1909FDB",
        "evidence_ids": ["CURRENT_DEVICE_API_MUTATION_PATHS"],
        "locators": [
            {"purpose": "device create path", "line_start": 64, "line_end": 88},
            {"purpose": "device enable path", "line_start": 137, "line_end": 160},
        ],
    },
    "current_gateway_decode": {
        "path": "ruisheng-gw/src/ruisheng_gw/ingest.py",
        "sha256": "CF9A7057D51AB654EFA86EA65AC5DC0DA3FA3B4113436EB8EB9D5AB34F98014B",
        "evidence_ids": ["CURRENT_GATEWAY_DECODE_SEMANTICS"],
        "locators": [
            {"purpose": "coil/register decode", "line_start": 220, "line_end": 280},
        ],
    },
    "current_gateway_scaling": {
        "path": "ruisheng-gw/src/ruisheng_gw/domain/point.py",
        "sha256": "600EA4F12E6FB4FDC2DDF42981767A93272A4D6D673800AA0A83CFC55DE3583A",
        "evidence_ids": ["CURRENT_GATEWAY_SCALING_FORMULA"],
        "locators": [
            {"purpose": "scaling formula and overflow handling", "line_start": 1, "line_end": 49},
        ],
    },
    "current_gateway_registry": {
        "path": "ruisheng-gw/src/ruisheng_gw/domain/registry.py",
        "sha256": "96F6F78B10CD0C84F201D443D8D8FA4687809C5401E471C66C0242C8A19DF132",
        "evidence_ids": ["CURRENT_GATEWAY_REGISTRY_MAPPING"],
        "locators": [
            {"purpose": "device and point mapping", "line_start": 40, "line_end": 119},
            {"purpose": "startup database load", "line_start": 150, "line_end": 185},
        ],
    },
    "current_gateway_poller": {
        "path": "ruisheng-gw/src/ruisheng_gw/scheduler/poller.py",
        "sha256": "36C3E93EEBE0B24B45700FEEA6E7A3D97173B214E10E1E300B22C3C864A6DB9C",
        "evidence_ids": ["CURRENT_GATEWAY_POLL_ROUTING"],
        "locators": [
            {
                "purpose": "poll grouping and Modbus address routing",
                "line_start": 32,
                "line_end": 118,
            },
        ],
    },
    "current_gateway_runtime": {
        "path": "ruisheng-gw/src/ruisheng_gw/main.py",
        "sha256": "3FBF6FDE519E3C526A55401AFAAD03FC4D146240F2FC089D80D2723654C0D203",
        "evidence_ids": ["CURRENT_GATEWAY_STARTUP_AND_RELOAD", "CURRENT_GATEWAY_SERIAL_SOURCE"],
        "locators": [
            {"purpose": "registry startup load", "line_start": 115, "line_end": 124},
            {"purpose": "alarm-only reload paths", "line_start": 159, "line_end": 202},
            {"purpose": "serial buses from runtime config", "line_start": 356, "line_end": 379},
        ],
    },
    "current_gateway_serial_config": {
        "path": "ruisheng-gw/src/ruisheng_gw/config.py",
        "sha256": "6D4B8107B05C536DBF604E0B803ADE04AF87B7020118BE6937318AEBD818847E",
        "evidence_ids": ["CURRENT_GATEWAY_SERIAL_CONFIG"],
        "locators": [
            {"purpose": "GW_SERIAL_PORTS settings field", "line_start": 79, "line_end": 103},
        ],
    },
}


class MdfEvidenceError(ValueError):
    """The selected page does not satisfy the frozen evidence contract."""


@dataclass(frozen=True)
class RecordLayout:
    slot_index: int
    page_offset: int
    record_length: int
    fixed_length_value: int
    column_count: int
    null_bitmap_hex: str
    null_column_indexes: tuple[int, ...]
    variable_column_count: int
    variable_end_offsets: tuple[int, ...]


@dataclass(frozen=True)
class BoundDirectory:
    path: Path
    identity: os.stat_result
    posix_descriptor: int | None = None
    windows_handles: tuple[int, ...] = ()


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


_WIN_GENERIC_READ = 0x80000000
_WIN_GENERIC_WRITE = 0x40000000
_WIN_DELETE = 0x00010000
_WIN_FILE_LIST_DIRECTORY = 0x0001
_WIN_FILE_ADD_FILE = 0x0002
_WIN_FILE_TRAVERSE = 0x0020
_WIN_FILE_READ_ATTRIBUTES = 0x0080
_WIN_FILE_SHARE_READ = 0x00000001
_WIN_FILE_SHARE_WRITE = 0x00000002
_WIN_CREATE_NEW = 1
_WIN_OPEN_EXISTING = 3
_WIN_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_WIN_FILE_ATTRIBUTE_NORMAL = 0x00000080
_WIN_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_WIN_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WIN_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WIN_FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
_WIN_FILE_RENAME_INFO_EX_CLASS = 22
_WIN_FILE_DISPOSITION_INFO_CLASS = 4
_WIN_ERROR_FILE_EXISTS = 80
_WIN_ERROR_ALREADY_EXISTS = 183
_WIN_ERROR_INVALID_PARAMETER = 87
_IS_WINDOWS = os.name == "nt"
_OS_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_OS_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_OS_O_TMPFILE = getattr(os, "O_TMPFILE", 0)
_POSIX_AT_EMPTY_PATH = 0x1000


def _u16(data: bytes, offset: int) -> int:
    try:
        return cast(int, struct.unpack_from("<H", data, offset)[0])
    except struct.error as exc:
        raise MdfEvidenceError(f"uint16 at offset {offset} is outside the record") from exc


def _u32(data: bytes, offset: int) -> int:
    try:
        return cast(int, struct.unpack_from("<I", data, offset)[0])
    except struct.error as exc:
        raise MdfEvidenceError(f"uint32 at offset {offset} is outside the record") from exc


def _i32(data: bytes, offset: int) -> int:
    try:
        return cast(int, struct.unpack_from("<i", data, offset)[0])
    except struct.error as exc:
        raise MdfEvidenceError(f"int32 at offset {offset} is outside the record") from exc


def _f64(data: bytes, offset: int) -> float:
    try:
        return cast(float, struct.unpack_from("<d", data, offset)[0])
    except struct.error as exc:
        raise MdfEvidenceError(f"float64 at offset {offset} is outside the record") from exc


def _sha256_stream(stream: BinaryIO, *, maximum_bytes: int) -> str:
    digest = hashlib.sha256()
    remaining = maximum_bytes + 1
    while remaining and (chunk := stream.read(min(1024 * 1024, remaining))):
        digest.update(chunk)
        remaining -= len(chunk)
    if remaining == 0:
        raise MdfEvidenceError("file exceeds the configured supporting evidence size boundary")
    return digest.hexdigest().upper()


def _stat_signature(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_mode,
        value.st_dev,
        value.st_ino,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        int(getattr(value, "st_file_attributes", 0)),
    )


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return os.path.samestat(left, right) and stat.S_IFMT(left.st_mode) == stat.S_IFMT(right.st_mode)


def _win_kernel32() -> Any:
    if os.name != "nt":
        raise MdfEvidenceError("Windows handle operation requested on a non-Windows platform")
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
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in (None, invalid_handle):
        error_code = ctypes.get_last_error()
        raise ctypes.WinError(error_code)
    return cast(int, handle)


def _win_close_handle(handle: int) -> None:
    kernel32 = _win_kernel32()
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    if not close_handle(ctypes.c_void_p(handle)):
        error_code = ctypes.get_last_error()
        raise OSError(error_code, ctypes.FormatError(error_code))


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
        _WIN_FILE_ATTRIBUTE_TAG_INFO_CLASS,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        error_code = ctypes.get_last_error()
        raise OSError(error_code, ctypes.FormatError(error_code))
    return information


def _win_final_path(handle: int) -> str:
    kernel32 = _win_kernel32()
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
    ]
    get_final_path.restype = ctypes.c_uint32
    buffer = ctypes.create_unicode_buffer(32_768)
    length = get_final_path(ctypes.c_void_p(handle), buffer, len(buffer), 0)
    if length == 0 or length >= len(buffer):
        error_code = ctypes.get_last_error()
        raise OSError(error_code, ctypes.FormatError(error_code))
    raw = buffer.value
    if raw.startswith("\\\\?\\UNC\\"):
        raw = "\\\\" + raw[8:]
    elif raw.startswith("\\\\?\\"):
        raw = raw[4:]
    return ntpath.normcase(ntpath.normpath(raw))


def _win_expected_path(path: Path) -> str:
    return ntpath.normcase(ntpath.normpath(os.fspath(path)))


def _win_descriptor_handle(descriptor: int) -> int:
    msvcrt = importlib.import_module("msvcrt")
    return cast(int, msvcrt.get_osfhandle(descriptor))


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
        _WIN_FILE_DISPOSITION_INFO_CLASS,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        error_code = ctypes.get_last_error()
        raise OSError(error_code, ctypes.FormatError(error_code))


def _win_rename_handle_no_replace(handle: int, output: Path) -> None:
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
        _WIN_FILE_RENAME_INFO_EX_CLASS,
        buffer,
        size,
    ):
        error_code = ctypes.get_last_error()
        if error_code in (_WIN_ERROR_FILE_EXISTS, _WIN_ERROR_ALREADY_EXISTS):
            raise FileExistsError(error_code, "output already exists", os.fspath(output))
        if error_code == _WIN_ERROR_INVALID_PARAMETER:
            raise MdfEvidenceError(
                "Windows handle-bound no-replace rename is unavailable; refusing path fallback"
            )
        raise OSError(error_code, ctypes.FormatError(error_code), os.fspath(output))


def _open_windows_bound_directory(path: Path, *, label: str, writable: bool) -> BoundDirectory:
    absolute = Path(os.path.abspath(path))
    if not absolute.anchor:
        raise MdfEvidenceError(f"{label} must be absolute")
    current = Path(absolute.anchor)
    subjects = [current]
    for component in absolute.parts[1:]:
        current /= component
        subjects.append(current)

    handles: list[int] = []
    try:
        for index, subject in enumerate(subjects):
            desired_access = (
                _WIN_FILE_READ_ATTRIBUTES | _WIN_FILE_LIST_DIRECTORY | _WIN_FILE_TRAVERSE
            )
            if writable and index == len(subjects) - 1:
                desired_access |= _WIN_FILE_ADD_FILE
            handle = _win_create_handle(
                subject,
                desired_access=desired_access,
                share_mode=_WIN_FILE_SHARE_READ | _WIN_FILE_SHARE_WRITE,
                creation_disposition=_WIN_OPEN_EXISTING,
                flags_and_attributes=(
                    _WIN_FILE_FLAG_BACKUP_SEMANTICS | _WIN_FILE_FLAG_OPEN_REPARSE_POINT
                ),
            )
            handles.append(handle)
            attributes = _win_handle_attributes(handle).file_attributes
            if not attributes & _WIN_FILE_ATTRIBUTE_DIRECTORY:
                raise MdfEvidenceError(f"{label} component is not a directory: {subject}")
            if attributes & _WIN_FILE_ATTRIBUTE_REPARSE_POINT:
                raise MdfEvidenceError(f"{label} must not traverse a symlink or reparse point")
            if _win_final_path(handle) != _win_expected_path(subject):
                raise MdfEvidenceError(f"{label} final path changed while binding: {subject}")
        identity = absolute.stat(follow_symlinks=False)
        if _is_reparse(identity) or not stat.S_ISDIR(identity.st_mode):
            raise MdfEvidenceError(f"{label} is not a bound regular directory")
        return BoundDirectory(path=absolute, identity=identity, windows_handles=tuple(handles))
    except Exception:
        for handle in reversed(handles):
            with suppress(OSError):
                _win_close_handle(handle)
        raise


def _open_posix_bound_directory(path: Path, *, label: str) -> BoundDirectory:
    if os.open not in os.supports_dir_fd or not _OS_O_DIRECTORY or not _OS_O_NOFOLLOW:
        raise MdfEvidenceError(
            f"{label} requires openat/O_NOFOLLOW directory binding on this platform"
        )
    absolute = Path(os.path.abspath(path))
    flags = os.O_RDONLY | _OS_O_DIRECTORY | _OS_O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(absolute.anchor, flags)
    try:
        for component in absolute.parts[1:]:
            try:
                next_descriptor = os.open(component, flags, dir_fd=descriptor)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise MdfEvidenceError(
                        f"{label} must not traverse a symlink or reparse point"
                    ) from exc
                raise
            os.close(descriptor)
            descriptor = next_descriptor
        identity = os.fstat(descriptor)
        if not stat.S_ISDIR(identity.st_mode):
            raise MdfEvidenceError(f"{label} is not a directory")
        return BoundDirectory(path=absolute, identity=identity, posix_descriptor=descriptor)
    except Exception:
        os.close(descriptor)
        raise


@contextmanager
def _bound_directory(path: Path, *, label: str, writable: bool = False) -> Iterator[BoundDirectory]:
    _reject_windows_special_path(path, label=label)
    _reject_parent_segments(path, label=label)
    try:
        bound = (
            _open_windows_bound_directory(path, label=label, writable=writable)
            if os.name == "nt"
            else _open_posix_bound_directory(path, label=label)
        )
    except OSError as exc:
        raise MdfEvidenceError(f"cannot bind {label}: {exc}") from exc
    try:
        yield bound
    finally:
        if bound.posix_descriptor is not None:
            os.close(bound.posix_descriptor)
        for handle in reversed(bound.windows_handles):
            _win_close_handle(handle)


def _validate_bound_directory_path(bound: BoundDirectory, *, label: str) -> None:
    try:
        current = bound.path.lstat()
    except OSError as exc:
        raise MdfEvidenceError(f"cannot revalidate {label}: {exc}") from exc
    if (
        _is_reparse(current)
        or not stat.S_ISDIR(current.st_mode)
        or not _same_identity(current, bound.identity)
    ):
        raise MdfEvidenceError(f"{label} identity changed during the operation")


def _bound_lstat(bound: BoundDirectory, name: str) -> os.stat_result:
    if not name or Path(name).name != name:
        raise MdfEvidenceError("bound-directory operation requires a leaf name")
    if bound.posix_descriptor is not None:
        return os.stat(
            name,
            dir_fd=bound.posix_descriptor,
            follow_symlinks=False,
        )
    return (bound.path / name).stat(follow_symlinks=False)


def _bound_lexists(bound: BoundDirectory, name: str) -> bool:
    try:
        _bound_lstat(bound, name)
    except FileNotFoundError:
        return False
    return True


def _fsync_bound_directory(bound: BoundDirectory) -> None:
    if bound.posix_descriptor is not None:
        os.fsync(bound.posix_descriptor)


def _posix_link_anonymous_no_replace(
    source_descriptor: int, destination_descriptor: int, name: str
) -> None:
    if _IS_WINDOWS:
        raise MdfEvidenceError("POSIX anonymous publication requested on Windows")
    if not name or Path(name).name != name:
        raise MdfEvidenceError("anonymous publication requires a leaf output name")
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
            os.fsencode(name),
            _POSIX_AT_EMPTY_PATH,
        )
        == 0
    ):
        return
    error_code = ctypes.get_errno()
    if error_code == errno.EEXIST:
        raise FileExistsError(error_code, "output already exists", name)
    unavailable_errors = {
        errno.EINVAL,
        errno.ENOENT,
        errno.ENOSYS,
        errno.EPERM,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }
    if error_code in unavailable_errors:
        raise MdfEvidenceError(
            "POSIX linkat(AT_EMPTY_PATH) publication is unavailable; refusing path fallback"
        )
    raise OSError(error_code, os.strerror(error_code), name)


def _validate_page_number(page_number: int) -> None:
    if page_number != DEFAULT_PAGE_NUMBER:
        raise MdfEvidenceError(
            f"only frozen zero-based page {DEFAULT_PAGE_NUMBER} is supported; found {page_number}"
        )


def _reject_windows_special_path(path: Path, *, label: str) -> None:
    raw = os.fspath(path)
    windows_form = raw.replace("/", "\\")
    lowered = windows_form.lower()
    if lowered.startswith(("\\\\?\\", "\\\\.\\", "\\??\\")):
        raise MdfEvidenceError(f"{label} must not use a Windows device namespace")
    if os.name != "nt":
        return

    drive, tail = ntpath.splitdrive(windows_form)
    if drive.startswith("\\\\"):
        raise MdfEvidenceError(f"{label} must not use a UNC path")
    if ":" in tail:
        raise MdfEvidenceError(f"{label} must not use an NTFS alternate data stream")
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
    for component in tail.split("\\"):
        if not component or component in (".", ".."):
            continue
        if component.endswith((" ", ".")):
            raise MdfEvidenceError(f"{label} contains a Windows trailing-dot/space alias")
        if component.split(".", 1)[0].upper() in reserved:
            raise MdfEvidenceError(f"{label} contains a reserved Windows device name")


def _reject_parent_segments(path: Path, *, label: str) -> None:
    if ".." in path.parts:
        raise MdfEvidenceError(f"{label} must not contain parent-directory segments")


def _is_reparse(stat_result: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    file_attributes = int(getattr(stat_result, "st_file_attributes", 0))
    return stat.S_ISLNK(stat_result.st_mode) or bool(
        reparse_flag and file_attributes & reparse_flag
    )


def _reject_reparse_components(path: Path, *, label: str) -> Path:
    _reject_windows_special_path(path, label=label)
    _reject_parent_segments(path, label=label)
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    try:
        for component in absolute.parts[1:]:
            current /= component
            component_stat = current.lstat()
            if _is_reparse(component_stat):
                raise MdfEvidenceError(f"{label} must not traverse a symlink or reparse point")
    except OSError as exc:
        raise MdfEvidenceError(f"cannot stat {label}: {exc}") from exc
    return absolute


def _canonical_source_path(source: Path) -> Path:
    source_absolute = _reject_reparse_components(source, label="source")
    expected_absolute = _reject_reparse_components(_EXPECTED_SOURCE_PATH, label="expected source")
    try:
        source_resolved = source_absolute.resolve(strict=True)
        expected_resolved = expected_absolute.resolve(strict=True)
    except OSError as exc:
        raise MdfEvidenceError(f"cannot resolve source: {exc}") from exc
    if os.path.normcase(os.fspath(source_resolved)) != os.path.normcase(
        os.fspath(expected_resolved)
    ):
        raise MdfEvidenceError(f"source must be the repository MDF at {SOURCE_LOGICAL_PATH}")
    return expected_resolved


def _open_bound_source_leaf(  # noqa: PLR0912 - platform-specific handle binding
    bound: BoundDirectory, name: str
) -> tuple[BinaryIO, os.stat_result]:
    try:
        before_path = _bound_lstat(bound, name)
    except OSError as exc:
        raise MdfEvidenceError(f"cannot inspect source before opening: {exc}") from exc
    if _is_reparse(before_path) or not stat.S_ISREG(before_path.st_mode):
        raise MdfEvidenceError("source must be a regular file, not a symlink or reparse point")
    if before_path.st_nlink != 1:
        raise MdfEvidenceError("repository source MDF must have exactly one hard link")

    descriptor: int | None = None
    windows_handle: int | None = None
    try:
        if bound.posix_descriptor is not None:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | _OS_O_NOFOLLOW
            )
            descriptor = os.open(name, flags, dir_fd=bound.posix_descriptor)
        else:
            source_path = bound.path / name
            windows_handle = _win_create_handle(
                source_path,
                desired_access=_WIN_GENERIC_READ,
                share_mode=_WIN_FILE_SHARE_READ,
                creation_disposition=_WIN_OPEN_EXISTING,
                flags_and_attributes=_WIN_FILE_FLAG_OPEN_REPARSE_POINT,
            )
            attributes = _win_handle_attributes(windows_handle).file_attributes
            if attributes & _WIN_FILE_ATTRIBUTE_REPARSE_POINT:
                raise MdfEvidenceError("source must not be a symlink or reparse point")
            if _win_final_path(windows_handle) != _win_expected_path(source_path):
                raise MdfEvidenceError("opened source final path is not the repository MDF")

            msvcrt = importlib.import_module("msvcrt")
            descriptor = msvcrt.open_osfhandle(
                windows_handle, os.O_RDONLY | getattr(os, "O_BINARY", 0)
            )
            windows_handle = None

        if descriptor is None:
            raise MdfEvidenceError("source file descriptor is unavailable")
        opened_stat = os.fstat(descriptor)
        after_path = _bound_lstat(bound, name)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or not _same_identity(before_path, opened_stat)
            or not _same_identity(opened_stat, after_path)
            or _is_reparse(after_path)
            or opened_stat.st_nlink != 1
            or _stat_signature(before_path) != _stat_signature(opened_stat)
            or _stat_signature(opened_stat) != _stat_signature(after_path)
        ):
            raise MdfEvidenceError("source identity changed while opening the repository MDF")
        stream = cast(BinaryIO, os.fdopen(descriptor, "rb"))
        descriptor = None
        return stream, opened_stat
    except OSError as exc:
        raise MdfEvidenceError(f"cannot open source read-only: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if windows_handle is not None:
            _win_close_handle(windows_handle)


def _read_bound_regular_file_snapshot(
    path: Path,
    *,
    label: str,
    require_single_link: bool = True,
    maximum_bytes: int = MAX_SUPPORTING_EVIDENCE_BYTES,
) -> tuple[bytes, str, os.stat_result]:
    """Read one regular file through a retained, no-follow handle.

    Both the directory and leaf identities remain bound for the whole read.  The
    before/after signatures deliberately include size, mtime and ctime so a same-size
    in-place rewrite cannot be accepted merely because the final digest happens to be
    checked after a path-based reopen.
    """

    _reject_windows_special_path(path, label=label)
    _reject_parent_segments(path, label=label)
    absolute = Path(os.path.abspath(path))
    with _bound_directory(absolute.parent, label=f"{label} parent") as bound:
        _validate_bound_directory_path(bound, label=f"{label} parent")
        stream, opened = _open_bound_regular_leaf(
            bound,
            absolute.name,
            label=label,
            require_single_link=require_single_link,
        )
        try:
            if opened.st_size < 0 or opened.st_size > maximum_bytes:
                raise MdfEvidenceError(
                    f"{label} exceeds the {maximum_bytes}-byte supporting evidence limit"
                )
            data = stream.read(maximum_bytes + 1)
            if len(data) > maximum_bytes:
                raise MdfEvidenceError(
                    f"{label} exceeds the {maximum_bytes}-byte supporting evidence limit"
                )
            after = os.fstat(stream.fileno())
            after_path = _bound_lstat(bound, absolute.name)
            _validate_bound_directory_path(bound, label=f"{label} parent")
            if (
                _stat_signature(opened) != _stat_signature(after)
                or _is_reparse(after_path)
                or not _same_identity(after, after_path)
                or _stat_signature(after) != _stat_signature(after_path)
                or len(data) != opened.st_size
            ):
                raise MdfEvidenceError(
                    f"{label} identity, size, mtime or ctime changed during read"
                )
            return data, hashlib.sha256(data).hexdigest().upper(), after
        except OSError as exc:
            raise MdfEvidenceError(f"cannot read {label}: {exc}") from exc
        finally:
            stream.close()


def _read_bound_regular_file(
    path: Path,
    *,
    label: str,
    require_single_link: bool = True,
    maximum_bytes: int = MAX_SUPPORTING_EVIDENCE_BYTES,
) -> tuple[bytes, str]:
    data, digest, _stat = _read_bound_regular_file_snapshot(
        path,
        label=label,
        require_single_link=require_single_link,
        maximum_bytes=maximum_bytes,
    )
    return data, digest


def _open_bound_regular_leaf(  # noqa: PLR0912 - platform-specific handle binding
    bound: BoundDirectory,
    name: str,
    *,
    label: str,
    require_single_link: bool,
) -> tuple[BinaryIO, os.stat_result]:
    try:
        before_path = _bound_lstat(bound, name)
    except OSError as exc:
        raise MdfEvidenceError(f"cannot inspect {label} before opening: {exc}") from exc
    if _is_reparse(before_path) or not stat.S_ISREG(before_path.st_mode):
        raise MdfEvidenceError(f"{label} must be a regular file, not a symlink or reparse point")
    if require_single_link and before_path.st_nlink != 1:
        raise MdfEvidenceError(f"{label} must have exactly one hard link")

    descriptor: int | None = None
    windows_handle: int | None = None
    try:
        if bound.posix_descriptor is not None:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | _OS_O_NOFOLLOW
            )
            descriptor = os.open(name, flags, dir_fd=bound.posix_descriptor)
        else:
            leaf_path = bound.path / name
            windows_handle = _win_create_handle(
                leaf_path,
                desired_access=_WIN_GENERIC_READ,
                share_mode=_WIN_FILE_SHARE_READ,
                creation_disposition=_WIN_OPEN_EXISTING,
                flags_and_attributes=_WIN_FILE_FLAG_OPEN_REPARSE_POINT,
            )
            attributes = _win_handle_attributes(windows_handle).file_attributes
            if attributes & _WIN_FILE_ATTRIBUTE_REPARSE_POINT:
                raise MdfEvidenceError(
                    f"{label} must be a regular file, not a symlink or reparse point"
                )
            if _win_final_path(windows_handle) != _win_expected_path(leaf_path):
                raise MdfEvidenceError(f"opened {label} final path changed while binding")
            msvcrt = importlib.import_module("msvcrt")
            descriptor = msvcrt.open_osfhandle(
                windows_handle, os.O_RDONLY | getattr(os, "O_BINARY", 0)
            )
            windows_handle = None

        if descriptor is None:
            raise MdfEvidenceError(f"{label} file descriptor is unavailable")
        opened = os.fstat(descriptor)
        after_open_path = _bound_lstat(bound, name)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(after_open_path)
            or not _same_identity(before_path, opened)
            or not _same_identity(opened, after_open_path)
            or (require_single_link and opened.st_nlink != 1)
            or _stat_signature(before_path) != _stat_signature(opened)
            or _stat_signature(opened) != _stat_signature(after_open_path)
        ):
            raise MdfEvidenceError(f"{label} identity or metadata changed while opening")
        stream = cast(BinaryIO, os.fdopen(descriptor, "rb"))
        descriptor = None
        return stream, opened
    except OSError as exc:
        raise MdfEvidenceError(f"cannot read {label}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if windows_handle is not None:
            _win_close_handle(windows_handle)


_PARSER_SOURCE_PATH = Path(__file__).resolve(strict=True)
_REPO_ROOT = _PARSER_SOURCE_PATH.parents[1]
_EXPECTED_SOURCE_PATH = _REPO_ROOT / SOURCE_LOGICAL_PATH
_EXPECTED_SOURCE_PARENT_STAT_AT_LOAD = _EXPECTED_SOURCE_PATH.parent.stat(follow_symlinks=False)
_EXPECTED_SOURCE_STAT_AT_LOAD = _EXPECTED_SOURCE_PATH.stat(follow_symlinks=False)
(
    _PARSER_SOURCE_BYTES_AT_LOAD,
    _PARSER_SOURCE_SHA256_AT_LOAD,
    _PARSER_SOURCE_STAT_AT_LOAD,
) = _read_bound_regular_file_snapshot(_PARSER_SOURCE_PATH, label="extractor source")


def _verify_parser_source_unchanged() -> None:
    try:
        current_path = Path(__file__).resolve(strict=True)
        current_bytes, current_hash, current_stat = _read_bound_regular_file_snapshot(
            current_path,
            label="extractor source",
        )
    except OSError as exc:
        raise MdfEvidenceError(f"cannot revalidate extractor source: {exc}") from exc
    if current_path != _PARSER_SOURCE_PATH:
        raise MdfEvidenceError("extractor source path changed after module load")
    if _stat_signature(current_stat) != _stat_signature(_PARSER_SOURCE_STAT_AT_LOAD):
        raise MdfEvidenceError("extractor source identity or metadata changed after module load")
    if (
        current_hash != _PARSER_SOURCE_SHA256_AT_LOAD
        or current_bytes != _PARSER_SOURCE_BYTES_AT_LOAD
    ):
        raise MdfEvidenceError("extractor source bytes changed after module load")


@contextmanager
def _open_bound_source(
    source: Path,
) -> Iterator[tuple[BinaryIO, os.stat_result, BoundDirectory, str]]:
    expected = _canonical_source_path(source)
    with _bound_directory(expected.parent, label="source parent") as bound:
        if not _same_identity(bound.identity, _EXPECTED_SOURCE_PARENT_STAT_AT_LOAD):
            raise MdfEvidenceError("bound source parent does not match its module-load identity")
        _validate_bound_directory_path(bound, label="source parent")
        stream, opened_stat = _open_bound_source_leaf(bound, expected.name)
        try:
            if not _same_identity(opened_stat, _EXPECTED_SOURCE_STAT_AT_LOAD):
                raise MdfEvidenceError("opened source does not match its module-load MDF identity")
            yield stream, opened_stat, bound, expected.name
        finally:
            stream.close()


def _validate_bound_source_after(
    stream: BinaryIO,
    before: os.stat_result,
    bound: BoundDirectory,
    name: str,
) -> None:
    try:
        after = os.fstat(stream.fileno())
        path_after = _bound_lstat(bound, name)
    except OSError as exc:
        raise MdfEvidenceError(f"cannot revalidate source: {exc}") from exc
    if _stat_signature(before) != _stat_signature(after):
        raise MdfEvidenceError("source identity or metadata changed during extraction")
    if (
        _is_reparse(path_after)
        or not _same_identity(after, path_after)
        or _stat_signature(after) != _stat_signature(path_after)
    ):
        raise MdfEvidenceError("repository source path changed during extraction")
    _validate_bound_directory_path(bound, label="source parent")


def _read_frozen_source(source: Path, page_number: int) -> tuple[bytes, str, int]:
    _validate_page_number(page_number)
    page_start = page_number * PAGE_SIZE
    page_end = page_start + PAGE_SIZE
    digest = hashlib.sha256()
    page_parts: list[bytes] = []
    position = 0

    try:
        with _open_bound_source(source) as (stream, before, bound, name):
            if before.st_size != EXPECTED_SOURCE_SIZE:
                raise MdfEvidenceError(
                    f"source size mismatch: expected {EXPECTED_SOURCE_SIZE}, found {before.st_size}"
                )
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                chunk_end = position + len(chunk)
                overlap_start = max(position, page_start)
                overlap_end = min(chunk_end, page_end)
                if overlap_start < overlap_end:
                    page_parts.append(chunk[overlap_start - position : overlap_end - position])
                position = chunk_end
            _validate_bound_source_after(stream, before, bound, name)
    except OSError as exc:
        raise MdfEvidenceError(f"cannot read source: {exc}") from exc

    if position != EXPECTED_SOURCE_SIZE:
        raise MdfEvidenceError(
            f"source read length changed: expected {EXPECTED_SOURCE_SIZE}, found {position}"
        )
    source_hash = digest.hexdigest().upper()
    if source_hash != EXPECTED_SOURCE_SHA256:
        raise MdfEvidenceError(f"source SHA-256 mismatch: {source_hash}")
    page = b"".join(page_parts)
    if len(page) != PAGE_SIZE:
        raise MdfEvidenceError(f"page {page_number} is truncated: {len(page)} bytes")
    return page, source_hash, before.st_size


def read_page(source: Path, page_number: int = DEFAULT_PAGE_NUMBER) -> tuple[bytes, int]:
    _validate_page_number(page_number)
    file_offset = page_number * PAGE_SIZE
    try:
        with _open_bound_source(source) as (stream, before, bound, name):
            stream.seek(file_offset)
            page = stream.read(PAGE_SIZE)
            _validate_bound_source_after(stream, before, bound, name)
    except OSError as exc:
        raise MdfEvidenceError(f"cannot read source page: {exc}") from exc
    if len(page) != PAGE_SIZE:
        raise MdfEvidenceError(f"page {page_number} is truncated: {len(page)} bytes")
    return page, file_offset


def _record_offsets(page: bytes) -> tuple[list[int], int]:
    slot_count = _u16(page, 22)
    free_data = _u16(page, 30)
    if slot_count != EXPECTED_SLOT_COUNT:
        raise MdfEvidenceError(f"expected {EXPECTED_SLOT_COUNT} slots, found {slot_count}")
    offsets = [_u16(page, PAGE_SIZE - 2 * (slot + 1)) for slot in range(slot_count)]
    if offsets != sorted(offsets) or len(set(offsets)) != slot_count:
        raise MdfEvidenceError("slot offsets must be unique and strictly ordered")
    if offsets[0] < PAGE_HEADER_SIZE or not offsets[-1] < free_data <= PAGE_SIZE - slot_count * 2:
        raise MdfEvidenceError("slot/free-data boundary is invalid")
    return offsets, free_data


def _validate_page_header(page: bytes, *, page_number: int) -> None:
    if page[0] != EXPECTED_PAGE_HEADER_VERSION:
        raise MdfEvidenceError(f"unexpected page-header version {page[0]}")
    if page[1] != EXPECTED_PAGE_TYPE:
        raise MdfEvidenceError(f"unexpected SQL Server page type {page[1]}")
    page_id = _u32(page, 32)
    file_id = _u16(page, 36)
    if page_id != page_number:
        raise MdfEvidenceError(
            f"page-header page ID mismatch: expected {page_number}, found {page_id}"
        )
    if file_id != EXPECTED_DATABASE_FILE_ID:
        raise MdfEvidenceError(
            f"page-header file ID mismatch: expected {EXPECTED_DATABASE_FILE_ID}, found {file_id}"
        )


def _null_column_indexes(bitmap: bytes, column_count: int) -> tuple[int, ...]:
    return tuple(
        column_index
        for column_index in range(column_count)
        if bitmap[column_index // 8] & (1 << (column_index % 8))
    )


def _decode_text(field: bytes, field_name: str) -> str:
    if len(field) % 2:
        raise MdfEvidenceError(f"{field_name}: UTF-16LE field has odd byte length")
    try:
        value = field.decode("utf-16le", errors="strict")
    except UnicodeDecodeError as exc:
        raise MdfEvidenceError(f"{field_name}: invalid UTF-16LE") from exc
    if any(ord(character) < CONTROL_CHARACTER_LIMIT for character in value):
        raise MdfEvidenceError(f"{field_name}: control character is not allowed")
    return value


def _decode_variable_fields(
    record: bytes, fixed_length: int, column_count: int
) -> tuple[dict[str, str | None], RecordLayout]:
    null_bitmap_start = fixed_length + 2
    null_bitmap_len = math.ceil(column_count / 8)
    null_bitmap_end = null_bitmap_start + null_bitmap_len
    variable_count = _u16(record, null_bitmap_end)
    if variable_count not in EXPECTED_VARIABLE_COLUMN_COUNTS:
        raise MdfEvidenceError(f"unexpected variable-column count {variable_count}")
    ends_start = null_bitmap_end + 2
    ends_end = ends_start + 2 * variable_count
    if ends_end > len(record):
        raise MdfEvidenceError("variable-column offset array is outside the record")
    end_offsets = tuple(_u16(record, ends_start + 2 * index) for index in range(variable_count))
    if any(end & 0x8000 for end in end_offsets):
        raise MdfEvidenceError(
            "flagged variable-column offsets are unsupported by this evidence parser"
        )
    data_start = ends_end
    if any(end < data_start or end > len(record) for end in end_offsets):
        raise MdfEvidenceError("variable-column end offset is outside the record")
    if tuple(sorted(end_offsets)) != end_offsets:
        raise MdfEvidenceError("variable-column end offsets are not ordered")
    if not end_offsets or end_offsets[-1] != len(record):
        raise MdfEvidenceError("record boundary does not equal the final variable-column offset")
    values: list[str | None] = []
    cursor = data_start
    for field_name, end in zip(VARIABLE_FIELD_NAMES, end_offsets, strict=False):
        values.append(_decode_text(record[cursor:end], field_name))
        cursor = end
    values.extend([None] * (len(VARIABLE_FIELD_NAMES) - len(values)))
    fields = dict(zip(VARIABLE_FIELD_NAMES, values, strict=True))
    layout = RecordLayout(
        slot_index=-1,
        page_offset=-1,
        record_length=len(record),
        fixed_length_value=fixed_length,
        column_count=column_count,
        null_bitmap_hex=record[null_bitmap_start:null_bitmap_end].hex(),
        null_column_indexes=_null_column_indexes(
            record[null_bitmap_start:null_bitmap_end], column_count
        ),
        variable_column_count=variable_count,
        variable_end_offsets=end_offsets,
    )
    return fields, layout


def _field_claim(
    *,
    claim_id: str,
    value: Any,
    supports: list[str],
    does_not_test: list[str],
    refutes: list[str] | None = None,
    evidence_grade: str = "source_candidate",
) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "value": value,
        "evidence_grade": evidence_grade,
        "evidence_refs": ["MDF_PAGE_258_PHYSICAL_ROW", "LEGACY_CSHARP_FIELD_MAPPING"],
        "supports": supports,
        "refutes": refutes or [],
        "does_not_test": does_not_test,
    }


def _candidate_field_evidence(candidate: dict[str, Any]) -> dict[str, Any]:
    candidate_id = cast(str, candidate["candidate_id"])
    current_map_unknown = ["CURRENT_DEVICE_IDENTITY", "CURRENT_FIRMWARE_POINT_MAP"]
    field_specs: tuple[tuple[str, Any, list[str], list[str]], ...] = (
        (
            "/model_candidate",
            candidate["model_candidate"],
            ["LEGACY_MODEL_TEXT_PRESENT"],
            current_map_unknown,
        ),
        (
            "/legacy_point_id",
            candidate["legacy_point_id"],
            ["LEGACY_POINT_ID_PRESENT"],
            current_map_unknown,
        ),
        (
            "/fun_code",
            candidate["fun_code"],
            ["LEGACY_FUNCTION_CODE_PRESENT"],
            current_map_unknown,
        ),
        (
            "/point_number",
            candidate["point_number"],
            ["LEGACY_POINT_NUMBER_PRESENT"],
            current_map_unknown,
        ),
        (
            "/legacy_r_bit_candidate",
            candidate["legacy_r_bit_candidate"],
            ["LEGACY_RBIT_PRESENT"],
            [*current_map_unknown, "CURRENT_FC1_ADDRESS_TRANSLATION"],
        ),
        (
            "/point_name",
            candidate["point_name"],
            ["LEGACY_POINT_NAME_PRESENT"],
            [*current_map_unknown, "CURRENT_POINT_SEMANTICS"],
        ),
        (
            "/user_point_name",
            candidate["user_point_name"],
            ["LEGACY_USER_POINT_NAME_PRESENT"],
            [*current_map_unknown, "CURRENT_POINT_SEMANTICS"],
        ),
        (
            "/legacy_value_type",
            candidate["legacy_value_type"],
            ["LEGACY_VALUE_TYPE_TEXT_PRESENT"],
            [*current_map_unknown, "CURRENT_DEVICE_ENCODING"],
        ),
        (
            "/unit_original",
            candidate["unit_original"],
            (
                ["LEGACY_UNIT_FIELD_OBSERVED"]
                if candidate["unit_storage_status"]
                != "fifth_variable_field_not_stored_interpretation_unresolved"
                else ["LEGACY_UNIT_FIELD_NOT_STORED"]
            ),
            [*current_map_unknown, "CURRENT_ENGINEERING_UNIT"],
        ),
        *(
            (
                f"/legacy_scaling/{field_name}",
                candidate["legacy_scaling"][field_name],
                ["LEGACY_SCALING_VALUE_PRESENT"],
                [*current_map_unknown, "CURRENT_SCALING_FORMULA", "PHYSICAL_CALIBRATION"],
            )
            for field_name in (
                "point_ratio",
                "point_offset",
                "user_ratio",
                "user_point_offset",
                "min_value",
                "max_value",
            )
        ),
        (
            "/legacy_runtime/show",
            candidate["legacy_runtime"]["show"],
            ["LEGACY_RUNTIME_VALUE_PRESENT"],
            [*current_map_unknown, "CURRENT_UI_VISIBILITY"],
        ),
        (
            "/legacy_runtime/update_interval_seconds",
            candidate["legacy_runtime"]["update_interval_seconds"],
            ["LEGACY_RUNTIME_VALUE_PRESENT"],
            [*current_map_unknown, "CURRENT_POLL_INTERVAL"],
        ),
    )
    claims: dict[str, Any] = {}
    for field_path, value, supports, does_not_test in field_specs:
        claim_slug = field_path.strip("/").replace("/", "-").replace("_", "-")
        claims[field_path] = _field_claim(
            claim_id=f"{candidate_id}:{claim_slug}",
            value=value,
            supports=supports,
            does_not_test=does_not_test,
            evidence_grade=(
                "unresolved"
                if field_path == "/unit_original"
                and candidate["unit_storage_status"]
                == "fifth_variable_field_not_stored_interpretation_unresolved"
                else "source_candidate"
            ),
            refutes=(
                ["MECHANICAL_FC1_RBIT_TRANSLATION_TO_CURRENT_API"]
                if field_path == "/legacy_r_bit_candidate"
                and candidate["fun_code"] == FC_READ_COILS
                else []
            ),
        )
    return claims


def parse_page(
    page: bytes,
    *,
    page_number: int = DEFAULT_PAGE_NUMBER,
    page_file_offset: int = EXPECTED_PAGE_FILE_OFFSET,
) -> list[dict[str, Any]]:
    _validate_page_number(page_number)
    if page_file_offset != EXPECTED_PAGE_FILE_OFFSET:
        raise MdfEvidenceError(
            f"page file offset mismatch: expected {EXPECTED_PAGE_FILE_OFFSET}, found {page_file_offset}"
        )
    if len(page) != PAGE_SIZE:
        raise MdfEvidenceError(f"expected an {PAGE_SIZE}-byte page")
    page_hash = hashlib.sha256(page).hexdigest().upper()
    if page_hash != EXPECTED_PAGE_SHA256:
        raise MdfEvidenceError(f"page SHA-256 mismatch: {page_hash}")
    _validate_page_header(page, page_number=page_number)
    offsets, free_data = _record_offsets(page)
    candidates: list[dict[str, Any]] = []
    for slot_index, page_offset in enumerate(offsets):
        record_end = offsets[slot_index + 1] if slot_index + 1 < len(offsets) else free_data
        record = page[page_offset:record_end]
        if len(record) < EXPECTED_FIXED_LENGTH + 7:
            raise MdfEvidenceError(f"slot {slot_index}: record is too short: {len(record)} bytes")
        if record[:2] != EXPECTED_RECORD_STATUS:
            raise MdfEvidenceError(
                f"slot {slot_index}: unsupported record status {record[:2].hex()}"
            )
        fixed_length = _u16(record, 2)
        if fixed_length != EXPECTED_FIXED_LENGTH:
            raise MdfEvidenceError(f"slot {slot_index}: fixed length {fixed_length}")
        column_count = _u16(record, fixed_length)
        if column_count != EXPECTED_COLUMN_COUNT:
            raise MdfEvidenceError(f"slot {slot_index}: column count {column_count}")
        fields, layout_base = _decode_variable_fields(record, fixed_length, column_count)
        layout = RecordLayout(
            slot_index=slot_index,
            page_offset=page_offset,
            record_length=layout_base.record_length,
            fixed_length_value=layout_base.fixed_length_value,
            column_count=layout_base.column_count,
            null_bitmap_hex=layout_base.null_bitmap_hex,
            null_column_indexes=layout_base.null_column_indexes,
            variable_column_count=layout_base.variable_column_count,
            variable_end_offsets=layout_base.variable_end_offsets,
        )
        point_id = _i32(record, 4)
        fun_code = _i32(record, 12)
        point_number = _i32(record, 16)
        legacy_r_bit = _i32(record, 20)
        model = fields["DevType"]
        if not model:
            raise MdfEvidenceError(f"slot {slot_index}: missing model candidate")
        if model not in EXPECTED_MODEL_COUNTS:
            raise MdfEvidenceError(f"slot {slot_index}: unsupported model candidate {model!r}")
        if fun_code not in EXPECTED_FUNCTION_CODES:
            raise MdfEvidenceError(f"slot {slot_index}: unsupported function code {fun_code}")
        blockers = (
            ["GW_SIGNED_16_DECODE_UNSUPPORTED", "API_VALUE_TYPE_S16_UNSUPPORTED"]
            if fun_code == FC_READ_HOLDING_REGISTERS
            else ["API_VALUE_TYPE_S16_UNSUPPORTED", "FC1_POINT_NUMBER_RBIT_SEMANTICS_UNRESOLVED"]
        )
        is_register_candidate = fun_code == FC_READ_HOLDING_REGISTERS
        candidate: dict[str, Any] = {
            "candidate_id": f"{model}-{point_id:03d}",
            "model_candidate": model,
            "legacy_point_id": point_id,
            "fun_code": fun_code,
            "point_number": point_number,
            "legacy_r_bit_candidate": legacy_r_bit,
            "point_name": fields["PointName"],
            "user_point_name": fields["UserPointName"],
            "legacy_value_type": fields["ValueType"],
            "unit_original": fields["PointUint"],
            "unit_storage_status": (
                "stored_empty"
                if layout.variable_column_count == EXPECTED_VARIABLE_FIELD_COUNT
                and fields["PointUint"] == ""
                else "stored_value"
                if layout.variable_column_count == EXPECTED_VARIABLE_FIELD_COUNT
                else "fifth_variable_field_not_stored_interpretation_unresolved"
            ),
            "encoding_candidate": {
                "register_width": 1 if is_register_candidate else None,
                "signedness": (
                    "signed_16_candidate"
                    if is_register_candidate
                    else "unresolved_fc1_s16_contract_conflict"
                ),
                "byte_order": "unresolved" if is_register_candidate else "not_applicable_coil",
                "word_order": (
                    "not_applicable_single_register_candidate"
                    if is_register_candidate
                    else "not_applicable_coil"
                ),
                "raw_domain": [-32768, 32767] if is_register_candidate else None,
                "status": "source_candidate" if is_register_candidate else "ambiguous",
            },
            "dev_addr": None,
            "dev_addr_status": "unresolved_not_mapped_from_physical_row",
            "legacy_scaling": {
                "point_ratio": _f64(record, 28),
                "point_offset": _f64(record, 44),
                "user_ratio": _f64(record, 36),
                "user_point_offset": _f64(record, 52),
                "min_value": _f64(record, 60),
                "max_value": _f64(record, 68),
            },
            "legacy_runtime": {
                "show": _i32(record, 24),
                "update_interval_seconds": _i32(record, 80),
            },
            "source_location": {
                "mdf_zero_based_page": page_number,
                "slot_index": layout.slot_index,
                "page_record_offset": layout.page_offset,
                "record_length": layout.record_length,
                "record_sha256": hashlib.sha256(record).hexdigest().upper(),
                "file_absolute_offset": page_file_offset + layout.page_offset,
            },
            "record_layout": {
                "fixed_length_value": layout.fixed_length_value,
                "record_status_hex": record[:2].hex().upper(),
                "column_count": layout.column_count,
                "null_bitmap_hex": layout.null_bitmap_hex.upper(),
                "null_column_indexes": list(layout.null_column_indexes),
                "variable_column_count": layout.variable_column_count,
                "variable_end_offsets": list(layout.variable_end_offsets),
                "unresolved_fixed_bytes": {
                    "record_offset_8_length_4_hex": record[8:12].hex().upper(),
                    "record_offset_76_length_4_hex": record[76:80].hex().upper(),
                    "record_offset_84_length_8_hex": record[84:92].hex().upper(),
                },
            },
            "identity_status": "model_candidate",
            "semantic_status": "source_candidate",
            "encoding_status": "source_candidate" if is_register_candidate else "ambiguous",
            "unit_status": "source_candidate" if fields["PointUint"] else "unresolved",
            "calibration_status": "unresolved",
            "implementation_status": "unsupported",
            "direct_import_allowed": False,
            "implementation_supported": False,
            "implementation_blockers": blockers,
            "prohibited_reason": "identity, semantics, encoding and calibration are not resolved",
        }
        candidate["field_evidence"] = _candidate_field_evidence(candidate)
        candidates.append(candidate)
    _validate_candidates(candidates)
    return candidates


def _validate_candidates(candidates: list[dict[str, Any]]) -> None:
    if len(candidates) != EXPECTED_SLOT_COUNT:
        raise MdfEvidenceError(f"candidate count mismatch: {len(candidates)}")
    candidate_ids = [candidate["candidate_id"] for candidate in candidates]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise MdfEvidenceError("candidate IDs must be unique")
    model_counts = {
        model: sum(candidate["model_candidate"] == model for candidate in candidates)
        for model in EXPECTED_MODEL_COUNTS
    }
    if model_counts != EXPECTED_MODEL_COUNTS:
        raise MdfEvidenceError(f"model counts mismatch: {model_counts}")

    for candidate in candidates:
        _validate_candidate(candidate)


def _validate_candidate(candidate: dict[str, Any]) -> None:
    candidate_id = candidate["candidate_id"]
    if candidate["legacy_value_type"] != "s16":
        raise MdfEvidenceError(f"{candidate_id}: expected legacy value type s16")
    if not isinstance(candidate["point_name"], str) or not candidate["point_name"]:
        raise MdfEvidenceError(f"{candidate_id}: missing point name")
    if not isinstance(candidate["user_point_name"], str) or not candidate["user_point_name"]:
        raise MdfEvidenceError(f"{candidate_id}: missing user point name")
    if not 0 <= candidate["point_number"] <= MAX_POINT_NUMBER:
        raise MdfEvidenceError(f"{candidate_id}: point number is outside uint16")
    legacy_r_bit = candidate["legacy_r_bit_candidate"]
    if candidate["fun_code"] == FC_READ_HOLDING_REGISTERS and legacy_r_bit != -1:
        raise MdfEvidenceError(f"{candidate_id}: FC3 legacy RBit must be -1")
    if candidate["fun_code"] == FC_READ_COILS and legacy_r_bit not in (0, 1):
        raise MdfEvidenceError(f"{candidate_id}: FC1 legacy RBit must be 0 or 1")
    scaling = candidate["legacy_scaling"]
    if not all(math.isfinite(value) for value in scaling.values()):
        raise MdfEvidenceError(f"{candidate_id}: non-finite scaling value")
    if scaling["min_value"] > scaling["max_value"]:
        raise MdfEvidenceError(f"{candidate_id}: minimum exceeds maximum")
    runtime = candidate["legacy_runtime"]
    if runtime["show"] not in (0, 1):
        raise MdfEvidenceError(f"{candidate_id}: invalid legacy show flag")
    if not 1 <= runtime["update_interval_seconds"] <= MAX_UPDATE_INTERVAL_SECONDS:
        raise MdfEvidenceError(f"{candidate_id}: invalid update interval")


def _verified_b06_evidence_files(repo_root: Path) -> dict[str, dict[str, str]]:
    verified: dict[str, dict[str, str]] = {}
    for evidence_name, (relative_path, expected_hash) in B06_EVIDENCE_FILES.items():
        _data, actual_hash = _read_bound_regular_file(
            repo_root / relative_path,
            label=f"B-06 {evidence_name} evidence",
        )
        if actual_hash != expected_hash:
            raise MdfEvidenceError(f"B-06 {evidence_name} evidence SHA-256 mismatch: {actual_hash}")
        verified[evidence_name] = {"path": relative_path, "sha256": actual_hash}
    return verified


def _verified_evidence_source_files(repo_root: Path) -> dict[str, dict[str, Any]]:
    verified: dict[str, dict[str, Any]] = {}
    for evidence_name, specification in EVIDENCE_SOURCE_FILES.items():
        relative_path = cast(str, specification["path"])
        expected_hash = cast(str, specification["sha256"])
        source_path = repo_root / relative_path
        _data, actual_hash = _read_bound_regular_file(
            source_path,
            label=f"{evidence_name} evidence source",
        )
        if actual_hash != expected_hash:
            raise MdfEvidenceError(
                f"{evidence_name} evidence source SHA-256 mismatch: {actual_hash}"
            )
        verified[evidence_name] = {
            "path": relative_path,
            "sha256": actual_hash,
            "evidence_ids": list(cast(list[str], specification["evidence_ids"])),
            "locators": [
                dict(locator) for locator in cast(list[dict[str, Any]], specification["locators"])
            ],
        }
    return verified


def extract(source: Path, *, page_number: int = DEFAULT_PAGE_NUMBER) -> dict[str, Any]:
    _verify_parser_source_unchanged()
    page, source_hash, source_size = _read_frozen_source(source, page_number)
    page_file_offset = EXPECTED_PAGE_FILE_OFFSET
    candidates = parse_page(page, page_number=page_number, page_file_offset=page_file_offset)
    page_hash = hashlib.sha256(page).hexdigest().upper()
    offsets, free_data = _record_offsets(page)
    b06_ranges: list[dict[str, Any]] = [
        {
            "evidence_id": "B06_FC3_RANGE_0_5_READABLE",
            "fun_code": 3,
            "start": 0,
            "quantity": 6,
            "raw_registers": [3, 0, 0, 0, 0, 0],
            "supports_range_only": True,
            "does_not_test": ["MODEL", "POINT_NAME", "SIGNEDNESS", "UNIT", "SCALING"],
        },
        {
            "evidence_id": "B06_FC3_RANGE_27_35_READABLE",
            "fun_code": 3,
            "start": 27,
            "quantity": 9,
            "raw_registers": [3, 0, 0, 0, 0, 0, 0, 0, 0],
            "supports_range_only": True,
            "does_not_test": ["MODEL", "POINT_NAME", "SIGNEDNESS", "UNIT", "SCALING"],
        },
    ]
    for candidate in candidates:
        candidate["b06_observations"] = [
            evidence["evidence_id"]
            for evidence in b06_ranges
            if candidate["fun_code"] == evidence["fun_code"]
            and evidence["start"]
            <= candidate["point_number"]
            < evidence["start"] + evidence["quantity"]
        ]
    repo_root = _REPO_ROOT
    artifact = {
        "schema_version": "1.3",
        "artifact_id": "b08-legacy-point-candidates-20260827",
        "generated_on": "2026-08-27",
        "purpose": "只读固化旧 MDF 物理页候选；不是生产点表，不授权导入、轮询或控制。",
        "deployable": False,
        "source": {
            "path": SOURCE_LOGICAL_PATH,
            "size_bytes": source_size,
            "expected_size_bytes": EXPECTED_SOURCE_SIZE,
            "sha256": source_hash,
            "expected_sha256": EXPECTED_SOURCE_SHA256,
            "page_size_bytes": PAGE_SIZE,
            "zero_based_page_number": page_number,
            "page_file_offset": page_file_offset,
            "page_sha256": page_hash,
            "expected_page_sha256": EXPECTED_PAGE_SHA256,
            "slot_count": len(offsets),
            "free_data_offset": free_data,
            "raw_page_header": {
                "page_offset": 0,
                "length_bytes": PAGE_HEADER_SIZE,
                "hex": page[:PAGE_HEADER_SIZE].hex().upper(),
                "observations": {
                    "header_version_u8_at_0": page[0],
                    "page_type_u8_at_1": page[1],
                    "slot_count_u16_le_at_22": _u16(page, 22),
                    "free_data_offset_u16_le_at_30": _u16(page, 30),
                    "page_id_u32_le_at_32": _u32(page, 32),
                    "file_id_u16_le_at_36": _u16(page, 36),
                },
            },
            "sql_table_identity": "UNRESOLVED",
        },
        "parser_contract": {
            "tool": PARSER_LOGICAL_PATH,
            "algorithm_version": ALGORITHM_VERSION,
            "extractor_source_sha256": _PARSER_SOURCE_SHA256_AT_LOAD,
            "mode": "read_only_identity_bound_authenticated_mdf_page_258_only",
            "source_binding": (
                "repository logical path, no symlink/reparse components, and opened-handle identity"
            ),
            "output_publish": (
                "same-directory fsynced temporary file followed by atomic no-replace publish"
            ),
            "record_status_hex": EXPECTED_RECORD_STATUS.hex(),
            "fixed_length_value": EXPECTED_FIXED_LENGTH,
            "fixed_area_end_formula": "record_page_offset + fixed_length_value",
            "column_count": EXPECTED_COLUMN_COUNT,
            "variable_field_order_candidate": list(VARIABLE_FIELD_NAMES),
            "variable_offset_flags": "rejected_as_unresolved",
            "record_boundary_rule": "final variable end offset must equal next physical row or freeData",
            "missing_fifth_variable_field_interpretation": "UNRESOLVED without SQL catalog",
        },
        "fixed_field_layout_candidate": [
            {
                "name": "PointID",
                "record_offset": 4,
                "byte_length": 4,
                "type": "int32_le",
                "confidence": "high",
            },
            {
                "name": "unknown_fixed_8",
                "record_offset": 8,
                "byte_length": 4,
                "type": "unresolved",
                "confidence": "unknown",
            },
            {
                "name": "FunCode",
                "record_offset": 12,
                "byte_length": 4,
                "type": "int32_le",
                "confidence": "high",
            },
            {
                "name": "PointNumber",
                "record_offset": 16,
                "byte_length": 4,
                "type": "int32_le",
                "confidence": "high",
            },
            {
                "name": "RBit",
                "record_offset": 20,
                "byte_length": 4,
                "type": "int32_le",
                "confidence": "high",
            },
            {
                "name": "Show",
                "record_offset": 24,
                "byte_length": 4,
                "type": "int32_le",
                "confidence": "high",
            },
            *[
                {
                    "name": name,
                    "record_offset": offset,
                    "byte_length": 8,
                    "type": "float64_le",
                    "confidence": "high",
                }
                for name, offset in (
                    ("PointRatio", 28),
                    ("UserRatio", 36),
                    ("PointOffset", 44),
                    ("UserPointOffset", 52),
                    ("MinValue", 60),
                    ("MaxValue", 68),
                )
            ],
            {
                "name": "unknown_fixed_76",
                "record_offset": 76,
                "byte_length": 4,
                "type": "unresolved",
                "confidence": "unknown",
            },
            {
                "name": "UpdateInterval",
                "record_offset": 80,
                "byte_length": 4,
                "type": "int32_le",
                "confidence": "high",
            },
            {
                "name": "unknown_fixed_tail",
                "record_offset": 84,
                "byte_length": 8,
                "type": "unresolved",
                "confidence": "unknown",
            },
        ],
        "evidence_sources": _verified_evidence_source_files(repo_root),
        "b06_authenticated_range_evidence": {
            "run_id": "9ec05b61-3081-49bd-8020-55fb78a9dcd7",
            "approval_scope": "b06-9600-8n1-unit1-fc3-r0-5-r27-35",
            "evidence_files": _verified_b06_evidence_files(repo_root),
            "ranges": b06_ranges,
        },
        "model_assessments": [
            {
                "model_candidate": "CBMM",
                "confidence": "medium",
                "rubric": "medium requires both authenticated FC3 ranges to align with one recovered candidate; high requires device, firmware and authoritative point-map binding",
                "supports": ["B06_FC3_RANGE_0_5_READABLE", "B06_FC3_RANGE_27_35_READABLE"],
                "limitations": ["range readability does not identify a model"],
                "status": "retained_candidate",
            },
            {
                "model_candidate": "BCMM",
                "confidence": "low",
                "rubric": "low means one authenticated range aligns; medium requires both; high requires device, firmware and authoritative point-map binding",
                "supports": ["B06_FC3_RANGE_0_5_READABLE"],
                "limitations": ["the recovered BCMM rowset does not explain range 27..35"],
                "status": "retained_candidate",
            },
        ],
        "current_runtime_compatibility": {
            "fc3_signed_16_decode": "unsupported_if_legacy_mapping_applies",
            "fc1_point_number_r_bit_semantics": "unresolved_and_incompatible_with_current_api_contract",
            "api_allowed_value_types": ["字", "双字", "bit"],
            "gateway_decode_semantics": ["unsigned_16", "unsigned_32_high_word_first", "bit"],
            "scaling_formula": "display=(raw*point_ratio+point_offset)*user_ratio+user_point_offset",
            "hot_reload": "devices, points and serial bindings are startup-loaded; alarm rules alone reload",
            "safe_atomic_disabled_onboarding": "not_supported_by_current_rest_api",
            "serial_runtime_source": "GW_SERIAL_PORTS, not devices.baud_rate",
            "point_dev_addr_use": "not mapped from this physical row; current polling routes by devices.modbus_addr",
            "serial_line_parameters": "only port and baud_rate are explicit; pyserial defaults provide 8N1",
        },
        "classification_summary": {
            "total_candidates": len(candidates),
            "model_counts": {
                model: sum(point["model_candidate"] == model for point in candidates)
                for model in EXPECTED_MODEL_COUNTS
            },
            "source_candidate": len(candidates),
            "resolved": 0,
            "deployment_eligible": 0,
        },
        "candidates": candidates,
        "unresolved": [
            "Current physical device model and firmware/point-map version.",
            "Logical SQL table identity of the recovered physical rows.",
            "Whether the recovered ValueType field mapping applies to the current device.",
            "FC1 PointNumber/RBit semantics and a safe current-schema translation.",
            "Per-point independent reference method, tolerances and calibration approval.",
            "Authoritative scaling formula for the current firmware.",
        ],
        "contradictions": [
            {
                "id": "LEGACY_SCALING_FORMULA_CONFLICT",
                "status": "open",
                "summary": "legacy load, realtime and history paths use incompatible offset/ratio formulas",
                "affected_fields": ["/candidates/*/legacy_scaling"],
                "supports": ["MULTIPLE_LEGACY_CODE_PATHS_OBSERVED"],
                "refutes": ["ONE_AUTHORITATIVE_LEGACY_SCALING_FORMULA"],
                "does_not_test": ["CURRENT_DEVICE_SCALING"],
                "evidence_refs": ["LEGACY_CSHARP_FORMULA_PATHS"],
            },
            {
                "id": "FC1_RBIT_CONTRACT_CONFLICT",
                "status": "open",
                "summary": "legacy FC1 rows contain RBit values while the current API requires RBit to be absent for FC1/FC2",
                "affected_fields": [
                    "/candidates/42/legacy_r_bit_candidate",
                    "/candidates/43/legacy_r_bit_candidate",
                    "/candidates/44/legacy_r_bit_candidate",
                    "/candidates/45/legacy_r_bit_candidate",
                ],
                "supports": [
                    "LEGACY_FC1_RBIT_ROWS_OBSERVED",
                    "CURRENT_API_FC1_RBIT_FORBIDDEN",
                ],
                "refutes": ["MECHANICAL_FC1_TO_CURRENT_BIT_TRANSLATION"],
                "does_not_test": ["CURRENT_DEVICE_COIL_SEMANTICS"],
                "evidence_refs": [
                    "MDF_PAGE_258_PHYSICAL_ROW",
                    "CURRENT_POINT_API_CONTRACT",
                ],
            },
            {
                "id": "MODEL_IDENTITY_CONFLICT",
                "status": "open",
                "summary": "BCMM and CBMM coexist and authenticated range reads do not identify the current device",
                "affected_fields": ["/candidates/*/model_candidate"],
                "supports": ["BCMM_AND_CBMM_ROWS_OBSERVED", "B06_RANGES_READABLE"],
                "refutes": ["RANGE_READABILITY_PROVES_MODEL"],
                "does_not_test": [
                    "CURRENT_DEVICE_IDENTITY",
                    "CURRENT_FIRMWARE_POINT_MAP",
                ],
                "evidence_refs": [
                    "MDF_PAGE_258_PHYSICAL_ROW",
                    "B06_AUTHENTICATED_RANGE_EVIDENCE",
                ],
            },
        ],
        "prohibited_uses": [
            "Do not import these rows into the production database.",
            "Do not mark CBMM or BCMM as the confirmed device model.",
            "Do not enable or rebuild the production gateway from this artifact.",
            "Do not send any Modbus request on the authority of this artifact.",
            "Do not translate s16 to 字 or FC1 rows to bit without separately validated semantics.",
        ],
    }
    _verify_parser_source_unchanged()
    return artifact


def render_artifact(artifact: dict[str, Any]) -> str:
    return json.dumps(artifact, ensure_ascii=False, indent=2) + "\n"


def _validate_output_path(*, source: Path, output: Path) -> tuple[Path, Path]:
    source_resolved = _canonical_source_path(source)
    _reject_windows_special_path(output, label="output")
    _reject_parent_segments(output, label="output")
    if not output.name or output.name in (".", ".."):
        raise MdfEvidenceError("output must name a file")

    output_absolute = Path(os.path.abspath(output))
    if os.path.normcase(os.fspath(source_resolved)) == os.path.normcase(os.fspath(output_absolute)):
        raise MdfEvidenceError("output must not alias the source MDF")
    return output_absolute, output_absolute.parent


def _is_create_collision(exc: OSError) -> bool:
    return isinstance(exc, FileExistsError) or getattr(exc, "winerror", None) in (
        _WIN_ERROR_FILE_EXISTS,
        _WIN_ERROR_ALREADY_EXISTS,
    )


def _create_posix_anonymous_temporary(
    bound: BoundDirectory,
) -> tuple[BinaryIO, None, os.stat_result]:
    if bound.posix_descriptor is None or not _OS_O_TMPFILE:
        raise MdfEvidenceError(
            "POSIX output publication requires O_TMPFILE; refusing named-temp fallback"
        )
    flags = os.O_RDWR | _OS_O_TMPFILE | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(".", flags, 0o600, dir_fd=bound.posix_descriptor)
    except OSError as exc:
        raise MdfEvidenceError(
            "output filesystem does not support anonymous O_TMPFILE publication"
        ) from exc
    try:
        created_stat = os.fstat(descriptor)
        if not stat.S_ISREG(created_stat.st_mode) or created_stat.st_nlink != 0:
            raise MdfEvidenceError("anonymous temporary output must be a regular unlinked inode")
        stream = cast(BinaryIO, os.fdopen(descriptor, "w+b"))
    except Exception:
        os.close(descriptor)
        raise
    return stream, None, created_stat


def _create_windows_temporary(
    bound: BoundDirectory,
) -> tuple[BinaryIO, str, os.stat_result]:
    for _attempt in range(128):
        name = f".b08-{secrets.token_hex(16)}.tmp"
        descriptor: int | None = None
        windows_handle: int | None = None
        created_stat: os.stat_result | None = None
        try:
            windows_handle = _win_create_handle(
                bound.path / name,
                desired_access=(_WIN_GENERIC_READ | _WIN_GENERIC_WRITE | _WIN_DELETE),
                share_mode=_WIN_FILE_SHARE_READ,
                creation_disposition=_WIN_CREATE_NEW,
                flags_and_attributes=_WIN_FILE_ATTRIBUTE_NORMAL,
            )
            msvcrt = importlib.import_module("msvcrt")
            descriptor = msvcrt.open_osfhandle(
                windows_handle, os.O_RDWR | getattr(os, "O_BINARY", 0)
            )
            windows_handle = None

            if descriptor is None:
                raise MdfEvidenceError("temporary output file descriptor is unavailable")
            created_stat = os.fstat(descriptor)
            path_stat = _bound_lstat(bound, name)
            if (
                _is_reparse(path_stat)
                or not stat.S_ISREG(created_stat.st_mode)
                or not _same_identity(created_stat, path_stat)
                or created_stat.st_nlink != 1
                or _stat_signature(created_stat) != _stat_signature(path_stat)
            ):
                raise MdfEvidenceError("created temporary output identity is not bound")
            stream = cast(BinaryIO, os.fdopen(descriptor, "w+b"))
            descriptor = None
            return stream, name, created_stat
        except OSError as exc:
            if descriptor is None and windows_handle is None and _is_create_collision(exc):
                continue
            raise
        finally:
            if descriptor is not None or windows_handle is not None:
                try:
                    handle = (
                        _win_descriptor_handle(descriptor)
                        if descriptor is not None
                        else cast(int, windows_handle)
                    )
                    _win_mark_handle_for_deletion(handle)
                finally:
                    if descriptor is not None:
                        os.close(descriptor)
                    elif windows_handle is not None:
                        _win_close_handle(windows_handle)
    raise MdfEvidenceError("cannot allocate a unique temporary output name")


def _create_bound_temporary(
    bound: BoundDirectory,
) -> tuple[BinaryIO, str | None, os.stat_result]:
    if bound.posix_descriptor is not None:
        return _create_posix_anonymous_temporary(bound)
    return _create_windows_temporary(bound)


def _sha256_open_stream(stream: BinaryIO) -> str:
    position = stream.tell()
    digest = hashlib.sha256()
    stream.seek(0)
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    stream.seek(position)
    return digest.hexdigest().upper()


def _validate_open_output(
    stream: BinaryIO,
    *,
    created_stat: os.stat_result,
    expected_size: int,
    expected_hash: str,
    bound: BoundDirectory,
    name: str | None,
    expected_link_count: int,
    expected_metadata: os.stat_result | None = None,
) -> os.stat_result:
    try:
        handle_before = os.fstat(stream.fileno())
        if not stat.S_ISREG(handle_before.st_mode):
            raise MdfEvidenceError("retained output handle is not a regular file")
        if not _same_identity(created_stat, handle_before):
            raise MdfEvidenceError("retained output handle identity changed")
        if handle_before.st_nlink != expected_link_count:
            raise MdfEvidenceError(
                "retained output link count mismatch: "
                f"expected {expected_link_count}, found {handle_before.st_nlink}"
            )
        path_before = _bound_lstat(bound, name) if name is not None else None
        if path_before is not None and _is_reparse(path_before):
            raise MdfEvidenceError("published output is a symlink or reparse point")
        if path_before is not None and not _same_identity(handle_before, path_before):
            raise MdfEvidenceError("output path does not match the retained file handle")
        if expected_metadata is not None and _stat_signature(handle_before) != _stat_signature(
            expected_metadata
        ):
            raise MdfEvidenceError("temporary output metadata changed before publication")
        if handle_before.st_size != expected_size:
            raise MdfEvidenceError(
                f"temporary output size mismatch: expected {expected_size}, "
                f"found {handle_before.st_size}"
            )
        actual_hash = _sha256_open_stream(stream)
        handle_after = os.fstat(stream.fileno())
        path_after = _bound_lstat(bound, name) if name is not None else None
    except OSError as exc:
        raise MdfEvidenceError(f"cannot validate retained output handle: {exc}") from exc
    if (
        _stat_signature(handle_before) != _stat_signature(handle_after)
        or (path_before is None) != (path_after is None)
        or (
            path_before is not None
            and path_after is not None
            and (
                _stat_signature(path_before) != _stat_signature(path_after)
                or not _same_identity(handle_after, path_after)
            )
        )
    ):
        raise MdfEvidenceError("output identity or metadata changed during SHA-256 validation")
    if actual_hash != expected_hash:
        raise MdfEvidenceError(f"retained output SHA-256 mismatch: {actual_hash}")
    return handle_after


def _publish_no_replace(
    bound: BoundDirectory,
    stream: BinaryIO,
    temporary_name: str | None,
    output_name: str,
) -> None:
    if bound.posix_descriptor is not None:
        if temporary_name is not None:
            raise MdfEvidenceError("POSIX publication requires an anonymous temporary inode")
        _posix_link_anonymous_no_replace(stream.fileno(), bound.posix_descriptor, output_name)
        return
    if temporary_name is None:
        raise MdfEvidenceError("Windows publication requires its retained temporary name")
    _win_rename_handle_no_replace(_win_descriptor_handle(stream.fileno()), bound.path / output_name)


def _write_fsynced_temporary(
    stream: BinaryIO, rendered: bytes, *, expected_link_count: int
) -> os.stat_result:
    written = stream.write(rendered)
    if written != len(rendered):
        raise MdfEvidenceError(
            f"short artifact write: expected {len(rendered)} bytes, wrote {written}"
        )
    stream.flush()
    os.fsync(stream.fileno())
    temporary_stat = os.fstat(stream.fileno())
    if (
        not stat.S_ISREG(temporary_stat.st_mode)
        or temporary_stat.st_size != len(rendered)
        or temporary_stat.st_nlink != expected_link_count
    ):
        raise MdfEvidenceError("temporary output handle has an invalid type or size")
    return temporary_stat


def write_artifact_exclusive(artifact: dict[str, Any], *, source: Path, output: Path) -> None:
    _verify_parser_source_unchanged()
    rendered = render_artifact(artifact).encode("utf-8")
    rendered_hash = hashlib.sha256(rendered).hexdigest().upper()
    output_resolved, parent = _validate_output_path(source=source, output=output)

    with _bound_directory(parent, label="output parent", writable=True) as bound:
        _validate_bound_directory_path(bound, label="output parent")
        if _bound_lexists(bound, output_resolved.name):
            raise MdfEvidenceError(f"output already exists: {output}")
        try:
            stream, temporary_name, created_stat = _create_bound_temporary(bound)
        except OSError as exc:
            raise MdfEvidenceError(f"cannot create temporary output: {exc}") from exc

        completed = False
        published = False
        try:
            written_stat = _write_fsynced_temporary(
                stream,
                rendered,
                expected_link_count=(0 if bound.posix_descriptor is not None else 1),
            )
            _validate_open_output(
                stream,
                created_stat=created_stat,
                expected_size=len(rendered),
                expected_hash=rendered_hash,
                bound=bound,
                name=temporary_name,
                expected_link_count=(0 if bound.posix_descriptor is not None else 1),
                expected_metadata=written_stat,
            )
            _validate_bound_directory_path(bound, label="output parent")
            _verify_parser_source_unchanged()
            _publish_no_replace(bound, stream, temporary_name, output_resolved.name)
            published = True
            _validate_open_output(
                stream,
                created_stat=created_stat,
                expected_size=len(rendered),
                expected_hash=rendered_hash,
                bound=bound,
                name=output_resolved.name,
                expected_link_count=1,
            )
            _validate_bound_directory_path(bound, label="output parent")
            _fsync_bound_directory(bound)
            _validate_open_output(
                stream,
                created_stat=created_stat,
                expected_size=len(rendered),
                expected_hash=rendered_hash,
                bound=bound,
                name=output_resolved.name,
                expected_link_count=1,
            )
            _validate_bound_directory_path(bound, label="output parent")
            _verify_parser_source_unchanged()
            completed = True
        except FileExistsError as exc:
            raise MdfEvidenceError(f"output already exists: {output}") from exc
        except OSError as exc:
            message = f"cannot publish output: {exc}"
            if published and bound.posix_descriptor is not None:
                message += (
                    "; published output retained because POSIX rollback cannot safely "
                    "unlink by name"
                )
            raise MdfEvidenceError(message) from exc
        except Exception as exc:
            if published and bound.posix_descriptor is not None:
                raise MdfEvidenceError(
                    "published output validation failed; output retained because POSIX "
                    f"rollback cannot safely unlink by name: {exc}"
                ) from exc
            raise
        finally:
            try:
                if bound.posix_descriptor is not None and published and not completed:
                    _fsync_bound_directory(bound)
                elif bound.posix_descriptor is None and not completed:
                    _win_mark_handle_for_deletion(_win_descriptor_handle(stream.fileno()))
            finally:
                stream.close()


def _write_stdout_canonical(artifact: dict[str, Any]) -> None:
    output_buffer = getattr(sys.stdout, "buffer", None)
    if output_buffer is None:
        raise MdfEvidenceError("stdout does not expose a binary buffer for canonical UTF-8 output")
    rendered = render_artifact(artifact).encode("utf-8")
    _verify_parser_source_unchanged()
    written = output_buffer.write(rendered)
    if written != len(rendered):
        raise MdfEvidenceError(
            f"short stdout write: expected {len(rendered)} bytes, wrote {written}"
        )
    output_buffer.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--page-number", type=int, default=DEFAULT_PAGE_NUMBER)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        artifact = extract(args.source, page_number=args.page_number)
        if args.output is None:
            _write_stdout_canonical(artifact)
        else:
            write_artifact_exclusive(artifact, source=args.source, output=args.output)
    except (MdfEvidenceError, OSError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
