[CmdletBinding()]
param(
    [string]$OutputDirectory = "C:\ProgramData\Ruisheng\staging\witness-system-acceptance"
)

$ErrorActionPreference = "Stop"
$pwsh = (Get-Command pwsh.exe -ErrorAction Stop).Source
$temporary = "$OutputDirectory.$([guid]::NewGuid().ToString('N')).tmp"

function Write-Json([string]$Path, [object]$Value) {
    [IO.File]::WriteAllText(
        $Path,
        (($Value | ConvertTo-Json -Depth 10) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
}

function Invoke-Script([string]$Path, [int[]]$ExpectedExitCodes, [int]$TimeoutSeconds = 180) {
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $pwsh
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    [void]$start.ArgumentList.Add("-NoProfile")
    [void]$start.ArgumentList.Add("-NonInteractive")
    [void]$start.ArgumentList.Add("-File")
    [void]$start.ArgumentList.Add($Path)
    $process = [Diagnostics.Process]::Start($start)
    try {
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill($true)
            [void]$process.WaitForExit(5000)
            throw "target acceptance script timed out: $Path"
        }
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        $result = [ordered]@{
            exit_code = $process.ExitCode
            stdout = $stdout
            stderr = $stderr
        }
        if ($process.ExitCode -notin $ExpectedExitCodes) {
            throw "target acceptance script failed: $Path; exit=$($process.ExitCode); stderr=$stderr"
        }
        return [pscustomobject]$result
    } finally {
        $process.Dispose()
    }
}

function Parse-SingleJsonOutput([object]$Result, [string]$Label) {
    try { return ([string]$Result.stdout).Trim() | ConvertFrom-Json } catch {
        throw "$Label did not return one JSON object: $($_.Exception.Message)"
    }
}

if (Test-Path -LiteralPath $OutputDirectory) {
    throw "target acceptance output directory already exists: $OutputDirectory"
}
if (Test-Path -LiteralPath $temporary) { throw "temporary output path already exists" }
New-Item -ItemType Directory -Path $temporary | Out-Null
$summary = $null
try {
    $beforeResult = Invoke-Script (Join-Path $PSScriptRoot "capture-production-boundary.ps1") @(0) 30
    $before = Parse-SingleJsonOutput $beforeResult "pre-acceptance production snapshot"
    Write-Json (Join-Path $temporary "containers-before.json") $before

    $clearedResult = Invoke-Script `
        (Join-Path $PSScriptRoot "launch-target-provider-cleared-env.ps1") @(0) 60
    $cleared = Parse-SingleJsonOutput $clearedResult "cleared-env provider"
    Write-Json (Join-Path $temporary "cleared-env-result.json") $cleared
    if ([int]$cleared.exit_code -ne 0 -or -not [bool]$cleared.output_exists) {
        throw "cleared-env provider evidence failed"
    }
    $attestationPath = "C:\ProgramData\Ruisheng\staging\freshness-cleared-env.json"
    $candidateManifestPath = "C:\Ruisheng\candidates\deploy-20260831.1\MANIFEST.json"
    Copy-Item -LiteralPath $attestationPath `
        -Destination (Join-Path $temporary "freshness-cleared-env.json")
    Copy-Item -LiteralPath $candidateManifestPath `
        -Destination (Join-Path $temporary "candidate-MANIFEST.json")

    $publisherStartedAt = [DateTimeOffset]::UtcNow.ToString("o")
    $publisherResult = Invoke-Script `
        (Join-Path $PSScriptRoot "run-target-validator-profile.ps1") @(2) 180
    Write-Json (Join-Path $temporary "publisher-result.json") $publisherResult

    foreach ($suite in @(
        [pscustomobject]@{ name = "freshness-negative-result.json"; script = "run-target-freshness-negative-tests.ps1" },
        [pscustomobject]@{ name = "replay-negative-result.json"; script = "run-target-replay-negative-test.ps1" },
        [pscustomobject]@{ name = "publisher-negative-result.json"; script = "run-target-publisher-negative-tests.ps1" }
    )) {
        $suiteResult = Invoke-Script (Join-Path $PSScriptRoot $suite.script) @(0) 240
        $suiteJson = Parse-SingleJsonOutput $suiteResult $suite.script
        if ($suiteJson.all_passed -isnot [bool] -or -not [bool]$suiteJson.all_passed) {
            throw "negative suite did not pass: $($suite.script)"
        }
        Write-Json (Join-Path $temporary $suite.name) $suiteJson
    }
    $summary = [ordered]@{
        schema_version = 1
        artifact_type = "ruisheng.witness-system-autostart-target-run"
        completed = $true
        passed = $true
        publisher_started_at = $publisherStartedAt
        attestation_file = "freshness-cleared-env.json"
        candidate_manifest_file = "candidate-MANIFEST.json"
        captured_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
} catch {
    $summary = [ordered]@{
        schema_version = 1
        artifact_type = "ruisheng.witness-system-autostart-target-run"
        completed = $true
        passed = $false
        error = $_.Exception.Message
        captured_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
} finally {
    try {
        $afterResult = Invoke-Script `
            (Join-Path $PSScriptRoot "capture-production-boundary.ps1") @(0) 30
        $after = Parse-SingleJsonOutput $afterResult "post-acceptance production snapshot"
        Write-Json (Join-Path $temporary "containers-after.json") $after
        if ([int]$after.temporary_listener_count -ne 0) {
            $summary.passed = $false
            $summary.error = "temporary target listener remains active"
        }
    } catch {
        $summary.passed = $false
        $summary.error = "post-acceptance boundary capture failed: $($_.Exception.Message)"
    }
    Write-Json (Join-Path $temporary "summary.json") $summary
    Move-Item -LiteralPath $temporary -Destination $OutputDirectory
}

$summary | ConvertTo-Json -Depth 8 -Compress
if (-not $summary.passed) { exit 1 }
