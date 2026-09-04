"""Issue, verify, install, and inspect signed site-scoped entitlements."""

from __future__ import annotations

import argparse
import base64
import binascii
import ctypes
import getpass
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from ctypes import wintypes
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

SCHEMA_VERSION = 1
ARTIFACT_TYPE = "ruisheng.site-entitlement"
SIGNATURE_ALGORITHM = "Ed25519"
SIGNATURE_DOMAIN = b"ruisheng.site-entitlement/v1\0"
MAX_JSON_BYTES = 1024 * 1024
MAX_AUDIT_BYTES = 16 * 1024 * 1024
MAX_AUDIT_LINE_BYTES = 64 * 1024
MAX_AUDIT_RECORDS = 50_000
MAX_OPERATION_RECORDS = 2_048
MAX_OPERATION_BYTES = 16 * 1024 * 1024
MAX_OPERATION_RECORD_BYTES = 16 * 1024
MAX_FEATURES = 128
MAX_GRACE_DAYS = 90
MAX_GRANT_DURATION = timedelta(days=366)
SIGNATURE_BYTES = 64
REASON_MIN_LENGTH = 8
REASON_MAX_LENGTH = 200
CONTROL_CHAR_LIMIT = 32
DELETE_CHAR = 127
MAX_PASSWORD_BYTES = 4096
LOCK_TIMEOUT_SECONDS = 30.0
CLOCK_ROLLBACK_TOLERANCE = timedelta(minutes=5)
WINDOWS_DIRECTORY_ACE_FLAGS = 0x3
WINDOWS_FILE_ALL_ACCESS = 0x1F01FF
IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
BASE64_RE = re.compile(r"[A-Za-z0-9+/]+={0,2}\Z")
PAYLOAD_KEYS = (
    "schema_version",
    "artifact_type",
    "grant_id",
    "site_id",
    "customer_id",
    "plan",
    "features",
    "serial",
    "issued_at",
    "starts_at",
    "expires_at",
    "grace_until",
    "key_id",
)
SIGNATURE_KEYS = ("algorithm", "key_id", "value")


class EntitlementError(ValueError):
    """A safe entitlement error code suitable for logs and receipts."""


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_artifact_bytes(value: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or IDENTIFIER_RE.fullmatch(value) is None:
        raise EntitlementError(f"{label}_invalid")
    return value


def _operation_id(value: str) -> str:
    if not isinstance(value, str):
        raise EntitlementError("operation_id_invalid")
    normalized = value.lower()
    if UUID_RE.fullmatch(normalized) is None:
        raise EntitlementError("operation_id_invalid")
    return normalized


def _reason(value: str) -> str:
    if not REASON_MIN_LENGTH <= len(value) <= REASON_MAX_LENGTH or any(
        ord(char) < CONTROL_CHAR_LIMIT or ord(char) == DELETE_CHAR for char in value
    ):
        raise EntitlementError("reason_invalid")
    return value


def _timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise EntitlementError(f"{label}_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except (OverflowError, ValueError) as exc:
        raise EntitlementError(f"{label}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise EntitlementError(f"{label}_not_utc")
    if value != parsed.astimezone(UTC).isoformat(timespec="seconds"):
        raise EntitlementError(f"{label}_not_canonical")
    return parsed.astimezone(UTC)


def _timestamp_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise EntitlementError("timestamp_timezone_required")
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EntitlementError("duplicate_json_key")
        result[key] = value
    return result


def _parse_canonical_document(
    data: bytes, *, label: str, enforce_canonical: bool = True
) -> dict[str, Any]:
    if not data or len(data) > MAX_JSON_BYTES:
        raise EntitlementError(f"{label}_size_invalid")
    if data.startswith(b"\xef\xbb\xbf"):
        raise EntitlementError(f"{label}_encoding_invalid")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EntitlementError(f"{label}_encoding_invalid") from exc
    try:
        value = json.loads(text, object_pairs_hook=_no_duplicate_object)
    except EntitlementError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise EntitlementError(f"{label}_json_invalid") from exc
    if not isinstance(value, dict):
        raise EntitlementError(f"{label}_object_required")
    if enforce_canonical:
        try:
            canonical = canonical_artifact_bytes(value)
        except (TypeError, ValueError) as exc:
            raise EntitlementError(f"{label}_json_invalid") from exc
        if data != canonical:
            raise EntitlementError(f"{label}_not_canonical")
    return value


def _read_limited(path: Path, *, label: str, limit: int, size_error: str) -> bytes:
    try:
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        raise EntitlementError(f"{label}_read_failed") from exc
    if len(data) > limit:
        raise EntitlementError(size_error)
    return data


def _read_bounded(path: Path, *, label: str) -> bytes:
    return _read_limited(
        path,
        label=label,
        limit=MAX_JSON_BYTES,
        size_error=f"{label}_size_invalid",
    )


def _payload(grant: Mapping[str, Any]) -> dict[str, Any]:
    if set(grant) != {*PAYLOAD_KEYS, "signature"}:
        raise EntitlementError("grant_fields_invalid")
    signature = grant.get("signature")
    if not isinstance(signature, dict) or set(signature) != set(SIGNATURE_KEYS):
        raise EntitlementError("signature_fields_invalid")
    payload = {key: grant[key] for key in PAYLOAD_KEYS}
    if signature.get("algorithm") != SIGNATURE_ALGORITHM:
        raise EntitlementError("signature_algorithm_invalid")
    if signature.get("key_id") != payload["key_id"]:
        raise EntitlementError("signature_key_mismatch")
    _decode_signature(signature.get("value"))
    return payload


def _decode_signature(value: object) -> bytes:
    if not isinstance(value, str) or BASE64_RE.fullmatch(value) is None:
        raise EntitlementError("signature_invalid")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EntitlementError("signature_invalid") from exc
    if len(decoded) != SIGNATURE_BYTES or base64.b64encode(decoded).decode("ascii") != value:
        raise EntitlementError("signature_invalid")
    return decoded


def validate_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if set(payload) != set(PAYLOAD_KEYS):
        raise EntitlementError("grant_fields_invalid")
    schema_version = payload.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != SCHEMA_VERSION
    ):
        raise EntitlementError("schema_version_invalid")
    if payload.get("artifact_type") != ARTIFACT_TYPE:
        raise EntitlementError("artifact_type_invalid")
    for field in ("grant_id", "site_id", "customer_id", "plan", "key_id"):
        _identifier(payload.get(field), field)
    features = payload.get("features")
    if (
        not isinstance(features, list)
        or not features
        or len(features) > MAX_FEATURES
        or any(
            not isinstance(item, str) or IDENTIFIER_RE.fullmatch(item) is None for item in features
        )
        or features != sorted(set(features))
    ):
        raise EntitlementError("features_invalid")
    serial = payload.get("serial")
    if not isinstance(serial, int) or isinstance(serial, bool) or not 1 <= serial <= 2**63 - 1:
        raise EntitlementError("serial_invalid")
    issued = _timestamp(payload.get("issued_at"), "issued_at")
    starts = _timestamp(payload.get("starts_at"), "starts_at")
    expires = _timestamp(payload.get("expires_at"), "expires_at")
    grace = _timestamp(payload.get("grace_until"), "grace_until")
    if issued > expires or starts >= expires or expires > grace:
        raise EntitlementError("time_order_invalid")
    if expires - starts > MAX_GRANT_DURATION:
        raise EntitlementError("grant_duration_too_long")
    if grace - expires > timedelta(days=MAX_GRACE_DAYS):
        raise EntitlementError("grace_too_long")
    return dict(payload)


def load_grant(path: Path) -> tuple[dict[str, Any], bytes]:
    data = _read_bounded(path, label="grant")
    grant = _parse_canonical_document(data, label="grant", enforce_canonical=False)
    validate_payload(_payload(grant))
    if data != canonical_artifact_bytes(grant):
        raise EntitlementError("grant_not_canonical")
    return grant, data


def _public_key(path: Path) -> tuple[Ed25519PublicKey, str]:
    data = _read_bounded(path, label="public_key")
    try:
        line = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise EntitlementError("public_key_invalid") from exc
    match = re.fullmatch(
        r"ssh-ed25519 ([A-Za-z0-9+/]+={0,2}) ([A-Za-z0-9][A-Za-z0-9._:-]{0,127})\n", line
    )
    if match is None:
        raise EntitlementError("public_key_format_invalid")
    key_id = match.group(2)
    try:
        key = serialization.load_ssh_public_key(data)
    except (ValueError, TypeError) as exc:
        raise EntitlementError("public_key_invalid") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise EntitlementError("public_key_invalid")
    return key, key_id


def _private_key(path: Path, password: bytes) -> Ed25519PrivateKey:
    if not password:
        raise EntitlementError("private_key_password_required")
    try:
        key = serialization.load_pem_private_key(
            _read_bounded(path, label="private_key"), password=password
        )
    except (ValueError, TypeError) as exc:
        raise EntitlementError("private_key_invalid") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise EntitlementError("private_key_invalid")
    return key


def _windows_dlls() -> tuple[Any, Any]:
    loader = getattr(ctypes, "windll", None)
    if loader is None:
        raise EntitlementError("windows_api_unavailable")
    return loader.kernel32, loader.advapi32


def _require_protected_private_key_parent(path: Path) -> None:  # noqa: PLR0912, PLR0915
    """Require the Windows key directory to have the target's exact protected ACL."""
    if os.name != "nt":
        return
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        raise EntitlementError("private_key_parent_missing")
    kernel32, advapi32 = _windows_dlls()

    invalid_attributes = 0xFFFFFFFF
    file_attribute_reparse_point = 0x400
    attributes = kernel32.GetFileAttributesW(str(parent))
    if attributes == invalid_attributes:
        raise EntitlementError("private_key_parent_read_failed")
    if attributes & file_attribute_reparse_point:
        raise EntitlementError("private_key_parent_reparse_point")

    owner_information = 0x1
    dacl_information = 0x4
    se_file_object = 1
    security_descriptor = ctypes.c_void_p()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    result = advapi32.GetNamedSecurityInfoW(
        str(parent),
        se_file_object,
        owner_information | dacl_information,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(security_descriptor),
    )
    if result:
        raise EntitlementError("private_key_parent_acl_invalid")

    class AclSizeInformation(ctypes.Structure):
        _fields_ = [
            ("ace_count", wintypes.DWORD),
            ("acl_bytes_in_use", wintypes.DWORD),
            ("acl_bytes_free", wintypes.DWORD),
        ]

    class AceHeader(ctypes.Structure):
        _fields_ = [
            ("ace_type", ctypes.c_ubyte),
            ("ace_flags", ctypes.c_ubyte),
            ("ace_size", wintypes.WORD),
        ]

    try:
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if (
            not advapi32.GetSecurityDescriptorControl(
                security_descriptor, ctypes.byref(control), ctypes.byref(revision)
            )
            or not control.value & 0x1000
        ):
            raise EntitlementError("private_key_parent_acl_unprotected")
        if not dacl:
            raise EntitlementError("private_key_parent_acl_invalid")

        expected_sids: list[ctypes.c_void_p] = []
        for sid_text in ("S-1-5-32-544", "S-1-5-18"):
            sid = ctypes.c_void_p()
            if not advapi32.ConvertStringSidToSidW(sid_text, ctypes.byref(sid)):
                raise EntitlementError("private_key_parent_acl_invalid")
            expected_sids.append(sid)
        try:
            if not any(advapi32.EqualSid(owner, sid) for sid in expected_sids):
                raise EntitlementError("private_key_parent_owner_invalid")
            info = AclSizeInformation()
            if not advapi32.GetAclInformation(
                dacl, ctypes.byref(info), ctypes.sizeof(info), 2
            ) or info.ace_count != len(expected_sids):
                raise EntitlementError("private_key_parent_acl_invalid")
            seen: set[int] = set()
            for index in range(info.ace_count):
                ace = ctypes.c_void_p()
                if not advapi32.GetAce(dacl, index, ctypes.byref(ace)):
                    raise EntitlementError("private_key_parent_acl_invalid")
                header = ctypes.cast(ace, ctypes.POINTER(AceHeader)).contents
                raw = ctypes.cast(ace, ctypes.POINTER(ctypes.c_ubyte))
                mask = ctypes.cast(
                    ctypes.byref(raw.contents, 4), ctypes.POINTER(wintypes.DWORD)
                ).contents.value
                ace_sid = ctypes.cast(ctypes.byref(raw.contents, 8), ctypes.c_void_p)
                matches = [
                    position
                    for position, sid in enumerate(expected_sids)
                    if advapi32.EqualSid(ace_sid, sid)
                ]
                if (
                    header.ace_type != 0
                    or header.ace_flags != WINDOWS_DIRECTORY_ACE_FLAGS
                    or mask != WINDOWS_FILE_ALL_ACCESS
                    or len(matches) != 1
                    or matches[0] in seen
                ):
                    raise EntitlementError("private_key_parent_acl_invalid")
                seen.add(matches[0])
            if len(seen) != len(expected_sids):
                raise EntitlementError("private_key_parent_acl_invalid")
        finally:
            for sid in expected_sids:
                kernel32.LocalFree(sid)
    finally:
        kernel32.LocalFree(security_descriptor)


def _set_protected_private_key_acl(path: Path) -> None:
    if os.name != "nt":
        return
    kernel32, advapi32 = _windows_dlls()
    security_descriptor = ctypes.c_void_p()
    descriptor_size = wintypes.ULONG()
    sddl = "O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)"
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(security_descriptor), ctypes.byref(descriptor_size)
    ):
        raise EntitlementError("private_key_acl_invalid")
    try:
        if not advapi32.SetFileSecurityW(str(path), 0x1 | 0x2 | 0x4, security_descriptor):
            raise EntitlementError("private_key_acl_invalid")
    finally:
        kernel32.LocalFree(security_descriptor)


def _require_protected_private_key_leaf(path: Path) -> None:  # noqa: PLR0912, PLR0915
    if os.name != "nt":
        return
    if not path.exists() or not path.is_file():
        raise EntitlementError("private_key_missing")
    kernel32, advapi32 = _windows_dlls()
    attributes = kernel32.GetFileAttributesW(str(path))
    invalid_attributes = 0xFFFFFFFF
    if attributes == invalid_attributes:
        raise EntitlementError("private_key_read_failed")
    if attributes & 0x400:
        raise EntitlementError("private_key_reparse_point")

    security_descriptor = ctypes.c_void_p()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    result = advapi32.GetNamedSecurityInfoW(
        str(path),
        1,
        0x1 | 0x4,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(security_descriptor),
    )
    if result:
        raise EntitlementError("private_key_acl_invalid")

    class AclSizeInformation(ctypes.Structure):
        _fields_ = [
            ("ace_count", wintypes.DWORD),
            ("acl_bytes_in_use", wintypes.DWORD),
            ("acl_bytes_free", wintypes.DWORD),
        ]

    class AceHeader(ctypes.Structure):
        _fields_ = [
            ("ace_type", ctypes.c_ubyte),
            ("ace_flags", ctypes.c_ubyte),
            ("ace_size", wintypes.WORD),
        ]

    try:
        control = wintypes.WORD()
        revision = wintypes.DWORD()
        if (
            not advapi32.GetSecurityDescriptorControl(
                security_descriptor, ctypes.byref(control), ctypes.byref(revision)
            )
            or not control.value & 0x1000
            or not dacl
        ):
            raise EntitlementError("private_key_acl_invalid")
        expected_sids: list[ctypes.c_void_p] = []
        for sid_text in ("S-1-5-32-544", "S-1-5-18"):
            sid = ctypes.c_void_p()
            if not advapi32.ConvertStringSidToSidW(sid_text, ctypes.byref(sid)):
                raise EntitlementError("private_key_acl_invalid")
            expected_sids.append(sid)
        try:
            if not any(advapi32.EqualSid(owner, sid) for sid in expected_sids):
                raise EntitlementError("private_key_owner_invalid")
            info = AclSizeInformation()
            if not advapi32.GetAclInformation(
                dacl, ctypes.byref(info), ctypes.sizeof(info), 2
            ) or info.ace_count != len(expected_sids):
                raise EntitlementError("private_key_acl_invalid")
            seen: set[int] = set()
            for index in range(info.ace_count):
                ace = ctypes.c_void_p()
                if not advapi32.GetAce(dacl, index, ctypes.byref(ace)):
                    raise EntitlementError("private_key_acl_invalid")
                header = ctypes.cast(ace, ctypes.POINTER(AceHeader)).contents
                raw = ctypes.cast(ace, ctypes.POINTER(ctypes.c_ubyte))
                mask = ctypes.cast(
                    ctypes.byref(raw.contents, 4), ctypes.POINTER(wintypes.DWORD)
                ).contents.value
                ace_sid = ctypes.cast(ctypes.byref(raw.contents, 8), ctypes.c_void_p)
                matches = [
                    position
                    for position, sid in enumerate(expected_sids)
                    if advapi32.EqualSid(ace_sid, sid)
                ]
                if (
                    header.ace_type != 0
                    or header.ace_flags != 0
                    or mask != WINDOWS_FILE_ALL_ACCESS
                    or len(matches) != 1
                    or matches[0] in seen
                ):
                    raise EntitlementError("private_key_acl_invalid")
                seen.add(matches[0])
        finally:
            for sid in expected_sids:
                kernel32.LocalFree(sid)
    finally:
        kernel32.LocalFree(security_descriptor)


def _message(payload: Mapping[str, Any]) -> bytes:
    return SIGNATURE_DOMAIN + canonical_json_bytes(payload)


def verify_grant(
    grant: Mapping[str, Any],
    public_key: Ed25519PublicKey,
    trusted_key_id: str,
    *,
    expected_site_id: str | None = None,
    now: datetime | None = None,
    for_install: bool = False,
    artifact_bytes: bytes | None = None,
) -> tuple[dict[str, Any], str]:
    payload = validate_payload(_payload(grant))
    if payload["key_id"] != trusted_key_id:
        raise EntitlementError("unknown_key_id")
    if expected_site_id is not None and payload["site_id"] != expected_site_id:
        raise EntitlementError("site_mismatch")
    signature = _decode_signature(grant["signature"]["value"])
    try:
        public_key.verify(signature, _message(payload))
    except InvalidSignature as exc:
        raise EntitlementError("signature_invalid") from exc
    observed = (now or datetime.now(UTC)).astimezone(UTC)
    if for_install:
        if _timestamp(payload["issued_at"], "issued_at") > observed:
            raise EntitlementError("issued_in_future")
        if _timestamp(payload["starts_at"], "starts_at") > observed:
            raise EntitlementError("starts_in_future")
        if _timestamp(payload["grace_until"], "grace_until") <= observed:
            raise EntitlementError("grant_expired")
    data = artifact_bytes if artifact_bytes is not None else canonical_artifact_bytes(grant)
    if data != canonical_artifact_bytes(grant):
        raise EntitlementError("grant_not_canonical")
    return payload, sha256_bytes(data)


def verify_grant_file(
    grant_path: Path,
    public_key_path: Path,
    *,
    expected_site_id: str | None = None,
    now: datetime | None = None,
    for_install: bool = False,
) -> tuple[dict[str, Any], str]:
    grant, data = load_grant(grant_path)
    key, key_id = _public_key(public_key_path)
    return verify_grant(
        grant,
        key,
        key_id,
        expected_site_id=expected_site_id,
        now=now,
        for_install=for_install,
        artifact_bytes=data,
    )


def issue_grant(
    *,
    private_key_path: Path,
    private_key_password: bytes,
    key_id: str,
    site_id: str,
    customer_id: str,
    plan: str,
    features: list[str],
    serial: int,
    start: datetime,
    end: datetime,
    issued: datetime | None = None,
    grace_days: int = 7,
    grant_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    observed = (now or datetime.now(UTC)).astimezone(UTC)
    issued_at = (issued or observed).astimezone(UTC)
    if issued_at > observed:
        raise EntitlementError("issued_in_future")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "grant_id": grant_id or str(uuid.uuid4()),
        "site_id": site_id,
        "customer_id": customer_id,
        "plan": plan,
        "features": sorted(set(features)),
        "serial": serial,
        "issued_at": _timestamp_text(issued_at),
        "starts_at": _timestamp_text(start),
        "expires_at": _timestamp_text(end),
        "grace_until": _timestamp_text(end + timedelta(days=grace_days)),
        "key_id": key_id,
    }
    validate_payload(payload)
    signature = _private_key(private_key_path, private_key_password).sign(_message(payload))
    return {
        **payload,
        "signature": {
            "algorithm": SIGNATURE_ALGORITHM,
            "key_id": key_id,
            "value": base64.b64encode(signature).decode("ascii"),
        },
    }


def _create_file_no_clobber(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    except FileExistsError as exc:
        raise EntitlementError("output_exists") from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        with suppress(FileNotFoundError):
            path.unlink()
        raise


def _atomic_replace(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary)


if sys.platform == "win32":

    def _lock_handle(handle: BinaryIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_handle(handle: BinaryIO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:

    def _lock_handle(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_handle(handle: BinaryIO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def transaction_lock(path: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                _lock_handle(handle)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise EntitlementError("transaction_lock_timeout") from None
                time.sleep(0.02)
        try:
            yield
        finally:
            _unlock_handle(handle)


def _audit_archive_path(path: Path, slot: int = 1) -> Path:
    if slot not in {1, 2}:
        raise EntitlementError("audit_checkpoint_invalid")
    return path.with_name(f"{path.name}.{slot}")


def _audit_entries(  # noqa: PLR0912
    path: Path, *, verify_checkpoint_archive: bool = True
) -> tuple[bytes, list[dict[str, Any]], str]:
    old = (
        _read_limited(
            path,
            label="audit",
            limit=MAX_AUDIT_BYTES,
            size_error="audit_file_limit_exceeded",
        )
        if path.exists()
        else b""
    )
    entries: list[dict[str, Any]] = []
    previous_hash = "0" * 64
    for index, raw_line in enumerate(old.splitlines(keepends=True)):
        if len(raw_line) > MAX_AUDIT_LINE_BYTES:
            raise EntitlementError("audit_line_limit_exceeded")
        value = _parse_canonical_document(raw_line, label="audit")
        record_hash = value.pop("record_hash", None)
        claimed_previous = value.get("previous_hash")
        if index == 0 and claimed_previous != previous_hash:
            if value.get("event") != "audit_checkpoint":
                raise EntitlementError("audit_chain_invalid")
            archive_digest = value.get("archive_sha256")
            archive_tail = value.get("archive_tail_hash")
            archive_file = value.get("archive_file")
            audit_name = path.name[:-2] if path.name.endswith((".1", ".2")) else path.name
            if (
                not isinstance(archive_digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", archive_digest) is None
                or archive_tail != claimed_previous
                or archive_file not in {f"{audit_name}.1", f"{audit_name}.2"}
            ):
                raise EntitlementError("audit_chain_invalid")
            if verify_checkpoint_archive:
                archive_path = path.with_name(str(archive_file))
                if not archive_path.exists():
                    raise EntitlementError("audit_archive_missing")
                try:
                    archive_raw, _, actual_tail = _audit_entries(
                        archive_path, verify_checkpoint_archive=False
                    )
                except EntitlementError as exc:
                    raise EntitlementError("audit_archive_invalid") from exc
                if sha256_bytes(archive_raw) != archive_digest or actual_tail != archive_tail:
                    raise EntitlementError("audit_archive_invalid")
            previous_hash = str(claimed_previous)
        if claimed_previous != previous_hash:
            raise EntitlementError("audit_chain_invalid")
        if not isinstance(record_hash, str) or record_hash != sha256_bytes(
            canonical_json_bytes(value)
        ):
            raise EntitlementError("audit_chain_invalid")
        value["record_hash"] = record_hash
        entries.append(value)
        previous_hash = record_hash
    if len(entries) > MAX_AUDIT_RECORDS:
        raise EntitlementError("audit_record_limit_exceeded")
    return old, entries, previous_hash


def _audit_line(record: Mapping[str, Any], previous_hash: str) -> bytes:
    material = {**record, "previous_hash": previous_hash}
    entry = {**material, "record_hash": sha256_bytes(canonical_json_bytes(material))}
    line = canonical_artifact_bytes(entry)
    if len(line) > MAX_AUDIT_LINE_BYTES:
        raise EntitlementError("audit_size_limit_exceeded")
    return line


def _rotate_audit_locked(path: Path, old: bytes, previous_hash: str) -> tuple[bytes, str]:
    if not old:
        return old, previous_hash
    first = _parse_canonical_document(old.splitlines(keepends=True)[0], label="audit")
    previous_archive = (
        first.get("archive_file") if first.get("event") == "audit_checkpoint" else None
    )
    slot = 2 if previous_archive == f"{path.name}.1" else 1
    archive_path = _audit_archive_path(path, slot)
    _atomic_replace(archive_path, old)
    checkpoint = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "event": "audit_checkpoint",
        "archive_file": archive_path.name,
        "archive_sha256": sha256_bytes(old),
        "archive_tail_hash": previous_hash,
    }
    line = _audit_line(checkpoint, previous_hash)
    _atomic_replace(path, line)
    stale_slot = _audit_archive_path(path, 3 - slot)
    with suppress(FileNotFoundError):
        stale_slot.unlink()
    checkpoint_entry = _parse_canonical_document(line, label="audit_checkpoint")
    return line, str(checkpoint_entry["record_hash"])


def _prepare_audit_append_locked(path: Path, record: Mapping[str, Any]) -> None:
    old, entries, previous_hash = _audit_entries(path)
    line = _audit_line(record, previous_hash)
    if len(entries) >= MAX_AUDIT_RECORDS or len(old) + len(line) > MAX_AUDIT_BYTES:
        old, previous_hash = _rotate_audit_locked(path, old, previous_hash)
        entries = _audit_entries(path)[1]
        line = _audit_line(record, previous_hash)
    if len(entries) >= MAX_AUDIT_RECORDS or len(old) + len(line) > MAX_AUDIT_BYTES:
        raise EntitlementError("audit_size_limit_exceeded")


def _append_audit_locked(path: Path, record: Mapping[str, Any]) -> None:
    _prepare_audit_append_locked(path, record)
    old, entries, previous_hash = _audit_entries(path)
    line = _audit_line(record, previous_hash)
    if len(entries) >= MAX_AUDIT_RECORDS or len(old) + len(line) > MAX_AUDIT_BYTES:
        raise EntitlementError("audit_size_limit_exceeded")
    _atomic_replace(path, old + line)


def _operation_path(state_path: Path, operation_id: str) -> Path:
    return state_path.parent / "operations" / f"{operation_id}.json"


def _journal_path(state_path: Path) -> Path:
    return state_path.parent / "transaction.json"


def _last_seen_path(state_path: Path) -> Path:
    return state_path.parent / "last-seen.json"


def _identity(
    *,
    operation_id: str,
    actor: str,
    site_id: str,
    reason: str,
    grant_id: str,
    grant_sha256: str,
    serial: int,
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "actor": actor,
        "site_id": site_id,
        "reason_sha256": sha256_bytes(reason.encode("utf-8")),
        "grant_id": grant_id,
        "grant_sha256": grant_sha256,
        "serial": serial,
    }


def _read_canonical_optional(path: Path, label: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _parse_canonical_document(_read_bounded(path, label=label), label=label)


def _hashed_record(material: Mapping[str, Any]) -> dict[str, Any]:
    return {**material, "record_hash": sha256_bytes(canonical_json_bytes(material))}


def _validate_hashed_record(
    value: Mapping[str, Any], *, required: set[str], label: str
) -> dict[str, Any]:
    if set(value) != required | {"record_hash"}:
        raise EntitlementError(f"{label}_invalid")
    material = {key: value[key] for key in value if key != "record_hash"}
    if value.get("record_hash") != sha256_bytes(canonical_json_bytes(material)):
        raise EntitlementError(f"{label}_invalid")
    return material


def _read_operation(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = _parse_canonical_document(
        _read_limited(
            path,
            label="operation",
            limit=MAX_OPERATION_RECORD_BYTES,
            size_error="operation_size_invalid",
        ),
        label="operation",
    )
    return _validate_hashed_record(
        value,
        required={
            "schema_version",
            "operation_id",
            "actor",
            "site_id",
            "reason_sha256",
            "grant_id",
            "grant_sha256",
            "serial",
            "status",
            "error_code",
            "recorded_at",
        },
        label="operation",
    )


def _write_operation(
    path: Path, identity: Mapping[str, Any], status: str, error_code: str = ""
) -> None:
    material = {
        "schema_version": 1,
        **identity,
        "status": status,
        "error_code": error_code,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    data = canonical_artifact_bytes(_hashed_record(material))
    if len(data) > MAX_OPERATION_RECORD_BYTES:
        raise EntitlementError("operation_record_too_large")
    _atomic_replace(path, data)


def _validate_journal(
    journal: Mapping[str, Any],
) -> tuple[dict[str, Any], bytes, bytes]:
    required = {
        "schema_version",
        "phase",
        "identity",
        "new_state_b64",
        "old_state_b64",
        "old_state_present",
    }
    identity = journal.get("identity")
    identity_fields = {
        "operation_id",
        "actor",
        "site_id",
        "reason_sha256",
        "grant_id",
        "grant_sha256",
        "serial",
    }
    if (
        set(journal) != required
        or journal.get("schema_version") != 1
        or journal.get("phase") not in {"prepared", "state_replaced"}
        or not isinstance(identity, dict)
        or set(identity) != identity_fields
        or not isinstance(journal.get("old_state_present"), bool)
    ):
        raise EntitlementError("journal_invalid")
    operation_id = identity.get("operation_id")
    if not isinstance(operation_id, str):
        raise EntitlementError("journal_invalid")
    try:
        _operation_id(operation_id)
        _identifier(identity.get("actor"), "actor")
        _identifier(identity.get("site_id"), "site_id")
        _identifier(identity.get("grant_id"), "grant_id")
        for field in ("reason_sha256", "grant_sha256"):
            if (
                not isinstance(identity.get(field), str)
                or re.fullmatch(r"[0-9a-f]{64}", str(identity[field])) is None
            ):
                raise EntitlementError("journal_invalid")
        serial = identity.get("serial")
        if not isinstance(serial, int) or isinstance(serial, bool) or serial < 0:
            raise EntitlementError("journal_invalid")
        new_state = base64.b64decode(journal["new_state_b64"], validate=True)
        old_state = base64.b64decode(journal["old_state_b64"], validate=True)
    except (binascii.Error, EntitlementError, TypeError, ValueError) as exc:
        raise EntitlementError("journal_invalid") from exc
    if not new_state or bool(old_state) != bool(journal["old_state_present"]):
        raise EntitlementError("journal_invalid")
    return dict(identity), new_state, old_state


def _active_journal_operation_id(state_path: Path) -> str | None:
    journal = _read_canonical_optional(_journal_path(state_path), "journal")
    if journal is None:
        return None
    identity, _, _ = _validate_journal(journal)
    return str(identity["operation_id"])


def _prune_operations_locked(state_path: Path, operation_id: str) -> None:
    directory = state_path.parent / "operations"
    if not directory.exists():
        return
    referenced_operation = _active_journal_operation_id(state_path)
    records: list[tuple[int, str, Path, int]] = []
    total_bytes = 0
    count = 0
    for path in directory.iterdir():
        if not path.is_file() or path.suffix != ".json":
            continue
        count += 1
        size = path.stat().st_size
        total_bytes += size
        value = _read_operation(path)
        if value is None or path.stem in {operation_id, referenced_operation}:
            continue
        orphan_priority = 0 if value.get("status") == "executing" else 1
        records.append((orphan_priority, str(value["recorded_at"]), path, size))
    for _, _, path, size in sorted(records, key=lambda item: (item[0], item[1], item[2].name)):
        if (
            count < MAX_OPERATION_RECORDS
            and total_bytes + MAX_OPERATION_RECORD_BYTES <= MAX_OPERATION_BYTES
        ):
            break
        path.unlink()
        count -= 1
        total_bytes -= size
    if (
        count >= MAX_OPERATION_RECORDS
        or total_bytes + MAX_OPERATION_RECORD_BYTES > MAX_OPERATION_BYTES
    ):
        raise EntitlementError("operation_retention_exhausted")


def _observe_time_locked(time_state_path: Path, observed: datetime, *, update: bool) -> datetime:
    path = time_state_path
    value = _read_canonical_optional(path, "last_seen")
    previous: datetime | None = None
    if value is not None:
        material = _validate_hashed_record(
            value,
            required={"schema_version", "last_seen_utc"},
            label="last_seen",
        )
        if material.get("schema_version") != 1:
            raise EntitlementError("last_seen_invalid")
        previous = _timestamp(material.get("last_seen_utc"), "last_seen")
        if observed + CLOCK_ROLLBACK_TOLERANCE < previous:
            raise EntitlementError("clock_rollback")
    effective = observed if previous is None or observed > previous else previous
    if update and (previous is None or effective > previous):
        material = {
            "schema_version": 1,
            "last_seen_utc": _timestamp_text(effective),
        }
        try:
            _atomic_replace(path, canonical_artifact_bytes(_hashed_record(material)))
        except OSError as exc:
            raise EntitlementError("last_seen_write_failed") from exc
    return effective


def _audit_record(identity: Mapping[str, Any], result: str, error_code: str = "") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "event": "entitlement_install",
        **identity,
        "result": result,
        "error_code": error_code,
    }


def _recover_locked(state_path: Path, audit_path: Path) -> None:
    journal_path = _journal_path(state_path)
    journal = _read_canonical_optional(journal_path, "journal")
    if journal is None:
        return
    identity, new_state, old_state = _validate_journal(journal)
    current = _read_bounded(state_path, label="current_grant") if state_path.exists() else b""
    operation_path = _operation_path(state_path, str(identity.get("operation_id")))
    try:
        if current == new_state:
            _append_audit_locked(audit_path, _audit_record(identity, "installed_recovered"))
            _write_operation(operation_path, identity, "installed")
        elif current == old_state and bool(journal["old_state_present"]):
            _atomic_replace(state_path, old_state)
            _append_audit_locked(
                audit_path, _audit_record(identity, "crash_recovered", "interrupted")
            )
            _write_operation(operation_path, identity, "rejected", "interrupted")
        elif not current and not bool(journal["old_state_present"]):
            _append_audit_locked(
                audit_path, _audit_record(identity, "crash_recovered", "interrupted")
            )
            _write_operation(operation_path, identity, "rejected", "interrupted")
        else:
            raise EntitlementError("transaction_state_unrecognized")
        journal_path.unlink()
    except EntitlementError as exc:
        raise EntitlementError("transaction_uncertain") from exc


def _untrusted_identity(
    *,
    operation_id: str,
    actor: str,
    site_id: str,
    reason: str,
    raw_digest: str,
) -> dict[str, Any]:
    return _identity(
        operation_id=operation_id,
        actor=actor,
        site_id=site_id,
        reason=reason,
        grant_id="unverified",
        grant_sha256=raw_digest,
        serial=0,
    )


def _recover_or_raise_uncertain(state_path: Path, audit_path: Path) -> None:
    try:
        _recover_locked(state_path, audit_path)
    except EntitlementError as exc:
        raise EntitlementError("transaction_uncertain") from exc


def _resolve_existing_operation(
    existing: Mapping[str, Any] | None,
    identity: Mapping[str, Any],
    payload: Mapping[str, Any],
    audit_path: Path,
    state_path: Path,
) -> dict[str, Any] | None:
    if existing is None:
        return None
    expected = {key: existing.get(key) for key in identity}
    if expected != identity:
        raise EntitlementError("operation_conflict")
    if existing.get("status") == "installed":
        try:
            current_digest = sha256_bytes(_read_bounded(state_path, label="current_grant"))
        except EntitlementError as exc:
            raise EntitlementError("transaction_uncertain") from exc
        if current_digest != identity["grant_sha256"]:
            raise EntitlementError("transaction_uncertain")
        return _install_receipt(identity, payload, idempotent=True)
    if existing.get("status") != "executing":
        raise EntitlementError("operation_already_rejected")
    _append_audit_locked(
        audit_path,
        _audit_record(identity, "crash_recovered", "interrupted_before_journal"),
    )
    return None


def install_grant(  # noqa: PLR0912, PLR0915
    *,
    grant_path: Path,
    public_key_path: Path,
    state_path: Path,
    audit_path: Path,
    site_id: str,
    operation_id: str,
    reason: str,
    actor: str = "operator",
    claimed_site_id: str | None = None,
    now: datetime | None = None,
    time_state_path: Path | None = None,
    fault_after_operation_write: bool = False,
    fault_after_state_replace: bool = False,
) -> dict[str, Any]:
    operation_id = _operation_id(operation_id)
    site_id = _identifier(site_id, "site_id")
    claimed_site_id = _identifier(claimed_site_id or site_id, "claimed_site_id")
    actor = _identifier(actor, "actor")
    reason = _reason(reason)
    requested_observed = now.astimezone(UTC) if now is not None else None
    raw = _read_bounded(grant_path, label="grant")
    raw_digest = sha256_bytes(raw)
    lock_path = state_path.parent / ".transaction.lock"
    with transaction_lock(lock_path):
        operation_path = _operation_path(state_path, operation_id)
        operation_before_time_check = _read_operation(operation_path)
        if operation_before_time_check is None:
            _prune_operations_locked(state_path, operation_id)
        try:
            observed = _observe_time_locked(
                time_state_path or _last_seen_path(state_path),
                requested_observed or datetime.now(UTC),
                update=True,
            )
        except EntitlementError as exc:
            time_failure_identity = _untrusted_identity(
                operation_id=operation_id,
                actor=actor,
                site_id=site_id,
                reason=reason,
                raw_digest=raw_digest,
            )
            _append_audit_locked(
                audit_path, _audit_record(time_failure_identity, "rejected", str(exc))
            )
            if operation_before_time_check is None:
                _write_operation(operation_path, time_failure_identity, "rejected", str(exc))
            raise
        _recover_or_raise_uncertain(state_path, audit_path)
        existing_operation = _read_operation(operation_path)
        try:
            if claimed_site_id != site_id:
                raise EntitlementError("site_mismatch")
            grant = _parse_canonical_document(raw, label="grant")
            key, trusted_key_id = _public_key(public_key_path)
            payload, digest = verify_grant(
                grant,
                key,
                trusted_key_id,
                expected_site_id=site_id,
                now=observed,
                for_install=True,
                artifact_bytes=raw,
            )
            identity = _identity(
                operation_id=operation_id,
                actor=actor,
                site_id=site_id,
                reason=reason,
                grant_id=str(payload["grant_id"]),
                grant_sha256=digest,
                serial=int(payload["serial"]),
            )
            recorded_result = _resolve_existing_operation(
                existing_operation, identity, payload, audit_path, state_path
            )
            if recorded_result is not None:
                return recorded_result
            existing_operation = None
            old_raw = (
                _read_bounded(state_path, label="current_grant") if state_path.exists() else b""
            )
            if old_raw:
                old_grant = _parse_canonical_document(old_raw, label="current_grant")
                old_payload, old_digest = verify_grant(
                    old_grant,
                    key,
                    trusted_key_id,
                    expected_site_id=site_id,
                    now=observed,
                    artifact_bytes=old_raw,
                )
                if (
                    payload["grant_id"] == old_payload["grant_id"]
                    or digest == old_digest
                    or int(payload["serial"]) <= int(old_payload["serial"])
                    or _timestamp(payload["expires_at"], "expires_at")
                    <= _timestamp(old_payload["expires_at"], "expires_at")
                ):
                    raise EntitlementError("replay_rejected")
            _write_operation(operation_path, identity, "executing")
            if fault_after_operation_write:
                raise RuntimeError("injected_crash_after_operation_write")
            journal = {
                "schema_version": 1,
                "phase": "prepared",
                "identity": identity,
                "new_state_b64": base64.b64encode(raw).decode("ascii"),
                "old_state_b64": base64.b64encode(old_raw).decode("ascii"),
                "old_state_present": bool(old_raw),
            }
            _prepare_audit_append_locked(audit_path, _audit_record(identity, "installed"))
            _atomic_replace(_journal_path(state_path), canonical_artifact_bytes(journal))
            _atomic_replace(state_path, raw)
            journal["phase"] = "state_replaced"
            _atomic_replace(_journal_path(state_path), canonical_artifact_bytes(journal))
            if fault_after_state_replace:
                raise RuntimeError("injected_crash_after_state_replace")
            _append_audit_locked(audit_path, _audit_record(identity, "installed"))
            _write_operation(operation_path, identity, "installed")
            _journal_path(state_path).unlink()
            return _install_receipt(identity, payload, idempotent=False)
        except EntitlementError as exc:
            failure_identity: object = locals().get("identity")
            if not isinstance(failure_identity, dict):
                failure_identity = _untrusted_identity(
                    operation_id=operation_id,
                    actor=actor,
                    site_id=site_id,
                    reason=reason,
                    raw_digest=raw_digest,
                )
            if _journal_path(state_path).exists():
                raise EntitlementError("transaction_uncertain") from exc
            _append_audit_locked(audit_path, _audit_record(failure_identity, "rejected", str(exc)))
            if existing_operation is None:
                _write_operation(operation_path, failure_identity, "rejected", str(exc))
            raise


def _install_receipt(
    identity: Mapping[str, Any], payload: Mapping[str, Any], *, idempotent: bool
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "ok": True,
        "status": "installed",
        "idempotent": idempotent,
        "operation_id": identity["operation_id"],
        "site_id": identity["site_id"],
        "grant_id": identity["grant_id"],
        "grant_sha256": identity["grant_sha256"],
        "serial": identity["serial"],
        "starts_at": payload["starts_at"],
        "expires_at": payload["expires_at"],
        "grace_until": payload["grace_until"],
        "safety_preserved": True,
        "collection_preserved": True,
        "alarms_preserved": True,
        "data_preserved": True,
    }


def status_grant(
    *,
    state_path: Path,
    public_key_path: Path,
    site_id: str,
    now: datetime | None = None,
    time_state_path: Path | None = None,
) -> dict[str, Any]:
    site_id = _identifier(site_id, "site_id")
    requested_observed = now.astimezone(UTC) if now is not None else None
    with transaction_lock(state_path.parent / ".transaction.lock"):
        try:
            observed = _observe_time_locked(
                time_state_path or _last_seen_path(state_path),
                requested_observed or datetime.now(UTC),
                update=True,
            )
        except EntitlementError:
            return _status_receipt("uncertain", site_id)
        if _journal_path(state_path).exists():
            return _status_receipt("uncertain", site_id)
        if not state_path.exists():
            return _status_receipt("missing", site_id)
        payload, digest = verify_grant_file(
            state_path, public_key_path, expected_site_id=site_id, now=observed
        )
    starts = _timestamp(payload["starts_at"], "starts_at")
    expires = _timestamp(payload["expires_at"], "expires_at")
    grace = _timestamp(payload["grace_until"], "grace_until")
    if observed < starts:
        status = "pending"
    elif observed < expires:
        status = "active"
    elif observed < grace:
        status = "grace"
    else:
        status = "expired"
    return {
        **_status_receipt(status, site_id),
        "grant_id": payload["grant_id"],
        "grant_sha256": digest,
        "serial": payload["serial"],
        "starts_at": payload["starts_at"],
        "expires_at": payload["expires_at"],
        "grace_until": payload["grace_until"],
        "features": payload["features"],
    }


def _status_receipt(status: str, site_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "ok": status not in {"uncertain"},
        "status": status,
        "site_id": site_id,
        "entitlement_dependent": status in {"grace", "expired"},
        "features": [],
        "safety_preserved": True,
        "collection_preserved": True,
        "alarms_preserved": True,
        "data_preserved": True,
    }


def _password_from_stdin(confirm: bool = False) -> bytes:
    if sys.stdin.isatty():
        first = getpass.getpass("Entitlement key password: ").encode("utf-8")
        if confirm and first != getpass.getpass("Confirm password: ").encode("utf-8"):
            raise EntitlementError("password_confirmation_mismatch")
        return first
    first = sys.stdin.buffer.readline(MAX_PASSWORD_BYTES + 1)
    if len(first) > MAX_PASSWORD_BYTES:
        raise EntitlementError("password_too_long")
    return first.rstrip(b"\r\n")


def _parse_datetime(value: str, *, shanghai_date: bool = False) -> datetime:
    if shanghai_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            year, month, day = (int(part) for part in value.split("-"))
            return datetime(year, month, day, tzinfo=timezone(timedelta(hours=8))).astimezone(UTC)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("timestamp must be a valid date") from exc
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp requires timezone")
    return parsed.astimezone(UTC)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    keygen = sub.add_parser("keygen")
    keygen.add_argument("--private-key", type=Path, required=True)
    keygen.add_argument("--public-key", type=Path, required=True)
    keygen.add_argument("--key-id", required=True)
    keygen.add_argument("--password-stdin", action="store_true", required=True)
    issue = sub.add_parser("issue")
    issue.add_argument("--private-key", type=Path, required=True)
    issue.add_argument("--key-id", required=True)
    issue.add_argument("--site-id", required=True)
    issue.add_argument("--customer-id", required=True)
    issue.add_argument("--plan", required=True)
    issue.add_argument("--features", nargs="+", required=True)
    issue.add_argument("--serial", type=int, required=True)
    issue.add_argument(
        "--start", type=lambda value: _parse_datetime(value, shanghai_date=True), required=True
    )
    issue.add_argument(
        "--end", type=lambda value: _parse_datetime(value, shanghai_date=True), required=True
    )
    issue.add_argument("--issued", type=_parse_datetime)
    issue.add_argument("--grace-days", type=int, default=7)
    issue.add_argument("--grant-id")
    issue.add_argument("--output", type=Path, required=True)
    issue.add_argument("--password-stdin", action="store_true", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--grant", type=Path, required=True)
    verify.add_argument("--public-key", type=Path, required=True)
    verify.add_argument("--site-id")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--grant", type=Path, required=True)
    status = sub.add_parser("status")
    status.add_argument("--state", type=Path, required=True)
    status.add_argument("--public-key", type=Path, required=True)
    status.add_argument("--site-id", required=True)
    status.add_argument("--time-state", type=Path)
    install = sub.add_parser("install")
    install.add_argument("--grant", type=Path, required=True)
    install.add_argument("--public-key", type=Path, required=True)
    install.add_argument("--state", type=Path, required=True)
    install.add_argument("--audit", type=Path, required=True)
    install.add_argument("--site-id", required=True)
    install.add_argument("--claimed-site-id")
    install.add_argument("--operation-id", required=True)
    install.add_argument("--reason", required=True)
    install.add_argument("--actor", default="operator")
    install.add_argument("--time-state", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0915
    args = _parser().parse_args(argv)
    try:
        if args.command == "keygen":
            key_id = _identifier(args.key_id, "key_id")
            password = _password_from_stdin(confirm=True)
            if not password:
                raise EntitlementError("private_key_password_required")
            private = Ed25519PrivateKey.generate()
            private_bytes = private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(password),
            )
            public_bytes = (
                private.public_key().public_bytes(
                    serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
                )
                + b" "
                + key_id.encode("ascii")
                + b"\n"
            )
            if args.private_key.exists() or args.public_key.exists():
                raise EntitlementError("output_exists")
            _require_protected_private_key_parent(args.private_key)
            _create_file_no_clobber(args.private_key, private_bytes)
            try:
                _set_protected_private_key_acl(args.private_key)
                _require_protected_private_key_leaf(args.private_key)
                _create_file_no_clobber(args.public_key, public_bytes, 0o644)
            except Exception:
                with suppress(FileNotFoundError):
                    args.private_key.unlink()
                raise
            result: dict[str, Any] = {"status": "generated", "key_id": key_id}
        elif args.command == "issue":
            password = _password_from_stdin()
            _require_protected_private_key_parent(args.private_key)
            _require_protected_private_key_leaf(args.private_key)
            features = [part for item in args.features for part in item.split(",") if part]
            grant = issue_grant(
                private_key_path=args.private_key,
                private_key_password=password,
                key_id=args.key_id,
                site_id=args.site_id,
                customer_id=args.customer_id,
                plan=args.plan,
                features=features,
                serial=args.serial,
                start=args.start,
                end=args.end,
                issued=args.issued,
                grace_days=args.grace_days,
                grant_id=args.grant_id,
            )
            data = canonical_artifact_bytes(grant)
            _create_file_no_clobber(args.output, data)
            result = {
                "status": "issued",
                "grant_id": grant["grant_id"],
                "site_id": grant["site_id"],
                "serial": grant["serial"],
                "grant_sha256": sha256_bytes(data),
            }
        elif args.command == "inspect":
            grant, data = load_grant(args.grant)
            payload = validate_payload(_payload(grant))
            result = {
                "schema_version": 1,
                "ok": True,
                "status": "inspected",
                "site_id": payload["site_id"],
                "grant_id": payload["grant_id"],
                "grant_sha256": sha256_bytes(data),
                "serial": payload["serial"],
                "starts_at": payload["starts_at"],
                "expires_at": payload["expires_at"],
                "grace_until": payload["grace_until"],
            }
        elif args.command == "verify":
            payload, digest = verify_grant_file(
                args.grant, args.public_key, expected_site_id=args.site_id
            )
            result = {"status": "verified", "grant": payload, "grant_sha256": digest}
        elif args.command == "status":
            result = status_grant(
                state_path=args.state,
                public_key_path=args.public_key,
                site_id=args.site_id,
                time_state_path=args.time_state,
            )
        else:
            result = install_grant(
                grant_path=args.grant,
                public_key_path=args.public_key,
                state_path=args.state,
                audit_path=args.audit,
                site_id=args.site_id,
                operation_id=args.operation_id,
                reason=args.reason,
                actor=args.actor,
                claimed_site_id=args.claimed_site_id,
                time_state_path=args.time_state,
            )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except EntitlementError as exc:
        status = "uncertain" if str(exc) == "transaction_uncertain" else "rejected"
        print(json.dumps({"status": status, "error_code": str(exc)}, separators=(",", ":")))
        return 2
    except Exception:
        status = "uncertain" if args.command == "install" else "rejected"
        print(json.dumps({"status": status, "error_code": "internal_error"}, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
