[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{32}$')]
    [string]$TransactionId
)

& (Join-Path $PSScriptRoot "launch-elevated-operation.ps1") `
    -Operation Rollback `
    -TransactionId $TransactionId
