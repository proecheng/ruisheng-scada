from __future__ import annotations

import base64
import copy
import ctypes
import hashlib
import json
import os
import sys
import tomllib
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

import tools.trust_root_freshness as freshness_module
import tools.validate_device_point_profile as validator_module
from tools.validate_device_point_profile import (
    ARTIFACT_TYPE,
    MAX_ARTIFACT_BYTES,
    MAX_BOUND_ARTIFACT_BYTES,
    MAX_EVIDENCE_BINDINGS,
    MAX_EVIDENCE_BYTES,
    MAX_PROFILE_POINTS,
    REQUIRED_RUNTIME_ASSERTIONS,
    SEMANTIC_VALIDATOR_ID,
    AnalogCalibrationEvidence,
    AnalogReferenceEvidence,
    AuthoritativeMapEvidenceContent,
    BinaryCalibrationEvidence,
    CalibrationPointPlan,
    CalibrationRunApprovalArtifact,
    CalibrationRunApprovalBinding,
    CounterCalibrationEvidence,
    CounterReferenceEvidence,
    DevicePointProfile,
    EvidenceArtifact,
    LineProtocol,
    LineProtocolEvidenceContent,
    PointProfile,
    PolicyTrustRoot,
    RawLineProbeObservationRecord,
    RawObservationEvidenceContent,
    ReleaseVerificationReceipt,
    RuntimeRawReport,
    TrustPolicy,
    _authoritative_map_conflict_reasons,
    _calibration_run_approval_reasons,
    _counter_sequence_valid,
    _evidence_ownership_reasons,
    _identity_evidence_conflict_reasons,
    _line_protocol_evidence_reasons,
    _load_json_bytes,
    _point_calibration_reasons,
    _read_windows_fixed_trust_root_once,
    _run_content_timestamps,
    _trust_key_valid_at,
    _validate_profile_data_with_trusted_context,
    _windows_fixed_trust_root_acl_is_protected,
    _windows_native_trust_root_operations,
    _WindowsAce,
    _WindowsAclSnapshot,
    _WindowsHandleSnapshot,
    _WindowsTrustRootOperations,
    approval_signature_message,
    calibration_run_approval_signature_message,
    candidate_profile_from_legacy_evidence,
    canonical_calibration_plan_sha256,
    canonical_calibration_profile_input_sha256,
    canonical_device_identity_sha256,
    canonical_gate_sha256,
    canonical_payload_sha256,
    current_schema_sha256,
    current_validator_source_sha256,
    evidence_signature_message,
    line_configuration_readback_sha256,
    raw_observation_record_sha256,
    release_receipt_check_digests,
    release_receipt_protected_snapshot_id,
    release_receipt_signature_message,
    render_schema_document,
    runtime_signature_message,
    schema_document,
    trust_policy_sha256,
    trust_policy_signature_message,
    trust_root_sha256,
    validate_legacy_evidence_file,
    validate_profile_data,
    validate_profile_file,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "schemas" / "point-profile" / "point-profile-v1.schema.json"
LEGACY = (
    ROOT
    / "docs"
    / "superpowers"
    / "specs"
    / "evidence"
    / "b08-20260827"
    / "legacy-point-candidates.json"
)
NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
SHA_ZERO = "sha256:" + "0" * 64
SHA_ONE = "sha256:" + "1" * 64
PUBLISHER_FINGERPRINT = "SHA256:" + "A" * 43
RELEASE_RECEIPT_SIGNATURE_NAMESPACE = "ruisheng-release-verification-receipt-v1"
WINDOWS_USB_INTERFACE_PATH = (
    r"\\?\USB#VID_0001&PID_0002#SERIAL"
    r"#{4D36E978-E325-11CE-BFC1-08002BE10318}"
)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _write_bytes(root: Path, relative: str, contents: bytes) -> dict[str, Any]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    return {
        "path": relative,
        "sha256": "sha256:" + hashlib.sha256(contents).hexdigest(),
        "size_bytes": len(contents),
    }


def _public_key(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def _signature(key_id: str, key: Ed25519PrivateKey, message: bytes) -> dict[str, str]:
    return {
        "algorithm": "Ed25519",
        "key_id": key_id,
        "value": base64.b64encode(key.sign(message)).decode("ascii"),
    }


def _ssh_string(value: bytes) -> bytes:
    return len(value).to_bytes(4, "big") + value


def _release_receipt_signature(
    key_id: str,
    key: Ed25519PrivateKey,
    message: bytes,
) -> dict[str, str]:
    namespace = RELEASE_RECEIPT_SIGNATURE_NAMESPACE.encode("ascii")
    hash_algorithm = b"sha512"
    public_key = key.public_key().public_bytes_raw()
    public_key_blob = _ssh_string(b"ssh-ed25519") + _ssh_string(public_key)
    signed_payload = (
        b"SSHSIG"
        + _ssh_string(namespace)
        + _ssh_string(b"")
        + _ssh_string(hash_algorithm)
        + _ssh_string(hashlib.sha512(message).digest())
    )
    signature_blob = _ssh_string(b"ssh-ed25519") + _ssh_string(key.sign(signed_payload))
    binary_signature = (
        b"SSHSIG"
        + (1).to_bytes(4, "big")
        + _ssh_string(public_key_blob)
        + _ssh_string(namespace)
        + _ssh_string(b"")
        + _ssh_string(hash_algorithm)
        + _ssh_string(signature_blob)
    )
    return {
        "algorithm": "OpenSSH-SSHSIG-Ed25519",
        "key_id": key_id,
        "namespace": RELEASE_RECEIPT_SIGNATURE_NAMESPACE,
        "value": base64.b64encode(binary_signature).decode("ascii"),
    }


def _dummy_signature(key_id: str = "unused") -> dict[str, str]:
    return {
        "algorithm": "Ed25519",
        "key_id": key_id,
        "value": base64.b64encode(b"0" * 64).decode("ascii"),
    }


@pytest.mark.parametrize(
    "created_at",
    [
        "2026-08-27T11:00:00+08:00",
        "2026-08-27T03:00:00Z",
        "2026-08-27T03:00+00:00",
    ],
)
def test_timestamps_require_canonical_utc_text(tmp_path: Path, created_at: str) -> None:
    profile = _minimal_profile(tmp_path)
    profile["created_at"] = created_at

    with pytest.raises(ValidationError):
        DevicePointProfile.model_validate(profile)


def _trust_contract() -> tuple[
    TrustPolicy,
    PolicyTrustRoot,
    dict[str, Ed25519PrivateKey],
]:
    keys = {
        name: Ed25519PrivateKey.generate()
        for name in (
            "authority",
            "project_owner",
            "device_firmware_owner",
            "site_safety_owner",
            "test_owner",
            "evidence",
            "reference",
            "runner",
            "release",
        )
    }
    policy_value: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "ruisheng.device-point-profile-trust-policy",
        "policy_id": "site-a-point-profile",
        "policy_version": 2,
        "semantic_validator": SEMANTIC_VALIDATOR_ID,
        "validator_source_sha256": current_validator_source_sha256(),
        "authority_id": "site-security-authority",
        "valid_from": "2026-08-01T00:00:00+00:00",
        "expires_at": "2026-09-01T00:00:00+00:00",
        "revocation_sequence": 7,
        "status": "active",
        "approval_keys": [
            {
                "role": role,
                "key_id": f"{role}-key",
                "identity": f"{role}@example.invalid",
                "public_key": _public_key(keys[role]),
                "valid_from": "2026-08-01T00:00:00+00:00",
                "expires_at": "2026-09-01T00:00:00+00:00",
                "revocation_sequence": 7,
                "status": "active",
            }
            for role in (
                "project_owner",
                "device_firmware_owner",
                "site_safety_owner",
                "test_owner",
            )
        ],
        "evidence_keys": [
            {
                "attestor_id": "evidence-runner",
                "key_id": "evidence-key",
                "public_key": _public_key(keys["evidence"]),
                "roles": [
                    "identity",
                    "authoritative_map",
                    "line_protocol",
                    "calibration",
                    "raw_observation",
                    "contradiction_resolution",
                ],
                "valid_from": "2026-08-01T00:00:00+00:00",
                "expires_at": "2026-09-01T00:00:00+00:00",
                "revocation_sequence": 7,
                "status": "active",
            },
            {
                "attestor_id": "reference-runner",
                "key_id": "reference-key",
                "public_key": _public_key(keys["reference"]),
                "roles": ["reference"],
                "valid_from": "2026-08-01T00:00:00+00:00",
                "expires_at": "2026-09-01T00:00:00+00:00",
                "revocation_sequence": 7,
                "status": "active",
            },
        ],
        "runtime_runner_keys": [
            {
                "runner_id": "runtime-runner",
                "key_id": "runner-key",
                "public_key": _public_key(keys["runner"]),
                "tool_id": "point-profile-runtime-suite/v2",
                "tool_sha256": SHA_ONE,
                "valid_from": "2026-08-01T00:00:00+00:00",
                "expires_at": "2026-09-01T00:00:00+00:00",
                "revocation_sequence": 7,
                "status": "active",
            }
        ],
        "release_verifier_keys": [
            {
                "verifier_id": "protected-release-verifier",
                "key_id": "release-key",
                "public_key": _public_key(keys["release"]),
                "tool_id": "ruisheng.release-artifacts-receipt-producer/v1",
                "tool_sha256": SHA_ONE,
                "publisher_key_fingerprints": [PUBLISHER_FINGERPRINT],
                "valid_from": "2026-08-01T00:00:00+00:00",
                "expires_at": "2026-09-01T00:00:00+00:00",
                "revocation_sequence": 7,
                "status": "active",
            }
        ],
    }
    policy_value["authority_signature"] = _signature(
        "authority-key",
        keys["authority"],
        trust_policy_signature_message(policy_value),
    )
    policy = TrustPolicy.model_validate(policy_value)
    root = PolicyTrustRoot.model_validate(
        {
            "schema_version": 1,
            "artifact_type": "ruisheng.device-point-profile-policy-trust-root",
            "root_id": "site-a-protected-root",
            "root_version": 3,
            "authority_id": "site-security-authority",
            "key_id": "authority-key",
            "public_key": _public_key(keys["authority"]),
            "valid_from": "2026-08-01T00:00:00+00:00",
            "expires_at": "2027-08-01T00:00:00+00:00",
            "revocation_sequence": 11,
            "status": "active",
            "authorized_policies": [
                {
                    "policy_id": policy.policy_id,
                    "policy_version": policy.policy_version,
                    "policy_sha256": trust_policy_sha256(policy),
                    "revocation_sequence": policy.revocation_sequence,
                    "status": "active",
                }
            ],
        }
    )
    return policy, root, keys


@pytest.mark.parametrize(
    ("collection", "identity_field", "error"),
    (
        ("runtime_runner_keys", "runner_id", "runtime runners must use distinct public keys"),
        (
            "release_verifier_keys",
            "verifier_id",
            "release verifiers must use distinct public keys",
        ),
    ),
)
def test_trust_policy_rejects_key_id_aliases_for_one_public_key(
    collection: str, identity_field: str, error: str
) -> None:
    policy, _root, _keys = _trust_contract()
    value = policy.model_dump(mode="json")
    alias = dict(value[collection][0])
    alias["key_id"] = "alias-key"
    alias[identity_field] = value[collection][0][identity_field]
    value[collection].append(alias)

    with pytest.raises(ValidationError, match=error):
        TrustPolicy.model_validate(value)


def _minimal_profile(
    root: Path,
    *,
    policy: TrustPolicy | None = None,
    trust_root: PolicyTrustRoot | None = None,
) -> dict[str, Any]:
    legacy_binding = _write_bytes(root, "evidence/legacy.json", b"{}")
    payload = {
        "device_identity": {
            "status": "unknown",
            "model": None,
            "hardware_revision": None,
            "firmware_version": None,
            "point_map_version": None,
            "usb_serial_number": None,
            "evidence_refs": [],
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
        "points": [
            {
                "point_id": "P1",
                "point_name": "candidate",
                "function_code": 3,
                "start_address": 1,
                "register_width": 1,
                "bit": None,
                "identity_status": "unknown",
                "semantic_status": "unknown",
                "encoding_status": "unknown",
                "unit_status": "unknown",
                "calibration_status": "unknown",
                "implementation_status": "unknown",
                "encoding": {
                    "value_type": "unknown",
                    "byte_order": "unknown",
                    "word_order": "unknown",
                    "raw_domain": None,
                },
                "unit": None,
                "calibration_profile": {"kind": "unknown", "method": "unknown"},
                "evidence_refs": ["LEGACY"],
            }
        ],
    }
    return {
        "schema_version": 1,
        "artifact_type": ARTIFACT_TYPE,
        "profile_id": "profile-test",
        "created_at": "2026-08-27T03:00:00+00:00",
        "semantic_validator": SEMANTIC_VALIDATOR_ID,
        "validator_source_sha256": current_validator_source_sha256(),
        "policy_sha256": None if policy is None else trust_policy_sha256(policy),
        "trust_root_sha256": None if trust_root is None else trust_root_sha256(trust_root),
        "schema_sha256": current_schema_sha256(),
        "profile_payload": payload,
        "payload_sha256": canonical_payload_sha256(payload),
        "evidence_bindings": [
            {
                "evidence_id": "LEGACY",
                "role": "legacy_source",
                **legacy_binding,
                "media_type": "application/json",
                "subject_point_ids": ["P1"],
            }
        ],
        "calibration_run_approval_binding": None,
        "approval_binding": None,
        "runtime_target": None,
        "runtime_evidence": [],
        "contradictions": [],
    }


def test_profile_rejects_stale_validator_source_digest(tmp_path: Path) -> None:
    policy, trust_root, _ = _trust_contract()
    profile = _minimal_profile(tmp_path, policy=policy, trust_root=trust_root)
    profile["validator_source_sha256"] = SHA_ZERO

    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=trust_root,
    )

    assert report.decision == "INVALID"
    assert "VALIDATOR_SOURCE_HASH_MISMATCH" in _codes(report)


def test_policy_rejects_stale_validator_source_digest(tmp_path: Path) -> None:
    policy, trust_root, keys = _trust_contract()
    policy_value = policy.model_dump(mode="json")
    policy_value["validator_source_sha256"] = SHA_ZERO
    policy_value["authority_signature"] = _signature(
        "authority-key",
        keys["authority"],
        trust_policy_signature_message(policy_value),
    )
    stale_policy = TrustPolicy.model_validate(policy_value)
    profile = _minimal_profile(tmp_path, policy=stale_policy, trust_root=trust_root)

    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=stale_policy,
        trust_root=trust_root,
    )

    assert report.decision == "INVALID"
    assert "TRUST_POLICY_VALIDATOR_SOURCE_MISMATCH" in _codes(report)


@pytest.mark.parametrize(
    "collection", ["approval_keys", "evidence_keys", "runtime_runner_keys", "release_verifier_keys"]
)
def test_trust_keys_are_evaluated_at_artifact_time(collection: str) -> None:
    policy, _, _ = _trust_contract()
    key = getattr(policy, collection)[0]

    assert not _trust_key_valid_at(key, "2026-07-31T23:59:59+00:00", policy)
    assert _trust_key_valid_at(key, "2026-08-01T00:00:00+00:00", policy)
    assert _trust_key_valid_at(key, "2026-08-31T23:59:59.999999+00:00", policy)
    assert not _trust_key_valid_at(key, "2026-09-01T00:00:00+00:00", policy)
    assert not _trust_key_valid_at(key.model_copy(update={"status": "revoked"}), NOW, policy)


def test_calibration_profile_results_do_not_change_pre_run_input_digest(tmp_path: Path) -> None:
    profile, _, _ = _synthetic_eligible_contract(tmp_path)
    before = canonical_calibration_profile_input_sha256(profile)
    point = profile["profile_payload"]["points"][0]
    point["calibration_status"] = "ambiguous"
    point["calibration_profile"]["engineering_mapping"] = {"ratio": 0.2, "offset": 4.0}

    assert canonical_calibration_profile_input_sha256(profile) == before


_WINDOWS_SYSTEM_SID = "S-1-5-18"
_WINDOWS_ADMINISTRATORS_SID = "S-1-5-32-544"
_WINDOWS_TRUSTED_INSTALLER_SID = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
_WINDOWS_USERS_SID = "S-1-5-32-545"


def _windows_acl_snapshot(
    *,
    owner_sid: str | None = _WINDOWS_SYSTEM_SID,
    dacl_present: bool = True,
    protected: bool = True,
    aces: tuple[_WindowsAce, ...] = (),
) -> _WindowsAclSnapshot:
    return _WindowsAclSnapshot(owner_sid, dacl_present, protected, aces)


def _windows_acl_chain(
    *,
    length: int = 5,
) -> tuple[_WindowsAclSnapshot, ...]:
    return tuple(_windows_acl_snapshot() for _ in range(length))


@pytest.mark.parametrize(
    "owner_sid",
    [
        _WINDOWS_SYSTEM_SID,
        _WINDOWS_ADMINISTRATORS_SID,
        _WINDOWS_TRUSTED_INSTALLER_SID,
    ],
)
def test_fixed_windows_trust_root_accepts_closed_authority_owner_set(
    owner_sid: str,
) -> None:
    snapshots = tuple(_windows_acl_snapshot(owner_sid=owner_sid) for _ in range(5))

    assert _windows_fixed_trust_root_acl_is_protected(snapshots)


@pytest.mark.parametrize(
    ("replacement", "expected"),
    [
        (_windows_acl_snapshot(owner_sid=_WINDOWS_USERS_SID), False),
        (_windows_acl_snapshot(dacl_present=False), False),
        (_windows_acl_snapshot(protected=False), False),
    ],
)
def test_fixed_windows_trust_root_rejects_unsafe_owner_or_leaf_descriptor(
    replacement: _WindowsAclSnapshot,
    expected: bool,
) -> None:
    snapshots = list(_windows_acl_chain())
    snapshots[-1] = replacement

    assert _windows_fixed_trust_root_acl_is_protected(tuple(snapshots)) is expected


def test_fixed_windows_trust_root_requires_direct_parent_protected_dacl() -> None:
    snapshots = list(_windows_acl_chain())
    snapshots[-2] = _windows_acl_snapshot(protected=False)

    assert not _windows_fixed_trust_root_acl_is_protected(tuple(snapshots))


@pytest.mark.parametrize(
    ("component_index", "mask"),
    [
        (-1, 0x0002),  # leaf FILE_WRITE_DATA
        (-1, 0x00040000),  # leaf WRITE_DAC
        (-2, 0x0002),  # direct parent FILE_ADD_FILE
        (-2, 0x0040),  # direct parent FILE_DELETE_CHILD
        (-2, 0x0100),  # direct parent FILE_WRITE_ATTRIBUTES
        (1, 0x0040),  # ancestor FILE_DELETE_CHILD
        (1, 0x00080000),  # ancestor WRITE_OWNER
    ],
)
def test_fixed_windows_trust_root_rejects_untrusted_mutation_or_replacement_rights(
    component_index: int,
    mask: int,
) -> None:
    snapshots = list(_windows_acl_chain())
    snapshots[component_index] = _windows_acl_snapshot(
        aces=(_WindowsAce(0, 0, mask, _WINDOWS_USERS_SID),)
    )

    assert not _windows_fixed_trust_root_acl_is_protected(tuple(snapshots))


def test_fixed_windows_trust_root_allows_untrusted_read_only_access() -> None:
    read_only = _WindowsAce(0, 0, 0x0001 | 0x00020000, _WINDOWS_USERS_SID)
    snapshots = tuple(_windows_acl_snapshot(aces=(read_only,)) for _ in range(5))

    assert _windows_fixed_trust_root_acl_is_protected(snapshots)


def test_fixed_windows_trust_root_ignores_inherit_only_creator_owner_write() -> None:
    inherit_only_write = _WindowsAce(0, 0x08, 0x40000000, "S-1-3-0")
    snapshots = tuple(_windows_acl_snapshot(aces=(inherit_only_write,)) for _ in range(5))

    assert _windows_fixed_trust_root_acl_is_protected(snapshots)


@pytest.mark.parametrize("ace_type", [4, 5, 9, 11, 255])
def test_fixed_windows_trust_root_rejects_unparsed_allow_ace_types(
    ace_type: int,
) -> None:
    snapshots = list(_windows_acl_chain())
    snapshots[-1] = _windows_acl_snapshot(aces=(_WindowsAce(ace_type, 0, 0, None),))

    assert not _windows_fixed_trust_root_acl_is_protected(tuple(snapshots))


def _fake_windows_trust_root_operations(
    payload_path: Path,
    *,
    drift_after_read: bool = False,
) -> tuple[_WindowsTrustRootOperations, set[int], list[str]]:
    active: set[int] = set()
    events: list[str] = []
    snapshots_per_handle: dict[int, int] = {}

    def open_component(path: str, is_directory: bool) -> int:
        handle = 100 + len(active)
        active.add(handle)
        events.append(f"open:{path}:{is_directory}")
        return handle

    def snapshot_component(
        handle: int,
        path: str,
        is_directory: bool,
    ) -> _WindowsHandleSnapshot:
        assert handle in active
        count = snapshots_per_handle.get(handle, 0) + 1
        snapshots_per_handle[handle] = count
        events.append(f"snapshot:{handle}:{count}")
        identity = (1, 0, handle, 1)
        if drift_after_read and count == 2 and handle == min(active):
            identity = (1, 0, handle + 1, 1)
        return _WindowsHandleSnapshot(
            final_path=path,
            identity=identity,
            attributes=0x10 if is_directory else 0,
            acl=_windows_acl_snapshot(),
        )

    def duplicate_to_fd(handle: int) -> int:
        assert handle in active
        assert len(active) == 5
        events.append("duplicate")
        return os.open(payload_path, os.O_RDONLY)

    def close_handle(handle: int) -> None:
        assert handle in active
        active.remove(handle)
        events.append(f"close:{handle}")

    return (
        _WindowsTrustRootOperations(
            open_component=open_component,
            snapshot_component=snapshot_component,
            duplicate_to_fd=duplicate_to_fd,
            close_handle=close_handle,
        ),
        active,
        events,
    )


def test_fixed_windows_trust_root_holds_every_component_handle_during_read(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload.json"
    payload.write_bytes(b'{"trusted":true}')
    operations, active, events = _fake_windows_trust_root_operations(payload)

    contents = _read_windows_fixed_trust_root_once(
        Path(r"C:\ProgramData\Ruisheng\trust\root.json"),
        maximum_bytes=1024,
        operations=operations,
    )

    assert contents == b'{"trusted":true}'
    assert not active
    duplicate_index = events.index("duplicate")
    assert not any(event.startswith("close:") for event in events[:duplicate_index])


def test_fixed_windows_trust_root_rejects_handle_snapshot_drift(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload.json"
    payload.write_bytes(b"{}")
    operations, active, _events = _fake_windows_trust_root_operations(
        payload,
        drift_after_read=True,
    )

    with pytest.raises(ValueError, match="identity, path, or ACL changed"):
        _read_windows_fixed_trust_root_once(
            Path(r"C:\ProgramData\Ruisheng\trust\root.json"),
            maximum_bytes=1024,
            operations=operations,
        )

    assert not active


@pytest.mark.skipif(os.name != "nt", reason="Win32 trust-root handle contract")
def test_windows_native_trust_root_leaf_blocks_existing_data_writer(
    tmp_path: Path,
) -> None:
    leaf = tmp_path / "root.json"
    leaf.write_bytes(b'{"trusted":true}')
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    writer = create_file(
        str(leaf),
        0x40000000,  # GENERIC_WRITE
        0x00000001 | 0x00000002,  # Existing writer otherwise permits readers and writers.
        None,
        3,  # OPEN_EXISTING
        0x00000080,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    assert writer not in (None, invalid_handle)
    native = _windows_native_trust_root_operations()
    try:
        with pytest.raises(OSError) as error:
            native.open_component(str(leaf), False)
        assert error.value.winerror == 32
    finally:
        assert close_handle(ctypes.c_void_p(writer))

    handle = native.open_component(str(leaf), False)
    try:
        before = native.snapshot_component(handle, str(leaf), False)
        descriptor = native.duplicate_to_fd(handle)
        try:
            assert os.read(descriptor, 1024) == b'{"trusted":true}'
        finally:
            os.close(descriptor)
        after = native.snapshot_component(handle, str(leaf), False)
        assert before == after
        assert after.identity[-1] == 1
        assert not after.attributes & 0x00000010
        assert after.acl.owner_sid is not None
        assert after.acl.dacl_present
    finally:
        native.close_handle(handle)


def _codes(report: Any) -> set[str]:
    return {reason.code for reason in report.reasons}


def _analog_evidence() -> dict[str, Any]:
    values = {
        "A": (0.0, 0.0),
        "B": (100.0, 10.0),
        "C": (50.0, 5.0),
        "A_RETURN": (0.0, 0.0),
    }
    states = []
    for order, (state, (raw, engineering)) in enumerate(values.items(), start=1):
        event = f"event-{state}"
        states.append(
            {
                "state_id": state,
                "event_id": event,
                "samples": [
                    {
                        "sample_id": f"{state}-{index}",
                        "state_id": state,
                        "event_id": event,
                        "observed_at": f"2026-08-27T0{order}:0{index}:00+00:00",
                        "raw": raw,
                        "reference_value": engineering,
                        "engineering": engineering,
                        "sync_error_ms": 1,
                        "uncertainty": 0.1,
                        "stable": True,
                    }
                    for index in range(3)
                ],
                "aggregate_raw": raw,
                "aggregate_engineering": engineering,
                "observed_stability": 0.05,
                "terminal_state": "PASS",
            }
        )
    return {
        "kind": "analog_calibration",
        "evidence_schema_version": 3,
        "point_id": "P1",
        "plan_id": "plan-P1",
        "states": states,
        "ratio": 0.1,
        "offset": 0.0,
        "aggregation_method": "arithmetic_mean",
        "unit_conversion": {
            "source_unit": "degC",
            "target_unit": "degC",
            "method": "identity",
            "scale": 1,
            "offset": 0,
        },
        "exclusion_policy": {
            "rule_set_id": "analog-exclusions-v1",
            "rule_set_sha256": SHA_ONE,
            "allowed_reason_codes": [
                "INSTRUMENT_OUT_OF_RANGE",
                "REFERENCE_UNCERTAINTY_EXCEEDED",
                "SYNC_ERROR_EXCEEDED",
                "UNSTABLE",
            ],
            "maximum_excluded_per_state": 1,
        },
        "exclusion_log": [],
        "thresholds": {
            "minimum_raw_span": 20,
            "minimum_reference_span": 2,
            "absolute_tolerance": 0.5,
            "relative_tolerance": 0.01,
            "return_raw_tolerance": 1,
            "return_engineering_tolerance": 0.5,
            "maximum_sync_error_ms": 5,
            "uncertainty_budget": 0.1,
            "business_tolerance_source": {
                "source_id": "approved-business-tolerance-v1",
                "source_sha256": SHA_ONE,
            },
        },
        "negative_controls": [
            {
                "control_id": "out-of-domain",
                "observed_at": "2026-08-27T05:00:00+00:00",
                "injected_raw": 40000,
                "observed_result": "REJECTED",
                "reason": "outside approved raw domain",
            }
        ],
        "runner_terminal_state": "PASS",
        "reference_terminal_state": "PASS",
    }


def _binary_evidence() -> dict[str, Any]:
    states = []
    for order, (state, raw) in enumerate(
        (("INACTIVE", 0), ("ACTIVE", 1), ("RETURN", 0)),
        start=1,
    ):
        event = f"event-{state}"
        states.append(
            {
                "state_id": state,
                "event_id": event,
                "samples": [
                    {
                        "sample_id": f"{state}-{index}",
                        "state_id": state,
                        "event_id": event,
                        "observed_at": f"2026-08-27T0{order}:0{index}:00+00:00",
                        "raw": raw,
                        "stable": True,
                    }
                    for index in range(3)
                ],
                "aggregate_raw": raw,
                "observed_stability": 0.05,
                "chatter_transitions": 0,
                "terminal_state": "PASS",
            }
        )
    return {
        "kind": "binary_calibration",
        "evidence_schema_version": 3,
        "point_id": "P1",
        "plan_id": "plan-P1",
        "states": states,
        "maximum_chatter_transitions": 0,
        "maximum_sync_error_ms": 5,
        "negative_controls": [
            {
                "control_id": "invalid-state",
                "observed_at": "2026-08-27T04:00:00+00:00",
                "injected_raw": 2,
                "observed_result": "REJECTED",
                "reason": "not an approved state",
            }
        ],
        "address_semantics": {
            "candidate_id": "selected-whole-register",
            "kind": "whole_register",
            "function_code": 3,
            "start_address": 1,
            "register_width": 1,
            "bit": None,
        },
        "unintervened_channel_controls": [
            {
                "control_id": "unintervened-channel-1",
                "sample_id": "binary-unintervened-1",
                "event_id": "event-ACTIVE",
                "observed_at": "2026-08-27T02:04:00+00:00",
                "point_id": "P-CONTROL",
                "point_name": "control_channel",
                "address_semantics": {
                    "candidate_id": "control-whole-register",
                    "kind": "whole_register",
                    "function_code": 3,
                    "start_address": 2,
                    "register_width": 1,
                    "bit": None,
                },
                "baseline_raw": 0,
                "observed_raw": 0,
                "terminal_state": "PASS",
            }
        ],
        "competing_candidate_controls": [
            {
                "control_id": "competing-candidate-1",
                "sample_id": "binary-competitor-1",
                "event_id": "event-ACTIVE",
                "observed_at": "2026-08-27T02:05:00+00:00",
                "candidate": {
                    "candidate_id": "rejected-register-bit",
                    "kind": "register_bit",
                    "function_code": 3,
                    "start_address": 1,
                    "register_width": 1,
                    "bit": 1,
                },
                "observed_raw": 0,
                "observed_result": "REJECTED",
                "reason": "candidate did not transition with the physical state",
            }
        ],
        "runner_terminal_state": "PASS",
        "reference_terminal_state": "PASS",
    }


def _counter_evidence() -> dict[str, Any]:
    state_values = (
        ("BASELINE", 14, 0),
        ("INCREMENT", 15, 1),
        ("ROLLOVER", 0, 1),
        ("PERSISTENCE", 0, 0),
    )
    observations = [
        {
            "sample_id": f"counter-{state_id.lower()}-{sample_index}",
            "state_id": state_id,
            "event_id": f"event-{state_id}",
            "observed_at": (f"2026-08-27T0{state_index + 1}:0{sample_index}:00+00:00"),
            "raw": raw,
            "reference_increment": reference_increment,
            "sync_error_ms": 1,
            "observed_stability": 0.05,
            "stable": True,
        }
        for state_index, (state_id, raw, reference_increment) in enumerate(state_values)
        for sample_index in range(3)
    ]
    return {
        "kind": "counter_calibration",
        "evidence_schema_version": 3,
        "point_id": "P1",
        "plan_id": "plan-P1",
        "counts_per_unit": 1,
        "modulus": 16,
        "rollover_behavior": "wrap",
        "expected_increment": 1,
        "increment_tolerance": 0,
        "maximum_sync_error_ms": 5,
        "observations": observations,
        "monotonicity_verified": True,
        "terminal_raw": 0,
        "persistence_before": 0,
        "persistence_after": 0,
        "persistence_event": {
            "event_id": "event-PERSISTENCE",
            "method": "physical_power_disconnect",
            "power_removed_at": "2026-08-27T03:10:00+00:00",
            "power_restored_at": "2026-08-27T03:20:00+00:00",
            "post_restore_observed_at": "2026-08-27T04:02:00+00:00",
            "power_off_duration_seconds": 600,
            "pre_power_raw": 0,
            "post_power_raw": 0,
            "terminal_state": "PASS",
        },
        "persistence_terminal_state": "PASS",
        "negative_controls": [
            {
                "control_id": "invalid-counter",
                "observed_at": "2026-08-27T04:00:00+00:00",
                "injected_raw": 16,
                "observed_result": "REJECTED",
                "reason": "outside modulus",
            }
        ],
        "runner_terminal_state": "PASS",
        "reference_terminal_state": "PASS",
    }


def _counter_reference_evidence(
    counter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    counter = copy.deepcopy(counter or _counter_evidence())
    samples = [
        {
            "sample_id": observation["sample_id"],
            "state_id": observation["state_id"],
            "event_id": observation["event_id"],
            "observed_at": (
                datetime.fromisoformat(observation["observed_at"])
                + timedelta(milliseconds=observation["sync_error_ms"])
            ).isoformat(),
            "reference_raw": observation["raw"],
            "reference_increment": observation["reference_increment"],
            "unit": "count",
            "sync_error_ms": observation["sync_error_ms"],
            "uncertainty": 0.1,
        }
        for observation in counter["observations"]
    ]
    persistence_event = copy.deepcopy(counter["persistence_event"])
    persistence_event["post_restore_observed_at"] = samples[-1]["observed_at"]
    return {
        "kind": "counter_reference",
        "evidence_schema_version": 4,
        "point_id": counter["point_id"],
        "plan_id": counter["plan_id"],
        "counts_per_unit": counter["counts_per_unit"],
        "modulus": counter["modulus"],
        "rollover_behavior": counter["rollover_behavior"],
        "expected_increment": counter["expected_increment"],
        "expected_terminal_raw": counter["terminal_raw"],
        "expected_persistence_raw": counter["persistence_after"],
        "samples": samples,
        "persistence_event": persistence_event,
        "power_loss_event_id": counter["persistence_event"]["event_id"],
        "persistence_method": counter["persistence_event"]["method"],
        "reference_id": "instrument-counter",
        "channel_id": "channel-counter",
        "calibration_certificate_sha256": SHA_ONE,
        "terminal_state": "PASS",
        "reference_collector_tool_id": "ruisheng.reference-collector/v1",
        "reference_collector_tool_sha256": SHA_ONE,
    }


def _counter_plan_value(
    *,
    sample_count_per_state: int = 3,
    maximum_requests: int = 12,
) -> dict[str, Any]:
    return {
        "plan_id": "plan-counter",
        "point_id": "P-COUNTER",
        "point_name": "pulse_count",
        "point_unit": "count",
        "function_code": 3,
        "start_address": 30,
        "register_width": 1,
        "bit": None,
        "value_type": "u16",
        "byte_order": "big",
        "word_order": "not_applicable",
        "raw_domain": {"minimum": 0, "maximum": 15},
        "calibration_kind": "counter",
        "state_ids": ["BASELINE", "INCREMENT", "ROLLOVER", "PERSISTENCE"],
        "sample_count_per_state": sample_count_per_state,
        "instrument_id": "instrument-counter",
        "instrument_calibration_sha256": SHA_ONE,
        "reference_channel_id": "channel-counter",
        "reference_unit": "count",
        "sync_tolerance_ms": 5,
        "stability_threshold": 0.1,
        "minimum_raw_span": 1,
        "minimum_reference_span": 1,
        "absolute_tolerance": 0.5,
        "relative_tolerance": 0.01,
        "uncertainty_budget": 0.1,
        "expected_counter_increment": 1,
        "counter_increment_tolerance": 0,
        "counter_modulus": 16,
        "counter_rollover_behavior": "wrap",
        "counter_persistence_method": "physical_power_disconnect",
        "minimum_power_off_duration_seconds": 600,
        "persistence_required": True,
        "tx_scope": [
            {
                "function_code": 3,
                "start_address": 30,
                "quantity": 1,
                "maximum_requests": maximum_requests,
                "write_allowed": False,
            }
        ],
        "safety_plan_id": "safety-plan",
        "operator_id": "operator",
        "raw_collector_tool_id": "ruisheng.calibration-collector/v1",
        "raw_collector_tool_sha256": SHA_ONE,
        "reference_collector_tool_id": "ruisheng.reference-collector/v1",
        "reference_collector_tool_sha256": SHA_ONE,
    }


def _modbus_crc16(payload: bytes) -> int:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _rtu_hex(payload: bytes) -> str:
    return (payload + _modbus_crc16(payload).to_bytes(2, "little")).hex()


def _posix_line_readback(
    *,
    stable_device_path: str = "/dev/serial/by-id/usb-Ruisheng_SERIAL",
    device_serial: str = "SERIAL",
    baud_rate: int = 9600,
) -> dict[str, Any]:
    return {
        "kind": "posix_termios_udev",
        "observation_method": "posix_termios_readback",
        "termios": {
            "device_node": "/dev/ttyUSB0",
            "baud_rate": baud_rate,
            "data_bits": 8,
            "parity": "N",
            "stop_bits": 1,
        },
        "udev": {
            "stable_device_path": stable_device_path,
            "device_node": "/dev/ttyUSB0",
            "id_bus": "usb",
            "id_serial_short": device_serial,
            "devlinks": [stable_device_path],
        },
    }


def _windows_line_readback(
    *,
    stable_device_path: str = WINDOWS_USB_INTERFACE_PATH,
    device_serial: str = "SERIAL",
    baud_rate: int = 9600,
) -> dict[str, Any]:
    return {
        "kind": "windows_dcb_setupapi",
        "observation_method": "win32_dcb_readback",
        "dcb": {
            "port_name": "COM3",
            "baud_rate": baud_rate,
            "data_bits": 8,
            "parity": "N",
            "stop_bits": 1,
        },
        "setupapi": {
            "device_interface_path": stable_device_path,
            "device_instance_id": rf"USB\VID_0001&PID_0002\{device_serial}",
            "hardware_ids": [r"USB\VID_0001&PID_0002"],
            "serial_number": device_serial,
        },
    }


def _raw_observation_evidence(
    calibration: dict[str, Any],
    *,
    point_id: str,
    plan_id: str,
    run_id: str,
    unit_id: int,
    function_code: int,
    start_address: int,
    quantity: int,
    line_probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if line_probe is not None:
        line_probe = copy.deepcopy(line_probe)
        line_probe["configuration_readback_sha256"] = line_configuration_readback_sha256(
            line_probe["configuration_readback"]
        )
    samples: list[dict[str, Any]] = []
    if calibration["kind"] == "analog_calibration":
        samples.extend(sample for state in calibration["states"] for sample in state["samples"])
        samples.extend(calibration["exclusion_log"])
    elif calibration["kind"] == "binary_calibration":
        samples.extend(sample for state in calibration["states"] for sample in state["samples"])
        for control in calibration["unintervened_channel_controls"]:
            semantics = control["address_semantics"]
            samples.append(
                {
                    "sample_id": control["sample_id"],
                    "event_id": control["event_id"],
                    "observed_at": control["observed_at"],
                    "raw": control["observed_raw"],
                    "_scope": (
                        semantics["function_code"],
                        semantics["start_address"],
                        semantics["register_width"],
                    ),
                }
            )
        for control in calibration["competing_candidate_controls"]:
            candidate = control["candidate"]
            samples.append(
                {
                    "sample_id": control["sample_id"],
                    "event_id": control["event_id"],
                    "observed_at": control["observed_at"],
                    "raw": control["observed_raw"],
                    "_scope": (
                        candidate["function_code"],
                        candidate["start_address"],
                        candidate["register_width"],
                    ),
                }
            )
    else:
        samples.extend(calibration["observations"])
    samples.sort(key=lambda sample: cast(str, sample["observed_at"]))
    records: list[dict[str, Any]] = []
    previous_sha256 = "GENESIS"

    def add_record(record: dict[str, Any]) -> None:
        nonlocal previous_sha256
        record.update(
            {
                "record_schema_version": 1,
                "record_id": f"{point_id}-record-{len(records)}",
                "sequence_number": len(records),
                "previous_record_sha256": previous_sha256,
                "point_id": point_id,
                "plan_id": plan_id,
                "run_id": run_id,
            }
        )
        record["record_sha256"] = raw_observation_record_sha256(record)
        previous_sha256 = record["record_sha256"]
        records.append(record)

    add_record(
        {
            "record_type": "audit_event",
            "observed_at": "2026-08-27T00:45:00+00:00",
            "event": "RUN_STARTED",
            "outcome": "STARTED",
        }
    )
    for sample_index, sample in enumerate(samples):
        raw = sample["raw"]
        sample_function_code, sample_start_address, sample_quantity = sample.get(
            "_scope", (function_code, start_address, quantity)
        )
        request = bytes(
            (
                unit_id,
                sample_function_code,
                sample_start_address >> 8,
                sample_start_address & 0xFF,
                sample_quantity >> 8,
                sample_quantity & 0xFF,
            )
        )
        if sample_function_code in {1, 2}:
            response = bytes((unit_id, sample_function_code, 1, int(raw) & 1))
        else:
            payload = int(raw).to_bytes(sample_quantity * 2, "big")
            response = bytes((unit_id, sample_function_code, len(payload))) + payload
        add_record(
            {
                "record_type": (
                    "line_probe_observation"
                    if sample_index == 0 and line_probe is not None
                    else "modbus_observation"
                ),
                "sample_id": sample["sample_id"],
                "event_id": sample["event_id"],
                "observed_at": sample["observed_at"],
                "unit_id": unit_id,
                "function_code": sample_function_code,
                "start_address": sample_start_address,
                "quantity": sample_quantity,
                "request_rtu_hex": _rtu_hex(request),
                "response_rtu_hex": _rtu_hex(response),
                "request_crc_valid": True,
                "response_crc_valid": True,
                "decoded_raw": raw,
                **(line_probe if sample_index == 0 and line_probe is not None else {}),
            }
        )
    add_record(
        {
            "record_type": "audit_event",
            "observed_at": "2026-08-27T05:30:00+00:00",
            "event": "RUN_COMPLETED",
            "outcome": "PASS",
        }
    )
    return {
        "kind": "raw_observation",
        "evidence_schema_version": 4,
        "point_id": point_id,
        "plan_id": plan_id,
        "run_id": run_id,
        "collector_tool_id": "ruisheng.calibration-collector/v1",
        "collector_tool_sha256": SHA_ONE,
        "records": records,
        "chain_tip_sha256": previous_sha256,
        "terminal_state": "PASS",
    }


def _reference_timestamp(value: str) -> str:
    return (datetime.fromisoformat(value) + timedelta(milliseconds=1)).isoformat()


def _analog_reference_samples(calibration: dict[str, Any]) -> list[dict[str, Any]]:
    samples = [
        {
            "sample_id": sample["sample_id"],
            "state_id": sample["state_id"],
            "event_id": sample["event_id"],
            "observed_at": _reference_timestamp(sample["observed_at"]),
            "reference_value": sample["reference_value"],
            "unit": calibration["unit_conversion"]["source_unit"],
            "sync_error_ms": sample["sync_error_ms"],
            "uncertainty": sample["uncertainty"],
            "stable": sample["stable"],
            "outcome": "ACCEPTED",
            "exclusion_reason": None,
        }
        for state in calibration["states"]
        for sample in state["samples"]
    ]
    return sorted(samples, key=lambda sample: cast(str, sample["observed_at"]))


def _binary_reference_samples(calibration: dict[str, Any]) -> list[dict[str, Any]]:
    physical_states = {"INACTIVE": "INACTIVE", "ACTIVE": "ACTIVE", "RETURN": "INACTIVE"}
    return [
        {
            "sample_id": sample["sample_id"],
            "state_id": sample["state_id"],
            "event_id": sample["event_id"],
            "observed_at": _reference_timestamp(sample["observed_at"]),
            "reference_state": physical_states[sample["state_id"]],
            "unit": "state",
            "sync_error_ms": 1,
            "uncertainty": 0.1,
        }
        for state in calibration["states"]
        for sample in state["samples"]
    ]


def _counter_reference_samples(calibration: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "sample_id": observation["sample_id"],
            "state_id": observation["state_id"],
            "event_id": observation["event_id"],
            "observed_at": _reference_timestamp(observation["observed_at"]),
            "reference_raw": observation["raw"],
            "reference_increment": observation["reference_increment"],
            "unit": "count",
            "sync_error_ms": observation["sync_error_ms"],
            "uncertainty": 0.1,
        }
        for observation in calibration["observations"]
    ]


def test_checked_in_schema_is_generated_from_authoritative_model() -> None:
    assert json.loads(SCHEMA.read_text(encoding="utf-8")) == schema_document()
    assert SCHEMA.read_bytes() == render_schema_document().encode("utf-8")
    assert schema_document()["x-semantic-validator"] == SEMANTIC_VALIDATOR_ID
    assert "ReleaseManifestBinding" not in schema_document()["$defs"]
    assert "ReleaseVerificationReceiptBinding" in schema_document()["$defs"]


def test_point_profile_validator_dependencies_are_available_without_dev_group() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    dev_dependencies = project["dependency-groups"]["dev"]

    assert any(item.startswith("pydantic>=") for item in dependencies)
    assert any(item.startswith("cryptography>=") for item in dependencies)
    assert not any(item.startswith("pydantic>=") for item in dev_dependencies)
    assert not any(item.startswith("cryptography>=") for item in dev_dependencies)


def test_public_api_cannot_accept_or_establish_root_authority(tmp_path: Path) -> None:
    policy, root, _ = _trust_contract()
    profile = _minimal_profile(tmp_path, policy=policy, trust_root=root)
    missing = validate_profile_data(profile, root=tmp_path, now=NOW, trust_policy=policy)
    with pytest.raises(TypeError):
        validate_profile_data(  # type: ignore[call-arg]
            profile,
            root=tmp_path,
            now=NOW,
            trust_policy=policy,
            trust_root=root,
        )
    injected = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=cast(Any, root.model_dump(mode="json")),
    )
    assert missing.decision == "BLOCKED"
    assert "FRESHNESS_CONTEXT_REQUIRED" in _codes(missing)
    assert injected.decision == "INVALID"
    assert _codes(injected) == {"TRUST_ROOT_INVALID"}


@dataclass(frozen=True)
class _FreshnessQualificationContext:
    candidate_logical_identity: str
    challenge: str
    requested_at: str
    trust_root_snapshot: Path
    trust_root_snapshot_sha256: str
    provider_config_snapshot: Path
    attestation_snapshot: Path
    attestation_snapshot_sha256: str


def _freshness_authentication_kwargs(
    profile_path: Path,
    policy_path: Path,
    context: _FreshnessQualificationContext,
    *,
    now: datetime,
) -> dict[str, Any]:
    return {
        "profile_path": profile_path,
        "policy_path": policy_path,
        "trust_root_path": context.trust_root_snapshot,
        "provider_config_path": context.provider_config_snapshot,
        "attestation_path": context.attestation_snapshot,
        "challenge": context.challenge,
        "requested_at": context.requested_at,
        "candidate_logical_identity": context.candidate_logical_identity,
        "expected_trust_root_snapshot_sha256": context.trust_root_snapshot_sha256,
        "expected_provider_config_snapshot_sha256": "sha256:"
        + hashlib.sha256(context.provider_config_snapshot.read_bytes()).hexdigest(),
        "expected_attestation_sha256": "sha256:"
        + hashlib.sha256(context.attestation_snapshot.read_bytes()).hexdigest(),
        "now": now,
    }


def _qualify_profile_with_freshness(
    profile_path: Path,
    *,
    root: Path,
    trust_policy_path: Path,
    freshness: _FreshnessQualificationContext,
    now: datetime | None = None,
    completion_now: datetime | None = None,
) -> Any:
    return freshness_module.qualify_freshness(
        evidence_root=root,
        profile_path=profile_path,
        policy_path=trust_policy_path,
        trust_root_path=freshness.trust_root_snapshot,
        provider_config_path=freshness.provider_config_snapshot,
        attestation_path=freshness.attestation_snapshot,
        challenge=freshness.challenge,
        requested_at=freshness.requested_at,
        candidate_logical_identity=freshness.candidate_logical_identity,
        expected_trust_root_snapshot_sha256=freshness.trust_root_snapshot_sha256,
        expected_provider_config_snapshot_sha256="sha256:"
        + hashlib.sha256(freshness.provider_config_snapshot.read_bytes()).hexdigest(),
        expected_attestation_sha256=(
            "sha256:" + hashlib.sha256(freshness.attestation_snapshot.read_bytes()).hexdigest()
            if freshness.attestation_snapshot.exists()
            else freshness.attestation_snapshot_sha256
        ),
        now=now,
        completion_now=completion_now or now,
    )


def _publisher_freshness_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    attestation_state_update: dict[str, Any] | None = None,
    monotonic_state_id: str = "site-a-root-policy-high-water",
    monotonic_counter: int = 42,
) -> tuple[Path, Path, _FreshnessQualificationContext]:
    policy, trust_root, _keys = _trust_contract()
    profile_value = _minimal_profile(tmp_path, policy=policy, trust_root=trust_root)
    profile = DevicePointProfile.model_validate(profile_value)
    profile_path = tmp_path / "profile.json"
    policy_path = tmp_path / "policy.json"
    root_snapshot = tmp_path / "publisher-root-snapshot.json"
    config_snapshot = tmp_path / "freshness-config.json"
    fixed_config = tmp_path / "fixed-freshness-config.json"
    attestation_snapshot = tmp_path / "freshness-attestation.json"
    for path, value in (
        (profile_path, profile.model_dump(mode="json")),
        (policy_path, policy.model_dump(mode="json")),
        (root_snapshot, trust_root.model_dump(mode="json")),
    ):
        path.write_text(
            json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
    witness_key = Ed25519PrivateKey.generate()
    config = freshness_module.FreshnessProviderConfig.model_validate(
        {
            "schema_version": 1,
            "artifact_type": "ruisheng.trust-root-freshness-provider-config",
            "site_id": "site-a",
            "provider_id": "site-a-independent-witness",
            "witness_key_id": "freshness-key-1",
            "witness_public_key": _public_key(witness_key),
            "verifier_id": "ruisheng.verify-publisher.test-v1",
            "verifier_tool_sha256": SHA_ONE,
            "monotonic_state_id": "site-a-root-policy-high-water",
            "minimum_monotonic_counter": 42,
            "maximum_clock_skew_seconds": 60,
            "maximum_attestation_lifetime_seconds": 300,
        }
    )
    config_snapshot.write_text(
        json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    fixed_config.write_bytes(config_snapshot.read_bytes())
    monkeypatch.setattr(
        validator_module,
        "FIXED_FRESHNESS_PROVIDER_CONFIG_PATH",
        fixed_config,
    )

    def read_protected_config(path: Path, *, maximum_bytes: int) -> bytes:
        assert path == fixed_config
        return validator_module._read_explicit_file_once(path, maximum_bytes=maximum_bytes)

    monkeypatch.setattr(
        validator_module,
        "_read_fixed_trust_root_once",
        read_protected_config,
    )
    state_value: dict[str, Any] = {
        "root_id": trust_root.root_id,
        "root_version": trust_root.root_version,
        "root_revocation_sequence": trust_root.revocation_sequence,
        "root_sha256": trust_root_sha256(trust_root),
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "policy_revocation_sequence": policy.revocation_sequence,
        "policy_sha256": trust_policy_sha256(policy),
    }
    request = freshness_module.FreshnessRequest.model_validate(
        {
            "schema_version": 1,
            "artifact_type": "ruisheng.trust-root-freshness-request",
            "site_id": config.site_id,
            "challenge": "A" * 43,
            "requested_at": NOW.isoformat(),
            "candidate_logical_identity": SHA_ONE,
            "root_snapshot_sha256": "sha256:"
            + hashlib.sha256(root_snapshot.read_bytes()).hexdigest(),
            "provider_config_sha256": "sha256:"
            + hashlib.sha256(config_snapshot.read_bytes()).hexdigest(),
            "profile_id": profile.profile_id,
            "profile_sha256": validator_module._canonical_sha256(profile.model_dump(mode="json")),
            "payload_sha256": profile.payload_sha256,
            "canonical_gate_sha256": canonical_gate_sha256(profile),
            "semantic_validator": profile.semantic_validator,
            "validator_source_sha256": profile.validator_source_sha256,
            "verifier_id": "ruisheng.verify-publisher.test-v1",
            "verifier_tool_sha256": SHA_ONE,
            "state": state_value,
        }
    )
    high_water = dict(state_value)
    high_water.update(attestation_state_update or {})
    attestation: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "ruisheng.trust-root-freshness-attestation",
        "provider_id": config.provider_id,
        "witness_key_id": config.witness_key_id,
        "request": request.model_dump(mode="json"),
        "high_water": high_water,
        "monotonic_state_id": monotonic_state_id,
        "monotonic_counter": monotonic_counter,
        "observed_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
        "signature": _dummy_signature(config.witness_key_id),
    }
    attestation["signature"] = _signature(
        config.witness_key_id,
        witness_key,
        freshness_module.freshness_attestation_signature_message(attestation),
    )
    attestation_snapshot.write_text(
        json.dumps(attestation, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    context = _FreshnessQualificationContext(
        candidate_logical_identity=SHA_ONE,
        challenge="A" * 43,
        requested_at=NOW.isoformat(),
        trust_root_snapshot=root_snapshot,
        trust_root_snapshot_sha256="sha256:"
        + hashlib.sha256(root_snapshot.read_bytes()).hexdigest(),
        provider_config_snapshot=config_snapshot,
        attestation_snapshot=attestation_snapshot,
        attestation_snapshot_sha256="sha256:"
        + hashlib.sha256(attestation_snapshot.read_bytes()).hexdigest(),
    )
    return profile_path, policy_path, context


def test_publisher_freshness_context_allows_existing_gate_only_after_exact_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_path, policy_path, context = _publisher_freshness_fixture(tmp_path, monkeypatch)

    report = _qualify_profile_with_freshness(
        profile_path,
        root=tmp_path,
        trust_policy_path=policy_path,
        freshness=context,
        now=NOW,
    )

    assert report.decision == "BLOCKED"
    assert not any(code.startswith("FRESHNESS_") for code in _codes(report))
    assert "IDENTITY_UNRESOLVED" in _codes(report)


def test_missing_publisher_attestation_blocks_before_evidence_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_path, policy_path, context = _publisher_freshness_fixture(tmp_path, monkeypatch)
    context.attestation_snapshot.unlink()

    monkeypatch.setattr(
        validator_module,
        "_check_binding",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("freshness failure must precede evidence I/O")
        ),
    )
    report = _qualify_profile_with_freshness(
        profile_path,
        root=tmp_path,
        trust_policy_path=policy_path,
        freshness=context,
        now=NOW,
    )

    assert report.decision == "BLOCKED"
    assert _codes(report) == {"FRESHNESS_ATTESTATION_MISSING"}


def test_unavailable_publisher_provider_blocks_before_evidence_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_path, policy_path, context = _publisher_freshness_fixture(tmp_path, monkeypatch)
    validator_module.FIXED_FRESHNESS_PROVIDER_CONFIG_PATH.unlink()
    monkeypatch.setattr(
        validator_module,
        "_check_binding",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("freshness failure must precede evidence I/O")
        ),
    )

    report = _qualify_profile_with_freshness(
        profile_path,
        root=tmp_path,
        trust_policy_path=policy_path,
        freshness=context,
        now=NOW,
    )

    assert report.decision == "BLOCKED"
    assert _codes(report) == {"FRESHNESS_PROVIDER_CONFIG_MISSING"}


def test_caller_supplied_freshness_config_and_key_cannot_establish_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_path, policy_path, context = _publisher_freshness_fixture(tmp_path, monkeypatch)
    attacker_key = Ed25519PrivateKey.generate()
    trusted_config_path = validator_module.FIXED_FRESHNESS_PROVIDER_CONFIG_PATH
    attacker_config_path = tmp_path / "caller-provider-config.json"
    attacker_config = json.loads(trusted_config_path.read_text(encoding="utf-8"))
    attacker_config["witness_public_key"] = _public_key(attacker_key)
    attacker_config_path.write_text(json.dumps(attacker_config), encoding="utf-8")
    attestation = json.loads(context.attestation_snapshot.read_text(encoding="utf-8"))
    attestation["signature"] = _signature(
        attestation["witness_key_id"],
        attacker_key,
        freshness_module.freshness_attestation_signature_message(attestation),
    )
    context.attestation_snapshot.write_text(json.dumps(attestation), encoding="utf-8")

    report = _qualify_profile_with_freshness(
        profile_path,
        root=tmp_path,
        trust_policy_path=policy_path,
        freshness=context,
        now=NOW,
    )

    assert attacker_config_path.exists()
    assert report.decision == "INVALID"
    assert _codes(report) == {"FRESHNESS_SIGNATURE_INVALID"}


def test_hidden_publisher_cli_rejects_caller_freshness_config_parameter() -> None:
    parser = validator_module._build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "validate-publisher",
                "profile.json",
                "--root",
                "evidence",
                "--trust-policy",
                "policy.json",
                "--publisher-trust-root-snapshot",
                "root.json",
                "--publisher-trust-root-snapshot-sha256",
                SHA_ONE,
                "--publisher-freshness-attestation",
                "attestation.json",
                "--publisher-freshness-challenge",
                "A" * 43,
                "--publisher-freshness-requested-at",
                NOW.isoformat(),
                "--publisher-candidate-logical-identity",
                SHA_ONE,
                "--publisher-verifier-id",
                "ruisheng.verify-publisher.test-v1",
                "--publisher-verifier-tool-sha256",
                SHA_ONE,
            ]
        )


def test_publisher_attestation_rollback_is_invalid_before_evidence_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_path, policy_path, context = _publisher_freshness_fixture(
        tmp_path,
        monkeypatch,
        attestation_state_update={"root_version": 4, "root_sha256": SHA_ZERO},
    )
    monkeypatch.setattr(
        validator_module,
        "_check_binding",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("freshness failure must precede evidence I/O")
        ),
    )

    report = _qualify_profile_with_freshness(
        profile_path,
        root=tmp_path,
        trust_policy_path=policy_path,
        freshness=context,
        now=NOW,
    )

    assert report.decision == "INVALID"
    assert _codes(report) == {"FRESHNESS_ROOT_VERSION_ROLLBACK"}


@pytest.mark.parametrize(
    ("high_water_update", "expected_decision", "expected_code"),
    (
        ({"root_sha256": SHA_ZERO}, "INVALID", "FRESHNESS_ROOT_HASH_CONFLICT"),
        ({"root_id": "other-root"}, "INVALID", "FRESHNESS_ROOT_ID_SWITCH"),
        ({"root_version": 2}, "BLOCKED", "FRESHNESS_LOCAL_STATE_AHEAD"),
        ({"policy_sha256": SHA_ZERO}, "INVALID", "FRESHNESS_POLICY_HASH_CONFLICT"),
        ({"policy_id": "other-policy"}, "INVALID", "FRESHNESS_POLICY_ID_SWITCH"),
        ({"policy_version": 1}, "BLOCKED", "FRESHNESS_LOCAL_STATE_AHEAD"),
    ),
)
def test_publisher_freshness_state_mismatch_fails_before_evidence_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    high_water_update: dict[str, Any],
    expected_decision: str,
    expected_code: str,
) -> None:
    profile_path, policy_path, context = _publisher_freshness_fixture(
        tmp_path,
        monkeypatch,
        attestation_state_update=high_water_update,
    )
    monkeypatch.setattr(
        validator_module,
        "_check_binding",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("freshness failure must precede evidence I/O")
        ),
    )

    report = _qualify_profile_with_freshness(
        profile_path,
        root=tmp_path,
        trust_policy_path=policy_path,
        freshness=context,
        now=NOW,
    )

    assert report.decision == expected_decision
    assert _codes(report) == {expected_code}


def test_publisher_freshness_replay_and_clock_rollback_are_invalid_before_evidence_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_path, policy_path, context = _publisher_freshness_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        validator_module,
        "_check_binding",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("freshness failure must precede evidence I/O")
        ),
    )

    replay = _qualify_profile_with_freshness(
        profile_path,
        root=tmp_path,
        trust_policy_path=policy_path,
        freshness=replace(context, challenge="Q" * 43),
        now=NOW,
    )
    rolled_back_clock = _qualify_profile_with_freshness(
        profile_path,
        root=tmp_path,
        trust_policy_path=policy_path,
        freshness=context,
        now=NOW - timedelta(minutes=10),
    )

    assert replay.decision == "INVALID"
    assert _codes(replay) == {"FRESHNESS_REQUEST_MISMATCH"}
    assert rolled_back_clock.decision == "INVALID"
    assert _codes(rolled_back_clock) == {"FRESHNESS_CLOCK_SKEW"}


def test_publisher_freshness_counter_rollback_is_invalid_before_evidence_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_path, policy_path, context = _publisher_freshness_fixture(
        tmp_path,
        monkeypatch,
        monotonic_counter=41,
    )
    monkeypatch.setattr(
        validator_module,
        "_check_binding",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("freshness failure must precede evidence I/O")
        ),
    )

    report = _qualify_profile_with_freshness(
        profile_path,
        root=tmp_path,
        trust_policy_path=policy_path,
        freshness=context,
        now=NOW,
    )

    assert report.decision == "INVALID"
    assert _codes(report) == {"FRESHNESS_MONOTONIC_COUNTER_ROLLBACK"}


def test_publisher_context_binds_the_root_snapshot_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_path, policy_path, context = _publisher_freshness_fixture(tmp_path, monkeypatch)
    changed = json.loads(context.trust_root_snapshot.read_text(encoding="utf-8"))
    changed["root_version"] += 1
    context.trust_root_snapshot.write_text(json.dumps(changed), encoding="utf-8")
    monkeypatch.setattr(
        validator_module,
        "_check_binding",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("freshness failure must precede evidence I/O")
        ),
    )

    report = _qualify_profile_with_freshness(
        profile_path,
        root=tmp_path,
        trust_policy_path=policy_path,
        freshness=context,
        now=NOW,
    )

    assert report.decision == "INVALID"
    assert _codes(report) == {"FRESHNESS_TRUST_ROOT_SNAPSHOT_MISMATCH"}


def test_root_reserialization_cannot_reuse_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_path, policy_path, context = _publisher_freshness_fixture(tmp_path, monkeypatch)
    value = json.loads(context.trust_root_snapshot.read_text(encoding="utf-8"))
    context.trust_root_snapshot.write_text(json.dumps(value, indent=2), encoding="utf-8")
    changed_context = replace(
        context,
        trust_root_snapshot_sha256="sha256:"
        + hashlib.sha256(context.trust_root_snapshot.read_bytes()).hexdigest(),
    )

    report = _qualify_profile_with_freshness(
        profile_path,
        root=tmp_path,
        trust_policy_path=policy_path,
        freshness=changed_context,
        now=NOW,
    )

    assert report.decision == "INVALID"
    assert _codes(report) == {"FRESHNESS_REQUEST_MISMATCH"}


def test_provider_config_reserialization_cannot_reuse_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_path, policy_path, context = _publisher_freshness_fixture(tmp_path, monkeypatch)
    config_path = validator_module.FIXED_FRESHNESS_PROVIDER_CONFIG_PATH
    value = json.loads(config_path.read_text(encoding="utf-8"))
    config_path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    report = _qualify_profile_with_freshness(
        profile_path,
        root=tmp_path,
        trust_policy_path=policy_path,
        freshness=context,
        now=NOW,
    )

    assert report.decision == "INVALID"
    assert _codes(report) == {"FRESHNESS_PROVIDER_CONFIG_SNAPSHOT_MISMATCH"}


def test_attestation_expiry_during_validator_execution_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_path, policy_path, context = _publisher_freshness_fixture(tmp_path, monkeypatch)

    report = _qualify_profile_with_freshness(
        profile_path,
        root=tmp_path,
        trust_policy_path=policy_path,
        freshness=context,
        now=NOW,
        completion_now=NOW + timedelta(minutes=5),
    )

    assert report.decision == "INVALID"
    assert _codes(report) == {"FRESHNESS_ATTESTATION_EXPIRED"}


def test_attestation_completion_clock_rollback_is_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_path, policy_path, context = _publisher_freshness_fixture(tmp_path, monkeypatch)

    report = _qualify_profile_with_freshness(
        profile_path,
        root=tmp_path,
        trust_policy_path=policy_path,
        freshness=context,
        now=NOW,
        completion_now=NOW - timedelta(seconds=1),
    )

    assert report.decision == "INVALID"
    assert _codes(report) == {"FRESHNESS_CLOCK_ROLLBACK"}


def test_attestation_monotonic_deadline_survives_wall_clock_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_path, policy_path, context = _publisher_freshness_fixture(tmp_path, monkeypatch)
    readings = iter((100.0, 401.0))
    monkeypatch.setattr(freshness_module.time, "monotonic", lambda: next(readings))

    report = _qualify_profile_with_freshness(
        profile_path,
        root=tmp_path,
        trust_policy_path=policy_path,
        freshness=context,
        now=NOW,
        completion_now=NOW,
    )

    assert report.decision == "INVALID"
    assert _codes(report) == {"FRESHNESS_ATTESTATION_EXPIRED"}


def test_freshness_preflight_exact_does_not_read_business_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile_path, policy_path, context = _publisher_freshness_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        validator_module,
        "_check_binding",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("freshness preflight must not read business artifacts")
        ),
    )

    report = freshness_module.preflight_freshness(
        **_freshness_authentication_kwargs(profile_path, policy_path, context, now=NOW)
    )

    assert report.decision == "EXACT"


@pytest.mark.parametrize(
    ("invalid_signature", "high_water_update", "decision", "reason"),
    (
        (True, None, "INVALID", "FRESHNESS_SIGNATURE_INVALID"),
        (False, {"root_version": 2}, "BLOCKED", "FRESHNESS_LOCAL_STATE_AHEAD"),
    ),
)
def test_freshness_preflight_fails_closed_without_business_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_signature: bool,
    high_water_update: dict[str, Any] | None,
    decision: str,
    reason: str,
) -> None:
    profile_path, policy_path, context = _publisher_freshness_fixture(
        tmp_path,
        monkeypatch,
        attestation_state_update=high_water_update,
    )
    if invalid_signature:
        attestation = json.loads(context.attestation_snapshot.read_text(encoding="utf-8"))
        attestation["signature"]["value"] = base64.b64encode(b"0" * 64).decode()
        context.attestation_snapshot.write_text(json.dumps(attestation), encoding="utf-8")
    monkeypatch.setattr(
        validator_module,
        "_check_binding",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("freshness preflight must not read business artifacts")
        ),
    )

    report = freshness_module.preflight_freshness(
        **_freshness_authentication_kwargs(profile_path, policy_path, context, now=NOW)
    )

    assert report.decision == decision
    assert report.reason_code == reason


@pytest.mark.parametrize(
    ("digest_argument", "reason_code"),
    (
        (
            "expected_trust_root_snapshot_sha256",
            "FRESHNESS_TRUST_ROOT_SNAPSHOT_MISMATCH",
        ),
        (
            "expected_provider_config_snapshot_sha256",
            "FRESHNESS_PROVIDER_CONFIG_SNAPSHOT_MISMATCH",
        ),
        ("expected_attestation_sha256", "FRESHNESS_ATTESTATION_SNAPSHOT_MISMATCH"),
    ),
)
def test_freshness_preflight_rejects_expected_raw_digest_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    digest_argument: str,
    reason_code: str,
) -> None:
    profile_path, policy_path, context = _publisher_freshness_fixture(tmp_path, monkeypatch)
    inputs = _freshness_authentication_kwargs(profile_path, policy_path, context, now=NOW)
    inputs[digest_argument] = SHA_ZERO

    report = freshness_module.preflight_freshness(**inputs)

    assert report.decision == "INVALID"
    assert report.reason_code == reason_code


def test_public_validator_api_cannot_accept_freshness_or_provider_parameters(
    tmp_path: Path,
) -> None:
    profile = _minimal_profile(tmp_path)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile), encoding="utf-8")

    with pytest.raises(TypeError):
        validate_profile_data(  # type: ignore[call-arg]
            profile,
            root=tmp_path,
            freshness_context=object(),
        )
    with pytest.raises(TypeError):
        validate_profile_file(  # type: ignore[call-arg]
            profile_path,
            root=tmp_path,
            freshness_context=object(),
        )


def test_public_validator_stops_before_business_artifact_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy, trust_root, _keys = _trust_contract()
    profile = _minimal_profile(tmp_path, policy=policy, trust_root=trust_root)
    monkeypatch.setattr(
        validator_module,
        "_check_binding",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("public validation must stop before business artifact I/O")
        ),
    )

    report = validate_profile_data(profile, root=tmp_path, now=NOW, trust_policy=policy)

    assert report.decision == "BLOCKED"
    assert _codes(report) == {"FRESHNESS_CONTEXT_REQUIRED"}


def test_public_validator_cli_cannot_bypass_publisher_freshness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile_path = tmp_path / "profile.json"
    policy_path = tmp_path / "policy.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_device_point_profile.py",
            "validate",
            str(profile_path),
            "--root",
            str(tmp_path),
            "--trust-policy",
            str(policy_path),
        ],
    )
    monkeypatch.setattr(
        validator_module,
        "_check_binding",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("public CLI must stop before business evidence I/O")
        ),
    )

    exit_code = validator_module.main()
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert report["decision"] == "BLOCKED"
    assert report["reasons"] == [{"code": "FRESHNESS_CONTEXT_REQUIRED", "path": "/freshness"}]


def test_authorized_policy_is_checked_for_hash_time_and_revocation(tmp_path: Path) -> None:
    policy, root, _ = _trust_contract()
    profile = _minimal_profile(tmp_path, policy=policy, trust_root=root)
    accepted = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=root,
    )
    revoked_value = root.model_dump(mode="json")
    revoked_value["status"] = "revoked"
    rejected = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=PolicyTrustRoot.model_validate(revoked_value),
    )
    assert "TRUST_POLICY_AUTHORITY_INVALID" not in _codes(accepted)
    assert rejected.decision == "INVALID"
    assert "TRUST_ROOT_REVOKED" in _codes(rejected)


@pytest.mark.parametrize(
    ("root_update", "expected_decision", "expected_code"),
    [
        ({"status": "revoked"}, "INVALID", "TRUST_ROOT_REVOKED"),
        (
            {"valid_from": "2026-08-28T00:00:00+00:00"},
            "BLOCKED",
            "TRUST_ROOT_NOT_CURRENT",
        ),
    ],
)
def test_unusable_authority_stops_before_evidence_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root_update: dict[str, object],
    expected_decision: str,
    expected_code: str,
) -> None:
    policy, root, _ = _trust_contract()
    unusable_value = root.model_dump(mode="json")
    unusable_value.update(root_update)
    unusable_root = PolicyTrustRoot.model_validate(unusable_value)
    profile = _minimal_profile(tmp_path, policy=policy, trust_root=unusable_root)

    def unexpected_binding_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("authority rejection must precede evidence I/O")

    monkeypatch.setattr(validator_module, "_check_binding", unexpected_binding_read)

    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=unusable_root,
    )

    assert report.decision == expected_decision
    assert expected_code in _codes(report)


@pytest.mark.parametrize(
    ("collection", "limit", "expected_code"),
    [
        ("points", MAX_PROFILE_POINTS, "PROFILE_POINT_LIMIT_EXCEEDED"),
        (
            "evidence_bindings",
            MAX_EVIDENCE_BINDINGS,
            "EVIDENCE_BINDING_LIMIT_EXCEEDED",
        ),
    ],
)
def test_profile_collection_limits_reject_before_model_work(
    tmp_path: Path,
    collection: str,
    limit: int,
    expected_code: str,
) -> None:
    profile = _minimal_profile(tmp_path)
    if collection == "points":
        point = profile["profile_payload"]["points"][0]
        profile["profile_payload"]["points"] = [point] * (limit + 1)
    else:
        binding = profile["evidence_bindings"][0]
        profile["evidence_bindings"] = [binding] * (limit + 1)

    report = validate_profile_data(profile, root=tmp_path, now=NOW)

    assert report.decision == "INVALID"
    assert _codes(report) == {expected_code}


def test_total_evidence_size_limit_rejects_before_evidence_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, root, _ = _trust_contract()
    profile = _minimal_profile(tmp_path, policy=policy, trust_root=root)
    binding_count = MAX_EVIDENCE_BYTES // MAX_ARTIFACT_BYTES + 1
    profile["evidence_bindings"] = [
        {
            **profile["evidence_bindings"][0],
            "evidence_id": f"LEGACY-{index}",
            "path": f"evidence/legacy-{index}.json",
            "size_bytes": MAX_ARTIFACT_BYTES,
        }
        for index in range(binding_count)
    ]

    def unexpected_binding_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("aggregate evidence rejection must precede evidence I/O")

    monkeypatch.setattr(validator_module, "_check_binding", unexpected_binding_read)

    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=root,
    )

    assert report.decision == "INVALID"
    assert _codes(report) == {"EVIDENCE_BYTES_LIMIT_EXCEEDED"}


def test_binding_read_limit_uses_declared_size_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, root, _ = _trust_contract()
    profile = _minimal_profile(tmp_path, policy=policy, trust_root=root)
    binding_path = profile["evidence_bindings"][0]["path"]
    profile["evidence_bindings"][0]["size_bytes"] = 1
    observed_limits: list[int] = []
    original_read = validator_module._read_relative_file_once

    def bounded_read(
        artifact_root: Path,
        relative: str,
        *,
        maximum_bytes: int,
    ) -> bytes:
        if relative == binding_path:
            observed_limits.append(maximum_bytes)
        return original_read(artifact_root, relative, maximum_bytes=maximum_bytes)

    monkeypatch.setattr(validator_module, "_read_relative_file_once", bounded_read)

    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=root,
    )

    assert report.decision == "INVALID"
    assert "ARTIFACT_PATH_INVALID" in _codes(report)
    assert observed_limits == [1]


def test_total_bound_artifact_limit_includes_approvals_before_artifact_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, root, _ = _trust_contract()
    profile = _minimal_profile(tmp_path, policy=policy, trust_root=root)
    template = profile["evidence_bindings"][0]
    binding_count = MAX_BOUND_ARTIFACT_BYTES // MAX_ARTIFACT_BYTES
    profile["evidence_bindings"] = [
        {
            **template,
            "evidence_id": f"LEGACY-{index}",
            "path": f"evidence/legacy-{index}.json",
            "size_bytes": MAX_ARTIFACT_BYTES,
        }
        for index in range(binding_count)
    ]
    profile["approval_binding"] = {
        "path": "approval/eligibility.json",
        "sha256": SHA_ZERO,
        "size_bytes": 1,
        "subject_gate_sha256": SHA_ZERO,
    }

    def unexpected_binding_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("aggregate artifact rejection must precede artifact I/O")

    monkeypatch.setattr(validator_module, "_check_binding", unexpected_binding_read)

    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=root,
    )

    assert report.decision == "INVALID"
    assert _codes(report) == {"ARTIFACT_BYTES_LIMIT_EXCEEDED"}


def test_additional_artifact_budget_rejects_before_nested_artifact_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = validator_module.ArtifactBinding.model_validate(
        {
            "path": "runtime/raw.json",
            "sha256": SHA_ZERO,
            "size_bytes": 1,
        }
    )
    budget = validator_module._ArtifactReadBudget(remaining_bytes=0)

    def unexpected_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("exhausted nested budget must reject before artifact I/O")

    monkeypatch.setattr(validator_module, "_read_relative_file_once", unexpected_read)

    contents, error = validator_module._check_binding(
        binding,
        tmp_path,
        additional_budget=budget,
    )

    assert contents is None
    assert error is not None
    assert error.code == "ARTIFACT_BYTES_LIMIT_EXCEEDED"


def test_validator_source_identity_is_frozen_for_the_loaded_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = current_validator_source_sha256()

    def unexpected_source_read(*args: object, **kwargs: object) -> None:
        raise AssertionError("loaded policy identity must not drift with the on-disk source")

    monkeypatch.setattr(validator_module, "_read_explicit_file_once", unexpected_source_read)

    assert current_validator_source_sha256() == expected


def test_raw_manifest_parallel_signature_contract_is_schema_invalid(tmp_path: Path) -> None:
    policy, root, _ = _trust_contract()
    profile = _minimal_profile(tmp_path, policy=policy, trust_root=root)
    profile["runtime_target"] = {
        "source_commit": "a" * 40,
        "candidate_id": "deploy-20260827.9",
        "logical_identity": SHA_ONE,
        "api_image_digest": SHA_ONE,
        "gateway_image_digest": SHA_ONE,
        "alembic_head": "head",
        "release_manifest": {
            "path": "MANIFEST.json",
            "sha256": SHA_ONE,
            "size_bytes": 1,
            "signer_id": "caller",
            "signature": _dummy_signature(),
        },
    }
    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=root,
    )
    assert report.decision == "INVALID"
    assert _codes(report) == {"SCHEMA_INVALID"}


def _attach_signed_release_receipt(
    root: Path,
    profile: dict[str, Any],
    *,
    signing_key: Ed25519PrivateKey,
    verifier_id: str = "protected-release-verifier",
    key_id: str = "release-key",
    verifier_tool_id: str = "ruisheng.release-artifacts-receipt-producer/v1",
    verifier_tool_sha256: str = SHA_ONE,
    path: str = "release/verification-receipt.json",
    release_key_fingerprint: str = PUBLISHER_FINGERPRINT,
    verified_at: str = "2026-08-27T02:00:00+00:00",
    check_override: tuple[str, str] | None = None,
    protected_snapshot_id: str | None = None,
) -> None:
    components = ["postgres", "redis", "api", "gw", "web"]
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "ruisheng.release-verification-receipt",
        "receipt_id": "pending",
        "verification_method": "openssh-sha256sums-protected-snapshot/v1",
        "verifier_id": verifier_id,
        "verifier_tool_id": verifier_tool_id,
        "verifier_tool_sha256": verifier_tool_sha256,
        "verified_at": verified_at,
        "protected_snapshot_id": "pending",
        "publisher_principal": "ruisheng-release",
        "signature_namespace": "ruisheng-candidate-v1",
        "signed_object": "SHA256SUMS",
        "signature_file": "SHA256SUMS.sig",
        "release_key_fingerprint": release_key_fingerprint,
        "sha256sums_sha256": SHA_ZERO,
        "signature_file_sha256": SHA_ONE,
        "manifest_sha256": "sha256:" + "2" * 64,
        "package_file_set_sha256": "sha256:" + "4" * 64,
        "candidate_id": "deploy-20260827.9",
        "source_commit": "a" * 40,
        "logical_identity": "sha256:" + "3" * 64,
        "alembic_head": "20260818_0012",
        "observed_alembic_head": "20260818_0012",
        "images": [
            {
                "component": component,
                "image_id": "sha256:" + f"{index + 4:x}" * 64,
                "archive_sha256": "sha256:" + f"{index + 9:x}" * 64,
                "os": "linux",
                "architecture": "amd64",
            }
            for index, component in enumerate(components)
        ],
        "checks": [],
    }
    receipt["protected_snapshot_id"] = (
        protected_snapshot_id or release_receipt_protected_snapshot_id(receipt)
    )
    receipt["receipt_id"] = f"receipt-{receipt['protected_snapshot_id'].removeprefix('sha256:')}"
    check_digests = release_receipt_check_digests(receipt)
    if check_override is not None:
        check_digests[check_override[0]] = check_override[1]
    receipt["checks"] = [
        {"check_id": check_id, "result": "PASS", "observed_sha256": digest}
        for check_id, digest in check_digests.items()
    ]
    receipt["signature"] = _release_receipt_signature(
        key_id,
        signing_key,
        release_receipt_signature_message(receipt),
    )
    binding = _write_bytes(root, path, _json_bytes(receipt))
    image_ids = {item["component"]: item["image_id"] for item in receipt["images"]}
    profile["runtime_target"] = {
        "source_commit": receipt["source_commit"],
        "candidate_id": receipt["candidate_id"],
        "logical_identity": receipt["logical_identity"],
        "api_image_digest": image_ids["api"],
        "gateway_image_digest": image_ids["gw"],
        "alembic_head": receipt["alembic_head"],
        "release_verification_receipt": {"receipt_id": receipt["receipt_id"], **binding},
    }


def _synthetic_eligible_contract(  # noqa: PLR0915 - complete cross-artifact fixture
    root_path: Path,
    *,
    receipt_verified_at: str = "2026-08-27T02:00:00+00:00",
    eligibility_approved_at: str = "2026-08-27T08:00:00+00:00",
    binary_function_code: int = 1,
    binary_runtime_check_id: str | None = None,
) -> tuple[dict[str, Any], TrustPolicy, PolicyTrustRoot]:
    if binary_function_code not in {1, 2}:
        raise ValueError("synthetic binary function code must be FC1 or FC2")
    binary_address_kind = "coil" if binary_function_code == 1 else "discrete_input"
    binary_candidate_id = (
        "selected-coil" if binary_function_code == 1 else "selected-discrete-input"
    )
    required_address_check = (
        "FC1_ADDRESS_TRANSLATION" if binary_function_code == 1 else "FC2_ADDRESS_TRANSLATION"
    )
    selected_address_check = binary_runtime_check_id or required_address_check
    if selected_address_check not in {"FC1_ADDRESS_TRANSLATION", "FC2_ADDRESS_TRANSLATION"}:
        raise ValueError("synthetic binary runtime check must be an address-translation check")
    policy, trust_root, keys = _trust_contract()
    identity = {
        "status": "resolved",
        "model": "RSC-1",
        "hardware_revision": "A",
        "firmware_version": "1.2.3",
        "point_map_version": "2026.08",
        "usb_serial_number": "SERIAL",
        "evidence_refs": ["IDENTITY", "MAP"],
    }
    line = {
        "status": "resolved",
        "stable_device_path": "/dev/serial/by-id/usb-Ruisheng_SERIAL",
        "unit_id": 1,
        "baud_rate": 9600,
        "data_bits": 8,
        "parity": "N",
        "stop_bits": 1,
        "evidence_refs": ["LINE", "RAW-ANALOG"],
    }
    points: list[dict[str, Any]] = [
        {
            "point_id": "P-ANALOG",
            "point_name": "temperature",
            "function_code": 3,
            "start_address": 10,
            "register_width": 1,
            "bit": None,
            "identity_status": "resolved",
            "semantic_status": "resolved",
            "encoding_status": "resolved",
            "unit_status": "resolved",
            "calibration_status": "resolved",
            "implementation_status": "supported",
            "encoding": {
                "value_type": "u16",
                "byte_order": "big",
                "word_order": "not_applicable",
                "raw_domain": {"minimum": 0, "maximum": 100},
            },
            "unit": "degC",
            "calibration_profile": {
                "kind": "analog",
                "method": "affine_holdout_return",
                "engineering_mapping": {"ratio": 0.1, "offset": 0.0},
            },
            "evidence_refs": ["MAP", "RAW-ANALOG", "CAL-ANALOG", "REF-ANALOG"],
        },
        {
            "point_id": "P-BINARY",
            "point_name": "door_open",
            "function_code": binary_function_code,
            "start_address": 20,
            "register_width": 1,
            "bit": None,
            "identity_status": "resolved",
            "semantic_status": "resolved",
            "encoding_status": "resolved",
            "unit_status": "resolved",
            "calibration_status": "resolved",
            "implementation_status": "supported",
            "encoding": {
                "value_type": "bit",
                "byte_order": "not_applicable",
                "word_order": "not_applicable",
                "raw_domain": {"minimum": 0, "maximum": 1},
            },
            "unit": "state",
            "calibration_profile": {
                "kind": "binary",
                "method": "state_transition",
                "inactive_raw": 0,
                "active_raw": 1,
            },
            "evidence_refs": ["MAP", "RAW-BINARY", "CAL-BINARY", "REF-BINARY"],
        },
        {
            "point_id": "P-COUNTER",
            "point_name": "pulse_count",
            "function_code": 3,
            "start_address": 30,
            "register_width": 1,
            "bit": None,
            "identity_status": "resolved",
            "semantic_status": "resolved",
            "encoding_status": "resolved",
            "unit_status": "resolved",
            "calibration_status": "resolved",
            "implementation_status": "supported",
            "encoding": {
                "value_type": "u16",
                "byte_order": "big",
                "word_order": "not_applicable",
                "raw_domain": {"minimum": 0, "maximum": 15},
            },
            "unit": "count",
            "calibration_profile": {
                "kind": "counter",
                "method": "monotonicity_rollover",
                "counts_per_unit": 1,
                "modulus": 16,
                "rollover_behavior": "wrap",
            },
            "evidence_refs": ["MAP", "RAW-COUNTER", "CAL-COUNTER", "REF-COUNTER"],
        },
    ]
    payload = {"device_identity": identity, "line_protocol": line, "points": points}
    profile: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": ARTIFACT_TYPE,
        "profile_id": "profile-synthetic-eligible",
        "created_at": "2026-08-27T00:00:00+00:00",
        "semantic_validator": SEMANTIC_VALIDATOR_ID,
        "validator_source_sha256": current_validator_source_sha256(),
        "policy_sha256": trust_policy_sha256(policy),
        "trust_root_sha256": trust_root_sha256(trust_root),
        "schema_sha256": current_schema_sha256(),
        "profile_payload": payload,
        "payload_sha256": canonical_payload_sha256(payload),
        "evidence_bindings": [],
        "calibration_run_approval_binding": None,
        "approval_binding": None,
        "runtime_target": None,
        "runtime_evidence": [],
        "contradictions": [],
    }

    plan_defaults: dict[str, Any] = {
        "sample_count_per_state": 3,
        "instrument_calibration_sha256": SHA_ONE,
        "sync_tolerance_ms": 5,
        "stability_threshold": 0.1,
        "minimum_raw_span": 1,
        "minimum_reference_span": 1,
        "absolute_tolerance": 0.5,
        "relative_tolerance": 0.01,
        "uncertainty_budget": 0.1,
        "safety_plan_id": "safety-plan",
        "operator_id": "operator",
        "raw_collector_tool_id": "ruisheng.calibration-collector/v1",
        "raw_collector_tool_sha256": SHA_ONE,
        "reference_collector_tool_id": "ruisheng.reference-collector/v1",
        "reference_collector_tool_sha256": SHA_ONE,
    }
    run_approval: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "ruisheng.device-point-profile-calibration-run-approval",
        "run_id": "run-synthetic-1",
        "subject_plan_sha256": SHA_ZERO,
        "profile_id": profile["profile_id"],
        "profile_input_sha256": canonical_calibration_profile_input_sha256(profile),
        "schema_sha256": profile["schema_sha256"],
        "policy_sha256": profile["policy_sha256"],
        "trust_root_sha256": profile["trust_root_sha256"],
        "semantic_validator": SEMANTIC_VALIDATOR_ID,
        "validator_source_sha256": current_validator_source_sha256(),
        "device_identity_sha256": canonical_device_identity_sha256(identity),
        "device_serial": identity["usb_serial_number"],
        "model": identity["model"],
        "hardware_revision": identity["hardware_revision"],
        "firmware_version": identity["firmware_version"],
        "point_map_version": identity["point_map_version"],
        "stable_device_path": line["stable_device_path"],
        "unit_id": line["unit_id"],
        "baud_rate": line["baud_rate"],
        "data_bits": line["data_bits"],
        "parity": line["parity"],
        "stop_bits": line["stop_bits"],
        "valid_from": "2026-08-27T00:00:00+00:00",
        "expires_at": "2026-08-28T00:00:00+00:00",
        "nonce": "0123456789abcdef",
        "safety_owner_present": True,
        "emergency_stop_available": True,
        "plans": [
            {
                **plan_defaults,
                "plan_id": "plan-analog",
                "point_id": "P-ANALOG",
                "point_name": "temperature",
                "point_unit": "degC",
                "function_code": 3,
                "start_address": 10,
                "register_width": 1,
                "bit": None,
                "value_type": "u16",
                "byte_order": "big",
                "word_order": "not_applicable",
                "raw_domain": {"minimum": 0, "maximum": 100},
                "calibration_kind": "analog",
                "state_ids": ["A", "B", "C", "A_RETURN"],
                "instrument_id": "instrument-analog",
                "reference_channel_id": "channel-analog",
                "reference_unit": "degC",
                "minimum_raw_span": 20,
                "minimum_reference_span": 2,
                "analog_aggregation_method": "arithmetic_mean",
                "analog_unit_conversion": {
                    "source_unit": "degC",
                    "target_unit": "degC",
                    "method": "identity",
                    "scale": 1,
                    "offset": 0,
                },
                "analog_exclusion_policy": {
                    "rule_set_id": "analog-exclusions-v1",
                    "rule_set_sha256": SHA_ONE,
                    "allowed_reason_codes": [
                        "INSTRUMENT_OUT_OF_RANGE",
                        "REFERENCE_UNCERTAINTY_EXCEEDED",
                        "SYNC_ERROR_EXCEEDED",
                        "UNSTABLE",
                    ],
                    "maximum_excluded_per_state": 1,
                },
                "analog_business_tolerance_source": {
                    "source_id": "approved-business-tolerance-v1",
                    "source_sha256": SHA_ONE,
                },
                "analog_instrument_capability": {
                    "range_minimum": -10,
                    "range_maximum": 100,
                    "resolution": 0.01,
                    "accuracy": 0.05,
                    "status": "IN_CALIBRATION",
                },
                "maximum_reference_uncertainty": 0.1,
                "return_raw_tolerance": 1,
                "return_engineering_tolerance": 0.5,
                "maximum_chatter_transitions": None,
                "expected_counter_increment": None,
                "counter_increment_tolerance": None,
                "counter_modulus": None,
                "counter_rollover_behavior": None,
                "persistence_required": False,
                "tx_scope": [
                    {
                        "function_code": 3,
                        "start_address": 10,
                        "quantity": 1,
                        "maximum_requests": 20,
                        "write_allowed": False,
                    }
                ],
            },
            {
                **plan_defaults,
                "plan_id": "plan-binary",
                "point_id": "P-BINARY",
                "point_name": "door_open",
                "point_unit": "state",
                "function_code": binary_function_code,
                "start_address": 20,
                "register_width": 1,
                "bit": None,
                "value_type": "bit",
                "byte_order": "not_applicable",
                "word_order": "not_applicable",
                "raw_domain": {"minimum": 0, "maximum": 1},
                "calibration_kind": "binary",
                "state_ids": ["INACTIVE", "ACTIVE", "RETURN"],
                "instrument_id": "instrument-binary",
                "reference_channel_id": "channel-binary",
                "reference_unit": "state",
                "return_raw_tolerance": None,
                "return_engineering_tolerance": None,
                "maximum_chatter_transitions": 0,
                "binary_selected_candidate_id": binary_candidate_id,
                "binary_address_candidates": [
                    {
                        "candidate_id": binary_candidate_id,
                        "kind": binary_address_kind,
                        "function_code": binary_function_code,
                        "start_address": 20,
                        "register_width": 1,
                        "bit": None,
                    },
                    {
                        "candidate_id": "rejected-register-bit",
                        "kind": "register_bit",
                        "function_code": 3,
                        "start_address": 1,
                        "register_width": 1,
                        "bit": 1,
                    },
                ],
                "binary_unintervened_channels": [
                    {
                        "control_id": "unintervened-channel-1",
                        "point_id": "P-CONTROL",
                        "point_name": "control_channel",
                        "address_semantics": {
                            "candidate_id": "control-whole-register",
                            "kind": "whole_register",
                            "function_code": 3,
                            "start_address": 2,
                            "register_width": 1,
                            "bit": None,
                        },
                        "expected_raw": 0,
                    }
                ],
                "expected_counter_increment": None,
                "counter_increment_tolerance": None,
                "counter_modulus": None,
                "counter_rollover_behavior": None,
                "persistence_required": False,
                "tx_scope": [
                    {
                        "function_code": binary_function_code,
                        "start_address": 20,
                        "quantity": 1,
                        "maximum_requests": 20,
                        "write_allowed": False,
                    },
                    {
                        "function_code": 3,
                        "start_address": 1,
                        "quantity": 1,
                        "maximum_requests": 1,
                        "write_allowed": False,
                    },
                    {
                        "function_code": 3,
                        "start_address": 2,
                        "quantity": 1,
                        "maximum_requests": 1,
                        "write_allowed": False,
                    },
                ],
            },
            {
                **plan_defaults,
                "plan_id": "plan-counter",
                "point_id": "P-COUNTER",
                "point_name": "pulse_count",
                "point_unit": "count",
                "function_code": 3,
                "start_address": 30,
                "register_width": 1,
                "bit": None,
                "value_type": "u16",
                "byte_order": "big",
                "word_order": "not_applicable",
                "raw_domain": {"minimum": 0, "maximum": 15},
                "calibration_kind": "counter",
                "state_ids": ["BASELINE", "INCREMENT", "ROLLOVER", "PERSISTENCE"],
                "instrument_id": "instrument-counter",
                "reference_channel_id": "channel-counter",
                "reference_unit": "count",
                "return_raw_tolerance": None,
                "return_engineering_tolerance": None,
                "maximum_chatter_transitions": None,
                "expected_counter_increment": 1,
                "counter_increment_tolerance": 0,
                "counter_modulus": 16,
                "counter_rollover_behavior": "wrap",
                "counter_persistence_method": "physical_power_disconnect",
                "minimum_power_off_duration_seconds": 600,
                "persistence_required": True,
                "tx_scope": [
                    {
                        "function_code": 3,
                        "start_address": 30,
                        "quantity": 1,
                        "maximum_requests": 20,
                        "write_allowed": False,
                    }
                ],
            },
        ],
        "approvals": [],
    }
    run_approval["subject_plan_sha256"] = canonical_calibration_plan_sha256(run_approval)
    for role in ("project_owner", "device_firmware_owner", "site_safety_owner", "test_owner"):
        entry: dict[str, Any] = {
            "role": role,
            "key_id": f"{role}-key",
            "identity": f"{role}@example.invalid",
            "approved_at": "2026-08-27T00:30:00+00:00",
        }
        entry["signature"] = _signature(
            f"{role}-key",
            keys[role],
            calibration_run_approval_signature_message(run_approval, entry),
        )
        run_approval["approvals"].append(entry)
    run_binding = _write_bytes(
        root_path, "approval/calibration-run.json", _json_bytes(run_approval)
    )
    profile["calibration_run_approval_binding"] = {
        **run_binding,
        "subject_plan_sha256": run_approval["subject_plan_sha256"],
    }

    identity_digest = canonical_device_identity_sha256(identity)
    evidence_bindings: list[dict[str, Any]] = []

    def add_evidence(
        evidence_id: str,
        role: str,
        subject_point_ids: list[str],
        content: dict[str, Any],
        *,
        observed_at: str,
        reference: bool = False,
        run_bound: bool = False,
    ) -> None:
        key_name = "reference" if reference else "evidence"
        artifact: dict[str, Any] = {
            "schema_version": 1,
            "artifact_type": "ruisheng.device-point-profile-evidence",
            "evidence_id": evidence_id,
            "role": role,
            "profile_id": profile["profile_id"],
            "device_identity_sha256": identity_digest,
            "device_serial": identity["usb_serial_number"],
            "run_id": run_approval["run_id"] if run_bound else None,
            "calibration_run_approval_sha256": run_binding["sha256"] if run_bound else None,
            "subject_point_ids": subject_point_ids,
            "observed_at": observed_at,
            "attestor_id": f"{key_name}-runner",
            "content": content,
        }
        artifact["signature"] = _signature(
            f"{key_name}-key",
            keys[key_name],
            evidence_signature_message(artifact),
        )
        binding = _write_bytes(
            root_path,
            f"evidence/{evidence_id.lower()}.json",
            _json_bytes(artifact),
        )
        evidence_bindings.append(
            {
                "evidence_id": evidence_id,
                "role": role,
                **binding,
                "media_type": "application/json",
                "subject_point_ids": subject_point_ids,
            }
        )

    add_evidence(
        "IDENTITY",
        "identity",
        [],
        {
            "kind": "identity",
            "model": identity["model"],
            "hardware_revision": identity["hardware_revision"],
            "firmware_version": identity["firmware_version"],
            "point_map_version": identity["point_map_version"],
            "usb_serial_number": identity["usb_serial_number"],
        },
        observed_at="2026-08-27T01:00:00+00:00",
    )
    add_evidence(
        "MAP",
        "authoritative_map",
        [point["point_id"] for point in points],
        {
            "kind": "authoritative_map",
            "device_model": identity["model"],
            "hardware_revision": identity["hardware_revision"],
            "firmware_version": identity["firmware_version"],
            "point_map_version": identity["point_map_version"],
            "device_identity_sha256": identity_digest,
            "device_serial": identity["usb_serial_number"],
            "points": [
                {
                    "point_id": point["point_id"],
                    "point_name": point["point_name"],
                    "unit": point["unit"],
                    "function_code": point["function_code"],
                    "start_address": point["start_address"],
                    "register_width": point["register_width"],
                    "bit": point["bit"],
                    "value_type": point["encoding"]["value_type"],
                    "byte_order": point["encoding"]["byte_order"],
                    "word_order": point["encoding"]["word_order"],
                }
                for point in points
            ],
        },
        observed_at="2026-08-27T01:00:00+00:00",
    )
    analog = _analog_evidence()
    analog.update({"point_id": "P-ANALOG", "plan_id": "plan-analog"})
    raw_analog = _raw_observation_evidence(
        analog,
        point_id="P-ANALOG",
        plan_id="plan-analog",
        run_id=run_approval["run_id"],
        unit_id=cast(int, line["unit_id"]),
        function_code=3,
        start_address=10,
        quantity=1,
        line_probe={
            "device_identity_sha256": identity_digest,
            "configuration_observed_at": "2026-08-27T00:59:59+00:00",
            "configuration_readback": {
                "kind": "posix_termios_udev",
                "observation_method": "posix_termios_readback",
                "termios": {
                    "device_node": "/dev/ttyUSB0",
                    "baud_rate": line["baud_rate"],
                    "data_bits": line["data_bits"],
                    "parity": line["parity"],
                    "stop_bits": line["stop_bits"],
                },
                "udev": {
                    "stable_device_path": line["stable_device_path"],
                    "device_node": "/dev/ttyUSB0",
                    "id_bus": "usb",
                    "id_serial_short": identity["usb_serial_number"],
                    "devlinks": [line["stable_device_path"]],
                },
            },
        },
    )
    add_evidence(
        "RAW-ANALOG",
        "raw_observation",
        ["P-ANALOG"],
        raw_analog,
        observed_at="2026-08-27T06:00:00+00:00",
        run_bound=True,
    )
    line_probe_sha256 = raw_analog["records"][1]["record_sha256"]
    add_evidence(
        "LINE",
        "line_protocol",
        [],
        {
            "kind": "line_protocol",
            "stable_device_path": line["stable_device_path"],
            "device_identity_sha256": identity_digest,
            "device_serial": identity["usb_serial_number"],
            "unit_id": line["unit_id"],
            "baud_rate": line["baud_rate"],
            "data_bits": line["data_bits"],
            "parity": line["parity"],
            "stop_bits": line["stop_bits"],
            "field_claims": [
                {
                    "field": field,
                    "observed_value": line[field],
                    "source_record_sha256": line_probe_sha256,
                }
                for field in ("unit_id", "baud_rate", "data_bits", "parity", "stop_bits")
            ],
        },
        observed_at="2026-08-27T06:01:00+00:00",
    )
    add_evidence(
        "CAL-ANALOG",
        "calibration",
        ["P-ANALOG"],
        analog,
        observed_at="2026-08-27T06:00:00+00:00",
        run_bound=True,
    )
    add_evidence(
        "REF-ANALOG",
        "reference",
        ["P-ANALOG"],
        {
            "kind": "analog_reference",
            "evidence_schema_version": 4,
            "point_id": "P-ANALOG",
            "plan_id": "plan-analog",
            "reference_state_aggregates": {
                state["state_id"]: state["aggregate_engineering"] for state in analog["states"]
            },
            "state_aggregates": {
                state["state_id"]: state["aggregate_engineering"] for state in analog["states"]
            },
            "samples": _analog_reference_samples(analog),
            "reference_id": "instrument-analog",
            "channel_id": "channel-analog",
            "calibration_certificate_sha256": SHA_ONE,
            "instrument_capability": {
                "range_minimum": -10,
                "range_maximum": 100,
                "resolution": 0.01,
                "accuracy": 0.05,
                "status": "IN_CALIBRATION",
            },
            "unit_conversion": copy.deepcopy(analog["unit_conversion"]),
            "uncertainty": 0.1,
            "reference_collector_tool_id": "ruisheng.reference-collector/v1",
            "reference_collector_tool_sha256": SHA_ONE,
            "terminal_state": "PASS",
        },
        observed_at="2026-08-27T06:00:00+00:00",
        reference=True,
        run_bound=True,
    )

    binary = _binary_evidence()
    binary.update({"point_id": "P-BINARY", "plan_id": "plan-binary"})
    binary["address_semantics"].update(
        {
            "candidate_id": binary_candidate_id,
            "kind": binary_address_kind,
            "function_code": binary_function_code,
            "start_address": 20,
        }
    )
    add_evidence(
        "RAW-BINARY",
        "raw_observation",
        ["P-BINARY"],
        _raw_observation_evidence(
            binary,
            point_id="P-BINARY",
            plan_id="plan-binary",
            run_id=run_approval["run_id"],
            unit_id=cast(int, line["unit_id"]),
            function_code=binary_function_code,
            start_address=20,
            quantity=1,
        ),
        observed_at="2026-08-27T06:00:00+00:00",
        run_bound=True,
    )
    add_evidence(
        "CAL-BINARY",
        "calibration",
        ["P-BINARY"],
        binary,
        observed_at="2026-08-27T06:00:00+00:00",
        run_bound=True,
    )
    add_evidence(
        "REF-BINARY",
        "reference",
        ["P-BINARY"],
        {
            "kind": "binary_reference",
            "evidence_schema_version": 4,
            "point_id": "P-BINARY",
            "plan_id": "plan-binary",
            "inactive_raw": 0,
            "active_raw": 1,
            "reference_id": "instrument-binary",
            "channel_id": "channel-binary",
            "calibration_certificate_sha256": SHA_ONE,
            "samples": _binary_reference_samples(binary),
            "selected_candidate_id": binary["address_semantics"]["candidate_id"],
            "rejected_candidate_ids": [
                item["candidate"]["candidate_id"] for item in binary["competing_candidate_controls"]
            ],
            "unintervened_control_ids": [
                item["control_id"] for item in binary["unintervened_channel_controls"]
            ],
            "reference_collector_tool_id": "ruisheng.reference-collector/v1",
            "reference_collector_tool_sha256": SHA_ONE,
            "terminal_state": "PASS",
        },
        observed_at="2026-08-27T06:00:00+00:00",
        reference=True,
        run_bound=True,
    )

    counter = _counter_evidence()
    counter.update({"point_id": "P-COUNTER", "plan_id": "plan-counter"})
    counter_reference_samples = _counter_reference_samples(counter)
    counter_reference_persistence = copy.deepcopy(counter["persistence_event"])
    counter_reference_persistence["post_restore_observed_at"] = counter_reference_samples[-1][
        "observed_at"
    ]
    add_evidence(
        "RAW-COUNTER",
        "raw_observation",
        ["P-COUNTER"],
        _raw_observation_evidence(
            counter,
            point_id="P-COUNTER",
            plan_id="plan-counter",
            run_id=run_approval["run_id"],
            unit_id=cast(int, line["unit_id"]),
            function_code=3,
            start_address=30,
            quantity=1,
        ),
        observed_at="2026-08-27T06:00:00+00:00",
        run_bound=True,
    )
    add_evidence(
        "CAL-COUNTER",
        "calibration",
        ["P-COUNTER"],
        counter,
        observed_at="2026-08-27T06:00:00+00:00",
        run_bound=True,
    )
    add_evidence(
        "REF-COUNTER",
        "reference",
        ["P-COUNTER"],
        {
            "kind": "counter_reference",
            "evidence_schema_version": 4,
            "point_id": "P-COUNTER",
            "plan_id": "plan-counter",
            "counts_per_unit": 1,
            "modulus": 16,
            "rollover_behavior": "wrap",
            "expected_increment": 1,
            "expected_terminal_raw": 0,
            "expected_persistence_raw": 0,
            "samples": counter_reference_samples,
            "persistence_event": counter_reference_persistence,
            "power_loss_event_id": counter["persistence_event"]["event_id"],
            "persistence_method": counter["persistence_event"]["method"],
            "reference_id": "instrument-counter",
            "channel_id": "channel-counter",
            "calibration_certificate_sha256": SHA_ONE,
            "reference_collector_tool_id": "ruisheng.reference-collector/v1",
            "reference_collector_tool_sha256": SHA_ONE,
            "terminal_state": "PASS",
        },
        observed_at="2026-08-27T06:00:00+00:00",
        reference=True,
        run_bound=True,
    )
    profile["evidence_bindings"] = evidence_bindings

    _attach_signed_release_receipt(
        root_path,
        profile,
        signing_key=keys["release"],
        verified_at=receipt_verified_at,
    )
    required_checks = set(REQUIRED_RUNTIME_ASSERTIONS) - {
        "SIGNED_DECODE_BOUNDARIES",
        "FC1_ADDRESS_TRANSLATION",
        "FC2_ADDRESS_TRANSLATION",
    }
    required_checks.add(selected_address_check)
    runtime_bindings: list[dict[str, Any]] = []
    for check_id in sorted(required_checks):
        raw_report = {
            "schema_version": 1,
            "artifact_type": "ruisheng.device-point-profile-runtime-raw-report",
            "check_id": check_id,
            "result": "PASS",
            "started_at": "2026-08-27T07:00:00+00:00",
            "completed_at": "2026-08-27T07:01:00+00:00",
            "exit_code": 0,
            "assertions": [
                {
                    "assertion_id": assertion_id,
                    "outcome": "PASS",
                    "detail": "synthetic versioned observation",
                    "expected": "required invariant",
                    "observed": "invariant held",
                    "observation_sha256": SHA_ZERO,
                }
                for assertion_id in sorted(REQUIRED_RUNTIME_ASSERTIONS[check_id])
            ],
        }
        raw_binding = _write_bytes(
            root_path,
            f"runtime/raw-{check_id.lower()}.json",
            _json_bytes(raw_report),
        )
        runtime_artifact: dict[str, Any] = {
            "schema_version": 1,
            "artifact_type": "ruisheng.device-point-profile-runtime-evidence",
            "profile_id": profile["profile_id"],
            "profile_payload_sha256": profile["payload_sha256"],
            "calibration_run_approval_sha256": run_binding["sha256"],
            "release_verification_receipt_sha256": profile["runtime_target"][
                "release_verification_receipt"
            ]["sha256"],
            "check_id": check_id,
            "result": "PASS",
            "observed_at": "2026-08-27T07:02:00+00:00",
            "runner_id": "runtime-runner",
            "tool_id": "point-profile-runtime-suite/v2",
            "tool_sha256": SHA_ONE,
            "raw_report": raw_binding,
            "runtime_target": copy.deepcopy(profile["runtime_target"]),
        }
        runtime_artifact["signature"] = _signature(
            "runner-key",
            keys["runner"],
            runtime_signature_message(runtime_artifact),
        )
        runtime_binding = _write_bytes(
            root_path,
            f"runtime/{check_id.lower()}.json",
            _json_bytes(runtime_artifact),
        )
        runtime_bindings.append({"check_id": check_id, **runtime_binding})
    profile["runtime_evidence"] = runtime_bindings

    gate_sha256 = canonical_gate_sha256(DevicePointProfile.model_validate(profile))
    eligibility_approval: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "ruisheng.device-point-profile-eligibility-approval",
        "subject_gate_sha256": gate_sha256,
        "schema_sha256": profile["schema_sha256"],
        "policy_sha256": profile["policy_sha256"],
        "trust_root_sha256": profile["trust_root_sha256"],
        "semantic_validator": SEMANTIC_VALIDATOR_ID,
        "validator_source_sha256": current_validator_source_sha256(),
        "valid_from": "2026-08-27T00:00:00+00:00",
        "expires_at": "2026-08-28T00:00:00+00:00",
        "nonce": "fedcba9876543210",
        "approvals": [],
    }
    for role in ("project_owner", "device_firmware_owner", "site_safety_owner", "test_owner"):
        entry = {
            "role": role,
            "key_id": f"{role}-key",
            "identity": f"{role}@example.invalid",
            "approved_at": eligibility_approved_at,
        }
        entry["signature"] = _signature(
            f"{role}-key",
            keys[role],
            approval_signature_message(eligibility_approval, entry),
        )
        eligibility_approval["approvals"].append(entry)
    eligibility_binding = _write_bytes(
        root_path,
        "approval/eligibility.json",
        _json_bytes(eligibility_approval),
    )
    profile["approval_binding"] = {
        **eligibility_binding,
        "subject_gate_sha256": gate_sha256,
    }
    DevicePointProfile.model_validate(profile)
    return profile, policy, trust_root


def test_complete_v3_synthetic_contract_is_eligible(tmp_path: Path) -> None:
    profile, policy, trust_root = _synthetic_eligible_contract(tmp_path)

    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=trust_root,
    )

    assert report.decision == "ELIGIBLE"
    assert report.reasons == ()


def test_complete_v3_synthetic_fc2_contract_is_eligible(tmp_path: Path) -> None:
    profile, policy, trust_root = _synthetic_eligible_contract(
        tmp_path,
        binary_function_code=2,
    )

    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=trust_root,
    )

    assert report.decision == "ELIGIBLE"
    assert report.reasons == ()


def test_fc2_profile_cannot_use_fc1_coil_runtime_evidence(tmp_path: Path) -> None:
    profile, policy, trust_root = _synthetic_eligible_contract(
        tmp_path,
        binary_function_code=2,
        binary_runtime_check_id="FC1_ADDRESS_TRANSLATION",
    )
    address_binding = next(
        binding
        for binding in profile["runtime_evidence"]
        if binding["check_id"] == "FC1_ADDRESS_TRANSLATION"
    )
    raw_report = json.loads(
        (tmp_path / "runtime/raw-fc1_address_translation.json").read_text(encoding="utf-8")
    )

    assert not any(
        binding["check_id"] == "FC2_ADDRESS_TRANSLATION" for binding in profile["runtime_evidence"]
    )
    assert address_binding["check_id"] == "FC1_ADDRESS_TRANSLATION"
    assert {assertion["assertion_id"] for assertion in raw_report["assertions"]} == {
        "COIL_ADDRESS_TRANSLATION"
    }

    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=trust_root,
    )

    assert report.decision == "BLOCKED"
    assert {(reason.code, reason.path) for reason in report.reasons} == {
        ("RUNTIME_EVIDENCE_MISSING", "/runtime_evidence/FC2_ADDRESS_TRANSLATION")
    }


def test_runtime_must_start_after_release_receipt_verification(tmp_path: Path) -> None:
    profile, policy, trust_root = _synthetic_eligible_contract(
        tmp_path,
        receipt_verified_at="2026-08-27T07:30:00+00:00",
    )

    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=trust_root,
    )

    assert report.decision == "INVALID"
    assert "RUNTIME_PRECEDES_RELEASE_RECEIPT" in _codes(report)


def test_eligibility_approval_must_follow_release_receipt(tmp_path: Path) -> None:
    profile, policy, trust_root = _synthetic_eligible_contract(
        tmp_path,
        receipt_verified_at="2026-08-27T06:30:00+00:00",
        eligibility_approved_at="2026-08-27T06:00:00+00:00",
    )

    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=trust_root,
    )

    assert report.decision == "BLOCKED"
    assert "ELIGIBILITY_APPROVAL_PRECEDES_RELEASE_RECEIPT" in _codes(report)


def _evidence_artifact_value(
    root: Path,
    profile: dict[str, Any],
    evidence_id: str,
) -> dict[str, Any]:
    binding = next(
        item for item in profile["evidence_bindings"] if item["evidence_id"] == evidence_id
    )
    return cast(
        dict[str, Any],
        json.loads((root / binding["path"]).read_text(encoding="utf-8")),
    )


def _synthetic_evidence_artifacts(
    root: Path,
    profile: dict[str, Any],
    *evidence_ids: str,
) -> dict[str, EvidenceArtifact]:
    return {
        evidence_id: EvidenceArtifact.model_validate(
            _evidence_artifact_value(root, profile, evidence_id)
        )
        for evidence_id in evidence_ids
    }


def _rechain_raw_observation(value: dict[str, Any]) -> None:
    previous_sha256 = "GENESIS"
    for sequence_number, record in enumerate(value["records"]):
        record["sequence_number"] = sequence_number
        record["previous_record_sha256"] = previous_sha256
        record["record_sha256"] = raw_observation_record_sha256(record)
        previous_sha256 = record["record_sha256"]
    value["chain_tip_sha256"] = previous_sha256


def _synthetic_counter_context(
    root: Path,
) -> tuple[
    dict[str, Any],
    DevicePointProfile,
    CalibrationRunApprovalArtifact,
    CalibrationRunApprovalBinding,
    PointProfile,
    dict[str, EvidenceArtifact],
]:
    profile_value, _, _ = _synthetic_eligible_contract(root)
    profile = DevicePointProfile.model_validate(profile_value)
    approval_binding = CalibrationRunApprovalBinding.model_validate(
        profile_value["calibration_run_approval_binding"]
    )
    approval = CalibrationRunApprovalArtifact.model_validate(
        json.loads((root / approval_binding.path).read_text(encoding="utf-8"))
    )
    point = next(point for point in profile.profile_payload.points if point.point_id == "P-COUNTER")
    artifacts = {
        evidence_id: EvidenceArtifact.model_validate(
            _evidence_artifact_value(root, profile_value, evidence_id)
        )
        for evidence_id in ("RAW-COUNTER", "CAL-COUNTER", "REF-COUNTER")
    }
    return profile_value, profile, approval, approval_binding, point, artifacts


def _counter_approval_with_plan_overrides(
    approval: CalibrationRunApprovalArtifact,
    approval_binding: CalibrationRunApprovalBinding,
    **overrides: Any,
) -> tuple[CalibrationRunApprovalArtifact, CalibrationRunApprovalBinding]:
    return _approval_with_plan_overrides(
        approval,
        approval_binding,
        point_id="P-COUNTER",
        **overrides,
    )


def _approval_with_plan_overrides(
    approval: CalibrationRunApprovalArtifact,
    approval_binding: CalibrationRunApprovalBinding,
    *,
    point_id: str,
    **overrides: Any,
) -> tuple[CalibrationRunApprovalArtifact, CalibrationRunApprovalBinding]:
    value = approval.model_dump(mode="json")
    plan = next(item for item in value["plans"] if item["point_id"] == point_id)
    plan.update(overrides)
    value["subject_plan_sha256"] = canonical_calibration_plan_sha256(value)
    return (
        CalibrationRunApprovalArtifact.model_validate(value),
        approval_binding.model_copy(update={"subject_plan_sha256": value["subject_plan_sha256"]}),
    )


@pytest.mark.parametrize(
    ("source_kind", "expected_code"),
    [
        ("unknown", "LINE_FIELD_SOURCE_RECORD_MISSING"),
        ("audit", "LINE_FIELD_SOURCE_RECORD_TYPE_MISMATCH"),
    ],
)
def test_line_claims_resolve_to_one_real_line_probe(
    tmp_path: Path,
    source_kind: str,
    expected_code: str,
) -> None:
    profile_value, _, _ = _synthetic_eligible_contract(tmp_path)
    profile = DevicePointProfile.model_validate(profile_value)
    artifacts = _synthetic_evidence_artifacts(tmp_path, profile_value, "LINE", "RAW-ANALOG")
    line_artifact = artifacts["LINE"]
    raw_content = cast(RawObservationEvidenceContent, artifacts["RAW-ANALOG"].content)
    source_sha256 = SHA_ZERO if source_kind == "unknown" else raw_content.records[0].record_sha256
    content_value = cast(LineProtocolEvidenceContent, line_artifact.content).model_dump(mode="json")
    for claim in content_value["field_claims"]:
        claim["source_record_sha256"] = source_sha256
    artifacts["LINE"] = line_artifact.model_copy(
        update={"content": LineProtocolEvidenceContent.model_validate(content_value)}
    )

    invalid, blocked = _line_protocol_evidence_reasons(
        profile.profile_payload.line_protocol,
        canonical_device_identity_sha256(profile.profile_payload.device_identity),
        profile.profile_payload.device_identity.usb_serial_number,
        artifacts,
        "/profile_payload/line_protocol",
    )

    assert expected_code in {reason.code for reason in (*invalid, *blocked)}


@pytest.mark.parametrize(
    ("field", "foreign_value", "expected_code"),
    [
        ("baud_rate", 19200, "LINE_FIELD_SOURCE_RECORD_VALUE_MISMATCH"),
        (
            "stable_device_path",
            "/dev/serial/by-id/usb-foreign",
            "LINE_FIELD_SOURCE_RECORD_SCOPE_MISMATCH",
        ),
    ],
)
def test_line_probe_must_match_resolved_value_and_device_scope(
    tmp_path: Path,
    field: str,
    foreign_value: int | str,
    expected_code: str,
) -> None:
    profile_value, _, _ = _synthetic_eligible_contract(tmp_path)
    profile = DevicePointProfile.model_validate(profile_value)
    artifacts = _synthetic_evidence_artifacts(tmp_path, profile_value, "LINE", "RAW-ANALOG")
    raw_artifact = artifacts["RAW-ANALOG"]
    raw_content = cast(RawObservationEvidenceContent, raw_artifact.content)
    records = list(raw_content.records)
    probe = cast(RawLineProbeObservationRecord, records[1])
    readback = cast(Any, probe.configuration_readback)
    if field == "baud_rate":
        readback = readback.model_copy(
            update={"termios": readback.termios.model_copy(update={"baud_rate": foreign_value})}
        )
    else:
        readback = readback.model_copy(
            update={
                "udev": readback.udev.model_copy(
                    update={
                        "stable_device_path": foreign_value,
                        "devlinks": [foreign_value],
                    }
                )
            }
        )
    records[1] = probe.model_copy(update={"configuration_readback": readback})
    artifacts["RAW-ANALOG"] = raw_artifact.model_copy(
        update={"content": raw_content.model_copy(update={"records": records})}
    )

    invalid, _ = _line_protocol_evidence_reasons(
        profile.profile_payload.line_protocol,
        canonical_device_identity_sha256(profile.profile_payload.device_identity),
        profile.profile_payload.device_identity.usb_serial_number,
        artifacts,
        "/profile_payload/line_protocol",
    )

    assert expected_code in {reason.code for reason in invalid}


def test_line_source_must_be_explicitly_referenced_and_attested_after_raw(
    tmp_path: Path,
) -> None:
    profile_value, _, _ = _synthetic_eligible_contract(tmp_path)
    profile = DevicePointProfile.model_validate(profile_value)
    artifacts = _synthetic_evidence_artifacts(tmp_path, profile_value, "LINE", "RAW-ANALOG")
    line_without_raw = profile.profile_payload.line_protocol.model_copy(
        update={"evidence_refs": ["LINE"]}
    )
    _, blocked = _line_protocol_evidence_reasons(
        line_without_raw,
        canonical_device_identity_sha256(profile.profile_payload.device_identity),
        profile.profile_payload.device_identity.usb_serial_number,
        artifacts,
        "/profile_payload/line_protocol",
    )
    assert "LINE_PROTOCOL_SOURCE_EVIDENCE_MISSING" in {reason.code for reason in blocked}

    artifacts["RAW-ANALOG"] = artifacts["RAW-ANALOG"].model_copy(
        update={"observed_at": "2026-08-27T06:02:00+00:00"}
    )
    invalid, _ = _line_protocol_evidence_reasons(
        profile.profile_payload.line_protocol,
        canonical_device_identity_sha256(profile.profile_payload.device_identity),
        profile.profile_payload.device_identity.usb_serial_number,
        artifacts,
        "/profile_payload/line_protocol",
    )
    assert "LINE_FIELD_SOURCE_RECORD_TIME_INVALID" in {reason.code for reason in invalid}


def test_hidden_conflicting_line_probe_is_invalid(tmp_path: Path) -> None:
    profile_value, _, _ = _synthetic_eligible_contract(tmp_path)
    profile = DevicePointProfile.model_validate(profile_value)
    artifacts = _synthetic_evidence_artifacts(
        tmp_path, profile_value, "LINE", "RAW-ANALOG", "RAW-BINARY"
    )
    raw_artifact = artifacts["RAW-BINARY"]
    raw_content = cast(RawObservationEvidenceContent, raw_artifact.content)
    first_exchange = raw_content.records[1]
    hidden_readback = _posix_line_readback(
        stable_device_path="/dev/serial/by-id/usb-foreign_SERIAL",
        baud_rate=19200,
    )
    hidden_probe_value = first_exchange.model_dump(mode="json") | {
        "record_type": "line_probe_observation",
        "device_identity_sha256": canonical_device_identity_sha256(
            profile.profile_payload.device_identity
        ),
        "configuration_observed_at": "2026-08-27T00:59:59+00:00",
        "configuration_readback": hidden_readback,
        "configuration_readback_sha256": line_configuration_readback_sha256(hidden_readback),
    }
    hidden_probe_value["record_sha256"] = raw_observation_record_sha256(hidden_probe_value)
    hidden_probe = RawLineProbeObservationRecord.model_validate(hidden_probe_value)
    artifacts["RAW-BINARY"] = raw_artifact.model_copy(
        update={
            "content": raw_content.model_copy(
                update={"records": [raw_content.records[0], hidden_probe]}
            )
        }
    )

    invalid, _ = _line_protocol_evidence_reasons(
        profile.profile_payload.line_protocol,
        canonical_device_identity_sha256(profile.profile_payload.device_identity),
        profile.profile_payload.device_identity.usb_serial_number,
        artifacts,
        "/profile_payload/line_protocol",
    )

    assert "LINE_PROBE_CONTRADICTION" in {reason.code for reason in invalid}


@pytest.mark.parametrize(
    "path",
    [
        "/dev/serial/by-id/../ttyS0",
        "/dev/serial/by-id/",
        "/dev/serial/by-id/not-usb",
        "/dev/serial/by-id/usb-   ",
        r"\\?\C:\temp\file",
        r"\\?\USB#   ",
        r"\\?\USB#x",
        r"\\?\USB#VID_0001&PID_0002#   ",
        r"\\?\USB#VID_0001&PID_0002#SERIAL",
        r"\\?\USB#VID_0001&PID_0002#SERIAL#{NOT-A-GUID}",
    ],
)
def test_stable_serial_path_rejects_traversal_and_windows_files(path: str) -> None:
    value = {
        "status": "resolved",
        "stable_device_path": path,
        "unit_id": 1,
        "baud_rate": 9600,
        "data_bits": 8,
        "parity": "N",
        "stop_bits": 1,
        "evidence_refs": ["LINE"],
    }

    with pytest.raises(ValidationError):
        LineProtocol.model_validate(value)


def test_reference_samples_detect_paired_value_tampering_with_unchanged_aggregate(
    tmp_path: Path,
) -> None:
    profile_value, _, _ = _synthetic_eligible_contract(tmp_path)
    profile = DevicePointProfile.model_validate(profile_value)
    point = next(item for item in profile.profile_payload.points if item.point_id == "P-ANALOG")
    artifacts = _synthetic_evidence_artifacts(
        tmp_path,
        profile_value,
        "CAL-ANALOG",
        "RAW-ANALOG",
        "REF-ANALOG",
    )
    reference_artifact = artifacts["REF-ANALOG"]
    reference = cast(AnalogReferenceEvidence, reference_artifact.content)
    samples = list(reference.samples)
    samples[0] = samples[0].model_copy(update={"reference_value": samples[0].reference_value + 1})
    samples[1] = samples[1].model_copy(update={"reference_value": samples[1].reference_value - 1})
    artifacts["REF-ANALOG"] = reference_artifact.model_copy(
        update={"content": reference.model_copy(update={"samples": samples})}
    )

    reasons = _point_calibration_reasons(
        point,
        artifacts,
        "/profile_payload/points/0",
    )

    assert "REFERENCE_SAMPLE_FACT_MISMATCH" in {reason.code for reason in reasons}


def test_binary_reference_must_independently_confirm_physical_state(tmp_path: Path) -> None:
    profile_value, _, _ = _synthetic_eligible_contract(tmp_path)
    profile = DevicePointProfile.model_validate(profile_value)
    point = next(item for item in profile.profile_payload.points if item.point_id == "P-BINARY")
    artifacts = _synthetic_evidence_artifacts(
        tmp_path,
        profile_value,
        "CAL-BINARY",
        "RAW-BINARY",
        "REF-BINARY",
    )
    reference_artifact = artifacts["REF-BINARY"]
    reference = cast(Any, reference_artifact.content)
    samples = list(reference.samples)
    active_index = next(
        index for index, sample in enumerate(samples) if sample.state_id == "ACTIVE"
    )
    samples[active_index] = samples[active_index].model_copy(update={"reference_state": "INACTIVE"})
    artifacts["REF-BINARY"] = reference_artifact.model_copy(
        update={"content": reference.model_copy(update={"samples": samples})}
    )

    reasons = _point_calibration_reasons(
        point,
        artifacts,
        "/profile_payload/points/1",
    )

    assert "REFERENCE_SAMPLE_FACT_MISMATCH" in {reason.code for reason in reasons}


def test_reference_sample_times_and_collector_are_plan_bound(tmp_path: Path) -> None:
    profile_value, policy, _ = _synthetic_eligible_contract(tmp_path)
    profile = DevicePointProfile.model_validate(profile_value)
    approval_binding = CalibrationRunApprovalBinding.model_validate(
        profile_value["calibration_run_approval_binding"]
    )
    approval = CalibrationRunApprovalArtifact.model_validate(
        json.loads((tmp_path / approval_binding.path).read_text(encoding="utf-8"))
    )
    artifacts = _synthetic_evidence_artifacts(tmp_path, profile_value, "REF-ANALOG")
    reference_artifact = artifacts["REF-ANALOG"]
    reference = cast(AnalogReferenceEvidence, reference_artifact.content)
    samples = list(reference.samples)
    samples[-1] = samples[-1].model_copy(update={"observed_at": "2026-08-27T06:00:01+00:00"})
    tampered_reference = reference.model_copy(
        update={
            "samples": samples,
            "reference_collector_tool_id": "unapproved-reference-collector/v1",
        }
    )
    artifacts["REF-ANALOG"] = reference_artifact.model_copy(update={"content": tampered_reference})
    assert max(_run_content_timestamps(tampered_reference)) > datetime.fromisoformat(
        reference_artifact.observed_at
    )

    invalid, _ = _calibration_run_approval_reasons(
        approval,
        approval_binding,
        profile,
        policy,
        current=NOW,
        evidence_artifacts=artifacts,
    )
    codes = {reason.code for reason in invalid}
    assert "RUN_EVIDENCE_AFTER_ATTESTATION" in codes
    assert "REFERENCE_PLAN_BINDING_MISMATCH" in codes


@pytest.mark.parametrize(
    ("point_id", "evidence_id", "planned_count", "observed_count", "expected_code"),
    [
        ("P-ANALOG", "CAL-ANALOG", 3, 4, "ANALOG_PLAN_THRESHOLD_MISMATCH"),
        ("P-ANALOG", "CAL-ANALOG", 4, 3, "ANALOG_PLAN_THRESHOLD_MISMATCH"),
        ("P-BINARY", "CAL-BINARY", 3, 4, "BINARY_PLAN_THRESHOLD_MISMATCH"),
        ("P-BINARY", "CAL-BINARY", 4, 3, "BINARY_PLAN_THRESHOLD_MISMATCH"),
    ],
)
def test_analog_and_binary_runs_require_exactly_the_approved_sample_count(
    tmp_path: Path,
    point_id: str,
    evidence_id: str,
    planned_count: int,
    observed_count: int,
    expected_code: str,
) -> None:
    profile_value, _, _ = _synthetic_eligible_contract(tmp_path)
    profile = DevicePointProfile.model_validate(profile_value)
    binding = CalibrationRunApprovalBinding.model_validate(
        profile_value["calibration_run_approval_binding"]
    )
    approval = CalibrationRunApprovalArtifact.model_validate(
        json.loads((tmp_path / binding.path).read_text(encoding="utf-8"))
    )
    approval, binding = _approval_with_plan_overrides(
        approval,
        binding,
        point_id=point_id,
        sample_count_per_state=planned_count,
    )
    artifact_value = _evidence_artifact_value(tmp_path, profile_value, evidence_id)
    if observed_count == 4:
        for state in artifact_value["content"]["states"]:
            extra = copy.deepcopy(state["samples"][-1])
            extra["sample_id"] += "-extra"
            extra["observed_at"] = (
                datetime.fromisoformat(extra["observed_at"]) + timedelta(minutes=1)
            ).isoformat()
            state["samples"].append(extra)
    artifact = EvidenceArtifact.model_validate(artifact_value)

    invalid, _ = _calibration_run_approval_reasons(
        approval,
        binding,
        profile,
        None,
        current=NOW,
        evidence_artifacts={evidence_id: artifact},
    )

    assert expected_code in {reason.code for reason in invalid}


def test_non_identity_affine_reference_units_are_accepted(tmp_path: Path) -> None:
    profile_value, _, _ = _synthetic_eligible_contract(tmp_path)
    profile = DevicePointProfile.model_validate(profile_value)
    point = next(item for item in profile.profile_payload.points if item.point_id == "P-ANALOG")
    binding = CalibrationRunApprovalBinding.model_validate(
        profile_value["calibration_run_approval_binding"]
    )
    approval = CalibrationRunApprovalArtifact.model_validate(
        json.loads((tmp_path / binding.path).read_text(encoding="utf-8"))
    )
    conversion = {
        "source_unit": "degF",
        "target_unit": "degC",
        "method": "affine",
        "scale": 5 / 9,
        "offset": -160 / 9,
    }
    approval, binding = _approval_with_plan_overrides(
        approval,
        binding,
        point_id="P-ANALOG",
        reference_unit="degF",
        analog_unit_conversion=conversion,
    )
    calibration_value = _evidence_artifact_value(tmp_path, profile_value, "CAL-ANALOG")
    calibration_value["content"]["unit_conversion"] = conversion
    reference_by_sample: dict[str, float] = {}
    source_aggregates: dict[str, float] = {}
    for state in calibration_value["content"]["states"]:
        source_aggregates[state["state_id"]] = state["aggregate_engineering"] * 9 / 5 + 32
        for sample in state["samples"]:
            sample["reference_value"] = sample["engineering"] * 9 / 5 + 32
            reference_by_sample[sample["sample_id"]] = sample["reference_value"]
    reference_value = _evidence_artifact_value(tmp_path, profile_value, "REF-ANALOG")
    reference_value["content"]["unit_conversion"] = conversion
    reference_value["content"]["reference_state_aggregates"] = source_aggregates
    for sample in reference_value["content"]["samples"]:
        sample["reference_value"] = reference_by_sample[sample["sample_id"]]
        sample["unit"] = "degF"
    artifacts = _synthetic_evidence_artifacts(tmp_path, profile_value, "RAW-ANALOG")
    artifacts["CAL-ANALOG"] = EvidenceArtifact.model_validate(calibration_value)
    artifacts["REF-ANALOG"] = EvidenceArtifact.model_validate(reference_value)

    reasons = _point_calibration_reasons(
        point,
        artifacts,
        "/profile_payload/points/P-ANALOG",
        point_plan=next(plan for plan in approval.plans if plan.point_id == "P-ANALOG"),
    )
    invalid, blocked = _calibration_run_approval_reasons(
        approval,
        binding,
        profile,
        None,
        current=NOW,
        evidence_artifacts=artifacts,
    )

    assert reasons == []
    assert invalid == []
    assert blocked == []


@pytest.mark.parametrize(
    ("point_id", "foreign_unit"), [("P-BINARY", "count"), ("P-COUNTER", "state")]
)
def test_non_analog_reference_unit_must_equal_point_unit(
    tmp_path: Path,
    point_id: str,
    foreign_unit: str,
) -> None:
    profile_value, _, _ = _synthetic_eligible_contract(tmp_path)
    binding = CalibrationRunApprovalBinding.model_validate(
        profile_value["calibration_run_approval_binding"]
    )
    approval_value = json.loads((tmp_path / binding.path).read_text(encoding="utf-8"))
    plan = next(item for item in approval_value["plans"] if item["point_id"] == point_id)
    plan["reference_unit"] = foreign_unit

    with pytest.raises(ValidationError):
        CalibrationRunApprovalArtifact.model_validate(approval_value)


def test_resolved_calibration_requires_trusted_raw_observation(tmp_path: Path) -> None:
    profile, policy, trust_root = _synthetic_eligible_contract(tmp_path)
    profile["profile_payload"]["points"][0]["evidence_refs"].remove("RAW-ANALOG")
    profile["payload_sha256"] = canonical_payload_sha256(profile["profile_payload"])

    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=trust_root,
    )

    assert report.decision != "ELIGIBLE"
    assert "RAW_OBSERVATION_EVIDENCE_INCOMPLETE" in _codes(report)


def test_raw_observation_artifact_rejects_cross_point_subject(tmp_path: Path) -> None:
    profile, _, _ = _synthetic_eligible_contract(tmp_path)
    artifact = _evidence_artifact_value(tmp_path, profile, "RAW-ANALOG")
    artifact["subject_point_ids"] = ["P-BINARY"]

    with pytest.raises(ValidationError):
        EvidenceArtifact.model_validate(artifact)


@pytest.mark.parametrize(
    ("field", "foreign_value"),
    [
        ("point_id", "P-BINARY"),
        ("plan_id", "plan-binary"),
        ("run_id", "run-foreign"),
    ],
)
def test_raw_observation_records_reject_cross_scope(
    tmp_path: Path,
    field: str,
    foreign_value: str,
) -> None:
    profile, _, _ = _synthetic_eligible_contract(tmp_path)
    content = _evidence_artifact_value(tmp_path, profile, "RAW-ANALOG")["content"]
    content["records"][1][field] = foreign_value

    with pytest.raises(ValidationError):
        RawObservationEvidenceContent.model_validate(content)


def test_raw_observation_content_run_must_match_artifact_run(tmp_path: Path) -> None:
    profile, _, _ = _synthetic_eligible_contract(tmp_path)
    artifact = _evidence_artifact_value(tmp_path, profile, "RAW-ANALOG")
    artifact["content"]["run_id"] = "run-foreign"
    for record in artifact["content"]["records"]:
        record["run_id"] = "run-foreign"
    _rechain_raw_observation(artifact["content"])

    with pytest.raises(ValidationError):
        EvidenceArtifact.model_validate(artifact)


def test_calibration_approval_rejects_cross_plan_raw_observation(tmp_path: Path) -> None:
    profile_value, policy, _ = _synthetic_eligible_contract(tmp_path)
    profile = DevicePointProfile.model_validate(profile_value)
    approval_binding = CalibrationRunApprovalBinding.model_validate(
        profile_value["calibration_run_approval_binding"]
    )
    approval = CalibrationRunApprovalArtifact.model_validate(
        json.loads((tmp_path / approval_binding.path).read_text(encoding="utf-8"))
    )
    raw_value = _evidence_artifact_value(tmp_path, profile_value, "RAW-ANALOG")
    raw_value["content"]["plan_id"] = "plan-binary"
    for record in raw_value["content"]["records"]:
        record["plan_id"] = "plan-binary"
    _rechain_raw_observation(raw_value["content"])
    raw_artifact = EvidenceArtifact.model_validate(raw_value)

    invalid, _ = _calibration_run_approval_reasons(
        approval,
        approval_binding,
        profile,
        policy,
        current=NOW,
        evidence_artifacts={"RAW-ANALOG": raw_artifact},
    )

    assert "RUN_EVIDENCE_PLAN_MISMATCH" in {reason.code for reason in invalid}


@pytest.mark.parametrize(
    "malformation", ["legacy", "arbitrary", "truncated", "broken_chain", "bad_crc"]
)
def test_raw_observation_rejects_malformed_audit_chain(
    tmp_path: Path,
    malformation: str,
) -> None:
    profile, _, _ = _synthetic_eligible_contract(tmp_path)
    content = _evidence_artifact_value(tmp_path, profile, "RAW-ANALOG")["content"]
    if malformation == "legacy":
        content["evidence_schema_version"] = 2
    elif malformation == "arbitrary":
        content["records"][1] = {"arbitrary": "dictionary"}
    elif malformation == "truncated":
        content["records"].pop()
    elif malformation == "broken_chain":
        content["records"][1]["previous_record_sha256"] = SHA_ZERO
    else:
        response = content["records"][1]["response_rtu_hex"]
        content["records"][1]["response_rtu_hex"] = response[:-2] + "00"

    with pytest.raises(ValidationError):
        RawObservationEvidenceContent.model_validate(content)


@pytest.mark.parametrize("duplicate_at", ["start", "completed"])
def test_raw_observation_rejects_duplicate_timestamps(
    tmp_path: Path,
    duplicate_at: str,
) -> None:
    profile, _, _ = _synthetic_eligible_contract(tmp_path)
    content = _evidence_artifact_value(tmp_path, profile, "RAW-ANALOG")["content"]
    if duplicate_at == "start":
        content["records"][1]["observed_at"] = content["records"][0]["observed_at"]
    else:
        content["records"][-1]["observed_at"] = content["records"][-2]["observed_at"]
    _rechain_raw_observation(content)

    with pytest.raises(ValidationError):
        RawObservationEvidenceContent.model_validate(content)


def test_raw_observation_rejects_nonzero_modbus_bit_padding() -> None:
    binary = _binary_evidence()
    binary["address_semantics"].update(
        {
            "candidate_id": "selected-coil",
            "kind": "coil",
            "function_code": 1,
            "start_address": 20,
        }
    )
    value = _raw_observation_evidence(
        binary,
        point_id="P-BINARY",
        plan_id="plan-binary",
        run_id="run-1",
        unit_id=1,
        function_code=1,
        start_address=20,
        quantity=1,
    )
    record = next(item for item in value["records"] if item["record_type"] == "modbus_observation")
    record["response_rtu_hex"] = _rtu_hex(bytes((1, 1, 1, 0xFF)))
    _rechain_raw_observation(value)

    with pytest.raises(ValidationError):
        RawObservationEvidenceContent.model_validate(value)


@pytest.mark.parametrize("boundary", ["unit", "encoding", "line"])
def test_pre_run_approval_freezes_payload_and_line_scope(
    tmp_path: Path,
    boundary: str,
) -> None:
    profile, policy, trust_root = _synthetic_eligible_contract(tmp_path)
    if boundary == "unit":
        profile["profile_payload"]["points"][0]["unit"] = "psi"
    elif boundary == "encoding":
        profile["profile_payload"]["points"][0]["encoding"]["value_type"] = "s16"
    else:
        profile["profile_payload"]["line_protocol"]["baud_rate"] = 19200
    profile["payload_sha256"] = canonical_payload_sha256(profile["profile_payload"])

    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=trust_root,
    )

    assert report.decision == "INVALID"
    assert "CALIBRATION_APPROVAL_SCOPE_MISMATCH" in _codes(report)


def test_all_points_must_share_the_approved_run_id(tmp_path: Path) -> None:
    profile_value, policy, _ = _synthetic_eligible_contract(tmp_path)
    profile = DevicePointProfile.model_validate(profile_value)
    approval_binding = CalibrationRunApprovalBinding.model_validate(
        profile_value["calibration_run_approval_binding"]
    )
    approval = CalibrationRunApprovalArtifact.model_validate(
        json.loads((tmp_path / approval_binding.path).read_text(encoding="utf-8"))
    )
    evidence_artifacts: dict[str, EvidenceArtifact] = {}
    for binding in profile.evidence_bindings:
        if binding.role not in {"calibration", "reference"}:
            continue
        evidence_artifacts[binding.evidence_id] = EvidenceArtifact.model_validate(
            json.loads((tmp_path / binding.path).read_text(encoding="utf-8"))
        )
    evidence_artifacts["CAL-BINARY"] = evidence_artifacts["CAL-BINARY"].model_copy(
        update={"run_id": "different-batch"}
    )

    invalid, _ = _calibration_run_approval_reasons(
        approval,
        approval_binding,
        profile,
        policy,
        current=NOW,
        evidence_artifacts=evidence_artifacts,
    )

    assert "RUN_APPROVAL_BINDING_MISMATCH" in {reason.code for reason in invalid}


@pytest.mark.parametrize(
    ("boundary", "expected_code"),
    [
        ("payload", "PAYLOAD_HASH_MISMATCH"),
        ("trust_root", "TRUST_ROOT_HASH_MISMATCH"),
        ("calibration_plan", "CALIBRATION_PLAN_BINDING_MISMATCH"),
        ("release_receipt", "ARTIFACT_HASH_MISMATCH"),
        ("runtime", "ARTIFACT_HASH_MISMATCH"),
        ("eligibility_gate", "APPROVAL_GATE_MISMATCH"),
    ],
)
def test_complete_v3_contract_tampering_fails_closed(
    tmp_path: Path,
    boundary: str,
    expected_code: str,
) -> None:
    profile, policy, trust_root = _synthetic_eligible_contract(tmp_path)
    if boundary == "payload":
        profile["profile_payload"]["points"][0]["point_name"] = "tampered"
    elif boundary == "trust_root":
        profile["trust_root_sha256"] = SHA_ZERO
    elif boundary == "calibration_plan":
        profile["calibration_run_approval_binding"]["subject_plan_sha256"] = SHA_ZERO
    elif boundary == "release_receipt":
        profile["runtime_target"]["release_verification_receipt"]["sha256"] = SHA_ZERO
    elif boundary == "runtime":
        profile["runtime_evidence"][0]["sha256"] = SHA_ZERO
    else:
        profile["approval_binding"]["subject_gate_sha256"] = SHA_ZERO

    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=trust_root,
    )

    assert report.decision != "ELIGIBLE"
    assert expected_code in _codes(report)


def test_root_authorized_signed_release_receipt_has_no_release_blocker(tmp_path: Path) -> None:
    policy, root, keys = _trust_contract()
    profile = _minimal_profile(tmp_path, policy=policy, trust_root=root)
    _attach_signed_release_receipt(
        tmp_path,
        profile,
        signing_key=keys["release"],
    )
    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=root,
    )
    assert report.decision == "BLOCKED"
    assert not {code for code in _codes(report) if code.startswith("RELEASE_")}


@pytest.mark.parametrize(
    ("receipt_kwargs", "expected_code"),
    [
        (
            {"release_key_fingerprint": "SHA256:" + "B" * 43},
            "RELEASE_PUBLISHER_KEY_UNTRUSTED",
        ),
        (
            {"verifier_tool_sha256": "sha256:" + "b" * 64},
            "RELEASE_VERIFIER_TOOL_MISMATCH",
        ),
        (
            {
                "check_override": (
                    "PACKAGE_HASHES_VERIFIED",
                    "sha256:" + "f" * 64,
                )
            },
            "RELEASE_VERIFICATION_RECEIPT_CHECK_MISMATCH",
        ),
        (
            {"protected_snapshot_id": "sha256:" + "f" * 64},
            "RELEASE_VERIFICATION_SNAPSHOT_MISMATCH",
        ),
        (
            {"verified_at": "2026-07-31T23:59:59+00:00"},
            "RELEASE_RECEIPT_OUTSIDE_POLICY_WINDOW",
        ),
    ],
)
def test_release_receipt_facts_must_match_the_authorized_verification_contract(
    tmp_path: Path,
    receipt_kwargs: dict[str, Any],
    expected_code: str,
) -> None:
    policy, root, keys = _trust_contract()
    profile = _minimal_profile(tmp_path, policy=policy, trust_root=root)
    _attach_signed_release_receipt(
        tmp_path,
        profile,
        signing_key=keys["release"],
        **receipt_kwargs,
    )

    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=root,
    )

    assert report.decision == "INVALID"
    assert expected_code in _codes(report)


def test_release_receipt_rejects_legacy_raw_ed25519_signature_shape(tmp_path: Path) -> None:
    policy, root, keys = _trust_contract()
    profile = _minimal_profile(tmp_path, policy=policy, trust_root=root)
    _attach_signed_release_receipt(tmp_path, profile, signing_key=keys["release"])
    receipt_path = tmp_path / profile["runtime_target"]["release_verification_receipt"]["path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["signature"] = _signature(
        "release-key",
        keys["release"],
        release_receipt_signature_message(receipt),
    )
    binding = _write_bytes(
        tmp_path,
        profile["runtime_target"]["release_verification_receipt"]["path"],
        _json_bytes(receipt),
    )
    profile["runtime_target"]["release_verification_receipt"].update(binding)

    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=root,
    )

    assert report.decision == "INVALID"
    assert "RELEASE_VERIFICATION_RECEIPT_INVALID" in _codes(report)


def test_caller_self_signed_receipt_under_unauthorized_policy_is_invalid(tmp_path: Path) -> None:
    trusted_policy, root, _ = _trust_contract()
    caller_authority = Ed25519PrivateKey.generate()
    caller_release = Ed25519PrivateKey.generate()
    policy_value = trusted_policy.model_dump(mode="json")
    policy_value.update(
        {
            "policy_id": "caller-controlled-policy",
            "authority_id": "caller-controlled-authority",
            "release_verifier_keys": [
                {
                    "verifier_id": "caller-release-verifier",
                    "key_id": "caller-release-key",
                    "public_key": _public_key(caller_release),
                    "tool_id": "caller-release-tool/v1",
                    "tool_sha256": SHA_ZERO,
                    "publisher_key_fingerprints": [PUBLISHER_FINGERPRINT],
                    "valid_from": "2026-08-01T00:00:00+00:00",
                    "expires_at": "2026-09-01T00:00:00+00:00",
                    "revocation_sequence": 7,
                    "status": "active",
                }
            ],
        }
    )
    policy_value["authority_signature"] = _signature(
        "caller-authority-key",
        caller_authority,
        trust_policy_signature_message(policy_value),
    )
    caller_policy = TrustPolicy.model_validate(policy_value)
    profile = _minimal_profile(tmp_path, policy=caller_policy, trust_root=root)
    _attach_signed_release_receipt(
        tmp_path,
        profile,
        signing_key=caller_release,
        verifier_id="caller-release-verifier",
        key_id="caller-release-key",
        verifier_tool_id="caller-release-tool/v1",
        verifier_tool_sha256=SHA_ZERO,
    )

    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=caller_policy,
        trust_root=root,
    )

    assert report.decision == "INVALID"
    assert "TRUST_POLICY_NOT_AUTHORIZED" in _codes(report)
    assert "TRUST_POLICY_AUTHORITY_INVALID" in _codes(report)
    assert "RELEASE_VERIFICATION_RECEIPT_SIGNATURE_INVALID" not in _codes(report)


@pytest.mark.parametrize(
    "stable_path",
    [
        "/dev/serial/by-id/usb-Ruisheng_001",
        WINDOWS_USB_INTERFACE_PATH,
        WINDOWS_USB_INTERFACE_PATH.lower(),
    ],
)
def test_stable_device_path_accepts_posix_and_windows(stable_path: str) -> None:
    line = LineProtocol.model_validate(
        {
            "status": "resolved",
            "stable_device_path": stable_path,
            "unit_id": 1,
            "baud_rate": 9600,
            "data_bits": 8,
            "parity": "N",
            "stop_bits": 1,
            "evidence_refs": ["LINE"],
        }
    )
    assert line.stable_device_path == stable_path


@pytest.mark.parametrize("platform", ["posix", "windows"])
def test_line_probe_accepts_typed_platform_readback(tmp_path: Path, platform: str) -> None:
    profile, _, _ = _synthetic_eligible_contract(tmp_path)
    raw_value = _evidence_artifact_value(tmp_path, profile, "RAW-ANALOG")["content"]
    line_probe = next(
        record
        for record in raw_value["records"]
        if record["record_type"] == "line_probe_observation"
    )
    readback = _posix_line_readback() if platform == "posix" else _windows_line_readback()
    line_probe["configuration_readback"] = readback
    line_probe["configuration_readback_sha256"] = line_configuration_readback_sha256(readback)
    _rechain_raw_observation(raw_value)

    parsed = RawObservationEvidenceContent.model_validate(raw_value)
    parsed_probe = next(
        record for record in parsed.records if record.record_type == "line_probe_observation"
    )
    assert (
        parsed_probe.stable_device_path
        == readback["udev" if platform == "posix" else "setupapi"][
            "stable_device_path" if platform == "posix" else "device_interface_path"
        ]
    )


@pytest.mark.parametrize(
    "readback",
    [
        _posix_line_readback(stable_device_path=WINDOWS_USB_INTERFACE_PATH),
        _windows_line_readback(stable_device_path="/dev/serial/by-id/usb-Ruisheng_SERIAL"),
    ],
)
def test_line_probe_rejects_readback_payload_for_the_wrong_platform(
    tmp_path: Path,
    readback: dict[str, Any],
) -> None:
    profile, _, _ = _synthetic_eligible_contract(tmp_path)
    raw_value = _evidence_artifact_value(tmp_path, profile, "RAW-ANALOG")["content"]
    line_probe = next(
        record
        for record in raw_value["records"]
        if record["record_type"] == "line_probe_observation"
    )
    line_probe["configuration_readback"] = readback
    line_probe["configuration_readback_sha256"] = line_configuration_readback_sha256(readback)
    _rechain_raw_observation(raw_value)

    with pytest.raises(ValidationError):
        RawObservationEvidenceContent.model_validate(raw_value)


@pytest.mark.parametrize(
    ("platform", "mutation"),
    [
        ("posix", "termios_value"),
        ("posix", "serial"),
        ("posix", "device_node"),
        ("windows", "dcb_value"),
        ("windows", "serial"),
        ("windows", "instance_id"),
        ("windows", "hardware_id"),
    ],
)
def test_line_probe_rejects_tampered_or_incoherent_readback(
    tmp_path: Path,
    platform: str,
    mutation: str,
) -> None:
    profile, _, _ = _synthetic_eligible_contract(tmp_path)
    raw_value = _evidence_artifact_value(tmp_path, profile, "RAW-ANALOG")["content"]
    line_probe = next(
        record
        for record in raw_value["records"]
        if record["record_type"] == "line_probe_observation"
    )
    readback = _posix_line_readback() if platform == "posix" else _windows_line_readback()
    if mutation in {"termios_value", "dcb_value"}:
        readback["termios" if platform == "posix" else "dcb"]["baud_rate"] = 19200
        line_probe["configuration_readback"] = readback
        line_probe["configuration_readback_sha256"] = SHA_ZERO
    elif platform == "posix" and mutation == "serial":
        readback["udev"]["id_serial_short"] = "OTHER"
        line_probe["configuration_readback"] = readback
        line_probe["configuration_readback_sha256"] = line_configuration_readback_sha256(readback)
    elif platform == "posix":
        readback["udev"]["device_node"] = "/dev/ttyUSB1"
        line_probe["configuration_readback"] = readback
        line_probe["configuration_readback_sha256"] = line_configuration_readback_sha256(readback)
    elif mutation == "serial":
        readback["setupapi"]["serial_number"] = "OTHER"
        line_probe["configuration_readback"] = readback
        line_probe["configuration_readback_sha256"] = line_configuration_readback_sha256(readback)
    elif mutation == "instance_id":
        readback["setupapi"]["device_instance_id"] = r"USB\VID_0001&PID_0002\OTHER"
        line_probe["configuration_readback"] = readback
        line_probe["configuration_readback_sha256"] = line_configuration_readback_sha256(readback)
    else:
        readback["setupapi"]["hardware_ids"] = [r"USB\VID_FFFF&PID_FFFF"]
        line_probe["configuration_readback"] = readback
        line_probe["configuration_readback_sha256"] = line_configuration_readback_sha256(readback)
    _rechain_raw_observation(raw_value)

    with pytest.raises(ValidationError):
        RawObservationEvidenceContent.model_validate(raw_value)


def test_unstable_path_and_incomplete_line_claims_are_rejected() -> None:
    with pytest.raises(ValidationError):
        LineProtocol.model_validate(
            {
                "status": "resolved",
                "stable_device_path": "COM3",
                "unit_id": 1,
                "baud_rate": 9600,
                "data_bits": 8,
                "parity": "N",
                "stop_bits": 1,
                "evidence_refs": ["LINE"],
            }
        )
    with pytest.raises(ValidationError):
        LineProtocolEvidenceContent.model_validate(
            {
                "kind": "line_protocol",
                "stable_device_path": "/dev/serial/by-id/x",
                "device_identity_sha256": SHA_ZERO,
                "device_serial": "SERIAL",
                "unit_id": 1,
                "baud_rate": 9600,
                "data_bits": 8,
                "parity": "N",
                "stop_bits": 1,
                "field_claims": [],
            }
        )


def test_authoritative_map_requires_name_unit_and_device_firmware_scope() -> None:
    value: dict[str, Any] = {
        "kind": "authoritative_map",
        "device_model": "RSC-1",
        "hardware_revision": "A",
        "firmware_version": "1.2.3",
        "point_map_version": "2026.08",
        "device_identity_sha256": SHA_ZERO,
        "device_serial": "SERIAL",
        "points": [
            {
                "point_id": "P1",
                "point_name": "temperature",
                "unit": "degC",
                "function_code": 3,
                "start_address": 1,
                "register_width": 1,
                "bit": None,
                "value_type": "s16",
                "byte_order": "big",
                "word_order": "not_applicable",
            }
        ],
    }
    assert AuthoritativeMapEvidenceContent.model_validate(value).points[0].unit == "degC"
    del value["points"][0]["unit"]
    with pytest.raises(ValidationError):
        AuthoritativeMapEvidenceContent.model_validate(value)


def test_point_map_scope_must_match_profile_device_and_firmware(tmp_path: Path) -> None:
    policy, trust_root, keys = _trust_contract()
    profile = _minimal_profile(tmp_path, policy=policy, trust_root=trust_root)
    identity = profile["profile_payload"]["device_identity"]
    identity.update(
        {
            "status": "resolved",
            "model": "RSC-1",
            "hardware_revision": "A",
            "firmware_version": "1.2.3",
            "point_map_version": "2026.08",
            "usb_serial_number": "SERIAL",
            "evidence_refs": ["BAD_MAP"],
        }
    )
    identity_digest = canonical_device_identity_sha256(identity)
    artifact: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "ruisheng.device-point-profile-evidence",
        "evidence_id": "BAD_MAP",
        "role": "authoritative_map",
        "profile_id": profile["profile_id"],
        "device_identity_sha256": identity_digest,
        "device_serial": "SERIAL",
        "run_id": None,
        "calibration_run_approval_sha256": None,
        "subject_point_ids": ["P1"],
        "observed_at": "2026-08-27T02:00:00+00:00",
        "attestor_id": "evidence-runner",
        "content": {
            "kind": "authoritative_map",
            "device_model": "RSC-1",
            "hardware_revision": "A",
            "firmware_version": "9.9.9",
            "point_map_version": "2026.08",
            "device_identity_sha256": identity_digest,
            "device_serial": "SERIAL",
            "points": [
                {
                    "point_id": "P1",
                    "point_name": "candidate",
                    "unit": "degC",
                    "function_code": 3,
                    "start_address": 1,
                    "register_width": 1,
                    "bit": None,
                    "value_type": "u16",
                    "byte_order": "big",
                    "word_order": "not_applicable",
                }
            ],
        },
    }
    artifact["signature"] = _signature(
        "evidence-key",
        keys["evidence"],
        evidence_signature_message(artifact),
    )
    binding = _write_bytes(tmp_path, "evidence/bad-map.json", _json_bytes(artifact))
    profile["evidence_bindings"].append(
        {
            "evidence_id": "BAD_MAP",
            "role": "authoritative_map",
            **binding,
            "media_type": "application/json",
            "subject_point_ids": ["P1"],
        }
    )
    profile["profile_payload"]["points"][0]["evidence_refs"].append("BAD_MAP")
    profile["payload_sha256"] = canonical_payload_sha256(profile["profile_payload"])

    report = _validate_profile_data_with_trusted_context(
        profile,
        root=tmp_path,
        now=NOW,
        trust_policy=policy,
        trust_root=trust_root,
    )

    assert report.decision == "INVALID"
    assert "AUTHORITATIVE_MAP_SCOPE_MISMATCH" in _codes(report)


def test_analog_requires_three_samples_fresh_states_and_derived_mapping() -> None:
    value = _analog_evidence()
    assert AnalogCalibrationEvidence.model_validate(value).ratio == 0.1
    insufficient = copy.deepcopy(value)
    insufficient["states"][2]["samples"] = insufficient["states"][2]["samples"][:2]
    with pytest.raises(ValidationError):
        AnalogCalibrationEvidence.model_validate(insufficient)
    posthoc = copy.deepcopy(value)
    posthoc["ratio"] = 0.2
    posthoc["thresholds"]["absolute_tolerance"] = 1000
    with pytest.raises(ValidationError):
        AnalogCalibrationEvidence.model_validate(posthoc)


def test_analog_v3_requires_policy_bound_units_exclusions_and_instrument_limits() -> None:
    value = _analog_evidence()
    legacy = copy.deepcopy(value)
    legacy["evidence_schema_version"] = 2
    with pytest.raises(ValidationError):
        AnalogCalibrationEvidence.model_validate(legacy)

    bad_conversion = copy.deepcopy(value)
    bad_conversion["states"][2]["samples"][0]["engineering"] = 99
    with pytest.raises(ValidationError):
        AnalogCalibrationEvidence.model_validate(bad_conversion)

    unapproved_exclusion = copy.deepcopy(value)
    unapproved_exclusion["exclusion_log"] = [
        {
            "sample_id": "excluded-A-1",
            "state_id": "A",
            "event_id": "event-A",
            "observed_at": "2026-08-27T01:01:30+00:00",
            "raw": 0,
            "reason_code": "SYNC_ERROR_EXCEEDED",
        }
    ]
    unapproved_exclusion["exclusion_policy"]["allowed_reason_codes"] = ["UNSTABLE"]
    with pytest.raises(ValidationError):
        AnalogCalibrationEvidence.model_validate(unapproved_exclusion)

    bad_reference = {
        "kind": "analog_reference",
        "evidence_schema_version": 4,
        "point_id": "P1",
        "plan_id": "plan-P1",
        "reference_state_aggregates": {"A": 0, "B": 10, "C": 5, "A_RETURN": 0},
        "state_aggregates": {"A": 0, "B": 10, "C": 5, "A_RETURN": 0},
        "samples": _analog_reference_samples(value),
        "reference_id": "instrument",
        "channel_id": "channel",
        "calibration_certificate_sha256": SHA_ONE,
        "instrument_capability": {
            "range_minimum": 0,
            "range_maximum": 4,
            "resolution": 0.01,
            "accuracy": 0.05,
            "status": "IN_CALIBRATION",
        },
        "unit_conversion": copy.deepcopy(value["unit_conversion"]),
        "uncertainty": 0.1,
        "reference_collector_tool_id": "ruisheng.reference-collector/v1",
        "reference_collector_tool_sha256": SHA_ONE,
        "terminal_state": "PASS",
    }
    with pytest.raises(ValidationError):
        AnalogReferenceEvidence.model_validate(bad_reference)


def test_reference_sample_ids_must_exactly_match_calibration_samples() -> None:
    calibration_value = _analog_evidence()
    calibration = EvidenceArtifact.model_validate(
        {
            "schema_version": 1,
            "artifact_type": "ruisheng.device-point-profile-evidence",
            "evidence_id": "CAL",
            "role": "calibration",
            "profile_id": "profile-test",
            "device_identity_sha256": SHA_ZERO,
            "device_serial": "SERIAL",
            "run_id": "run-1",
            "calibration_run_approval_sha256": SHA_ONE,
            "subject_point_ids": ["P1"],
            "observed_at": "2026-08-27T06:00:00+00:00",
            "attestor_id": "calibration-runner",
            "content": calibration_value,
            "signature": _dummy_signature("calibration-key"),
        }
    )
    raw_observation = EvidenceArtifact.model_validate(
        {
            "schema_version": 1,
            "artifact_type": "ruisheng.device-point-profile-evidence",
            "evidence_id": "RAW",
            "role": "raw_observation",
            "profile_id": "profile-test",
            "device_identity_sha256": SHA_ZERO,
            "device_serial": "SERIAL",
            "run_id": "run-1",
            "calibration_run_approval_sha256": SHA_ONE,
            "subject_point_ids": ["P1"],
            "observed_at": "2026-08-27T06:00:00+00:00",
            "attestor_id": "calibration-runner",
            "content": _raw_observation_evidence(
                calibration_value,
                point_id="P1",
                plan_id="plan-P1",
                run_id="run-1",
                unit_id=1,
                function_code=3,
                start_address=1,
                quantity=1,
            ),
            "signature": _dummy_signature("calibration-key"),
        }
    )
    reference_samples = _analog_reference_samples(calibration_value)
    reference_samples[-1]["sample_id"] = "not-a-calibration-sample"
    reference = EvidenceArtifact.model_validate(
        {
            "schema_version": 1,
            "artifact_type": "ruisheng.device-point-profile-evidence",
            "evidence_id": "REF",
            "role": "reference",
            "profile_id": "profile-test",
            "device_identity_sha256": SHA_ZERO,
            "device_serial": "SERIAL",
            "run_id": "run-1",
            "calibration_run_approval_sha256": SHA_ONE,
            "subject_point_ids": ["P1"],
            "observed_at": "2026-08-27T06:00:00+00:00",
            "attestor_id": "reference-runner",
            "content": {
                "kind": "analog_reference",
                "evidence_schema_version": 4,
                "point_id": "P1",
                "plan_id": "plan-P1",
                "reference_state_aggregates": {
                    state["state_id"]: state["aggregate_engineering"]
                    for state in calibration_value["states"]
                },
                "state_aggregates": {
                    state["state_id"]: state["aggregate_engineering"]
                    for state in calibration_value["states"]
                },
                "samples": reference_samples,
                "reference_id": "instrument",
                "channel_id": "channel",
                "calibration_certificate_sha256": SHA_ONE,
                "instrument_capability": {
                    "range_minimum": -10,
                    "range_maximum": 100,
                    "resolution": 0.01,
                    "accuracy": 0.05,
                    "status": "IN_CALIBRATION",
                },
                "unit_conversion": copy.deepcopy(calibration_value["unit_conversion"]),
                "uncertainty": 0.1,
                "reference_collector_tool_id": "ruisheng.reference-collector/v1",
                "reference_collector_tool_sha256": SHA_ONE,
                "terminal_state": "PASS",
            },
            "signature": _dummy_signature("reference-key"),
        }
    )
    point = PointProfile.model_validate(
        {
            "point_id": "P1",
            "point_name": "temperature",
            "function_code": 3,
            "start_address": 1,
            "register_width": 1,
            "bit": None,
            "identity_status": "resolved",
            "semantic_status": "resolved",
            "encoding_status": "resolved",
            "unit_status": "resolved",
            "calibration_status": "resolved",
            "implementation_status": "supported",
            "encoding": {
                "value_type": "u16",
                "byte_order": "big",
                "word_order": "not_applicable",
                "raw_domain": {"minimum": 0, "maximum": 100},
            },
            "unit": "degC",
            "calibration_profile": {
                "kind": "analog",
                "method": "affine_holdout_return",
                "engineering_mapping": {"ratio": 0.1, "offset": 0.0},
            },
            "evidence_refs": ["RAW", "CAL", "REF"],
        }
    )

    reasons = _point_calibration_reasons(
        point,
        {"RAW": raw_observation, "CAL": calibration, "REF": reference},
        "/profile_payload/points/0",
    )

    assert {reason.code for reason in reasons} == {"REFERENCE_SAMPLE_SET_MISMATCH"}


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("range", "REFERENCE_SAMPLE_RANGE_EXCEEDED"),
        ("sync", "REFERENCE_SAMPLE_SYNC_EXCEEDED"),
        ("uncertainty", "REFERENCE_SAMPLE_UNCERTAINTY_EXCEEDED"),
    ],
)
def test_analog_reference_limit_diagnostics_are_reachable(
    tmp_path: Path,
    mutation: str,
    expected_code: str,
) -> None:
    profile_value, _, _ = _synthetic_eligible_contract(tmp_path)
    profile = DevicePointProfile.model_validate(profile_value)
    point = next(item for item in profile.profile_payload.points if item.point_id == "P-ANALOG")
    artifacts = _synthetic_evidence_artifacts(
        tmp_path,
        profile_value,
        "CAL-ANALOG",
        "RAW-ANALOG",
    )
    reference_value = _evidence_artifact_value(tmp_path, profile_value, "REF-ANALOG")
    samples = reference_value["content"]["samples"]
    if mutation == "range":
        samples[0]["reference_value"] = 200
        samples[1]["reference_value"] = -200
    elif mutation == "sync":
        calibration = cast(AnalogCalibrationEvidence, artifacts["CAL-ANALOG"].content)
        samples[0]["observed_at"] = (
            datetime.fromisoformat(calibration.states[0].samples[0].observed_at)
            + timedelta(milliseconds=6)
        ).isoformat()
        samples[0]["sync_error_ms"] = 6
    else:
        samples[0]["uncertainty"] = 0.2
        reference_value["content"]["uncertainty"] = 0.2
    artifacts["REF-ANALOG"] = EvidenceArtifact.model_validate(reference_value)

    reasons = _point_calibration_reasons(
        point,
        artifacts,
        "/profile_payload/points/P-ANALOG",
    )

    assert expected_code in {reason.code for reason in reasons}


@pytest.mark.parametrize(
    ("point_id", "reference_id", "expected_code"),
    [
        ("P-ANALOG", "REF-ANALOG", "ANALOG_REFERENCE_LIMIT_MISMATCH"),
        ("P-BINARY", "REF-BINARY", "BINARY_REFERENCE_LIMIT_MISMATCH"),
        ("P-COUNTER", "REF-COUNTER", "COUNTER_REFERENCE_LIMIT_MISMATCH"),
    ],
)
def test_reference_plan_limit_diagnostics_are_reachable(
    tmp_path: Path,
    point_id: str,
    reference_id: str,
    expected_code: str,
) -> None:
    profile_value, _, _ = _synthetic_eligible_contract(tmp_path)
    profile = DevicePointProfile.model_validate(profile_value)
    binding = CalibrationRunApprovalBinding.model_validate(
        profile_value["calibration_run_approval_binding"]
    )
    approval = CalibrationRunApprovalArtifact.model_validate(
        json.loads((tmp_path / binding.path).read_text(encoding="utf-8"))
    )
    reference_value = _evidence_artifact_value(tmp_path, profile_value, reference_id)
    reference_value["content"]["samples"][0]["uncertainty"] = 0.2
    if point_id == "P-ANALOG":
        reference_value["content"]["uncertainty"] = 0.2
    reference = EvidenceArtifact.model_validate(reference_value)

    invalid, _ = _calibration_run_approval_reasons(
        approval,
        binding,
        profile,
        None,
        current=NOW,
        evidence_artifacts={reference_id: reference},
    )

    assert expected_code in {reason.code for reason in invalid}


def test_analog_uncertainty_exclusion_uses_the_stricter_approved_limit(
    tmp_path: Path,
) -> None:
    profile_value, _, _ = _synthetic_eligible_contract(tmp_path)
    profile = DevicePointProfile.model_validate(profile_value)
    point = next(item for item in profile.profile_payload.points if item.point_id == "P-ANALOG")
    binding = CalibrationRunApprovalBinding.model_validate(
        profile_value["calibration_run_approval_binding"]
    )
    approval = CalibrationRunApprovalArtifact.model_validate(
        json.loads((tmp_path / binding.path).read_text(encoding="utf-8"))
    )
    approval, binding = _approval_with_plan_overrides(
        approval,
        binding,
        point_id="P-ANALOG",
        maximum_reference_uncertainty=0.05,
    )
    point_plan = next(plan for plan in approval.plans if plan.point_id == "P-ANALOG")

    excluded_at = "2026-08-27T01:01:30+00:00"
    calibration_value = _evidence_artifact_value(tmp_path, profile_value, "CAL-ANALOG")
    for state in calibration_value["content"]["states"]:
        for sample in state["samples"]:
            sample["uncertainty"] = 0.04
    calibration_value["content"]["exclusion_log"].append(
        {
            "sample_id": "excluded-A-uncertainty",
            "state_id": "A",
            "event_id": "event-A",
            "observed_at": excluded_at,
            "raw": 0,
            "reason_code": "REFERENCE_UNCERTAINTY_EXCEEDED",
        }
    )
    calibration = EvidenceArtifact.model_validate(calibration_value)

    reference_value = _evidence_artifact_value(tmp_path, profile_value, "REF-ANALOG")
    for sample in reference_value["content"]["samples"]:
        sample["uncertainty"] = 0.04
    reference_value["content"]["uncertainty"] = 0.04
    reference_value["content"]["samples"].append(
        {
            "sample_id": "excluded-A-uncertainty",
            "state_id": "A",
            "event_id": "event-A",
            "observed_at": _reference_timestamp(excluded_at),
            "reference_value": 0,
            "unit": "degC",
            "sync_error_ms": 1,
            "uncertainty": 0.075,
            "stable": True,
            "outcome": "EXCLUDED",
            "exclusion_reason": "REFERENCE_UNCERTAINTY_EXCEEDED",
        }
    )
    reference_value["content"]["samples"].sort(key=lambda sample: sample["observed_at"])
    reference = EvidenceArtifact.model_validate(reference_value)

    raw_value = _evidence_artifact_value(tmp_path, profile_value, "RAW-ANALOG")
    raw_records = raw_value["content"]["records"]
    template = next(
        record
        for record in raw_records
        if record["record_type"] == "modbus_observation" and record["event_id"] == "event-A"
    )
    excluded_record = copy.deepcopy(template)
    excluded_record.update(
        {
            "record_type": "modbus_observation",
            "record_id": "P-ANALOG-record-excluded-uncertainty",
            "sample_id": "excluded-A-uncertainty",
            "event_id": "event-A",
            "observed_at": excluded_at,
            "decoded_raw": 0,
        }
    )
    raw_records.insert(-1, excluded_record)
    raw_records[1:-1] = sorted(raw_records[1:-1], key=lambda record: record["observed_at"])
    _rechain_raw_observation(raw_value["content"])
    raw = EvidenceArtifact.model_validate(raw_value)
    artifacts = {
        "CAL-ANALOG": calibration,
        "RAW-ANALOG": raw,
        "REF-ANALOG": reference,
    }

    reasons = _point_calibration_reasons(
        point,
        artifacts,
        "/profile_payload/points/P-ANALOG",
        point_plan=point_plan,
    )
    invalid, blocked = _calibration_run_approval_reasons(
        approval,
        binding,
        profile,
        None,
        current=NOW,
        evidence_artifacts=artifacts,
    )

    assert reasons == []
    assert "ANALOG_REFERENCE_LIMIT_MISMATCH" not in {reason.code for reason in invalid}
    assert blocked == []

    mislabeled_value = copy.deepcopy(reference_value)
    excluded_sample = next(
        sample
        for sample in mislabeled_value["content"]["samples"]
        if sample["sample_id"] == "excluded-A-uncertainty"
    )
    excluded_sample["uncertainty"] = 0.04
    mislabeled_artifacts = dict(artifacts)
    mislabeled_artifacts["REF-ANALOG"] = EvidenceArtifact.model_validate(mislabeled_value)
    mislabeled_reasons = _point_calibration_reasons(
        point,
        mislabeled_artifacts,
        "/profile_payload/points/P-ANALOG",
        point_plan=point_plan,
    )

    assert "REFERENCE_SAMPLE_FACT_MISMATCH" in {reason.code for reason in mislabeled_reasons}


def test_binary_requires_chatter_negative_control_and_address_semantics() -> None:
    value = _binary_evidence()
    assert BinaryCalibrationEvidence.model_validate(value).states[1].aggregate_raw == 1
    chatter = copy.deepcopy(value)
    chatter["states"][1]["chatter_transitions"] = 1
    with pytest.raises(ValidationError):
        BinaryCalibrationEvidence.model_validate(chatter)
    wrong_address = copy.deepcopy(value)
    wrong_address["address_semantics"]["kind"] = "coil"
    with pytest.raises(ValidationError):
        BinaryCalibrationEvidence.model_validate(wrong_address)


def test_binary_v3_requires_unintervened_and_competing_candidate_identity() -> None:
    value = _binary_evidence()
    missing_control = copy.deepcopy(value)
    missing_control["unintervened_channel_controls"] = []
    with pytest.raises(ValidationError):
        BinaryCalibrationEvidence.model_validate(missing_control)

    selected_as_competitor = copy.deepcopy(value)
    selected_as_competitor["competing_candidate_controls"][0]["candidate"]["candidate_id"] = (
        selected_as_competitor["address_semantics"]["candidate_id"]
    )
    with pytest.raises(ValidationError):
        BinaryCalibrationEvidence.model_validate(selected_as_competitor)

    changed_control = copy.deepcopy(value)
    changed_control["unintervened_channel_controls"][0]["observed_raw"] = 1
    with pytest.raises(ValidationError):
        BinaryCalibrationEvidence.model_validate(changed_control)


def test_binary_v3_requires_ordered_state_windows() -> None:
    value = _binary_evidence()
    out_of_order = copy.deepcopy(value)
    out_of_order["states"][0], out_of_order["states"][1] = (
        out_of_order["states"][1],
        out_of_order["states"][0],
    )
    with pytest.raises(ValidationError):
        BinaryCalibrationEvidence.model_validate(out_of_order)

    overlapping = copy.deepcopy(value)
    for index, sample in enumerate(overlapping["states"][1]["samples"]):
        sample["observed_at"] = f"2026-08-27T00:0{index}:00+00:00"
    with pytest.raises(ValidationError):
        BinaryCalibrationEvidence.model_validate(overlapping)


@pytest.mark.parametrize(
    "control_field",
    ["unintervened_channel_controls", "competing_candidate_controls"],
)
def test_binary_v3_requires_controls_inside_active_window(control_field: str) -> None:
    value = _binary_evidence()
    value[control_field][0]["observed_at"] = "2026-08-27T04:00:00+00:00"

    with pytest.raises(ValidationError):
        BinaryCalibrationEvidence.model_validate(value)


def test_binary_and_counter_negative_control_ids_must_be_unique() -> None:
    binary = _binary_evidence()
    binary["negative_controls"].append(copy.deepcopy(binary["negative_controls"][0]))
    with pytest.raises(ValidationError):
        BinaryCalibrationEvidence.model_validate(binary)

    counter = _counter_evidence()
    counter["negative_controls"].append(copy.deepcopy(counter["negative_controls"][0]))
    with pytest.raises(ValidationError):
        CounterCalibrationEvidence.model_validate(counter)


def test_counter_requires_increment_rollover_and_persistence() -> None:
    value = _counter_evidence()
    counter = CounterCalibrationEvidence.model_validate(value)
    assert _counter_sequence_valid(counter)
    no_increment = copy.deepcopy(value)
    for observation in no_increment["observations"]:
        observation["reference_increment"] = 0
    assert not _counter_sequence_valid(CounterCalibrationEvidence.model_validate(no_increment))
    wrong_delta = copy.deepcopy(value)
    for observation in wrong_delta["observations"]:
        if observation["state_id"] == "BASELINE":
            observation["raw"] = 1
    assert not _counter_sequence_valid(CounterCalibrationEvidence.model_validate(wrong_delta))
    saturating = copy.deepcopy(value)
    saturating["rollover_behavior"] = "saturate"
    for observation in saturating["observations"]:
        if observation["state_id"] in {"ROLLOVER", "PERSISTENCE"}:
            observation["raw"] = 15
    saturating["terminal_raw"] = 15
    saturating["persistence_before"] = 15
    saturating["persistence_after"] = 15
    saturating["persistence_event"]["pre_power_raw"] = 15
    saturating["persistence_event"]["post_power_raw"] = 15
    assert _counter_sequence_valid(CounterCalibrationEvidence.model_validate(saturating))
    resetting = copy.deepcopy(value)
    resetting["rollover_behavior"] = "reset"
    resetting["observations"][-1]["reference_increment"] = 0
    assert _counter_sequence_valid(CounterCalibrationEvidence.model_validate(resetting))
    arbitrary_reset = copy.deepcopy(resetting)
    arbitrary_raw = {"BASELINE": 6, "INCREMENT": 7, "ROLLOVER": 0, "PERSISTENCE": 0}
    for observation in arbitrary_reset["observations"]:
        observation["raw"] = arbitrary_raw[observation["state_id"]]
    assert not _counter_sequence_valid(CounterCalibrationEvidence.model_validate(arbitrary_reset))
    persistence_failure = copy.deepcopy(value)
    persistence_failure["persistence_after"] = 1
    with pytest.raises(ValidationError):
        CounterCalibrationEvidence.model_validate(persistence_failure)


def test_counter_v3_requires_ordered_approved_states_and_physical_power_loss() -> None:
    value = _counter_evidence()
    out_of_order = copy.deepcopy(value)
    out_of_order["observations"][1], out_of_order["observations"][2] = (
        out_of_order["observations"][2],
        out_of_order["observations"][1],
    )
    with pytest.raises(ValidationError):
        CounterCalibrationEvidence.model_validate(out_of_order)

    simulated_persistence = copy.deepcopy(value)
    simulated_persistence["persistence_event"]["method"] = "software_restart"
    with pytest.raises(ValidationError):
        CounterCalibrationEvidence.model_validate(simulated_persistence)

    no_power_window = copy.deepcopy(value)
    no_power_window["persistence_event"]["power_restored_at"] = no_power_window[
        "persistence_event"
    ]["power_removed_at"]
    with pytest.raises(ValidationError):
        CounterCalibrationEvidence.model_validate(no_power_window)


@pytest.mark.parametrize("state_id", ["BASELINE", "INCREMENT", "ROLLOVER", "PERSISTENCE"])
def test_counter_v3_requires_three_samples_per_state(state_id: str) -> None:
    value = _counter_evidence()
    value["observations"] = [
        observation
        for observation in value["observations"]
        if not (observation["state_id"] == state_id and observation["sample_id"].endswith("-2"))
    ]

    with pytest.raises(ValidationError):
        CounterCalibrationEvidence.model_validate(value)


def test_counter_total_of_twelve_cannot_hide_an_underfilled_state() -> None:
    value = _counter_evidence()
    reassigned = value["observations"][2]
    reassigned.update(
        {
            "state_id": "INCREMENT",
            "event_id": "event-INCREMENT",
            "raw": 15,
            "reference_increment": 1,
        }
    )

    assert len(value["observations"]) == 12
    with pytest.raises(ValidationError):
        CounterCalibrationEvidence.model_validate(value)


def test_counter_v3_rejects_interleaved_events_and_unstable_state_samples() -> None:
    interleaved = _counter_evidence()
    interleaved["observations"][2], interleaved["observations"][3] = (
        interleaved["observations"][3],
        interleaved["observations"][2],
    )
    with pytest.raises(ValidationError):
        CounterCalibrationEvidence.model_validate(interleaved)

    split_event = _counter_evidence()
    split_event["observations"][1]["event_id"] = "event-BASELINE-SECOND"
    with pytest.raises(ValidationError):
        CounterCalibrationEvidence.model_validate(split_event)

    unstable = _counter_evidence()
    unstable["observations"][1]["raw"] = 13
    with pytest.raises(ValidationError):
        CounterCalibrationEvidence.model_validate(unstable)


def test_counter_v3_binds_persistence_to_final_post_restore_sample() -> None:
    value = _counter_evidence()
    value["persistence_event"]["post_restore_observed_at"] = value["observations"][-2][
        "observed_at"
    ]

    with pytest.raises(ValidationError):
        CounterCalibrationEvidence.model_validate(value)


def test_counter_reference_binds_each_sample_to_its_state() -> None:
    value = _counter_reference_evidence()
    CounterReferenceEvidence.model_validate(value)

    mismatched = copy.deepcopy(value)
    mismatched["samples"].pop()
    with pytest.raises(ValidationError):
        CounterReferenceEvidence.model_validate(mismatched)

    interleaved = copy.deepcopy(value)
    interleaved["samples"][2], interleaved["samples"][3] = (
        interleaved["samples"][3],
        interleaved["samples"][2],
    )
    with pytest.raises(ValidationError):
        CounterReferenceEvidence.model_validate(interleaved)


@pytest.mark.parametrize("boundary", ["last_rollover", "first_persistence"])
def test_counter_reference_power_cycle_stays_between_state_windows(boundary: str) -> None:
    value = _counter_reference_evidence()
    event = value["persistence_event"]
    if boundary == "last_rollover":
        event["power_removed_at"] = next(
            sample["observed_at"]
            for sample in reversed(value["samples"])
            if sample["state_id"] == "ROLLOVER"
        )
    else:
        first_persistence = next(
            sample["observed_at"]
            for sample in value["samples"]
            if sample["state_id"] == "PERSISTENCE"
        )
        event["power_restored_at"] = (
            datetime.fromisoformat(first_persistence) + timedelta(milliseconds=1)
        ).isoformat()
    event["power_off_duration_seconds"] = (
        datetime.fromisoformat(event["power_restored_at"])
        - datetime.fromisoformat(event["power_removed_at"])
    ).total_seconds()

    with pytest.raises(ValidationError):
        CounterReferenceEvidence.model_validate(value)


@pytest.mark.parametrize(("offset_ms", "mismatch"), [(5, False), (6, True)])
def test_counter_independent_power_cycle_uses_sync_tolerance(
    tmp_path: Path,
    offset_ms: int,
    mismatch: bool,
) -> None:
    profile_value, _, _, _, point, artifacts = _synthetic_counter_context(tmp_path)
    reference_value = _evidence_artifact_value(tmp_path, profile_value, "REF-COUNTER")
    event = reference_value["content"]["persistence_event"]
    for field in ("power_removed_at", "power_restored_at"):
        event[field] = (
            datetime.fromisoformat(event[field]) + timedelta(milliseconds=offset_ms)
        ).isoformat()
    artifacts["REF-COUNTER"] = EvidenceArtifact.model_validate(reference_value)

    reasons = _point_calibration_reasons(
        point,
        artifacts,
        "/profile_payload/points/P-COUNTER",
    )
    codes = {reason.code for reason in reasons}

    assert ("REFERENCE_PERSISTENCE_MISMATCH" in codes) is mismatch


def test_counter_independent_power_cycle_durations_need_not_match_exactly(tmp_path: Path) -> None:
    profile_value, _, _, _, point, artifacts = _synthetic_counter_context(tmp_path)
    reference_value = _evidence_artifact_value(tmp_path, profile_value, "REF-COUNTER")
    event = reference_value["content"]["persistence_event"]
    event["power_removed_at"] = (
        datetime.fromisoformat(event["power_removed_at"]) + timedelta(milliseconds=2)
    ).isoformat()
    event["power_restored_at"] = (
        datetime.fromisoformat(event["power_restored_at"]) + timedelta(milliseconds=4)
    ).isoformat()
    event["power_off_duration_seconds"] = (
        datetime.fromisoformat(event["power_restored_at"])
        - datetime.fromisoformat(event["power_removed_at"])
    ).total_seconds()
    artifacts["REF-COUNTER"] = EvidenceArtifact.model_validate(reference_value)

    reasons = _point_calibration_reasons(
        point,
        artifacts,
        "/profile_payload/points/P-COUNTER",
    )

    assert "REFERENCE_PERSISTENCE_MISMATCH" not in {reason.code for reason in reasons}


def test_counter_reference_sync_limit_diagnostic_is_reachable(tmp_path: Path) -> None:
    profile_value, _, _, _, point, artifacts = _synthetic_counter_context(tmp_path)
    reference_value = _evidence_artifact_value(tmp_path, profile_value, "REF-COUNTER")
    calibration = cast(CounterCalibrationEvidence, artifacts["CAL-COUNTER"].content)
    reference_value["content"]["samples"][0]["observed_at"] = (
        datetime.fromisoformat(calibration.observations[0].observed_at) + timedelta(milliseconds=6)
    ).isoformat()
    reference_value["content"]["samples"][0]["sync_error_ms"] = 6
    artifacts["REF-COUNTER"] = EvidenceArtifact.model_validate(reference_value)

    reasons = _point_calibration_reasons(
        point,
        artifacts,
        "/profile_payload/points/P-COUNTER",
    )

    assert "REFERENCE_SAMPLE_SYNC_EXCEEDED" in {reason.code for reason in reasons}


@pytest.mark.parametrize(
    ("sample_count_per_state", "maximum_requests", "valid"),
    [(3, 11, False), (3, 12, True), (4, 12, False)],
)
def test_counter_plan_tx_budget_covers_every_planned_sample(
    sample_count_per_state: int,
    maximum_requests: int,
    valid: bool,
) -> None:
    value = _counter_plan_value(
        sample_count_per_state=sample_count_per_state,
        maximum_requests=maximum_requests,
    )

    if valid:
        CalibrationPointPlan.model_validate(value)
    else:
        with pytest.raises(ValidationError):
            CalibrationPointPlan.model_validate(value)


def test_counter_run_requires_plan_n_samples_in_each_state(tmp_path: Path) -> None:
    _, profile, approval, binding, _, artifacts = _synthetic_counter_context(tmp_path)
    approval, binding = _counter_approval_with_plan_overrides(
        approval,
        binding,
        sample_count_per_state=4,
        tx_scope=[
            {
                "function_code": 3,
                "start_address": 30,
                "quantity": 1,
                "maximum_requests": 16,
                "write_allowed": False,
            }
        ],
    )
    counter = artifacts["CAL-COUNTER"].content
    assert isinstance(counter, CounterCalibrationEvidence)
    assert len(counter.observations) == 12

    invalid, _ = _calibration_run_approval_reasons(
        approval,
        binding,
        profile,
        None,
        current=NOW,
        evidence_artifacts={"CAL-COUNTER": artifacts["CAL-COUNTER"]},
    )

    assert "COUNTER_PLAN_THRESHOLD_MISMATCH" in {reason.code for reason in invalid}


def test_counter_reference_sample_order_and_identity_match_calibration(
    tmp_path: Path,
) -> None:
    profile_value, _, _, _, point, artifacts = _synthetic_counter_context(tmp_path)
    reference_value = _evidence_artifact_value(tmp_path, profile_value, "REF-COUNTER")

    for mutation in ("reordered", "replaced"):
        candidate = copy.deepcopy(reference_value)
        samples = candidate["content"]["samples"]
        if mutation == "reordered":
            samples[0]["sample_id"], samples[1]["sample_id"] = (
                samples[1]["sample_id"],
                samples[0]["sample_id"],
            )
        else:
            samples[0]["sample_id"] = "counter-foreign-sample"
        candidate_artifacts = dict(artifacts)
        candidate_artifacts["REF-COUNTER"] = EvidenceArtifact.model_validate(candidate)

        reasons = _point_calibration_reasons(
            point,
            candidate_artifacts,
            "/profile_payload/points/P-COUNTER",
        )

        assert "REFERENCE_SAMPLE_SET_MISMATCH" in {reason.code for reason in reasons}


def test_counter_reference_sample_facts_match_calibration(tmp_path: Path) -> None:
    profile_value, _, _, _, point, artifacts = _synthetic_counter_context(tmp_path)
    reference_value = _evidence_artifact_value(tmp_path, profile_value, "REF-COUNTER")
    reference_value["content"]["samples"][0]["event_id"] = "event-foreign"
    artifacts["REF-COUNTER"] = EvidenceArtifact.model_validate(reference_value)

    reasons = _point_calibration_reasons(
        point,
        artifacts,
        "/profile_payload/points/P-COUNTER",
    )

    assert "REFERENCE_SAMPLE_FACT_MISMATCH" in {reason.code for reason in reasons}


def test_counter_raw_requires_all_twelve_exact_sample_ids(tmp_path: Path) -> None:
    profile_value, _, _, _, point, artifacts = _synthetic_counter_context(tmp_path)
    raw_value = _evidence_artifact_value(tmp_path, profile_value, "RAW-COUNTER")
    observation_index = next(
        index
        for index, record in enumerate(raw_value["content"]["records"])
        if record["record_type"] == "modbus_observation"
    )

    for mutation in ("missing", "replaced"):
        candidate = copy.deepcopy(raw_value)
        if mutation == "missing":
            candidate["content"]["records"].pop(observation_index)
        else:
            candidate["content"]["records"][observation_index]["sample_id"] = (
                "counter-foreign-sample"
            )
        _rechain_raw_observation(candidate["content"])
        candidate_artifacts = dict(artifacts)
        candidate_artifacts["RAW-COUNTER"] = EvidenceArtifact.model_validate(candidate)

        reasons = _point_calibration_reasons(
            point,
            candidate_artifacts,
            "/profile_payload/points/P-COUNTER",
        )

        assert "RAW_OBSERVATION_SAMPLE_MISMATCH" in {reason.code for reason in reasons}


def test_counter_raw_decoded_value_tampering_is_detected(tmp_path: Path) -> None:
    profile_value, profile, approval, binding, _, artifacts = _synthetic_counter_context(tmp_path)
    raw_value = _evidence_artifact_value(tmp_path, profile_value, "RAW-COUNTER")
    observation = next(
        record
        for record in raw_value["content"]["records"]
        if record["record_type"] == "modbus_observation"
    )
    observation["decoded_raw"] = observation["decoded_raw"] - 1
    _rechain_raw_observation(raw_value["content"])
    artifacts["RAW-COUNTER"] = EvidenceArtifact.model_validate(raw_value)

    invalid, _ = _calibration_run_approval_reasons(
        approval,
        binding,
        profile,
        None,
        current=NOW,
        evidence_artifacts={
            "CAL-COUNTER": artifacts["CAL-COUNTER"],
            "RAW-COUNTER": artifacts["RAW-COUNTER"],
        },
    )

    assert "RAW_OBSERVATION_DECODE_MISMATCH" in {reason.code for reason in invalid}


def test_counter_raw_actual_requests_cannot_exceed_tx_budget(tmp_path: Path) -> None:
    profile_value, profile, approval, binding, _, artifacts = _synthetic_counter_context(tmp_path)
    approval, binding = _counter_approval_with_plan_overrides(
        approval,
        binding,
        tx_scope=[
            {
                "function_code": 3,
                "start_address": 30,
                "quantity": 1,
                "maximum_requests": 12,
                "write_allowed": False,
            }
        ],
    )
    raw_value = _evidence_artifact_value(tmp_path, profile_value, "RAW-COUNTER")
    extra = copy.deepcopy(raw_value["content"]["records"][-2])
    extra.update(
        {
            "record_id": "P-COUNTER-record-extra",
            "sample_id": "counter-extra-sample",
            "event_id": "event-extra",
            "observed_at": "2026-08-27T05:00:00+00:00",
        }
    )
    raw_value["content"]["records"].insert(-1, extra)
    _rechain_raw_observation(raw_value["content"])
    artifacts["RAW-COUNTER"] = EvidenceArtifact.model_validate(raw_value)

    invalid, _ = _calibration_run_approval_reasons(
        approval,
        binding,
        profile,
        None,
        current=NOW,
        evidence_artifacts={
            "CAL-COUNTER": artifacts["CAL-COUNTER"],
            "RAW-COUNTER": artifacts["RAW-COUNTER"],
        },
    )

    assert "RAW_OBSERVATION_TX_BUDGET_EXCEEDED" in {reason.code for reason in invalid}


@pytest.mark.parametrize("check_id", sorted(REQUIRED_RUNTIME_ASSERTIONS))
def test_runtime_reports_require_check_specific_closed_assertions(check_id: str) -> None:
    value: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "ruisheng.device-point-profile-runtime-raw-report",
        "check_id": check_id,
        "result": "PASS",
        "started_at": "2026-08-27T01:00:00+00:00",
        "completed_at": "2026-08-27T01:01:00+00:00",
        "exit_code": 0,
        "assertions": [
            {
                "assertion_id": assertion_id,
                "outcome": "PASS",
                "detail": "versioned observation",
                "expected": "required invariant",
                "observed": "invariant held",
                "observation_sha256": SHA_ZERO,
            }
            for assertion_id in sorted(REQUIRED_RUNTIME_ASSERTIONS[check_id])
        ],
    }
    assert RuntimeRawReport.model_validate(value).result == "PASS"
    value["assertions"][0]["assertion_id"] = "GENERIC_PASS"
    with pytest.raises(ValidationError):
        RuntimeRawReport.model_validate(value)


def test_deep_artifact_returns_invalid_instead_of_recursion_escape(tmp_path: Path) -> None:
    policy, trust_root, _keys = _trust_contract()
    profile = _minimal_profile(tmp_path, policy=policy, trust_root=trust_root)
    nested = ("[" * 2000 + "0" + "]" * 2000).encode()
    binding = _write_bytes(tmp_path, "evidence/deep.json", nested)
    profile["evidence_bindings"] = [
        {
            "evidence_id": "DEEP",
            "role": "calibration",
            **binding,
            "media_type": "application/json",
            "subject_point_ids": ["P1"],
        }
    ]
    profile["profile_payload"]["points"][0]["evidence_refs"] = ["DEEP"]
    profile["payload_sha256"] = canonical_payload_sha256(profile["profile_payload"])
    report = _validate_profile_data_with_trusted_context(
        profile, root=tmp_path, now=NOW, trust_policy=policy, trust_root=trust_root
    )
    assert report.decision == "INVALID"
    assert "EVIDENCE_ARTIFACT_INVALID" in _codes(report)


@pytest.mark.parametrize("role", ["calibration", "reference"])
def test_nonfinite_evidence_and_reference_return_invalid(
    tmp_path: Path,
    role: str,
) -> None:
    policy, trust_root, _keys = _trust_contract()
    profile = _minimal_profile(tmp_path, policy=policy, trust_root=trust_root)
    binding = _write_bytes(tmp_path, f"evidence/{role}.json", b'{"value":1e400}')
    profile["evidence_bindings"] = [
        {
            "evidence_id": "NONFINITE",
            "role": role,
            **binding,
            "media_type": "application/json",
            "subject_point_ids": ["P1"],
        }
    ]
    profile["profile_payload"]["points"][0]["evidence_refs"] = ["NONFINITE"]
    profile["payload_sha256"] = canonical_payload_sha256(profile["profile_payload"])

    report = _validate_profile_data_with_trusted_context(
        profile, root=tmp_path, now=NOW, trust_policy=policy, trust_root=trust_root
    )

    assert report.decision == "INVALID"
    assert "EVIDENCE_ARTIFACT_INVALID" in _codes(report)


def test_nonfinite_approval_returns_invalid(tmp_path: Path) -> None:
    policy, trust_root, _keys = _trust_contract()
    profile = _minimal_profile(tmp_path, policy=policy, trust_root=trust_root)
    binding = _write_bytes(tmp_path, "approval/nonfinite.json", b'{"value":1e400}')
    profile["approval_binding"] = {"subject_gate_sha256": SHA_ZERO, **binding}

    report = _validate_profile_data_with_trusted_context(
        profile, root=tmp_path, now=NOW, trust_policy=policy, trust_root=trust_root
    )

    assert report.decision == "INVALID"
    assert "APPROVAL_ARTIFACT_INVALID" in _codes(report)


def test_nonfinite_runtime_returns_invalid(tmp_path: Path) -> None:
    policy, trust_root, _keys = _trust_contract()
    profile = _minimal_profile(tmp_path, policy=policy, trust_root=trust_root)
    binding = _write_bytes(tmp_path, "runtime/nonfinite.json", b'{"value":1e400}')
    profile["runtime_evidence"] = [{"check_id": "STRICT_VALUE_TYPE_VALIDATION", **binding}]

    report = _validate_profile_data_with_trusted_context(
        profile, root=tmp_path, now=NOW, trust_policy=policy, trust_root=trust_root
    )

    assert report.decision == "INVALID"
    assert "RUNTIME_ARTIFACT_INVALID" in _codes(report)


def test_calibration_run_approval_rejects_preapproval_samples(tmp_path: Path) -> None:
    policy, root, keys = _trust_contract()
    profile_value = _minimal_profile(tmp_path, policy=policy, trust_root=root)
    identity = profile_value["profile_payload"]["device_identity"]
    identity.update(
        {
            "status": "resolved",
            "model": "RSC-1",
            "hardware_revision": "A",
            "firmware_version": "1.2.3",
            "point_map_version": "2026.08",
            "usb_serial_number": "SERIAL",
        }
    )
    profile_value["profile_payload"]["line_protocol"].update(
        {
            "status": "resolved",
            "stable_device_path": "/dev/serial/by-id/usb-device",
            "unit_id": 1,
            "baud_rate": 9600,
            "data_bits": 8,
            "parity": "N",
            "stop_bits": 1,
        }
    )
    point_value = profile_value["profile_payload"]["points"][0]
    point_value["encoding"] = {
        "value_type": "u16",
        "byte_order": "big",
        "word_order": "not_applicable",
        "raw_domain": {"minimum": 0, "maximum": 100},
    }
    point_value["unit"] = "degC"
    point_value["calibration_profile"] = {
        "kind": "analog",
        "method": "affine_holdout_return",
        "engineering_mapping": {"ratio": 0.1, "offset": 0.0},
    }
    profile_value["payload_sha256"] = canonical_payload_sha256(profile_value["profile_payload"])
    approval: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "ruisheng.device-point-profile-calibration-run-approval",
        "run_id": "run-1",
        "subject_plan_sha256": SHA_ZERO,
        "profile_id": profile_value["profile_id"],
        "profile_input_sha256": canonical_calibration_profile_input_sha256(profile_value),
        "schema_sha256": profile_value["schema_sha256"],
        "policy_sha256": profile_value["policy_sha256"],
        "trust_root_sha256": profile_value["trust_root_sha256"],
        "semantic_validator": SEMANTIC_VALIDATOR_ID,
        "validator_source_sha256": current_validator_source_sha256(),
        "device_identity_sha256": canonical_device_identity_sha256(identity),
        "device_serial": "SERIAL",
        "model": "RSC-1",
        "hardware_revision": "A",
        "firmware_version": "1.2.3",
        "point_map_version": "2026.08",
        "stable_device_path": "/dev/serial/by-id/usb-device",
        "unit_id": 1,
        "baud_rate": 9600,
        "data_bits": 8,
        "parity": "N",
        "stop_bits": 1,
        "valid_from": "2026-08-27T00:00:00+00:00",
        "expires_at": "2026-08-28T00:00:00+00:00",
        "nonce": "0123456789abcdef",
        "safety_owner_present": True,
        "emergency_stop_available": True,
        "plans": [
            {
                "plan_id": "plan-P1",
                "point_id": "P1",
                "point_name": "candidate",
                "point_unit": "degC",
                "function_code": 3,
                "start_address": 1,
                "register_width": 1,
                "bit": None,
                "value_type": "u16",
                "byte_order": "big",
                "word_order": "not_applicable",
                "raw_domain": {"minimum": 0, "maximum": 100},
                "calibration_kind": "analog",
                "state_ids": ["A", "B", "C", "A_RETURN"],
                "sample_count_per_state": 3,
                "instrument_id": "instrument",
                "instrument_calibration_sha256": SHA_ONE,
                "reference_channel_id": "channel",
                "reference_unit": "degC",
                "sync_tolerance_ms": 5,
                "stability_threshold": 0.1,
                "minimum_raw_span": 20,
                "minimum_reference_span": 2,
                "absolute_tolerance": 0.5,
                "relative_tolerance": 0.01,
                "uncertainty_budget": 0.1,
                "analog_aggregation_method": "arithmetic_mean",
                "analog_unit_conversion": {
                    "source_unit": "degC",
                    "target_unit": "degC",
                    "method": "identity",
                    "scale": 1,
                    "offset": 0,
                },
                "analog_exclusion_policy": {
                    "rule_set_id": "analog-exclusions-v1",
                    "rule_set_sha256": SHA_ONE,
                    "allowed_reason_codes": [
                        "INSTRUMENT_OUT_OF_RANGE",
                        "REFERENCE_UNCERTAINTY_EXCEEDED",
                        "SYNC_ERROR_EXCEEDED",
                        "UNSTABLE",
                    ],
                    "maximum_excluded_per_state": 1,
                },
                "analog_business_tolerance_source": {
                    "source_id": "approved-business-tolerance-v1",
                    "source_sha256": SHA_ONE,
                },
                "analog_instrument_capability": {
                    "range_minimum": -10,
                    "range_maximum": 100,
                    "resolution": 0.01,
                    "accuracy": 0.05,
                    "status": "IN_CALIBRATION",
                },
                "maximum_reference_uncertainty": 0.1,
                "return_raw_tolerance": 1,
                "return_engineering_tolerance": 0.5,
                "maximum_chatter_transitions": None,
                "expected_counter_increment": None,
                "counter_increment_tolerance": None,
                "counter_modulus": None,
                "counter_rollover_behavior": None,
                "persistence_required": False,
                "tx_scope": [
                    {
                        "function_code": 3,
                        "start_address": 1,
                        "quantity": 1,
                        "maximum_requests": 20,
                        "write_allowed": False,
                    }
                ],
                "safety_plan_id": "safety-plan",
                "operator_id": "operator",
                "raw_collector_tool_id": "ruisheng.calibration-collector/v1",
                "raw_collector_tool_sha256": SHA_ONE,
                "reference_collector_tool_id": "ruisheng.reference-collector/v1",
                "reference_collector_tool_sha256": SHA_ONE,
            }
        ],
        "approvals": [],
    }
    approval["subject_plan_sha256"] = canonical_calibration_plan_sha256(approval)
    for role in ("project_owner", "device_firmware_owner", "site_safety_owner", "test_owner"):
        entry: dict[str, Any] = {
            "role": role,
            "key_id": f"{role}-key",
            "identity": f"{role}@example.invalid",
            "approved_at": "2026-08-27T04:00:00+00:00",
        }
        entry["signature"] = _signature(
            f"{role}-key",
            keys[role],
            calibration_run_approval_signature_message(approval, entry),
        )
        approval["approvals"].append(entry)
    parsed_approval = CalibrationRunApprovalArtifact.model_validate(approval)
    profile = DevicePointProfile.model_validate(profile_value)
    binding = CalibrationRunApprovalBinding.model_validate(
        {
            "path": "approval/run.json",
            "sha256": SHA_ONE,
            "size_bytes": 1,
            "subject_plan_sha256": approval["subject_plan_sha256"],
        }
    )
    run_artifact = EvidenceArtifact.model_validate(
        {
            "schema_version": 1,
            "artifact_type": "ruisheng.device-point-profile-evidence",
            "evidence_id": "CAL",
            "role": "calibration",
            "profile_id": profile.profile_id,
            "device_identity_sha256": canonical_device_identity_sha256(
                profile.profile_payload.device_identity
            ),
            "device_serial": "SERIAL",
            "run_id": "run-1",
            "calibration_run_approval_sha256": SHA_ONE,
            "subject_point_ids": ["P1"],
            "observed_at": "2026-08-27T05:30:00+00:00",
            "attestor_id": "evidence-runner",
            "content": _analog_evidence(),
            "signature": _dummy_signature("evidence-key"),
        }
    )
    invalid, _ = _calibration_run_approval_reasons(
        parsed_approval,
        binding,
        profile,
        policy,
        current=NOW,
        evidence_artifacts={"CAL": run_artifact},
    )
    assert "RUN_EVIDENCE_PRECEDES_APPROVAL" in {reason.code for reason in invalid}

    cross_run = run_artifact.model_copy(update={"run_id": "run-2"})
    cross_run_invalid, _ = _calibration_run_approval_reasons(
        parsed_approval,
        binding,
        profile,
        policy,
        current=NOW,
        evidence_artifacts={"CAL": cross_run},
    )
    assert "RUN_APPROVAL_BINDING_MISMATCH" in {reason.code for reason in cross_run_invalid}

    unstable_value = run_artifact.model_dump(mode="json")
    unstable_value["content"]["states"][0]["observed_stability"] = 0.2
    unstable = EvidenceArtifact.model_validate(unstable_value)
    unstable_invalid, _ = _calibration_run_approval_reasons(
        parsed_approval,
        binding,
        profile,
        policy,
        current=NOW,
        evidence_artifacts={"CAL": unstable},
    )
    assert "ANALOG_PLAN_THRESHOLD_MISMATCH" in {reason.code for reason in unstable_invalid}


def test_profile_file_rejects_duplicate_keys_and_symlink(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    assert validate_profile_file(duplicate, root=tmp_path).decision == "INVALID"
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    assert validate_profile_file(link, root=tmp_path).decision == "INVALID"


@pytest.mark.parametrize(
    "contents",
    [
        b'{"schema_version":1,"schema_version":1}',
        b'{"outer":{"evidence_id":"A","evidence_id":"B"}}',
    ],
)
def test_shared_json_loader_rejects_duplicate_keys_at_every_depth(contents: bytes) -> None:
    with pytest.raises(ValueError, match="duplicate JSON key"):
        _load_json_bytes(contents)


def test_release_receipt_rejects_receipt_id_not_derived_from_snapshot(
    tmp_path: Path,
) -> None:
    contract, _policy, _trust_root = _synthetic_eligible_contract(tmp_path)
    binding = contract["runtime_target"]["release_verification_receipt"]
    receipt_path = tmp_path / binding["path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["receipt_id"] = "receipt-" + "f" * 64

    with pytest.raises(ValidationError, match="receipt ID must derive"):
        ReleaseVerificationReceipt.model_validate(receipt)


def test_legacy_mapping_remains_read_only_and_blocked() -> None:
    before = (LEGACY.stat().st_size, LEGACY.stat().st_mtime_ns)
    report = validate_legacy_evidence_file(LEGACY, root=ROOT, now=NOW)
    after = (LEGACY.stat().st_size, LEGACY.stat().st_mtime_ns)
    assert report.decision == "BLOCKED"
    assert _codes(report) == {"FRESHNESS_CONTEXT_REQUIRED"}
    assert before == after
    candidate = candidate_profile_from_legacy_evidence(
        json.loads(LEGACY.read_text(encoding="utf-8")),
        evidence_path=LEGACY.relative_to(ROOT).as_posix(),
        evidence_sha256=SHA_ZERO,
        evidence_size_bytes=LEGACY.stat().st_size,
    )
    assert candidate["profile_payload"]["line_protocol"]["stable_device_path"] is None
    assert candidate["trust_root_sha256"] is None


def _minimal_resolved_point(
    point_id: str,
    *,
    function_code: int = 3,
    start_address: int = 10,
    register_width: int = 1,
    bit: int | None = None,
) -> dict[str, Any]:
    value_type = "bit" if bit is not None or function_code in (1, 2) else "u16"
    return {
        "point_id": point_id,
        "point_name": point_id,
        "function_code": function_code,
        "start_address": start_address,
        "register_width": register_width,
        "bit": bit,
        "identity_status": "resolved",
        "semantic_status": "resolved",
        "encoding_status": "resolved",
        "unit_status": "resolved",
        "calibration_status": "unknown",
        "implementation_status": "supported",
        "encoding": {
            "value_type": value_type,
            "byte_order": "not_applicable" if value_type == "bit" else "big",
            "word_order": "not_applicable",
            "raw_domain": {"minimum": 0, "maximum": 1 if value_type == "bit" else 65535},
        },
        "unit": "state" if value_type == "bit" else "count",
        "calibration_profile": {"kind": "unknown", "method": "unknown"},
        "evidence_refs": [],
    }


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (
            {
                **_minimal_resolved_point("P1", start_address=10, register_width=2),
                "encoding": {
                    "value_type": "u32",
                    "byte_order": "big",
                    "word_order": "high_first",
                    "raw_domain": {"minimum": 0, "maximum": 4294967295},
                },
            },
            _minimal_resolved_point("P2", start_address=11),
        ),
        (
            _minimal_resolved_point("P1", start_address=10, bit=3),
            _minimal_resolved_point("P2", start_address=10, bit=3),
        ),
        (
            _minimal_resolved_point("P1", start_address=10, bit=3),
            _minimal_resolved_point("P2", start_address=10),
        ),
    ],
)
def test_resolved_profile_rejects_overlapping_modbus_geometry(
    left: dict[str, Any],
    right: dict[str, Any],
) -> None:
    from tools.validate_device_point_profile import ProfilePayload

    with pytest.raises(ValidationError, match="resolved Modbus point ranges must not overlap"):
        ProfilePayload.model_validate(
            {
                "device_identity": {
                    "status": "unknown",
                    "model": None,
                    "hardware_revision": None,
                    "firmware_version": None,
                    "point_map_version": None,
                    "usb_serial_number": None,
                    "evidence_refs": [],
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
                "points": [left, right],
            }
        )


def test_resolved_profile_allows_distinct_bits_in_one_register() -> None:
    payload = {
        "device_identity": {
            "status": "unknown",
            "model": None,
            "hardware_revision": None,
            "firmware_version": None,
            "point_map_version": None,
            "usb_serial_number": None,
            "evidence_refs": [],
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
        "points": [
            _minimal_resolved_point("P1", bit=3),
            _minimal_resolved_point("P2", bit=4),
        ],
    }
    from tools.validate_device_point_profile import ProfilePayload

    ProfilePayload.model_validate(payload)


@pytest.mark.parametrize("field", ["fun_code", "point_number"])
@pytest.mark.parametrize("bad_value", [True, 3.0, "3"])
def test_legacy_mapper_rejects_non_integer_modbus_fields(
    field: str,
    bad_value: object,
) -> None:
    legacy = json.loads(LEGACY.read_text(encoding="utf-8"))
    legacy["candidates"] = [copy.deepcopy(legacy["candidates"][0])]
    legacy["candidates"][0][field] = bad_value

    with pytest.raises(ValueError, match=rf"legacy {field} must be an integer"):
        candidate_profile_from_legacy_evidence(
            legacy,
            evidence_path=LEGACY.relative_to(ROOT).as_posix(),
            evidence_sha256=SHA_ZERO,
            evidence_size_bytes=LEGACY.stat().st_size,
        )


def test_legacy_mapper_preserves_one_model_as_candidate() -> None:
    legacy = json.loads(LEGACY.read_text(encoding="utf-8"))
    legacy["candidates"] = [copy.deepcopy(legacy["candidates"][0])]

    profile = candidate_profile_from_legacy_evidence(
        legacy,
        evidence_path=LEGACY.relative_to(ROOT).as_posix(),
        evidence_sha256=SHA_ZERO,
        evidence_size_bytes=LEGACY.stat().st_size,
    )

    identity = profile["profile_payload"]["device_identity"]
    assert identity["status"] == "candidate"
    assert identity["model"] == legacy["candidates"][0]["model_candidate"]


def _clone_evidence_artifact(
    artifact: EvidenceArtifact,
    evidence_id: str,
    *,
    content: dict[str, Any] | None = None,
) -> EvidenceArtifact:
    value = artifact.model_dump(mode="json")
    value["evidence_id"] = evidence_id
    if content is not None:
        value["content"] = content
    value["signature"] = _dummy_signature()
    return EvidenceArtifact.model_validate(value)


@pytest.mark.parametrize("role", ["calibration", "reference", "raw_observation"])
def test_hidden_second_run_evidence_is_rejected_globally(
    tmp_path: Path,
    role: str,
) -> None:
    profile_value, _, _ = _synthetic_eligible_contract(tmp_path)
    profile = DevicePointProfile.model_validate(profile_value)
    evidence_id = {
        "calibration": "CAL-ANALOG",
        "reference": "REF-ANALOG",
        "raw_observation": "RAW-ANALOG",
    }[role]
    artifact = _synthetic_evidence_artifacts(tmp_path, profile_value, evidence_id)[evidence_id]
    duplicate = _clone_evidence_artifact(artifact, f"{evidence_id}-HIDDEN")

    reasons = _evidence_ownership_reasons(
        profile,
        {evidence_id: artifact, duplicate.evidence_id: duplicate},
    )

    assert {reason.code for reason in reasons} >= {
        "EVIDENCE_OWNERSHIP_INVALID",
        "RUN_EVIDENCE_CARDINALITY_INVALID",
    }


def test_device_identity_cannot_own_point_run_evidence(tmp_path: Path) -> None:
    profile_value, _, _ = _synthetic_eligible_contract(tmp_path)
    profile_value["profile_payload"]["device_identity"]["evidence_refs"].append("CAL-ANALOG")
    profile_value["payload_sha256"] = canonical_payload_sha256(profile_value["profile_payload"])
    profile = DevicePointProfile.model_validate(profile_value)
    artifacts = _synthetic_evidence_artifacts(
        tmp_path,
        profile_value,
        "IDENTITY",
        "MAP",
        "CAL-ANALOG",
    )

    reasons = _evidence_ownership_reasons(profile, artifacts)

    assert "IDENTITY_EVIDENCE_ROLE_INVALID" in {reason.code for reason in reasons}


def test_point_cannot_own_device_identity_evidence(tmp_path: Path) -> None:
    profile_value, _, _ = _synthetic_eligible_contract(tmp_path)
    profile_value["profile_payload"]["points"][0]["evidence_refs"].append("IDENTITY")
    profile_value["payload_sha256"] = canonical_payload_sha256(profile_value["profile_payload"])
    profile = DevicePointProfile.model_validate(profile_value)
    artifacts = _synthetic_evidence_artifacts(
        tmp_path,
        profile_value,
        "IDENTITY",
        "MAP",
        "CAL-ANALOG",
        "REF-ANALOG",
        "RAW-ANALOG",
    )

    reasons = _evidence_ownership_reasons(profile, artifacts)

    assert "POINT_EVIDENCE_OWNER_INVALID" in {reason.code for reason in reasons}


def test_all_trusted_identity_artifacts_must_agree(tmp_path: Path) -> None:
    profile_value, _, _ = _synthetic_eligible_contract(tmp_path)
    profile = DevicePointProfile.model_validate(profile_value)
    identity = profile.profile_payload.device_identity
    identity_artifact = _synthetic_evidence_artifacts(tmp_path, profile_value, "IDENTITY")[
        "IDENTITY"
    ]
    conflicting_content = identity_artifact.content.model_dump(mode="json")
    conflicting_content["firmware_version"] = "9.9.9"
    conflicting = _clone_evidence_artifact(
        identity_artifact,
        "IDENTITY-CONFLICT",
        content=conflicting_content,
    )

    reasons = _identity_evidence_conflict_reasons(
        identity,
        canonical_device_identity_sha256(identity),
        (identity_artifact, conflicting),
    )

    assert "IDENTITY_EVIDENCE_CONFLICT" in {reason.code for reason in reasons}


def test_all_trusted_authoritative_maps_must_agree(tmp_path: Path) -> None:
    profile_value, _, _ = _synthetic_eligible_contract(tmp_path)
    profile = DevicePointProfile.model_validate(profile_value)
    map_artifact = _synthetic_evidence_artifacts(tmp_path, profile_value, "MAP")["MAP"]
    conflicting_content = map_artifact.content.model_dump(mode="json")
    conflicting_content["points"][0]["start_address"] += 1
    conflicting = _clone_evidence_artifact(
        map_artifact,
        "MAP-CONFLICT",
        content=conflicting_content,
    )

    reasons = _authoritative_map_conflict_reasons(profile, (map_artifact, conflicting))

    assert "AUTHORITATIVE_MAP_CONFLICT" in {reason.code for reason in reasons}
