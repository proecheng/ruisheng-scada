"""Real Windows child processes must terminate before diagnostics return."""

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

LIBRARY = Path(__file__).parents[2] / "tools/diagnostic_limits.ps1"


@pytest.fixture(params=["powershell.exe", "pwsh.exe"])
def engine(request):
    executable = shutil.which(request.param)
    if not executable:
        pytest.skip(f"{request.param} unavailable")
    return executable


def run(engine, script, timeout=30):
    script = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)\n" + script
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        [engine, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def harness(body):
    return f"$ErrorActionPreference='Stop'; . '{LIBRARY.as_posix()}'\n{body}"


def test_remote_envelope_executes_under_worker_limits(engine):
    result = run(
        engine,
        harness("""
$remote=New-BoundedDiagnosticScript -Script "@{message='中文';value=23} | ConvertTo-Json -Compress"
& ([ScriptBlock]::Create($remote))
"""),
    )
    assert result["ok"] is True
    assert json.loads(result["output"]) == {"message": "中文", "value": 23}
    assert 0 < result["peak_job_memory_bytes"] <= 512 * 1024 * 1024


@pytest.mark.parametrize("parent_finishes", [False, True])
def test_timeout_or_parent_exit_removes_descendant(engine, tmp_path, parent_finishes):
    marker = (tmp_path / "child.pid").as_posix()
    ending = "" if parent_finishes else "Start-Sleep -Seconds 60"
    result = run(
        engine,
        harness(f"""
$script=@'
$child=Start-Process -FilePath (Get-Process -Id $PID).Path -ArgumentList '-NoLogo -NoProfile -NonInteractive -Command Start-Sleep -Seconds 60' -WindowStyle Hidden -PassThru
[IO.File]::WriteAllText('{marker}',[string]$child.Id)
{ending}
'@
$errorCode=''
try {{ [void](Invoke-BoundedDiagnostic -Script $script -TimeoutSeconds 4) }}
catch {{ $errorCode=$_.Exception.Message }}
$childId=[int][IO.File]::ReadAllText('{marker}')
@{{error=$errorCode;child_alive=($null -ne (Get-Process -Id $childId -ErrorAction SilentlyContinue))}} | ConvertTo-Json -Compress
"""),
    )
    expected = "diagnostic_descendant_left_running" if parent_finishes else "diagnostic_timeout"
    assert result == {"error": expected, "child_alive": False}


def test_excess_output_is_bounded_and_rejected(engine):
    result = run(
        engine,
        harness("""
$source="[Console]::Out.Write(('x' * 200000))"
try { [void](Invoke-BoundedDiagnostic -Script $source -MaxOutputChars 1024); $errorCode='' }
catch { $errorCode=$_.Exception.Message }
@{error=$errorCode} | ConvertTo-Json -Compress
"""),
    )
    assert result["error"] == "diagnostic_output_exceeded"


def test_hard_commit_limit_prevents_large_allocation(engine):
    result = run(
        engine,
        harness("""
$source=@'
$blocks=New-Object Collections.ArrayList
try {
    for($i=0;$i -lt 80;$i++) { [void]$blocks.Add([byte[]]::new(8MB)) }
    @{outcome='allocation_unbounded';allocated_mb=$blocks.Count*8} | ConvertTo-Json -Compress
} catch { @{outcome='allocation_rejected';allocated_mb=$blocks.Count*8} | ConvertTo-Json -Compress }
'@
try {
    $run=Invoke-BoundedDiagnostic -Script $source -MemoryLimitMb 256 -TimeoutSeconds 15
    @{error='';output=$run.output.Trim();peak=$run.peak_job_memory_bytes} | ConvertTo-Json -Compress
} catch { @{error=$_.Exception.Message} | ConvertTo-Json -Compress }
"""),
    )
    if result["error"]:
        assert result["error"] in {"diagnostic_worker_failed", "diagnostic_worker_stderr"}
    else:
        allocation = json.loads(result["output"])
        assert allocation["outcome"] == "allocation_rejected"
        assert allocation["allocated_mb"] < 256


def test_supervisor_termination_kills_worker(engine, tmp_path):
    marker = (tmp_path / "worker.pid").as_posix()
    wrapper = tmp_path / "supervisor.ps1"
    worker_source = f"[IO.File]::WriteAllText('{marker}',[string]$PID); Start-Sleep -Seconds 60"
    encoded = base64.b64encode(worker_source.encode()).decode()
    wrapper.write_text(
        harness(f"""
$source=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))
Invoke-BoundedDiagnostic -Script $source -TimeoutSeconds 60
"""),
        encoding="utf-8-sig",
    )
    result = run(
        engine,
        f"""
$ErrorActionPreference='Stop'
$start=New-Object Diagnostics.ProcessStartInfo
$start.FileName='{engine.replace("'", "''")}'
$start.Arguments='-NoLogo -NoProfile -NonInteractive -File "{wrapper.as_posix()}"'
$start.UseShellExecute=$false; $start.CreateNoWindow=$true
$start.RedirectStandardOutput=$true; $start.RedirectStandardError=$true
$supervisor=[Diagnostics.Process]::Start($start)
try {{
    $deadline=[datetime]::UtcNow.AddSeconds(15)
    while(-not [IO.File]::Exists('{marker}')) {{
        if([datetime]::UtcNow -ge $deadline) {{ throw 'worker_did_not_start' }}
        Start-Sleep -Milliseconds 100
    }}
    $workerId=[int][IO.File]::ReadAllText('{marker}')
    $supervisor.Kill(); [void]$supervisor.WaitForExit(5000)
    $deadline=[datetime]::UtcNow.AddSeconds(5)
    do {{
        $alive=$null -ne (Get-Process -Id $workerId -ErrorAction SilentlyContinue)
        if(-not $alive) {{ break }}
        Start-Sleep -Milliseconds 100
    }} while([datetime]::UtcNow -lt $deadline)
    @{{worker_alive=$alive}} | ConvertTo-Json -Compress
}} finally {{ if(-not $supervisor.HasExited) {{ $supervisor.Kill() }}; $supervisor.Dispose() }}
""",
    )
    assert result == {"worker_alive": False}
