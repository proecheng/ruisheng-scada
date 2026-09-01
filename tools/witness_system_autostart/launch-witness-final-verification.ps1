[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$TargetEvidencePath,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$TargetEvidenceSha256
)

& (Join-Path $PSScriptRoot "launch-elevated-operation.ps1") `
    -Operation Verify `
    -TargetEvidencePath $TargetEvidencePath `
    -TargetEvidenceSha256 $TargetEvidenceSha256
