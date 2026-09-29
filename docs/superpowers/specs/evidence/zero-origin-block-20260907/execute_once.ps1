$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'remote_helpers.ps1')
$Transfer = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'transfer-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$DryText = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'target-dryrun.log') -Raw -Encoding UTF8
$DryLine = @($DryText -split '\r?\n' | Where-Object { $_.StartsWith('{') })
if ($DryLine.Count -ne 1) { throw 'Missing unique dry-run result' }
$Dry = $DryLine[0] | ConvertFrom-Json
$Approved = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'approved-config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$ConfigHash = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'approved-config.json') -Algorithm SHA256).Hash.ToLowerInvariant()
if ($Dry.mode -cne 'dry-run' -or $Dry.config_sha256 -cne $ConfigHash -or
    $ConfigHash -cne $Transfer.config_sha256 -or
    $Dry.script_sha256 -cne @($Transfer.metadata | Where-Object name -CEQ 'probe_modbus_rtu.py')[0].sha256 -or
    $Dry.plan.scope.unit_id -ne 1 -or $Dry.plan.scope.function_code -ne 3 -or
    @($Dry.plan.scope.requests).Count -ne 5 -or
    $Dry.plan.budget.max_requests -ne 10 -or $Dry.plan.budget.max_response_bytes -ne 80) {
    throw 'Dry run does not match the exact approved plan'
}
for ($Index = 0; $Index -lt 5; $Index++) {
    foreach ($Field in @('start_address','register_count','requires_previous_valid')) {
        if ($Dry.plan.scope.requests[$Index].$Field -cne $Approved.scope.requests[$Index].$Field) {
            throw 'Dry-run ordered range mismatch'
        }
    }
}
$ProbeAudit = 'C:\Ruisheng\audit\zero-origin-block-20260907-physical.jsonl'
$Marker = Join-Path $PSScriptRoot 'single-run-dispatch.json'
$Bytes = [Text.Encoding]::UTF8.GetBytes(([ordered]@{
    observed_at=(Get-Date).ToString('o'); scope=$Approved.approval.scope_id;
    config_sha256=$ConfigHash; physical_audit=$ProbeAudit;
    dispatch_once=$true; automatic_retry_of_run_forbidden=$true
} | ConvertTo-Json -Compress))
$Stream = [IO.FileStream]::new($Marker,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None,4096,[IO.FileOptions]::WriteThrough)
try { $Stream.Write($Bytes,0,$Bytes.Length); $Stream.Flush($true) } finally { $Stream.Dispose() }
$Command = "if (Test-Path -LiteralPath '$ProbeAudit') { throw 'Physical audit already exists' }; " +
    "& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoProfile -NonInteractive -File 'C:\Ruisheng\tools\run_modbus_probe.ps1' " +
    "-ConfigPath 'C:\Ruisheng\site\modbus-probe-zero-origin-20260907.json' -AuditPath '$ProbeAudit' -Execute; exit " + '$LASTEXITCODE'
Invoke-ZeroTarget $Command 'target-physical.log'
