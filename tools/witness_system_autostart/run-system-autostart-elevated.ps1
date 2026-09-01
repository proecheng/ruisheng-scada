[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Install", "Baseline", "Restart", "Verify", "Rollback")]
    [string]$Operation,
    [Parameter(Mandatory = $true)]
    [string]$SourceRoot,
    [ValidatePattern('^[0-9a-f]{32}$')]
    [string]$TransactionId,
    [string]$TargetEvidencePath,
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$TargetEvidenceSha256
)

$ErrorActionPreference = "Stop"
$root = "C:\ProgramData\RuishengWitness"
$operationsRoot = Join-Path $root "operations"
$bundleId = "witness-system-autostart-review-20260901"
$bundleRoot = Join-Path $operationsRoot $bundleId
$bundle = [ordered]@{
    "diagnose-witness-system-start.py" = "13f38fa3f9d60da94edff7ebfd8e480a0d9357a8967f4b1ad9a8913784c2a9da"
    "freshness_witness.py" = "f441790914ce3d22e24d3ba78712bcac6cb2129f1b48beb27dcfaf53c56b15ca"
    "install-witness-system-autostart.ps1" = "1d81ef0d0826ce4f5fcd6161e116cfa669cc5493016a3a7825a3ad8168d483e5"
    "read-witness-audit.py" = "a6ac1fbfce9a1bceb0e379e856c3744e559c8b043fe5d5391e20a872e1f4faff"
    "rollback-witness-system-autostart.ps1" = "8dd3f09ff5199d14b67522e9ab7bbd29cfaaab9b1767cbddb403ca40c4ad0b55"
    "runtime-source-manifest.json" = "301172759e6269bcd1b04d7aed04c9b4df78f32150d34dd1a4c5d0cd7be329d0"
    "test-witness-system-autostart.ps1" = "9eaead65b6a4308b482810697fdb49d4b812a739e55c9a620d551dbbf657d09f"
    "verify-witness-final-state.ps1" = "53e541505be8350bd634f5c0f0df326381c0ece6789305c09913458c91dbf5a7"
    "verify-witness-system-restart.ps1" = "6831b37afda8003345c5838595752887b945f7c695c250e725a9ab3515bd31c1"
}

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "administrator token required"
    }
}

function Get-BytesSha256([byte[]]$Bytes) {
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha256.ComputeHash($Bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }
}

function New-ProtectedDirectorySecurity {
    $security = [Security.AccessControl.DirectorySecurity]::new()
    $security.SetAccessRuleProtection($true, $false)
    $administrators = [Security.Principal.SecurityIdentifier]::new("S-1-5-32-544")
    $system = [Security.Principal.SecurityIdentifier]::new("S-1-5-18")
    $security.SetOwner($administrators)
    $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    foreach ($sid in @($system, $administrators)) {
        [void]$security.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow
        ))
    }
    return $security
}

function New-ProtectedFileSecurity {
    $security = [Security.AccessControl.FileSecurity]::new()
    $security.SetAccessRuleProtection($true, $false)
    $administrators = [Security.Principal.SecurityIdentifier]::new("S-1-5-32-544")
    $system = [Security.Principal.SecurityIdentifier]::new("S-1-5-18")
    $security.SetOwner($administrators)
    foreach ($sid in @($system, $administrators)) {
        [void]$security.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [Security.AccessControl.FileSystemRights]::FullControl,
            [Security.AccessControl.AccessControlType]::Allow
        ))
    }
    return $security
}

function Stage-ApprovedBundle {
    New-Item -ItemType Directory -Path $operationsRoot -Force | Out-Null
    Set-Acl -LiteralPath $operationsRoot -AclObject (New-ProtectedDirectorySecurity)
    New-Item -ItemType Directory -Path $bundleRoot -Force | Out-Null
    Set-Acl -LiteralPath $bundleRoot -AclObject (New-ProtectedDirectorySecurity)
    foreach ($entry in $bundle.GetEnumerator()) {
        $source = Join-Path $SourceRoot ([string]$entry.Key)
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "approved bundle input is missing: $($entry.Key)"
        }
        $bytes = [IO.File]::ReadAllBytes($source)
        if ((Get-BytesSha256 $bytes) -cne [string]$entry.Value) {
            throw "approved bundle input hash mismatch: $($entry.Key)"
        }
        $destination = Join-Path $bundleRoot ([string]$entry.Key)
        [IO.File]::WriteAllBytes($destination, $bytes)
        Set-Acl -LiteralPath $destination -AclObject (New-ProtectedFileSecurity)
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $destination).Hash.ToLowerInvariant() -cne
            [string]$entry.Value) {
            throw "protected bundle copy hash mismatch: $($entry.Key)"
        }
    }
}

function Invoke-ProtectedPowerShell(
    [string]$ScriptPath,
    [Collections.IDictionary]$Arguments,
    [int]$TimeoutSeconds
) {
    $scriptPathBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($ScriptPath))
    $argumentJson = $Arguments | ConvertTo-Json -Compress
    $argumentBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($argumentJson))
    $command = @"
`$ErrorActionPreference = 'Stop'
`$scriptPath = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$scriptPathBase64'))
`$json = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$argumentBase64')) | ConvertFrom-Json
`$map = @{}
foreach (`$property in @(`$json.PSObject.Properties)) { `$map[[string]`$property.Name] = `$property.Value }
& `$scriptPath @map
"@
    $encodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    $start.Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand $encodedCommand"
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    if (-not $process.Start()) { throw "protected operation did not start" }
    try {
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill()
            [void]$process.WaitForExit(5000)
            throw "protected operation timed out: $ScriptPath"
        }
        return [pscustomobject]@{
            exit_code = $process.ExitCode
            stdout = $stdoutTask.GetAwaiter().GetResult()
            stderr = $stderrTask.GetAwaiter().GetResult()
        }
    } finally {
        $process.Dispose()
    }
}

Assert-Administrator
Stage-ApprovedBundle
$lines = [Collections.Generic.List[string]]::new()
$code = 1
$errorMessage = $null
try {
    if ($Operation -ceq "Install") {
        $operationResult = Invoke-ProtectedPowerShell `
            (Join-Path $bundleRoot "install-witness-system-autostart.ps1") @{} 900
    } elseif ($Operation -ceq "Baseline") {
        $baselineOutput = & (Join-Path $root "runtime\python.exe") -I -B -S `
            (Join-Path $bundleRoot "read-witness-audit.py") `
            (Join-Path $root "trust\witness-audit.sqlite3") 2>&1 |
            ForEach-Object { [void]$lines.Add([string]$_) }
        if ($LASTEXITCODE -ne 0) { throw "audit baseline reader exited with code $LASTEXITCODE" }
        $operationResult = [pscustomobject]@{ exit_code = 0; stdout = ""; stderr = "" }
    } elseif ($Operation -ceq "Restart") {
        $operationResult = Invoke-ProtectedPowerShell `
            (Join-Path $bundleRoot "verify-witness-system-restart.ps1") @{} 180
    } elseif ($Operation -ceq "Verify") {
        if (-not $TargetEvidencePath -or -not $TargetEvidenceSha256) {
            throw "target evidence path and SHA-256 are required for final verification"
        }
        $operationResult = Invoke-ProtectedPowerShell `
            (Join-Path $bundleRoot "verify-witness-final-state.ps1") ([ordered]@{
                TargetEvidencePath = $TargetEvidencePath
                ExpectedTargetEvidenceSha256 = $TargetEvidenceSha256
            }) 180
    } else {
        if (-not $TransactionId) { throw "transaction id is required for rollback" }
        $operationResult = Invoke-ProtectedPowerShell `
            (Join-Path $bundleRoot "rollback-witness-system-autostart.ps1") ([ordered]@{
                TransactionId = $TransactionId
            }) 300
    }
    foreach ($line in @(([string]$operationResult.stdout) -split "`r?`n")) {
        if ($line) { [void]$lines.Add($line) }
    }
    foreach ($line in @(([string]$operationResult.stderr) -split "`r?`n")) {
        if ($line) { [void]$lines.Add("STDERR: $line") }
    }
    if ([int]$operationResult.exit_code -ne 0) {
        throw "$Operation script exited with code $($operationResult.exit_code)"
    }
    $code = 0
} catch {
    $errorMessage = $_.Exception.Message
    [void]$lines.Add(($_ | Out-String))
} finally {
    $output = Join-Path $bundleRoot "$($Operation.ToLowerInvariant()).log"
    $status = Join-Path $bundleRoot "$($Operation.ToLowerInvariant()).status.json"
    [IO.File]::WriteAllLines($output, $lines, [Text.UTF8Encoding]::new($false))
    Set-Acl -LiteralPath $output -AclObject (New-ProtectedFileSecurity)
    $statusValue = [ordered]@{
        completed = $true
        operation = $Operation
        exit_code = $code
        is_administrator = $true
        bundle_id = $bundleId
        at = [DateTimeOffset]::Now.ToString("o")
    }
    if ($null -ne $errorMessage) { $statusValue.error = $errorMessage }
    [IO.File]::WriteAllText(
        $status,
        (($statusValue | ConvertTo-Json -Compress) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
    Set-Acl -LiteralPath $status -AclObject (New-ProtectedFileSecurity)
}
exit $code
