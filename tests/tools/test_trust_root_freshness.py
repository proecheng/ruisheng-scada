from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from tools.trust_root_freshness import (
    MAX_FRESHNESS_JSON_BYTES,
    FreshnessAttestation,
    FreshnessComparison,
    FreshnessGateDecision,
    FreshnessProviderConfig,
    FreshnessRequest,
    FreshnessState,
    FreshnessValidationError,
    compare_freshness_states,
    freshness_attestation_signature_message,
    main,
    parse_freshness_attestation,
    validate_live_freshness_attestation,
)

NOW = datetime(2026, 8, 30, 8, 0, tzinfo=UTC)
SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64


def _state(**updates: Any) -> FreshnessState:
    value: dict[str, Any] = {
        "root_id": "site-a-root",
        "root_version": 3,
        "root_revocation_sequence": 11,
        "root_sha256": SHA_A,
        "policy_id": "site-a-policy",
        "policy_version": 2,
        "policy_revocation_sequence": 7,
        "policy_sha256": SHA_B,
    }
    value.update(updates)
    return FreshnessState.model_validate(value)


def _request(**updates: Any) -> FreshnessRequest:
    value: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "ruisheng.trust-root-freshness-request",
        "site_id": "site-a",
        "challenge": "A" * 43,
        "requested_at": NOW.isoformat(),
        "candidate_logical_identity": SHA_A,
        "root_snapshot_sha256": SHA_A,
        "provider_config_sha256": SHA_B,
        "profile_id": "profile-a",
        "profile_sha256": SHA_A,
        "payload_sha256": SHA_B,
        "canonical_gate_sha256": SHA_A,
        "semantic_validator": "ruisheng.device-point-profile-validator/v5",
        "validator_source_sha256": SHA_B,
        "verifier_id": "ruisheng.verify-publisher.posix-v1",
        "verifier_tool_sha256": SHA_A,
        "state": _state().model_dump(mode="json"),
    }
    value.update(updates)
    return FreshnessRequest.model_validate(value)


def _config(key: Ed25519PrivateKey) -> FreshnessProviderConfig:
    return FreshnessProviderConfig.model_validate(
        {
            "schema_version": 1,
            "artifact_type": "ruisheng.trust-root-freshness-provider-config",
            "site_id": "site-a",
            "provider_id": "site-a-independent-witness",
            "witness_key_id": "freshness-key-1",
            "witness_public_key": base64.b64encode(key.public_key().public_bytes_raw()).decode(),
            "verifier_id": "ruisheng.verify-publisher.posix-v1",
            "verifier_tool_sha256": SHA_A,
            "monotonic_state_id": "site-a-root-policy-high-water",
            "minimum_monotonic_counter": 42,
            "maximum_clock_skew_seconds": 60,
            "maximum_attestation_lifetime_seconds": 300,
        }
    )


def _attestation(
    key: Ed25519PrivateKey,
    *,
    request: FreshnessRequest | None = None,
    high_water: FreshnessState | None = None,
    monotonic_state_id: str = "site-a-root-policy-high-water",
    monotonic_counter: int = 42,
    observed_at: datetime = NOW,
    expires_at: datetime | None = None,
) -> FreshnessAttestation:
    value: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "ruisheng.trust-root-freshness-attestation",
        "provider_id": "site-a-independent-witness",
        "witness_key_id": "freshness-key-1",
        "request": (request or _request()).model_dump(mode="json"),
        "high_water": (high_water or _state()).model_dump(mode="json"),
        "monotonic_state_id": monotonic_state_id,
        "monotonic_counter": monotonic_counter,
        "observed_at": observed_at.isoformat(),
        "expires_at": (expires_at or observed_at + timedelta(minutes=5)).isoformat(),
        "signature": {
            "algorithm": "Ed25519",
            "key_id": "freshness-key-1",
            "value": base64.b64encode(b"0" * 64).decode(),
        },
    }
    value["signature"] = {
        "algorithm": "Ed25519",
        "key_id": "freshness-key-1",
        "value": base64.b64encode(
            key.sign(freshness_attestation_signature_message(value))
        ).decode(),
    }
    return FreshnessAttestation.model_validate(value)


def test_freshness_comparison_accepts_only_exact_idempotent_state() -> None:
    result = compare_freshness_states(_state(), _state())

    assert result.decision == "EXACT"
    assert result.reason_code == "FRESHNESS_EXACT"


@pytest.mark.parametrize(
    ("updates", "reason"),
    (
        ({"root_version": 2}, "FRESHNESS_ROOT_VERSION_ROLLBACK"),
        ({"root_revocation_sequence": 10}, "FRESHNESS_ROOT_REVOCATION_ROLLBACK"),
        ({"policy_version": 1}, "FRESHNESS_POLICY_VERSION_ROLLBACK"),
        ({"policy_revocation_sequence": 6}, "FRESHNESS_POLICY_REVOCATION_ROLLBACK"),
        ({"root_id": "other-root"}, "FRESHNESS_ROOT_ID_SWITCH"),
        ({"policy_id": "other-policy"}, "FRESHNESS_POLICY_ID_SWITCH"),
        ({"root_sha256": SHA_B}, "FRESHNESS_ROOT_HASH_CONFLICT"),
        ({"policy_sha256": SHA_A}, "FRESHNESS_POLICY_HASH_CONFLICT"),
    ),
)
def test_freshness_comparison_classifies_rollback_and_conflicts_invalid(
    updates: dict[str, Any], reason: str
) -> None:
    result = compare_freshness_states(_state(**updates), _state())

    assert result.decision == "INVALID"
    assert result.reason_code == reason


@pytest.mark.parametrize(
    "updates",
    (
        {"root_version": 4, "root_sha256": SHA_B},
        {"root_revocation_sequence": 12},
        {"policy_version": 3, "policy_sha256": SHA_A},
        {"policy_revocation_sequence": 8},
    ),
)
def test_freshness_comparison_classifies_unprovisioned_local_state_blocked(
    updates: dict[str, Any],
) -> None:
    result = compare_freshness_states(_state(**updates), _state())

    assert result.decision == "BLOCKED"
    assert result.reason_code == "FRESHNESS_LOCAL_STATE_AHEAD"


def test_live_attestation_verifies_signature_time_request_and_high_water() -> None:
    key = Ed25519PrivateKey.generate()
    request = _request()
    attestation = _attestation(key, request=request)

    validated = validate_live_freshness_attestation(
        attestation,
        expected_request=request,
        provider_config=_config(key),
        now=NOW,
    )

    assert validated == attestation


def test_live_attestation_rejects_replayed_challenge() -> None:
    key = Ed25519PrivateKey.generate()
    expected = _request()
    replayed = _attestation(key, request=_request(challenge="Q" * 43))

    with pytest.raises(FreshnessValidationError) as error:
        validate_live_freshness_attestation(
            replayed,
            expected_request=expected,
            provider_config=_config(key),
            now=NOW,
        )

    assert error.value.decision == "INVALID"
    assert error.value.reason_code == "FRESHNESS_REQUEST_MISMATCH"


def test_live_attestation_rejects_clock_rollback() -> None:
    key = Ed25519PrivateKey.generate()
    attestation = _attestation(key, observed_at=NOW + timedelta(minutes=10))

    with pytest.raises(FreshnessValidationError) as error:
        validate_live_freshness_attestation(
            attestation,
            expected_request=_request(),
            provider_config=_config(key),
            now=NOW,
        )

    assert error.value.decision == "INVALID"
    assert error.value.reason_code == "FRESHNESS_CLOCK_SKEW"


def test_timestamp_boundary_returns_invalid_instead_of_overflow() -> None:
    key = Ed25519PrivateKey.generate()
    boundary = datetime.min.replace(tzinfo=UTC)
    request = _request(requested_at=boundary.isoformat())
    attestation = _attestation(
        key,
        request=request,
        observed_at=boundary,
        expires_at=boundary + timedelta(seconds=1),
    )

    with pytest.raises(FreshnessValidationError) as error:
        validate_live_freshness_attestation(
            attestation,
            expected_request=request,
            provider_config=_config(key),
            now=boundary,
        )

    assert error.value.decision == "INVALID"
    assert error.value.reason_code == "FRESHNESS_TIMESTAMP_INVALID"


def test_live_attestation_rejects_invalid_signature_and_expiry() -> None:
    signing_key = Ed25519PrivateKey.generate()
    config_key = Ed25519PrivateKey.generate()

    with pytest.raises(FreshnessValidationError) as signature_error:
        validate_live_freshness_attestation(
            _attestation(signing_key),
            expected_request=_request(),
            provider_config=_config(config_key),
            now=NOW,
        )
    with pytest.raises(FreshnessValidationError) as expiry_error:
        validate_live_freshness_attestation(
            _attestation(signing_key, expires_at=NOW),
            expected_request=_request(),
            provider_config=_config(signing_key),
            now=NOW,
        )

    assert signature_error.value.reason_code == "FRESHNESS_SIGNATURE_INVALID"
    assert expiry_error.value.reason_code == "FRESHNESS_CLOCK_SKEW"


@pytest.mark.parametrize(
    ("attestation_kwargs", "reason"),
    (
        (
            {"monotonic_state_id": "other-monotonic-state"},
            "FRESHNESS_MONOTONIC_STATE_MISMATCH",
        ),
        ({"monotonic_counter": 41}, "FRESHNESS_MONOTONIC_COUNTER_ROLLBACK"),
    ),
)
def test_live_attestation_rejects_monotonic_state_rollback(
    attestation_kwargs: dict[str, Any], reason: str
) -> None:
    key = Ed25519PrivateKey.generate()
    attestation = _attestation(key, **attestation_kwargs)

    with pytest.raises(FreshnessValidationError) as error:
        validate_live_freshness_attestation(
            attestation,
            expected_request=_request(),
            provider_config=_config(key),
            now=NOW,
        )

    assert error.value.decision == "INVALID"
    assert error.value.reason_code == reason


def test_attestation_parser_rejects_duplicate_keys() -> None:
    value = json.dumps(_attestation(Ed25519PrivateKey.generate()).model_dump(mode="json"))
    duplicate = value.replace('"provider_id":', '"provider_id":"duplicate","provider_id":', 1)

    with pytest.raises(FreshnessValidationError) as error:
        parse_freshness_attestation(duplicate.encode())

    assert error.value.decision == "INVALID"
    assert error.value.reason_code == "FRESHNESS_ATTESTATION_INVALID"


def test_attestation_parser_rejects_before_parsing_over_budget_input() -> None:
    with pytest.raises(FreshnessValidationError) as error:
        parse_freshness_attestation(b" " * (MAX_FRESHNESS_JSON_BYTES + 1))

    assert error.value.reason_code == "FRESHNESS_ATTESTATION_TOO_LARGE"


def test_freshness_request_rejects_noncanonical_256_bit_challenge() -> None:
    with pytest.raises(ValidationError):
        _request(challenge="_" * 43)


def test_provider_config_forbids_caller_selected_transport_fields() -> None:
    key = Ed25519PrivateKey.generate()
    value = _config(key).model_dump(mode="json") | {"provider_executable": "attacker.exe"}

    with pytest.raises(ValidationError):
        FreshnessProviderConfig.model_validate(value)


@pytest.mark.parametrize(
    ("decision", "expected_exit"),
    (("EXACT", 0), ("BLOCKED", 2), ("INVALID", 3)),
)
def test_preflight_cli_emits_machine_readable_decision_and_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    decision: FreshnessGateDecision,
    expected_exit: int,
) -> None:
    monkeypatch.setattr(
        "tools.trust_root_freshness.preflight_freshness",
        lambda **kwargs: FreshnessComparison(  # noqa: ARG005
            decision=decision,
            reason_code="FRESHNESS_EXACT" if decision == "EXACT" else "FRESHNESS_TEST",
        ),
    )

    exit_code = main(
        [
            "preflight",
            "profile.json",
            "--trust-policy",
            "policy.json",
            "--trust-root-snapshot",
            "root.json",
            "--provider-config-snapshot",
            "config.json",
            "--attestation",
            "attestation.json",
            "--challenge",
            "A" * 43,
            "--requested-at",
            NOW.isoformat(),
            "--candidate-logical-identity",
            SHA_A,
            "--expected-trust-root-snapshot-sha256",
            SHA_A,
            "--expected-provider-config-snapshot-sha256",
            SHA_A,
            "--expected-attestation-sha256",
            SHA_A,
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == expected_exit
    assert output["decision"] == decision
