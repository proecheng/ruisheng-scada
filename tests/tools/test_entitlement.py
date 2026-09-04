from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tools import entitlement

NOW = datetime(2026, 9, 4, tzinfo=UTC)
PASSWORD = b"correct horse battery staple"
OPERATION = "00000000-0000-4000-8000-000000000001"
ORIGINAL_PARENT_CHECK = entitlement._require_protected_private_key_parent
ORIGINAL_LEAF_CHECK = entitlement._require_protected_private_key_leaf


@pytest.fixture(autouse=True)
def _allow_test_signing_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entitlement, "_require_protected_private_key_parent", lambda _path: None)
    monkeypatch.setattr(entitlement, "_require_protected_private_key_leaf", lambda _path: None)
    monkeypatch.setattr(entitlement, "_set_protected_private_key_acl", lambda _path: None)


def _keys(tmp_path: Path, *, key_id: str = "entitlement-2026") -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    private_path = tmp_path / "entitlement-private.pem"
    public_path = tmp_path / "entitlement-public-key"
    private_path.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(PASSWORD),
        )
    )
    public_path.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
        )
        + b" "
        + key_id.encode()
        + b"\n"
    )
    return private_path, public_path


def _grant(
    tmp_path: Path,
    *,
    private: Path | None = None,
    public: Path | None = None,
    site_id: str = "site-1",
    serial: int = 1,
    grant_id: str | None = None,
    issued: datetime = NOW,
    start: datetime = NOW,
    end: datetime = datetime(2027, 9, 4, tzinfo=UTC),
) -> tuple[dict[str, object], Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    if private is None or public is None:
        private, public = _keys(tmp_path)
    grant = entitlement.issue_grant(
        private_key_path=private,
        private_key_password=PASSWORD,
        key_id="entitlement-2026",
        site_id=site_id,
        customer_id="customer-1",
        plan="annual",
        features=["support", "upgrade"],
        serial=serial,
        start=start,
        end=end,
        issued=issued,
        grant_id=grant_id,
        now=NOW,
    )
    path = tmp_path / f"grant-{serial}.json"
    path.write_bytes(entitlement.canonical_artifact_bytes(grant))
    return grant, path, private, public


def _install(
    grant: Path,
    public: Path,
    state: Path,
    audit: Path,
    *,
    operation: str = OPERATION,
    fault_operation: bool = False,
    fault: bool = False,
) -> dict[str, object]:
    return entitlement.install_grant(
        grant_path=grant,
        public_key_path=public,
        state_path=state,
        audit_path=audit,
        site_id="site-1",
        operation_id=operation,
        reason="approved annual entitlement",
        now=NOW,
        fault_after_operation_write=fault_operation,
        fault_after_state_replace=fault,
    )


def test_canonical_grant_has_exact_boundaries_and_key_binding(tmp_path: Path) -> None:
    grant, path, _, public = _grant(tmp_path)
    payload, digest = entitlement.verify_grant_file(
        path, public, expected_site_id="site-1", now=NOW, for_install=True
    )
    assert path.read_bytes() == entitlement.canonical_artifact_bytes(grant)
    assert digest == entitlement.sha256_bytes(path.read_bytes())
    assert payload["starts_at"] == "2026-09-04T00:00:00+00:00"
    assert payload["expires_at"] == "2027-09-04T00:00:00+00:00"
    assert grant["signature"]["key_id"] == "entitlement-2026"  # type: ignore[index]


def test_example_entitlement_public_key_is_valid() -> None:
    example = Path(__file__).parents[2] / "deploy" / "entitlement-public-key.example"
    public_key, key_id = entitlement._public_key(example)
    assert key_id == "entitlement-2026"
    assert (
        len(public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
        == 32
    )


@pytest.mark.parametrize("schema_version", [True, 1.0, "1", None])
def test_schema_version_requires_an_actual_integer(tmp_path: Path, schema_version: object) -> None:
    grant, path, _, public = _grant(tmp_path)
    grant["schema_version"] = schema_version
    path.write_bytes(entitlement.canonical_artifact_bytes(grant))
    with pytest.raises(entitlement.EntitlementError, match="schema_version_invalid"):
        entitlement.load_grant(path)


def test_grant_duration_is_limited_to_366_days(tmp_path: Path) -> None:
    private, _ = _keys(tmp_path)
    with pytest.raises(entitlement.EntitlementError, match="grant_duration_too_long"):
        entitlement.issue_grant(
            private_key_path=private,
            private_key_password=PASSWORD,
            key_id="entitlement-2026",
            site_id="site-1",
            customer_id="customer-1",
            plan="annual",
            features=["remote-support"],
            serial=1,
            start=NOW,
            end=NOW + timedelta(days=366, seconds=1),
            issued=NOW,
            now=NOW,
        )


@pytest.mark.parametrize(
    "transform,error",
    [
        (lambda raw: raw.rstrip(b"\n"), "grant_not_canonical"),
        (lambda raw: b"\xef\xbb\xbf" + raw, "grant_encoding_invalid"),
        (
            lambda raw: raw.replace(b'"site_id":', b'"site_id":"duplicate","site_id":', 1),
            "duplicate_json_key",
        ),
        (
            lambda raw: raw.replace(b'"signature":{', b'"signature":{"extra":1,', 1),
            "signature_fields_invalid",
        ),
    ],
)
def test_noncanonical_duplicate_and_open_signature_documents_are_rejected(
    tmp_path: Path, transform: Callable[[bytes], bytes], error: str
) -> None:
    _, path, _, public = _grant(tmp_path)
    path.write_bytes(transform(path.read_bytes()))
    with pytest.raises(entitlement.EntitlementError, match=error):
        entitlement.verify_grant_file(path, public, expected_site_id="site-1", now=NOW)


def test_signature_site_and_public_key_comment_fail_closed(tmp_path: Path) -> None:
    grant, path, _, public = _grant(tmp_path)
    altered = dict(grant)
    altered["customer_id"] = "customer-2"
    path.write_bytes(entitlement.canonical_artifact_bytes(altered))
    with pytest.raises(entitlement.EntitlementError, match="signature_invalid"):
        entitlement.verify_grant_file(path, public, expected_site_id="site-1", now=NOW)
    path.write_bytes(entitlement.canonical_artifact_bytes(grant))
    with pytest.raises(entitlement.EntitlementError, match="site_mismatch"):
        entitlement.verify_grant_file(path, public, expected_site_id="site-2", now=NOW)
    public.write_bytes(public.read_bytes().replace(b"entitlement-2026", b"entitlement-rotated"))
    with pytest.raises(entitlement.EntitlementError, match="unknown_key_id"):
        entitlement.verify_grant_file(path, public, expected_site_id="site-1", now=NOW)


def test_install_rejects_future_issued_start_and_past_grace_with_negative_audit(
    tmp_path: Path,
) -> None:
    private, public = _keys(tmp_path)
    state = tmp_path / "state" / "current.json"
    audit = tmp_path / "state" / "audit.jsonl"
    future, path, _, _ = _grant(tmp_path / "future", private=private, public=public)
    # Build a validly signed hostile artifact because the issuer refuses future issued_at.
    future["issued_at"] = "2026-09-04T00:00:01+00:00"
    key = cast(
        Ed25519PrivateKey,
        serialization.load_pem_private_key(private.read_bytes(), password=PASSWORD),
    )
    payload = {name: future[name] for name in entitlement.PAYLOAD_KEYS}
    future["signature"] = {
        "algorithm": "Ed25519",
        "key_id": "entitlement-2026",
        "value": __import__("base64").b64encode(key.sign(entitlement._message(payload))).decode(),
    }
    path.write_bytes(entitlement.canonical_artifact_bytes(future))
    with pytest.raises(entitlement.EntitlementError, match="issued_in_future"):
        _install(path, public, state, audit)
    assert not state.exists()
    assert b"issued_in_future" in audit.read_bytes()

    _, future_start_path, _, _ = _grant(
        tmp_path / "future-start",
        private=private,
        public=public,
        serial=2,
        start=NOW + timedelta(seconds=1),
    )
    with pytest.raises(entitlement.EntitlementError, match="starts_in_future"):
        _install(
            future_start_path,
            public,
            state,
            audit,
            operation="00000000-0000-4000-8000-000000000002",
        )
    assert not state.exists()
    assert b"starts_in_future" in audit.read_bytes()

    _, expired_path, _, _ = _grant(
        tmp_path / "expired",
        private=private,
        public=public,
        serial=2,
        issued=NOW - timedelta(days=20),
        start=NOW - timedelta(days=20),
        end=NOW - timedelta(days=10),
    )
    with pytest.raises(entitlement.EntitlementError, match="grant_expired"):
        _install(
            expired_path,
            public,
            state,
            audit,
            operation="00000000-0000-4000-8000-000000000003",
        )
    assert b"grant_expired" in audit.read_bytes()


def test_operation_identity_replay_and_state_preservation(tmp_path: Path) -> None:
    grant, first_path, private, public = _grant(
        tmp_path, grant_id="grant-one", end=datetime(2027, 1, 4, tzinfo=UTC)
    )
    state = tmp_path / "state" / "current.json"
    audit = tmp_path / "state" / "audit.jsonl"
    first = _install(first_path, public, state, audit)
    assert first["status"] == "installed"
    assert _install(first_path, public, state, audit)["idempotent"] is True
    original = state.read_bytes()
    _, second_path, _, _ = _grant(
        tmp_path / "second",
        private=private,
        public=public,
        serial=2,
        grant_id="grant-two",
        end=datetime(2027, 9, 4, tzinfo=UTC),
    )
    with pytest.raises(entitlement.EntitlementError, match="operation_conflict"):
        _install(second_path, public, state, audit)
    assert state.read_bytes() == original
    with pytest.raises(entitlement.EntitlementError, match="replay_rejected"):
        _install(
            first_path,
            public,
            state,
            audit,
            operation="00000000-0000-4000-8000-000000000003",
        )
    assert state.read_bytes() == original
    assert grant["grant_id"] == "grant-one"
    assert b"operation_conflict" in audit.read_bytes()
    assert b"replay_rejected" in audit.read_bytes()


def test_claimed_site_mismatch_is_audited_before_state_replacement(tmp_path: Path) -> None:
    _, grant_path, _, public = _grant(tmp_path)
    state = tmp_path / "state" / "current.json"
    audit = tmp_path / "state" / "audit.jsonl"

    with pytest.raises(entitlement.EntitlementError, match="site_mismatch"):
        entitlement.install_grant(
            grant_path=grant_path,
            public_key_path=public,
            state_path=state,
            audit_path=audit,
            site_id="site-1",
            claimed_site_id="site-2",
            operation_id=OPERATION,
            reason="approved annual entitlement",
            now=NOW,
        )

    assert not state.exists()
    operation = entitlement._read_operation(entitlement._operation_path(state, OPERATION))
    assert operation is not None
    assert operation["error_code"] == "site_mismatch"
    assert b'"error_code":"site_mismatch"' in audit.read_bytes()


def test_protected_time_failure_does_not_overwrite_terminal_operation(tmp_path: Path) -> None:
    _, grant_path, _, public = _grant(tmp_path)
    state = tmp_path / "state" / "current.json"
    audit = tmp_path / "state" / "audit.jsonl"
    _install(grant_path, public, state, audit)
    operation_path = entitlement._operation_path(state, OPERATION)
    terminal_record = operation_path.read_bytes()

    with pytest.raises(entitlement.EntitlementError, match="clock_rollback"):
        entitlement.install_grant(
            grant_path=grant_path,
            public_key_path=public,
            state_path=state,
            audit_path=audit,
            site_id="site-1",
            operation_id=OPERATION,
            reason="approved annual entitlement",
            now=NOW - timedelta(days=1),
        )

    assert operation_path.read_bytes() == terminal_record
    operation = entitlement._read_operation(operation_path)
    assert operation is not None
    assert operation["status"] == "installed"


def test_idempotent_operation_requires_matching_current_state(tmp_path: Path) -> None:
    _, path, _, public = _grant(tmp_path)
    state = tmp_path / "state" / "current.json"
    audit = tmp_path / "state" / "audit.jsonl"
    _install(path, public, state, audit)
    state.unlink()
    with pytest.raises(entitlement.EntitlementError, match="transaction_uncertain"):
        _install(path, public, state, audit)


def test_crash_journal_is_completed_before_next_mutation(tmp_path: Path) -> None:
    _, path, _, public = _grant(tmp_path)
    state = tmp_path / "state" / "current.json"
    audit = tmp_path / "state" / "audit.jsonl"
    with pytest.raises(RuntimeError, match="injected_crash"):
        _install(path, public, state, audit, fault=True)
    assert (state.parent / "transaction.json").exists()
    assert (
        entitlement.status_grant(
            state_path=state, public_key_path=public, site_id="site-1", now=NOW
        )["status"]
        == "uncertain"
    )
    recovered = _install(path, public, state, audit)
    assert recovered["idempotent"] is True
    assert not (state.parent / "transaction.json").exists()
    assert b"installed_recovered" in audit.read_bytes()


def test_first_install_journal_is_uncertain_before_missing_state(tmp_path: Path) -> None:
    state = tmp_path / "state" / "current.json"
    journal = state.parent / "transaction.json"
    journal.parent.mkdir(parents=True)
    journal.write_bytes(entitlement.canonical_artifact_bytes({"incomplete": True}))
    result = entitlement.status_grant(
        state_path=state,
        public_key_path=tmp_path / "unused-public-key",
        site_id="site-1",
        now=NOW,
    )
    assert result["status"] == "uncertain"
    assert json.loads((state.parent / "last-seen.json").read_text())["last_seen_utc"] == (
        NOW.isoformat(timespec="seconds")
    )


def test_recovery_refuses_to_overwrite_an_unrecognized_third_state(tmp_path: Path) -> None:
    _, path, _, public = _grant(tmp_path)
    state = tmp_path / "state" / "current.json"
    audit = tmp_path / "state" / "audit.jsonl"
    with pytest.raises(RuntimeError, match="injected_crash_after_state_replace"):
        _install(path, public, state, audit, fault=True)
    state.write_bytes(b'{"third":"state"}\n')
    with pytest.raises(entitlement.EntitlementError, match="transaction_uncertain"):
        _install(path, public, state, audit)
    assert state.read_bytes() == b'{"third":"state"}\n'
    assert (state.parent / "transaction.json").exists()


def test_crash_before_journal_resumes_the_same_operation_safely(tmp_path: Path) -> None:
    _, path, _, public = _grant(tmp_path)
    state = tmp_path / "state" / "current.json"
    audit = tmp_path / "state" / "audit.jsonl"
    with pytest.raises(RuntimeError, match="injected_crash_after_operation_write"):
        _install(path, public, state, audit, fault_operation=True)
    assert not state.exists()
    assert not (state.parent / "transaction.json").exists()

    recovered = _install(path, public, state, audit)
    assert recovered["idempotent"] is False
    assert state.read_bytes() == path.read_bytes()
    assert b"interrupted_before_journal" in audit.read_bytes()


def test_post_state_audit_failure_is_reported_as_uncertain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, path, _, public = _grant(tmp_path)
    state = tmp_path / "state" / "current.json"
    audit = tmp_path / "state" / "audit.jsonl"

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise entitlement.EntitlementError("audit_write_failed")

    monkeypatch.setattr(entitlement, "_append_audit_locked", fail_audit)
    with pytest.raises(entitlement.EntitlementError, match="transaction_uncertain"):
        _install(path, public, state, audit)
    assert state.read_bytes() == path.read_bytes()
    assert (state.parent / "transaction.json").exists()
    assert (
        entitlement.status_grant(
            state_path=state, public_key_path=public, site_id="site-1", now=NOW
        )["status"]
        == "uncertain"
    )


def test_two_processes_serialize_and_cannot_downgrade(tmp_path: Path) -> None:
    private, public = _keys(tmp_path)
    _, grant_two, _, _ = _grant(
        tmp_path / "two",
        private=private,
        public=public,
        serial=2,
        end=datetime(2027, 6, 4, tzinfo=UTC),
    )
    _, grant_three, _, _ = _grant(
        tmp_path / "three",
        private=private,
        public=public,
        serial=3,
        end=datetime(2027, 9, 4, tzinfo=UTC),
    )
    state = tmp_path / "state" / "current.json"
    audit = tmp_path / "state" / "audit.jsonl"
    code = (
        "from pathlib import Path;from datetime import datetime,UTC;"
        "from tools.entitlement import install_grant;import sys;"
        "install_grant(grant_path=Path(sys.argv[1]),public_key_path=Path(sys.argv[2]),"
        "state_path=Path(sys.argv[3]),audit_path=Path(sys.argv[4]),site_id='site-1',"
        "operation_id=sys.argv[5],reason='approved concurrent grant',"
        "now=datetime(2026,9,4,tzinfo=UTC))"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(grant), str(public), str(state), str(audit), op],
            cwd=Path(__file__).parents[2],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2])},
        )
        for grant, op in (
            (grant_two, "00000000-0000-4000-8000-000000000002"),
            (grant_three, "00000000-0000-4000-8000-000000000003"),
        )
    ]
    for process in processes:
        process.communicate(timeout=20)
    status = entitlement.status_grant(
        state_path=state, public_key_path=public, site_id="site-1", now=NOW
    )
    assert status["serial"] == 3
    assert not (state.parent / "transaction.json").exists()
    entitlement._audit_entries(audit)


def test_status_advances_protected_time_and_expiry_preserves_safety(tmp_path: Path) -> None:
    _, path, _, public = _grant(tmp_path)
    state = tmp_path / "current.json"
    state.write_bytes(path.read_bytes())
    expired = entitlement.status_grant(
        state_path=state,
        public_key_path=public,
        site_id="site-1",
        now=datetime(2027, 9, 12, tzinfo=UTC),
    )
    assert expired["status"] == "expired"
    last_seen = json.loads((tmp_path / "last-seen.json").read_text())
    assert last_seen["last_seen_utc"] == "2027-09-12T00:00:00+00:00"
    for field in ("safety_preserved", "collection_preserved", "alarms_preserved", "data_preserved"):
        assert expired[field] is True
    assert expired["features"] == ["support", "upgrade"]


def test_tolerated_clock_rollback_uses_the_nondecreasing_effective_time(tmp_path: Path) -> None:
    grant, path, _, public = _grant(
        tmp_path,
        start=NOW - timedelta(days=1),
        end=NOW + timedelta(minutes=2),
    )
    state = tmp_path / "state" / "current.json"
    state.parent.mkdir()
    state.write_bytes(path.read_bytes())
    later = NOW + timedelta(minutes=4)
    assert (
        entitlement.status_grant(
            state_path=state, public_key_path=public, site_id="site-1", now=later
        )["status"]
        == "grace"
    )
    rolled_back_within_tolerance = entitlement.status_grant(
        state_path=state, public_key_path=public, site_id="site-1", now=NOW
    )
    assert rolled_back_within_tolerance["status"] == "grace"
    assert rolled_back_within_tolerance["features"] == grant["features"]


def test_missing_status_advances_time_and_detects_pre_grant_rollback(tmp_path: Path) -> None:
    state = tmp_path / "state" / "current.json"
    time_state = state.parent / "last-seen.json"
    later = NOW + timedelta(days=1)
    assert (
        entitlement.status_grant(
            state_path=state,
            public_key_path=tmp_path / "unused-public-key",
            site_id="site-1",
            now=later,
        )["status"]
        == "missing"
    )
    recorded = json.loads(time_state.read_text())
    assert recorded["last_seen_utc"] == later.isoformat(timespec="seconds")
    assert (
        entitlement.status_grant(
            state_path=state,
            public_key_path=tmp_path / "unused-public-key",
            site_id="site-1",
            now=NOW,
        )["status"]
        == "uncertain"
    )


def test_all_protected_time_corruption_is_uncertain_with_missing_grant(tmp_path: Path) -> None:
    state = tmp_path / "state" / "current.json"
    time_state = state.parent / "last-seen.json"
    time_state.parent.mkdir(parents=True)
    valid_material = {"schema_version": 1, "last_seen_utc": NOW.isoformat(timespec="seconds")}
    corruptions = [
        b"{not-json}\n",
        b"\xff\n",
        b'{"last_seen_utc":NaN,"record_hash":"x","schema_version":1}\n',
        entitlement.canonical_artifact_bytes({**valid_material, "record_hash": "0" * 64}),
        entitlement.canonical_artifact_bytes(
            entitlement._hashed_record({"schema_version": 1, "last_seen_utc": "not-a-timestamp"})
        ),
        b"x" * (entitlement.MAX_JSON_BYTES + 1),
    ]
    for raw in corruptions:
        time_state.write_bytes(raw)
        result = entitlement.status_grant(
            state_path=state,
            public_key_path=tmp_path / "unused-public-key",
            site_id="site-1",
            now=NOW,
        )
        assert result["status"] == "uncertain"


def test_encrypted_key_cli_password_stdin_and_outputs_are_no_clobber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    private = tmp_path / "private.pem"
    public = tmp_path / "public"
    command = [
        "keygen",
        "--private-key",
        str(private),
        "--public-key",
        str(public),
        "--key-id",
        "entitlement-cli",
        "--password-stdin",
    ]
    monkeypatch.setattr(entitlement, "_password_from_stdin", lambda confirm=False: PASSWORD)
    assert entitlement.main(command) == 0
    first = capsys.readouterr()
    assert entitlement.main(command) == 2
    second = capsys.readouterr()
    assert b"ENCRYPTED PRIVATE KEY" in private.read_bytes()
    assert "correct horse" not in first.out + first.err
    assert "output_exists" in second.out


def test_shanghai_dates_convert_to_exact_utc_boundaries() -> None:
    assert (
        entitlement._parse_datetime("2026-09-04", shanghai_date=True).isoformat()
        == "2026-09-03T16:00:00+00:00"
    )
    assert (
        entitlement._parse_datetime("2027-09-04", shanghai_date=True).isoformat()
        == "2027-09-03T16:00:00+00:00"
    )


def test_inspect_cli_returns_closed_canonical_identity_without_verifying_signature(
    tmp_path: Path,
) -> None:
    _, grant_path, _, _ = _grant(tmp_path)
    script = Path(__file__).parents[2] / "tools" / "entitlement.py"
    completed = subprocess.run(
        [sys.executable, str(script), "inspect", "--grant", str(grant_path)],
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 0
    result = json.loads(completed.stdout)
    assert set(result) == {
        "schema_version",
        "ok",
        "status",
        "site_id",
        "grant_id",
        "grant_sha256",
        "serial",
        "starts_at",
        "expires_at",
        "grace_until",
    }
    assert result["status"] == "inspected"
    assert result["grant_sha256"] == entitlement.sha256_bytes(grant_path.read_bytes())


def test_cli_preserves_explicit_transaction_uncertainty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def uncertain(**_kwargs: object) -> dict[str, object]:
        raise entitlement.EntitlementError("transaction_uncertain")

    monkeypatch.setattr(entitlement, "install_grant", uncertain)
    result = entitlement.main(
        [
            "install",
            "--grant",
            str(tmp_path / "grant"),
            "--public-key",
            str(tmp_path / "public"),
            "--state",
            str(tmp_path / "state"),
            "--audit",
            str(tmp_path / "audit"),
            "--site-id",
            "site-1",
            "--operation-id",
            OPERATION,
            "--reason",
            "approved annual entitlement",
        ]
    )
    output = json.loads(capsys.readouterr().out)
    assert result == 2
    assert output == {"status": "uncertain", "error_code": "transaction_uncertain"}


def test_clock_rollback_makes_status_uncertain_and_install_rejects(tmp_path: Path) -> None:
    _, first_path, private, public = _grant(tmp_path, end=datetime(2027, 1, 4, tzinfo=UTC))
    state = tmp_path / "state" / "current.json"
    audit = tmp_path / "state" / "audit.jsonl"
    _install(first_path, public, state, audit)
    later = datetime(2027, 9, 12, tzinfo=UTC)
    assert (
        entitlement.status_grant(
            state_path=state, public_key_path=public, site_id="site-1", now=later
        )["status"]
        == "expired"
    )

    rolled_back = entitlement.status_grant(
        state_path=state, public_key_path=public, site_id="site-1", now=NOW
    )
    assert rolled_back["status"] == "uncertain"

    _, second_path, _, _ = _grant(
        tmp_path / "second",
        private=private,
        public=public,
        serial=2,
        end=datetime(2027, 9, 4, tzinfo=UTC),
    )
    old_state = state.read_bytes()
    with pytest.raises(entitlement.EntitlementError, match="clock_rollback"):
        _install(
            second_path,
            public,
            state,
            audit,
            operation="00000000-0000-4000-8000-000000000002",
        )
    assert state.read_bytes() == old_state
    assert b"clock_rollback" in audit.read_bytes()


def test_last_seen_tampering_fails_closed(tmp_path: Path) -> None:
    _, path, _, public = _grant(tmp_path)
    state = tmp_path / "state" / "current.json"
    audit = tmp_path / "state" / "audit.jsonl"
    _install(path, public, state, audit)
    last_seen_path = state.parent / "last-seen.json"
    value = json.loads(last_seen_path.read_text())
    value["last_seen_utc"] = "2025-01-01T00:00:00+00:00"
    last_seen_path.write_bytes(entitlement.canonical_artifact_bytes(value))
    status = entitlement.status_grant(
        state_path=state, public_key_path=public, site_id="site-1", now=NOW
    )
    assert status["status"] == "uncertain"


def test_audit_rotation_is_bounded_and_checkpoint_authenticates_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr(entitlement, "MAX_AUDIT_RECORDS", 3)
    for serial in range(1, 8):
        entitlement._append_audit_locked(
            audit,
            {
                "schema_version": 1,
                "recorded_at": NOW.isoformat(timespec="seconds"),
                "event": "test",
                "serial": serial,
            },
        )
        entitlement._audit_entries(audit)
        assert len(entitlement._audit_entries(audit)[1]) <= 3
        assert audit.stat().st_size <= entitlement.MAX_AUDIT_BYTES
    checkpoint = json.loads(audit.read_bytes().splitlines()[0])
    archive = audit.with_name(checkpoint["archive_file"])
    assert archive.exists()
    assert archive.stat().st_size <= entitlement.MAX_AUDIT_BYTES
    assert len(list(tmp_path.glob("audit.jsonl.*"))) == 1
    archive.write_bytes(archive.read_bytes().replace(b'"serial":4', b'"serial":0'))
    with pytest.raises(entitlement.EntitlementError, match="audit_archive_invalid"):
        entitlement._audit_entries(audit)


def test_state_audit_and_archive_reads_enforce_limits_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state" / "current.json"
    state.parent.mkdir(parents=True)
    monkeypatch.setattr(entitlement, "MAX_JSON_BYTES", 512)
    state.write_bytes(b"x" * 513)
    with pytest.raises(entitlement.EntitlementError, match="grant_size_invalid"):
        entitlement.status_grant(
            state_path=state,
            public_key_path=tmp_path / "unused-public-key",
            site_id="site-1",
            now=NOW,
        )

    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr(entitlement, "MAX_AUDIT_BYTES", 1024)
    audit.write_bytes(b"x" * 1025)
    with pytest.raises(entitlement.EntitlementError, match="audit_file_limit_exceeded"):
        entitlement._audit_entries(audit)

    archive = tmp_path / "archived-audit.jsonl.1"
    archive_raw = b"x" * 1025
    archive.write_bytes(archive_raw)
    previous_hash = "a" * 64
    checkpoint = {
        "schema_version": 1,
        "recorded_at": NOW.isoformat(timespec="seconds"),
        "event": "audit_checkpoint",
        "archive_file": archive.name,
        "archive_sha256": entitlement.sha256_bytes(archive_raw),
        "archive_tail_hash": previous_hash,
    }
    active = tmp_path / "archived-audit.jsonl"
    active.write_bytes(entitlement._audit_line(checkpoint, previous_hash))
    with pytest.raises(entitlement.EntitlementError, match="audit_archive_invalid"):
        entitlement._audit_entries(active)


def test_operation_retention_is_bounded_before_state_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private, public = _keys(tmp_path)
    state = tmp_path / "state" / "current.json"
    audit = tmp_path / "state" / "audit.jsonl"
    monkeypatch.setattr(entitlement, "MAX_OPERATION_RECORDS", 3)
    monkeypatch.setattr(entitlement, "MAX_AUDIT_RECORDS", 3)
    for serial in range(1, 7):
        _, grant_path, _, _ = _grant(
            tmp_path / f"grant-{serial}",
            private=private,
            public=public,
            serial=serial,
            end=NOW + timedelta(days=300 + serial),
        )
        result = _install(
            grant_path,
            public,
            state,
            audit,
            operation=f"00000000-0000-4000-8000-{serial:012d}",
        )
        assert result["status"] == "installed"
        assert len(list((state.parent / "operations").glob("*.json"))) <= 3
    assert (
        entitlement.status_grant(
            state_path=state, public_key_path=public, site_id="site-1", now=NOW
        )["serial"]
        == 6
    )


def test_operation_retention_is_applied_before_protected_time_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, grant_path, _, public = _grant(tmp_path)
    state = tmp_path / "state" / "current.json"
    audit = tmp_path / "state" / "audit.jsonl"
    _install(grant_path, public, state, audit)
    second_id = "00000000-0000-4000-8000-000000000002"
    second_identity = entitlement._identity(
        operation_id=second_id,
        actor="operator",
        site_id="site-1",
        reason="approved annual entitlement",
        grant_id="rejected-grant",
        grant_sha256="a" * 64,
        serial=2,
    )
    entitlement._write_operation(
        entitlement._operation_path(state, second_id),
        second_identity,
        "rejected",
        "signature_invalid",
    )
    monkeypatch.setattr(entitlement, "MAX_OPERATION_RECORDS", 2)
    third_id = "00000000-0000-4000-8000-000000000003"

    with pytest.raises(entitlement.EntitlementError, match="clock_rollback"):
        entitlement.install_grant(
            grant_path=grant_path,
            public_key_path=public,
            state_path=state,
            audit_path=audit,
            site_id="site-1",
            operation_id=third_id,
            reason="approved annual entitlement",
            now=NOW - timedelta(days=1),
        )

    operations = list((state.parent / "operations").glob("*.json"))
    assert len(operations) == 2
    operation = entitlement._read_operation(entitlement._operation_path(state, third_id))
    assert operation is not None
    assert operation["error_code"] == "clock_rollback"


def test_orphan_executing_operation_is_reclaimed_but_journal_and_current_are_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state" / "current.json"
    current_id = "00000000-0000-4000-8000-000000000010"
    referenced_id = "00000000-0000-4000-8000-000000000011"
    orphan_id = "00000000-0000-4000-8000-000000000012"

    def identity(operation_id: str) -> dict[str, object]:
        return entitlement._identity(
            operation_id=operation_id,
            actor="operator",
            site_id="site-1",
            reason="approved annual entitlement",
            grant_id=f"grant-{operation_id[-2:]}",
            grant_sha256="a" * 64,
            serial=1,
        )

    for operation_id in (current_id, referenced_id, orphan_id):
        entitlement._write_operation(
            entitlement._operation_path(state, operation_id),
            identity(operation_id),
            "executing",
        )
    journal = {
        "schema_version": 1,
        "phase": "prepared",
        "identity": identity(referenced_id),
        "new_state_b64": "bmV3",
        "old_state_b64": "",
        "old_state_present": False,
    }
    entitlement._atomic_replace(
        entitlement._journal_path(state), entitlement.canonical_artifact_bytes(journal)
    )
    monkeypatch.setattr(entitlement, "MAX_OPERATION_RECORDS", 3)
    entitlement._prune_operations_locked(state, current_id)
    assert entitlement._operation_path(state, current_id).exists()
    assert entitlement._operation_path(state, referenced_id).exists()
    assert not entitlement._operation_path(state, orphan_id).exists()

    entitlement._journal_path(state).unlink()
    monkeypatch.setattr(entitlement, "MAX_OPERATION_RECORDS", 2)
    entitlement._prune_operations_locked(state, current_id)
    assert entitlement._operation_path(state, current_id).exists()
    assert not entitlement._operation_path(state, referenced_id).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL contract")
def test_windows_private_key_parent_must_be_existing_and_protected(tmp_path: Path) -> None:
    with pytest.raises(entitlement.EntitlementError, match="private_key_parent_missing"):
        ORIGINAL_PARENT_CHECK(tmp_path / "missing" / "key.pem")
    with pytest.raises(entitlement.EntitlementError, match="private_key_parent_acl_unprotected"):
        ORIGINAL_PARENT_CHECK(tmp_path / "key.pem")


def test_issue_cli_checks_private_key_parent_before_reading_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject(_path: Path) -> None:
        raise entitlement.EntitlementError("private_key_parent_acl_invalid")

    monkeypatch.setattr(entitlement, "_require_protected_private_key_parent", reject)
    monkeypatch.setattr(entitlement, "_password_from_stdin", lambda confirm=False: PASSWORD)
    assert (
        entitlement.main(
            [
                "issue",
                "--private-key",
                str(tmp_path / "missing.pem"),
                "--key-id",
                "entitlement-2026",
                "--site-id",
                "site-1",
                "--customer-id",
                "customer-1",
                "--plan",
                "annual",
                "--features",
                "support",
                "--serial",
                "1",
                "--start",
                "2026-09-04",
                "--end",
                "2027-09-04",
                "--output",
                str(tmp_path / "grant.json"),
                "--password-stdin",
            ]
        )
        == 2
    )


def test_invalid_shanghai_date_is_a_controlled_parser_error() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="valid date"):
        entitlement._parse_datetime("2026-02-30", shanghai_date=True)
