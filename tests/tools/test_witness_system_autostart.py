from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[2]
TOOL_ROOT = ROOT / "tools" / "witness_system_autostart"


def load_witness() -> ModuleType:
    path = TOOL_ROOT / "freshness_witness.py"
    spec = importlib.util.spec_from_file_location("reviewed_freshness_witness", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_certificate(key: Ed25519PrivateKey, common_name: str) -> tuple[str, bytes]:
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, algorithm=None)
    )
    pem = certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")
    der = certificate.public_bytes(serialization.Encoding.DER)
    return pem, der


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_identity_material_is_bound_to_config() -> None:
    witness = load_witness()
    key = Ed25519PrivateKey.generate()
    server_pem, server_der = make_certificate(key, "server")
    client_pem, client_der = make_certificate(Ed25519PrivateKey.generate(), "client")
    public_key = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    config = {
        "witness_public_key": base64.b64encode(public_key).decode("ascii"),
        "server_cert_sha256": hashlib.sha256(server_der).hexdigest(),
        "client_cert_sha256": hashlib.sha256(client_der).hexdigest(),
    }

    witness.validate_identity_material(config, key, server_pem, client_pem)

    with pytest.raises(RuntimeError, match="server certificate"):
        witness.validate_identity_material(
            dict(config, server_cert_sha256="0" * 64), key, server_pem, client_pem
        )
    with pytest.raises(RuntimeError, match="public key"):
        witness.validate_identity_material(
            dict(config, witness_public_key=base64.b64encode(b"x" * 32).decode("ascii")),
            key,
            server_pem,
            client_pem,
        )


def test_success_response_fails_closed_when_audit_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    witness = load_witness()
    handler = object.__new__(witness.Handler)
    handler.path = "/v1/attest"
    handler.wfile = io.BytesIO()
    statuses: list[int] = []
    headers: list[tuple[str, str]] = []
    handler.send_response = statuses.append
    handler.send_header = lambda name, value: headers.append((name, value))
    handler.end_headers = lambda: None
    monkeypatch.setattr(witness, "audit", lambda status, value, path: False)

    handler.response(200, {"attestation": "would-have-succeeded"}, require_audit=True)

    assert statuses == [503]
    body = json.loads(handler.wfile.getvalue())
    assert body == {"decision": "BLOCKED", "reason_code": "FRESHNESS_AUDIT_UNAVAILABLE"}
    assert ("Cache-Control", "no-store") in headers


def test_audit_reader_requires_new_attestation_after_baseline(tmp_path: Path) -> None:
    database = tmp_path / "audit.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE requests (id INTEGER PRIMARY KEY, at TEXT NOT NULL, path TEXT NOT NULL, "
            "status INTEGER NOT NULL, decision TEXT, reason_code TEXT)"
        )
        connection.executemany(
            "INSERT INTO requests VALUES(?,?,?,?,?,?)",
            [
                (1, "2026-09-01T00:00:01+00:00", "/v1/attest", 200, None, None),
                (2, "2026-09-01T00:00:02+00:00", "/health", 200, None, None),
                (3, "2026-09-01T00:00:03+00:00", "/v1/attest", 503, "BLOCKED", "ERROR"),
                (4, "2026-09-01T00:00:04+00:00", "/v1/attest", 200, None, None),
            ],
        )
        connection.commit()
    finally:
        connection.close()
    reader = TOOL_ROOT / "read-witness-audit.py"

    baseline = subprocess.run(
        [sys.executable, str(reader), str(database)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(baseline.stdout)["baseline_id"] == 4

    query = subprocess.run(
        [
            sys.executable,
            str(reader),
            str(database),
            "2026-09-01T00:00:02+00:00",
            "2",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(query.stdout)
    assert result["success_count"] == 1
    assert result["latest"]["id"] == 4
    assert result["latest"]["path"] == "/v1/attest"


def test_approved_runtime_manifest_is_canonical_and_complete() -> None:
    path = TOOL_ROOT / "runtime-source-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert set(manifest) == {
        "schema_version",
        "artifact_type",
        "python_version",
        "source_root_identity",
        "files",
    }
    assert manifest["schema_version"] == 1
    assert manifest["artifact_type"] == "ruisheng.witness-approved-python-runtime"
    assert manifest["python_version"] == "3.11.9"
    assert len(manifest["files"]) == 1494
    paths = [entry["relative_path"] for entry in manifest["files"]]
    assert sha256(path) == "301172759e6269bcd1b04d7aed04c9b4df78f32150d34dd1a4c5d0cd7be329d0"
    assert len({path.casefold() for path in paths}) == len(paths)
    assert all("\\" not in path and ":" not in path for path in paths)
    python_entry = next(
        entry for entry in manifest["files"] if entry["relative_path"] == "python.exe"
    )
    assert (
        python_entry["sha256"] == "5f7b89a612c9b8af1d6456cdfcd1dbe5ca630849e79aebced9bee9a6694952ec"
    )
    pth_entry = next(
        entry for entry in manifest["files"] if entry["relative_path"] == "python311._pth"
    )
    pth = b".\r\nLib\r\nDLLs\r\nLib\\site-packages\r\n"
    assert pth_entry == {
        "relative_path": "python311._pth",
        "size": len(pth),
        "sha256": hashlib.sha256(pth).hexdigest(),
        "generated": True,
    }


def extract_assignment(script: str, name: str) -> str:
    match = re.search(rf'\${re.escape(name)}\s*=\s*"([0-9a-f]{{64}})"', script)
    assert match is not None, f"missing fixed digest: {name}"
    return match.group(1)


def test_powershell_bundle_hashes_are_closed() -> None:
    install = (TOOL_ROOT / "install-witness-system-autostart.ps1").read_text(encoding="utf-8")
    acceptance = (TOOL_ROOT / "test-witness-system-autostart.ps1").read_text(encoding="utf-8")
    restart = (TOOL_ROOT / "verify-witness-system-restart.ps1").read_text(encoding="utf-8")
    final = (TOOL_ROOT / "verify-witness-final-state.ps1").read_text(encoding="utf-8")
    runner = (TOOL_ROOT / "run-system-autostart-elevated.ps1").read_text(encoding="utf-8")
    launcher = (TOOL_ROOT / "launch-elevated-operation.ps1").read_text(encoding="utf-8")
    assert "TO_BE_UPDATED" not in "".join((install, acceptance, restart, final, runner, launcher))
    expected = {
        "expectedWitnessSha256": sha256(TOOL_ROOT / "freshness_witness.py"),
        "expectedTestScriptSha256": sha256(TOOL_ROOT / "test-witness-system-autostart.ps1"),
        "expectedProbeSha256": sha256(TOOL_ROOT / "diagnose-witness-system-start.py"),
        "expectedRollbackScriptSha256": sha256(TOOL_ROOT / "rollback-witness-system-autostart.ps1"),
        "expectedApprovedRuntimeManifestSha256": sha256(TOOL_ROOT / "runtime-source-manifest.json"),
        "expectedAuditReaderSha256": sha256(TOOL_ROOT / "read-witness-audit.py"),
    }
    for name in (
        "expectedWitnessSha256",
        "expectedTestScriptSha256",
        "expectedProbeSha256",
        "expectedRollbackScriptSha256",
        "expectedApprovedRuntimeManifestSha256",
    ):
        if f"${name}" in install:
            assert extract_assignment(install, name) == expected[name]
    assert (
        extract_assignment(acceptance, "ExpectedWitnessSha256") == expected["expectedWitnessSha256"]
    )
    for script in (restart, final):
        assert (
            extract_assignment(script, "expectedWitnessSha256") == expected["expectedWitnessSha256"]
        )
        assert (
            extract_assignment(script, "expectedTestScriptSha256")
            == expected["expectedTestScriptSha256"]
        )
    assert (
        extract_assignment(final, "expectedAuditReaderSha256")
        == expected["expectedAuditReaderSha256"]
    )
    bundle_entries = dict(
        re.findall(r'^\s*"([^\"]+)"\s*=\s*"([0-9a-f]{64})"', runner, re.MULTILINE)
    )
    assert bundle_entries
    for filename, digest in bundle_entries.items():
        assert sha256(TOOL_ROOT / filename) == digest
    assert extract_assignment(launcher, "expectedRunnerSha256") == sha256(
        TOOL_ROOT / "run-system-autostart-elevated.ps1"
    )


def test_reviewed_scripts_are_versionable() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(TOOL_ROOT / "freshness_witness.py")],
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 1
    assert (TOOL_ROOT / "target" / "run-target-acceptance.ps1").is_file()


def test_windows_powershell51_contracts() -> None:
    acceptance = (TOOL_ROOT / "test-witness-system-autostart.ps1").read_text(encoding="utf-8")
    rollback = (TOOL_ROOT / "rollback-witness-system-autostart.ps1").read_text(encoding="utf-8")
    assert "SetEquals([string[]]$allowed)" in acceptance
    assert "Assert-RuntimeMatchesManifest $runtime $manifestBackup" in rollback
    assert "Assert-RestoredTaskListener" in rollback
    if sys.platform != "win32":
        pytest.skip("Windows PowerShell 5.1 is Windows-specific")
    powershell = Path(os.environ["SYSTEMROOT"]) / ("System32/WindowsPowerShell/v1.0/powershell.exe")
    command = (
        "$h=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal);"
        "[void]$h.Add('a');[void]$h.Add('b');$allowed=@('a','b');"
        "if(-not $h.SetEquals([string[]]$allowed)){exit 1}"
    )
    subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
        check=True,
    )


def test_target_evidence_preserves_utc_timestamp_text() -> None:
    script = (TOOL_ROOT / "new-target-acceptance-evidence.ps1").read_text(encoding="utf-8")
    assert 'Parameters.ContainsKey("DateKind")' in script
    assert "ConvertFrom-Json -DateKind String" in script
    assert "RUISHENG_VERIFY_EXIT_CODE=2" in script
    assert '$publisherDecision = "BLOCKED"' in script
