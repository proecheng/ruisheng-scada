$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'remote_helpers.ps1')
$RunNames = @{}
foreach ($Kind in @('dryrun','physical')) {
    $Text = Get-Content -LiteralPath (Join-Path $PSScriptRoot ('target-' + $Kind + '.log')) -Raw -Encoding UTF8
    $Matches = [regex]::Matches($Text, 'modbus-runner-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jsonl')
    if ($Matches.Count -ne 1) { throw ('Missing unique runner audit: ' + $Kind) }
    $RunNames[$Kind] = $Matches[0].Value
}
$Hashes = @()
$Hashes += Copy-ZeroFromTarget 'C:\Ruisheng\audit\zero-origin-block-20260907-physical.jsonl' 'physical.jsonl'
foreach ($Kind in @('dryrun','physical')) {
    $Hashes += Copy-ZeroFromTarget ('C:\Ruisheng\audit\' + $RunNames[$Kind]) $RunNames[$Kind]
}
$Hashes += Copy-ZeroFromTarget 'C:\ProgramData\Ruisheng\receipts\modbus-probe-release.json' 'installed-receipt.json'
$Hashes += Copy-ZeroFromTarget 'C:\Ruisheng\site\modbus-probe-zero-origin-20260907.json' 'installed-config.json'
$Hashes | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'target-evidence-hashes.json') -Encoding UTF8
$RunNames | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'runner-names.json') -Encoding UTF8
Invoke-ZeroTargetFile 'target_verify.ps1' '' 'target-after-approved-run.json'
