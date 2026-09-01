$ErrorActionPreference = "Stop"
$runtime = "C:\ProgramData\Ruisheng\runtime"
$dependencyRoot = Join-Path $runtime "Lib\site-packages"
$provider = "C:\ProgramData\Ruisheng\bin\trust-root-freshness-provider.exe"
$config = "C:\ProgramData\Ruisheng\trust\point-profile-freshness-provider.json"
$root = "C:\ProgramData\Ruisheng\trust\point-profile-policy-root.json"
$policy = "C:\ProgramData\Ruisheng\site\b08\point-profile-trust-policy.json"
$profile = "C:\ProgramData\Ruisheng\site\b08\point-profile.json"
$publisher = "C:\ProgramData\Ruisheng\bin\verify-publisher.ps1"
$candidate = "C:\Ruisheng\candidates\deploy-20260831.1"
$work = "C:\ProgramData\Ruisheng\staging\freshness-replay-" + [guid]::NewGuid().ToString("N")
$toolchain = Join-Path $work "toolchain"
$attestation = Join-Path $work "attestation.json"
New-Item -ItemType Directory -Path $toolchain -Force | Out-Null

function New-Challenge {
    $bytes = [byte[]]::new(32)
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $random.GetBytes($bytes) } finally { $random.Dispose() }
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}
function Get-CanonicalNow {
    $now = [DateTimeOffset]::UtcNow
    $micros = [int64][Math]::Floor([decimal]($now.Ticks % [TimeSpan]::TicksPerSecond) / 10)
    $prefix = $now.ToString("yyyy-MM-dd'T'HH:mm:ss", [Globalization.CultureInfo]::InvariantCulture)
    if ($micros -eq 0) { return "${prefix}+00:00" }
    return "${prefix}.$($micros.ToString('D6'))+00:00"
}
function Invoke-ProcessWithTimeout([string]$FileName, [string[]]$Arguments, [int]$Seconds = 30) {
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $FileName
    $start.UseShellExecute = $false
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($argument in $Arguments) { [void]$start.ArgumentList.Add($argument) }
    $process = [Diagnostics.Process]::Start($start)
    if (-not $process.WaitForExit($Seconds * 1000)) {
        $process.Kill($true)
        throw "subprocess timed out: $FileName"
    }
    return [pscustomobject]@{
        exit_code = $process.ExitCode
        stdout = $process.StandardOutput.ReadToEnd()
        stderr = $process.StandardError.ReadToEnd()
    }
}
function Invoke-Preflight([string]$Challenge, [string]$RequestedAt) {
    $script = Join-Path $toolchain "tools\trust_root_freshness.py"
    $bootstrap = @'
import pathlib, runpy, sys, types
dependency_root, root, script = sys.argv[1:4]
sys.path.insert(0, dependency_root)
sys.path.insert(0, root)
package = types.ModuleType("tools")
package.__path__ = [str(pathlib.Path(root) / "tools")]
sys.modules["tools"] = package
sys.argv = [script, *sys.argv[4:]]
runpy.run_path(script, run_name="__main__")
'@
    $arguments = @(
        "preflight", $profile, "--trust-policy", $policy,
        "--trust-root-snapshot", $root, "--provider-config-snapshot", $config,
        "--attestation", $attestation, "--challenge", $Challenge,
        "--requested-at", $RequestedAt,
        "--candidate-logical-identity", [string]$manifest.logical_identity,
        "--expected-trust-root-snapshot-sha256", ("sha256:" + (
            Get-FileHash -Algorithm SHA256 -LiteralPath $root
        ).Hash.ToLowerInvariant()),
        "--expected-provider-config-snapshot-sha256", ("sha256:" + (
            Get-FileHash -Algorithm SHA256 -LiteralPath $config
        ).Hash.ToLowerInvariant()),
        "--expected-attestation-sha256", ("sha256:" + (
            Get-FileHash -Algorithm SHA256 -LiteralPath $attestation
        ).Hash.ToLowerInvariant())
    )
    $processArguments = @(
        "-I", "-B", "-S", "-X", "utf8", "-c", $bootstrap,
        $dependencyRoot, $toolchain, $script
    ) + $arguments
    $result = Invoke-ProcessWithTimeout (Join-Path $runtime "python.exe") $processArguments
    return [pscustomobject]@{ exit_code = $result.exit_code; report = $result.stdout.Trim() }
}

function Invoke-Qualification {
    $script = Join-Path $toolchain "tools\validate_device_point_profile.py"
    $bootstrap = @'
import pathlib, runpy, sys, types
dependency_root, root, script = sys.argv[1:4]
sys.path.insert(0, dependency_root)
sys.path.insert(0, root)
package = types.ModuleType("tools")
package.__path__ = [str(pathlib.Path(root) / "tools")]
sys.modules["tools"] = package
sys.argv = [script, *sys.argv[4:]]
runpy.run_path(script, run_name="__main__")
'@
    $request = (Get-Content -Raw -LiteralPath $attestation | ConvertFrom-Json -DateKind String).request
    $arguments = @(
        "qualify", $profile, "--trust-policy", $policy,
        "--trust-root-snapshot", $root, "--provider-config-snapshot", $config,
        "--attestation", $attestation, "--challenge", [string]$request.challenge,
        "--requested-at", [string]$request.requested_at,
        "--candidate-logical-identity", [string]$request.candidate_logical_identity,
        "--expected-trust-root-snapshot-sha256", [string]$request.root_snapshot_sha256,
        "--expected-provider-config-snapshot-sha256", [string]$request.provider_config_sha256,
        "--expected-attestation-sha256", ("sha256:" + (
            Get-FileHash -Algorithm SHA256 -LiteralPath $attestation
        ).Hash.ToLowerInvariant()),
        "--evidence-root", "C:\ProgramData\Ruisheng\site\b08"
    )
    $processArguments = @(
        "-I", "-B", "-S", "-X", "utf8", "-c", $bootstrap,
        $dependencyRoot, $toolchain, $script
    ) + $arguments
    $result = Invoke-ProcessWithTimeout (Join-Path $runtime "python.exe") $processArguments 60
    return [pscustomobject]@{ exit_code = $result.exit_code; report = $result.stdout.Trim() }
}

try {
    tar.exe -xzf (Join-Path $candidate "qualification-toolchain.tar.gz") -C $toolchain
    if ($LASTEXITCODE -ne 0) { throw "qualification toolchain extraction failed" }
    $manifest = Get-Content -Raw -LiteralPath (Join-Path $candidate "MANIFEST.json") |
        ConvertFrom-Json -DateKind String
    $challenge = New-Challenge
    $requestedAt = Get-CanonicalNow
    & $provider attest --config $config --trust-root $root --trust-policy $policy `
        --profile $profile --candidate-logical-identity ([string]$manifest.logical_identity) `
        --verifier-id "ruisheng.protected-release-publisher.windows.v1" `
        --verifier-tool-sha256 ("sha256:" + (
            Get-FileHash -Algorithm SHA256 -LiteralPath $publisher
        ).Hash.ToLowerInvariant()) `
        --challenge $challenge --requested-at $requestedAt --output $attestation
    if ($LASTEXITCODE -ne 0) { throw "exact attestation creation failed" }
    $exact = Invoke-Preflight $challenge $requestedAt
    $replay = Invoke-Preflight (New-Challenge) $requestedAt
    $qualification = Invoke-Qualification
    $replayReport = $replay.report | ConvertFrom-Json
    $allPassed = $exact.exit_code -eq 0 -and $replay.exit_code -eq 3 -and
        $replayReport.reason_code -ceq "FRESHNESS_REQUEST_MISMATCH" -and
        $qualification.exit_code -eq 2
    [pscustomobject]@{
        all_passed = $allPassed
        exact = $exact
        replay = $replay
        qualification = $qualification
    } | ConvertTo-Json -Depth 6 -Compress
    if (-not $allPassed) { throw "replay negative test failed" }
    exit 0
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
