[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RunDirectory,
    [Parameter(Mandatory = $true)][ValidateRange(0, [long]::MaxValue)][long]$WitnessAuditBaselineId,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\.."))
)

$ErrorActionPreference = "Stop"
$runRoot = [IO.Path]::GetFullPath($RunDirectory)
if (-not (Test-Path -LiteralPath $runRoot -PathType Container)) {
    throw "target acceptance run directory is missing"
}
$summary = Get-Content -Raw -LiteralPath (Join-Path $runRoot "summary.json") | ConvertFrom-Json
if ($summary.passed -isnot [bool] -or -not [bool]$summary.passed) {
    throw "target acceptance run did not pass"
}
$PublisherStartedAt = [string]$summary.publisher_started_at
$ClearedEnvResultPath = Join-Path $runRoot "cleared-env-result.json"
$AttestationPath = Join-Path $runRoot "freshness-cleared-env.json"
$PublisherResultPath = Join-Path $runRoot "publisher-result.json"
$CandidateManifestPath = Join-Path $runRoot "candidate-MANIFEST.json"
$FreshnessNegativeResultPath = Join-Path $runRoot "freshness-negative-result.json"
$ReplayNegativeResultPath = Join-Path $runRoot "replay-negative-result.json"
$PublisherNegativeResultPath = Join-Path $runRoot "publisher-negative-result.json"
$ContainersBeforePath = Join-Path $runRoot "containers-before.json"
$ContainersAfterPath = Join-Path $runRoot "containers-after.json"

function Read-Json([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label is missing" }
    try { return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json } catch {
        throw "$Label is not valid JSON: $($_.Exception.Message)"
    }
}

function Get-Sha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "file is missing: $Path" }
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Get-AllPassed([string]$Path, [string]$Label) {
    $value = Read-Json $Path $Label
    if ($value.all_passed -isnot [bool] -or -not [bool]$value.all_passed) {
        throw "$Label did not pass"
    }
    return $true
}

function Read-ContainerSnapshot([string]$Path, [string]$Label) {
    $value = Read-Json $Path $Label
    $ids = @($value.container_ids | ForEach-Object { [string]$_ })
    if ($ids.Count -ne 5 -or @($ids | Where-Object { $_ -cnotmatch '^[0-9a-f]{12,64}$' }).Count) {
        throw "$Label must contain exactly five container ids"
    }
    if ($value.temporary_listener_count -isnot [int] -and
        $value.temporary_listener_count -isnot [long]) {
        throw "$Label temporary listener count is invalid"
    }
    return [pscustomobject]@{
        container_ids = $ids
        temporary_listener_count = [int]$value.temporary_listener_count
    }
}

$publisherStarted = [DateTimeOffset]::ParseExact(
    $PublisherStartedAt,
    "o",
    [Globalization.CultureInfo]::InvariantCulture
)
if ($publisherStarted.Offset -ne [TimeSpan]::Zero) {
    throw "publisher start timestamp must use UTC"
}
$cleared = Read-Json $ClearedEnvResultPath "cleared-env provider result"
if ([int]$cleared.exit_code -ne 0 -or $cleared.output_exists -isnot [bool] -or
    -not [bool]$cleared.output_exists) {
    throw "cleared-env provider did not produce an attestation"
}
$attestationSha256 = Get-Sha256 $AttestationPath

$publisher = Read-Json $PublisherResultPath "publisher result"
if ([int]$publisher.exit_code -ne 2) { throw "publisher did not return expected exit code 2" }
$publisherOutput = [string]$publisher.stdout
if ($publisherOutput.IndexOf(
    "[publisher] VERIFIED: publisher signature and complete candidate hashes passed",
    [StringComparison]::Ordinal
) -lt 0) {
    throw "publisher signature and complete-hash marker is missing"
}
$reports = @($publisherOutput -split "`r?`n" | ForEach-Object {
    try { $_ | ConvertFrom-Json -ErrorAction Stop } catch { $null }
} | Where-Object { $null -ne $_ -and $_.PSObject.Properties.Name -contains "decision" })
if ($reports.Count -lt 1 -or [string]$reports[-1].decision -cne "BLOCKED") {
    throw "publisher BLOCKED report is missing"
}

$before = Read-ContainerSnapshot $ContainersBeforePath "pre-acceptance container snapshot"
$after = Read-ContainerSnapshot $ContainersAfterPath "post-acceptance container snapshot"
if (@(Compare-Object $before.container_ids $after.container_ids -CaseSensitive -SyncWindow 0).Count) {
    throw "production container ids changed during acceptance"
}
if ($after.temporary_listener_count -ne 0) { throw "temporary target listener remains active" }

$repo = [IO.Path]::GetFullPath($RepositoryRoot)
$tracked = @(& git -C $repo ls-files -- "tools/witness_system_autostart")
if ($LASTEXITCODE -ne 0) { throw "unable to inspect Git implementation inventory" }
$requiredTracked = @(
    "tools/witness_system_autostart/freshness_witness.py",
    "tools/witness_system_autostart/install-witness-system-autostart.ps1",
    "tools/witness_system_autostart/rollback-witness-system-autostart.ps1",
    "tools/witness_system_autostart/runtime-source-manifest.json",
    "tools/witness_system_autostart/test-witness-system-autostart.ps1"
)
$implementationFilesTracked = @($requiredTracked | Where-Object { $_ -cnotin $tracked }).Count -eq 0
$allTracked = @(& git -C $repo ls-files)
if ($LASTEXITCODE -ne 0) { throw "unable to inspect Git tracked files" }
$sensitiveTracked = @($allTracked | Where-Object {
    $_ -match '(?i)(?:^|/)(?:secrets?|private)(?:/|$)' -or
    $_ -match '(?i)\.(?:pem|key|pfx|p12|sqlite3?|log)$'
})
if ($sensitiveTracked.Count) {
    throw "sensitive material is tracked by Git: $($sensitiveTracked -join ', ')"
}
if (-not $implementationFilesTracked) {
    throw "witness implementation files are not all tracked by Git"
}

$evidence = [ordered]@{
    schema_version = 1
    artifact_type = "ruisheng.witness-system-autostart-target-acceptance"
    captured_at = [DateTimeOffset]::UtcNow.ToString("o")
    publisher_started_at = $publisherStarted.ToString("o")
    witness_audit_baseline_id = $WitnessAuditBaselineId
    cleared_env_provider = [ordered]@{
        exit_code = 0
        attestation_sha256 = $attestationSha256
    }
    publisher = [ordered]@{
        exit_code = 2
        decision = "BLOCKED"
        signature_verified = $true
        full_hash_verified = $true
        candidate_sha256 = Get-Sha256 $CandidateManifestPath
    }
    negative_tests = [ordered]@{
        freshness_all_passed = Get-AllPassed $FreshnessNegativeResultPath "freshness negatives"
        replay_all_passed = Get-AllPassed $ReplayNegativeResultPath "replay negatives"
        publisher_all_passed = Get-AllPassed $PublisherNegativeResultPath "publisher negatives"
    }
    production = [ordered]@{
        container_ids_before = $before.container_ids
        container_ids_after = $after.container_ids
        temporary_listener_count = $after.temporary_listener_count
    }
    git = [ordered]@{
        implementation_files_tracked = $true
        sensitive_material_tracked = $false
    }
}
$outputFullPath = [IO.Path]::GetFullPath($OutputPath)
$parent = Split-Path -Parent $outputFullPath
if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
    throw "target evidence output directory is missing"
}
$temporary = "$outputFullPath.$([guid]::NewGuid().ToString('N')).tmp"
try {
    [IO.File]::WriteAllText(
        $temporary,
        (($evidence | ConvertTo-Json -Depth 8) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $temporary -Destination $outputFullPath -Force
} finally {
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
}
[pscustomobject]@{
    path = $outputFullPath
    sha256 = Get-Sha256 $outputFullPath
    evidence = $evidence
} | ConvertTo-Json -Depth 9 -Compress
