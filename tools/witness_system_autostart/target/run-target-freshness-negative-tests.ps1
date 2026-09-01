$ErrorActionPreference = "Stop"
$provider = "C:\ProgramData\Ruisheng\bin\trust-root-freshness-provider.exe"
$config = "C:\ProgramData\Ruisheng\trust\point-profile-freshness-provider.json"
$root = "C:\ProgramData\Ruisheng\trust\point-profile-policy-root.json"
$policy = "C:\ProgramData\Ruisheng\site\b08\point-profile-trust-policy.json"
$profile = "C:\ProgramData\Ruisheng\site\b08\point-profile.json"
$publisher = "C:\ProgramData\Ruisheng\bin\verify-publisher.ps1"
$manifest = Get-Content -Raw -LiteralPath "C:\Ruisheng\candidates\deploy-20260831.1\MANIFEST.json" |
    ConvertFrom-Json
$testRoot = "C:\ProgramData\Ruisheng\staging\freshness-negative-" + [guid]::NewGuid().ToString("N")
New-Item -ItemType Directory -Path $testRoot | Out-Null

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

function Invoke-Case([string]$Name, [string]$RootPath, [string]$PolicyPath, [int]$Expected) {
    $output = Join-Path $testRoot "$Name.json"
    $existedBefore = Test-Path -LiteralPath $output
    $arguments = @(
        "attest", "--config", $config, "--trust-root", $RootPath,
        "--trust-policy", $PolicyPath, "--profile", $profile,
        "--candidate-logical-identity", [string]$manifest.logical_identity,
        "--verifier-id", "ruisheng.protected-release-publisher.windows.v1",
        "--verifier-tool-sha256", ("sha256:" + (
            Get-FileHash -Algorithm SHA256 -LiteralPath $publisher
        ).Hash.ToLowerInvariant()),
        "--challenge", (New-Challenge), "--requested-at", (Get-CanonicalNow),
        "--output", $output
    )
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $provider
    $start.UseShellExecute = $false
    foreach ($argument in $arguments) { [void]$start.ArgumentList.Add($argument) }
    $start.Environment.Clear()
    $system = [Environment]::SystemDirectory
    $windows = [IO.Directory]::GetParent($system).FullName
    $start.Environment["COMSPEC"] = Join-Path $system "cmd.exe"
    $start.Environment["PATH"] = $system
    $start.Environment["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
    $start.Environment["SYSTEMROOT"] = $windows
    $start.Environment["WINDIR"] = $windows
    $process = [Diagnostics.Process]::Start($start)
    $process.WaitForExit()
    $created = -not $existedBefore -and (Test-Path -LiteralPath $output)
    return [pscustomobject]@{
        case = $Name
        expected = $Expected
        actual = $process.ExitCode
        passed = $process.ExitCode -eq $Expected -and (($Expected -eq 0) -eq $created)
        attestation_created = $created
    }
}

function Write-Json([string]$Path, [object]$Value) {
    [IO.File]::WriteAllText(
        $Path,
        (($Value | ConvertTo-Json -Depth 100 -Compress) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
}

try {
    $results = @()
    $results += Invoke-Case "exact" $root $policy 0

    $rootRollback = Join-Path $testRoot "root-rollback.json"
    $value = Get-Content -Raw -LiteralPath $root | ConvertFrom-Json -DateKind String
    $value.root_version = 0
    Write-Json $rootRollback $value
    $results += Invoke-Case "root-rollback" $rootRollback $policy 3

    $rootConflict = Join-Path $testRoot "root-conflict.json"
    $value = Get-Content -Raw -LiteralPath $root | ConvertFrom-Json -AsHashtable -DateKind String
    $value["negative_probe"] = 1
    Write-Json $rootConflict $value
    $results += Invoke-Case "root-same-version-hash-conflict" $rootConflict $policy 3

    $rootSwitch = Join-Path $testRoot "root-switch.json"
    $value = Get-Content -Raw -LiteralPath $root | ConvertFrom-Json -DateKind String
    $value.root_id = [string]$value.root_id + ".switched"
    Write-Json $rootSwitch $value
    $results += Invoke-Case "root-id-switch" $rootSwitch $policy 3

    $policyRollback = Join-Path $testRoot "policy-rollback.json"
    $value = Get-Content -Raw -LiteralPath $policy | ConvertFrom-Json -DateKind String
    $value.policy_version = 0
    Write-Json $policyRollback $value
    $results += Invoke-Case "policy-rollback" $root $policyRollback 3

    $policyConflict = Join-Path $testRoot "policy-conflict.json"
    $value = Get-Content -Raw -LiteralPath $policy | ConvertFrom-Json -AsHashtable -DateKind String
    $value["negative_probe"] = 1
    Write-Json $policyConflict $value
    $results += Invoke-Case "policy-same-version-hash-conflict" $root $policyConflict 3

    $policySwitch = Join-Path $testRoot "policy-switch.json"
    $value = Get-Content -Raw -LiteralPath $policy | ConvertFrom-Json -DateKind String
    $value.policy_id = [string]$value.policy_id + ".switched"
    Write-Json $policySwitch $value
    $results += Invoke-Case "policy-id-switch" $root $policySwitch 3

    $rootAhead = Join-Path $testRoot "root-ahead.json"
    $value = Get-Content -Raw -LiteralPath $root | ConvertFrom-Json -DateKind String
    $value.root_version = [int64]$value.root_version + 1
    Write-Json $rootAhead $value
    $results += Invoke-Case "root-local-ahead" $rootAhead $policy 2

    $allPassed = @($results | Where-Object { -not $_.passed }).Count -eq 0
    [pscustomobject]@{
        all_passed = $allPassed
        results = $results
    } | ConvertTo-Json -Depth 6 -Compress
    if (-not $allPassed) { throw "freshness negative tests failed" }
    exit 0
} finally {
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
