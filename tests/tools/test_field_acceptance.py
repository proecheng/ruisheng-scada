"""Evidence calculations must not overstate physical collection success."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.field_acceptance import summarize_browser, summarize_intervals


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
def test_collector_preserves_attempt_timeout_and_retry_outcomes(executable):
    engine = shutil.which(executable)
    if not engine:
        pytest.skip(f"{executable} unavailable")
    source = (Path(__file__).parents[2] / "tools/field_acceptance.remote.ps1").read_text()
    block = source[source.index("$events = @()") : source.index("$unchanged = ")]
    labels = [
        "serial response timeout",
        "serial retry attempt",
        "serial retry recovered",
        "serial retry exhausted",
    ]
    lines = ",".join(
        f"'2026-09-17T01:00:00Z WARNING {label} port=/dev/ttyUSB0 device=DEV001'"
        for label in labels
    )
    harness = f"$ErrorActionPreference='Stop'; $logLines=@({lines});\n{block}\n$events | ConvertTo-Json -Compress"
    result = subprocess.run(
        [engine, "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert [event["event"] for event in json.loads(result.stdout)] == labels


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize("service_count", [4, 5, 6])
def test_native_docker_service_array_on_both_powershell_engines(executable, service_count):
    engine = shutil.which(executable)
    if not engine:
        pytest.skip(f"{executable} unavailable")
    source = (Path(__file__).parents[2] / "tools/field_acceptance.remote.ps1").read_text()
    # Exercise the real collector block, including the native exit-code guard.
    block = source[source.index("$infoRaw = ") : source.index("$services = ")]
    payload = json.dumps([{"Name": f"service-{i}"} for i in range(service_count)])
    harness = f"""
$ErrorActionPreference='Stop'
$docker='Read-FixtureDocker'; $dockerBase=@()
function Read-FixtureDocker {{ $global:LASTEXITCODE=0; '{payload}' }}
try {{
{block}
    [Console]::Out.Write(($info | ForEach-Object {{ $_.Name }}) -join ',')
}} catch {{ [Console]::Out.Write($_.Exception.Message); exit 2 }}
"""
    result = subprocess.run(
        [engine, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if service_count == 5:
        assert result.returncode == 0, result.stderr
        assert result.stdout == ",".join(f"service-{i}" for i in range(5))
    else:
        assert result.returncode == 2
        assert result.stdout == "service_inspection_failed"


@pytest.mark.parametrize("executable", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize("multiline", [False, True])
def test_postgres_json_record_aggregation_keeps_continuation_lines(executable, multiline):
    engine = shutil.which(executable)
    if not engine:
        pytest.skip(f"{executable} unavailable")
    source = (Path(__file__).parents[2] / "tools/field_acceptance.remote.ps1").read_text()
    block = source[source.index("$jsonText = ") : source.index("# Bound the log window")]
    payload = json.dumps(
        {
            "observed_at": "2026-09-16T08:40:00Z",
            "settings": {"period": 5},
            "windows": [{"rounds": 100}, {"rounds": 200}],
        },
        indent=2 if multiline else None,
    )
    lines = ",".join("'" + line + "'" for line in payload.splitlines())
    harness = f"""
$ErrorActionPreference='Stop'
$raw=@({lines})
{block}
[Console]::Out.Write(($db.windows | ForEach-Object {{ $_.rounds }}) -join ',')
"""
    result = subprocess.run(
        [engine, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", harness],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "100,200"


def test_deduplicates_tabs_and_counts_multiple_missing_rounds():
    result = summarize_intervals(
        [
            "2026-09-15T00:00:20Z",
            "2026-09-15T00:00:00Z",
            "2026-09-15T08:00:00+08:00",
            "2026-09-15T00:00:05Z",
        ]
    )
    assert result["observed_rounds"] == 3
    assert result["estimated_unobserved_rounds_between_samples"] == 2
    assert result["max_interval_seconds"] == 15
    assert result["estimated_observed_fraction"] == 0.6


@pytest.mark.parametrize("values", [[], ["2026-09-15T00:00:00Z"]])
def test_sparse_evidence_has_no_interval_or_invented_missing_samples(values):
    result = summarize_intervals(values)
    assert result["median_interval_seconds"] is None
    assert result["estimated_unobserved_rounds_between_samples"] == 0


@pytest.mark.parametrize("period", [0, -1, float("nan"), float("inf")])
def test_invalid_period_is_rejected(period):
    with pytest.raises(ValueError):
        summarize_intervals([], period)


def test_timezone_is_required():
    with pytest.raises(ValueError, match="timezone"):
        summarize_intervals(["2026-09-15T00:00:00"])


def test_browser_bounds_exclude_later_navigation_and_do_not_double_count_tabs(tmp_path):
    path = tmp_path / "browser.json"
    path.write_text(
        json.dumps(
            {
                "samples": [{"at": "2026-09-15T00:00:00Z"}, {"at": "2026-09-15T00:00:10Z"}],
                "rounds": {
                    "2026-09-15T00:00:00Z": 76,
                    "2026-09-15T00:00:05Z": 76,
                    "2026-09-15T00:02:00Z": 38,
                },
            }
        ),
        encoding="utf-8",
    )
    result = summarize_browser(path)
    assert result["observed_rounds"] == 2
    assert result["max_interval_seconds"] == 5
    assert result["point_identity_available"] is False
    assert "rounds_with_38_unique_point_ids" not in result


def test_frame_point_completeness_uses_unique_ids_and_expected_device(tmp_path):
    path = tmp_path / "browser.json"
    at = "2026-09-15T00:00:05Z"
    path.write_text(
        json.dumps(
            {
                "samples": [{"at": "2026-09-15T00:00:00Z"}, {"at": "2026-09-15T00:00:10Z"}],
                "frames": [{"dev_number": "DEV001", "ts": at, "point_id": 1}] * 38
                + [{"dev_number": "OTHER", "ts": at, "point_id": n} for n in range(38)],
            }
        ),
        encoding="utf-8",
    )
    result = summarize_browser(path)
    assert result["observed_rounds"] == 1
    assert result["rounds_with_38_unique_point_ids"] == 0
