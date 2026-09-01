$ErrorActionPreference = "Stop"
$provider = "C:\ProgramData\Ruisheng\bin\trust-root-freshness-provider.exe"
$providerHold = "C:\ProgramData\Ruisheng\staging\trust-root-freshness-provider.unavailable"
$runtime = "C:\ProgramData\Ruisheng\runtime\Lib\site-packages\typing_extensions.py"
$runtimeBackup = "C:\ProgramData\Ruisheng\staging\typing_extensions.runtime-backup"
$publisher = "C:\ProgramData\Ruisheng\bin\verify-publisher.ps1"
$candidate = "C:\Ruisheng\candidates\deploy-20260831.1"
$profile = "C:\ProgramData\Ruisheng\site\b08\point-profile.json"
$site = "C:\ProgramData\Ruisheng\site\b08"
$policy = "C:\ProgramData\Ruisheng\site\b08\point-profile-trust-policy.json"

function Invoke-Publisher {
    & pwsh.exe $publisher $candidate `
        -QualificationMode ValidatorProfile `
        -QualificationProfilePath $profile `
        -QualificationRootPath $site `
        -QualificationTrustPolicyPath $policy *> $null
    return [int]$LASTEXITCODE
}

$providerHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $provider).Hash.ToLowerInvariant()
$runtimeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $runtime).Hash.ToLowerInvariant()
$results = @()
try {
    Move-Item -LiteralPath $provider -Destination $providerHold
    $code = Invoke-Publisher
    $results += [pscustomobject]@{
        case = "provider-unavailable"; expected = 2; actual = $code; passed = $code -eq 2
    }
} finally {
    if (Test-Path -LiteralPath $providerHold) {
        Move-Item -LiteralPath $providerHold -Destination $provider
    }
}

try {
    Copy-Item -LiteralPath $runtime -Destination $runtimeBackup
    [IO.File]::AppendAllText($runtime, "# tamper`n", [Text.UTF8Encoding]::new($false))
    $code = Invoke-Publisher
    $results += [pscustomobject]@{
        case = "runtime-content-tamper"; expected = 2; actual = $code; passed = $code -eq 2
    }
} finally {
    if (Test-Path -LiteralPath $runtimeBackup) {
        Copy-Item -LiteralPath $runtimeBackup -Destination $runtime -Force
        Remove-Item -LiteralPath $runtimeBackup -Force
    }
}

try {
    $aclBefore = Get-Acl -LiteralPath $runtime
    $acl = Get-Acl -LiteralPath $runtime
    $users = [Security.Principal.SecurityIdentifier]::new("S-1-5-32-545")
    $rule = [Security.AccessControl.FileSystemAccessRule]::new(
        $users,
        [Security.AccessControl.FileSystemRights]::Write,
        [Security.AccessControl.AccessControlType]::Allow
    )
    [void]$acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $runtime -AclObject $acl
    $code = Invoke-Publisher
    $results += [pscustomobject]@{
        case = "runtime-acl-tamper"; expected = 2; actual = $code; passed = $code -eq 2
    }
} finally {
    if ($null -ne $aclBefore) { Set-Acl -LiteralPath $runtime -AclObject $aclBefore }
}

$providerRestored = (Get-FileHash -Algorithm SHA256 -LiteralPath $provider).Hash.ToLowerInvariant() -ceq $providerHash
$runtimeRestored = (Get-FileHash -Algorithm SHA256 -LiteralPath $runtime).Hash.ToLowerInvariant() -ceq $runtimeHash
$allPassed = @($results | Where-Object { -not $_.passed }).Count -eq 0 -and
    $providerRestored -and $runtimeRestored
[pscustomobject]@{
    all_passed = $allPassed
    provider_restored = $providerRestored
    runtime_restored = $runtimeRestored
    results = $results
} | ConvertTo-Json -Depth 6 -Compress
if (-not $allPassed) { throw "publisher negative tests failed or restoration was incomplete" }
exit 0
