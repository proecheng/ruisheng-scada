"""Strict, read-only trust-root freshness contracts and comparison logic."""

from __future__ import annotations

import base64
import binascii
import hashlib
import importlib
import json
import os
import re
import stat
import time
from argparse import ArgumentParser
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

MAX_FRESHNESS_JSON_BYTES = 4 * 1024 * 1024
ED25519_PUBLIC_KEY_BYTES = 32
ED25519_SIGNATURE_BYTES = 64
FRESHNESS_CHALLENGE_BYTES = 32
SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
CHALLENGE_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
FRESHNESS_SIGNATURE_DOMAIN = b"ruisheng.trust-root-freshness-attestation/v1\0"

FreshnessGateDecision = Literal["EXACT", "BLOCKED", "INVALID"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _validate_identifier(value: str) -> str:
    if IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError("identifier is not canonical")
    return value


def _validate_sha256(value: str) -> str:
    if SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError("SHA-256 digest is not canonical")
    return value


def _validate_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except (OverflowError, ValueError) as error:
        raise ValueError("timestamp is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("timestamp must use an explicit UTC offset")
    if value != parsed.isoformat():
        raise ValueError("timestamp is not canonical")
    return value


def _decode_base64(value: str, *, expected_size: int, label: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(f"{label} is not canonical base64") from error
    if len(decoded) != expected_size or base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError(f"{label} has an invalid size or encoding")
    return decoded


class FreshnessState(_StrictModel):
    root_id: str = Field(min_length=1, max_length=128)
    root_version: int = Field(ge=1)
    root_revocation_sequence: int = Field(ge=0)
    root_sha256: str
    policy_id: str = Field(min_length=1, max_length=128)
    policy_version: int = Field(ge=1)
    policy_revocation_sequence: int = Field(ge=0)
    policy_sha256: str

    _root_id = field_validator("root_id")(_validate_identifier)
    _policy_id = field_validator("policy_id")(_validate_identifier)
    _root_hash = field_validator("root_sha256")(_validate_sha256)
    _policy_hash = field_validator("policy_sha256")(_validate_sha256)


class FreshnessRequest(_StrictModel):
    schema_version: Literal[1]
    artifact_type: Literal["ruisheng.trust-root-freshness-request"]
    site_id: str = Field(min_length=1, max_length=128)
    challenge: str
    requested_at: str
    candidate_logical_identity: str
    root_snapshot_sha256: str
    provider_config_sha256: str
    profile_id: str = Field(min_length=1, max_length=128)
    profile_sha256: str
    payload_sha256: str
    canonical_gate_sha256: str
    semantic_validator: Literal["ruisheng.device-point-profile-validator/v5"]
    validator_source_sha256: str
    verifier_id: str = Field(min_length=1, max_length=256)
    verifier_tool_sha256: str
    state: FreshnessState

    _site = field_validator("site_id")(_validate_identifier)
    _profile = field_validator("profile_id")(_validate_identifier)
    _verifier = field_validator("verifier_id")(_validate_identifier)
    _requested = field_validator("requested_at")(_validate_timestamp)
    _digests = field_validator(
        "candidate_logical_identity",
        "root_snapshot_sha256",
        "provider_config_sha256",
        "profile_sha256",
        "payload_sha256",
        "canonical_gate_sha256",
        "validator_source_sha256",
        "verifier_tool_sha256",
    )(_validate_sha256)

    @field_validator("challenge")
    @classmethod
    def valid_challenge(cls, value: str) -> str:
        if CHALLENGE_PATTERN.fullmatch(value) is None:
            raise ValueError("freshness challenge must encode exactly 256 random bits")
        try:
            decoded = base64.b64decode(value + "=", altchars=b"-_", validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("freshness challenge is not canonical base64url") from error
        canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
        if len(decoded) != FRESHNESS_CHALLENGE_BYTES or canonical != value:
            raise ValueError("freshness challenge is not canonical base64url")
        return value


class FreshnessSignature(_StrictModel):
    algorithm: Literal["Ed25519"]
    key_id: str = Field(min_length=1, max_length=128)
    value: str

    _key_id = field_validator("key_id")(_validate_identifier)

    @field_validator("value")
    @classmethod
    def valid_signature(cls, value: str) -> str:
        _decode_base64(
            value,
            expected_size=ED25519_SIGNATURE_BYTES,
            label="freshness signature",
        )
        return value


class FreshnessAttestation(_StrictModel):
    schema_version: Literal[1]
    artifact_type: Literal["ruisheng.trust-root-freshness-attestation"]
    provider_id: str = Field(min_length=1, max_length=128)
    witness_key_id: str = Field(min_length=1, max_length=128)
    request: FreshnessRequest
    high_water: FreshnessState
    monotonic_state_id: str = Field(min_length=1, max_length=256)
    monotonic_counter: int = Field(ge=0)
    observed_at: str
    expires_at: str
    signature: FreshnessSignature

    _provider = field_validator("provider_id")(_validate_identifier)
    _key = field_validator("witness_key_id")(_validate_identifier)
    _state_id = field_validator("monotonic_state_id")(_validate_identifier)
    _observed = field_validator("observed_at")(_validate_timestamp)
    _expires = field_validator("expires_at")(_validate_timestamp)

    @model_validator(mode="after")
    def matching_signature_key(self) -> FreshnessAttestation:
        if self.signature.key_id != self.witness_key_id:
            raise ValueError("freshness signature key does not match witness key")
        return self


class FreshnessProviderConfig(_StrictModel):
    """Fixed site configuration; transport and executable selection are intentionally absent."""

    schema_version: Literal[1]
    artifact_type: Literal["ruisheng.trust-root-freshness-provider-config"]
    site_id: str = Field(min_length=1, max_length=128)
    provider_id: str = Field(min_length=1, max_length=128)
    witness_key_id: str = Field(min_length=1, max_length=128)
    witness_public_key: str
    verifier_id: str = Field(min_length=1, max_length=256)
    verifier_tool_sha256: str
    monotonic_state_id: str = Field(min_length=1, max_length=256)
    minimum_monotonic_counter: int = Field(ge=0)
    maximum_clock_skew_seconds: int = Field(ge=0, le=300)
    maximum_attestation_lifetime_seconds: int = Field(ge=1, le=900)

    _site = field_validator("site_id")(_validate_identifier)
    _provider = field_validator("provider_id")(_validate_identifier)
    _key = field_validator("witness_key_id")(_validate_identifier)
    _verifier = field_validator("verifier_id")(_validate_identifier)
    _state_id = field_validator("monotonic_state_id")(_validate_identifier)
    _verifier_hash = field_validator("verifier_tool_sha256")(_validate_sha256)

    @field_validator("witness_public_key")
    @classmethod
    def valid_public_key(cls, value: str) -> str:
        _decode_base64(
            value,
            expected_size=ED25519_PUBLIC_KEY_BYTES,
            label="freshness witness public key",
        )
        return value


class FreshnessComparison(_StrictModel):
    decision: FreshnessGateDecision
    reason_code: str


class FreshnessValidationError(ValueError):
    def __init__(self, decision: Literal["BLOCKED", "INVALID"], reason_code: str) -> None:
        super().__init__(reason_code)
        self.decision = decision
        self.reason_code = reason_code


def compare_freshness_states(  # noqa: PLR0911 - deterministic reason precedence
    local: FreshnessState,
    high_water: FreshnessState,
) -> FreshnessComparison:
    """Compare without side effects; this function never advances the external high-water mark."""

    if local.root_id != high_water.root_id:
        return FreshnessComparison(decision="INVALID", reason_code="FRESHNESS_ROOT_ID_SWITCH")
    if local.policy_id != high_water.policy_id:
        return FreshnessComparison(decision="INVALID", reason_code="FRESHNESS_POLICY_ID_SWITCH")
    if local.root_version < high_water.root_version:
        return FreshnessComparison(
            decision="INVALID", reason_code="FRESHNESS_ROOT_VERSION_ROLLBACK"
        )
    if local.root_revocation_sequence < high_water.root_revocation_sequence:
        return FreshnessComparison(
            decision="INVALID", reason_code="FRESHNESS_ROOT_REVOCATION_ROLLBACK"
        )
    if local.policy_version < high_water.policy_version:
        return FreshnessComparison(
            decision="INVALID", reason_code="FRESHNESS_POLICY_VERSION_ROLLBACK"
        )
    if local.policy_revocation_sequence < high_water.policy_revocation_sequence:
        return FreshnessComparison(
            decision="INVALID", reason_code="FRESHNESS_POLICY_REVOCATION_ROLLBACK"
        )
    if (
        local.root_version == high_water.root_version
        and local.root_sha256 != high_water.root_sha256
    ):
        return FreshnessComparison(decision="INVALID", reason_code="FRESHNESS_ROOT_HASH_CONFLICT")
    if (
        local.policy_version == high_water.policy_version
        and local.policy_sha256 != high_water.policy_sha256
    ):
        return FreshnessComparison(decision="INVALID", reason_code="FRESHNESS_POLICY_HASH_CONFLICT")
    if local != high_water:
        return FreshnessComparison(decision="BLOCKED", reason_code="FRESHNESS_LOCAL_STATE_AHEAD")
    return FreshnessComparison(decision="EXACT", reason_code="FRESHNESS_EXACT")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def freshness_attestation_signature_message(
    value: FreshnessAttestation | Mapping[str, Any],
) -> bytes:
    document = (
        value.model_dump(mode="json") if isinstance(value, FreshnessAttestation) else dict(value)
    )
    document.pop("signature", None)
    return FRESHNESS_SIGNATURE_DOMAIN + _canonical_json_bytes(document)


def freshness_request_sha256(value: FreshnessRequest) -> str:
    return (
        "sha256:" + hashlib.sha256(_canonical_json_bytes(value.model_dump(mode="json"))).hexdigest()
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def parse_freshness_attestation(contents: bytes) -> FreshnessAttestation:
    if len(contents) > MAX_FRESHNESS_JSON_BYTES:
        raise FreshnessValidationError("INVALID", "FRESHNESS_ATTESTATION_TOO_LARGE")
    try:
        value = json.loads(
            contents.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value: {value}")
            ),
        )
        return FreshnessAttestation.model_validate(value)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
        ValidationError,
    ) as error:
        if isinstance(error, FreshnessValidationError):
            raise
        raise FreshnessValidationError("INVALID", "FRESHNESS_ATTESTATION_INVALID") from error


def parse_freshness_provider_config(contents: bytes) -> FreshnessProviderConfig:
    if len(contents) > MAX_FRESHNESS_JSON_BYTES:
        raise FreshnessValidationError("INVALID", "FRESHNESS_CONFIG_TOO_LARGE")
    try:
        value = json.loads(
            contents.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value: {value}")
            ),
        )
        return FreshnessProviderConfig.model_validate(value)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
        ValidationError,
    ) as error:
        raise FreshnessValidationError("INVALID", "FRESHNESS_CONFIG_INVALID") from error


def validate_live_freshness_attestation(
    attestation: FreshnessAttestation,
    *,
    expected_request: FreshnessRequest,
    provider_config: FreshnessProviderConfig,
    now: datetime,
) -> FreshnessAttestation:
    if now.tzinfo is None or now.utcoffset() is None:
        raise FreshnessValidationError("INVALID", "FRESHNESS_CURRENT_TIME_INVALID")
    try:
        current = now.astimezone(UTC)
    except (OverflowError, ValueError) as error:
        raise FreshnessValidationError("INVALID", "FRESHNESS_CURRENT_TIME_INVALID") from error
    if (
        provider_config.site_id != expected_request.site_id
        or attestation.provider_id != provider_config.provider_id
        or attestation.witness_key_id != provider_config.witness_key_id
    ):
        raise FreshnessValidationError("INVALID", "FRESHNESS_PROVIDER_MISMATCH")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            _decode_base64(
                provider_config.witness_public_key,
                expected_size=ED25519_PUBLIC_KEY_BYTES,
                label="freshness witness public key",
            )
        )
        signature = _decode_base64(
            attestation.signature.value,
            expected_size=ED25519_SIGNATURE_BYTES,
            label="freshness signature",
        )
        public_key.verify(signature, freshness_attestation_signature_message(attestation))
    except (InvalidSignature, ValueError) as error:
        raise FreshnessValidationError("INVALID", "FRESHNESS_SIGNATURE_INVALID") from error
    if attestation.request != expected_request:
        raise FreshnessValidationError("INVALID", "FRESHNESS_REQUEST_MISMATCH")
    if attestation.monotonic_state_id != provider_config.monotonic_state_id:
        raise FreshnessValidationError("INVALID", "FRESHNESS_MONOTONIC_STATE_MISMATCH")
    if attestation.monotonic_counter < provider_config.minimum_monotonic_counter:
        raise FreshnessValidationError("INVALID", "FRESHNESS_MONOTONIC_COUNTER_ROLLBACK")

    try:
        requested = datetime.fromisoformat(expected_request.requested_at)
        observed = datetime.fromisoformat(attestation.observed_at)
        expires = datetime.fromisoformat(attestation.expires_at)
        skew = timedelta(seconds=provider_config.maximum_clock_skew_seconds)
        maximum_lifetime = timedelta(seconds=provider_config.maximum_attestation_lifetime_seconds)
        invalid_time = (
            expires <= observed
            or expires - observed > maximum_lifetime
            or abs(observed - requested) > skew
            or current < observed - skew
            or current >= expires
        )
    except (OverflowError, ValueError) as error:
        raise FreshnessValidationError("INVALID", "FRESHNESS_TIMESTAMP_INVALID") from error
    if invalid_time:
        raise FreshnessValidationError("INVALID", "FRESHNESS_CLOCK_SKEW")

    comparison = compare_freshness_states(expected_request.state, attestation.high_water)
    if comparison.decision == "BLOCKED":
        raise FreshnessValidationError("BLOCKED", comparison.reason_code)
    if comparison.decision == "INVALID":
        raise FreshnessValidationError("INVALID", comparison.reason_code)
    return attestation


def _read_preflight_file(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        before = os.lstat(path)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or (os.name != "nt" and before.st_nlink != 1)
            or before.st_size > MAX_FRESHNESS_JSON_BYTES
        ):
            raise ValueError("preflight input is not a regular file")
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not os.path.samestat(before, opened):
                raise ValueError("preflight input identity changed")
            chunks: list[bytes] = []
            remaining = MAX_FRESHNESS_JSON_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            contents = b"".join(chunks)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        path_after = os.lstat(path)
    except FileNotFoundError as error:
        raise FreshnessValidationError(
            "BLOCKED", "FRESHNESS_PREFLIGHT_INPUT_UNAVAILABLE"
        ) from error
    except (OSError, ValueError) as error:
        raise FreshnessValidationError("INVALID", "FRESHNESS_PREFLIGHT_INPUT_INVALID") from error
    if (
        len(contents) > MAX_FRESHNESS_JSON_BYTES
        or not stat.S_ISREG(opened.st_mode)
        or (os.name != "nt" and opened.st_nlink != 1)
        or not os.path.samestat(opened, after)
        or not os.path.samestat(after, path_after)
        or opened.st_size != len(contents)
    ):
        raise FreshnessValidationError("INVALID", "FRESHNESS_PREFLIGHT_INPUT_INVALID")
    return contents


@dataclass(frozen=True)
class _ValidatedFreshnessInputs:
    validator: Any
    profile_value: dict[str, Any]
    profile: Any
    policy: Any
    trust_root: Any
    provider_config: FreshnessProviderConfig
    attestation: FreshnessAttestation


def _raw_sha256(contents: bytes) -> str:
    return "sha256:" + hashlib.sha256(contents).hexdigest()


def _read_snapshot(path: Path, *, missing_reason_code: str) -> bytes:
    try:
        return _read_preflight_file(path)
    except FreshnessValidationError as error:
        if error.reason_code == "FRESHNESS_PREFLIGHT_INPUT_UNAVAILABLE":
            raise FreshnessValidationError("BLOCKED", missing_reason_code) from error
        raise


def _authenticate_snapshots(  # noqa: PLR0913
    *,
    profile_path: Path,
    policy_path: Path,
    trust_root_path: Path,
    provider_config_path: Path,
    attestation_path: Path,
    challenge: str,
    requested_at: str,
    candidate_logical_identity: str,
    expected_trust_root_snapshot_sha256: str,
    expected_provider_config_snapshot_sha256: str,
    expected_attestation_sha256: str,
    now: datetime | None = None,
) -> _ValidatedFreshnessInputs:
    """Read, parse, and authenticate every publisher snapshot exactly once."""

    validator = importlib.import_module("tools.validate_device_point_profile")
    profile_contents = _read_preflight_file(profile_path)
    policy_contents = _read_preflight_file(policy_path)
    trust_root_contents = _read_snapshot(
        trust_root_path, missing_reason_code="FRESHNESS_TRUST_ROOT_MISSING"
    )
    config_contents = _read_snapshot(
        provider_config_path, missing_reason_code="FRESHNESS_PROVIDER_CONFIG_MISSING"
    )
    attestation_contents = _read_snapshot(
        attestation_path, missing_reason_code="FRESHNESS_ATTESTATION_MISSING"
    )
    expected_digests = (
        (
            trust_root_contents,
            expected_trust_root_snapshot_sha256,
            "FRESHNESS_TRUST_ROOT_SNAPSHOT_MISMATCH",
        ),
        (
            config_contents,
            expected_provider_config_snapshot_sha256,
            "FRESHNESS_PROVIDER_CONFIG_SNAPSHOT_MISMATCH",
        ),
        (
            attestation_contents,
            expected_attestation_sha256,
            "FRESHNESS_ATTESTATION_SNAPSHOT_MISMATCH",
        ),
    )
    for contents, expected, reason_code in expected_digests:
        if _raw_sha256(contents) != expected:
            raise FreshnessValidationError("INVALID", reason_code)
    try:
        fixed_config_contents = validator._read_fixed_trust_root_once(
            validator.FIXED_FRESHNESS_PROVIDER_CONFIG_PATH,
            maximum_bytes=MAX_FRESHNESS_JSON_BYTES,
        )
    except FileNotFoundError as error:
        raise FreshnessValidationError("BLOCKED", "FRESHNESS_PROVIDER_CONFIG_MISSING") from error
    except (OSError, ValueError) as error:
        raise FreshnessValidationError("INVALID", "FRESHNESS_CONFIG_INVALID") from error
    if fixed_config_contents != config_contents:
        raise FreshnessValidationError("INVALID", "FRESHNESS_PROVIDER_CONFIG_SNAPSHOT_MISMATCH")
    profile_value = validator._load_json_bytes(profile_contents)
    profile = validator.DevicePointProfile.model_validate(profile_value)
    policy = validator.TrustPolicy.model_validate(validator._load_json_bytes(policy_contents))
    trust_root = validator.PolicyTrustRoot.model_validate(
        validator._load_json_bytes(trust_root_contents)
    )
    provider_config = parse_freshness_provider_config(config_contents)
    attestation = parse_freshness_attestation(attestation_contents)
    expected_request = FreshnessRequest(
        schema_version=1,
        artifact_type="ruisheng.trust-root-freshness-request",
        site_id=provider_config.site_id,
        challenge=challenge,
        requested_at=requested_at,
        candidate_logical_identity=candidate_logical_identity,
        root_snapshot_sha256=_raw_sha256(trust_root_contents),
        provider_config_sha256=_raw_sha256(config_contents),
        profile_id=profile.profile_id,
        profile_sha256=validator._canonical_sha256(profile.model_dump(mode="json")),
        payload_sha256=profile.payload_sha256,
        canonical_gate_sha256=validator.canonical_gate_sha256(profile),
        semantic_validator=profile.semantic_validator,
        validator_source_sha256=validator.current_validator_source_sha256(),
        verifier_id=provider_config.verifier_id,
        verifier_tool_sha256=provider_config.verifier_tool_sha256,
        state=FreshnessState(
            root_id=trust_root.root_id,
            root_version=trust_root.root_version,
            root_revocation_sequence=trust_root.revocation_sequence,
            root_sha256=validator.trust_root_sha256(trust_root),
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            policy_revocation_sequence=policy.revocation_sequence,
            policy_sha256=validator.trust_policy_sha256(policy),
        ),
    )
    validated_attestation = validate_live_freshness_attestation(
        attestation,
        expected_request=expected_request,
        provider_config=provider_config,
        now=now or datetime.now(UTC),
    )
    return _ValidatedFreshnessInputs(
        validator=validator,
        profile_value=profile_value,
        profile=profile,
        policy=policy,
        trust_root=trust_root,
        provider_config=provider_config,
        attestation=validated_attestation,
    )


def preflight_freshness(**kwargs: Any) -> FreshnessComparison:
    """Authenticate snapshots without reading business evidence or runtime artifacts."""

    try:
        _authenticate_snapshots(**kwargs)
    except FreshnessValidationError as error:
        return FreshnessComparison(decision=error.decision, reason_code=error.reason_code)
    except (
        ImportError,
        OSError,
        OverflowError,
        RecursionError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        ValidationError,
    ):
        return FreshnessComparison(
            decision="INVALID", reason_code="FRESHNESS_PREFLIGHT_INPUT_INVALID"
        )
    return FreshnessComparison(decision="EXACT", reason_code="FRESHNESS_EXACT")


def qualify_freshness(
    *, evidence_root: Path, completion_now: datetime | None = None, **kwargs: Any
) -> Any:
    """Authenticate snapshots, then run the business gate against the in-memory inputs."""

    authentication_now = kwargs.pop("now", None) or datetime.now(UTC)
    authentication_monotonic = time.monotonic()
    try:
        validated = _authenticate_snapshots(now=authentication_now, **kwargs)
    except FreshnessValidationError as error:
        decision = "BLOCKED" if error.decision == "BLOCKED" else "INVALID"
        return _eligibility_error(
            importlib.import_module("tools.validate_device_point_profile"),
            decision,
            error.reason_code,
        )
    except (
        ImportError,
        OSError,
        OverflowError,
        RecursionError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        ValidationError,
    ):
        validator = importlib.import_module("tools.validate_device_point_profile")
        return _eligibility_error(validator, "INVALID", "FRESHNESS_PREFLIGHT_INPUT_INVALID")
    report = validated.validator._validate_profile_data_with_trusted_context(
        validated.profile_value,
        root=evidence_root,
        now=datetime.fromisoformat(validated.attestation.observed_at),
        trust_policy=validated.policy,
        trust_root=validated.trust_root,
    )
    final_now = completion_now or datetime.now(UTC)
    completion_monotonic = time.monotonic()
    try:
        if final_now.tzinfo is None or final_now.utcoffset() is None:
            raise ValueError("completion time must be timezone-aware")
        authenticated_utc = authentication_now.astimezone(UTC)
        completion_utc = final_now.astimezone(UTC)
        expires_at = datetime.fromisoformat(validated.attestation.expires_at)
        monotonic_elapsed = completion_monotonic - authentication_monotonic
        monotonic_lifetime = (expires_at - authenticated_utc).total_seconds()
    except (OverflowError, TypeError, ValueError):
        return _eligibility_error(validated.validator, "INVALID", "FRESHNESS_TIMESTAMP_INVALID")
    if completion_utc < authenticated_utc or monotonic_elapsed < 0:
        return _eligibility_error(validated.validator, "INVALID", "FRESHNESS_CLOCK_ROLLBACK")
    if completion_utc >= expires_at or monotonic_elapsed >= monotonic_lifetime:
        return _eligibility_error(validated.validator, "INVALID", "FRESHNESS_ATTESTATION_EXPIRED")
    return report


def _eligibility_error(validator: Any, decision: str, reason_code: str) -> Any:
    return validator._report(decision, [validator._reason(reason_code, "/freshness")])


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("preflight", "qualify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("profile", type=Path)
        subparser.add_argument("--trust-policy", type=Path, required=True)
        subparser.add_argument("--trust-root-snapshot", type=Path, required=True)
        subparser.add_argument("--provider-config-snapshot", type=Path, required=True)
        subparser.add_argument("--attestation", type=Path, required=True)
        subparser.add_argument("--challenge", required=True)
        subparser.add_argument("--requested-at", required=True)
        subparser.add_argument("--candidate-logical-identity", required=True)
        subparser.add_argument("--expected-trust-root-snapshot-sha256", required=True)
        subparser.add_argument("--expected-provider-config-snapshot-sha256", required=True)
        subparser.add_argument("--expected-attestation-sha256", required=True)
        if command == "qualify":
            subparser.add_argument("--evidence-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    inputs = {
        "profile_path": args.profile,
        "policy_path": args.trust_policy,
        "trust_root_path": args.trust_root_snapshot,
        "provider_config_path": args.provider_config_snapshot,
        "attestation_path": args.attestation,
        "challenge": args.challenge,
        "requested_at": args.requested_at,
        "candidate_logical_identity": args.candidate_logical_identity,
        "expected_trust_root_snapshot_sha256": args.expected_trust_root_snapshot_sha256,
        "expected_provider_config_snapshot_sha256": (args.expected_provider_config_snapshot_sha256),
        "expected_attestation_sha256": args.expected_attestation_sha256,
    }
    if args.command == "qualify":
        report = qualify_freshness(evidence_root=args.evidence_root, **inputs)
        exit_code = {"ELIGIBLE": 0, "BLOCKED": 2, "INVALID": 3}[report.decision]
    else:
        report = preflight_freshness(**inputs)
        exit_code = {"EXACT": 0, "BLOCKED": 2, "INVALID": 3}[report.decision]
    print(json.dumps(report.model_dump(mode="json"), sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
