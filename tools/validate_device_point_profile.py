"""Read-only, fail-closed eligibility gate for versioned device point profiles."""

from __future__ import annotations

import argparse
import base64
import binascii
import ctypes
import hashlib
import importlib
import json
import math
import ntpath
import os
import re
import stat
import struct
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

SCHEMA_VERSION = 1
ARTIFACT_TYPE = "ruisheng.device-point-profile"
SEMANTIC_VALIDATOR_ID = "ruisheng.device-point-profile-validator/v5"
VALIDATOR_SOURCE_LOGICAL_PATH = "tools/validate_device_point_profile.py"
MAX_PROFILE_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_PROFILE_POINTS = 1024
MAX_EVIDENCE_BINDINGS = 4096
MAX_EVIDENCE_BYTES = 256 * 1024 * 1024
MAX_BOUND_ARTIFACT_BYTES = 256 * 1024 * 1024
FIXED_POLICY_TRUST_ROOT_PATH = (
    Path(r"C:\ProgramData\Ruisheng\trust\point-profile-policy-root.json")
    if os.name == "nt"
    else Path("/etc/ruisheng/trust/point-profile-policy-root.json")
)
FIXED_FRESHNESS_PROVIDER_CONFIG_PATH = (
    Path(r"C:\ProgramData\Ruisheng\trust\point-profile-freshness-provider.json")
    if os.name == "nt"
    else Path("/etc/ruisheng/trust/point-profile-freshness-provider.json")
)
CONTROL_CHARACTER_LIMIT = 32
UNICODE_SURROGATE_MIN = 0xD800
UNICODE_SURROGATE_MAX = 0xDFFF
MIN_BINARY_ADDRESS_CANDIDATES = 2
MODBUS_FC_DISCRETE_INPUTS = 2
MIN_CALIBRATION_SAMPLES_PER_STATE = 3
COUNTER_STATE_SEQUENCE = ("BASELINE", "INCREMENT", "ROLLOVER", "PERSISTENCE")
MIN_COUNTER_OBSERVATIONS = len(COUNTER_STATE_SEQUENCE) * MIN_CALIBRATION_SAMPLES_PER_STATE
MODBUS_ADDRESS_SPACE = 65536
MIN_MODBUS_RTU_FRAME_BYTES = 4
MAX_BCD_DIGIT = 9
SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
BASE64_PATTERN = re.compile(r"[A-Za-z0-9+/]+={0,2}\Z")
OPENSSH_FINGERPRINT_PATTERN = re.compile(r"SHA256:[A-Za-z0-9+/]{43}\Z")
RTU_HEX_PATTERN = re.compile(r"[0-9a-f]+\Z")
WINDOWS_USB_INTERFACE_PATTERN = re.compile(
    r"\\\\\?\\usb#"
    r"(?P<hardware_id>vid_[0-9a-f]{4}&pid_[0-9a-f]{4}(?:&[a-z0-9_.-]+)*)"
    r"#(?P<serial>[a-z0-9_.&-]+)"
    r"#\{[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}",
    flags=re.IGNORECASE,
)
RELEASE_RECEIPT_SIGNATURE_NAMESPACE = "ruisheng-release-verification-receipt-v1"
SSHSIG_MAGIC = b"SSHSIG"
SSHSIG_VERSION = 1
SSHSIG_HASH_ALGORITHM = b"sha512"
SSHSIG_KEY_TYPE = b"ssh-ed25519"
SSH_STRING_LENGTH_BYTES = 4
ED25519_PUBLIC_KEY_BYTES = 32
ED25519_SIGNATURE_BYTES = 64
MIN_SSHSIG_PAYLOAD_BYTES = 128


FORBIDDEN_DERIVED_KEYS = frozenset(
    {"deployment_eligible", "deployable", "direct_import_allowed", "eligible", "eligibility"}
)

ResolutionStatus = Literal["unknown", "candidate", "ambiguous", "resolved"]
CalibrationStatus = Literal["unknown", "provisional", "ambiguous", "resolved"]
ImplementationStatus = Literal["unknown", "unsupported", "supported"]
ContradictionStatus = Literal["open", "resolved"]
Decision = Literal["ELIGIBLE", "BLOCKED", "INVALID"]
RuntimeCheckId = Literal[
    "STRICT_VALUE_TYPE_VALIDATION",
    "SIGNED_DECODE_BOUNDARIES",
    "FC1_ADDRESS_TRANSLATION",
    "FC2_ADDRESS_TRANSLATION",
    "ATOMIC_DISABLED_ONBOARDING",
    "SHARED_SERIAL_LOCK",
    "PROFILE_DRY_RUN",
    "RUNTIME_BUILD_BINDING",
]

BASE_RUNTIME_CHECKS: frozenset[str] = frozenset(
    {
        "STRICT_VALUE_TYPE_VALIDATION",
        "ATOMIC_DISABLED_ONBOARDING",
        "SHARED_SERIAL_LOCK",
        "PROFILE_DRY_RUN",
        "RUNTIME_BUILD_BINDING",
    }
)
REQUIRED_APPROVAL_ROLES = frozenset(
    {"project_owner", "device_firmware_owner", "site_safety_owner", "test_owner"}
)
REQUIRED_RELEASE_RECEIPT_CHECKS = frozenset(
    {
        "OPENSSH_SSHSIG_VERIFIED",
        "SHA256SUMS_ALLOWLIST_VERIFIED",
        "PACKAGE_HASHES_VERIFIED",
        "MANIFEST_VERIFIED",
        "ARCHIVE_IDENTITIES_VERIFIED",
        "MIGRATION_HEAD_VERIFIED",
    }
)
RELEASE_RECEIPT_CHECK_ORDER = (
    "OPENSSH_SSHSIG_VERIFIED",
    "SHA256SUMS_ALLOWLIST_VERIFIED",
    "PACKAGE_HASHES_VERIFIED",
    "MANIFEST_VERIFIED",
    "ARCHIVE_IDENTITIES_VERIFIED",
    "MIGRATION_HEAD_VERIFIED",
)
REQUIRED_RUNTIME_ASSERTIONS: dict[str, frozenset[str]] = {
    "STRICT_VALUE_TYPE_VALIDATION": frozenset(
        {"UNKNOWN_VALUES_REJECTED", "VALUE_TYPE_ROUND_TRIP_EXACT"}
    ),
    "SIGNED_DECODE_BOUNDARIES": frozenset({"S16_BOUNDARIES", "S32_BOUNDARIES"}),
    "FC1_ADDRESS_TRANSLATION": frozenset({"COIL_ADDRESS_TRANSLATION"}),
    "FC2_ADDRESS_TRANSLATION": frozenset({"DISCRETE_INPUT_ADDRESS_TRANSLATION"}),
    "ATOMIC_DISABLED_ONBOARDING": frozenset(
        {
            "DISABLED_BEFORE_VISIBLE",
            "FAILURE_ROLLS_BACK",
            "TENANT_ISOLATION",
            "CONCURRENT_IDEMPOTENCY",
        }
    ),
    "SHARED_SERIAL_LOCK": frozenset(
        {"LOCK_BEFORE_SERIAL_OPEN", "STALE_LOCK_RECOVERY", "DUAL_PROCESS_CONTENTION"}
    ),
    "PROFILE_DRY_RUN": frozenset({"EXACT_DIFF", "NO_SIDE_EFFECTS", "ROLLBACK_PLAN"}),
    "RUNTIME_BUILD_BINDING": frozenset(
        {
            "SOURCE_COMMIT_MATCH",
            "API_IMAGE_MATCH",
            "GATEWAY_IMAGE_MATCH",
            "ALEMBIC_HEAD_MATCH",
            "RELEASE_RECEIPT_MATCH",
        }
    ),
}

STRICT_CONFIG = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)
Sha256Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
RepoRelativePath = Annotated[
    str,
    Field(
        min_length=1,
        json_schema_extra={
            "x-semantic-validation": "normalized POSIX repository-relative path; no symlinks/reparse points",
        },
    ),
]
AwareTimestamp = Annotated[str, Field(json_schema_extra={"format": "date-time"})]


def _validate_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except (OverflowError, ValueError) as error:
        raise ValueError("timestamp must be ISO-8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone offset")
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("timestamp must use the canonical UTC offset")
    canonical = parsed.isoformat(timespec="microseconds" if parsed.microsecond else "seconds")
    if value != canonical:
        raise ValueError("timestamp must use canonical ISO-8601 UTC text")
    return value


_EXECUTING_VALIDATOR_SOURCE_SHA256: str | None = None


def current_validator_source_sha256() -> str:
    """Return the digest of the exact validator source executing this policy."""
    if _EXECUTING_VALIDATOR_SOURCE_SHA256 is None:
        raise RuntimeError("validator source identity was not captured during module loading")
    return _EXECUTING_VALIDATOR_SOURCE_SHA256


def _validate_sha256(value: str) -> str:
    if SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError("digest must be lowercase sha256:<64 hex>")
    return value


def _validate_repo_path(value: str) -> str:
    if (
        not value
        or "\\" in value
        or ":" in value
        or any(ord(char) < CONTROL_CHARACTER_LIMIT for char in value)
    ):
        raise ValueError("path must be a non-empty POSIX repository-relative path")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or parsed.parts[0].endswith(":"):
        raise ValueError("absolute paths are forbidden")
    if any(part in {"", ".", ".."} for part in parsed.parts) or parsed.as_posix() != value:
        raise ValueError("path must be normalized and cannot contain dot segments")
    return value


def _validate_nonblank(value: str) -> str:
    if not value.strip() or any(
        UNICODE_SURROGATE_MIN <= ord(char) <= UNICODE_SURROGATE_MAX for char in value
    ):
        raise ValueError("value must be non-blank Unicode text without surrogates")
    return value


def _stable_serial_device_platform(value: str) -> Literal["posix", "windows"]:
    _validate_nonblank(value)
    if any(ord(char) < CONTROL_CHARACTER_LIMIT for char in value):
        raise ValueError("stable serial device path cannot contain control characters")
    posix_prefix = "/dev/serial/by-id/"
    if value.startswith(posix_prefix):
        basename = value.removeprefix(posix_prefix)
        if (
            not basename.startswith("usb-")
            or not basename.removeprefix("usb-").strip()
            or "/" in basename
            or "\\" in basename
        ):
            raise ValueError("POSIX stable serial path requires one USB by-id basename")
        return "posix"
    if WINDOWS_USB_INTERFACE_PATTERN.fullmatch(value) is not None:
        return "windows"
    raise ValueError("line protocol requires a stable USB device path")


def _validate_stable_serial_device_path(value: str) -> str:
    _stable_serial_device_platform(value)
    return value


def _finite_number(value: int | float, *, label: str) -> int | float:
    if isinstance(value, bool):
        raise ValueError(f"{label} cannot be boolean")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


def _validate_base64(value: str, *, expected_size: int, label: str) -> str:
    if BASE64_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be canonical base64")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(f"{label} must be canonical base64") from error
    if len(decoded) != expected_size or base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError(f"{label} has the wrong size or non-canonical encoding")
    return value


def _reject_unicode_surrogates(value: Any) -> None:
    if isinstance(value, str):
        if any(UNICODE_SURROGATE_MIN <= ord(char) <= UNICODE_SURROGATE_MAX for char in value):
            raise ValueError("Unicode surrogate code points are forbidden")
    elif isinstance(value, Mapping):
        for key, child in value.items():
            _reject_unicode_surrogates(key)
            _reject_unicode_surrogates(child)
    elif isinstance(value, list | tuple):
        for child in value:
            _reject_unicode_surrogates(child)


def _reject_non_finite_numbers(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite numbers are forbidden")
    if isinstance(value, Mapping):
        for key, child in value.items():
            _reject_non_finite_numbers(key)
            _reject_non_finite_numbers(child)
    elif isinstance(value, list | tuple):
        for child in value:
            _reject_non_finite_numbers(child)


def canonical_json_bytes(value: Any) -> bytes:
    _reject_unicode_surrogates(value)
    _reject_non_finite_numbers(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class StrictModel(BaseModel):
    model_config = STRICT_CONFIG


class ArtifactBinding(StrictModel):
    path: RepoRelativePath
    sha256: Sha256Digest
    size_bytes: int = Field(ge=0, le=MAX_ARTIFACT_BYTES)

    _path = field_validator("path")(_validate_repo_path)
    _sha256 = field_validator("sha256")(_validate_sha256)


EvidenceRole = Literal[
    "legacy_source",
    "raw_observation",
    "identity",
    "authoritative_map",
    "calibration",
    "reference",
    "contradiction_resolution",
    "line_protocol",
]

ApprovalRole = Literal["project_owner", "device_firmware_owner", "site_safety_owner", "test_owner"]


class DetachedSignature(StrictModel):
    algorithm: Literal["Ed25519"]
    key_id: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=88, max_length=88)

    _key = field_validator("key_id")(_validate_nonblank)

    @field_validator("value")
    @classmethod
    def valid_signature(cls, value: str) -> str:
        return _validate_base64(value, expected_size=64, label="Ed25519 signature")


class OpenSshDetachedSignature(StrictModel):
    algorithm: Literal["OpenSSH-SSHSIG-Ed25519"]
    key_id: str = Field(min_length=1, max_length=128)
    namespace: Literal["ruisheng-release-verification-receipt-v1"]
    value: str = Field(min_length=200, max_length=2048)

    _key = field_validator("key_id")(_validate_nonblank)

    @field_validator("value")
    @classmethod
    def valid_signature(cls, value: str) -> str:
        if BASE64_PATTERN.fullmatch(value) is None:
            raise ValueError("OpenSSH SSHSIG must be canonical base64")
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("OpenSSH SSHSIG must be canonical base64") from error
        if (
            len(decoded) < MIN_SSHSIG_PAYLOAD_BYTES
            or not decoded.startswith(SSHSIG_MAGIC)
            or base64.b64encode(decoded).decode("ascii") != value
        ):
            raise ValueError("OpenSSH SSHSIG payload is invalid")
        return value


class TimeBoundTrustKey(StrictModel):
    valid_from: AwareTimestamp
    expires_at: AwareTimestamp
    revocation_sequence: int = Field(ge=0)
    status: Literal["active", "revoked"]

    _valid_from = field_validator("valid_from")(_validate_timestamp)
    _expires_at = field_validator("expires_at")(_validate_timestamp)

    @model_validator(mode="after")
    def nonempty_validity_window(self) -> TimeBoundTrustKey:
        if datetime.fromisoformat(self.valid_from) >= datetime.fromisoformat(self.expires_at):
            raise ValueError("trust-key validity window is empty")
        return self


class ApprovalTrustKey(TimeBoundTrustKey):
    role: ApprovalRole
    key_id: str = Field(min_length=1, max_length=128)
    identity: str = Field(min_length=1, max_length=256)
    public_key: str = Field(min_length=44, max_length=44)

    _key = field_validator("key_id")(_validate_nonblank)
    _identity = field_validator("identity")(_validate_nonblank)

    @field_validator("public_key")
    @classmethod
    def valid_public_key(cls, value: str) -> str:
        return _validate_base64(value, expected_size=32, label="Ed25519 public key")


class EvidenceTrustKey(TimeBoundTrustKey):
    attestor_id: str = Field(min_length=1, max_length=128)
    key_id: str = Field(min_length=1, max_length=128)
    public_key: str = Field(min_length=44, max_length=44)
    roles: list[EvidenceRole] = Field(min_length=1)

    _attestor = field_validator("attestor_id")(_validate_nonblank)
    _key = field_validator("key_id")(_validate_nonblank)

    @field_validator("public_key")
    @classmethod
    def valid_public_key(cls, value: str) -> str:
        return _validate_base64(value, expected_size=32, label="Ed25519 public key")

    @model_validator(mode="after")
    def valid_roles(self) -> EvidenceTrustKey:
        if len(self.roles) != len(set(self.roles)):
            raise ValueError("evidence trust roles must be unique")
        if any(role in {"legacy_source", "runtime"} for role in self.roles):
            raise ValueError("legacy/runtime roles cannot be evidence attestation roles")
        return self


class RuntimeRunnerTrustKey(TimeBoundTrustKey):
    runner_id: str = Field(min_length=1, max_length=128)
    key_id: str = Field(min_length=1, max_length=128)
    public_key: str = Field(min_length=44, max_length=44)
    tool_id: str = Field(min_length=1, max_length=256)
    tool_sha256: Sha256Digest

    _runner = field_validator("runner_id")(_validate_nonblank)
    _key = field_validator("key_id")(_validate_nonblank)
    _tool = field_validator("tool_id")(_validate_nonblank)
    _tool_hash = field_validator("tool_sha256")(_validate_sha256)

    @field_validator("public_key")
    @classmethod
    def valid_public_key(cls, value: str) -> str:
        return _validate_base64(value, expected_size=32, label="Ed25519 public key")


class ReleaseVerifierTrustKey(TimeBoundTrustKey):
    verifier_id: str = Field(min_length=1, max_length=128)
    key_id: str = Field(min_length=1, max_length=128)
    public_key: str = Field(min_length=44, max_length=44)
    tool_id: str = Field(min_length=1, max_length=256)
    tool_sha256: Sha256Digest
    publisher_key_fingerprints: list[str] = Field(min_length=1)

    _verifier = field_validator("verifier_id")(_validate_nonblank)
    _key = field_validator("key_id")(_validate_nonblank)
    _tool = field_validator("tool_id")(_validate_nonblank)
    _tool_hash = field_validator("tool_sha256")(_validate_sha256)

    @field_validator("public_key")
    @classmethod
    def valid_public_key(cls, value: str) -> str:
        return _validate_base64(value, expected_size=32, label="Ed25519 public key")

    @field_validator("publisher_key_fingerprints")
    @classmethod
    def valid_publisher_fingerprints(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("release publisher fingerprints must be unique")
        if any(OPENSSH_FINGERPRINT_PATTERN.fullmatch(value) is None for value in values):
            raise ValueError("release publisher fingerprint is invalid")
        return values


class PolicyAuthorization(StrictModel):
    policy_id: str = Field(min_length=1, max_length=128)
    policy_version: int = Field(ge=1)
    policy_sha256: Sha256Digest
    revocation_sequence: int = Field(ge=0)
    status: Literal["active", "revoked"]

    _policy = field_validator("policy_id")(_validate_nonblank)
    _policy_hash = field_validator("policy_sha256")(_validate_sha256)


class PolicyTrustRoot(StrictModel):
    schema_version: Literal[1]
    artifact_type: Literal["ruisheng.device-point-profile-policy-trust-root"]
    root_id: str = Field(min_length=1, max_length=128)
    root_version: int = Field(ge=1)
    authority_id: str = Field(min_length=1, max_length=128)
    key_id: str = Field(min_length=1, max_length=128)
    public_key: str = Field(min_length=44, max_length=44)
    valid_from: AwareTimestamp
    expires_at: AwareTimestamp
    revocation_sequence: int = Field(ge=0)
    status: Literal["active", "revoked"]
    authorized_policies: list[PolicyAuthorization] = Field(min_length=1)

    _root = field_validator("root_id")(_validate_nonblank)
    _authority = field_validator("authority_id")(_validate_nonblank)
    _key = field_validator("key_id")(_validate_nonblank)
    _valid_from = field_validator("valid_from")(_validate_timestamp)
    _expires_at = field_validator("expires_at")(_validate_timestamp)

    @field_validator("public_key")
    @classmethod
    def valid_public_key(cls, value: str) -> str:
        return _validate_base64(value, expected_size=32, label="policy authority public key")

    @model_validator(mode="after")
    def unique_policy_ids(self) -> PolicyTrustRoot:
        identities = [
            (authorization.policy_id, authorization.policy_version)
            for authorization in self.authorized_policies
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("trust-root policy authorizations must be unique")
        if datetime.fromisoformat(self.valid_from) >= datetime.fromisoformat(self.expires_at):
            raise ValueError("trust-root validity window is empty")
        return self


class TrustPolicy(StrictModel):
    schema_version: Literal[1]
    artifact_type: Literal["ruisheng.device-point-profile-trust-policy"]
    policy_id: str = Field(min_length=1, max_length=128)
    policy_version: int = Field(ge=1)
    semantic_validator: Literal["ruisheng.device-point-profile-validator/v5"]
    validator_source_sha256: Sha256Digest
    authority_id: str = Field(min_length=1, max_length=128)
    valid_from: AwareTimestamp
    expires_at: AwareTimestamp
    revocation_sequence: int = Field(ge=0)
    status: Literal["active", "revoked"]
    approval_keys: list[ApprovalTrustKey] = Field(min_length=4)
    evidence_keys: list[EvidenceTrustKey] = Field(min_length=1)
    runtime_runner_keys: list[RuntimeRunnerTrustKey] = Field(min_length=1)
    release_verifier_keys: list[ReleaseVerifierTrustKey] = Field(min_length=1)
    authority_signature: DetachedSignature

    _policy = field_validator("policy_id")(_validate_nonblank)
    _authority = field_validator("authority_id")(_validate_nonblank)
    _validator_source_hash = field_validator("validator_source_sha256")(_validate_sha256)
    _valid_from = field_validator("valid_from")(_validate_timestamp)
    _expires_at = field_validator("expires_at")(_validate_timestamp)

    @model_validator(mode="after")
    def unique_trust_keys(self) -> TrustPolicy:
        approval_ids = [(key.role, key.key_id) for key in self.approval_keys]
        evidence_ids = [(key.attestor_id, key.key_id) for key in self.evidence_keys]
        runner_ids = [(key.runner_id, key.key_id) for key in self.runtime_runner_keys]
        release_ids = [(key.verifier_id, key.key_id) for key in self.release_verifier_keys]
        for values, label in (
            (approval_ids, "approval keys"),
            (evidence_ids, "evidence keys"),
            (runner_ids, "runtime runner keys"),
            (release_ids, "release manifest keys"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")
        if {key.role for key in self.approval_keys} != REQUIRED_APPROVAL_ROLES:
            raise ValueError("trust policy must contain all four approval roles")
        approval_public_keys = [key.public_key for key in self.approval_keys]
        approval_identities = [key.identity for key in self.approval_keys]
        if len(approval_public_keys) != len(set(approval_public_keys)):
            raise ValueError("approval roles must use distinct public keys")
        if len(approval_identities) != len(set(approval_identities)):
            raise ValueError("approval roles must use distinct identities")
        evidence_public_keys = [key.public_key for key in self.evidence_keys]
        evidence_attestors = [key.attestor_id for key in self.evidence_keys]
        if len(evidence_public_keys) != len(set(evidence_public_keys)):
            raise ValueError("evidence attestors must use distinct public keys")
        if len(evidence_attestors) != len(set(evidence_attestors)):
            raise ValueError("evidence attestor identities must be unique")
        runner_public_keys = [key.public_key for key in self.runtime_runner_keys]
        if len(runner_public_keys) != len(set(runner_public_keys)):
            raise ValueError("runtime runners must use distinct public keys")
        release_public_keys = [key.public_key for key in self.release_verifier_keys]
        if len(release_public_keys) != len(set(release_public_keys)):
            raise ValueError("release verifiers must use distinct public keys")
        if datetime.fromisoformat(self.valid_from) >= datetime.fromisoformat(self.expires_at):
            raise ValueError("trust-policy validity window is empty")
        return self


def trust_policy_sha256(policy: TrustPolicy | Mapping[str, Any]) -> str:
    value = policy.model_dump(mode="json") if isinstance(policy, TrustPolicy) else policy
    return _canonical_sha256(value)


def trust_root_sha256(root: PolicyTrustRoot | Mapping[str, Any]) -> str:
    value = root.model_dump(mode="json") if isinstance(root, PolicyTrustRoot) else root
    return _canonical_sha256(value)


def trust_policy_signature_message(policy: TrustPolicy | Mapping[str, Any]) -> bytes:
    document = policy.model_dump(mode="json") if isinstance(policy, TrustPolicy) else dict(policy)
    document.pop("authority_signature", None)
    return b"ruisheng.device-point-profile.trust-policy/v1\0" + canonical_json_bytes(document)


class EvidenceBinding(ArtifactBinding):
    evidence_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    role: EvidenceRole
    media_type: Literal["application/json", "application/jsonl", "application/octet-stream"]
    subject_point_ids: list[str] = Field(default_factory=list)


class RawDomain(StrictModel):
    minimum: int | float
    maximum: int | float

    @model_validator(mode="after")
    def valid_range(self) -> RawDomain:
        _finite_number(self.minimum, label="raw-domain minimum")
        _finite_number(self.maximum, label="raw-domain maximum")
        if self.minimum > self.maximum:
            raise ValueError("raw-domain minimum must not exceed maximum")
        return self


class EngineeringMapping(StrictModel):
    ratio: int | float
    offset: int | float

    @model_validator(mode="after")
    def finite_values(self) -> EngineeringMapping:
        _finite_number(self.ratio, label="mapping ratio")
        _finite_number(self.offset, label="mapping offset")
        if self.ratio == 0:
            raise ValueError("mapping ratio cannot be zero")
        return self


class UnknownCalibrationProfile(StrictModel):
    kind: Literal["unknown"]
    method: Literal["unknown"]


class AnalogCalibrationProfile(StrictModel):
    kind: Literal["analog"]
    method: Literal["affine_holdout_return"]
    engineering_mapping: EngineeringMapping | None = None


class BinaryCalibrationProfile(StrictModel):
    kind: Literal["binary"]
    method: Literal["state_transition"]
    inactive_raw: int | float | None = None
    active_raw: int | float | None = None

    @model_validator(mode="after")
    def finite_distinct_states(self) -> BinaryCalibrationProfile:
        values = (self.inactive_raw, self.active_raw)
        for value in values:
            if value is not None:
                _finite_number(value, label="binary calibration state")
        if None not in values and self.inactive_raw == self.active_raw:
            raise ValueError("binary calibration states must be distinct")
        return self


class CounterCalibrationProfile(StrictModel):
    kind: Literal["counter"]
    method: Literal["monotonicity_rollover"]
    counts_per_unit: int | float | None = None
    modulus: int | None = Field(default=None, gt=1)
    rollover_behavior: Literal["unknown", "wrap", "saturate", "reset"] = "unknown"

    @field_validator("counts_per_unit")
    @classmethod
    def positive_finite_scale(cls, value: int | float | None) -> int | float | None:
        if value is not None:
            _finite_number(value, label="counter counts_per_unit")
        if value is not None and value <= 0:
            raise ValueError("counter counts_per_unit must be positive")
        return value


CalibrationProfile = Annotated[
    UnknownCalibrationProfile
    | AnalogCalibrationProfile
    | BinaryCalibrationProfile
    | CounterCalibrationProfile,
    Field(discriminator="kind"),
]


class NegativeControl(StrictModel):
    control_id: str = Field(min_length=1, max_length=128)
    observed_at: AwareTimestamp
    injected_raw: int | float
    observed_result: Literal["REJECTED"]
    reason: str = Field(min_length=1, max_length=512)

    _control = field_validator("control_id")(_validate_nonblank)
    _observed = field_validator("observed_at")(_validate_timestamp)
    _reason_text = field_validator("reason")(_validate_nonblank)

    @field_validator("injected_raw")
    @classmethod
    def finite_raw(cls, value: int | float) -> int | float:
        return _finite_number(value, label="negative-control raw value")


class UnitConversion(StrictModel):
    source_unit: str = Field(min_length=1, max_length=32)
    target_unit: str = Field(min_length=1, max_length=32)
    method: Literal["identity", "affine"]
    scale: int | float
    offset: int | float

    _source = field_validator("source_unit")(_validate_nonblank)
    _target = field_validator("target_unit")(_validate_nonblank)

    @model_validator(mode="after")
    def coherent_conversion(self) -> UnitConversion:
        _finite_number(self.scale, label="unit-conversion scale")
        _finite_number(self.offset, label="unit-conversion offset")
        if self.scale == 0:
            raise ValueError("unit-conversion scale cannot be zero")
        if self.method == "identity" and (
            self.source_unit != self.target_unit
            or not _close_number(self.scale, 1)
            or not _close_number(self.offset, 0)
        ):
            raise ValueError("identity conversion requires equal units, scale one, and offset zero")
        return self


class BusinessToleranceSource(StrictModel):
    source_id: str = Field(min_length=1, max_length=256)
    source_sha256: Sha256Digest

    _source = field_validator("source_id")(_validate_nonblank)
    _source_hash = field_validator("source_sha256")(_validate_sha256)


AnalogExclusionReason = Literal[
    "INSTRUMENT_OUT_OF_RANGE",
    "REFERENCE_UNCERTAINTY_EXCEEDED",
    "SYNC_ERROR_EXCEEDED",
    "UNSTABLE",
]


class AnalogExclusionPolicy(StrictModel):
    rule_set_id: str = Field(min_length=1, max_length=256)
    rule_set_sha256: Sha256Digest
    allowed_reason_codes: list[AnalogExclusionReason] = Field(min_length=1)
    maximum_excluded_per_state: int = Field(ge=0)

    _rule_set = field_validator("rule_set_id")(_validate_nonblank)
    _rule_hash = field_validator("rule_set_sha256")(_validate_sha256)

    @model_validator(mode="after")
    def unique_reasons(self) -> AnalogExclusionPolicy:
        if len(self.allowed_reason_codes) != len(set(self.allowed_reason_codes)):
            raise ValueError("analog exclusion reason codes must be unique")
        return self


class ReferenceInstrumentCapability(StrictModel):
    range_minimum: int | float
    range_maximum: int | float
    resolution: int | float = Field(gt=0)
    accuracy: int | float = Field(ge=0)
    status: Literal["IN_CALIBRATION"]

    @model_validator(mode="after")
    def valid_capability(self) -> ReferenceInstrumentCapability:
        for field_name in ("range_minimum", "range_maximum", "resolution", "accuracy"):
            _finite_number(getattr(self, field_name), label=f"reference instrument {field_name}")
        if self.range_minimum >= self.range_maximum:
            raise ValueError("reference instrument range must be increasing")
        return self


def _close_number(actual: int | float, expected: int | float) -> bool:
    try:
        return math.isclose(float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-12)
    except (OverflowError, TypeError, ValueError):
        return False


class AnalogSample(StrictModel):
    sample_id: str = Field(min_length=1, max_length=128)
    state_id: Literal["A", "B", "C", "A_RETURN"]
    event_id: str = Field(min_length=1, max_length=128)
    observed_at: AwareTimestamp
    raw: int | float
    reference_value: int | float
    engineering: int | float
    sync_error_ms: int | float = Field(ge=0)
    uncertainty: int | float = Field(ge=0)
    stable: Literal[True]

    _sample = field_validator("sample_id")(_validate_nonblank)
    _event = field_validator("event_id")(_validate_nonblank)
    _observed = field_validator("observed_at")(_validate_timestamp)

    @model_validator(mode="after")
    def finite_values(self) -> AnalogSample:
        for label, value in (
            ("raw", self.raw),
            ("reference value", self.reference_value),
            ("engineering", self.engineering),
            ("sync error", self.sync_error_ms),
            ("uncertainty", self.uncertainty),
        ):
            _finite_number(value, label=f"analog sample {label}")
        return self


class AnalogExcludedSample(StrictModel):
    sample_id: str = Field(min_length=1, max_length=128)
    state_id: Literal["A", "B", "C", "A_RETURN"]
    event_id: str = Field(min_length=1, max_length=128)
    observed_at: AwareTimestamp
    raw: int | float
    reason_code: AnalogExclusionReason

    _sample = field_validator("sample_id")(_validate_nonblank)
    _event = field_validator("event_id")(_validate_nonblank)
    _observed = field_validator("observed_at")(_validate_timestamp)
    _raw = field_validator("raw")(
        lambda value: _finite_number(value, label="excluded analog raw value")
    )


class AnalogStateEvidence(StrictModel):
    state_id: Literal["A", "B", "C", "A_RETURN"]
    event_id: str = Field(min_length=1, max_length=128)
    samples: list[AnalogSample] = Field(min_length=MIN_CALIBRATION_SAMPLES_PER_STATE)
    aggregate_raw: int | float
    aggregate_engineering: int | float
    observed_stability: int | float = Field(ge=0)
    terminal_state: Literal["PASS"]

    _event = field_validator("event_id")(_validate_nonblank)

    @model_validator(mode="after")
    def coherent_state(self) -> AnalogStateEvidence:
        ids = [sample.sample_id for sample in self.samples]
        if len(ids) != len(set(ids)):
            raise ValueError("analog state sample IDs must be unique")
        if any(
            sample.state_id != self.state_id or sample.event_id != self.event_id
            for sample in self.samples
        ):
            raise ValueError("analog samples must bind their containing state event")
        raw_mean = math.fsum(float(sample.raw) for sample in self.samples) / len(self.samples)
        engineering_mean = math.fsum(float(sample.engineering) for sample in self.samples) / len(
            self.samples
        )
        if not _close_number(self.aggregate_raw, raw_mean) or not _close_number(
            self.aggregate_engineering, engineering_mean
        ):
            raise ValueError("analog aggregates must equal all accepted state samples")
        _finite_number(self.observed_stability, label="analog observed stability")
        return self


class AnalogThresholds(StrictModel):
    minimum_raw_span: int | float = Field(gt=0)
    minimum_reference_span: int | float = Field(gt=0)
    absolute_tolerance: int | float = Field(ge=0)
    relative_tolerance: int | float = Field(ge=0)
    return_raw_tolerance: int | float = Field(ge=0)
    return_engineering_tolerance: int | float = Field(ge=0)
    maximum_sync_error_ms: int | float = Field(gt=0)
    uncertainty_budget: int | float = Field(gt=0)
    business_tolerance_source: BusinessToleranceSource

    @model_validator(mode="after")
    def finite_thresholds(self) -> AnalogThresholds:
        for field_name in (
            "minimum_raw_span",
            "minimum_reference_span",
            "absolute_tolerance",
            "relative_tolerance",
            "return_raw_tolerance",
            "return_engineering_tolerance",
            "maximum_sync_error_ms",
            "uncertainty_budget",
        ):
            _finite_number(getattr(self, field_name), label=f"analog threshold {field_name}")
        if self.absolute_tolerance < self.uncertainty_budget:
            raise ValueError("analog tolerance cannot be smaller than its uncertainty budget")
        return self


class AnalogCalibrationEvidence(StrictModel):
    kind: Literal["analog_calibration"]
    evidence_schema_version: Literal[3]
    point_id: str = Field(min_length=1, max_length=128)
    plan_id: str = Field(min_length=1, max_length=128)
    states: list[AnalogStateEvidence] = Field(min_length=4, max_length=4)
    ratio: int | float
    offset: int | float
    aggregation_method: Literal["arithmetic_mean"]
    unit_conversion: UnitConversion
    exclusion_policy: AnalogExclusionPolicy
    exclusion_log: list[AnalogExcludedSample]
    thresholds: AnalogThresholds
    negative_controls: list[NegativeControl] = Field(min_length=1)
    runner_terminal_state: Literal["PASS"]
    reference_terminal_state: Literal["PASS"]

    _plan = field_validator("plan_id")(_validate_nonblank)

    @model_validator(mode="after")
    def complete_analog_run(self) -> AnalogCalibrationEvidence:  # noqa: PLR0912
        for label, value in (
            ("ratio", self.ratio),
            ("offset", self.offset),
        ):
            _finite_number(value, label=f"analog {label}")
        if self.ratio == 0:
            raise ValueError("analog evidence ratio cannot be zero")
        by_state: dict[str, AnalogStateEvidence] = {state.state_id: state for state in self.states}
        if set(by_state) != {"A", "B", "C", "A_RETURN"} or len(by_state) != len(self.states):
            raise ValueError("analog evidence requires exactly A/B/C/A_RETURN")
        events = [state.event_id for state in self.states]
        if len(events) != len(set(events)):
            raise ValueError("analog state events must be fresh and unique")
        sample_ids = [sample.sample_id for state in self.states for sample in state.samples]
        excluded_ids = [sample.sample_id for sample in self.exclusion_log]
        if len(sample_ids + excluded_ids) != len(set(sample_ids + excluded_ids)):
            raise ValueError("analog sample IDs must be unique across the run")
        state_events = {state.state_id: state.event_id for state in self.states}
        if any(
            excluded.event_id != state_events.get(excluded.state_id)
            or excluded.reason_code not in self.exclusion_policy.allowed_reason_codes
            for excluded in self.exclusion_log
        ):
            raise ValueError("analog exclusions must bind an approved state event and reason")
        excluded_counts = {
            state_id: sum(1 for sample in self.exclusion_log if sample.state_id == state_id)
            for state_id in state_events
        }
        if any(
            count > self.exclusion_policy.maximum_excluded_per_state
            for count in excluded_counts.values()
        ):
            raise ValueError("analog exclusions exceed the approved per-state limit")
        time_ranges = [
            (
                min(
                    datetime.fromisoformat(sample.observed_at) for sample in by_state[state].samples
                ),
                max(
                    datetime.fromisoformat(sample.observed_at) for sample in by_state[state].samples
                ),
            )
            for state in ("A", "B", "C", "A_RETURN")
        ]
        if any(
            current[1] >= following[0]
            for current, following in zip(time_ranges, time_ranges[1:], strict=False)
        ):
            raise ValueError("analog state events must occur in A/B/C/A_RETURN order")
        ranges_by_state = dict(zip(("A", "B", "C", "A_RETURN"), time_ranges, strict=True))
        if any(
            not ranges_by_state[excluded.state_id][0]
            <= datetime.fromisoformat(excluded.observed_at)
            <= ranges_by_state[excluded.state_id][1]
            for excluded in self.exclusion_log
        ):
            raise ValueError("analog exclusions must occur inside their state event window")
        a, b, c, returned = (by_state[state] for state in ("A", "B", "C", "A_RETURN"))
        raw_span = b.aggregate_raw - a.aggregate_raw
        reference_span = b.aggregate_engineering - a.aggregate_engineering
        if (
            abs(raw_span) < self.thresholds.minimum_raw_span
            or abs(reference_span) < self.thresholds.minimum_reference_span
        ):
            raise ValueError("analog A/B spans do not meet the approved thresholds")
        expected_ratio = reference_span / raw_span
        expected_offset = a.aggregate_engineering - expected_ratio * a.aggregate_raw
        if not _close_number(self.ratio, expected_ratio) or not _close_number(
            self.offset, expected_offset
        ):
            raise ValueError("analog ratio/offset must be derived from A/B aggregates")
        if any(
            abs(c.aggregate_engineering - endpoint.aggregate_engineering)
            < self.thresholds.minimum_reference_span
            for endpoint in (a, b)
        ):
            raise ValueError("analog C holdout is not separated from both fitted states")
        for state in self.states:
            if any(
                not _close_number(
                    sample.engineering,
                    self.unit_conversion.scale * sample.reference_value
                    + self.unit_conversion.offset,
                )
                for sample in state.samples
            ):
                raise ValueError("analog sample unit conversion is inconsistent")
            if any(
                sample.sync_error_ms > self.thresholds.maximum_sync_error_ms
                or sample.uncertainty > self.thresholds.uncertainty_budget
                for sample in state.samples
            ):
                raise ValueError("analog samples exceed approved sync or uncertainty thresholds")
        predicted_c = self.ratio * c.aggregate_raw + self.offset
        allowed_c = self.thresholds.absolute_tolerance + self.thresholds.relative_tolerance * abs(
            c.aggregate_engineering
        )
        if abs(predicted_c - c.aggregate_engineering) > allowed_c:
            raise ValueError("analog C holdout failed")
        if (
            abs(returned.aggregate_raw - a.aggregate_raw) > self.thresholds.return_raw_tolerance
            or abs(returned.aggregate_engineering - a.aggregate_engineering)
            > self.thresholds.return_engineering_tolerance
        ):
            raise ValueError("analog A_RETURN failed")
        controls = [control.control_id for control in self.negative_controls]
        if len(controls) != len(set(controls)):
            raise ValueError("analog negative controls must be unique")
        return self


class BinarySample(StrictModel):
    sample_id: str = Field(min_length=1, max_length=128)
    state_id: Literal["INACTIVE", "ACTIVE", "RETURN"]
    event_id: str = Field(min_length=1, max_length=128)
    observed_at: AwareTimestamp
    raw: int | float
    stable: Literal[True]

    _sample = field_validator("sample_id")(_validate_nonblank)
    _event = field_validator("event_id")(_validate_nonblank)
    _observed = field_validator("observed_at")(_validate_timestamp)
    _raw = field_validator("raw")(lambda value: _finite_number(value, label="binary raw"))


class BinaryStateEvidence(StrictModel):
    state_id: Literal["INACTIVE", "ACTIVE", "RETURN"]
    event_id: str = Field(min_length=1, max_length=128)
    samples: list[BinarySample] = Field(min_length=MIN_CALIBRATION_SAMPLES_PER_STATE)
    aggregate_raw: int | float
    observed_stability: int | float = Field(ge=0)
    chatter_transitions: int = Field(ge=0)
    terminal_state: Literal["PASS"]

    _event = field_validator("event_id")(_validate_nonblank)

    @model_validator(mode="after")
    def coherent_state(self) -> BinaryStateEvidence:
        ids = [sample.sample_id for sample in self.samples]
        if len(ids) != len(set(ids)):
            raise ValueError("binary state sample IDs must be unique")
        if any(
            sample.state_id != self.state_id or sample.event_id != self.event_id
            for sample in self.samples
        ):
            raise ValueError("binary samples must bind their containing state event")
        if not all(_close_number(sample.raw, self.aggregate_raw) for sample in self.samples):
            raise ValueError("binary stable samples must equal the state aggregate")
        _finite_number(self.observed_stability, label="binary observed stability")
        return self


class BinaryAddressSemantics(StrictModel):
    candidate_id: str = Field(min_length=1, max_length=128)
    kind: Literal["coil", "discrete_input", "register_bit", "whole_register"]
    function_code: Literal[1, 2, 3, 4]
    start_address: int = Field(ge=0, le=65535)
    register_width: int = Field(ge=1, le=125)
    bit: int | None = Field(default=None, ge=0, le=15)

    _candidate = field_validator("candidate_id")(_validate_nonblank)

    @model_validator(mode="after")
    def coherent_address_kind(self) -> BinaryAddressSemantics:
        if self.kind == "coil" and (
            self.function_code != 1 or self.register_width != 1 or self.bit is not None
        ):
            raise ValueError("coil semantics require FC1 without a register bit")
        if self.kind == "discrete_input" and (
            self.function_code != MODBUS_FC_DISCRETE_INPUTS
            or self.register_width != 1
            or self.bit is not None
        ):
            raise ValueError("discrete-input semantics require FC2 without a register bit")
        if self.kind == "register_bit" and (self.function_code not in {3, 4} or self.bit is None):
            raise ValueError("register-bit semantics require FC3/FC4 and an explicit bit")
        if self.kind == "whole_register" and (
            self.function_code not in {3, 4} or self.bit is not None
        ):
            raise ValueError("whole-register semantics require FC3/FC4 without a bit")
        return self


class BinaryUnintervenedChannelControl(StrictModel):
    control_id: str = Field(min_length=1, max_length=128)
    sample_id: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=1, max_length=128)
    observed_at: AwareTimestamp
    point_id: str = Field(min_length=1, max_length=128)
    point_name: str = Field(min_length=1, max_length=128)
    address_semantics: BinaryAddressSemantics
    baseline_raw: int | float
    observed_raw: int | float
    terminal_state: Literal["PASS"]

    _control = field_validator("control_id")(_validate_nonblank)
    _sample = field_validator("sample_id")(_validate_nonblank)
    _event = field_validator("event_id")(_validate_nonblank)
    _observed = field_validator("observed_at")(_validate_timestamp)
    _point = field_validator("point_id")(_validate_nonblank)
    _point_name = field_validator("point_name")(_validate_nonblank)

    @model_validator(mode="after")
    def unchanged_channel(self) -> BinaryUnintervenedChannelControl:
        _finite_number(self.baseline_raw, label="binary control baseline")
        _finite_number(self.observed_raw, label="binary control observation")
        if not _close_number(self.baseline_raw, self.observed_raw):
            raise ValueError("unintervened binary channel changed during the target transition")
        return self


class BinaryCompetingCandidateControl(StrictModel):
    control_id: str = Field(min_length=1, max_length=128)
    sample_id: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=1, max_length=128)
    observed_at: AwareTimestamp
    candidate: BinaryAddressSemantics
    observed_raw: int | float
    observed_result: Literal["REJECTED"]
    reason: str = Field(min_length=1, max_length=512)

    _control = field_validator("control_id")(_validate_nonblank)
    _sample = field_validator("sample_id")(_validate_nonblank)
    _event = field_validator("event_id")(_validate_nonblank)
    _observed = field_validator("observed_at")(_validate_timestamp)
    _reason = field_validator("reason")(_validate_nonblank)
    _raw = field_validator("observed_raw")(
        lambda value: _finite_number(value, label="binary competing-candidate observation")
    )


class BinaryCalibrationEvidence(StrictModel):
    kind: Literal["binary_calibration"]
    evidence_schema_version: Literal[3]
    point_id: str = Field(min_length=1, max_length=128)
    plan_id: str = Field(min_length=1, max_length=128)
    states: list[BinaryStateEvidence] = Field(min_length=3, max_length=3)
    maximum_chatter_transitions: int = Field(ge=0)
    maximum_sync_error_ms: int | float = Field(gt=0)
    negative_controls: list[NegativeControl] = Field(min_length=1)
    address_semantics: BinaryAddressSemantics
    unintervened_channel_controls: list[BinaryUnintervenedChannelControl] = Field(min_length=1)
    competing_candidate_controls: list[BinaryCompetingCandidateControl] = Field(min_length=1)
    runner_terminal_state: Literal["PASS"]
    reference_terminal_state: Literal["PASS"]

    _plan = field_validator("plan_id")(_validate_nonblank)

    @model_validator(mode="after")
    def transition_return(self) -> BinaryCalibrationEvidence:  # noqa: PLR0912
        _finite_number(self.maximum_sync_error_ms, label="binary maximum sync error")
        expected_states = ["INACTIVE", "ACTIVE", "RETURN"]
        if [state.state_id for state in self.states] != expected_states:
            raise ValueError("binary evidence states must occur in INACTIVE/ACTIVE/RETURN order")
        by_state = {state.state_id: state for state in self.states}
        if set(by_state) != {"INACTIVE", "ACTIVE", "RETURN"} or len(by_state) != len(self.states):
            raise ValueError("binary evidence requires INACTIVE/ACTIVE/RETURN states")
        if by_state["INACTIVE"].aggregate_raw == by_state["ACTIVE"].aggregate_raw:
            raise ValueError("binary transition must change raw state")
        if by_state["RETURN"].aggregate_raw != by_state["INACTIVE"].aggregate_raw:
            raise ValueError("binary transition must return to the initial state")
        if any(
            state.chatter_transitions > self.maximum_chatter_transitions for state in self.states
        ):
            raise ValueError("binary chatter exceeds the approved threshold")
        events = [state.event_id for state in self.states]
        if len(events) != len(set(events)):
            raise ValueError("binary transition events must be fresh and unique")
        time_ranges = [
            (
                min(datetime.fromisoformat(sample.observed_at) for sample in state.samples),
                max(datetime.fromisoformat(sample.observed_at) for sample in state.samples),
            )
            for state in self.states
        ]
        if any(
            current[1] >= following[0]
            for current, following in zip(time_ranges, time_ranges[1:], strict=False)
        ):
            raise ValueError("binary state events must be strictly time ordered")
        sample_ids = [sample.sample_id for state in self.states for sample in state.samples]
        sample_ids.extend(control.sample_id for control in self.unintervened_channel_controls)
        sample_ids.extend(control.sample_id for control in self.competing_candidate_controls)
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("binary sample IDs must be unique across the run")
        stable_values = {
            by_state["INACTIVE"].aggregate_raw,
            by_state["ACTIVE"].aggregate_raw,
        }
        negative_control_ids = [control.control_id for control in self.negative_controls]
        if len(negative_control_ids) != len(set(negative_control_ids)):
            raise ValueError("binary negative controls must be unique")
        if any(control.injected_raw in stable_values for control in self.negative_controls):
            raise ValueError("binary negative controls must test values outside both states")
        active_event = by_state["ACTIVE"].event_id
        if any(
            control.event_id != active_event or control.point_id == self.point_id
            for control in self.unintervened_channel_controls
        ):
            raise ValueError(
                "binary unintervened controls must bind the target transition and another point"
            )
        active_window = (time_ranges[1][0], time_ranges[2][0])
        if any(
            not active_window[0] <= datetime.fromisoformat(control.observed_at) < active_window[1]
            for control in self.unintervened_channel_controls
        ):
            raise ValueError(
                "binary unintervened controls must occur inside the active event window"
            )
        selected_id = self.address_semantics.candidate_id
        competing_ids = [
            control.candidate.candidate_id for control in self.competing_candidate_controls
        ]
        if selected_id in competing_ids or len(competing_ids) != len(set(competing_ids)):
            raise ValueError(
                "binary competing candidates must be unique and exclude the selected candidate"
            )
        if any(control.event_id != active_event for control in self.competing_candidate_controls):
            raise ValueError("binary competing-candidate controls must bind the target transition")
        if any(
            not active_window[0] <= datetime.fromisoformat(control.observed_at) < active_window[1]
            for control in self.competing_candidate_controls
        ):
            raise ValueError(
                "binary competing-candidate controls must occur inside the active event window"
            )
        return self


class CounterObservation(StrictModel):
    sample_id: str = Field(min_length=1, max_length=128)
    state_id: Literal["BASELINE", "INCREMENT", "ROLLOVER", "PERSISTENCE"]
    event_id: str = Field(min_length=1, max_length=128)
    observed_at: AwareTimestamp
    raw: int
    reference_increment: int | float = Field(ge=0)
    sync_error_ms: int | float = Field(ge=0)
    observed_stability: int | float = Field(ge=0)
    stable: Literal[True]

    _sample = field_validator("sample_id")(_validate_nonblank)
    _event = field_validator("event_id")(_validate_nonblank)
    _observed = field_validator("observed_at")(_validate_timestamp)

    @model_validator(mode="after")
    def finite_values(self) -> CounterObservation:
        _finite_number(self.reference_increment, label="counter reference increment")
        _finite_number(self.sync_error_ms, label="counter sync error")
        _finite_number(self.observed_stability, label="counter observed stability")
        return self


class CounterPersistenceEvent(StrictModel):
    event_id: str = Field(min_length=1, max_length=128)
    method: Literal["physical_power_disconnect"]
    power_removed_at: AwareTimestamp
    power_restored_at: AwareTimestamp
    post_restore_observed_at: AwareTimestamp
    power_off_duration_seconds: int | float = Field(gt=0)
    pre_power_raw: int = Field(ge=0)
    post_power_raw: int = Field(ge=0)
    terminal_state: Literal["PASS"]

    _event = field_validator("event_id")(_validate_nonblank)
    _removed = field_validator("power_removed_at")(_validate_timestamp)
    _restored = field_validator("power_restored_at")(_validate_timestamp)
    _observed = field_validator("post_restore_observed_at")(_validate_timestamp)

    @model_validator(mode="after")
    def physical_power_cycle(self) -> CounterPersistenceEvent:
        _finite_number(self.power_off_duration_seconds, label="counter power-off duration")
        removed = datetime.fromisoformat(self.power_removed_at)
        restored = datetime.fromisoformat(self.power_restored_at)
        observed = datetime.fromisoformat(self.post_restore_observed_at)
        if not removed < restored <= observed:
            raise ValueError("counter power-loss timestamps are not physically ordered")
        if not _close_number(
            self.power_off_duration_seconds,
            (restored - removed).total_seconds(),
        ):
            raise ValueError("counter power-off duration does not match its timestamps")
        if self.pre_power_raw != self.post_power_raw:
            raise ValueError("counter changed across the physical power-loss event")
        return self


class CounterCalibrationEvidence(StrictModel):
    kind: Literal["counter_calibration"]
    evidence_schema_version: Literal[3]
    point_id: str = Field(min_length=1, max_length=128)
    plan_id: str = Field(min_length=1, max_length=128)
    counts_per_unit: int | float
    modulus: int = Field(gt=1)
    rollover_behavior: Literal["wrap", "saturate", "reset"]
    expected_increment: int | float = Field(gt=0)
    increment_tolerance: int | float = Field(ge=0)
    maximum_sync_error_ms: int | float = Field(gt=0)
    observations: list[CounterObservation] = Field(min_length=MIN_COUNTER_OBSERVATIONS)
    monotonicity_verified: Literal[True]
    terminal_raw: int = Field(ge=0)
    persistence_before: int = Field(ge=0)
    persistence_after: int = Field(ge=0)
    persistence_event: CounterPersistenceEvent
    persistence_terminal_state: Literal["PASS"]
    negative_controls: list[NegativeControl] = Field(min_length=1)
    runner_terminal_state: Literal["PASS"]
    reference_terminal_state: Literal["PASS"]

    _plan = field_validator("plan_id")(_validate_nonblank)

    @model_validator(mode="after")
    def complete_counter_run(self) -> CounterCalibrationEvidence:  # noqa: PLR0912
        for label, value in (
            ("counts_per_unit", self.counts_per_unit),
            ("expected_increment", self.expected_increment),
            ("increment_tolerance", self.increment_tolerance),
            ("maximum_sync_error_ms", self.maximum_sync_error_ms),
        ):
            _finite_number(value, label=f"counter evidence {label}")
        if self.counts_per_unit <= 0:
            raise ValueError("counter evidence scale must be positive")
        if self.observations[-1].raw != self.terminal_raw:
            raise ValueError("counter terminal state must equal the final observation")
        if any(
            observation.raw < 0 or observation.raw >= self.modulus
            for observation in self.observations
        ):
            raise ValueError("counter observations must stay inside the modulus")
        ids = [observation.sample_id for observation in self.observations]
        if len(ids) != len(set(ids)):
            raise ValueError("counter observation IDs must be unique")
        groups = _counter_observation_groups(self.observations)
        if tuple(groups) != COUNTER_STATE_SEQUENCE:
            raise ValueError("counter observations must bind the approved state sequence")
        if any(
            len(observations) < MIN_CALIBRATION_SAMPLES_PER_STATE
            for observations in groups.values()
        ):
            raise ValueError("counter states require at least three observations")
        events = [observations[0].event_id for observations in groups.values()]
        if len(events) != len(set(events)) or any(
            observation.event_id != observations[0].event_id
            for observations in groups.values()
            for observation in observations
        ):
            raise ValueError("counter state observations must bind one fresh event per state")
        if any(
            observation.raw != observations[0].raw
            for observations in groups.values()
            for observation in observations
        ):
            raise ValueError("counter observations must be stable within each state")
        timestamps = [
            datetime.fromisoformat(observation.observed_at) for observation in self.observations
        ]
        if timestamps != sorted(timestamps) or len(timestamps) != len(set(timestamps)):
            raise ValueError("counter observations must be strictly time ordered")
        if any(
            observation.sync_error_ms > self.maximum_sync_error_ms
            for observation in self.observations
        ):
            raise ValueError("counter observations exceed the approved sync threshold")
        if self.persistence_before != self.persistence_after:
            raise ValueError("counter persistence check did not preserve the terminal value")
        rollover_observations = groups["ROLLOVER"]
        persistence_observations = groups["PERSISTENCE"]
        first_persistence = persistence_observations[0]
        last_persistence = persistence_observations[-1]
        if not (
            datetime.fromisoformat(rollover_observations[-1].observed_at)
            < datetime.fromisoformat(self.persistence_event.power_removed_at)
            < datetime.fromisoformat(self.persistence_event.power_restored_at)
            <= datetime.fromisoformat(first_persistence.observed_at)
        ):
            raise ValueError(
                "counter persistence event must occur between rollover and persistence samples"
            )
        if (
            self.persistence_event.event_id != last_persistence.event_id
            or self.persistence_event.post_restore_observed_at != last_persistence.observed_at
            or self.persistence_event.pre_power_raw != self.persistence_before
            or self.persistence_event.post_power_raw != self.persistence_after
            or self.persistence_before != rollover_observations[-1].raw
            or self.persistence_after != last_persistence.raw
        ):
            raise ValueError("counter persistence event does not bind the PERSISTENCE observation")
        negative_control_ids = [control.control_id for control in self.negative_controls]
        if len(negative_control_ids) != len(set(negative_control_ids)):
            raise ValueError("counter negative controls must be unique")
        return self


CalibrationEvidenceContent = Annotated[
    AnalogCalibrationEvidence | BinaryCalibrationEvidence | CounterCalibrationEvidence,
    Field(discriminator="kind"),
]


class AnalogReferenceSample(StrictModel):
    sample_id: str = Field(min_length=1, max_length=128)
    state_id: Literal["A", "B", "C", "A_RETURN"]
    event_id: str = Field(min_length=1, max_length=128)
    observed_at: AwareTimestamp
    reference_value: int | float
    unit: str = Field(min_length=1, max_length=32)
    sync_error_ms: int | float = Field(ge=0)
    uncertainty: int | float = Field(ge=0)
    stable: bool
    outcome: Literal["ACCEPTED", "EXCLUDED"]
    exclusion_reason: AnalogExclusionReason | None = None

    _sample = field_validator("sample_id")(_validate_nonblank)
    _event = field_validator("event_id")(_validate_nonblank)
    _observed = field_validator("observed_at")(_validate_timestamp)
    _unit = field_validator("unit")(_validate_nonblank)

    @model_validator(mode="after")
    def valid_measurement(self) -> AnalogReferenceSample:
        for label, value in (
            ("reference value", self.reference_value),
            ("sync error", self.sync_error_ms),
            ("uncertainty", self.uncertainty),
        ):
            _finite_number(value, label=f"analog reference sample {label}")
        if self.outcome == "ACCEPTED" and self.exclusion_reason is not None:
            raise ValueError("accepted analog reference samples cannot carry an exclusion reason")
        if self.outcome == "EXCLUDED" and self.exclusion_reason is None:
            raise ValueError("excluded analog reference samples require an exclusion reason")
        return self


class BinaryReferenceSample(StrictModel):
    sample_id: str = Field(min_length=1, max_length=128)
    state_id: Literal["INACTIVE", "ACTIVE", "RETURN"]
    event_id: str = Field(min_length=1, max_length=128)
    observed_at: AwareTimestamp
    reference_state: Literal["INACTIVE", "ACTIVE"]
    unit: str = Field(min_length=1, max_length=32)
    sync_error_ms: int | float = Field(ge=0)
    uncertainty: int | float = Field(ge=0)

    _sample = field_validator("sample_id")(_validate_nonblank)
    _event = field_validator("event_id")(_validate_nonblank)
    _observed = field_validator("observed_at")(_validate_timestamp)
    _unit = field_validator("unit")(_validate_nonblank)

    @model_validator(mode="after")
    def finite_measurement(self) -> BinaryReferenceSample:
        _finite_number(self.sync_error_ms, label="binary reference sample sync error")
        _finite_number(self.uncertainty, label="binary reference sample uncertainty")
        return self


class CounterReferenceSample(StrictModel):
    sample_id: str = Field(min_length=1, max_length=128)
    state_id: Literal["BASELINE", "INCREMENT", "ROLLOVER", "PERSISTENCE"]
    event_id: str = Field(min_length=1, max_length=128)
    observed_at: AwareTimestamp
    reference_raw: int = Field(ge=0)
    reference_increment: int | float = Field(ge=0)
    unit: str = Field(min_length=1, max_length=32)
    sync_error_ms: int | float = Field(ge=0)
    uncertainty: int | float = Field(ge=0)

    _sample = field_validator("sample_id")(_validate_nonblank)
    _event = field_validator("event_id")(_validate_nonblank)
    _observed = field_validator("observed_at")(_validate_timestamp)
    _unit = field_validator("unit")(_validate_nonblank)

    @model_validator(mode="after")
    def finite_measurement(self) -> CounterReferenceSample:
        for label, value in (
            ("reference increment", self.reference_increment),
            ("sync error", self.sync_error_ms),
            ("uncertainty", self.uncertainty),
        ):
            _finite_number(value, label=f"counter reference sample {label}")
        return self


class AnalogReferenceEvidence(StrictModel):
    kind: Literal["analog_reference"]
    evidence_schema_version: Literal[4]
    point_id: str = Field(min_length=1, max_length=128)
    plan_id: str = Field(min_length=1, max_length=128)
    reference_state_aggregates: dict[Literal["A", "B", "C", "A_RETURN"], int | float]
    state_aggregates: dict[Literal["A", "B", "C", "A_RETURN"], int | float]
    samples: list[AnalogReferenceSample] = Field(min_length=12)
    reference_id: str = Field(min_length=1, max_length=256)
    channel_id: str = Field(min_length=1, max_length=128)
    calibration_certificate_sha256: Sha256Digest
    instrument_capability: ReferenceInstrumentCapability
    unit_conversion: UnitConversion
    uncertainty: int | float = Field(gt=0)
    reference_collector_tool_id: str = Field(min_length=1, max_length=256)
    reference_collector_tool_sha256: Sha256Digest
    terminal_state: Literal["PASS"]

    _plan = field_validator("plan_id")(_validate_nonblank)
    _channel = field_validator("channel_id")(_validate_nonblank)
    _certificate = field_validator("calibration_certificate_sha256")(_validate_sha256)
    _collector = field_validator("reference_collector_tool_id")(_validate_nonblank)
    _collector_hash = field_validator("reference_collector_tool_sha256")(_validate_sha256)

    @model_validator(mode="after")
    def finite_reference(self) -> AnalogReferenceEvidence:  # noqa: PLR0912
        for label, value in (("uncertainty", self.uncertainty),):
            _finite_number(value, label=f"analog reference {label}")
        if set(self.state_aggregates) != {"A", "B", "C", "A_RETURN"} or set(
            self.reference_state_aggregates
        ) != {"A", "B", "C", "A_RETURN"}:
            raise ValueError("analog reference must cover A/B/C/A_RETURN")
        sample_ids = [sample.sample_id for sample in self.samples]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("analog reference sample IDs must be unique")
        timestamps = [datetime.fromisoformat(sample.observed_at) for sample in self.samples]
        if timestamps != sorted(timestamps) or len(timestamps) != len(set(timestamps)):
            raise ValueError("analog reference samples must be strictly time ordered")
        accepted = [sample for sample in self.samples if sample.outcome == "ACCEPTED"]
        if {sample.state_id for sample in accepted} != {"A", "B", "C", "A_RETURN"}:
            raise ValueError("analog reference accepted samples must cover every state")
        if any(sample.unit != self.unit_conversion.source_unit for sample in self.samples):
            raise ValueError("analog reference sample units must match the unit conversion")
        if any(not sample.stable for sample in accepted):
            raise ValueError("accepted analog reference samples must be stable")
        accepted_uncertainty = max((sample.uncertainty for sample in accepted), default=0)
        if not _close_number(self.uncertainty, accepted_uncertainty):
            raise ValueError("analog reference uncertainty must equal its accepted sample maximum")
        for state_id, value in self.reference_state_aggregates.items():
            _finite_number(value, label="analog reference aggregate")
            if not (
                self.instrument_capability.range_minimum
                <= value
                <= self.instrument_capability.range_maximum
            ):
                raise ValueError("analog reference aggregate is outside the instrument range")
            expected = self.unit_conversion.scale * value + self.unit_conversion.offset
            if not _close_number(self.state_aggregates[state_id], expected):
                raise ValueError("analog reference unit conversion is inconsistent")
            observed_values = [
                sample.reference_value for sample in accepted if sample.state_id == state_id
            ]
            observed_mean = math.fsum(float(item) for item in observed_values) / len(
                observed_values
            )
            if not _close_number(value, observed_mean):
                raise ValueError(
                    "analog reference aggregates must be derived from accepted samples"
                )
        for value in self.state_aggregates.values():
            _finite_number(value, label="analog engineering aggregate")
        _validate_nonblank(self.reference_id)
        return self


class BinaryReferenceEvidence(StrictModel):
    kind: Literal["binary_reference"]
    evidence_schema_version: Literal[4]
    point_id: str = Field(min_length=1, max_length=128)
    plan_id: str = Field(min_length=1, max_length=128)
    inactive_raw: int | float
    active_raw: int | float
    reference_id: str = Field(min_length=1, max_length=256)
    channel_id: str = Field(min_length=1, max_length=128)
    calibration_certificate_sha256: Sha256Digest
    samples: list[BinaryReferenceSample] = Field(min_length=9)
    selected_candidate_id: str = Field(min_length=1, max_length=128)
    rejected_candidate_ids: list[str] = Field(min_length=1)
    unintervened_control_ids: list[str] = Field(min_length=1)
    terminal_state: Literal["PASS"]
    reference_collector_tool_id: str = Field(min_length=1, max_length=256)
    reference_collector_tool_sha256: Sha256Digest

    _plan = field_validator("plan_id")(_validate_nonblank)
    _channel = field_validator("channel_id")(_validate_nonblank)
    _certificate = field_validator("calibration_certificate_sha256")(_validate_sha256)
    _selected = field_validator("selected_candidate_id")(_validate_nonblank)
    _collector = field_validator("reference_collector_tool_id")(_validate_nonblank)
    _collector_hash = field_validator("reference_collector_tool_sha256")(_validate_sha256)

    @model_validator(mode="after")
    def finite_reference(self) -> BinaryReferenceEvidence:
        _finite_number(self.inactive_raw, label="binary reference inactive raw")
        _finite_number(self.active_raw, label="binary reference active raw")
        if self.inactive_raw == self.active_raw:
            raise ValueError("binary reference states must be distinct")
        sample_ids = [sample.sample_id for sample in self.samples]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("binary reference sample IDs must be unique")
        timestamps = [datetime.fromisoformat(sample.observed_at) for sample in self.samples]
        if timestamps != sorted(timestamps) or len(timestamps) != len(set(timestamps)):
            raise ValueError("binary reference samples must be strictly time ordered")
        if (
            len(self.rejected_candidate_ids) != len(set(self.rejected_candidate_ids))
            or self.selected_candidate_id in self.rejected_candidate_ids
            or len(self.unintervened_control_ids) != len(set(self.unintervened_control_ids))
        ):
            raise ValueError("binary reference control identities are inconsistent")
        _validate_nonblank(self.reference_id)
        return self


class CounterReferenceEvidence(StrictModel):
    kind: Literal["counter_reference"]
    evidence_schema_version: Literal[4]
    point_id: str = Field(min_length=1, max_length=128)
    plan_id: str = Field(min_length=1, max_length=128)
    counts_per_unit: int | float
    modulus: int = Field(gt=1)
    rollover_behavior: Literal["wrap", "saturate", "reset"]
    expected_increment: int | float = Field(gt=0)
    expected_terminal_raw: int = Field(ge=0)
    expected_persistence_raw: int = Field(ge=0)
    samples: list[CounterReferenceSample] = Field(min_length=MIN_COUNTER_OBSERVATIONS)
    persistence_event: CounterPersistenceEvent
    power_loss_event_id: str = Field(min_length=1, max_length=128)
    persistence_method: Literal["physical_power_disconnect"]
    reference_id: str = Field(min_length=1, max_length=256)
    channel_id: str = Field(min_length=1, max_length=128)
    calibration_certificate_sha256: Sha256Digest
    terminal_state: Literal["PASS"]
    reference_collector_tool_id: str = Field(min_length=1, max_length=256)
    reference_collector_tool_sha256: Sha256Digest

    _plan = field_validator("plan_id")(_validate_nonblank)
    _channel = field_validator("channel_id")(_validate_nonblank)
    _certificate = field_validator("calibration_certificate_sha256")(_validate_sha256)
    _power_loss_event = field_validator("power_loss_event_id")(_validate_nonblank)
    _collector = field_validator("reference_collector_tool_id")(_validate_nonblank)
    _collector_hash = field_validator("reference_collector_tool_sha256")(_validate_sha256)

    @model_validator(mode="after")
    def valid_reference(self) -> CounterReferenceEvidence:
        _finite_number(self.counts_per_unit, label="counter reference counts_per_unit")
        _finite_number(self.expected_increment, label="counter reference expected_increment")
        if (
            self.counts_per_unit <= 0
            or self.expected_terminal_raw >= self.modulus
            or self.expected_persistence_raw != self.expected_terminal_raw
        ):
            raise ValueError("counter reference values are outside their valid domain")
        sample_ids = [sample.sample_id for sample in self.samples]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("counter reference sample IDs must be unique")
        timestamps = [datetime.fromisoformat(sample.observed_at) for sample in self.samples]
        if timestamps != sorted(timestamps) or len(timestamps) != len(set(timestamps)):
            raise ValueError("counter reference samples must be strictly time ordered")
        if any(sample.reference_raw >= self.modulus for sample in self.samples):
            raise ValueError("counter reference samples must stay inside the modulus")
        state_groups: dict[str, int] = {}
        last_state: str | None = None
        for sample in self.samples:
            state_id = sample.state_id
            if state_id != last_state:
                if state_id in state_groups:
                    raise ValueError("counter reference states must be contiguous")
                state_groups[state_id] = 0
                last_state = state_id
            state_groups[state_id] += 1
        if tuple(state_groups) != COUNTER_STATE_SEQUENCE or any(
            count < MIN_CALIBRATION_SAMPLES_PER_STATE for count in state_groups.values()
        ):
            raise ValueError("counter reference must bind the approved state sequence")
        rollover_samples = [sample for sample in self.samples if sample.state_id == "ROLLOVER"]
        persistence_samples = [
            sample for sample in self.samples if sample.state_id == "PERSISTENCE"
        ]
        if not (
            datetime.fromisoformat(rollover_samples[-1].observed_at)
            < datetime.fromisoformat(self.persistence_event.power_removed_at)
            < datetime.fromisoformat(self.persistence_event.power_restored_at)
            <= datetime.fromisoformat(persistence_samples[0].observed_at)
        ):
            raise ValueError(
                "counter reference persistence event must occur between rollover and persistence samples"
            )
        if (
            self.power_loss_event_id != self.persistence_event.event_id
            or self.persistence_method != self.persistence_event.method
            or self.persistence_event.pre_power_raw != self.expected_terminal_raw
            or self.persistence_event.post_power_raw != self.expected_persistence_raw
            or self.persistence_event.post_restore_observed_at != self.samples[-1].observed_at
        ):
            raise ValueError("counter reference persistence event identity is inconsistent")
        _validate_nonblank(self.reference_id)
        return self


ReferenceEvidenceContent = Annotated[
    AnalogReferenceEvidence | BinaryReferenceEvidence | CounterReferenceEvidence,
    Field(discriminator="kind"),
]


class IdentityEvidenceContent(StrictModel):
    kind: Literal["identity"]
    model: str = Field(min_length=1, max_length=128)
    hardware_revision: str = Field(min_length=1, max_length=128)
    firmware_version: str = Field(min_length=1, max_length=128)
    point_map_version: str = Field(min_length=1, max_length=128)
    usb_serial_number: str = Field(min_length=1, max_length=128)

    @field_validator(
        "model", "hardware_revision", "firmware_version", "point_map_version", "usb_serial_number"
    )
    @classmethod
    def nonblank_identity(cls, value: str) -> str:
        return _validate_nonblank(value)


class MapPointEvidence(StrictModel):
    point_id: str = Field(min_length=1, max_length=128)
    point_name: str = Field(min_length=1, max_length=128)
    unit: str = Field(min_length=1, max_length=32)
    function_code: Literal[1, 2, 3, 4]
    start_address: int = Field(ge=0, le=65535)
    register_width: int = Field(ge=1, le=125)
    bit: int | None = Field(default=None, ge=0, le=15)
    value_type: Literal["bit", "u16", "s16", "u32", "s32", "float32", "bcd"]
    byte_order: Literal["big", "little", "not_applicable"]
    word_order: Literal["high_first", "low_first", "not_applicable"]

    _point_name = field_validator("point_name")(_validate_nonblank)
    _unit = field_validator("unit")(_validate_nonblank)


class AuthoritativeMapEvidenceContent(StrictModel):
    kind: Literal["authoritative_map"]
    device_model: str = Field(min_length=1, max_length=128)
    hardware_revision: str = Field(min_length=1, max_length=128)
    firmware_version: str = Field(min_length=1, max_length=128)
    point_map_version: str = Field(min_length=1, max_length=128)
    device_identity_sha256: Sha256Digest
    device_serial: str = Field(min_length=1, max_length=128)
    points: list[MapPointEvidence] = Field(min_length=1)

    _identity_hash = field_validator("device_identity_sha256")(_validate_sha256)
    _serial = field_validator("device_serial")(_validate_nonblank)

    @model_validator(mode="after")
    def unique_points(self) -> AuthoritativeMapEvidenceContent:
        ids = [point.point_id for point in self.points]
        if len(ids) != len(set(ids)):
            raise ValueError("authoritative map point IDs must be unique")
        _validate_nonblank(self.device_model)
        _validate_nonblank(self.hardware_revision)
        _validate_nonblank(self.firmware_version)
        _validate_nonblank(self.point_map_version)
        return self


LineFieldName = Literal["unit_id", "baud_rate", "data_bits", "parity", "stop_bits"]


class PosixTermiosReadback(StrictModel):
    device_node: str = Field(pattern=r"^/dev/tty[A-Za-z0-9_.-]+$")
    baud_rate: Literal[1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
    data_bits: Literal[8]
    parity: Literal["N", "E", "O"]
    stop_bits: Literal[1, 2]


class PosixUdevIdentity(StrictModel):
    stable_device_path: str = Field(min_length=1, max_length=1024)
    device_node: str = Field(pattern=r"^/dev/tty[A-Za-z0-9_.-]+$")
    id_bus: Literal["usb"]
    id_serial_short: str = Field(min_length=1, max_length=128)
    devlinks: list[str] = Field(min_length=1)

    _stable_path = field_validator("stable_device_path")(_validate_stable_serial_device_path)
    _serial = field_validator("id_serial_short")(_validate_nonblank)

    @field_validator("devlinks")
    @classmethod
    def valid_devlinks(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or any(
            not value.startswith("/dev/") for value in values
        ):
            raise ValueError("udev DEVLINKS must contain unique absolute device links")
        return values

    @model_validator(mode="after")
    def stable_path_is_a_devlink(self) -> PosixUdevIdentity:
        if _stable_serial_device_platform(self.stable_device_path) != "posix":
            raise ValueError("udev identity requires a POSIX stable USB path")
        if self.stable_device_path not in self.devlinks:
            raise ValueError("udev DEVLINKS must contain the stable device path")
        basename = self.stable_device_path.removeprefix("/dev/serial/by-id/")
        if self.id_serial_short.casefold() not in basename.casefold():
            raise ValueError("udev serial number does not match the stable device path")
        return self


class PosixLineConfigurationReadback(StrictModel):
    kind: Literal["posix_termios_udev"]
    observation_method: Literal["posix_termios_readback"]
    termios: PosixTermiosReadback
    udev: PosixUdevIdentity

    @model_validator(mode="after")
    def same_device_node(self) -> PosixLineConfigurationReadback:
        if self.termios.device_node != self.udev.device_node:
            raise ValueError("termios and udev readbacks must identify the same device node")
        return self

    @property
    def stable_device_path(self) -> str:
        return self.udev.stable_device_path

    @property
    def device_serial(self) -> str:
        return self.udev.id_serial_short

    @property
    def baud_rate(self) -> int:
        return self.termios.baud_rate

    @property
    def data_bits(self) -> int:
        return self.termios.data_bits

    @property
    def parity(self) -> str:
        return self.termios.parity

    @property
    def stop_bits(self) -> int:
        return self.termios.stop_bits


class WindowsDcbReadback(StrictModel):
    port_name: str = Field(pattern=r"^COM[1-9][0-9]*$")
    baud_rate: Literal[1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
    data_bits: Literal[8]
    parity: Literal["N", "E", "O"]
    stop_bits: Literal[1, 2]


class WindowsSetupApiIdentity(StrictModel):
    device_interface_path: str = Field(min_length=1, max_length=1024)
    device_instance_id: str = Field(min_length=1, max_length=1024)
    hardware_ids: list[str] = Field(min_length=1)
    serial_number: str = Field(min_length=1, max_length=128)

    _interface_path = field_validator("device_interface_path")(_validate_stable_serial_device_path)
    _instance = field_validator("device_instance_id")(_validate_nonblank)
    _serial = field_validator("serial_number")(_validate_nonblank)

    @field_validator("hardware_ids")
    @classmethod
    def valid_hardware_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or any(not value.strip() for value in values):
            raise ValueError("SetupAPI hardware IDs must be unique non-blank strings")
        return values

    @model_validator(mode="after")
    def coherent_usb_identity(self) -> WindowsSetupApiIdentity:
        match = WINDOWS_USB_INTERFACE_PATTERN.fullmatch(self.device_interface_path)
        if match is None:
            raise ValueError("SetupAPI identity requires a complete Windows USB interface path")
        hardware_id = match.group("hardware_id").replace("#", "\\")
        expected_instance = f"USB\\{hardware_id}\\{match.group('serial')}"
        if self.device_instance_id.casefold() != expected_instance.casefold():
            raise ValueError("SetupAPI instance ID does not match the USB interface path")
        expected_hardware_id = f"USB\\{hardware_id}"
        if expected_hardware_id.casefold() not in {value.casefold() for value in self.hardware_ids}:
            raise ValueError("SetupAPI hardware IDs do not match the USB interface path")
        if self.serial_number.casefold() != match.group("serial").casefold():
            raise ValueError("SetupAPI serial number does not match the USB interface path")
        return self


class WindowsLineConfigurationReadback(StrictModel):
    kind: Literal["windows_dcb_setupapi"]
    observation_method: Literal["win32_dcb_readback"]
    dcb: WindowsDcbReadback
    setupapi: WindowsSetupApiIdentity

    @property
    def stable_device_path(self) -> str:
        return self.setupapi.device_interface_path

    @property
    def device_serial(self) -> str:
        return self.setupapi.serial_number

    @property
    def baud_rate(self) -> int:
        return self.dcb.baud_rate

    @property
    def data_bits(self) -> int:
        return self.dcb.data_bits

    @property
    def parity(self) -> str:
        return self.dcb.parity

    @property
    def stop_bits(self) -> int:
        return self.dcb.stop_bits


LineConfigurationReadback = Annotated[
    PosixLineConfigurationReadback | WindowsLineConfigurationReadback,
    Field(discriminator="kind"),
]


def line_configuration_readback_sha256(
    value: PosixLineConfigurationReadback | WindowsLineConfigurationReadback | Mapping[str, Any],
) -> str:
    document = value.model_dump(mode="json") if isinstance(value, StrictModel) else dict(value)
    return _canonical_sha256(document)


class LineFieldClaim(StrictModel):
    field: LineFieldName
    observed_value: int | str
    source_record_sha256: Sha256Digest

    _source_hash = field_validator("source_record_sha256")(_validate_sha256)


class LineProtocolEvidenceContent(StrictModel):
    kind: Literal["line_protocol"]
    stable_device_path: str = Field(min_length=1, max_length=1024)
    device_identity_sha256: Sha256Digest
    device_serial: str = Field(min_length=1, max_length=128)
    unit_id: int = Field(ge=1, le=247)
    baud_rate: Literal[1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
    data_bits: Literal[8]
    parity: Literal["N", "E", "O"]
    stop_bits: Literal[1, 2]
    field_claims: list[LineFieldClaim] = Field(min_length=5, max_length=5)

    _device_path = field_validator("stable_device_path")(_validate_nonblank)
    _identity_hash = field_validator("device_identity_sha256")(_validate_sha256)
    _serial = field_validator("device_serial")(_validate_nonblank)

    @model_validator(mode="after")
    def exact_field_claims(self) -> LineProtocolEvidenceContent:
        expected: dict[str, int | str] = {
            "unit_id": self.unit_id,
            "baud_rate": self.baud_rate,
            "data_bits": self.data_bits,
            "parity": self.parity,
            "stop_bits": self.stop_bits,
        }
        actual = {claim.field: claim.observed_value for claim in self.field_claims}
        if len(actual) != len(self.field_claims) or actual != expected:
            raise ValueError("line field claims must exactly bind every resolved line value")
        if len({claim.source_record_sha256 for claim in self.field_claims}) != 1:
            raise ValueError("line field claims must share one successful line-probe record")
        _validate_stable_serial_device_path(self.stable_device_path)
        return self


class ContradictionResolutionEvidenceContent(StrictModel):
    kind: Literal["contradiction_resolution"]
    contradiction_ids: list[str] = Field(min_length=1)
    resolution_record_id: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def unique_contradictions(self) -> ContradictionResolutionEvidenceContent:
        if len(self.contradiction_ids) != len(set(self.contradiction_ids)):
            raise ValueError("resolved contradiction IDs must be unique")
        _validate_nonblank(self.resolution_record_id)
        return self


def _modbus_crc16(payload: bytes) -> int:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _valid_modbus_rtu_frame(frame: bytes) -> bool:
    return len(frame) >= MIN_MODBUS_RTU_FRAME_BYTES and int.from_bytes(
        frame[-2:], "little"
    ) == _modbus_crc16(frame[:-2])


def raw_observation_record_sha256(value: StrictModel | Mapping[str, Any]) -> str:
    document = value.model_dump(mode="json") if isinstance(value, StrictModel) else dict(value)
    document.pop("record_sha256", None)
    return _canonical_sha256(document)


class RawObservationRecordBase(StrictModel):
    record_schema_version: Literal[1]
    record_id: str = Field(min_length=1, max_length=128)
    sequence_number: int = Field(ge=0)
    previous_record_sha256: Sha256Digest | Literal["GENESIS"]
    record_sha256: Sha256Digest
    observed_at: AwareTimestamp
    point_id: str = Field(min_length=1, max_length=128)
    plan_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)

    _record = field_validator("record_id")(_validate_nonblank)
    _record_hash = field_validator("record_sha256")(_validate_sha256)
    _observed = field_validator("observed_at")(_validate_timestamp)
    _point = field_validator("point_id")(_validate_nonblank)
    _plan = field_validator("plan_id")(_validate_nonblank)
    _run = field_validator("run_id")(_validate_nonblank)


class RawObservationAuditRecord(RawObservationRecordBase):
    record_type: Literal["audit_event"]
    event: Literal["RUN_STARTED", "RUN_COMPLETED"]
    outcome: Literal["STARTED", "PASS"]

    @model_validator(mode="after")
    def coherent_event_outcome(self) -> RawObservationAuditRecord:
        if (self.event, self.outcome) not in {
            ("RUN_STARTED", "STARTED"),
            ("RUN_COMPLETED", "PASS"),
        }:
            raise ValueError("raw-observation audit event has an invalid outcome")
        return self


class RawModbusExchangeRecordBase(RawObservationRecordBase):
    sample_id: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=1, max_length=128)
    unit_id: int = Field(ge=1, le=247)
    function_code: Literal[1, 2, 3, 4]
    start_address: int = Field(ge=0, le=65535)
    quantity: int = Field(ge=1, le=125)
    request_rtu_hex: str = Field(min_length=16, max_length=16)
    response_rtu_hex: str = Field(min_length=10, max_length=510)
    request_crc_valid: Literal[True]
    response_crc_valid: Literal[True]
    decoded_raw: int | float

    _sample = field_validator("sample_id")(_validate_nonblank)
    _event = field_validator("event_id")(_validate_nonblank)

    @field_validator("request_rtu_hex", "response_rtu_hex")
    @classmethod
    def canonical_rtu_hex(cls, value: str) -> str:
        if len(value) % 2 != 0 or RTU_HEX_PATTERN.fullmatch(value) is None:
            raise ValueError("Modbus RTU frames must use even-length lowercase hexadecimal")
        return value

    @model_validator(mode="after")
    def valid_read_exchange(self) -> RawModbusExchangeRecordBase:
        request = bytes.fromhex(self.request_rtu_hex)
        response = bytes.fromhex(self.response_rtu_hex)
        if not _valid_modbus_rtu_frame(request) or not _valid_modbus_rtu_frame(response):
            raise ValueError("Modbus RTU observation has an invalid CRC")
        if request[:2] != bytes((self.unit_id, self.function_code)) or (
            int.from_bytes(request[2:4], "big"),
            int.from_bytes(request[4:6], "big"),
        ) != (self.start_address, self.quantity):
            raise ValueError("Modbus RTU request does not match its declared scope")
        expected_byte_count = (
            (self.quantity + 7) // 8 if self.function_code in {1, 2} else self.quantity * 2
        )
        if (
            response[:2] != bytes((self.unit_id, self.function_code))
            or response[2] != expected_byte_count
            or len(response) != expected_byte_count + 5
        ):
            raise ValueError("Modbus RTU response does not match the approved read request")
        if self.function_code in {1, 2} and self.quantity % 8:
            valid_mask = (1 << (self.quantity % 8)) - 1
            if response[-3] & ~valid_mask:
                raise ValueError("Modbus bit response has non-zero padding bits")
        _finite_number(self.decoded_raw, label="raw Modbus decoded value")
        return self


class RawModbusObservationRecord(RawModbusExchangeRecordBase):
    record_type: Literal["modbus_observation"]


class RawLineProbeObservationRecord(RawModbusExchangeRecordBase):
    """A real RTU exchange that also records the resolved serial-line identity."""

    record_type: Literal["line_probe_observation"]
    device_identity_sha256: Sha256Digest
    configuration_observed_at: AwareTimestamp
    configuration_readback: LineConfigurationReadback
    configuration_readback_sha256: Sha256Digest

    _identity_hash = field_validator("device_identity_sha256")(_validate_sha256)
    _configuration_observed = field_validator("configuration_observed_at")(_validate_timestamp)
    _configuration_readback_hash = field_validator("configuration_readback_sha256")(
        _validate_sha256
    )

    @model_validator(mode="after")
    def readback_precedes_exchange(self) -> RawLineProbeObservationRecord:
        if self.configuration_readback_sha256 != line_configuration_readback_sha256(
            self.configuration_readback
        ):
            raise ValueError("line configuration readback hash is invalid")
        if datetime.fromisoformat(self.configuration_observed_at) > datetime.fromisoformat(
            self.observed_at
        ):
            raise ValueError("line configuration readback must precede its RTU exchange")
        return self

    @property
    def stable_device_path(self) -> str:
        return self.configuration_readback.stable_device_path

    @property
    def device_serial(self) -> str:
        return self.configuration_readback.device_serial

    @property
    def baud_rate(self) -> int:
        return self.configuration_readback.baud_rate

    @property
    def data_bits(self) -> int:
        return self.configuration_readback.data_bits

    @property
    def parity(self) -> str:
        return self.configuration_readback.parity

    @property
    def stop_bits(self) -> int:
        return self.configuration_readback.stop_bits


RawObservationRecord = Annotated[
    RawObservationAuditRecord | RawModbusObservationRecord | RawLineProbeObservationRecord,
    Field(discriminator="record_type"),
]


class RawObservationEvidenceContent(StrictModel):
    kind: Literal["raw_observation"]
    evidence_schema_version: Literal[4]
    point_id: str = Field(min_length=1, max_length=128)
    plan_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    collector_tool_id: str = Field(min_length=1, max_length=256)
    collector_tool_sha256: Sha256Digest
    records: list[RawObservationRecord] = Field(min_length=3)
    chain_tip_sha256: Sha256Digest
    terminal_state: Literal["PASS"]

    _point = field_validator("point_id")(_validate_nonblank)
    _plan = field_validator("plan_id")(_validate_nonblank)
    _run = field_validator("run_id")(_validate_nonblank)
    _collector = field_validator("collector_tool_id")(_validate_nonblank)
    _collector_hash = field_validator("collector_tool_sha256")(_validate_sha256)
    _chain_tip = field_validator("chain_tip_sha256")(_validate_sha256)

    @model_validator(mode="after")
    def complete_audit_chain(self) -> RawObservationEvidenceContent:  # noqa: PLR0912
        record_ids = [record.record_id for record in self.records]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("raw-observation audit record IDs must be unique")
        if [record.sequence_number for record in self.records] != list(range(len(self.records))):
            raise ValueError("raw-observation audit sequence must be contiguous from zero")
        expected_scope = (self.point_id, self.plan_id, self.run_id)
        if any(
            (record.point_id, record.plan_id, record.run_id) != expected_scope
            for record in self.records
        ):
            raise ValueError("raw-observation audit records must bind their content scope")
        first, last = self.records[0], self.records[-1]
        if not (
            isinstance(first, RawObservationAuditRecord)
            and first.event == "RUN_STARTED"
            and isinstance(last, RawObservationAuditRecord)
            and last.event == "RUN_COMPLETED"
        ):
            raise ValueError("raw-observation audit chain requires start and completed terminals")
        audit_events = [
            record.event for record in self.records if isinstance(record, RawObservationAuditRecord)
        ]
        observations = [
            record for record in self.records if isinstance(record, RawModbusExchangeRecordBase)
        ]
        line_probes = [
            record for record in self.records if isinstance(record, RawLineProbeObservationRecord)
        ]
        if audit_events != ["RUN_STARTED", "RUN_COMPLETED"] or not observations:
            raise ValueError("raw-observation audit chain has invalid event membership")
        if len(line_probes) > 1:
            raise ValueError("raw-observation audit chain permits at most one line probe")
        if line_probes:
            probe_index = self.records.index(line_probes[0])
            first_observation_index = next(
                index
                for index, record in enumerate(self.records)
                if isinstance(record, RawModbusExchangeRecordBase)
            )
            if probe_index != first_observation_index:
                raise ValueError("line probe must be the first Modbus observation")
            run_started_at = datetime.fromisoformat(first.observed_at)
            configuration_observed_at = datetime.fromisoformat(
                line_probes[0].configuration_observed_at
            )
            if not run_started_at <= configuration_observed_at:
                raise ValueError("line configuration readback must occur after the run starts")
        sample_ids = [record.sample_id for record in observations]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("raw Modbus observation sample IDs must be unique")
        timestamps = [datetime.fromisoformat(record.observed_at) for record in self.records]
        if timestamps != sorted(timestamps) or len(timestamps) != len(set(timestamps)):
            raise ValueError("raw-observation audit records must be strictly time ordered")
        previous_sha256 = "GENESIS"
        for record in self.records:
            if record.previous_record_sha256 != previous_sha256:
                raise ValueError("raw-observation audit hash chain is broken")
            if record.record_sha256 != raw_observation_record_sha256(record):
                raise ValueError("raw-observation audit record hash is invalid")
            previous_sha256 = record.record_sha256
        if self.chain_tip_sha256 != previous_sha256:
            raise ValueError("raw-observation audit chain tip does not match its final record")
        return self


EvidenceContent = Annotated[
    IdentityEvidenceContent
    | AuthoritativeMapEvidenceContent
    | LineProtocolEvidenceContent
    | AnalogCalibrationEvidence
    | BinaryCalibrationEvidence
    | CounterCalibrationEvidence
    | AnalogReferenceEvidence
    | BinaryReferenceEvidence
    | CounterReferenceEvidence
    | ContradictionResolutionEvidenceContent
    | RawObservationEvidenceContent,
    Field(discriminator="kind"),
]


class EvidenceArtifact(StrictModel):
    schema_version: Literal[1]
    artifact_type: Literal["ruisheng.device-point-profile-evidence"]
    evidence_id: str = Field(min_length=1, max_length=128)
    role: EvidenceRole
    profile_id: str = Field(min_length=1, max_length=128)
    device_identity_sha256: Sha256Digest
    device_serial: str = Field(min_length=1, max_length=128)
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    calibration_run_approval_sha256: Sha256Digest | None = None
    subject_point_ids: list[str]
    observed_at: AwareTimestamp
    attestor_id: str = Field(min_length=1, max_length=128)
    content: EvidenceContent
    signature: DetachedSignature

    _observed = field_validator("observed_at")(_validate_timestamp)
    _attestor = field_validator("attestor_id")(_validate_nonblank)
    _profile = field_validator("profile_id")(_validate_nonblank)
    _identity_hash = field_validator("device_identity_sha256")(_validate_sha256)
    _serial = field_validator("device_serial")(_validate_nonblank)

    @field_validator("calibration_run_approval_sha256")
    @classmethod
    def valid_run_approval_hash(cls, value: str | None) -> str | None:
        return None if value is None else _validate_sha256(value)

    @model_validator(mode="after")
    def role_and_subject_contract(self) -> EvidenceArtifact:
        kind_to_role: dict[str, EvidenceRole] = {
            "identity": "identity",
            "authoritative_map": "authoritative_map",
            "line_protocol": "line_protocol",
            "analog_calibration": "calibration",
            "binary_calibration": "calibration",
            "counter_calibration": "calibration",
            "analog_reference": "reference",
            "binary_reference": "reference",
            "counter_reference": "reference",
            "contradiction_resolution": "contradiction_resolution",
            "raw_observation": "raw_observation",
        }
        if self.role != kind_to_role[self.content.kind]:
            raise ValueError("evidence role does not match its versioned content kind")
        if len(self.subject_point_ids) != len(set(self.subject_point_ids)):
            raise ValueError("evidence subject point IDs must be unique")
        if hasattr(self.content, "point_id") and self.subject_point_ids != [self.content.point_id]:
            raise ValueError("point evidence must name exactly its content point")
        if isinstance(self.content, AuthoritativeMapEvidenceContent) and set(
            self.subject_point_ids
        ) != {point.point_id for point in self.content.points}:
            raise ValueError("map evidence subjects must equal its mapped point IDs")
        if (
            isinstance(self.content, IdentityEvidenceContent | LineProtocolEvidenceContent)
            and self.subject_point_ids
        ):
            raise ValueError("device-level evidence cannot claim point subjects")
        if self.role in {"calibration", "reference", "raw_observation"} and (
            self.run_id is None or self.calibration_run_approval_sha256 is None
        ):
            raise ValueError("run evidence must bind a run ID and calibration-run approval")
        if (
            isinstance(self.content, RawObservationEvidenceContent)
            and self.content.run_id != self.run_id
        ):
            raise ValueError("raw-observation content run must match its artifact run")
        if self.role not in {"calibration", "reference", "raw_observation"} and (
            self.run_id is not None or self.calibration_run_approval_sha256 is not None
        ):
            raise ValueError("non-run evidence cannot carry calibration-run bindings")
        if isinstance(self.content, AuthoritativeMapEvidenceContent) and (
            self.content.device_identity_sha256 != self.device_identity_sha256
            or self.content.device_serial != self.device_serial
        ):
            raise ValueError("map evidence identity scope must match its artifact binding")
        if isinstance(self.content, LineProtocolEvidenceContent) and (
            self.content.device_identity_sha256 != self.device_identity_sha256
            or self.content.device_serial != self.device_serial
        ):
            raise ValueError("line evidence identity scope must match its artifact binding")
        return self


class PointEncoding(StrictModel):
    value_type: Literal["unknown", "bit", "u16", "s16", "u32", "s32", "float32", "bcd"]
    byte_order: Literal["unknown", "big", "little", "not_applicable"]
    word_order: Literal["unknown", "high_first", "low_first", "not_applicable"]
    raw_domain: RawDomain | None = None


class DeviceIdentity(StrictModel):
    status: ResolutionStatus
    model: str | None = Field(default=None, min_length=1, max_length=128)
    hardware_revision: str | None = Field(default=None, min_length=1, max_length=128)
    firmware_version: str | None = Field(default=None, min_length=1, max_length=128)
    point_map_version: str | None = Field(default=None, min_length=1, max_length=128)
    usb_serial_number: str | None = Field(default=None, min_length=1, max_length=128)
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator(
        "model", "hardware_revision", "firmware_version", "point_map_version", "usb_serial_number"
    )
    @classmethod
    def nonblank_values(cls, value: str | None) -> str | None:
        return None if value is None else _validate_nonblank(value)


def canonical_device_identity_sha256(identity: DeviceIdentity | Mapping[str, Any]) -> str:
    value = (
        identity.model_dump(mode="json") if isinstance(identity, DeviceIdentity) else dict(identity)
    )
    value.pop("evidence_refs", None)
    return _canonical_sha256(value)


class LineProtocol(StrictModel):
    status: ResolutionStatus
    stable_device_path: str | None = Field(default=None, min_length=1, max_length=1024)
    unit_id: int | None = Field(default=None, ge=1, le=247)
    baud_rate: Literal[1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200] | None = None
    data_bits: Literal[8] | None = None
    parity: Literal["N", "E", "O"] | None = None
    stop_bits: Literal[1, 2] | None = None
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator("stable_device_path")
    @classmethod
    def stable_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_stable_serial_device_path(value)


class PointProfile(StrictModel):
    point_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    point_name: str = Field(min_length=1, max_length=128)
    function_code: Literal[1, 2, 3, 4]
    start_address: int = Field(ge=0, le=65535)
    register_width: int | None = Field(default=None, ge=1, le=125)
    bit: int | None = Field(default=None, ge=0, le=15)
    identity_status: ResolutionStatus
    semantic_status: ResolutionStatus
    encoding_status: ResolutionStatus
    unit_status: ResolutionStatus
    calibration_status: CalibrationStatus
    implementation_status: ImplementationStatus
    encoding: PointEncoding
    unit: str | None = Field(default=None, min_length=1, max_length=32)
    calibration_profile: CalibrationProfile
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def protocol_shape(self) -> PointProfile:  # noqa: PLR0912 - one closed geometry contract
        value_type = self.encoding.value_type
        expected_width = {
            "bit": 1,
            "u16": 1,
            "s16": 1,
            "bcd": 1,
            "u32": 2,
            "s32": 2,
            "float32": 2,
        }.get(value_type)
        if self.function_code in (1, 2):
            if self.bit is not None:
                raise ValueError("FC1/FC2 addresses are coils/discrete inputs and cannot carry bit")
            if self.register_width not in (None, 1):
                raise ValueError("FC1/FC2 width must be one when specified")
            if value_type not in {"unknown", "bit"}:
                raise ValueError("FC1/FC2 values must use bit encoding")
        elif value_type == "bit":
            if self.bit is None:
                raise ValueError("register bit profiles require bit")
        elif self.bit is not None:
            raise ValueError("bit is only valid for register bit profiles")
        if expected_width is not None and self.register_width not in (None, expected_width):
            raise ValueError("register width does not match the encoded value width")
        if (
            self.register_width is not None
            and self.start_address + self.register_width > MODBUS_ADDRESS_SPACE
        ):
            raise ValueError("point register range exceeds the Modbus address space")
        if value_type == "bit" and (
            self.encoding.byte_order not in {"unknown", "not_applicable"}
            or self.encoding.word_order not in {"unknown", "not_applicable"}
        ):
            raise ValueError("bit encoding cannot carry byte or word order")
        if value_type in {"u16", "s16", "bcd"} and self.encoding.word_order not in {
            "unknown",
            "not_applicable",
        }:
            raise ValueError("single-register encoding cannot carry word order")
        if value_type in {"u32", "s32", "float32"} and self.encoding.word_order == "not_applicable":
            raise ValueError("multi-register encoding requires word order")
        if self.function_code in (1, 2) and self.calibration_profile.kind not in {
            "unknown",
            "binary",
        }:
            raise ValueError("FC1/FC2 points require a binary calibration profile")
        if self.calibration_profile.kind == "analog" and self.encoding.value_type == "bit":
            raise ValueError("analog calibration cannot be applied to a bit encoding")
        if self.calibration_profile.kind == "counter" and self.encoding.value_type == "bit":
            raise ValueError("counter calibration cannot be applied to a bit encoding")
        domain_limits: dict[str, tuple[int | float, int | float]] = {
            "bit": (0, 1),
            "u16": (0, 65535),
            "s16": (-32768, 32767),
            "u32": (0, 4294967295),
            "s32": (-2147483648, 2147483647),
            "float32": (-3.4028235e38, 3.4028235e38),
            "bcd": (0, 9999),
        }
        if self.encoding.raw_domain is not None and value_type in domain_limits:
            lower, upper = domain_limits[value_type]
            if self.encoding.raw_domain.minimum < lower or self.encoding.raw_domain.maximum > upper:
                raise ValueError("raw domain exceeds the encoded value domain")
        if isinstance(self.calibration_profile, BinaryCalibrationProfile):
            states = (self.calibration_profile.inactive_raw, self.calibration_profile.active_raw)
            if (
                self.encoding.raw_domain is not None
                and all(state is not None for state in states)
                and any(
                    state < self.encoding.raw_domain.minimum
                    or state > self.encoding.raw_domain.maximum
                    for state in states
                    if state is not None
                )
            ):
                raise ValueError("binary states must be inside the raw domain")
        if isinstance(self.calibration_profile, CounterCalibrationProfile):
            if value_type not in {"unknown", "u16", "u32"}:
                raise ValueError("counter calibration requires unsigned integer encoding")
            if (
                self.calibration_profile.modulus is not None
                and value_type in domain_limits
                and self.calibration_profile.modulus - 1 > domain_limits[value_type][1]
            ):
                raise ValueError("counter modulus exceeds the encoded value domain")
        return self


class ProfilePayload(StrictModel):
    device_identity: DeviceIdentity
    line_protocol: LineProtocol
    points: list[PointProfile] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_points(self) -> ProfilePayload:
        point_ids = [point.point_id for point in self.points]
        if len(point_ids) != len(set(point_ids)):
            raise ValueError("point IDs must be unique")
        resolved_points = [
            point
            for point in self.points
            if point.identity_status == "resolved" and point.encoding_status == "resolved"
        ]
        for index, left in enumerate(resolved_points):
            left_width = left.register_width or 1
            left_end = left.start_address + left_width
            for right in resolved_points[index + 1 :]:
                if left.function_code != right.function_code:
                    continue
                right_width = right.register_width or 1
                right_end = right.start_address + right_width
                if left.start_address >= right_end or right.start_address >= left_end:
                    continue
                left_is_register_bit = left.function_code in (3, 4) and left.bit is not None
                right_is_register_bit = right.function_code in (3, 4) and right.bit is not None
                if (
                    left_is_register_bit
                    and right_is_register_bit
                    and left.start_address == right.start_address
                    and left.bit != right.bit
                ):
                    continue
                raise ValueError(
                    "resolved Modbus point ranges must not overlap without an explicit alias contract: "
                    f"{left.point_id} conflicts with {right.point_id}"
                )
        return self


class Contradiction(StrictModel):
    contradiction_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    )
    status: ContradictionStatus
    summary: str = Field(min_length=1, max_length=1000)
    subject_point_ids: list[str] = Field(default_factory=list)
    resolution: str | None = Field(default=None, min_length=1, max_length=2000)
    resolution_evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def resolution_contract(self) -> Contradiction:
        _validate_nonblank(self.summary)
        if self.status == "resolved":
            if self.resolution is None or not self.resolution_evidence_refs:
                raise ValueError("resolved contradiction requires resolution and evidence")
            _validate_nonblank(self.resolution)
        elif self.resolution is not None or self.resolution_evidence_refs:
            raise ValueError("open contradiction cannot contain resolution fields")
        if len(self.subject_point_ids) != len(set(self.subject_point_ids)):
            raise ValueError("contradiction subject points must be unique")
        if len(self.resolution_evidence_refs) != len(set(self.resolution_evidence_refs)):
            raise ValueError("contradiction resolution evidence refs must be unique")
        return self


class ReleaseVerificationReceiptBinding(ArtifactBinding):
    receipt_id: str = Field(min_length=1, max_length=128)

    _receipt = field_validator("receipt_id")(_validate_nonblank)


class ReleaseReceiptCheck(StrictModel):
    check_id: Literal[
        "OPENSSH_SSHSIG_VERIFIED",
        "SHA256SUMS_ALLOWLIST_VERIFIED",
        "PACKAGE_HASHES_VERIFIED",
        "MANIFEST_VERIFIED",
        "ARCHIVE_IDENTITIES_VERIFIED",
        "MIGRATION_HEAD_VERIFIED",
    ]
    result: Literal["PASS"]
    observed_sha256: Sha256Digest

    _observed_hash = field_validator("observed_sha256")(_validate_sha256)


class VerifiedImageReceipt(StrictModel):
    component: Literal["postgres", "redis", "api", "gw", "web"]
    image_id: Sha256Digest
    archive_sha256: Sha256Digest
    os: str = Field(min_length=1, max_length=64)
    architecture: str = Field(min_length=1, max_length=64)

    _image_hash = field_validator("image_id")(_validate_sha256)
    _archive_hash = field_validator("archive_sha256")(_validate_sha256)
    _os = field_validator("os")(_validate_nonblank)
    _architecture = field_validator("architecture")(_validate_nonblank)


class ReleaseVerificationReceipt(StrictModel):
    schema_version: Literal[1]
    artifact_type: Literal["ruisheng.release-verification-receipt"]
    receipt_id: str = Field(min_length=1, max_length=128)
    verification_method: Literal["openssh-sha256sums-protected-snapshot/v1"]
    verifier_id: str = Field(min_length=1, max_length=128)
    verifier_tool_id: str = Field(min_length=1, max_length=256)
    verifier_tool_sha256: Sha256Digest
    verified_at: AwareTimestamp
    protected_snapshot_id: Sha256Digest
    publisher_principal: Literal["ruisheng-release"]
    signature_namespace: Literal["ruisheng-candidate-v1"]
    signed_object: Literal["SHA256SUMS"]
    signature_file: Literal["SHA256SUMS.sig"]
    release_key_fingerprint: str = Field(pattern=r"^SHA256:[A-Za-z0-9+/]{43}$")
    sha256sums_sha256: Sha256Digest
    signature_file_sha256: Sha256Digest
    manifest_sha256: Sha256Digest
    package_file_set_sha256: Sha256Digest
    candidate_id: str = Field(
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9][a-z0-9._-]{0,62}$",
    )
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    logical_identity: Sha256Digest
    alembic_head: str = Field(min_length=1, max_length=128)
    observed_alembic_head: str = Field(min_length=1, max_length=128)
    images: list[VerifiedImageReceipt] = Field(min_length=5, max_length=5)
    checks: list[ReleaseReceiptCheck] = Field(min_length=6, max_length=6)
    signature: OpenSshDetachedSignature

    _receipt = field_validator("receipt_id")(_validate_nonblank)
    _verifier = field_validator("verifier_id")(_validate_nonblank)
    _verifier_tool = field_validator("verifier_tool_id")(_validate_nonblank)
    _verifier_tool_hash = field_validator("verifier_tool_sha256")(_validate_sha256)
    _verified = field_validator("verified_at")(_validate_timestamp)
    _snapshot = field_validator("protected_snapshot_id")(_validate_nonblank)
    _sums_hash = field_validator("sha256sums_sha256")(_validate_sha256)
    _signature_hash = field_validator("signature_file_sha256")(_validate_sha256)
    _manifest_hash = field_validator("manifest_sha256")(_validate_sha256)
    _file_set_hash = field_validator("package_file_set_sha256")(_validate_sha256)
    _logical_hash = field_validator("logical_identity")(_validate_sha256)
    _alembic = field_validator("alembic_head")(_validate_nonblank)
    _observed_alembic = field_validator("observed_alembic_head")(_validate_nonblank)

    @model_validator(mode="after")
    def complete_receipt(self) -> ReleaseVerificationReceipt:
        components = [image.component for image in self.images]
        if components != ["postgres", "redis", "api", "gw", "web"]:
            raise ValueError("release receipt must contain all five actual images in order")
        check_ids = [check.check_id for check in self.checks]
        if (
            len(check_ids) != len(set(check_ids))
            or set(check_ids) != REQUIRED_RELEASE_RECEIPT_CHECKS
        ):
            raise ValueError("release receipt must contain every verification check exactly once")
        if self.observed_alembic_head != self.alembic_head:
            raise ValueError("release receipt migration head did not match the candidate manifest")
        if self.receipt_id != f"receipt-{self.protected_snapshot_id.removeprefix('sha256:')}":
            raise ValueError("release receipt ID must derive from the protected snapshot")
        return self


class RuntimeTarget(StrictModel):
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    candidate_id: str = Field(
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9][a-z0-9._-]{0,62}$",
    )
    logical_identity: Sha256Digest
    api_image_digest: Sha256Digest
    gateway_image_digest: Sha256Digest
    alembic_head: str = Field(min_length=1, max_length=128)
    release_verification_receipt: ReleaseVerificationReceiptBinding

    @field_validator("source_commit")
    @classmethod
    def full_commit(cls, value: str) -> str:
        if COMMIT_PATTERN.fullmatch(value) is None:
            raise ValueError("source_commit must be 40 lowercase hexadecimal characters")
        return value

    _logical_hash = field_validator("logical_identity")(_validate_sha256)
    _api_hash = field_validator("api_image_digest")(_validate_sha256)
    _gateway_hash = field_validator("gateway_image_digest")(_validate_sha256)
    _alembic = field_validator("alembic_head")(_validate_nonblank)


class RuntimeEvidenceBinding(ArtifactBinding):
    check_id: RuntimeCheckId


class ApprovalBinding(ArtifactBinding):
    subject_gate_sha256: Sha256Digest

    _subject_hash = field_validator("subject_gate_sha256")(_validate_sha256)


class CalibrationRunApprovalBinding(ArtifactBinding):
    subject_plan_sha256: Sha256Digest

    _subject_hash = field_validator("subject_plan_sha256")(_validate_sha256)


class DevicePointProfile(StrictModel):
    schema_version: Literal[1]
    artifact_type: Literal["ruisheng.device-point-profile"]
    profile_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    created_at: AwareTimestamp
    semantic_validator: Literal["ruisheng.device-point-profile-validator/v5"]
    validator_source_sha256: Sha256Digest
    policy_sha256: Sha256Digest | None
    trust_root_sha256: Sha256Digest | None
    schema_sha256: Sha256Digest
    profile_payload: ProfilePayload
    payload_sha256: Sha256Digest
    evidence_bindings: list[EvidenceBinding] = Field(min_length=1)
    calibration_run_approval_binding: CalibrationRunApprovalBinding | None = None
    approval_binding: ApprovalBinding | None = None
    runtime_target: RuntimeTarget | None = None
    runtime_evidence: list[RuntimeEvidenceBinding] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)

    _created = field_validator("created_at")(_validate_timestamp)
    _validator_source_hash = field_validator("validator_source_sha256")(_validate_sha256)
    _schema_hash = field_validator("schema_sha256")(_validate_sha256)
    _payload_hash = field_validator("payload_sha256")(_validate_sha256)

    @field_validator("policy_sha256")
    @classmethod
    def valid_policy_hash(cls, value: str | None) -> str | None:
        return None if value is None else _validate_sha256(value)

    @field_validator("trust_root_sha256")
    @classmethod
    def valid_trust_root_hash(cls, value: str | None) -> str | None:
        return None if value is None else _validate_sha256(value)

    @model_validator(mode="after")
    def unique_contract_ids(self) -> DevicePointProfile:
        evidence_ids = [item.evidence_id for item in self.evidence_bindings]
        paths = [item.path for item in self.evidence_bindings]
        paths.extend(item.path for item in self.runtime_evidence)
        if self.approval_binding is not None:
            paths.append(self.approval_binding.path)
        if self.calibration_run_approval_binding is not None:
            paths.append(self.calibration_run_approval_binding.path)
        if self.runtime_target is not None:
            paths.append(self.runtime_target.release_verification_receipt.path)
        check_ids = [item.check_id for item in self.runtime_evidence]
        contradiction_ids = [item.contradiction_id for item in self.contradictions]
        for values, label in (
            (evidence_ids, "evidence IDs"),
            (paths, "evidence paths"),
            (check_ids, "runtime check IDs"),
            (contradiction_ids, "contradiction IDs"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")
        return self


class ApprovalEntry(StrictModel):
    role: ApprovalRole
    key_id: str = Field(min_length=1, max_length=128)
    identity: str = Field(min_length=1, max_length=256)
    approved_at: AwareTimestamp
    signature: DetachedSignature

    _approved = field_validator("approved_at")(_validate_timestamp)
    _key = field_validator("key_id")(_validate_nonblank)
    _identity = field_validator("identity")(_validate_nonblank)

    @model_validator(mode="after")
    def matching_signature_key(self) -> ApprovalEntry:
        if self.signature.key_id != self.key_id:
            raise ValueError("approval signature key does not match approval key_id")
        return self


class CalibrationTxScope(StrictModel):
    function_code: Literal[1, 2, 3, 4]
    start_address: int = Field(ge=0, le=65535)
    quantity: int = Field(ge=1, le=125)
    maximum_requests: int = Field(ge=1)
    write_allowed: Literal[False]

    @model_validator(mode="after")
    def valid_range(self) -> CalibrationTxScope:
        if self.start_address + self.quantity > MODBUS_ADDRESS_SPACE:
            raise ValueError("calibration TX range exceeds the Modbus address space")
        return self


class BinaryUnintervenedChannelPlan(StrictModel):
    control_id: str = Field(min_length=1, max_length=128)
    point_id: str = Field(min_length=1, max_length=128)
    point_name: str = Field(min_length=1, max_length=128)
    address_semantics: BinaryAddressSemantics
    expected_raw: int | float

    _control = field_validator("control_id")(_validate_nonblank)
    _point = field_validator("point_id")(_validate_nonblank)
    _point_name = field_validator("point_name")(_validate_nonblank)
    _raw = field_validator("expected_raw")(
        lambda value: _finite_number(value, label="binary control expected raw")
    )


class CalibrationPointPlan(StrictModel):
    plan_id: str = Field(min_length=1, max_length=128)
    point_id: str = Field(min_length=1, max_length=128)
    point_name: str = Field(min_length=1, max_length=128)
    point_unit: str = Field(min_length=1, max_length=32)
    function_code: Literal[1, 2, 3, 4]
    start_address: int = Field(ge=0, le=65535)
    register_width: int = Field(ge=1, le=125)
    bit: int | None = Field(default=None, ge=0, le=15)
    value_type: Literal["bit", "u16", "s16", "u32", "s32", "float32", "bcd"]
    byte_order: Literal["big", "little", "not_applicable"]
    word_order: Literal["high_first", "low_first", "not_applicable"]
    raw_domain: RawDomain
    calibration_kind: Literal["analog", "binary", "counter"]
    state_ids: list[str] = Field(min_length=3)
    sample_count_per_state: int = Field(ge=MIN_CALIBRATION_SAMPLES_PER_STATE)
    instrument_id: str = Field(min_length=1, max_length=256)
    instrument_calibration_sha256: Sha256Digest
    reference_channel_id: str = Field(min_length=1, max_length=128)
    reference_unit: str = Field(min_length=1, max_length=32)
    sync_tolerance_ms: int | float = Field(gt=0)
    stability_threshold: int | float = Field(ge=0)
    minimum_raw_span: int | float = Field(gt=0)
    minimum_reference_span: int | float = Field(gt=0)
    absolute_tolerance: int | float = Field(ge=0)
    relative_tolerance: int | float = Field(ge=0)
    uncertainty_budget: int | float = Field(gt=0)
    analog_aggregation_method: Literal["arithmetic_mean"] | None = None
    analog_unit_conversion: UnitConversion | None = None
    analog_exclusion_policy: AnalogExclusionPolicy | None = None
    analog_business_tolerance_source: BusinessToleranceSource | None = None
    analog_instrument_capability: ReferenceInstrumentCapability | None = None
    maximum_reference_uncertainty: int | float | None = Field(default=None, gt=0)
    return_raw_tolerance: int | float | None = Field(default=None, ge=0)
    return_engineering_tolerance: int | float | None = Field(default=None, ge=0)
    maximum_chatter_transitions: int | None = Field(default=None, ge=0)
    binary_selected_candidate_id: str | None = Field(default=None, min_length=1, max_length=128)
    binary_address_candidates: list[BinaryAddressSemantics] | None = None
    binary_unintervened_channels: list[BinaryUnintervenedChannelPlan] | None = None
    expected_counter_increment: int | float | None = Field(default=None, gt=0)
    counter_increment_tolerance: int | float | None = Field(default=None, ge=0)
    counter_modulus: int | None = Field(default=None, gt=1)
    counter_rollover_behavior: Literal["wrap", "saturate", "reset"] | None = None
    counter_persistence_method: Literal["physical_power_disconnect"] | None = None
    minimum_power_off_duration_seconds: int | float | None = Field(default=None, gt=0)
    persistence_required: bool
    tx_scope: list[CalibrationTxScope] = Field(min_length=1)
    safety_plan_id: str = Field(min_length=1, max_length=256)
    operator_id: str = Field(min_length=1, max_length=128)
    raw_collector_tool_id: str = Field(min_length=1, max_length=256)
    raw_collector_tool_sha256: Sha256Digest
    reference_collector_tool_id: str = Field(min_length=1, max_length=256)
    reference_collector_tool_sha256: Sha256Digest

    _plan = field_validator("plan_id")(_validate_nonblank)
    _point_name = field_validator("point_name")(_validate_nonblank)
    _point_unit = field_validator("point_unit")(_validate_nonblank)
    _instrument = field_validator("instrument_id")(_validate_nonblank)
    _instrument_hash = field_validator("instrument_calibration_sha256")(_validate_sha256)
    _channel = field_validator("reference_channel_id")(_validate_nonblank)
    _unit = field_validator("reference_unit")(_validate_nonblank)
    _safety = field_validator("safety_plan_id")(_validate_nonblank)
    _operator = field_validator("operator_id")(_validate_nonblank)
    _collector = field_validator("raw_collector_tool_id")(_validate_nonblank)
    _collector_hash = field_validator("raw_collector_tool_sha256")(_validate_sha256)
    _reference_collector = field_validator("reference_collector_tool_id")(_validate_nonblank)
    _reference_collector_hash = field_validator("reference_collector_tool_sha256")(_validate_sha256)

    @model_validator(mode="after")
    def complete_plan(self) -> CalibrationPointPlan:  # noqa: PLR0912, PLR0915
        if len(self.state_ids) != len(set(self.state_ids)):
            raise ValueError("calibration plan state IDs must be unique")
        expected_states = {
            "analog": ["A", "B", "C", "A_RETURN"],
            "binary": ["INACTIVE", "ACTIVE", "RETURN"],
            "counter": ["BASELINE", "INCREMENT", "ROLLOVER", "PERSISTENCE"],
        }[self.calibration_kind]
        if self.state_ids != expected_states:
            raise ValueError("calibration plan state sequence does not match its kind")
        for field_name in (
            "sync_tolerance_ms",
            "stability_threshold",
            "minimum_raw_span",
            "minimum_reference_span",
            "absolute_tolerance",
            "relative_tolerance",
            "uncertainty_budget",
        ):
            _finite_number(getattr(self, field_name), label=f"calibration plan {field_name}")
        for field_name in (
            "return_raw_tolerance",
            "return_engineering_tolerance",
            "expected_counter_increment",
            "counter_increment_tolerance",
            "maximum_reference_uncertainty",
            "minimum_power_off_duration_seconds",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _finite_number(value, label=f"calibration plan {field_name}")
        if self.absolute_tolerance < self.uncertainty_budget:
            raise ValueError("plan tolerance cannot be smaller than its uncertainty budget")
        analog_values = (
            self.analog_aggregation_method,
            self.analog_unit_conversion,
            self.analog_exclusion_policy,
            self.analog_business_tolerance_source,
            self.analog_instrument_capability,
            self.maximum_reference_uncertainty,
        )
        if self.calibration_kind == "analog":
            if any(value is None for value in analog_values):
                raise ValueError(
                    "analog plan requires aggregation, units, exclusions, tolerance source, and instrument limits"
                )
            if self.analog_unit_conversion is not None and (
                self.analog_unit_conversion.source_unit != self.reference_unit
                or self.analog_unit_conversion.target_unit != self.point_unit
            ):
                raise ValueError("analog unit conversion must bind reference and point units")
        elif any(value is not None for value in analog_values):
            raise ValueError("only analog plans can carry analog evidence policy")
        if self.calibration_kind != "analog" and self.reference_unit != self.point_unit:
            raise ValueError("binary and counter reference units must equal the point unit")
        if self.calibration_kind == "binary":
            if (
                self.maximum_chatter_transitions is None
                or self.binary_selected_candidate_id is None
                or not self.binary_address_candidates
                or len(self.binary_address_candidates) < MIN_BINARY_ADDRESS_CANDIDATES
                or not self.binary_unintervened_channels
            ):
                raise ValueError(
                    "binary plan requires chatter, candidate, and unintervened-channel controls"
                )
            candidate_ids = [item.candidate_id for item in self.binary_address_candidates]
            if (
                len(candidate_ids) != len(set(candidate_ids))
                or self.binary_selected_candidate_id not in candidate_ids
            ):
                raise ValueError("binary plan candidate identities are inconsistent")
            selected = next(
                item
                for item in self.binary_address_candidates
                if item.candidate_id == self.binary_selected_candidate_id
            )
            if (
                selected.function_code,
                selected.start_address,
                selected.register_width,
                selected.bit,
            ) != (
                self.function_code,
                self.start_address,
                self.register_width,
                self.bit,
            ):
                raise ValueError("binary selected candidate does not match the point address")
            control_ids = [item.control_id for item in self.binary_unintervened_channels]
            if len(control_ids) != len(set(control_ids)) or any(
                item.point_id == self.point_id for item in self.binary_unintervened_channels
            ):
                raise ValueError("binary unintervened-channel plans must be unique other points")
        elif self.maximum_chatter_transitions is not None:
            raise ValueError("only binary plans can carry a chatter threshold")
        if self.calibration_kind != "binary" and any(
            value is not None
            for value in (
                self.binary_selected_candidate_id,
                self.binary_address_candidates,
                self.binary_unintervened_channels,
            )
        ):
            raise ValueError("only binary plans can carry candidate controls")
        return_tolerances = (self.return_raw_tolerance, self.return_engineering_tolerance)
        if self.calibration_kind == "analog":
            if any(value is None for value in return_tolerances):
                raise ValueError("analog plan requires return thresholds")
        elif any(value is not None for value in return_tolerances):
            raise ValueError("only analog plans can carry return thresholds")
        counter_values = (
            self.expected_counter_increment,
            self.counter_increment_tolerance,
            self.counter_modulus,
            self.counter_rollover_behavior,
            self.counter_persistence_method,
            self.minimum_power_off_duration_seconds,
        )
        if self.calibration_kind == "counter":
            if any(value is None for value in counter_values) or not self.persistence_required:
                raise ValueError(
                    "counter plan requires increment, rollover, modulus, and persistence"
                )
        elif any(value is not None for value in counter_values) or self.persistence_required:
            raise ValueError("only counter plans can carry counter behavior or persistence")
        approved_scopes = [
            (scope.function_code, scope.start_address, scope.quantity) for scope in self.tx_scope
        ]
        if len(approved_scopes) != len(set(approved_scopes)):
            raise ValueError("calibration TX scopes must be unique")
        required_scopes = {(self.function_code, self.start_address, self.register_width)}
        if self.calibration_kind == "binary":
            required_scopes.update(
                (item.function_code, item.start_address, item.register_width)
                for item in (self.binary_address_candidates or [])
            )
            required_scopes.update(
                (
                    item.address_semantics.function_code,
                    item.address_semantics.start_address,
                    item.address_semantics.register_width,
                )
                for item in (self.binary_unintervened_channels or [])
            )
        if not required_scopes.issubset(set(approved_scopes)):
            raise ValueError("calibration TX scopes do not cover every planned observation")
        if self.calibration_kind == "counter":
            required_requests = len(COUNTER_STATE_SEQUENCE) * self.sample_count_per_state
            primary_scope = next(
                scope
                for scope in self.tx_scope
                if (
                    scope.function_code,
                    scope.start_address,
                    scope.quantity,
                )
                == (self.function_code, self.start_address, self.register_width)
            )
            if primary_scope.maximum_requests < required_requests:
                raise ValueError("counter TX budget does not cover every planned state sample")
        return self


class CalibrationRunApprovalArtifact(StrictModel):
    schema_version: Literal[1]
    artifact_type: Literal["ruisheng.device-point-profile-calibration-run-approval"]
    run_id: str = Field(min_length=1, max_length=128)
    subject_plan_sha256: Sha256Digest
    profile_id: str = Field(min_length=1, max_length=128)
    profile_input_sha256: Sha256Digest
    schema_sha256: Sha256Digest
    policy_sha256: Sha256Digest
    trust_root_sha256: Sha256Digest
    semantic_validator: Literal["ruisheng.device-point-profile-validator/v5"]
    validator_source_sha256: Sha256Digest
    device_identity_sha256: Sha256Digest
    device_serial: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=128)
    hardware_revision: str = Field(min_length=1, max_length=128)
    firmware_version: str = Field(min_length=1, max_length=128)
    point_map_version: str = Field(min_length=1, max_length=128)
    stable_device_path: str = Field(min_length=1, max_length=1024)
    unit_id: int = Field(ge=1, le=247)
    baud_rate: Literal[1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
    data_bits: Literal[8]
    parity: Literal["N", "E", "O"]
    stop_bits: Literal[1, 2]
    valid_from: AwareTimestamp
    expires_at: AwareTimestamp
    nonce: str = Field(min_length=16, max_length=256)
    safety_owner_present: Literal[True]
    emergency_stop_available: Literal[True]
    plans: list[CalibrationPointPlan] = Field(min_length=1)
    approvals: list[ApprovalEntry] = Field(min_length=4)

    _run = field_validator("run_id")(_validate_nonblank)
    _subject_hash = field_validator("subject_plan_sha256")(_validate_sha256)
    _profile = field_validator("profile_id")(_validate_nonblank)
    _profile_input_hash = field_validator("profile_input_sha256")(_validate_sha256)
    _schema_hash = field_validator("schema_sha256")(_validate_sha256)
    _policy_hash = field_validator("policy_sha256")(_validate_sha256)
    _root_hash = field_validator("trust_root_sha256")(_validate_sha256)
    _validator_source_hash = field_validator("validator_source_sha256")(_validate_sha256)
    _identity_hash = field_validator("device_identity_sha256")(_validate_sha256)
    _serial = field_validator("device_serial")(_validate_nonblank)
    _model = field_validator("model")(_validate_nonblank)
    _hardware = field_validator("hardware_revision")(_validate_nonblank)
    _firmware = field_validator("firmware_version")(_validate_nonblank)
    _map = field_validator("point_map_version")(_validate_nonblank)
    _device_path = field_validator("stable_device_path")(_validate_nonblank)
    _valid_from = field_validator("valid_from")(_validate_timestamp)
    _expires_at = field_validator("expires_at")(_validate_timestamp)

    @model_validator(mode="after")
    def approval_contract(self) -> CalibrationRunApprovalArtifact:
        roles = [approval.role for approval in self.approvals]
        if len(roles) != len(set(roles)) or set(roles) != REQUIRED_APPROVAL_ROLES:
            raise ValueError("calibration-run approval requires all four unique roles")
        if datetime.fromisoformat(self.valid_from) >= datetime.fromisoformat(self.expires_at):
            raise ValueError("calibration-run approval validity window is empty")
        plan_ids = [plan.plan_id for plan in self.plans]
        point_ids = [plan.point_id for plan in self.plans]
        if len(plan_ids) != len(set(plan_ids)) or len(point_ids) != len(set(point_ids)):
            raise ValueError("calibration-run plans and points must be unique")
        _validate_stable_serial_device_path(self.stable_device_path)
        return self


class EligibilityApprovalArtifact(StrictModel):
    schema_version: Literal[1]
    artifact_type: Literal["ruisheng.device-point-profile-eligibility-approval"]
    subject_gate_sha256: Sha256Digest
    schema_sha256: Sha256Digest
    policy_sha256: Sha256Digest
    trust_root_sha256: Sha256Digest
    semantic_validator: Literal["ruisheng.device-point-profile-validator/v5"]
    validator_source_sha256: Sha256Digest
    valid_from: AwareTimestamp
    expires_at: AwareTimestamp
    nonce: str = Field(min_length=16, max_length=256)
    approvals: list[ApprovalEntry] = Field(min_length=4)

    _subject_hash = field_validator("subject_gate_sha256")(_validate_sha256)
    _schema_hash = field_validator("schema_sha256")(_validate_sha256)
    _policy_hash = field_validator("policy_sha256")(_validate_sha256)
    _root_hash = field_validator("trust_root_sha256")(_validate_sha256)
    _validator_source_hash = field_validator("validator_source_sha256")(_validate_sha256)
    _valid_from = field_validator("valid_from")(_validate_timestamp)
    _expires_at = field_validator("expires_at")(_validate_timestamp)

    @model_validator(mode="after")
    def approval_contract(self) -> EligibilityApprovalArtifact:
        roles = [approval.role for approval in self.approvals]
        if len(roles) != len(set(roles)):
            raise ValueError("approval roles must be unique")
        if set(roles) != REQUIRED_APPROVAL_ROLES:
            raise ValueError("all required approval roles must be present")
        if datetime.fromisoformat(self.valid_from) >= datetime.fromisoformat(self.expires_at):
            raise ValueError("approval validity window is empty")
        return self


class RuntimeAssertion(StrictModel):
    assertion_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    outcome: Literal["PASS", "FAIL"]
    detail: str = Field(min_length=1, max_length=2000)
    expected: str = Field(min_length=1, max_length=1000)
    observed: str = Field(min_length=1, max_length=1000)
    observation_sha256: Sha256Digest

    _detail = field_validator("detail")(_validate_nonblank)
    _expected = field_validator("expected")(_validate_nonblank)
    _observed = field_validator("observed")(_validate_nonblank)
    _observation_hash = field_validator("observation_sha256")(_validate_sha256)


class RuntimeRawReport(StrictModel):
    schema_version: Literal[1]
    artifact_type: Literal["ruisheng.device-point-profile-runtime-raw-report"]
    check_id: RuntimeCheckId
    result: Literal["PASS", "FAIL"]
    started_at: AwareTimestamp
    completed_at: AwareTimestamp
    exit_code: int = Field(ge=0, le=255)
    assertions: list[RuntimeAssertion] = Field(min_length=1)

    _started = field_validator("started_at")(_validate_timestamp)
    _completed = field_validator("completed_at")(_validate_timestamp)

    @model_validator(mode="after")
    def coherent_result(self) -> RuntimeRawReport:
        assertion_ids = [assertion.assertion_id for assertion in self.assertions]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise ValueError("runtime raw assertion IDs must be unique")
        if datetime.fromisoformat(self.started_at) > datetime.fromisoformat(self.completed_at):
            raise ValueError("runtime raw report ends before it starts")
        all_pass = all(assertion.outcome == "PASS" for assertion in self.assertions)
        if (self.result == "PASS") != all_pass:
            raise ValueError("runtime raw result must equal its assertion outcomes")
        if (self.result == "PASS") != (self.exit_code == 0):
            raise ValueError("runtime raw result must agree with its exit code")
        required = REQUIRED_RUNTIME_ASSERTIONS[self.check_id]
        if set(assertion_ids) != required:
            raise ValueError("runtime raw report does not contain the closed assertion set")
        return self


class RuntimeEvidenceArtifact(StrictModel):
    schema_version: Literal[1]
    artifact_type: Literal["ruisheng.device-point-profile-runtime-evidence"]
    profile_id: str = Field(min_length=1, max_length=128)
    profile_payload_sha256: Sha256Digest
    calibration_run_approval_sha256: Sha256Digest
    release_verification_receipt_sha256: Sha256Digest
    check_id: RuntimeCheckId
    result: Literal["PASS", "FAIL"]
    observed_at: AwareTimestamp
    runner_id: str = Field(min_length=1, max_length=128)
    tool_id: str = Field(min_length=1, max_length=256)
    tool_sha256: Sha256Digest
    raw_report: ArtifactBinding
    runtime_target: RuntimeTarget
    signature: DetachedSignature

    _observed = field_validator("observed_at")(_validate_timestamp)
    _profile = field_validator("profile_id")(_validate_nonblank)
    _payload_hash = field_validator("profile_payload_sha256")(_validate_sha256)
    _run_approval_hash = field_validator("calibration_run_approval_sha256")(_validate_sha256)
    _release_receipt_hash = field_validator("release_verification_receipt_sha256")(_validate_sha256)
    _runner = field_validator("runner_id")(_validate_nonblank)
    _tool = field_validator("tool_id")(_validate_nonblank)
    _tool_hash = field_validator("tool_sha256")(_validate_sha256)


class GateReason(StrictModel):
    code: str
    path: str


class EligibilityReport(StrictModel):
    decision: Decision
    profile_id: str | None
    payload_sha256: str | None
    reasons: tuple[GateReason, ...]


def canonical_payload_sha256(payload: Mapping[str, Any] | ProfilePayload) -> str:
    value = payload.model_dump(mode="json") if isinstance(payload, ProfilePayload) else payload
    return _canonical_sha256(value)


def canonical_gate_input(profile: DevicePointProfile) -> dict[str, Any]:
    return {
        "schema_version": profile.schema_version,
        "artifact_type": profile.artifact_type,
        "profile_id": profile.profile_id,
        "created_at": profile.created_at,
        "semantic_validator": profile.semantic_validator,
        "validator_source_sha256": profile.validator_source_sha256,
        "policy_sha256": profile.policy_sha256,
        "trust_root_sha256": profile.trust_root_sha256,
        "schema_sha256": profile.schema_sha256,
        "profile_payload": profile.profile_payload.model_dump(mode="json"),
        "payload_sha256": profile.payload_sha256,
        "evidence_bindings": [
            binding.model_dump(mode="json")
            for binding in sorted(profile.evidence_bindings, key=lambda item: item.evidence_id)
        ],
        "contradictions": [
            contradiction.model_dump(mode="json")
            for contradiction in sorted(
                profile.contradictions, key=lambda item: item.contradiction_id
            )
        ],
        "calibration_run_approval_binding": (
            None
            if profile.calibration_run_approval_binding is None
            else profile.calibration_run_approval_binding.model_dump(mode="json")
        ),
        "runtime_target": (
            None
            if profile.runtime_target is None
            else profile.runtime_target.model_dump(mode="json")
        ),
        "runtime_evidence": [
            binding.model_dump(mode="json")
            for binding in sorted(profile.runtime_evidence, key=lambda item: item.check_id)
        ],
    }


def canonical_calibration_profile_input(
    profile: DevicePointProfile | Mapping[str, Any],
) -> dict[str, Any]:
    """Return immutable pre-run facts without result/status/evidence fields."""
    document = (
        profile.model_dump(mode="json")
        if isinstance(profile, DevicePointProfile)
        else dict(profile)
    )
    payload = document["profile_payload"]
    identity = dict(payload["device_identity"])
    identity.pop("status", None)
    identity.pop("evidence_refs", None)
    line = dict(payload["line_protocol"])
    line.pop("status", None)
    line.pop("evidence_refs", None)
    points: list[dict[str, Any]] = []
    for point in payload["points"]:
        points.append(
            {
                field: point[field]
                for field in (
                    "point_id",
                    "point_name",
                    "function_code",
                    "start_address",
                    "register_width",
                    "bit",
                    "encoding",
                    "unit",
                )
            }
        )
    return {
        "profile_id": document["profile_id"],
        "schema_sha256": document["schema_sha256"],
        "policy_sha256": document["policy_sha256"],
        "trust_root_sha256": document["trust_root_sha256"],
        "semantic_validator": document["semantic_validator"],
        "validator_source_sha256": document["validator_source_sha256"],
        "device_identity": identity,
        "line_protocol": line,
        "points": points,
    }


def canonical_calibration_profile_input_sha256(
    profile: DevicePointProfile | Mapping[str, Any],
) -> str:
    return _canonical_sha256(canonical_calibration_profile_input(profile))


def canonical_gate_sha256(profile: DevicePointProfile) -> str:
    return _canonical_sha256(canonical_gate_input(profile))


def _signed_artifact_message(domain: str, value: StrictModel | Mapping[str, Any]) -> bytes:
    document = value.model_dump(mode="json") if isinstance(value, StrictModel) else dict(value)
    document.pop("signature", None)
    return domain.encode("ascii") + b"\0" + canonical_json_bytes(document)


def evidence_signature_message(value: EvidenceArtifact | Mapping[str, Any]) -> bytes:
    return _signed_artifact_message("ruisheng.device-point-profile.evidence/v1", value)


def runtime_signature_message(value: RuntimeEvidenceArtifact | Mapping[str, Any]) -> bytes:
    return _signed_artifact_message("ruisheng.device-point-profile.runtime-evidence/v1", value)


def release_receipt_signature_message(
    value: ReleaseVerificationReceipt | Mapping[str, Any],
) -> bytes:
    return _signed_artifact_message("ruisheng.release-verification-receipt/v1", value)


def _release_receipt_document(
    value: ReleaseVerificationReceipt | Mapping[str, Any],
) -> dict[str, Any]:
    return (
        value.model_dump(mode="json")
        if isinstance(value, ReleaseVerificationReceipt)
        else dict(value)
    )


def _release_receipt_image_facts(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    images = document["images"]
    if not isinstance(images, list):
        raise ValueError("release receipt images must be a list")
    facts: list[dict[str, Any]] = []
    for image in images:
        if not isinstance(image, Mapping):
            raise ValueError("release receipt image must be an object")
        facts.append(
            {
                "component": image["component"],
                "image_id": image["image_id"],
                "archive_sha256": image["archive_sha256"],
                "os": image["os"],
                "architecture": image["architecture"],
            }
        )
    return facts


def release_receipt_check_digests(
    value: ReleaseVerificationReceipt | Mapping[str, Any],
) -> dict[str, str]:
    document = _release_receipt_document(value)
    image_identity_sha256 = _canonical_sha256(_release_receipt_image_facts(document))
    values = {
        "OPENSSH_SSHSIG_VERIFIED": _canonical_sha256(
            {
                "publisher_principal": document["publisher_principal"],
                "signature_namespace": document["signature_namespace"],
                "signed_object": document["signed_object"],
                "signature_file": document["signature_file"],
                "release_key_fingerprint": document["release_key_fingerprint"],
                "sha256sums_sha256": document["sha256sums_sha256"],
                "signature_file_sha256": document["signature_file_sha256"],
            }
        ),
        "SHA256SUMS_ALLOWLIST_VERIFIED": document["package_file_set_sha256"],
        "PACKAGE_HASHES_VERIFIED": _canonical_sha256(
            {
                "sha256sums_sha256": document["sha256sums_sha256"],
                "package_file_set_sha256": document["package_file_set_sha256"],
            }
        ),
        "MANIFEST_VERIFIED": _canonical_sha256(
            {
                "manifest_sha256": document["manifest_sha256"],
                "candidate_id": document["candidate_id"],
                "source_commit": document["source_commit"],
                "logical_identity": document["logical_identity"],
            }
        ),
        "ARCHIVE_IDENTITIES_VERIFIED": image_identity_sha256,
        "MIGRATION_HEAD_VERIFIED": _canonical_sha256(
            {
                "alembic_head": document["alembic_head"],
                "observed_alembic_head": document["observed_alembic_head"],
            }
        ),
    }
    return {check_id: values[check_id] for check_id in RELEASE_RECEIPT_CHECK_ORDER}


def release_receipt_protected_snapshot_id(
    value: ReleaseVerificationReceipt | Mapping[str, Any],
) -> str:
    document = _release_receipt_document(value)
    return _canonical_sha256(
        {
            "verifier_id": document["verifier_id"],
            "verifier_tool_id": document["verifier_tool_id"],
            "verifier_tool_sha256": document["verifier_tool_sha256"],
            "verification_method": document["verification_method"],
            "publisher_principal": document["publisher_principal"],
            "signature_namespace": document["signature_namespace"],
            "signed_object": document["signed_object"],
            "signature_file": document["signature_file"],
            "release_key_fingerprint": document["release_key_fingerprint"],
            "sha256sums_sha256": document["sha256sums_sha256"],
            "signature_file_sha256": document["signature_file_sha256"],
            "manifest_sha256": document["manifest_sha256"],
            "package_file_set_sha256": document["package_file_set_sha256"],
            "candidate_id": document["candidate_id"],
            "source_commit": document["source_commit"],
            "logical_identity": document["logical_identity"],
            "alembic_head": document["alembic_head"],
            "observed_alembic_head": document["observed_alembic_head"],
            "images": _release_receipt_image_facts(document),
        }
    )


def approval_signature_message(
    approval: EligibilityApprovalArtifact | Mapping[str, Any],
    entry: ApprovalEntry | Mapping[str, Any],
) -> bytes:
    document = (
        approval.model_dump(mode="json")
        if isinstance(approval, EligibilityApprovalArtifact)
        else dict(approval)
    )
    document.pop("approvals", None)
    entry_document = (
        entry.model_dump(mode="json") if isinstance(entry, ApprovalEntry) else dict(entry)
    )
    entry_document.pop("signature", None)
    document["approval"] = entry_document
    return b"ruisheng.device-point-profile.eligibility-approval/v1\0" + canonical_json_bytes(
        document
    )


def calibration_plan_input(
    approval: CalibrationRunApprovalArtifact | Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(approval, CalibrationRunApprovalArtifact):
        document = approval.model_dump(mode="json")
    else:
        document = dict(approval)
        plans = document.get("plans")
        if isinstance(plans, list):
            document["plans"] = [
                CalibrationPointPlan.model_validate(plan).model_dump(mode="json") for plan in plans
            ]
    document.pop("approvals", None)
    document.pop("subject_plan_sha256", None)
    return document


def canonical_calibration_plan_sha256(
    approval: CalibrationRunApprovalArtifact | Mapping[str, Any],
) -> str:
    return _canonical_sha256(calibration_plan_input(approval))


def calibration_run_approval_signature_message(
    approval: CalibrationRunApprovalArtifact | Mapping[str, Any],
    entry: ApprovalEntry | Mapping[str, Any],
) -> bytes:
    document = calibration_plan_input(approval)
    document["subject_plan_sha256"] = (
        approval.subject_plan_sha256
        if isinstance(approval, CalibrationRunApprovalArtifact)
        else approval["subject_plan_sha256"]
    )
    entry_document = (
        entry.model_dump(mode="json") if isinstance(entry, ApprovalEntry) else dict(entry)
    )
    entry_document.pop("signature", None)
    document["approval"] = entry_document
    return b"ruisheng.device-point-profile.calibration-run-approval/v1\0" + canonical_json_bytes(
        document
    )


def _verify_ed25519(public_key: str, signature: DetachedSignature, message: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True)).verify(
            base64.b64decode(signature.value, validate=True), message
        )
    except (InvalidSignature, ValueError, binascii.Error):
        return False
    return True


def _trust_key_valid_at(
    key: TimeBoundTrustKey,
    timestamp: str | datetime,
    policy: TrustPolicy,
) -> bool:
    observed = datetime.fromisoformat(timestamp) if isinstance(timestamp, str) else timestamp
    return (
        policy.status == "active"
        and key.status == "active"
        and key.revocation_sequence == policy.revocation_sequence
        and datetime.fromisoformat(policy.valid_from)
        <= observed
        < datetime.fromisoformat(policy.expires_at)
        and datetime.fromisoformat(key.valid_from)
        <= observed
        < datetime.fromisoformat(key.expires_at)
    )


def _verify_canonical_signature(
    public_key: str,
    signature: DetachedSignature,
    message_factory: Callable[[], bytes],
) -> bool:
    try:
        message = message_factory()
    except (
        OverflowError,
        RecursionError,
        TypeError,
        UnicodeEncodeError,
        ValueError,
    ):
        return False
    return _verify_ed25519(public_key, signature, message)


def _decode_ssh_string(value: bytes, offset: int) -> tuple[bytes, int]:
    if len(value) - offset < SSH_STRING_LENGTH_BYTES:
        raise ValueError("OpenSSH SSHSIG string length is truncated")
    length = int.from_bytes(
        value[offset : offset + SSH_STRING_LENGTH_BYTES],
        "big",
    )
    start = offset + SSH_STRING_LENGTH_BYTES
    end = start + length
    if length > len(value) - start:
        raise ValueError("OpenSSH SSHSIG string is truncated")
    return value[start:end], end


def _verify_openssh_sshsig(
    public_key: str,
    signature: OpenSshDetachedSignature,
    message_factory: Callable[[], bytes],
) -> bool:
    try:
        binary = base64.b64decode(signature.value, validate=True)
        if binary[: len(SSHSIG_MAGIC)] != SSHSIG_MAGIC:
            return False
        offset = len(SSHSIG_MAGIC)
        if len(binary) - offset < SSH_STRING_LENGTH_BYTES:
            return False
        version = int.from_bytes(
            binary[offset : offset + SSH_STRING_LENGTH_BYTES],
            "big",
        )
        offset += SSH_STRING_LENGTH_BYTES
        public_key_blob, offset = _decode_ssh_string(binary, offset)
        namespace, offset = _decode_ssh_string(binary, offset)
        reserved, offset = _decode_ssh_string(binary, offset)
        hash_algorithm, offset = _decode_ssh_string(binary, offset)
        signature_blob, offset = _decode_ssh_string(binary, offset)
        if offset != len(binary) or version != SSHSIG_VERSION:
            return False

        key_type, key_offset = _decode_ssh_string(public_key_blob, 0)
        embedded_public_key, key_offset = _decode_ssh_string(public_key_blob, key_offset)
        signature_type, signature_offset = _decode_ssh_string(signature_blob, 0)
        raw_signature, signature_offset = _decode_ssh_string(signature_blob, signature_offset)
        trusted_public_key = base64.b64decode(public_key, validate=True)
        expected_namespace = signature.namespace.encode("ascii")
        if (
            key_offset != len(public_key_blob)
            or signature_offset != len(signature_blob)
            or key_type != SSHSIG_KEY_TYPE
            or signature_type != SSHSIG_KEY_TYPE
            or embedded_public_key != trusted_public_key
            or len(embedded_public_key) != ED25519_PUBLIC_KEY_BYTES
            or len(raw_signature) != ED25519_SIGNATURE_BYTES
            or namespace != expected_namespace
            or expected_namespace != RELEASE_RECEIPT_SIGNATURE_NAMESPACE.encode("ascii")
            or reserved
            or hash_algorithm != SSHSIG_HASH_ALGORITHM
        ):
            return False
        message = message_factory()
        signed_payload = (
            SSHSIG_MAGIC
            + len(namespace).to_bytes(4, "big")
            + namespace
            + len(reserved).to_bytes(4, "big")
            + reserved
            + len(hash_algorithm).to_bytes(4, "big")
            + hash_algorithm
            + len(hashlib.sha512(message).digest()).to_bytes(4, "big")
            + hashlib.sha512(message).digest()
        )
        Ed25519PublicKey.from_public_bytes(embedded_public_key).verify(
            raw_signature,
            signed_payload,
        )
    except (
        InvalidSignature,
        OverflowError,
        RecursionError,
        TypeError,
        UnicodeEncodeError,
        ValueError,
        binascii.Error,
    ):
        return False
    return True


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _load_json_bytes(contents: bytes) -> Any:
    value = json.loads(
        contents.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_json_constant,
    )
    _reject_unicode_surrogates(value)
    _reject_non_finite_numbers(value)
    return value


def _contains_forbidden_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_DERIVED_KEYS:
                return str(key)
            found = _contains_forbidden_key(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _contains_forbidden_key(child)
            if found is not None:
                return found
    return None


def _reason(code: str, path: str) -> GateReason:
    return GateReason(code=code, path=path)


def _report(
    decision: Decision,
    reasons: Iterable[GateReason],
    *,
    profile_id: str | None = None,
    payload_sha256: str | None = None,
) -> EligibilityReport:
    ordered = tuple(sorted(set(reasons), key=lambda item: (item.code, item.path)))
    return EligibilityReport(
        decision=decision,
        profile_id=profile_id,
        payload_sha256=payload_sha256,
        reasons=ordered,
    )


def _is_reparse(stat_result: os.stat_result) -> bool:
    attributes = getattr(stat_result, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(stat_result.st_mode) or bool(attributes & reparse_flag)


def _read_fd_once(fd: int, *, maximum_bytes: int) -> bytes:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or _is_reparse(before):
        raise ValueError("artifact is not a non-reparse regular file")
    if before.st_size < 0 or before.st_size > maximum_bytes:
        raise ValueError("artifact exceeds the configured size boundary")
    chunks: list[bytes] = []
    remaining = maximum_bytes + 1
    while remaining:
        chunk = os.read(fd, min(1024 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    if remaining == 0 and os.read(fd, 1):
        raise ValueError("artifact exceeds the configured size boundary")
    contents = b"".join(chunks)
    after = os.fstat(fd)
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or len(contents) != after.st_size:
        raise ValueError("artifact changed while it was being read")
    return contents


def _open_posix_relative(root: Path, parts: tuple[str, ...]) -> int:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(root, directory_flags)
    try:
        for part in parts[:-1]:
            child_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd
        return os.open(
            parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
    finally:
        os.close(directory_fd)


def _windows_component_snapshot(
    root: Path, parts: tuple[str, ...]
) -> tuple[tuple[int, int, int], ...]:
    candidate = root
    snapshots: list[tuple[int, int, int]] = []
    for part in parts:
        candidate = candidate / part
        info = os.lstat(candidate)
        if _is_reparse(info):
            raise ValueError("reparse points are forbidden in artifact paths")
        snapshots.append((info.st_dev, info.st_ino, info.st_mode))
    return tuple(snapshots)


def _windows_dll(name: str) -> Any:
    return ctypes.__dict__["WinDLL"](name, use_last_error=True)


def _windows_last_error() -> int:
    return int(ctypes.__dict__["get_last_error"]())


def _windows_error(error_code: int | None = None) -> OSError:
    if error_code is None:
        error_code = _windows_last_error()
    return cast(OSError, ctypes.__dict__["WinError"](error_code))


def _read_relative_file_once(  # noqa: PLR0912, PLR0915 - platform-specific handle binding
    root: Path,
    relative: str,
    *,
    maximum_bytes: int,
) -> bytes:
    _validate_repo_path(relative)
    root_resolved = root.resolve(strict=True)
    if not root_resolved.is_dir():
        raise ValueError("artifact root must be a directory")
    parts = PurePosixPath(relative).parts
    if not parts:
        raise ValueError("artifact path is empty")
    fd: int | None = None
    try:
        if os.name == "nt":
            before = _windows_component_snapshot(root_resolved, parts)
            candidate = root_resolved.joinpath(*parts)
            kernel32 = _windows_dll("kernel32")
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
                os.fspath(candidate),
                0x80000000,
                0x00000001,
                None,
                3,
                0x00200000,
                None,
            )
            invalid_handle = ctypes.c_void_p(-1).value
            if handle in (None, invalid_handle):
                raise _windows_error()
            try:
                final_path = kernel32.GetFinalPathNameByHandleW
                final_path.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_wchar_p,
                    ctypes.c_uint32,
                    ctypes.c_uint32,
                ]
                final_path.restype = ctypes.c_uint32
                buffer = ctypes.create_unicode_buffer(32768)
                length = final_path(handle, buffer, len(buffer), 0)
                if length == 0 or length >= len(buffer):
                    raise _windows_error()
                opened_path = buffer.value
                if opened_path.startswith("\\\\?\\UNC\\"):
                    opened_path = "\\\\" + opened_path[8:]
                elif opened_path.startswith("\\\\?\\"):
                    opened_path = opened_path[4:]
                if ntpath.normcase(ntpath.normpath(opened_path)) != ntpath.normcase(
                    ntpath.normpath(os.fspath(candidate))
                ):
                    raise ValueError("opened artifact final path does not match its requested path")
                msvcrt = importlib.import_module("msvcrt")
                fd = msvcrt.open_osfhandle(
                    handle,
                    os.O_RDONLY | getattr(os, "O_BINARY", 0),
                )
                handle = None
            finally:
                if handle is not None:
                    kernel32.CloseHandle(ctypes.c_void_p(handle))
            after = _windows_component_snapshot(root_resolved, parts)
            if before != after:
                raise ValueError("artifact path changed while it was opened")
            if fd is None:
                raise ValueError("artifact file descriptor is unavailable")
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino, opened.st_mode) != after[-1]:
                raise ValueError("opened artifact identity does not match its path")
        else:
            fd = _open_posix_relative(root_resolved, parts)
        if fd is None:
            raise ValueError("artifact file descriptor is unavailable")
        return _read_fd_once(fd, maximum_bytes=maximum_bytes)
    finally:
        if fd is not None:
            os.close(fd)


def _read_explicit_file_once(path: Path, *, maximum_bytes: int) -> bytes:
    parent = path.parent.resolve(strict=True)
    return _read_relative_file_once(parent, path.name, maximum_bytes=maximum_bytes)


_EXECUTING_VALIDATOR_SOURCE_SHA256 = (
    "sha256:"
    + hashlib.sha256(
        _read_explicit_file_once(
            Path(__file__).resolve(strict=True), maximum_bytes=MAX_PROFILE_BYTES
        )
    ).hexdigest()
)


def _validate_posix_fixed_trust_root_permissions(path: Path) -> None:
    if os.name == "nt":
        return
    current = path.absolute()
    components = [current, *current.parents]
    for component in components:
        info = os.lstat(component)
        if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError("fixed trust-root file or ancestor is not root-owned and protected")
    leaf = os.lstat(current)
    if not stat.S_ISREG(leaf.st_mode) or _is_reparse(leaf):
        raise ValueError("fixed trust root must be a non-reparse regular file")


@dataclass(frozen=True)
class _WindowsAce:
    ace_type: int
    flags: int
    mask: int
    trustee_sid: str | None
    raw: bytes = b""


@dataclass(frozen=True)
class _WindowsAclSnapshot:
    owner_sid: str | None
    dacl_present: bool
    dacl_protected: bool
    aces: tuple[_WindowsAce, ...]


@dataclass(frozen=True)
class _WindowsHandleSnapshot:
    final_path: str
    identity: tuple[int, int, int, int]
    attributes: int
    acl: _WindowsAclSnapshot


@dataclass(frozen=True)
class _WindowsTrustRootOperations:
    open_component: Callable[[str, bool], int]
    snapshot_component: Callable[[int, str, bool], _WindowsHandleSnapshot]
    duplicate_to_fd: Callable[[int], int]
    close_handle: Callable[[int], None]


class _WindowsFileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _WindowsByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("attributes", ctypes.c_uint32),
        ("creation_time", _WindowsFileTime),
        ("last_access_time", _WindowsFileTime),
        ("last_write_time", _WindowsFileTime),
        ("volume_serial_number", ctypes.c_uint32),
        ("file_size_high", ctypes.c_uint32),
        ("file_size_low", ctypes.c_uint32),
        ("number_of_links", ctypes.c_uint32),
        ("file_index_high", ctypes.c_uint32),
        ("file_index_low", ctypes.c_uint32),
    ]


class _WindowsFileAttributeTagInformation(ctypes.Structure):
    _fields_ = [("attributes", ctypes.c_uint32), ("reparse_tag", ctypes.c_uint32)]


class _WindowsAclSizeInformation(ctypes.Structure):
    _fields_ = [
        ("ace_count", ctypes.c_uint32),
        ("acl_bytes_in_use", ctypes.c_uint32),
        ("acl_bytes_free", ctypes.c_uint32),
    ]


_WINDOWS_AUTHORITY_SIDS = frozenset(
    {
        "S-1-5-18",  # LocalSystem
        "S-1-5-32-544",  # Builtin Administrators
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",  # TrustedInstaller
    }
)
_WINDOWS_ACCESS_ALLOWED_ACE_TYPE = 0
_WINDOWS_INHERIT_ONLY_ACE = 0x08
_WINDOWS_MINIMUM_TRUST_CHAIN_COMPONENTS = 2
_WINDOWS_DOS_DRIVE_LENGTH = 2
_WINDOWS_ACE_HEADER_BYTES = 4
_WINDOWS_ACE_MASK_END = 8
_WINDOWS_SID_HEADER_BYTES = 8
_WINDOWS_SID_SUBAUTHORITY_BYTES = 4
_WINDOWS_NON_GRANT_ACE_TYPES = frozenset(
    {
        1,  # ACCESS_DENIED_ACE_TYPE
        2,  # SYSTEM_AUDIT_ACE_TYPE
        3,  # SYSTEM_ALARM_ACE_TYPE
        6,  # ACCESS_DENIED_OBJECT_ACE_TYPE
        7,  # SYSTEM_AUDIT_OBJECT_ACE_TYPE
        8,  # SYSTEM_ALARM_OBJECT_ACE_TYPE
        10,  # ACCESS_DENIED_CALLBACK_ACE_TYPE
        12,  # ACCESS_DENIED_CALLBACK_OBJECT_ACE_TYPE
        13,  # SYSTEM_AUDIT_CALLBACK_ACE_TYPE
        14,  # SYSTEM_ALARM_CALLBACK_ACE_TYPE
        15,  # SYSTEM_AUDIT_CALLBACK_OBJECT_ACE_TYPE
        16,  # SYSTEM_ALARM_CALLBACK_OBJECT_ACE_TYPE
        17,  # SYSTEM_MANDATORY_LABEL_ACE_TYPE
        18,  # SYSTEM_RESOURCE_ATTRIBUTE_ACE_TYPE
        19,  # SYSTEM_SCOPED_POLICY_ID_ACE_TYPE
        20,  # SYSTEM_PROCESS_TRUST_LABEL_ACE_TYPE
        21,  # SYSTEM_ACCESS_FILTER_ACE_TYPE
    }
)
_WINDOWS_DELETE = 0x00010000
_WINDOWS_WRITE_DAC = 0x00040000
_WINDOWS_WRITE_OWNER = 0x00080000
_WINDOWS_GENERIC_WRITE = 0x40000000
_WINDOWS_GENERIC_ALL = 0x10000000
_WINDOWS_FILE_WRITE_DATA = 0x0002
_WINDOWS_FILE_APPEND_DATA = 0x0004
_WINDOWS_FILE_WRITE_EA = 0x0010
_WINDOWS_FILE_DELETE_CHILD = 0x0040
_WINDOWS_FILE_WRITE_ATTRIBUTES = 0x0100
_WINDOWS_LEAF_MUTATION_MASK = (
    _WINDOWS_DELETE
    | _WINDOWS_WRITE_DAC
    | _WINDOWS_WRITE_OWNER
    | _WINDOWS_GENERIC_WRITE
    | _WINDOWS_GENERIC_ALL
    | _WINDOWS_FILE_WRITE_DATA
    | _WINDOWS_FILE_APPEND_DATA
    | _WINDOWS_FILE_WRITE_EA
    | _WINDOWS_FILE_WRITE_ATTRIBUTES
)
_WINDOWS_DIRECT_PARENT_MUTATION_MASK = (
    _WINDOWS_DELETE
    | _WINDOWS_WRITE_DAC
    | _WINDOWS_WRITE_OWNER
    | _WINDOWS_GENERIC_WRITE
    | _WINDOWS_GENERIC_ALL
    | _WINDOWS_FILE_WRITE_DATA  # FILE_ADD_FILE for a directory
    | _WINDOWS_FILE_APPEND_DATA  # FILE_ADD_SUBDIRECTORY for a directory
    | _WINDOWS_FILE_WRITE_EA
    | _WINDOWS_FILE_DELETE_CHILD
    | _WINDOWS_FILE_WRITE_ATTRIBUTES
)
_WINDOWS_ANCESTOR_REPLACEMENT_MASK = (
    _WINDOWS_DELETE
    | _WINDOWS_WRITE_DAC
    | _WINDOWS_WRITE_OWNER
    | _WINDOWS_GENERIC_WRITE
    | _WINDOWS_GENERIC_ALL
    | _WINDOWS_FILE_DELETE_CHILD
)


def _windows_fixed_trust_root_acl_is_protected(  # noqa: PLR0911, PLR0912
    snapshots: tuple[_WindowsAclSnapshot, ...],
) -> bool:
    """Accept only an authority-owned chain with no untrusted replacement capability."""
    if len(snapshots) < _WINDOWS_MINIMUM_TRUST_CHAIN_COMPONENTS:
        return False
    for index, snapshot in enumerate(snapshots):
        if snapshot.owner_sid not in _WINDOWS_AUTHORITY_SIDS or not snapshot.dacl_present:
            return False
        if index >= len(snapshots) - 2 and not snapshot.dacl_protected:
            return False
        if index == len(snapshots) - 1:
            forbidden_mask = _WINDOWS_LEAF_MUTATION_MASK
        elif index == len(snapshots) - 2:
            forbidden_mask = _WINDOWS_DIRECT_PARENT_MUTATION_MASK
        else:
            forbidden_mask = _WINDOWS_ANCESTOR_REPLACEMENT_MASK
        for ace in snapshot.aces:
            if ace.flags & _WINDOWS_INHERIT_ONLY_ACE:
                continue
            if ace.ace_type == _WINDOWS_ACCESS_ALLOWED_ACE_TYPE:
                if ace.trustee_sid is None:
                    return False
                if ace.trustee_sid not in _WINDOWS_AUTHORITY_SIDS and ace.mask & forbidden_mask:
                    return False
            elif ace.ace_type not in _WINDOWS_NON_GRANT_ACE_TYPES:
                # Object/callback/compound or future allow ACEs are unsafe unless fully parsed.
                return False
    return True


def _windows_fixed_path_chain(path: Path) -> tuple[str, ...]:
    raw_path = os.fspath(path).replace("/", "\\")
    drive, tail = ntpath.splitdrive(raw_path)
    if (
        "\x00" in raw_path
        or len(drive) != _WINDOWS_DOS_DRIVE_LENGTH
        or drive[1] != ":"
        or not tail.startswith("\\")
        or raw_path.startswith("\\\\?\\")
    ):
        raise ValueError("fixed Windows trust root must use an absolute local DOS path")
    parts = tail.split("\\")[1:]
    if not parts or any(
        not part or part in {".", ".."} or ":" in part or part.rstrip(" .") != part
        for part in parts
    ):
        raise ValueError("fixed Windows trust-root path is not canonical")
    current = drive.upper() + "\\"
    chain = [current]
    for part in parts:
        current = ntpath.join(current, part)
        chain.append(current)
    return tuple(chain)


def _windows_normalized_handle_path(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        path = "\\\\" + path[8:]
    elif path.startswith("\\\\?\\"):
        path = path[4:]
    return ntpath.normcase(ntpath.normpath(path))


def _windows_sid_to_string(advapi32: Any, kernel32: Any, sid_address: int) -> str:
    convert_sid = advapi32.ConvertSidToStringSidW
    convert_sid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    convert_sid.restype = ctypes.c_int
    string_pointer = ctypes.c_void_p()
    if not convert_sid(ctypes.c_void_p(sid_address), ctypes.byref(string_pointer)):
        raise _windows_error()
    try:
        if string_pointer.value is None:
            raise ValueError("Windows returned an empty SID string")
        return ctypes.wstring_at(string_pointer.value)
    finally:
        local_free = kernel32.LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p
        local_free(string_pointer)


def _windows_acl_from_handle(  # noqa: PLR0912, PLR0915
    advapi32: Any,
    kernel32: Any,
    handle: int,
) -> _WindowsAclSnapshot:
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    security_descriptor = ctypes.c_void_p()
    get_security_info = advapi32.GetSecurityInfo
    get_security_info.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_security_info.restype = ctypes.c_uint32
    error = get_security_info(
        ctypes.c_void_p(handle),
        1,  # SE_FILE_OBJECT
        0x00000001 | 0x00000004,  # OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(security_descriptor),
    )
    if error:
        raise OSError(error, "GetSecurityInfo failed")
    try:
        if security_descriptor.value is None:
            raise ValueError("Windows returned an empty security descriptor")
        get_control = advapi32.GetSecurityDescriptorControl
        get_control.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint16),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        get_control.restype = ctypes.c_int
        control = ctypes.c_uint16()
        revision = ctypes.c_uint32()
        if not get_control(
            security_descriptor,
            ctypes.byref(control),
            ctypes.byref(revision),
        ):
            raise _windows_error()
        owner_sid = (
            _windows_sid_to_string(advapi32, kernel32, owner.value)
            if owner.value is not None
            else None
        )
        dacl_present = bool(control.value & 0x0004) and dacl.value is not None
        aces: list[_WindowsAce] = []
        if dacl_present:
            get_acl_information = advapi32.GetAclInformation
            get_acl_information.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
            ]
            get_acl_information.restype = ctypes.c_int
            acl_info = _WindowsAclSizeInformation()
            if not get_acl_information(
                dacl,
                ctypes.byref(acl_info),
                ctypes.sizeof(acl_info),
                2,  # AclSizeInformation
            ):
                raise _windows_error()
            get_ace = advapi32.GetAce
            get_ace.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_void_p),
            ]
            get_ace.restype = ctypes.c_int
            for index in range(acl_info.ace_count):
                ace_pointer = ctypes.c_void_p()
                if not get_ace(dacl, index, ctypes.byref(ace_pointer)):
                    raise _windows_error()
                if ace_pointer.value is None:
                    raise ValueError("Windows returned an empty ACE")
                header = ctypes.string_at(ace_pointer.value, _WINDOWS_ACE_HEADER_BYTES)
                ace_size = int.from_bytes(header[2:_WINDOWS_ACE_HEADER_BYTES], "little")
                if ace_size < _WINDOWS_ACE_HEADER_BYTES or ace_size > acl_info.acl_bytes_in_use:
                    raise ValueError("Windows returned a malformed ACE")
                raw_ace = ctypes.string_at(ace_pointer.value, ace_size)
                ace_type = raw_ace[0]
                ace_flags = raw_ace[1]
                mask = (
                    int.from_bytes(raw_ace[4:_WINDOWS_ACE_MASK_END], "little")
                    if ace_size >= _WINDOWS_ACE_MASK_END
                    else 0
                )
                trustee_sid: str | None = None
                if ace_type == _WINDOWS_ACCESS_ALLOWED_ACE_TYPE:
                    sid_bytes = raw_ace[_WINDOWS_ACE_MASK_END:]
                    if len(sid_bytes) < _WINDOWS_SID_HEADER_BYTES:
                        raise ValueError("Windows returned a malformed allow ACE")
                    sid_length = (
                        _WINDOWS_SID_HEADER_BYTES + sid_bytes[1] * _WINDOWS_SID_SUBAUTHORITY_BYTES
                    )
                    if sid_length > len(sid_bytes):
                        raise ValueError("Windows returned a truncated allow ACE SID")
                    trustee_sid = _windows_sid_to_string(
                        advapi32,
                        kernel32,
                        ace_pointer.value + _WINDOWS_ACE_MASK_END,
                    )
                aces.append(_WindowsAce(ace_type, ace_flags, mask, trustee_sid, raw_ace))
        return _WindowsAclSnapshot(
            owner_sid=owner_sid,
            dacl_present=dacl_present,
            dacl_protected=bool(control.value & 0x1000),  # SE_DACL_PROTECTED
            aces=tuple(aces),
        )
    finally:
        if security_descriptor.value is not None:
            local_free = kernel32.LocalFree
            local_free.argtypes = [ctypes.c_void_p]
            local_free.restype = ctypes.c_void_p
            local_free(security_descriptor)


def _windows_handle_snapshot(
    kernel32: Any,
    advapi32: Any,
    handle: int,
    expected_path: str,
    expected_directory: bool,
) -> _WindowsHandleSnapshot:
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
    ]
    get_final_path.restype = ctypes.c_uint32
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_final_path(ctypes.c_void_p(handle), buffer, len(buffer), 0)
    if length == 0 or length >= len(buffer):
        raise _windows_error()
    final_path = _windows_normalized_handle_path(buffer.value)
    if final_path != _windows_normalized_handle_path(expected_path):
        raise ValueError("fixed trust-root handle final path does not match its requested path")

    get_attribute_tag = kernel32.GetFileInformationByHandleEx
    get_attribute_tag.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    get_attribute_tag.restype = ctypes.c_int
    attribute_tag = _WindowsFileAttributeTagInformation()
    if not get_attribute_tag(
        ctypes.c_void_p(handle),
        9,  # FileAttributeTagInfo
        ctypes.byref(attribute_tag),
        ctypes.sizeof(attribute_tag),
    ):
        raise _windows_error()
    if attribute_tag.attributes & 0x00000400:  # FILE_ATTRIBUTE_REPARSE_POINT
        raise ValueError("reparse points are forbidden in the fixed trust-root path")
    is_directory = bool(attribute_tag.attributes & 0x00000010)
    if is_directory != expected_directory:
        raise ValueError("fixed trust-root component type does not match its expected type")

    get_file_information = kernel32.GetFileInformationByHandle
    get_file_information.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_WindowsByHandleFileInformation),
    ]
    get_file_information.restype = ctypes.c_int
    file_information = _WindowsByHandleFileInformation()
    if not get_file_information(ctypes.c_void_p(handle), ctypes.byref(file_information)):
        raise _windows_error()
    identity = (
        file_information.volume_serial_number,
        file_information.file_index_high,
        file_information.file_index_low,
        file_information.number_of_links,
    )
    return _WindowsHandleSnapshot(
        final_path=final_path,
        identity=identity,
        attributes=attribute_tag.attributes,
        acl=_windows_acl_from_handle(advapi32, kernel32, handle),
    )


def _windows_native_trust_root_operations() -> _WindowsTrustRootOperations:
    kernel32 = _windows_dll("kernel32")
    advapi32 = _windows_dll("advapi32")
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
    close_native_handle = kernel32.CloseHandle
    close_native_handle.argtypes = [ctypes.c_void_p]
    close_native_handle.restype = ctypes.c_int

    def open_component(path: str, is_directory: bool) -> int:
        desired_access = 0x00020080 if is_directory else 0x80020000
        flags = 0x00200000 | (0x02000000 if is_directory else 0)
        share_mode = 0x00000001 | (0x00000002 if is_directory else 0)
        handle = create_file(
            path,
            desired_access,
            share_mode,  # The leaf must exclude concurrent data writers; never share DELETE.
            None,
            3,  # OPEN_EXISTING
            flags,  # OPEN_REPARSE_POINT, plus BACKUP_SEMANTICS for directories
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle in (None, invalid_handle):
            raise _windows_error()
        return int(handle)

    def snapshot_component(
        handle: int,
        path: str,
        is_directory: bool,
    ) -> _WindowsHandleSnapshot:
        return _windows_handle_snapshot(kernel32, advapi32, handle, path, is_directory)

    def duplicate_to_fd(handle: int) -> int:
        get_current_process = kernel32.GetCurrentProcess
        get_current_process.argtypes = []
        get_current_process.restype = ctypes.c_void_p
        duplicate_handle = kernel32.DuplicateHandle
        duplicate_handle.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint32,
        ]
        duplicate_handle.restype = ctypes.c_int
        process = get_current_process()
        duplicate = ctypes.c_void_p()
        if not duplicate_handle(
            process,
            ctypes.c_void_p(handle),
            process,
            ctypes.byref(duplicate),
            0,
            False,
            0x00000002,  # DUPLICATE_SAME_ACCESS
        ):
            raise _windows_error()
        if duplicate.value is None:
            raise ValueError("Windows returned an empty duplicate handle")
        msvcrt = importlib.import_module("msvcrt")
        try:
            return int(
                msvcrt.open_osfhandle(
                    duplicate.value,
                    os.O_RDONLY | getattr(os, "O_BINARY", 0),
                )
            )
        except (OSError, ValueError):
            close_native_handle(duplicate)
            raise

    def close_handle(handle: int) -> None:
        close_native_handle(ctypes.c_void_p(handle))

    return _WindowsTrustRootOperations(
        open_component=open_component,
        snapshot_component=snapshot_component,
        duplicate_to_fd=duplicate_to_fd,
        close_handle=close_handle,
    )


def _read_windows_fixed_trust_root_once(
    path: Path,
    *,
    maximum_bytes: int,
    operations: _WindowsTrustRootOperations | None = None,
) -> bytes:
    component_paths = _windows_fixed_path_chain(path)
    native = operations or _windows_native_trust_root_operations()
    handles: list[int] = []
    before: list[_WindowsHandleSnapshot] = []
    fd: int | None = None
    try:
        for index, component_path in enumerate(component_paths):
            is_directory = index < len(component_paths) - 1
            handle = native.open_component(component_path, is_directory)
            handles.append(handle)
            before.append(native.snapshot_component(handle, component_path, is_directory))
        before_tuple = tuple(before)
        if not _windows_fixed_trust_root_acl_is_protected(
            tuple(snapshot.acl for snapshot in before_tuple)
        ):
            raise ValueError("fixed trust-root ACL is not protected")
        fd = native.duplicate_to_fd(handles[-1])
        contents = _read_fd_once(fd, maximum_bytes=maximum_bytes)
        after = tuple(
            native.snapshot_component(
                handle,
                component_paths[index],
                index < len(component_paths) - 1,
            )
            for index, handle in enumerate(handles)
        )
        if before_tuple != after:
            raise ValueError("fixed trust-root identity, path, or ACL changed while it was read")
        if not _windows_fixed_trust_root_acl_is_protected(
            tuple(snapshot.acl for snapshot in after)
        ):
            raise ValueError("fixed trust-root ACL is not protected")
        return contents
    finally:
        if fd is not None:
            os.close(fd)
        for handle in reversed(handles):
            native.close_handle(handle)


def _read_fixed_trust_root_once(path: Path, *, maximum_bytes: int) -> bytes:
    if os.name == "nt":
        return _read_windows_fixed_trust_root_once(path, maximum_bytes=maximum_bytes)
    _validate_posix_fixed_trust_root_permissions(path)
    return _read_explicit_file_once(path, maximum_bytes=maximum_bytes)


@dataclass
class _ArtifactReadBudget:
    remaining_bytes: int

    def reserve(self, size_bytes: int) -> bool:
        if size_bytes < 0 or size_bytes > self.remaining_bytes:
            return False
        self.remaining_bytes -= size_bytes
        return True


def _check_binding(
    binding: ArtifactBinding,
    root: Path,
    *,
    additional_budget: _ArtifactReadBudget | None = None,
) -> tuple[bytes | None, GateReason | None]:
    if additional_budget is not None and not additional_budget.reserve(binding.size_bytes):
        return None, _reason("ARTIFACT_BYTES_LIMIT_EXCEEDED", f"/{binding.path}")
    try:
        contents = _read_relative_file_once(
            root,
            binding.path,
            maximum_bytes=min(MAX_ARTIFACT_BYTES, binding.size_bytes),
        )
    except (OSError, ValueError):
        return None, _reason("ARTIFACT_PATH_INVALID", f"/{binding.path}")
    if len(contents) != binding.size_bytes:
        return None, _reason("ARTIFACT_SIZE_MISMATCH", f"/{binding.path}")
    digest = "sha256:" + hashlib.sha256(contents).hexdigest()
    if digest != binding.sha256:
        return None, _reason("ARTIFACT_HASH_MISMATCH", f"/{binding.path}")
    return contents, None


def _invalid_validation_report(error: ValidationError) -> EligibilityReport:
    first = error.errors(include_url=False)[0]
    location = "/" + "/".join(str(part) for part in first["loc"])
    return _report("INVALID", [_reason("SCHEMA_INVALID", location)])


def _coerce_trust_policy(value: TrustPolicy | Mapping[str, Any] | None) -> TrustPolicy | None:
    if value is None or isinstance(value, TrustPolicy):
        return value
    return TrustPolicy.model_validate(value)


def _coerce_trust_root(value: PolicyTrustRoot | None) -> PolicyTrustRoot | None:
    """Accept only a root object supplied by a trusted embedding boundary."""
    if value is None or isinstance(value, PolicyTrustRoot):
        return value
    raise TypeError("trust root injection requires a validated PolicyTrustRoot instance")


def _policy_authority_reasons(
    profile: DevicePointProfile,
    policy: TrustPolicy | None,
    trust_root: PolicyTrustRoot | None,
    *,
    current: datetime,
) -> tuple[list[GateReason], list[GateReason]]:
    invalid: list[GateReason] = []
    blocked: list[GateReason] = []
    if trust_root is None:
        blocked.append(_reason("TRUST_ROOT_MISSING", "/trust_root"))
        return invalid, blocked
    actual_root_hash = trust_root_sha256(trust_root)
    if profile.trust_root_sha256 != actual_root_hash:
        invalid.append(_reason("TRUST_ROOT_HASH_MISMATCH", "/trust_root_sha256"))
    root_valid_from = datetime.fromisoformat(trust_root.valid_from)
    root_expires_at = datetime.fromisoformat(trust_root.expires_at)
    if trust_root.status != "active":
        invalid.append(_reason("TRUST_ROOT_REVOKED", "/trust_root"))
    if not root_valid_from <= current < root_expires_at:
        blocked.append(_reason("TRUST_ROOT_NOT_CURRENT", "/trust_root"))
    if policy is None:
        return invalid, blocked
    policy_hash = trust_policy_sha256(policy)
    authorization = next(
        (
            item
            for item in trust_root.authorized_policies
            if item.policy_id == policy.policy_id
            and item.policy_version == policy.policy_version
            and item.policy_sha256 == policy_hash
        ),
        None,
    )
    if authorization is None:
        invalid.append(_reason("TRUST_POLICY_NOT_AUTHORIZED", "/trust_policy"))
    elif (
        authorization.status != "active"
        or authorization.revocation_sequence != policy.revocation_sequence
    ):
        invalid.append(_reason("TRUST_POLICY_REVOKED", "/trust_policy"))
    if policy.status != "active":
        invalid.append(_reason("TRUST_POLICY_REVOKED", "/trust_policy"))
    policy_valid_from = datetime.fromisoformat(policy.valid_from)
    policy_expires_at = datetime.fromisoformat(policy.expires_at)
    if not policy_valid_from <= current < policy_expires_at:
        blocked.append(_reason("TRUST_POLICY_NOT_CURRENT", "/trust_policy"))
    if (
        policy.authority_id != trust_root.authority_id
        or policy.authority_signature.key_id != trust_root.key_id
        or not _verify_canonical_signature(
            trust_root.public_key,
            policy.authority_signature,
            lambda: trust_policy_signature_message(policy),
        )
    ):
        invalid.append(_reason("TRUST_POLICY_AUTHORITY_INVALID", "/trust_policy"))
    return invalid, blocked


def _load_evidence_artifact(  # noqa: PLR0911 - fail-closed reason classification
    binding: EvidenceBinding,
    contents: bytes,
    policy: TrustPolicy | None,
) -> tuple[EvidenceArtifact | None, GateReason | None]:
    if binding.role == "legacy_source":
        return None, None
    if binding.media_type != "application/json":
        return None, _reason("EVIDENCE_MEDIA_TYPE_INVALID", f"/evidence/{binding.evidence_id}")
    try:
        artifact = EvidenceArtifact.model_validate(_load_json_bytes(contents))
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
        ValidationError,
    ):
        return None, _reason("EVIDENCE_ARTIFACT_INVALID", f"/{binding.path}")
    if (
        artifact.evidence_id != binding.evidence_id
        or artifact.role != binding.role
        or artifact.subject_point_ids != binding.subject_point_ids
    ):
        return None, _reason("EVIDENCE_BINDING_MISMATCH", f"/evidence/{binding.evidence_id}")
    if policy is not None:
        trust_key = next(
            (
                key
                for key in policy.evidence_keys
                if key.attestor_id == artifact.attestor_id
                and key.key_id == artifact.signature.key_id
                and artifact.role in key.roles
            ),
            None,
        )
        if trust_key is None:
            return None, _reason("EVIDENCE_ATTESTOR_UNTRUSTED", f"/evidence/{binding.evidence_id}")
        if not _trust_key_valid_at(trust_key, artifact.observed_at, policy):
            return None, _reason(
                "EVIDENCE_KEY_NOT_VALID_AT_ARTIFACT_TIME",
                f"/evidence/{binding.evidence_id}",
            )
        if not _verify_canonical_signature(
            trust_key.public_key,
            artifact.signature,
            lambda: evidence_signature_message(artifact),
        ):
            return None, _reason("EVIDENCE_SIGNATURE_INVALID", f"/evidence/{binding.evidence_id}")
    return artifact, None


def _verify_release_receipt(  # noqa: PLR0911, PLR0912
    target: RuntimeTarget,
    contents: bytes,
    policy: TrustPolicy | None,
) -> tuple[ReleaseVerificationReceipt | None, GateReason | None]:
    binding = target.release_verification_receipt
    try:
        receipt = ReleaseVerificationReceipt.model_validate(_load_json_bytes(contents))
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
        ValidationError,
    ):
        return None, _reason(
            "RELEASE_VERIFICATION_RECEIPT_INVALID",
            "/runtime_target/release_verification_receipt",
        )
    if receipt.receipt_id != binding.receipt_id:
        return None, _reason(
            "RELEASE_VERIFICATION_RECEIPT_ID_MISMATCH",
            "/runtime_target/release_verification_receipt",
        )
    if policy is None:
        return None, _reason(
            "RELEASE_VERIFIER_POLICY_MISSING",
            "/runtime_target/release_verification_receipt",
        )
    trust_key = next(
        (
            key
            for key in policy.release_verifier_keys
            if key.verifier_id == receipt.verifier_id and key.key_id == receipt.signature.key_id
        ),
        None,
    )
    if trust_key is None:
        return None, _reason(
            "RELEASE_VERIFIER_UNTRUSTED",
            "/runtime_target/release_verification_receipt",
        )
    verified_at = datetime.fromisoformat(receipt.verified_at)
    if not (
        datetime.fromisoformat(policy.valid_from)
        <= verified_at
        < datetime.fromisoformat(policy.expires_at)
    ):
        return None, _reason(
            "RELEASE_RECEIPT_OUTSIDE_POLICY_WINDOW",
            "/runtime_target/release_verification_receipt/verified_at",
        )
    if not _trust_key_valid_at(trust_key, receipt.verified_at, policy):
        return None, _reason(
            "RELEASE_KEY_NOT_VALID_AT_ARTIFACT_TIME",
            "/runtime_target/release_verification_receipt/verified_at",
        )
    if not _verify_openssh_sshsig(
        trust_key.public_key,
        receipt.signature,
        lambda: release_receipt_signature_message(receipt),
    ):
        return None, _reason(
            "RELEASE_VERIFICATION_RECEIPT_SIGNATURE_INVALID",
            "/runtime_target/release_verification_receipt",
        )
    if (
        receipt.verifier_tool_id != trust_key.tool_id
        or receipt.verifier_tool_sha256 != trust_key.tool_sha256
    ):
        return None, _reason(
            "RELEASE_VERIFIER_TOOL_MISMATCH",
            "/runtime_target/release_verification_receipt",
        )
    if receipt.release_key_fingerprint not in trust_key.publisher_key_fingerprints:
        return None, _reason(
            "RELEASE_PUBLISHER_KEY_UNTRUSTED",
            "/runtime_target/release_verification_receipt/release_key_fingerprint",
        )
    try:
        expected_checks = release_receipt_check_digests(receipt)
        expected_snapshot = release_receipt_protected_snapshot_id(receipt)
    except (KeyError, OverflowError, RecursionError, TypeError, ValueError):
        return None, _reason(
            "RELEASE_VERIFICATION_RECEIPT_INVALID",
            "/runtime_target/release_verification_receipt",
        )
    observed_checks = {check.check_id: check.observed_sha256 for check in receipt.checks}
    if observed_checks != expected_checks:
        return None, _reason(
            "RELEASE_VERIFICATION_RECEIPT_CHECK_MISMATCH",
            "/runtime_target/release_verification_receipt/checks",
        )
    if receipt.protected_snapshot_id != expected_snapshot:
        return None, _reason(
            "RELEASE_VERIFICATION_SNAPSHOT_MISMATCH",
            "/runtime_target/release_verification_receipt/protected_snapshot_id",
        )
    images = {image.component: image.image_id for image in receipt.images}
    if (
        receipt.candidate_id != target.candidate_id
        or receipt.source_commit != target.source_commit
        or receipt.alembic_head != target.alembic_head
        or receipt.logical_identity != target.logical_identity
        or images.get("api") != target.api_image_digest
        or images.get("gw") != target.gateway_image_digest
    ):
        return None, _reason("RELEASE_VERIFICATION_TARGET_MISMATCH", "/runtime_target")
    return receipt, None


def _number_close(actual: int | float, expected: int | float, tolerance: int | float) -> bool:
    try:
        difference = abs(actual - expected)
    except (OverflowError, TypeError):
        return False
    if isinstance(difference, float) and not math.isfinite(difference):
        return False
    return difference <= tolerance


def _counter_observation_groups(
    observations: Iterable[CounterObservation],
) -> dict[str, list[CounterObservation]]:
    groups: dict[str, list[CounterObservation]] = {}
    last_state: str | None = None
    for observation in observations:
        if observation.state_id != last_state:
            if observation.state_id in groups:
                return {}
            groups[observation.state_id] = []
            last_state = observation.state_id
        groups[observation.state_id].append(observation)
    return groups


def _counter_sequence_valid(evidence: CounterCalibrationEvidence) -> bool:
    groups = _counter_observation_groups(evidence.observations)
    if tuple(groups) != COUNTER_STATE_SEQUENCE:
        return False
    if any(
        len(observations) < MIN_CALIBRATION_SAMPLES_PER_STATE for observations in groups.values()
    ):
        return False
    for state_id, observations in groups.items():
        expected_reference = (
            0 if state_id in {"BASELINE", "PERSISTENCE"} else evidence.expected_increment
        )
        if any(
            not _number_close(
                observation.reference_increment,
                expected_reference,
                evidence.increment_tolerance,
            )
            for observation in observations
        ):
            return False
    if any(
        observation.raw != observations[0].raw
        for observations in groups.values()
        for observation in observations
    ):
        return False
    baseline = groups["BASELINE"][0]
    increment = groups["INCREMENT"][0]
    rollover = groups["ROLLOVER"][0]
    persistence = groups["PERSISTENCE"][0]
    raw_tolerance = evidence.increment_tolerance * evidence.counts_per_unit
    expected_increment_raw = increment.reference_increment * evidence.counts_per_unit
    if not _number_close(increment.raw - baseline.raw, expected_increment_raw, raw_tolerance):
        return False
    if evidence.rollover_behavior == "wrap":
        terminal_valid = (increment.raw, rollover.raw) == (evidence.modulus - 1, 0)
    elif evidence.rollover_behavior == "saturate":
        terminal_valid = (increment.raw, rollover.raw) == (
            evidence.modulus - 1,
            evidence.modulus - 1,
        )
    else:
        terminal_valid = (increment.raw, rollover.raw) == (evidence.modulus - 1, 0)
    return terminal_valid and rollover.raw == persistence.raw


def _calibration_sample_ids(
    evidence: AnalogCalibrationEvidence | BinaryCalibrationEvidence | CounterCalibrationEvidence,
) -> set[str]:
    if isinstance(evidence, AnalogCalibrationEvidence | BinaryCalibrationEvidence):
        return {sample.sample_id for state in evidence.states for sample in state.samples}
    return {observation.sample_id for observation in evidence.observations}


def _timestamp_distance_ms(left: str, right: str) -> float:
    return abs(
        (datetime.fromisoformat(left) - datetime.fromisoformat(right)).total_seconds() * 1000
    )


def _analog_attempted_samples(
    evidence: AnalogCalibrationEvidence,
) -> list[AnalogSample | AnalogExcludedSample]:
    samples: list[AnalogSample | AnalogExcludedSample] = [
        sample for state in evidence.states for sample in state.samples
    ]
    samples.extend(evidence.exclusion_log)
    return sorted(samples, key=lambda sample: datetime.fromisoformat(sample.observed_at))


def _calibration_sample_facts(
    evidence: AnalogCalibrationEvidence | BinaryCalibrationEvidence | CounterCalibrationEvidence,
) -> dict[str, tuple[str, str, int | float]]:
    if isinstance(evidence, AnalogCalibrationEvidence):
        facts = {
            sample.sample_id: (sample.event_id, sample.observed_at, sample.raw)
            for state in evidence.states
            for sample in state.samples
        }
        facts.update(
            {
                sample.sample_id: (sample.event_id, sample.observed_at, sample.raw)
                for sample in evidence.exclusion_log
            }
        )
        return facts
    if isinstance(evidence, BinaryCalibrationEvidence):
        facts = {
            sample.sample_id: (sample.event_id, sample.observed_at, sample.raw)
            for state in evidence.states
            for sample in state.samples
        }
        facts.update(
            {
                control.sample_id: (
                    control.event_id,
                    control.observed_at,
                    control.observed_raw,
                )
                for control in evidence.unintervened_channel_controls
            }
        )
        facts.update(
            {
                control.sample_id: (
                    control.event_id,
                    control.observed_at,
                    control.observed_raw,
                )
                for control in evidence.competing_candidate_controls
            }
        )
        return facts
    return {
        observation.sample_id: (
            observation.event_id,
            observation.observed_at,
            observation.raw,
        )
        for observation in evidence.observations
    }


def _calibration_sample_scopes(
    evidence: AnalogCalibrationEvidence | BinaryCalibrationEvidence | CounterCalibrationEvidence,
    plan: CalibrationPointPlan,
) -> dict[str, tuple[int, int, int]]:
    primary = (plan.function_code, plan.start_address, plan.register_width)
    if isinstance(evidence, AnalogCalibrationEvidence):
        return dict.fromkeys(_calibration_sample_facts(evidence), primary)
    if isinstance(evidence, CounterCalibrationEvidence):
        return dict.fromkeys(_calibration_sample_facts(evidence), primary)
    scopes: dict[str, tuple[int, int, int]] = {
        sample.sample_id: (
            evidence.address_semantics.function_code,
            evidence.address_semantics.start_address,
            evidence.address_semantics.register_width,
        )
        for state in evidence.states
        for sample in state.samples
    }
    scopes.update(
        {
            control.sample_id: (
                control.address_semantics.function_code,
                control.address_semantics.start_address,
                control.address_semantics.register_width,
            )
            for control in evidence.unintervened_channel_controls
        }
    )
    scopes.update(
        {
            control.sample_id: (
                control.candidate.function_code,
                control.candidate.start_address,
                control.candidate.register_width,
            )
            for control in evidence.competing_candidate_controls
        }
    )
    return scopes


def _raw_modbus_observations(
    content: RawObservationEvidenceContent,
) -> list[RawModbusExchangeRecordBase]:
    return [record for record in content.records if isinstance(record, RawModbusExchangeRecordBase)]


def _decode_raw_modbus_value(  # noqa: PLR0911, PLR0912 - closed Modbus value variants
    observation: RawModbusExchangeRecordBase,
    plan: CalibrationPointPlan,
) -> int | float | None:
    response_data = bytes.fromhex(observation.response_rtu_hex)[3:-2]
    if observation.function_code in {1, 2}:
        if plan.value_type != "bit" or observation.quantity != 1:
            return None
        return response_data[0] & 1
    if plan.value_type == "bit":
        if observation.quantity != 1 or plan.bit is None:
            return None
        return (int.from_bytes(response_data, "big") >> plan.bit) & 1
    words = [response_data[index : index + 2] for index in range(0, len(response_data), 2)]
    if plan.byte_order == "little":
        words = [word[::-1] for word in words]
    elif plan.byte_order != "big":
        return None
    if len(words) > 1:
        if plan.word_order == "low_first":
            words.reverse()
        elif plan.word_order != "high_first":
            return None
    elif plan.word_order != "not_applicable":
        return None
    payload = b"".join(words)
    expected_lengths = {
        "u16": 2,
        "s16": 2,
        "u32": 4,
        "s32": 4,
        "float32": 4,
    }
    if plan.value_type in expected_lengths and len(payload) != expected_lengths[plan.value_type]:
        return None
    if plan.value_type in {"u16", "u32"}:
        return int.from_bytes(payload, "big", signed=False)
    if plan.value_type in {"s16", "s32"}:
        return int.from_bytes(payload, "big", signed=True)
    if plan.value_type == "float32":
        return float(struct.unpack(">f", payload)[0])
    if plan.value_type == "bcd":
        digits = [nibble for byte in payload for nibble in (byte >> 4, byte & 0x0F)]
        if any(digit > MAX_BCD_DIGIT for digit in digits):
            return None
        return int("".join(str(digit) for digit in digits))
    return None


def _decode_raw_modbus_binary_value(
    observation: RawModbusExchangeRecordBase,
    semantics: BinaryAddressSemantics,
) -> int | None:
    response_data = bytes.fromhex(observation.response_rtu_hex)[3:-2]
    if semantics.kind in {"coil", "discrete_input"}:
        return response_data[0] & 1
    register_value = int.from_bytes(response_data, "big")
    if semantics.kind == "register_bit":
        if semantics.bit is None:
            return None
        return (register_value >> semantics.bit) & 1
    return register_value


def _binary_sample_semantics(
    evidence: BinaryCalibrationEvidence,
) -> dict[str, BinaryAddressSemantics]:
    semantics = {
        sample.sample_id: evidence.address_semantics
        for state in evidence.states
        for sample in state.samples
    }
    semantics.update(
        {
            control.sample_id: control.address_semantics
            for control in evidence.unintervened_channel_controls
        }
    )
    semantics.update(
        {control.sample_id: control.candidate for control in evidence.competing_candidate_controls}
    )
    return semantics


def _run_content_timestamps(content: EvidenceContent) -> tuple[datetime, ...]:
    timestamp_values: list[str] = []
    if isinstance(content, AnalogCalibrationEvidence):
        timestamp_values.extend(
            sample.observed_at for state in content.states for sample in state.samples
        )
        timestamp_values.extend(sample.observed_at for sample in content.exclusion_log)
        timestamp_values.extend(control.observed_at for control in content.negative_controls)
    elif isinstance(content, BinaryCalibrationEvidence):
        timestamp_values.extend(
            sample.observed_at for state in content.states for sample in state.samples
        )
        timestamp_values.extend(
            control.observed_at for control in content.unintervened_channel_controls
        )
        timestamp_values.extend(
            control.observed_at for control in content.competing_candidate_controls
        )
        timestamp_values.extend(control.observed_at for control in content.negative_controls)
    elif isinstance(content, CounterCalibrationEvidence):
        timestamp_values.extend(observation.observed_at for observation in content.observations)
        timestamp_values.extend(control.observed_at for control in content.negative_controls)
        timestamp_values.extend(
            (
                content.persistence_event.power_removed_at,
                content.persistence_event.power_restored_at,
                content.persistence_event.post_restore_observed_at,
            )
        )
    elif isinstance(content, AnalogReferenceEvidence | BinaryReferenceEvidence):
        timestamp_values.extend(sample.observed_at for sample in content.samples)
    elif isinstance(content, CounterReferenceEvidence):
        timestamp_values.extend(sample.observed_at for sample in content.samples)
        timestamp_values.extend(
            (
                content.persistence_event.power_removed_at,
                content.persistence_event.power_restored_at,
                content.persistence_event.post_restore_observed_at,
            )
        )
    elif isinstance(content, RawObservationEvidenceContent):
        timestamp_values.extend(record.observed_at for record in content.records)
    return tuple(datetime.fromisoformat(value) for value in timestamp_values)


def _line_protocol_evidence_reasons(  # noqa: PLR0911, PLR0912
    line: LineProtocol,
    identity_digest: str,
    device_serial: str | None,
    artifacts: Mapping[str, EvidenceArtifact],
    pointer: str,
) -> tuple[list[GateReason], list[GateReason]]:
    invalid: list[GateReason] = []
    blocked: list[GateReason] = []
    referenced = [artifacts[ref] for ref in line.evidence_refs if ref in artifacts]
    if any(artifact.role not in {"line_protocol", "raw_observation"} for artifact in referenced):
        invalid.append(_reason("LINE_PROTOCOL_EVIDENCE_ROLE_INVALID", pointer))
    line_artifacts = [artifact for artifact in referenced if artifact.role == "line_protocol"]
    raw_artifacts = [artifact for artifact in referenced if artifact.role == "raw_observation"]
    all_line_artifacts = [
        artifact for artifact in artifacts.values() if artifact.role == "line_protocol"
    ]
    if set(map(id, all_line_artifacts)) != set(map(id, line_artifacts)):
        invalid.append(_reason("LINE_PROTOCOL_EVIDENCE_OWNERSHIP_INVALID", pointer))
    if not line_artifacts:
        blocked.append(_reason("LINE_PROTOCOL_EVIDENCE_MISSING", pointer))
        return invalid, blocked
    if len(line_artifacts) != 1:
        invalid.append(_reason("LINE_PROTOCOL_EVIDENCE_AMBIGUOUS", pointer))
        return invalid, blocked
    line_artifact = line_artifacts[0]
    content = line_artifact.content
    if not isinstance(content, LineProtocolEvidenceContent):
        invalid.append(_reason("LINE_PROTOCOL_EVIDENCE_MISMATCH", pointer))
        return invalid, blocked
    expected_line = (
        line.unit_id,
        line.baud_rate,
        line.data_bits,
        line.parity,
        line.stop_bits,
        line.stable_device_path,
        identity_digest,
        device_serial,
    )
    observed_line = (
        content.unit_id,
        content.baud_rate,
        content.data_bits,
        content.parity,
        content.stop_bits,
        content.stable_device_path,
        content.device_identity_sha256,
        content.device_serial,
    )
    if observed_line != expected_line:
        blocked.append(_reason("LINE_PROTOCOL_EVIDENCE_MISMATCH", pointer))
        return invalid, blocked
    if not raw_artifacts:
        blocked.append(_reason("LINE_PROTOCOL_SOURCE_EVIDENCE_MISSING", pointer))
        return invalid, blocked
    source_sha256 = content.field_claims[0].source_record_sha256
    all_line_probes = [
        record
        for artifact in artifacts.values()
        if artifact.role == "raw_observation"
        and isinstance(artifact.content, RawObservationEvidenceContent)
        for record in artifact.content.records
        if isinstance(record, RawLineProbeObservationRecord)
    ]
    expected_probe_scope = (
        *expected_line[:5],
        expected_line[5],
        expected_line[6],
        expected_line[7],
    )
    if any(
        (
            probe.unit_id,
            probe.baud_rate,
            probe.data_bits,
            probe.parity,
            probe.stop_bits,
            probe.stable_device_path,
            probe.device_identity_sha256,
            probe.device_serial,
        )
        != expected_probe_scope
        for probe in all_line_probes
    ):
        invalid.append(_reason("LINE_PROBE_CONTRADICTION", pointer))
    source_matches: list[tuple[EvidenceArtifact, RawObservationRecord]] = []
    for raw_artifact in raw_artifacts:
        raw_content = raw_artifact.content
        if not isinstance(raw_content, RawObservationEvidenceContent):
            continue
        source_matches.extend(
            (raw_artifact, record)
            for record in raw_content.records
            if record.record_sha256 == source_sha256
        )
    if not source_matches:
        invalid.append(_reason("LINE_FIELD_SOURCE_RECORD_MISSING", pointer))
        return invalid, blocked
    if len(source_matches) != 1:
        invalid.append(_reason("LINE_FIELD_SOURCE_RECORD_AMBIGUOUS", pointer))
        return invalid, blocked
    raw_artifact, source_record = source_matches[0]
    if not isinstance(source_record, RawLineProbeObservationRecord):
        invalid.append(_reason("LINE_FIELD_SOURCE_RECORD_TYPE_MISMATCH", pointer))
        return invalid, blocked
    source_line = (
        source_record.unit_id,
        source_record.baud_rate,
        source_record.data_bits,
        source_record.parity,
        source_record.stop_bits,
        source_record.stable_device_path,
        source_record.device_identity_sha256,
        source_record.device_serial,
    )
    if source_line[:5] != observed_line[:5]:
        invalid.append(_reason("LINE_FIELD_SOURCE_RECORD_VALUE_MISMATCH", pointer))
    if source_line[5:] != observed_line[5:]:
        invalid.append(_reason("LINE_FIELD_SOURCE_RECORD_SCOPE_MISMATCH", pointer))
    if datetime.fromisoformat(source_record.observed_at) > datetime.fromisoformat(
        raw_artifact.observed_at
    ) or datetime.fromisoformat(raw_artifact.observed_at) > datetime.fromisoformat(
        line_artifact.observed_at
    ):
        invalid.append(_reason("LINE_FIELD_SOURCE_RECORD_TIME_INVALID", pointer))
    return invalid, blocked


def _evidence_ownership_reasons(  # noqa: PLR0912 - closed evidence ownership graph
    profile: DevicePointProfile,
    artifacts: Mapping[str, EvidenceArtifact],
) -> list[GateReason]:
    """Enforce the closed ownership graph for every trusted evidence artifact."""
    invalid: list[GateReason] = []
    identity_refs = set(profile.profile_payload.device_identity.evidence_refs)
    line_refs = set(profile.profile_payload.line_protocol.evidence_refs)
    point_refs = {
        point.point_id: set(point.evidence_refs) for point in profile.profile_payload.points
    }
    contradiction_refs = {
        contradiction.contradiction_id: set(contradiction.resolution_evidence_refs)
        for contradiction in profile.contradictions
    }
    point_scoped_roles = {
        "authoritative_map",
        "calibration",
        "reference",
        "raw_observation",
    }

    for evidence_id, artifact in artifacts.items():
        pointer = f"/evidence/{evidence_id}"
        if artifact.role == "identity":
            owned = evidence_id in identity_refs
        elif artifact.role == "line_protocol":
            owned = evidence_id in line_refs
        elif artifact.role == "contradiction_resolution":
            content = artifact.content
            owned = isinstance(content, ContradictionResolutionEvidenceContent) and all(
                evidence_id in contradiction_refs.get(contradiction_id, set())
                for contradiction_id in content.contradiction_ids
            )
        elif artifact.role in point_scoped_roles:
            owned = bool(artifact.subject_point_ids) and all(
                evidence_id in point_refs.get(point_id, set())
                for point_id in artifact.subject_point_ids
            )
            if artifact.role == "authoritative_map":
                owned = owned and evidence_id in identity_refs
        else:
            owned = False
        if not owned:
            invalid.append(_reason("EVIDENCE_OWNERSHIP_INVALID", pointer))

    if any(
        artifacts[ref].role not in {"identity", "authoritative_map"}
        for ref in identity_refs
        if ref in artifacts
    ):
        invalid.append(
            _reason(
                "IDENTITY_EVIDENCE_ROLE_INVALID",
                "/profile_payload/device_identity/evidence_refs",
            )
        )
    if any(
        artifacts[ref].role not in {"line_protocol", "raw_observation"}
        for ref in line_refs
        if ref in artifacts
    ):
        invalid.append(
            _reason(
                "LINE_PROTOCOL_EVIDENCE_ROLE_INVALID",
                "/profile_payload/line_protocol/evidence_refs",
            )
        )
    for point_index, point in enumerate(profile.profile_payload.points):
        if any(
            artifacts[ref].role not in point_scoped_roles
            or point.point_id not in artifacts[ref].subject_point_ids
            for ref in point.evidence_refs
            if ref in artifacts
        ):
            invalid.append(
                _reason(
                    "POINT_EVIDENCE_OWNER_INVALID",
                    f"/profile_payload/points/{point_index}/evidence_refs",
                )
            )

    run_counts: dict[tuple[str, str, str], int] = {}
    run_ids_by_point: dict[str, set[str]] = {}
    for artifact in artifacts.values():
        if artifact.role not in {"calibration", "reference", "raw_observation"}:
            continue
        if artifact.run_id is None or len(artifact.subject_point_ids) != 1:
            continue
        point_id = artifact.subject_point_ids[0]
        key = (point_id, artifact.role, artifact.run_id)
        run_counts[key] = run_counts.get(key, 0) + 1
        run_ids_by_point.setdefault(point_id, set()).add(artifact.run_id)
    for point in profile.profile_payload.points:
        for run_id in run_ids_by_point.get(point.point_id, set()):
            for role in ("calibration", "reference", "raw_observation"):
                if run_counts.get((point.point_id, role, run_id), 0) != 1:
                    invalid.append(
                        _reason(
                            "RUN_EVIDENCE_CARDINALITY_INVALID",
                            f"/profile_payload/points/{point.point_id}/evidence_refs",
                        )
                    )
    return invalid


def _identity_evidence_conflict_reasons(
    identity: DeviceIdentity,
    identity_digest: str,
    artifacts: Iterable[EvidenceArtifact],
) -> list[GateReason]:
    expected_identity = (
        identity.model,
        identity.hardware_revision,
        identity.firmware_version,
        identity.point_map_version,
        identity.usb_serial_number,
    )
    invalid: list[GateReason] = []
    for artifact in artifacts:
        if artifact.role == "identity" and (
            not isinstance(artifact.content, IdentityEvidenceContent)
            or (
                artifact.content.model,
                artifact.content.hardware_revision,
                artifact.content.firmware_version,
                artifact.content.point_map_version,
                artifact.content.usb_serial_number,
            )
            != expected_identity
        ):
            invalid.append(
                _reason("IDENTITY_EVIDENCE_CONFLICT", "/profile_payload/device_identity")
            )
        if artifact.role == "authoritative_map" and (
            not isinstance(artifact.content, AuthoritativeMapEvidenceContent)
            or artifact.content.device_model != identity.model
            or artifact.content.hardware_revision != identity.hardware_revision
            or artifact.content.firmware_version != identity.firmware_version
            or artifact.content.point_map_version != identity.point_map_version
            or artifact.content.device_identity_sha256 != identity_digest
            or artifact.content.device_serial != identity.usb_serial_number
        ):
            invalid.append(_reason("IDENTITY_MAP_CONFLICT", "/profile_payload/device_identity"))
    return invalid


def _authoritative_map_conflict_reasons(
    profile: DevicePointProfile,
    artifacts: Iterable[EvidenceArtifact],
) -> list[GateReason]:
    mapped_by_point: dict[str, list[MapPointEvidence]] = {}
    for artifact in artifacts:
        if not isinstance(artifact.content, AuthoritativeMapEvidenceContent):
            continue
        for mapped in artifact.content.points:
            mapped_by_point.setdefault(mapped.point_id, []).append(mapped)
    invalid: list[GateReason] = []
    for index, point in enumerate(profile.profile_payload.points):
        expected = (
            point.point_name,
            point.unit,
            point.function_code,
            point.start_address,
            point.register_width,
            point.bit,
            point.encoding.value_type,
            point.encoding.byte_order,
            point.encoding.word_order,
        )
        if any(
            (
                mapped.point_name,
                mapped.unit,
                mapped.function_code,
                mapped.start_address,
                mapped.register_width,
                mapped.bit,
                mapped.value_type,
                mapped.byte_order,
                mapped.word_order,
            )
            != expected
            for mapped in mapped_by_point.get(point.point_id, [])
        ):
            invalid.append(
                _reason(
                    "AUTHORITATIVE_MAP_CONFLICT",
                    f"/profile_payload/points/{index}/encoding",
                )
            )
    return invalid


def _point_calibration_reasons(  # noqa: PLR0911, PLR0912, PLR0915 - closed calibration variants
    point: PointProfile,
    artifacts: Mapping[str, EvidenceArtifact],
    pointer: str,
    *,
    point_plan: CalibrationPointPlan | None = None,
) -> list[GateReason]:
    calibration_items = [
        artifacts[ref]
        for ref in point.evidence_refs
        if ref in artifacts and artifacts[ref].role == "calibration"
    ]
    reference_items = [
        artifacts[ref]
        for ref in point.evidence_refs
        if ref in artifacts and artifacts[ref].role == "reference"
    ]
    raw_observation_items = [
        artifacts[ref]
        for ref in point.evidence_refs
        if ref in artifacts and artifacts[ref].role == "raw_observation"
    ]
    if len(calibration_items) != 1 or len(reference_items) != 1:
        return [_reason("CALIBRATION_EVIDENCE_INCOMPLETE", f"{pointer}/evidence_refs")]
    if len(raw_observation_items) != 1:
        return [_reason("RAW_OBSERVATION_EVIDENCE_INCOMPLETE", f"{pointer}/evidence_refs")]
    if any(
        calibration.attestor_id == reference.attestor_id
        for calibration in calibration_items
        for reference in reference_items
    ):
        return [_reason("CALIBRATION_REFERENCE_NOT_INDEPENDENT", f"{pointer}/evidence_refs")]
    calibration_artifact = calibration_items[0]
    reference_artifact = reference_items[0]
    raw_observation_artifact = raw_observation_items[0]
    if (
        len(
            {
                calibration_artifact.run_id,
                reference_artifact.run_id,
                raw_observation_artifact.run_id,
            }
        )
        != 1
        or len(
            {
                calibration_artifact.calibration_run_approval_sha256,
                reference_artifact.calibration_run_approval_sha256,
                raw_observation_artifact.calibration_run_approval_sha256,
            }
        )
        != 1
    ):
        return [_reason("CALIBRATION_RUN_BINDING_MISMATCH", f"{pointer}/evidence_refs")]
    calibration = calibration_artifact.content
    reference = reference_artifact.content
    raw_observation = raw_observation_artifact.content
    if not isinstance(
        calibration,
        AnalogCalibrationEvidence | BinaryCalibrationEvidence | CounterCalibrationEvidence,
    ) or not isinstance(raw_observation, RawObservationEvidenceContent):
        return [_reason("CALIBRATION_KIND_MISMATCH", f"{pointer}/calibration_profile")]
    if raw_observation.plan_id != calibration.plan_id:
        return [_reason("RAW_OBSERVATION_PLAN_MISMATCH", f"{pointer}/evidence_refs")]
    sample_facts = _calibration_sample_facts(calibration)
    raw_records = _raw_modbus_observations(raw_observation)
    raw_facts = {
        record.sample_id: (record.event_id, record.observed_at, record.decoded_raw)
        for record in raw_records
    }
    if set(raw_facts) != set(sample_facts) or any(
        raw_facts[sample_id][:2] != expected[:2]
        or not _close_number(raw_facts[sample_id][2], expected[2])
        for sample_id, expected in sample_facts.items()
    ):
        return [_reason("RAW_OBSERVATION_SAMPLE_MISMATCH", f"{pointer}/evidence_refs")]
    profile = point.calibration_profile
    if isinstance(profile, AnalogCalibrationProfile):
        if not isinstance(calibration, AnalogCalibrationEvidence) or not isinstance(
            reference, AnalogReferenceEvidence
        ):
            return [_reason("CALIBRATION_KIND_MISMATCH", f"{pointer}/calibration_profile")]
        attempted = _analog_attempted_samples(calibration)
        if [sample.sample_id for sample in attempted] != [
            sample.sample_id for sample in reference.samples
        ]:
            return [_reason("REFERENCE_SAMPLE_SET_MISMATCH", f"{pointer}/evidence_refs")]
        maximum_reference_uncertainty = calibration.thresholds.uncertainty_budget
        if point_plan is not None and point_plan.maximum_reference_uncertainty is not None:
            maximum_reference_uncertainty = min(
                maximum_reference_uncertainty,
                point_plan.maximum_reference_uncertainty,
            )
        for observed, independent in zip(attempted, reference.samples, strict=True):
            if (
                independent.state_id != observed.state_id
                or independent.event_id != observed.event_id
                or independent.unit != calibration.unit_conversion.source_unit
            ):
                return [_reason("REFERENCE_SAMPLE_FACT_MISMATCH", f"{pointer}/evidence_refs")]
            actual_sync_error = _timestamp_distance_ms(
                observed.observed_at,
                independent.observed_at,
            )
            if not _close_number(independent.sync_error_ms, actual_sync_error):
                return [_reason("REFERENCE_SAMPLE_FACT_MISMATCH", f"{pointer}/evidence_refs")]
            in_range = (
                reference.instrument_capability.range_minimum
                <= independent.reference_value
                <= reference.instrument_capability.range_maximum
            )
            if isinstance(observed, AnalogSample):
                if (
                    independent.outcome != "ACCEPTED"
                    or independent.exclusion_reason is not None
                    or not independent.stable
                ):
                    return [_reason("REFERENCE_SAMPLE_FACT_MISMATCH", f"{pointer}/evidence_refs")]
                if not in_range:
                    return [_reason("REFERENCE_SAMPLE_RANGE_EXCEEDED", f"{pointer}/evidence_refs")]
                if actual_sync_error > calibration.thresholds.maximum_sync_error_ms:
                    return [_reason("REFERENCE_SAMPLE_SYNC_EXCEEDED", f"{pointer}/evidence_refs")]
                if independent.uncertainty > maximum_reference_uncertainty:
                    return [
                        _reason(
                            "REFERENCE_SAMPLE_UNCERTAINTY_EXCEEDED",
                            f"{pointer}/evidence_refs",
                        )
                    ]
                if (
                    not _close_number(independent.reference_value, observed.reference_value)
                    or not _close_number(independent.uncertainty, observed.uncertainty)
                    or not _close_number(observed.sync_error_ms, actual_sync_error)
                ):
                    return [_reason("REFERENCE_SAMPLE_FACT_MISMATCH", f"{pointer}/evidence_refs")]
            else:
                expected_exclusion = (
                    (observed.reason_code == "INSTRUMENT_OUT_OF_RANGE" and not in_range)
                    or (
                        observed.reason_code == "REFERENCE_UNCERTAINTY_EXCEEDED"
                        and independent.uncertainty > maximum_reference_uncertainty
                    )
                    or (
                        observed.reason_code == "SYNC_ERROR_EXCEEDED"
                        and actual_sync_error > calibration.thresholds.maximum_sync_error_ms
                    )
                    or (observed.reason_code == "UNSTABLE" and not independent.stable)
                )
                if (
                    independent.outcome != "EXCLUDED"
                    or independent.exclusion_reason != observed.reason_code
                    or not expected_exclusion
                ):
                    return [_reason("REFERENCE_SAMPLE_FACT_MISMATCH", f"{pointer}/evidence_refs")]
        mapping = profile.engineering_mapping
        if mapping is None or (calibration.ratio, calibration.offset) != (
            mapping.ratio,
            mapping.offset,
        ):
            return [_reason("ANALOG_MAPPING_EVIDENCE_MISMATCH", f"{pointer}/calibration_profile")]
        states = {state.state_id: state for state in calibration.states}
        if not all(
            _number_close(
                state.aggregate_engineering,
                mapping.ratio * state.aggregate_raw + mapping.offset,
                calibration.thresholds.absolute_tolerance
                + calibration.thresholds.relative_tolerance * abs(state.aggregate_engineering),
            )
            for state in states.values()
        ):
            return [_reason("ANALOG_CALIBRATION_FIT_FAILED", f"{pointer}/calibration_profile")]
        if not _number_close(
            states["A_RETURN"].aggregate_raw,
            states["A"].aggregate_raw,
            calibration.thresholds.return_raw_tolerance,
        ) or not _number_close(
            states["A_RETURN"].aggregate_engineering,
            states["A"].aggregate_engineering,
            calibration.thresholds.return_engineering_tolerance,
        ):
            return [_reason("ANALOG_RETURN_CHECK_FAILED", f"{pointer}/calibration_profile")]
        raw_domain = point.encoding.raw_domain
        if raw_domain is None or any(
            raw_domain.minimum <= control.injected_raw <= raw_domain.maximum
            for control in calibration.negative_controls
        ):
            return [_reason("ANALOG_NEGATIVE_CONTROL_FAILED", f"{pointer}/calibration_profile")]
        if (
            calibration.plan_id != reference.plan_id
            or any(
                not _number_close(
                    reference.state_aggregates[state_id],
                    state.aggregate_engineering,
                    calibration.thresholds.absolute_tolerance,
                )
                for state_id, state in states.items()
            )
            or reference.unit_conversion != calibration.unit_conversion
        ):
            return [_reason("REFERENCE_AGGREGATE_MISMATCH", f"{pointer}/evidence_refs")]
    elif isinstance(profile, BinaryCalibrationProfile):
        if not isinstance(calibration, BinaryCalibrationEvidence) or not isinstance(
            reference, BinaryReferenceEvidence
        ):
            return [_reason("CALIBRATION_KIND_MISMATCH", f"{pointer}/calibration_profile")]
        binary_samples = [sample for state in calibration.states for sample in state.samples]
        if [sample.sample_id for sample in binary_samples] != [
            sample.sample_id for sample in reference.samples
        ]:
            return [_reason("REFERENCE_SAMPLE_SET_MISMATCH", f"{pointer}/evidence_refs")]
        expected_reference_states = {
            "INACTIVE": "INACTIVE",
            "ACTIVE": "ACTIVE",
            "RETURN": "INACTIVE",
        }
        for binary_observed, binary_independent in zip(
            binary_samples,
            reference.samples,
            strict=True,
        ):
            actual_sync_error = _timestamp_distance_ms(
                binary_observed.observed_at,
                binary_independent.observed_at,
            )
            if (
                binary_independent.state_id != binary_observed.state_id
                or binary_independent.event_id != binary_observed.event_id
                or binary_independent.reference_state
                != expected_reference_states[binary_observed.state_id]
                or binary_independent.unit != point.unit
                or not _close_number(binary_independent.sync_error_ms, actual_sync_error)
            ):
                return [_reason("REFERENCE_SAMPLE_FACT_MISMATCH", f"{pointer}/evidence_refs")]
            if actual_sync_error > calibration.maximum_sync_error_ms:
                return [_reason("REFERENCE_SAMPLE_SYNC_EXCEEDED", f"{pointer}/evidence_refs")]
        profile_states = (profile.inactive_raw, profile.active_raw)
        binary_states: dict[str, BinaryStateEvidence] = {
            state.state_id: state for state in calibration.states
        }
        if None in profile_states or (
            binary_states["INACTIVE"].aggregate_raw,
            binary_states["ACTIVE"].aggregate_raw,
            binary_states["RETURN"].aggregate_raw,
        ) != (profile_states[0], profile_states[1], profile_states[0]):
            return [_reason("BINARY_TRANSITION_CHECK_FAILED", f"{pointer}/calibration_profile")]
        if (
            calibration.plan_id != reference.plan_id
            or (reference.inactive_raw, reference.active_raw) != profile_states
            or reference.selected_candidate_id != calibration.address_semantics.candidate_id
            or set(reference.rejected_candidate_ids)
            != {
                control.candidate.candidate_id
                for control in calibration.competing_candidate_controls
            }
            or set(reference.unintervened_control_ids)
            != {control.control_id for control in calibration.unintervened_channel_controls}
        ):
            return [_reason("BINARY_REFERENCE_CHECK_FAILED", f"{pointer}/evidence_refs")]
        if any(control.injected_raw in profile_states for control in calibration.negative_controls):
            return [_reason("BINARY_NEGATIVE_CONTROL_FAILED", f"{pointer}/calibration_profile")]
        address = calibration.address_semantics
        if (
            address.function_code,
            address.start_address,
            address.register_width,
            address.bit,
        ) != (point.function_code, point.start_address, point.register_width, point.bit):
            return [_reason("BINARY_ADDRESS_SEMANTICS_MISMATCH", f"{pointer}/calibration_profile")]
    elif isinstance(profile, CounterCalibrationProfile):
        if not isinstance(calibration, CounterCalibrationEvidence) or not isinstance(
            reference, CounterReferenceEvidence
        ):
            return [_reason("CALIBRATION_KIND_MISMATCH", f"{pointer}/calibration_profile")]
        if [observation.sample_id for observation in calibration.observations] != [
            sample.sample_id for sample in reference.samples
        ]:
            return [_reason("REFERENCE_SAMPLE_SET_MISMATCH", f"{pointer}/evidence_refs")]
        for counter_observed, counter_independent in zip(
            calibration.observations,
            reference.samples,
            strict=True,
        ):
            actual_sync_error = _timestamp_distance_ms(
                counter_observed.observed_at,
                counter_independent.observed_at,
            )
            if (
                counter_independent.state_id != counter_observed.state_id
                or counter_independent.event_id != counter_observed.event_id
                or counter_independent.reference_raw != counter_observed.raw
                or not _close_number(
                    counter_independent.reference_increment,
                    counter_observed.reference_increment,
                )
                or counter_independent.unit != point.unit
                or not _close_number(counter_independent.sync_error_ms, actual_sync_error)
            ):
                return [_reason("REFERENCE_SAMPLE_FACT_MISMATCH", f"{pointer}/evidence_refs")]
            if actual_sync_error > calibration.maximum_sync_error_ms:
                return [_reason("REFERENCE_SAMPLE_SYNC_EXCEEDED", f"{pointer}/evidence_refs")]
            if not _close_number(counter_observed.sync_error_ms, actual_sync_error):
                return [_reason("REFERENCE_SAMPLE_FACT_MISMATCH", f"{pointer}/evidence_refs")]
        expected = (profile.counts_per_unit, profile.modulus, profile.rollover_behavior)
        if (
            calibration.counts_per_unit,
            calibration.modulus,
            calibration.rollover_behavior,
        ) != expected or not _counter_sequence_valid(calibration):
            return [_reason("COUNTER_SEQUENCE_CHECK_FAILED", f"{pointer}/calibration_profile")]
        if (
            reference.counts_per_unit,
            reference.modulus,
            reference.rollover_behavior,
            reference.expected_increment,
            reference.expected_terminal_raw,
            reference.expected_persistence_raw,
            reference.power_loss_event_id,
            reference.persistence_method,
        ) != (
            *expected,
            calibration.expected_increment,
            calibration.terminal_raw,
            calibration.persistence_after,
            calibration.persistence_event.event_id,
            calibration.persistence_event.method,
        ) or calibration.plan_id != reference.plan_id:
            return [_reason("COUNTER_REFERENCE_CHECK_FAILED", f"{pointer}/evidence_refs")]
        reference_persistence = reference.persistence_event
        calibration_persistence = calibration.persistence_event
        if (
            reference_persistence.event_id != calibration_persistence.event_id
            or reference_persistence.method != calibration_persistence.method
            or _timestamp_distance_ms(
                reference_persistence.power_removed_at,
                calibration_persistence.power_removed_at,
            )
            > calibration.maximum_sync_error_ms
            or _timestamp_distance_ms(
                reference_persistence.power_restored_at,
                calibration_persistence.power_restored_at,
            )
            > calibration.maximum_sync_error_ms
            or reference_persistence.pre_power_raw != calibration_persistence.pre_power_raw
            or reference_persistence.post_power_raw != calibration_persistence.post_power_raw
            or _timestamp_distance_ms(
                reference_persistence.post_restore_observed_at,
                calibration_persistence.post_restore_observed_at,
            )
            > calibration.maximum_sync_error_ms
        ):
            return [_reason("REFERENCE_PERSISTENCE_MISMATCH", f"{pointer}/evidence_refs")]
        if any(
            0 <= control.injected_raw < calibration.modulus
            for control in calibration.negative_controls
        ):
            return [_reason("COUNTER_NEGATIVE_CONTROL_FAILED", f"{pointer}/calibration_profile")]
    else:
        return [
            _reason("CALIBRATION_PROFILE_KIND_UNRESOLVED", f"{pointer}/calibration_profile/kind")
        ]
    return []


def _load_runtime_artifact(  # noqa: PLR0911 - fail-closed reason classification
    binding: RuntimeEvidenceBinding,
    contents: bytes,
    *,
    root: Path,
    target: RuntimeTarget | None,
    policy: TrustPolicy | None,
    release_receipt: ReleaseVerificationReceipt | None,
    additional_artifact_budget: _ArtifactReadBudget,
) -> tuple[RuntimeEvidenceArtifact | None, GateReason | None]:
    try:
        artifact = RuntimeEvidenceArtifact.model_validate(_load_json_bytes(contents))
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
        ValidationError,
    ):
        return None, _reason("RUNTIME_ARTIFACT_INVALID", f"/{binding.path}")
    if artifact.check_id != binding.check_id:
        return None, _reason("RUNTIME_CHECK_ID_MISMATCH", f"/{binding.path}")
    if target is None or artifact.runtime_target != target:
        return None, _reason("RUNTIME_TARGET_MISMATCH", f"/runtime_evidence/{binding.check_id}")
    raw_contents, raw_error = _check_binding(
        artifact.raw_report,
        root,
        additional_budget=additional_artifact_budget,
    )
    if raw_error is not None:
        return None, raw_error
    try:
        raw_report = RuntimeRawReport.model_validate(_load_json_bytes(raw_contents or b""))
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
        ValidationError,
    ):
        return None, _reason("RUNTIME_RAW_REPORT_INVALID", f"/{artifact.raw_report.path}")
    if raw_report.check_id != artifact.check_id or raw_report.result != artifact.result:
        return None, _reason("RUNTIME_RAW_REPORT_MISMATCH", f"/{artifact.raw_report.path}")
    if datetime.fromisoformat(artifact.observed_at) < datetime.fromisoformat(
        raw_report.completed_at
    ):
        return None, _reason("RUNTIME_OBSERVATION_TIME_MISMATCH", f"/{artifact.raw_report.path}")
    if release_receipt is not None and datetime.fromisoformat(release_receipt.verified_at) > min(
        datetime.fromisoformat(raw_report.started_at),
        datetime.fromisoformat(artifact.observed_at),
    ):
        return None, _reason(
            "RUNTIME_PRECEDES_RELEASE_RECEIPT",
            f"/runtime_evidence/{binding.check_id}",
        )
    if policy is not None:
        trust_key = next(
            (
                key
                for key in policy.runtime_runner_keys
                if key.runner_id == artifact.runner_id
                and key.key_id == artifact.signature.key_id
                and key.tool_id == artifact.tool_id
                and key.tool_sha256 == artifact.tool_sha256
            ),
            None,
        )
        if trust_key is None:
            return None, _reason(
                "RUNTIME_RUNNER_UNTRUSTED", f"/runtime_evidence/{binding.check_id}"
            )
        if not _trust_key_valid_at(trust_key, artifact.observed_at, policy):
            return None, _reason(
                "RUNTIME_KEY_NOT_VALID_AT_ARTIFACT_TIME",
                f"/runtime_evidence/{binding.check_id}",
            )
        if not _verify_canonical_signature(
            trust_key.public_key,
            artifact.signature,
            lambda: runtime_signature_message(artifact),
        ):
            return None, _reason(
                "RUNTIME_SIGNATURE_INVALID", f"/runtime_evidence/{binding.check_id}"
            )
    return artifact, None


def _calibration_run_approval_reasons(  # noqa: PLR0912, PLR0915 - closed approval contract
    approval: CalibrationRunApprovalArtifact,
    approval_binding: CalibrationRunApprovalBinding,
    profile: DevicePointProfile,
    policy: TrustPolicy | None,
    *,
    current: datetime,
    evidence_artifacts: Mapping[str, EvidenceArtifact],
) -> tuple[list[GateReason], list[GateReason]]:
    invalid: list[GateReason] = []
    blocked: list[GateReason] = []
    pointer = "/calibration_run_approval_binding"
    if approval.subject_plan_sha256 != canonical_calibration_plan_sha256(approval):
        invalid.append(_reason("CALIBRATION_PLAN_HASH_MISMATCH", pointer))
    if approval_binding.subject_plan_sha256 != approval.subject_plan_sha256:
        invalid.append(_reason("CALIBRATION_PLAN_BINDING_MISMATCH", pointer))
    identity = profile.profile_payload.device_identity
    identity_digest = canonical_device_identity_sha256(identity)
    line = profile.profile_payload.line_protocol
    expected_identity = (
        profile.profile_id,
        canonical_calibration_profile_input_sha256(profile),
        profile.schema_sha256,
        profile.policy_sha256,
        profile.trust_root_sha256,
        profile.semantic_validator,
        profile.validator_source_sha256,
        identity_digest,
        identity.usb_serial_number,
        identity.model,
        identity.hardware_revision,
        identity.firmware_version,
        identity.point_map_version,
        line.stable_device_path,
        line.unit_id,
        line.baud_rate,
        line.data_bits,
        line.parity,
        line.stop_bits,
    )
    actual_identity = (
        approval.profile_id,
        approval.profile_input_sha256,
        approval.schema_sha256,
        approval.policy_sha256,
        approval.trust_root_sha256,
        approval.semantic_validator,
        approval.validator_source_sha256,
        approval.device_identity_sha256,
        approval.device_serial,
        approval.model,
        approval.hardware_revision,
        approval.firmware_version,
        approval.point_map_version,
        approval.stable_device_path,
        approval.unit_id,
        approval.baud_rate,
        approval.data_bits,
        approval.parity,
        approval.stop_bits,
    )
    if actual_identity != expected_identity:
        invalid.append(_reason("CALIBRATION_APPROVAL_SCOPE_MISMATCH", pointer))
    points = {point.point_id: point for point in profile.profile_payload.points}
    plans = {plan.point_id: plan for plan in approval.plans}
    if set(plans) != set(points):
        blocked.append(_reason("CALIBRATION_APPROVAL_POINT_SET_MISMATCH", pointer))
    for point_id, plan in plans.items():
        point = points.get(point_id)
        if point is None:
            continue
        if plan.calibration_kind != point.calibration_profile.kind:
            invalid.append(_reason("CALIBRATION_PLAN_KIND_MISMATCH", f"{pointer}/plans/{point_id}"))
        expected_point_scope = (
            point.point_name,
            point.unit,
            point.function_code,
            point.start_address,
            point.register_width,
            point.bit,
            point.encoding.value_type,
            point.encoding.byte_order,
            point.encoding.word_order,
            point.encoding.raw_domain,
            point.calibration_profile.kind,
        )
        actual_point_scope = (
            plan.point_name,
            plan.point_unit,
            plan.function_code,
            plan.start_address,
            plan.register_width,
            plan.bit,
            plan.value_type,
            plan.byte_order,
            plan.word_order,
            plan.raw_domain,
            plan.calibration_kind,
        )
        if actual_point_scope != expected_point_scope:
            invalid.append(
                _reason("CALIBRATION_PLAN_POINT_SCOPE_MISMATCH", f"{pointer}/plans/{point_id}")
            )
        if not any(
            scope.function_code == point.function_code
            and scope.start_address == point.start_address
            and scope.quantity == (point.register_width or 1)
            for scope in plan.tx_scope
        ):
            invalid.append(_reason("CALIBRATION_TX_SCOPE_MISMATCH", f"{pointer}/plans/{point_id}"))
    valid_from = datetime.fromisoformat(approval.valid_from)
    expires_at = datetime.fromisoformat(approval.expires_at)
    if not valid_from <= current < expires_at:
        blocked.append(_reason("CALIBRATION_APPROVAL_NOT_CURRENT", pointer))
    approval_times: list[datetime] = []
    for entry in approval.approvals:
        approved_at = datetime.fromisoformat(entry.approved_at)
        approval_times.append(approved_at)
        if not valid_from <= approved_at < expires_at or approved_at > current:
            blocked.append(_reason("CALIBRATION_APPROVAL_TIME_INVALID", f"{pointer}/{entry.role}"))
        if policy is None:
            continue
        trust_key = next(
            (
                key
                for key in policy.approval_keys
                if key.role == entry.role
                and key.key_id == entry.key_id
                and key.identity == entry.identity
            ),
            None,
        )
        if trust_key is None:
            invalid.append(_reason("CALIBRATION_APPROVER_UNTRUSTED", f"{pointer}/{entry.role}"))
        elif not _trust_key_valid_at(trust_key, approved_at, policy):
            invalid.append(
                _reason(
                    "CALIBRATION_APPROVER_KEY_NOT_VALID_AT_APPROVAL_TIME",
                    f"{pointer}/{entry.role}",
                )
            )
        elif not _verify_canonical_signature(
            trust_key.public_key,
            entry.signature,
            partial(calibration_run_approval_signature_message, approval, entry),
        ):
            invalid.append(
                _reason("CALIBRATION_APPROVAL_SIGNATURE_INVALID", f"{pointer}/{entry.role}")
            )
    latest_approval = max(approval_times) if approval_times else None
    for evidence_id, artifact in evidence_artifacts.items():
        if artifact.role not in {"calibration", "reference", "raw_observation"}:
            continue
        observed_at = datetime.fromisoformat(artifact.observed_at)
        content_timestamps = _run_content_timestamps(artifact.content)
        if (
            artifact.calibration_run_approval_sha256 != approval_binding.sha256
            or artifact.run_id != approval.run_id
        ):
            invalid.append(_reason("RUN_APPROVAL_BINDING_MISMATCH", f"/evidence/{evidence_id}"))
        if (
            latest_approval is None
            or observed_at < latest_approval
            or any(timestamp < latest_approval for timestamp in content_timestamps)
        ):
            invalid.append(_reason("RUN_EVIDENCE_PRECEDES_APPROVAL", f"/evidence/{evidence_id}"))
        if any(timestamp > observed_at for timestamp in content_timestamps):
            invalid.append(_reason("RUN_EVIDENCE_AFTER_ATTESTATION", f"/evidence/{evidence_id}"))
        if not valid_from <= observed_at < expires_at or any(
            not valid_from <= timestamp < expires_at for timestamp in content_timestamps
        ):
            blocked.append(
                _reason("RUN_EVIDENCE_OUTSIDE_APPROVAL_WINDOW", f"/evidence/{evidence_id}")
            )
        content = artifact.content
        run_point_id = getattr(content, "point_id", None)
        point_plan = plans.get(run_point_id) if isinstance(run_point_id, str) else None
        if point_plan is None:
            invalid.append(_reason("RUN_EVIDENCE_PLAN_MISSING", f"/evidence/{evidence_id}"))
            continue
        if getattr(content, "plan_id", None) != point_plan.plan_id:
            invalid.append(_reason("RUN_EVIDENCE_PLAN_MISMATCH", f"/evidence/{evidence_id}"))
            continue
        if isinstance(content, AnalogCalibrationEvidence):
            thresholds = content.thresholds
            expected_thresholds = (
                point_plan.minimum_raw_span,
                point_plan.minimum_reference_span,
                point_plan.absolute_tolerance,
                point_plan.relative_tolerance,
                point_plan.return_raw_tolerance,
                point_plan.return_engineering_tolerance,
                point_plan.sync_tolerance_ms,
                point_plan.uncertainty_budget,
            )
            actual_thresholds = (
                thresholds.minimum_raw_span,
                thresholds.minimum_reference_span,
                thresholds.absolute_tolerance,
                thresholds.relative_tolerance,
                thresholds.return_raw_tolerance,
                thresholds.return_engineering_tolerance,
                thresholds.maximum_sync_error_ms,
                thresholds.uncertainty_budget,
            )
            if (
                actual_thresholds != expected_thresholds
                or any(
                    len(state.samples) != point_plan.sample_count_per_state
                    for state in content.states
                )
                or any(
                    state.observed_stability > point_plan.stability_threshold
                    for state in content.states
                )
                or (
                    content.aggregation_method != point_plan.analog_aggregation_method
                    or content.unit_conversion != point_plan.analog_unit_conversion
                    or content.exclusion_policy != point_plan.analog_exclusion_policy
                    or thresholds.business_tolerance_source
                    != point_plan.analog_business_tolerance_source
                )
            ):
                invalid.append(
                    _reason("ANALOG_PLAN_THRESHOLD_MISMATCH", f"/evidence/{evidence_id}")
                )
        elif isinstance(content, BinaryCalibrationEvidence):
            planned_candidates = {
                candidate.candidate_id: candidate
                for candidate in (point_plan.binary_address_candidates or [])
            }
            planned_controls = {
                control.control_id: control
                for control in (point_plan.binary_unintervened_channels or [])
            }
            observed_competitors = {
                control.candidate.candidate_id: control.candidate
                for control in content.competing_candidate_controls
            }
            observed_controls = {
                control.control_id: control for control in content.unintervened_channel_controls
            }
            if (
                content.maximum_chatter_transitions != point_plan.maximum_chatter_transitions
                or content.maximum_sync_error_ms != point_plan.sync_tolerance_ms
                or any(
                    len(state.samples) != point_plan.sample_count_per_state
                    for state in content.states
                )
                or any(
                    state.observed_stability > point_plan.stability_threshold
                    for state in content.states
                )
                or content.address_semantics.candidate_id != point_plan.binary_selected_candidate_id
                or planned_candidates.get(content.address_semantics.candidate_id)
                != content.address_semantics
                or observed_competitors
                != {
                    candidate_id: candidate
                    for candidate_id, candidate in planned_candidates.items()
                    if candidate_id != point_plan.binary_selected_candidate_id
                }
                or set(observed_controls) != set(planned_controls)
                or any(
                    observed_controls[control_id].point_id != planned.point_id
                    or observed_controls[control_id].point_name != planned.point_name
                    or observed_controls[control_id].address_semantics != planned.address_semantics
                    or not _close_number(
                        observed_controls[control_id].baseline_raw,
                        planned.expected_raw,
                    )
                    for control_id, planned in planned_controls.items()
                )
            ):
                invalid.append(
                    _reason("BINARY_PLAN_THRESHOLD_MISMATCH", f"/evidence/{evidence_id}")
                )
        elif isinstance(content, CounterCalibrationEvidence):
            state_counts = {
                state_id: len(observations)
                for state_id, observations in _counter_observation_groups(
                    content.observations
                ).items()
            }
            if (
                content.expected_increment != point_plan.expected_counter_increment
                or content.increment_tolerance != point_plan.counter_increment_tolerance
                or content.modulus != point_plan.counter_modulus
                or content.rollover_behavior != point_plan.counter_rollover_behavior
                or content.maximum_sync_error_ms != point_plan.sync_tolerance_ms
                or content.persistence_event.method != point_plan.counter_persistence_method
                or point_plan.minimum_power_off_duration_seconds is None
                or content.persistence_event.power_off_duration_seconds
                < point_plan.minimum_power_off_duration_seconds
                or any(
                    observation.observed_stability > point_plan.stability_threshold
                    for observation in content.observations
                )
                or set(state_counts) != set(COUNTER_STATE_SEQUENCE)
                or any(
                    state_counts[state_id] != point_plan.sample_count_per_state
                    for state_id in COUNTER_STATE_SEQUENCE
                )
            ):
                invalid.append(
                    _reason("COUNTER_PLAN_THRESHOLD_MISMATCH", f"/evidence/{evidence_id}")
                )
        elif isinstance(
            content,
            AnalogReferenceEvidence | BinaryReferenceEvidence | CounterReferenceEvidence,
        ):
            if (
                content.reference_id != point_plan.instrument_id
                or content.channel_id != point_plan.reference_channel_id
                or content.calibration_certificate_sha256
                != point_plan.instrument_calibration_sha256
                or content.reference_collector_tool_id != point_plan.reference_collector_tool_id
                or content.reference_collector_tool_sha256
                != point_plan.reference_collector_tool_sha256
            ):
                invalid.append(
                    _reason("REFERENCE_PLAN_BINDING_MISMATCH", f"/evidence/{evidence_id}")
                )
            elif isinstance(content, AnalogReferenceEvidence) and (
                content.instrument_capability != point_plan.analog_instrument_capability
                or content.unit_conversion != point_plan.analog_unit_conversion
                or point_plan.maximum_reference_uncertainty is None
                or content.uncertainty > point_plan.maximum_reference_uncertainty
                or content.uncertainty > point_plan.uncertainty_budget
                or content.instrument_capability.resolution > point_plan.uncertainty_budget
                or content.instrument_capability.accuracy > point_plan.uncertainty_budget
                or any(
                    sample.outcome == "ACCEPTED"
                    and (
                        sample.sync_error_ms > point_plan.sync_tolerance_ms
                        or sample.uncertainty > point_plan.maximum_reference_uncertainty
                        or sample.uncertainty > point_plan.uncertainty_budget
                    )
                    for sample in content.samples
                )
            ):
                invalid.append(
                    _reason("ANALOG_REFERENCE_LIMIT_MISMATCH", f"/evidence/{evidence_id}")
                )
            elif isinstance(content, BinaryReferenceEvidence) and any(
                sample.sync_error_ms > point_plan.sync_tolerance_ms
                or sample.uncertainty > point_plan.uncertainty_budget
                for sample in content.samples
            ):
                invalid.append(
                    _reason("BINARY_REFERENCE_LIMIT_MISMATCH", f"/evidence/{evidence_id}")
                )
            elif isinstance(content, CounterReferenceEvidence) and (
                any(
                    sample.sync_error_ms > point_plan.sync_tolerance_ms
                    or sample.uncertainty > point_plan.uncertainty_budget
                    for sample in content.samples
                )
                or content.persistence_method != point_plan.counter_persistence_method
                or point_plan.minimum_power_off_duration_seconds is None
                or content.persistence_event.power_off_duration_seconds
                < point_plan.minimum_power_off_duration_seconds
            ):
                invalid.append(
                    _reason("COUNTER_REFERENCE_LIMIT_MISMATCH", f"/evidence/{evidence_id}")
                )
        elif isinstance(content, RawObservationEvidenceContent):
            observations = _raw_modbus_observations(content)
            if (
                content.collector_tool_id != point_plan.raw_collector_tool_id
                or content.collector_tool_sha256 != point_plan.raw_collector_tool_sha256
            ):
                invalid.append(
                    _reason("RAW_OBSERVATION_COLLECTOR_MISMATCH", f"/evidence/{evidence_id}")
                )
            calibration_content = next(
                (
                    artifact.content
                    for artifact in evidence_artifacts.values()
                    if artifact.role == "calibration"
                    and getattr(artifact.content, "point_id", None) == content.point_id
                    and artifact.run_id == content.run_id
                ),
                None,
            )
            if not isinstance(
                calibration_content,
                AnalogCalibrationEvidence | BinaryCalibrationEvidence | CounterCalibrationEvidence,
            ):
                invalid.append(
                    _reason("RAW_OBSERVATION_CALIBRATION_MISSING", f"/evidence/{evidence_id}")
                )
                continue
            expected_scopes = _calibration_sample_scopes(calibration_content, point_plan)
            observed_scopes = {
                observation.sample_id: (
                    observation.function_code,
                    observation.start_address,
                    observation.quantity,
                )
                for observation in observations
            }
            if (
                content.run_id != approval.run_id
                or any(
                    (
                        observation.unit_id,
                        observed_scopes.get(observation.sample_id),
                    )
                    != (approval.unit_id, expected_scopes.get(observation.sample_id))
                    for observation in observations
                )
                or set(observed_scopes) != set(expected_scopes)
            ):
                invalid.append(
                    _reason("RAW_OBSERVATION_POINT_SCOPE_MISMATCH", f"/evidence/{evidence_id}")
                )
            approved_scopes = {
                (scope.function_code, scope.start_address, scope.quantity): scope.maximum_requests
                for scope in point_plan.tx_scope
            }
            scope_counts = {
                scope: sum(1 for observed in observed_scopes.values() if observed == scope)
                for scope in set(observed_scopes.values())
            }
            if any(
                scope not in approved_scopes or count > approved_scopes[scope]
                for scope, count in scope_counts.items()
            ):
                invalid.append(
                    _reason("RAW_OBSERVATION_TX_BUDGET_EXCEEDED", f"/evidence/{evidence_id}")
                )
            binary_semantics = (
                _binary_sample_semantics(calibration_content)
                if isinstance(calibration_content, BinaryCalibrationEvidence)
                else {}
            )
            if any(
                (
                    decoded := (
                        _decode_raw_modbus_binary_value(
                            observation,
                            binary_semantics[observation.sample_id],
                        )
                        if observation.sample_id in binary_semantics
                        else _decode_raw_modbus_value(observation, point_plan)
                    )
                )
                is None
                or not _close_number(observation.decoded_raw, decoded)
                for observation in observations
            ):
                invalid.append(
                    _reason("RAW_OBSERVATION_DECODE_MISMATCH", f"/evidence/{evidence_id}")
                )
    return invalid, blocked


def _approval_reasons(  # noqa: PLR0912 - approval checks aggregate every reason
    approval: EligibilityApprovalArtifact,
    profile: DevicePointProfile,
    policy: TrustPolicy | None,
    *,
    current: datetime,
    evidence_artifacts: Mapping[str, EvidenceArtifact],
    runtime_artifacts: Mapping[str, RuntimeEvidenceArtifact],
    release_receipt: ReleaseVerificationReceipt | None,
) -> tuple[list[GateReason], list[GateReason]]:
    invalid: list[GateReason] = []
    blocked: list[GateReason] = []
    gate_digest = canonical_gate_sha256(profile)
    if approval.subject_gate_sha256 != gate_digest:
        blocked.append(_reason("APPROVAL_GATE_MISMATCH", "/approval_binding"))
    if approval.schema_sha256 != profile.schema_sha256:
        blocked.append(_reason("APPROVAL_SCHEMA_MISMATCH", "/approval_binding"))
    if approval.policy_sha256 != profile.policy_sha256:
        blocked.append(_reason("APPROVAL_POLICY_MISMATCH", "/approval_binding"))
    if approval.trust_root_sha256 != profile.trust_root_sha256:
        blocked.append(_reason("APPROVAL_TRUST_ROOT_MISMATCH", "/approval_binding"))
    if approval.semantic_validator != profile.semantic_validator:
        blocked.append(_reason("APPROVAL_VALIDATOR_MISMATCH", "/approval_binding"))
    if approval.validator_source_sha256 != profile.validator_source_sha256:
        blocked.append(_reason("APPROVAL_VALIDATOR_SOURCE_MISMATCH", "/approval_binding"))
    valid_from = datetime.fromisoformat(approval.valid_from)
    expires_at = datetime.fromisoformat(approval.expires_at)
    if not valid_from <= current < expires_at:
        blocked.append(_reason("APPROVAL_NOT_CURRENT", "/approval_binding"))
    approved_times: list[datetime] = []
    for entry in approval.approvals:
        approved_at = datetime.fromisoformat(entry.approved_at)
        approved_times.append(approved_at)
        if not valid_from <= approved_at < expires_at or approved_at > current:
            blocked.append(_reason("APPROVAL_TIME_INVALID", f"/approval/{entry.role}"))
        if policy is None:
            continue
        trust_key = next(
            (
                key
                for key in policy.approval_keys
                if key.role == entry.role
                and key.key_id == entry.key_id
                and key.identity == entry.identity
            ),
            None,
        )
        if trust_key is None:
            invalid.append(_reason("APPROVAL_IDENTITY_UNTRUSTED", f"/approval/{entry.role}"))
        elif not _trust_key_valid_at(trust_key, approved_at, policy):
            invalid.append(
                _reason(
                    "APPROVAL_KEY_NOT_VALID_AT_APPROVAL_TIME",
                    f"/approval/{entry.role}",
                )
            )
        elif not _verify_canonical_signature(
            trust_key.public_key,
            entry.signature,
            partial(approval_signature_message, approval, entry),
        ):
            invalid.append(_reason("APPROVAL_SIGNATURE_INVALID", f"/approval/{entry.role}"))
    if approved_times:
        latest_evidence = max(
            (datetime.fromisoformat(item.observed_at) for item in evidence_artifacts.values()),
            default=valid_from,
        )
        latest_runtime = max(
            (datetime.fromisoformat(item.observed_at) for item in runtime_artifacts.values()),
            default=valid_from,
        )
        if any(approved_at < latest_evidence for approved_at in approved_times):
            blocked.append(_reason("ELIGIBILITY_APPROVAL_PRECEDES_EVIDENCE", "/approval_binding"))
        if any(approved_at < latest_runtime for approved_at in approved_times):
            blocked.append(_reason("ELIGIBILITY_APPROVAL_PRECEDES_RUNTIME", "/approval_binding"))
        if release_receipt is not None and any(
            approved_at < datetime.fromisoformat(release_receipt.verified_at)
            for approved_at in approved_times
        ):
            blocked.append(
                _reason(
                    "ELIGIBILITY_APPROVAL_PRECEDES_RELEASE_RECEIPT",
                    "/approval_binding",
                )
            )
    return invalid, blocked


def _validate_profile_data_with_trusted_context(  # noqa: PLR0911, PLR0912, PLR0915
    value: Any,
    *,
    root: Path,
    now: datetime | None = None,
    trust_policy: TrustPolicy | Mapping[str, Any] | None = None,
    trust_root: PolicyTrustRoot | None = None,
) -> EligibilityReport:
    """Validate with authority context loaded by the protected fixed-root boundary or tests."""
    if not isinstance(value, dict):
        return _report("INVALID", [_reason("SCHEMA_INVALID", "/")])
    payload_value = value.get("profile_payload")
    points_value = payload_value.get("points") if isinstance(payload_value, dict) else None
    if isinstance(points_value, list) and len(points_value) > MAX_PROFILE_POINTS:
        return _report(
            "INVALID",
            [_reason("PROFILE_POINT_LIMIT_EXCEEDED", "/profile_payload/points")],
        )
    bindings_value = value.get("evidence_bindings")
    if isinstance(bindings_value, list) and len(bindings_value) > MAX_EVIDENCE_BINDINGS:
        return _report(
            "INVALID",
            [_reason("EVIDENCE_BINDING_LIMIT_EXCEEDED", "/evidence_bindings")],
        )
    try:
        forbidden = _contains_forbidden_key(value)
        _reject_unicode_surrogates(value)
        _reject_non_finite_numbers(value)
    except (RecursionError, TypeError, ValueError):
        return _report("INVALID", [_reason("SCHEMA_INVALID", "/")])
    if forbidden is not None:
        return _report("INVALID", [_reason("CALLER_ELIGIBILITY_FORBIDDEN", f"/{forbidden}")])
    try:
        profile = DevicePointProfile.model_validate(value)
    except ValidationError as validation_error:
        return _invalid_validation_report(validation_error)
    except (OverflowError, RecursionError, TypeError, ValueError):
        return _report("INVALID", [_reason("SCHEMA_INVALID", "/")])
    if sum(binding.size_bytes for binding in profile.evidence_bindings) > MAX_EVIDENCE_BYTES:
        return _report(
            "INVALID",
            [_reason("EVIDENCE_BYTES_LIMIT_EXCEEDED", "/evidence_bindings")],
            profile_id=profile.profile_id,
            payload_sha256=profile.payload_sha256,
        )
    top_level_bound_bytes = sum(binding.size_bytes for binding in profile.evidence_bindings)
    top_level_bound_bytes += sum(binding.size_bytes for binding in profile.runtime_evidence)
    if profile.calibration_run_approval_binding is not None:
        top_level_bound_bytes += profile.calibration_run_approval_binding.size_bytes
    if profile.approval_binding is not None:
        top_level_bound_bytes += profile.approval_binding.size_bytes
    if profile.runtime_target is not None:
        top_level_bound_bytes += profile.runtime_target.release_verification_receipt.size_bytes
    if top_level_bound_bytes > MAX_BOUND_ARTIFACT_BYTES:
        return _report(
            "INVALID",
            [_reason("ARTIFACT_BYTES_LIMIT_EXCEEDED", "/")],
            profile_id=profile.profile_id,
            payload_sha256=profile.payload_sha256,
        )
    additional_artifact_budget = _ArtifactReadBudget(
        remaining_bytes=MAX_BOUND_ARTIFACT_BYTES - top_level_bound_bytes
    )
    try:
        policy = _coerce_trust_policy(trust_policy)
    except (OverflowError, TypeError, ValueError, ValidationError):
        return _report(
            "INVALID",
            [_reason("TRUST_POLICY_INVALID", "/trust_policy")],
            profile_id=profile.profile_id,
            payload_sha256=profile.payload_sha256,
        )
    try:
        authority_root = _coerce_trust_root(trust_root)
    except (TypeError, ValueError):
        return _report(
            "INVALID",
            [_reason("TRUST_ROOT_INVALID", "/trust_root")],
            profile_id=profile.profile_id,
            payload_sha256=profile.payload_sha256,
        )
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        return _report(
            "INVALID",
            [_reason("CURRENT_TIME_INVALID", "/now")],
            profile_id=profile.profile_id,
            payload_sha256=profile.payload_sha256,
        )

    invalid: list[GateReason] = []
    blocked: list[GateReason] = []
    try:
        if canonical_payload_sha256(value["profile_payload"]) != profile.payload_sha256:
            invalid.append(_reason("PAYLOAD_HASH_MISMATCH", "/payload_sha256"))
    except (OverflowError, RecursionError, TypeError, UnicodeEncodeError, ValueError):
        invalid.append(_reason("PAYLOAD_CANONICALIZATION_INVALID", "/profile_payload"))
    if profile.schema_sha256 != current_schema_sha256():
        invalid.append(_reason("SCHEMA_HASH_MISMATCH", "/schema_sha256"))
    current_validator_hash = current_validator_source_sha256()
    if profile.validator_source_sha256 != current_validator_hash:
        invalid.append(_reason("VALIDATOR_SOURCE_HASH_MISMATCH", "/validator_source_sha256"))
    if datetime.fromisoformat(profile.created_at) > current:
        blocked.append(_reason("PROFILE_CREATED_IN_FUTURE", "/created_at"))
    if policy is None:
        blocked.append(_reason("TRUST_POLICY_MISSING", "/trust_policy"))
    else:
        expected_policy_hash = trust_policy_sha256(policy)
        if profile.policy_sha256 != expected_policy_hash:
            invalid.append(_reason("POLICY_HASH_MISMATCH", "/policy_sha256"))
        if policy.validator_source_sha256 != current_validator_hash:
            invalid.append(_reason("TRUST_POLICY_VALIDATOR_SOURCE_MISMATCH", "/trust_policy"))
    authority_invalid, authority_blocked = _policy_authority_reasons(
        profile,
        policy,
        authority_root,
        current=current,
    )
    invalid.extend(authority_invalid)
    blocked.extend(authority_blocked)
    if authority_invalid or authority_blocked:
        decision: Decision = "INVALID" if invalid else "BLOCKED"
        return _report(
            decision,
            invalid + blocked,
            profile_id=profile.profile_id,
            payload_sha256=profile.payload_sha256,
        )

    evidence_by_id = {binding.evidence_id: binding for binding in profile.evidence_bindings}
    referenced = set(profile.profile_payload.device_identity.evidence_refs)
    referenced.update(profile.profile_payload.line_protocol.evidence_refs)
    for point in profile.profile_payload.points:
        referenced.update(point.evidence_refs)
    for contradiction in profile.contradictions:
        referenced.update(contradiction.resolution_evidence_refs)
    missing_refs = referenced - set(evidence_by_id)
    extra_refs = set(evidence_by_id) - referenced
    for ref in sorted(missing_refs):
        invalid.append(_reason("EVIDENCE_REFERENCE_MISSING", f"/evidence/{ref}"))
    for ref in sorted(extra_refs):
        invalid.append(_reason("EVIDENCE_BINDING_UNUSED", f"/evidence/{ref}"))

    identity = profile.profile_payload.device_identity
    identity_digest = canonical_device_identity_sha256(identity)
    expected_serial = identity.usb_serial_number
    point_ids = {point.point_id for point in profile.profile_payload.points}
    evidence_artifacts: dict[str, EvidenceArtifact] = {}
    for binding in profile.evidence_bindings:
        if any(point_id not in point_ids for point_id in binding.subject_point_ids):
            invalid.append(_reason("EVIDENCE_POINT_UNKNOWN", f"/evidence/{binding.evidence_id}"))
        contents, binding_error = _check_binding(binding, root)
        if binding_error is not None:
            invalid.append(binding_error)
            continue
        if contents is None:
            invalid.append(_reason("ARTIFACT_PATH_INVALID", f"/{binding.path}"))
            continue
        artifact, evidence_error = _load_evidence_artifact(binding, contents, policy)
        if evidence_error is not None:
            invalid.append(evidence_error)
            continue
        if artifact is not None:
            evidence_artifacts[binding.evidence_id] = artifact
            if (
                artifact.profile_id != profile.profile_id
                or artifact.device_identity_sha256 != identity_digest
                or expected_serial is None
                or artifact.device_serial != expected_serial
            ):
                invalid.append(
                    _reason("EVIDENCE_DEVICE_SCOPE_MISMATCH", f"/evidence/{binding.evidence_id}")
                )
            if isinstance(artifact.content, AuthoritativeMapEvidenceContent) and (
                artifact.content.device_model,
                artifact.content.hardware_revision,
                artifact.content.firmware_version,
                artifact.content.point_map_version,
                artifact.content.device_identity_sha256,
                artifact.content.device_serial,
            ) != (
                identity.model,
                identity.hardware_revision,
                identity.firmware_version,
                identity.point_map_version,
                identity_digest,
                identity.usb_serial_number,
            ):
                invalid.append(
                    _reason(
                        "AUTHORITATIVE_MAP_SCOPE_MISMATCH",
                        f"/evidence/{binding.evidence_id}",
                    )
                )
            if datetime.fromisoformat(artifact.observed_at) > current:
                blocked.append(
                    _reason("EVIDENCE_OBSERVED_IN_FUTURE", f"/evidence/{binding.evidence_id}")
                )

    invalid.extend(_evidence_ownership_reasons(profile, evidence_artifacts))
    invalid.extend(
        _identity_evidence_conflict_reasons(
            identity,
            identity_digest,
            evidence_artifacts.values(),
        )
    )
    invalid.extend(_authoritative_map_conflict_reasons(profile, evidence_artifacts.values()))

    calibration_approval: CalibrationRunApprovalArtifact | None = None
    requires_calibration_approval = any(
        point.calibration_status == "resolved" for point in profile.profile_payload.points
    )
    if profile.calibration_run_approval_binding is None:
        if requires_calibration_approval:
            blocked.append(
                _reason("CALIBRATION_RUN_APPROVAL_MISSING", "/calibration_run_approval_binding")
            )
    else:
        approval_contents, approval_binding_error = _check_binding(
            profile.calibration_run_approval_binding,
            root,
        )
        if approval_binding_error is not None:
            invalid.append(approval_binding_error)
        elif approval_contents is not None:
            try:
                calibration_approval = CalibrationRunApprovalArtifact.model_validate(
                    _load_json_bytes(approval_contents)
                )
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                KeyError,
                OverflowError,
                RecursionError,
                TypeError,
                ValueError,
                ValidationError,
            ):
                invalid.append(
                    _reason(
                        "CALIBRATION_RUN_APPROVAL_INVALID",
                        f"/{profile.calibration_run_approval_binding.path}",
                    )
                )
            else:
                run_invalid, run_blocked = _calibration_run_approval_reasons(
                    calibration_approval,
                    profile.calibration_run_approval_binding,
                    profile,
                    policy,
                    current=current,
                    evidence_artifacts=evidence_artifacts,
                )
                invalid.extend(run_invalid)
                blocked.extend(run_blocked)

    if identity.status != "resolved":
        blocked.append(_reason("IDENTITY_UNRESOLVED", "/profile_payload/device_identity/status"))
    elif any(
        value is None
        for value in (
            identity.model,
            identity.hardware_revision,
            identity.firmware_version,
            identity.point_map_version,
            identity.usb_serial_number,
        )
    ):
        blocked.append(_reason("IDENTITY_BINDING_INCOMPLETE", "/profile_payload/device_identity"))
    identity_artifacts = [
        evidence_artifacts[ref]
        for ref in identity.evidence_refs
        if ref in evidence_artifacts and evidence_artifacts[ref].role == "identity"
    ]
    map_artifacts = [
        evidence_artifacts[ref]
        for ref in identity.evidence_refs
        if ref in evidence_artifacts and evidence_artifacts[ref].role == "authoritative_map"
    ]
    if identity.status == "resolved" and (not identity_artifacts or not map_artifacts):
        blocked.append(_reason("IDENTITY_EVIDENCE_INCOMPLETE", "/profile_payload/device_identity"))
    if identity.status == "resolved" and identity_artifacts:
        expected_identity = (
            identity.model,
            identity.hardware_revision,
            identity.firmware_version,
            identity.point_map_version,
            identity.usb_serial_number,
        )
        if not any(
            isinstance(item.content, IdentityEvidenceContent)
            and (
                item.content.model,
                item.content.hardware_revision,
                item.content.firmware_version,
                item.content.point_map_version,
                item.content.usb_serial_number,
            )
            == expected_identity
            for item in identity_artifacts
        ):
            blocked.append(
                _reason("IDENTITY_EVIDENCE_MISMATCH", "/profile_payload/device_identity")
            )
    if (
        identity.status == "resolved"
        and map_artifacts
        and not any(
            isinstance(item.content, AuthoritativeMapEvidenceContent)
            and item.content.device_model == identity.model
            and item.content.hardware_revision == identity.hardware_revision
            and item.content.firmware_version == identity.firmware_version
            and item.content.point_map_version == identity.point_map_version
            and item.content.device_identity_sha256 == identity_digest
            and item.content.device_serial == identity.usb_serial_number
            for item in map_artifacts
        )
    ):
        blocked.append(_reason("IDENTITY_MAP_MISMATCH", "/profile_payload/device_identity"))

    line_protocol = profile.profile_payload.line_protocol
    if line_protocol.status != "resolved":
        blocked.append(_reason("LINE_PROTOCOL_UNRESOLVED", "/profile_payload/line_protocol/status"))
    elif any(
        value is None
        for value in (
            line_protocol.stable_device_path,
            line_protocol.unit_id,
            line_protocol.baud_rate,
            line_protocol.data_bits,
            line_protocol.parity,
            line_protocol.stop_bits,
            line_protocol.stable_device_path,
        )
    ):
        blocked.append(
            _reason("LINE_PROTOCOL_BINDING_INCOMPLETE", "/profile_payload/line_protocol")
        )
    if line_protocol.status == "resolved":
        line_invalid, line_blocked = _line_protocol_evidence_reasons(
            line_protocol,
            identity_digest,
            identity.usb_serial_number,
            evidence_artifacts,
            "/profile_payload/line_protocol",
        )
        invalid.extend(line_invalid)
        blocked.extend(line_blocked)

    point_scoped_roles = {
        "authoritative_map",
        "raw_observation",
        "calibration",
        "reference",
        "contradiction_resolution",
    }
    for index, point in enumerate(profile.profile_payload.points):
        pointer = f"/profile_payload/points/{index}"
        for ref in point.evidence_refs:
            referenced_binding = evidence_by_id.get(ref)
            if (
                referenced_binding is not None
                and referenced_binding.role in point_scoped_roles
                and point.point_id not in referenced_binding.subject_point_ids
            ):
                invalid.append(_reason("EVIDENCE_SUBJECT_MISMATCH", f"{pointer}/evidence_refs"))
        for field in ("identity_status", "semantic_status", "encoding_status", "unit_status"):
            if getattr(point, field) != "resolved":
                blocked.append(
                    _reason(
                        field.upper().replace("_STATUS", "") + "_UNRESOLVED",
                        f"{pointer}/{field}",
                    )
                )
        if point.calibration_status != "resolved":
            blocked.append(_reason("CALIBRATION_UNRESOLVED", f"{pointer}/calibration_status"))
        if point.implementation_status != "supported":
            blocked.append(
                _reason("IMPLEMENTATION_UNSUPPORTED", f"{pointer}/implementation_status")
            )
        if point.encoding_status == "resolved" and (
            point.encoding.value_type == "unknown"
            or point.encoding.byte_order == "unknown"
            or point.encoding.word_order == "unknown"
            or point.encoding.raw_domain is None
            or point.register_width is None
        ):
            blocked.append(_reason("ENCODING_BINDING_INCOMPLETE", f"{pointer}/encoding"))
        if point.unit_status == "resolved" and point.unit is None:
            blocked.append(_reason("UNIT_BINDING_INCOMPLETE", f"{pointer}/unit"))

        scoped_roles = {
            evidence_artifacts[ref].role
            for ref in point.evidence_refs
            if ref in evidence_artifacts
            and (
                evidence_artifacts[ref].role not in point_scoped_roles
                or point.point_id in evidence_artifacts[ref].subject_point_ids
            )
        }
        if point.identity_status == "resolved" and "authoritative_map" not in scoped_roles:
            blocked.append(
                _reason("POINT_IDENTITY_EVIDENCE_INCOMPLETE", f"{pointer}/evidence_refs")
            )
        if point.semantic_status == "resolved" and "authoritative_map" not in scoped_roles:
            blocked.append(_reason("SEMANTIC_EVIDENCE_INCOMPLETE", f"{pointer}/evidence_refs"))
        if point.encoding_status == "resolved" and "authoritative_map" not in scoped_roles:
            blocked.append(_reason("ENCODING_EVIDENCE_INCOMPLETE", f"{pointer}/evidence_refs"))
        if point.unit_status == "resolved" and "authoritative_map" not in scoped_roles:
            blocked.append(_reason("UNIT_EVIDENCE_INCOMPLETE", f"{pointer}/evidence_refs"))

        point_maps: list[AuthoritativeMapEvidenceContent] = []
        for ref in point.evidence_refs:
            artifact = evidence_artifacts.get(ref)
            if (
                artifact is not None
                and isinstance(artifact.content, AuthoritativeMapEvidenceContent)
                and point.point_id in artifact.subject_point_ids
            ):
                point_maps.append(artifact.content)
        expected_map = (
            point.point_name,
            point.unit,
            point.function_code,
            point.start_address,
            point.register_width,
            point.bit,
            point.encoding.value_type,
            point.encoding.byte_order,
            point.encoding.word_order,
        )
        if point.encoding_status == "resolved" and not any(
            (
                mapped.point_name,
                mapped.unit,
                mapped.function_code,
                mapped.start_address,
                mapped.register_width,
                mapped.bit,
                mapped.value_type,
                mapped.byte_order,
                mapped.word_order,
            )
            == expected_map
            for content in point_maps
            for mapped in content.points
            if mapped.point_id == point.point_id
        ):
            blocked.append(_reason("AUTHORITATIVE_MAP_MISMATCH", f"{pointer}/encoding"))

        calibration = point.calibration_profile
        if point.calibration_status == "resolved":
            if isinstance(calibration, UnknownCalibrationProfile):
                blocked.append(
                    _reason(
                        "CALIBRATION_PROFILE_KIND_UNRESOLVED",
                        f"{pointer}/calibration_profile/kind",
                    )
                )
            elif (
                isinstance(calibration, AnalogCalibrationProfile)
                and calibration.engineering_mapping is None
            ):
                blocked.append(_reason("ANALOG_MAPPING_MISSING", f"{pointer}/calibration_profile"))
            elif isinstance(calibration, BinaryCalibrationProfile) and (
                calibration.inactive_raw is None or calibration.active_raw is None
            ):
                blocked.append(
                    _reason("BINARY_STATES_INCOMPLETE", f"{pointer}/calibration_profile")
                )
            elif isinstance(calibration, CounterCalibrationProfile) and (
                calibration.counts_per_unit is None
                or calibration.modulus is None
                or calibration.rollover_behavior == "unknown"
            ):
                blocked.append(
                    _reason("COUNTER_PROFILE_INCOMPLETE", f"{pointer}/calibration_profile")
                )
            else:
                point_plan = (
                    None
                    if calibration_approval is None
                    else next(
                        (
                            plan
                            for plan in calibration_approval.plans
                            if plan.point_id == point.point_id
                        ),
                        None,
                    )
                )
                blocked.extend(
                    _point_calibration_reasons(
                        point,
                        evidence_artifacts,
                        pointer,
                        point_plan=point_plan,
                    )
                )

    for index, contradiction in enumerate(profile.contradictions):
        pointer = f"/contradictions/{index}"
        unknown_points = set(contradiction.subject_point_ids) - point_ids
        if unknown_points:
            invalid.append(_reason("CONTRADICTION_POINT_UNKNOWN", pointer))
        if contradiction.status == "open":
            blocked.append(_reason("OPEN_CONTRADICTION", pointer))
            continue
        for ref in contradiction.resolution_evidence_refs:
            resolution_binding = evidence_by_id.get(ref)
            artifact = evidence_artifacts.get(ref)
            if (
                resolution_binding is None
                or artifact is None
                or resolution_binding.role != "contradiction_resolution"
                or set(resolution_binding.subject_point_ids) != set(contradiction.subject_point_ids)
                or not isinstance(artifact.content, ContradictionResolutionEvidenceContent)
                or contradiction.contradiction_id not in artifact.content.contradiction_ids
            ):
                invalid.append(_reason("CONTRADICTION_RESOLUTION_EVIDENCE_INVALID", pointer))

    required_checks = set(BASE_RUNTIME_CHECKS)
    if any(point.encoding.value_type in {"s16", "s32"} for point in profile.profile_payload.points):
        required_checks.add("SIGNED_DECODE_BOUNDARIES")
    if any(point.function_code == 1 for point in profile.profile_payload.points):
        required_checks.add("FC1_ADDRESS_TRANSLATION")
    if any(
        point.function_code == MODBUS_FC_DISCRETE_INPUTS for point in profile.profile_payload.points
    ):
        required_checks.add("FC2_ADDRESS_TRANSLATION")
    bindings_by_check = {binding.check_id: binding for binding in profile.runtime_evidence}
    for check_id in sorted(required_checks - set(bindings_by_check)):
        blocked.append(_reason("RUNTIME_EVIDENCE_MISSING", f"/runtime_evidence/{check_id}"))

    release_receipt: ReleaseVerificationReceipt | None = None
    if profile.runtime_target is None:
        blocked.append(_reason("RUNTIME_TARGET_MISSING", "/runtime_target"))
    else:
        receipt_contents, receipt_binding_error = _check_binding(
            profile.runtime_target.release_verification_receipt,
            root,
        )
        if receipt_binding_error is not None:
            invalid.append(receipt_binding_error)
        elif receipt_contents is None:
            invalid.append(
                _reason("ARTIFACT_PATH_INVALID", "/runtime_target/release_verification_receipt")
            )
        else:
            release_receipt, receipt_error = _verify_release_receipt(
                profile.runtime_target,
                receipt_contents,
                policy,
            )
            if receipt_error is not None:
                invalid.append(receipt_error)
            elif (
                release_receipt is not None
                and datetime.fromisoformat(release_receipt.verified_at) > current
            ):
                blocked.append(
                    _reason(
                        "RELEASE_RECEIPT_OBSERVED_IN_FUTURE",
                        "/runtime_target/release_verification_receipt",
                    )
                )
    runtime_artifacts: dict[str, RuntimeEvidenceArtifact] = {}
    for check_id, runtime_binding in bindings_by_check.items():
        contents, binding_error = _check_binding(runtime_binding, root)
        if binding_error is not None:
            invalid.append(binding_error)
            continue
        if contents is None:
            invalid.append(_reason("ARTIFACT_PATH_INVALID", f"/runtime_evidence/{check_id}"))
            continue
        runtime, runtime_error = _load_runtime_artifact(
            runtime_binding,
            contents,
            root=root,
            target=profile.runtime_target,
            policy=policy,
            release_receipt=release_receipt,
            additional_artifact_budget=additional_artifact_budget,
        )
        if runtime_error is not None:
            invalid.append(runtime_error)
            continue
        if runtime is None:
            invalid.append(_reason("RUNTIME_ARTIFACT_INVALID", f"/{runtime_binding.path}"))
            continue
        expected_run_approval_sha = (
            None
            if profile.calibration_run_approval_binding is None
            else profile.calibration_run_approval_binding.sha256
        )
        expected_release_receipt_sha = (
            None
            if profile.runtime_target is None
            else profile.runtime_target.release_verification_receipt.sha256
        )
        if (
            runtime.profile_id != profile.profile_id
            or runtime.profile_payload_sha256 != profile.payload_sha256
            or runtime.calibration_run_approval_sha256 != expected_run_approval_sha
            or runtime.release_verification_receipt_sha256 != expected_release_receipt_sha
        ):
            invalid.append(
                _reason("RUNTIME_PROFILE_BINDING_MISMATCH", f"/runtime_evidence/{check_id}")
            )
            continue
        runtime_artifacts[check_id] = runtime
        if runtime.result != "PASS":
            blocked.append(_reason("RUNTIME_CHECK_FAILED", f"/runtime_evidence/{check_id}"))
        if datetime.fromisoformat(runtime.observed_at) > current:
            blocked.append(_reason("RUNTIME_OBSERVED_IN_FUTURE", f"/runtime_evidence/{check_id}"))

    if profile.approval_binding is None:
        blocked.append(_reason("APPROVAL_MISSING", "/approval_binding"))
    else:
        gate_digest = canonical_gate_sha256(profile)
        if profile.approval_binding.subject_gate_sha256 != gate_digest:
            blocked.append(_reason("APPROVAL_GATE_MISMATCH", "/approval_binding"))
        approval_contents, binding_error = _check_binding(profile.approval_binding, root)
        if binding_error is not None:
            invalid.append(binding_error)
        elif approval_contents is None:
            invalid.append(_reason("ARTIFACT_PATH_INVALID", "/approval_binding"))
        else:
            try:
                approval = EligibilityApprovalArtifact.model_validate(
                    _load_json_bytes(approval_contents)
                )
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                KeyError,
                OverflowError,
                RecursionError,
                TypeError,
                ValueError,
                ValidationError,
            ):
                invalid.append(
                    _reason("APPROVAL_ARTIFACT_INVALID", f"/{profile.approval_binding.path}")
                )
            else:
                approval_invalid, approval_blocked = _approval_reasons(
                    approval,
                    profile,
                    policy,
                    current=current,
                    evidence_artifacts=evidence_artifacts,
                    runtime_artifacts=runtime_artifacts,
                    release_receipt=release_receipt,
                )
                invalid.extend(approval_invalid)
                blocked.extend(approval_blocked)

    if invalid:
        return _report(
            "INVALID",
            invalid + blocked,
            profile_id=profile.profile_id,
            payload_sha256=profile.payload_sha256,
        )
    if blocked:
        return _report(
            "BLOCKED",
            blocked,
            profile_id=profile.profile_id,
            payload_sha256=profile.payload_sha256,
        )
    return _report(
        "ELIGIBLE",
        (),
        profile_id=profile.profile_id,
        payload_sha256=profile.payload_sha256,
    )


def validate_profile_data(
    value: Any,
    *,
    root: Path,
    now: datetime | None = None,
    trust_policy: TrustPolicy | Mapping[str, Any] | None = None,
) -> EligibilityReport:
    """Public library API; it cannot establish a protected policy trust root."""
    report = _validate_profile_data_with_trusted_context(
        value,
        root=root,
        now=now,
        trust_policy=trust_policy,
        trust_root=None,
    )
    if any(reason.code == "TRUST_ROOT_MISSING" for reason in report.reasons):
        return _report(
            "BLOCKED",
            [_reason("FRESHNESS_CONTEXT_REQUIRED", "/freshness")],
            profile_id=report.profile_id,
            payload_sha256=report.payload_sha256,
        )
    return report


def _validate_profile_file_with_trusted_context(
    profile_path: Path,
    *,
    root: Path,
    now: datetime | None = None,
    trust_policy: TrustPolicy | Mapping[str, Any] | None = None,
    trust_root: PolicyTrustRoot | None = None,
) -> EligibilityReport:
    try:
        value = _load_json_bytes(
            _read_explicit_file_once(profile_path, maximum_bytes=MAX_PROFILE_BYTES)
        )
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        return _report("INVALID", [_reason("PROFILE_JSON_INVALID", "/")])
    return _validate_profile_data_with_trusted_context(
        value,
        root=root,
        now=now,
        trust_policy=trust_policy,
        trust_root=trust_root,
    )


def validate_profile_file(
    profile_path: Path,
    *,
    root: Path,
    now: datetime | None = None,
    trust_policy: TrustPolicy | Mapping[str, Any] | None = None,
) -> EligibilityReport:
    """Validate a profile file without accepting caller-injected root authority."""
    report = _validate_profile_file_with_trusted_context(
        profile_path,
        root=root,
        now=now,
        trust_policy=trust_policy,
        trust_root=None,
    )
    if any(reason.code == "TRUST_ROOT_MISSING" for reason in report.reasons):
        return _report(
            "BLOCKED",
            [_reason("FRESHNESS_CONTEXT_REQUIRED", "/freshness")],
            profile_id=report.profile_id,
            payload_sha256=report.payload_sha256,
        )
    return report


def validate_legacy_evidence_file(
    evidence_path: Path,
    *,
    root: Path,
    now: datetime | None = None,
) -> EligibilityReport:
    try:
        root_resolved = root.resolve(strict=True)
        evidence_absolute = evidence_path.absolute()
        relative = evidence_absolute.relative_to(root_resolved).as_posix()
        contents = _read_relative_file_once(
            root_resolved,
            relative,
            maximum_bytes=MAX_ARTIFACT_BYTES,
        )
        legacy = _load_json_bytes(contents)
        if not isinstance(legacy, dict):
            raise ValueError("legacy evidence must be an object")
        profile = candidate_profile_from_legacy_evidence(
            legacy,
            evidence_path=relative,
            evidence_sha256="sha256:" + hashlib.sha256(contents).hexdigest(),
            evidence_size_bytes=len(contents),
        )
    except (
        KeyError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
        ValidationError,
    ):
        return _report("INVALID", [_reason("LEGACY_EVIDENCE_INVALID", "/")])
    return validate_profile_data(profile, root=root_resolved, now=now)


def candidate_profile_from_legacy_evidence(
    legacy: Mapping[str, Any],
    *,
    evidence_path: str,
    evidence_sha256: str,
    evidence_size_bytes: int,
) -> dict[str, Any]:
    """Map legacy evidence to a valid, explicitly blocked candidate profile."""

    def legacy_integer(candidate: Mapping[str, Any], field: str) -> int:
        value = candidate.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"legacy {field} must be an integer")
        return value

    candidates = legacy.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("legacy evidence has no candidates")
    evidence_id = "LEGACY_POINT_CANDIDATES"
    points: list[dict[str, Any]] = []
    models: set[str] = set()
    fc1_points: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("legacy candidate must be an object")
        point_id = str(candidate["candidate_id"])
        function_code = legacy_integer(candidate, "fun_code")
        model_value = candidate.get("model_candidate")
        if not isinstance(model_value, str):
            raise ValueError("legacy model_candidate must be text")
        model = _validate_nonblank(model_value)
        models.add(model)
        if function_code in (1, 2):
            fc1_points.append(point_id)
        legacy_type = candidate.get("legacy_value_type")
        value_type = (
            legacy_type
            if legacy_type in {"bit", "u16", "s16", "u32", "s32", "float32", "bcd"}
            else "unknown"
        )
        if function_code in (1, 2):
            value_type = "unknown"
        unit = candidate.get("unit_original")
        points.append(
            {
                "point_id": point_id,
                "point_name": str(
                    candidate.get("user_point_name") or candidate.get("point_name") or point_id
                ),
                "function_code": function_code,
                "start_address": legacy_integer(candidate, "point_number"),
                "register_width": None if function_code in (1, 2) else 1,
                "bit": None,
                "identity_status": "candidate",
                "semantic_status": "candidate",
                "encoding_status": "candidate",
                "unit_status": "candidate" if unit is not None else "unknown",
                "calibration_status": "unknown",
                "implementation_status": (
                    candidate.get("implementation_status")
                    if candidate.get("implementation_status")
                    in {"unknown", "unsupported", "supported"}
                    else "unknown"
                ),
                "encoding": {
                    "value_type": value_type,
                    "byte_order": "unknown",
                    "word_order": "unknown",
                    "raw_domain": None,
                },
                "unit": unit,
                "calibration_profile": {"kind": "unknown", "method": "unknown"},
                "evidence_refs": [evidence_id],
            }
        )
    payload = {
        "device_identity": {
            "status": "ambiguous" if len(models) > 1 else "candidate",
            "model": next(iter(models)) if len(models) == 1 else None,
            "hardware_revision": None,
            "firmware_version": None,
            "point_map_version": None,
            "usb_serial_number": None,
            "evidence_refs": [evidence_id],
        },
        "line_protocol": {
            "status": "unknown",
            "stable_device_path": None,
            "unit_id": None,
            "baud_rate": None,
            "data_bits": None,
            "parity": None,
            "stop_bits": None,
            "evidence_refs": [],
        },
        "points": points,
    }
    contradictions: list[dict[str, Any]] = [
        {
            "contradiction_id": "MODEL_IDENTITY_CONFLICT",
            "status": "open",
            "summary": "Legacy evidence contains competing model candidates.",
            "subject_point_ids": [],
            "resolution": None,
            "resolution_evidence_refs": [],
        },
        {
            "contradiction_id": "LEGACY_SCALING_FORMULA_CONFLICT",
            "status": "open",
            "summary": "Legacy execution paths use incompatible scaling formulae.",
            "subject_point_ids": [],
            "resolution": None,
            "resolution_evidence_refs": [],
        },
    ]
    if fc1_points:
        contradictions.append(
            {
                "contradiction_id": "FC1_RBIT_CONTRACT_CONFLICT",
                "status": "open",
                "summary": "Legacy FC1 PointNumber/RBit fields conflict with current coil semantics.",
                "subject_point_ids": fc1_points,
                "resolution": None,
                "resolution_evidence_refs": [],
            }
        )
    result = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": ARTIFACT_TYPE,
        "profile_id": f"candidate-{legacy.get('artifact_id', 'legacy-evidence')}",
        "created_at": f"{legacy.get('generated_on', '1970-01-01')}T00:00:00+00:00",
        "semantic_validator": SEMANTIC_VALIDATOR_ID,
        "validator_source_sha256": current_validator_source_sha256(),
        "policy_sha256": None,
        "trust_root_sha256": None,
        "schema_sha256": current_schema_sha256(),
        "profile_payload": payload,
        "payload_sha256": canonical_payload_sha256(payload),
        "evidence_bindings": [
            {
                "evidence_id": evidence_id,
                "role": "legacy_source",
                "path": evidence_path,
                "sha256": evidence_sha256,
                "size_bytes": evidence_size_bytes,
                "media_type": "application/json",
                "subject_point_ids": [point["point_id"] for point in points],
            }
        ],
        "calibration_run_approval_binding": None,
        "approval_binding": None,
        "runtime_target": None,
        "runtime_evidence": [],
        "contradictions": contradictions,
    }
    DevicePointProfile.model_validate(result)
    return result


def schema_document() -> dict[str, Any]:
    schema = DevicePointProfile.model_json_schema(
        mode="validation",
        ref_template="#/$defs/{model}",
    ) | {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://ruisheng.local/schemas/point-profile/point-profile-v1.schema.json",
        "x-semantic-validator": SEMANTIC_VALIDATOR_ID,
        "x-semantic-validation-required": True,
        "x-semantic-validation-contract": (
            "JSON Schema is structural only; eligibility requires the referenced semantic validator, "
            "an external trust policy, nofollow single-read artifact verification, and policy-bound signatures."
        ),
    }
    return schema


def render_schema_document() -> str:
    return json.dumps(schema_document(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def current_schema_sha256() -> str:
    encoded = render_schema_document().encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="validate one profile without side effects")
    validate.add_argument("profile", type=Path)
    validate.add_argument("--root", type=Path, default=Path.cwd())
    validate.add_argument(
        "--trust-policy",
        type=Path,
        required=True,
        help="external trust policy; never read from the profile",
    )
    validate_legacy = subparsers.add_parser(
        "validate-legacy",
        help="map legacy candidate evidence in memory and report its profile eligibility",
    )
    validate_legacy.add_argument("evidence", type=Path)
    validate_legacy.add_argument("--root", type=Path, default=Path.cwd())
    subparsers.add_parser("schema", help="print the authoritative JSON Schema")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.command == "schema":
        print(render_schema_document(), end="")
        return 0
    if args.command == "validate-legacy":
        report = validate_legacy_evidence_file(args.evidence, root=args.root)
    else:
        report = _report(
            "BLOCKED",
            [_reason("FRESHNESS_CONTEXT_REQUIRED", "/freshness")],
        )
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
    return {"ELIGIBLE": 0, "BLOCKED": 2, "INVALID": 3}[report.decision]


if __name__ == "__main__":
    raise SystemExit(main())
