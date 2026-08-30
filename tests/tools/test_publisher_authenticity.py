"""Publisher authenticity coverage for B-06 probe artifacts."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tools.release_artifacts import FIXED_PACKAGE_FILES

ROOT = Path(__file__).parents[2]
PROBE_FILES = {
    "site-modbus-probe.json.example",
    "probe_modbus_rtu.py",
    "run_modbus_probe.ps1",
}


def test_release_manifest_hashes_all_probe_inputs() -> None:
    assert PROBE_FILES <= FIXED_PACKAGE_FILES


def test_windows_publisher_installs_only_from_authenticated_snapshot() -> None:
    script = (ROOT / "tools" / "release_trust" / "verify-publisher.ps1").read_text(encoding="utf-8")

    assert script.index("signed manifest authenticity contract is invalid") < script.index(
        "Install-AuthenticatedSerialTools $PackageRoot"
    )
    assert script.index("& $CandidateVerifier $PackageRoot") < script.index(
        "Install-AuthenticatedSerialTools $PackageRoot"
    )
    assert "$CandidateExitCode -notin @(0, 2)" in script
    assert script.index("$CandidateExitCode = $LASTEXITCODE") < script.index(
        "Install-AuthenticatedSerialTools $PackageRoot"
    )
    assert script.index("Install-AuthenticatedSerialTools $PackageRoot") < script.rindex(
        "exit $CandidateExitCode"
    )
    assert "Join-Path $AuthenticatedRoot $TemplateRelative" in script
    assert "Join-Path $AuthenticatedRoot $Relative" in script
    assert "modbus-probe-release.json" in script
    assert "probe_sha256" in script
    assert "runner_sha256" in script
    assert "gw_image_id" in script
    assert "$GwInspectArguments = @(" in script
    assert '"--host", "npipe:////./pipe/docker_engine"' in script
    assert '"inspect", "ruisheng-gw", "--format", "{{json .}}"' in script
    assert "& $DockerPath @GwInspectArguments" in script
    assert "$RunningGwImageId = [string]$GwContainer.Image" in script
    assert "gw_image_id = $RunningGwImageId" in script
    assert "gw_image_id = [string]$GwImages[0].image_id" not in script
    assert "Assert-ProtectedAncestors $Entry.destination" in script
    assert "Global\\RuishengAuthenticatedSerialToolInstall" in script
    assert "authenticated install failed and was rolled back" in script
    assert "Remove-Item -LiteralPath $ReceiptPath" in script
    for filename in PROBE_FILES:
        assert filename in script


def test_candidate_and_publisher_verifiers_use_exact_probe_allowlists() -> None:
    paths = (
        ROOT / "tools" / "release_trust" / "verify-publisher.ps1",
        ROOT / "tools" / "release_trust" / "verify-publisher.sh",
        ROOT / "deploy" / "verify-candidate.ps1",
        ROOT / "deploy" / "verify-candidate.sh",
    )

    for path in paths:
        value = path.read_text(encoding="utf-8")
        present = {filename for filename in PROBE_FILES if filename in value}
        assert present >= PROBE_FILES
        assert "allowlist mismatch" in value
        assert "SHA256SUMS" in value


@pytest.mark.skipif(
    os.name != "nt",
    reason="Windows Desktop PowerShell parser is Windows-only",
)
def test_windows_probe_scripts_parse_in_desktop_powershell() -> None:
    command = (
        "$errors=$null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "(Resolve-Path 'tools/run_modbus_probe.ps1'),[ref]$null,[ref]$errors)|Out-Null; "
        "if ($errors.Count -gt 0) { $errors | % Message; exit 1 }"
    )

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr or result.stdout
