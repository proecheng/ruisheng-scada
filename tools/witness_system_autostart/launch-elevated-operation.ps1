[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Install", "Baseline", "Restart", "Verify", "Rollback")]
    [string]$Operation,
    [ValidatePattern('^[0-9a-f]{32}$')]
    [string]$TransactionId,
    [string]$TargetEvidencePath,
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$TargetEvidenceSha256
)

$ErrorActionPreference = "Stop"
$runnerPath = Join-Path $PSScriptRoot "run-system-autostart-elevated.ps1"
$expectedRunnerSha256 = "131e2e9712deb5492754d3f4bd6971069da8859cdaa88b1c49dfb55d38fbfa9d"
$sourceRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$argumentMap = [ordered]@{
    Operation = $Operation
    SourceRoot = $sourceRoot
}
if ($TransactionId) { $argumentMap.TransactionId = $TransactionId }
if ($TargetEvidencePath) {
    $argumentMap.TargetEvidencePath = [IO.Path]::GetFullPath($TargetEvidencePath)
}
if ($TargetEvidenceSha256) { $argumentMap.TargetEvidenceSha256 = $TargetEvidenceSha256 }
$argumentJson = $argumentMap | ConvertTo-Json -Compress
$argumentBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($argumentJson))
$runnerPathBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(
    [IO.Path]::GetFullPath($runnerPath)
))
$bootstrap = @"
`$ErrorActionPreference = 'Stop'
`$runnerPath = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$runnerPathBase64'))
`$bytes = [IO.File]::ReadAllBytes(`$runnerPath)
`$sha = [Security.Cryptography.SHA256]::Create()
try { `$actual = ([BitConverter]::ToString(`$sha.ComputeHash(`$bytes))).Replace('-', '').ToLowerInvariant() } finally { `$sha.Dispose() }
if (`$actual -cne '$expectedRunnerSha256') { throw 'elevated runner hash mismatch' }
`$json = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$argumentBase64')) | ConvertFrom-Json
`$arguments = @{}
foreach (`$property in @(`$json.PSObject.Properties)) { `$arguments[[string]`$property.Name] = `$property.Value }
`$utf8 = [Text.UTF8Encoding]::new(`$false, `$true)
`$script = [ScriptBlock]::Create(`$utf8.GetString(`$bytes))
& `$script @arguments
"@
$encodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($bootstrap))
$process = Start-Process `
    -FilePath "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" `
    -ArgumentList @(
        "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-EncodedCommand", $encodedCommand
    ) `
    -Verb RunAs `
    -PassThru
[pscustomobject]@{
    elevation_launched = $true
    operation = $Operation
    process_id = $process.Id
    runner_sha256 = $expectedRunnerSha256
} | ConvertTo-Json -Compress
