[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TargetEvidencePath,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ExpectedTargetEvidenceSha256
)

$ErrorActionPreference = "Stop"
$taskName = "Ruisheng B08 Freshness Witness"
$root = "C:\ProgramData\RuishengWitness"
$listenAddress = "100.67.229.19"
$listenPort = 38475
$highWater = Join-Path $root "trust\high-water.json"
$witness = Join-Path $root "freshness_witness.py"
$auditPath = Join-Path $root "trust\witness-audit.sqlite3"
$runtimePython = Join-Path $root "runtime\python.exe"
$stderrLog = Join-Path $root "migration\witness-stderr.log"
$testScript = Join-Path $PSScriptRoot "test-witness-system-autostart.ps1"
$auditReader = Join-Path $PSScriptRoot "read-witness-audit.py"
$statusPath = Join-Path $PSScriptRoot "final-verification.status.json"
$expectedHighWaterSha256 = "134b160de987a102518105ca0feb32876c0b6f0d315f0dee8ca8d8d652cbe9db"
$expectedWitnessSha256 = "f441790914ce3d22e24d3ba78712bcac6cb2129f1b48beb27dcfaf53c56b15ca"
$expectedTestScriptSha256 = "9eaead65b6a4308b482810697fdb49d4b812a739e55c9a620d551dbbf657d09f"
$expectedAuditReaderSha256 = "a6ac1fbfce9a1bceb0e379e856c3744e559c8b043fe5d5391e20a872e1f4faff"

function Get-Sha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Write-Status([object]$Value) {
    [IO.File]::WriteAllText(
        $statusPath,
        (($Value | ConvertTo-Json -Depth 12 -Compress) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
}

function Assert-ExactProperties([object]$Value, [string[]]$Names, [string]$Label) {
    if ($null -eq $Value) { throw "$Label is missing" }
    $actual = @($Value.PSObject.Properties.Name | Sort-Object)
    $expected = @($Names | Sort-Object)
    if (@(Compare-Object $expected $actual -SyncWindow 0).Count) {
        throw "$Label schema is invalid"
    }
}

function Test-Sha256Text([object]$Value) {
    return $Value -is [string] -and [string]$Value -cmatch '^[0-9a-f]{64}$'
}

function Read-TargetEvidence([string]$Path, [string]$ExpectedSha256) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "target evidence is missing"
    }
    if ((Get-Sha256 $Path) -cne $ExpectedSha256) {
        throw "target evidence hash mismatch"
    }
    $evidence = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    Assert-ExactProperties $evidence @(
        "schema_version", "artifact_type", "captured_at", "publisher_started_at",
        "witness_audit_baseline_id", "cleared_env_provider", "publisher",
        "negative_tests", "production", "git"
    ) "target evidence"
    if ([int]$evidence.schema_version -ne 1 -or
        [string]$evidence.artifact_type -cne "ruisheng.witness-system-autostart-target-acceptance") {
        throw "target evidence identity is invalid"
    }
    try {
        $capturedAt = [DateTimeOffset]::ParseExact(
            [string]$evidence.captured_at, "o", [Globalization.CultureInfo]::InvariantCulture
        )
        $publisherStartedAt = [DateTimeOffset]::ParseExact(
            [string]$evidence.publisher_started_at, "o", [Globalization.CultureInfo]::InvariantCulture
        )
    } catch {
        throw "target evidence timestamps are invalid"
    }
    if ($capturedAt.Offset -ne [TimeSpan]::Zero -or
        $publisherStartedAt.Offset -ne [TimeSpan]::Zero -or
        $capturedAt -lt $publisherStartedAt -or
        $capturedAt -gt [DateTimeOffset]::UtcNow.AddMinutes(5)) {
        throw "target evidence time ordering is invalid"
    }
    if ($evidence.witness_audit_baseline_id -isnot [int] -and
        $evidence.witness_audit_baseline_id -isnot [long]) {
        throw "witness audit baseline id is invalid"
    }
    if ([int64]$evidence.witness_audit_baseline_id -lt 0) {
        throw "witness audit baseline id is invalid"
    }

    Assert-ExactProperties $evidence.cleared_env_provider @(
        "exit_code", "attestation_sha256"
    ) "cleared-env provider evidence"
    if ([int]$evidence.cleared_env_provider.exit_code -ne 0 -or
        -not (Test-Sha256Text $evidence.cleared_env_provider.attestation_sha256)) {
        throw "cleared-env provider evidence failed"
    }
    Assert-ExactProperties $evidence.publisher @(
        "exit_code", "decision", "signature_verified", "full_hash_verified",
        "candidate_sha256"
    ) "publisher evidence"
    if ([int]$evidence.publisher.exit_code -ne 2 -or
        [string]$evidence.publisher.decision -cne "BLOCKED" -or
        $evidence.publisher.signature_verified -isnot [bool] -or
        -not [bool]$evidence.publisher.signature_verified -or
        $evidence.publisher.full_hash_verified -isnot [bool] -or
        -not [bool]$evidence.publisher.full_hash_verified -or
        -not (Test-Sha256Text $evidence.publisher.candidate_sha256)) {
        throw "publisher evidence failed"
    }
    Assert-ExactProperties $evidence.negative_tests @(
        "freshness_all_passed", "replay_all_passed", "publisher_all_passed"
    ) "negative-test evidence"
    foreach ($property in @($evidence.negative_tests.PSObject.Properties)) {
        if ($property.Value -isnot [bool] -or -not [bool]$property.Value) {
            throw "negative-test evidence failed: $($property.Name)"
        }
    }
    Assert-ExactProperties $evidence.production @(
        "container_ids_before", "container_ids_after", "temporary_listener_count"
    ) "production evidence"
    $before = @($evidence.production.container_ids_before | ForEach-Object { [string]$_ })
    $after = @($evidence.production.container_ids_after | ForEach-Object { [string]$_ })
    if ($before.Count -ne 5 -or $after.Count -ne 5 -or
        @($before | Where-Object { $_ -cnotmatch '^[0-9a-f]{12,64}$' }).Count -or
        @(Compare-Object $before $after -CaseSensitive -SyncWindow 0).Count -or
        [int]$evidence.production.temporary_listener_count -ne 0) {
        throw "production boundary evidence failed"
    }
    Assert-ExactProperties $evidence.git @(
        "implementation_files_tracked", "sensitive_material_tracked"
    ) "Git evidence"
    if ($evidence.git.implementation_files_tracked -isnot [bool] -or
        -not [bool]$evidence.git.implementation_files_tracked -or
        $evidence.git.sensitive_material_tracked -isnot [bool] -or
        [bool]$evidence.git.sensitive_material_tracked) {
        throw "Git boundary evidence failed"
    }
    return $evidence
}

$result = $null
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "administrator token required"
    }
    if ((Get-Sha256 $testScript) -cne $expectedTestScriptSha256) {
        throw "acceptance script hash mismatch"
    }
    if ((Get-Sha256 $auditReader) -cne $expectedAuditReaderSha256) {
        throw "audit reader hash mismatch"
    }
    $targetEvidence = Read-TargetEvidence $TargetEvidencePath $ExpectedTargetEvidenceSha256

    $highWaterHash = Get-Sha256 $highWater
    $witnessHash = Get-Sha256 $witness
    $listeners = @(Get-NetTCPConnection -LocalAddress $listenAddress -LocalPort $listenPort `
        -State Listen -ErrorAction SilentlyContinue)
    $temporaryListeners = @(Get-NetTCPConnection -LocalPort 38477 -State Listen `
        -ErrorAction SilentlyContinue)
    $task = Get-ScheduledTask -TaskName $taskName
    $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
    $acceptanceJson = & $testScript -TaskName $taskName -Root $root `
        -ListenAddress $listenAddress -ListenPort $listenPort
    $acceptance = $acceptanceJson | ConvertFrom-Json
    $publisherStartedAt = [string]$targetEvidence.publisher_started_at
    $auditBaselineId = [int64]$targetEvidence.witness_audit_baseline_id
    $auditOutput = & $runtimePython -I -B -S $auditReader $auditPath `
        $publisherStartedAt ([string]$auditBaselineId) 2>&1
    if ($LASTEXITCODE -ne 0) { throw "witness audit read failed: $($auditOutput -join ' ')" }
    $audit = ($auditOutput -join "`n") | ConvertFrom-Json

    $passed = $highWaterHash -ceq $expectedHighWaterSha256 -and
        $witnessHash -ceq $expectedWitnessSha256 -and
        $listeners.Count -eq 1 -and $temporaryListeners.Count -eq 0 -and
        $task.State.ToString() -ceq "Running" -and $acceptance.all_passed -and
        [int64]$audit.success_count -gt 0 -and $null -ne $audit.latest
    $result = [ordered]@{
        completed = $true
        passed = $passed
        at = [DateTimeOffset]::Now.ToString("o")
        high_water_sha256 = $highWaterHash
        witness_sha256 = $witnessHash
        listener_pid = if ($listeners.Count -eq 1) { [int]$listeners[0].OwningProcess } else { $null }
        temporary_listener_count = $temporaryListeners.Count
        task_state = $task.State.ToString()
        task_last_result = $taskInfo.LastTaskResult
        stderr_log_bytes = if (Test-Path -LiteralPath $stderrLog -PathType Leaf) {
            (Get-Item -LiteralPath $stderrLog).Length
        } else { $null }
        target_evidence_sha256 = $ExpectedTargetEvidenceSha256
        target_evidence = $targetEvidence
        audit_since = $publisherStartedAt
        audit_baseline_id = $auditBaselineId
        audit_success_count = [int64]$audit.success_count
        audit_latest = $audit.latest
        acceptance = $acceptance
    }
    if (-not $passed) { throw "final witness verification failed" }
} catch {
    if ($null -eq $result) {
        $result = [ordered]@{
            completed = $true
            passed = $false
            at = [DateTimeOffset]::Now.ToString("o")
            error = $_.Exception.Message
        }
    } else {
        $result["error"] = $_.Exception.Message
    }
} finally {
    Write-Status $result
}

$result | ConvertTo-Json -Depth 12 -Compress
if (-not $result.passed) { exit 1 }
