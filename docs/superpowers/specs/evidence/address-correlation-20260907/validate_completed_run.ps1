$ErrorActionPreference = 'Stop'
$Evidence = $PSScriptRoot
$Checks = [ordered]@{}
function Json([string]$Name) { return Get-Content -LiteralPath (Join-Path $Evidence $Name) -Raw -Encoding UTF8 | ConvertFrom-Json }
function Hash([string]$Path) { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Require([string]$Name,[bool]$Value) {
    $Checks[$Name] = $Value
    if (-not $Value) { throw ('Evidence check failed: ' + $Name) }
}
function Canonical($Value) { return ConvertTo-Json -InputObject $Value -Depth 30 -Compress }
$Before = Json 'target-before-approved-run.json'
$After = Json 'target-after-approved-run.json'
$Comparison = Json 'address-comparison.json'
$Receipt = Json 'installed-receipt.json'
$Transfer = Json 'transfer-manifest.json'
$Config = Json 'approved-config.json'
$RemoteHashes = Json 'target-evidence-hashes.json'
$RunId = '93b0815d-22e4-4709-81b9-b1cdf7810a84'
$RunnerName = 'modbus-runner-' + $RunId + '.jsonl'
$DryName = 'modbus-runner-69594d08-ecab-433b-a94e-3bca10ac36c5.jsonl'
$Copies = [ordered]@{
    'C:\Ruisheng\audit\address-correlation-20260907-physical.jsonl'='physical.jsonl'
    'C:\Ruisheng\audit\modbus-runner-93b0815d-22e4-4709-81b9-b1cdf7810a84.jsonl'=$RunnerName
    'C:\Ruisheng\audit\modbus-runner-69594d08-ecab-433b-a94e-3bca10ac36c5.jsonl'=$DryName
    'C:\ProgramData\Ruisheng\receipts\modbus-probe-release.json'='installed-receipt.json'
    'C:\Ruisheng\site\modbus-probe-correlation-20260907.json'='approved-config.json'
}
foreach ($Path in $Copies.Keys) {
    $Expected = @($RemoteHashes | Where-Object path -CEQ $Path)
    Require ('exact_remote_copy_' + $Copies[$Path]) ($Expected.Count -eq 1 -and (Hash (Join-Path $Evidence $Copies[$Path])) -ceq $Expected[0].sha256)
}
Require 'production_release_unchanged' ((Canonical $Before.active_release) -ceq (Canonical $After.active_release))
Require 'five_running_container_identities_unchanged' (@($After.containers).Count -eq 5 -and (Canonical $Before.containers) -ceq (Canonical $After.containers))
Require 'production_tables_remain_empty' (@($After.production_counts.PSObject.Properties | Where-Object Value -NE 0).Count -eq 0)
Require 'previous_test_database_unchanged' ((Canonical $Before.test_counts) -ceq (Canonical $After.test_counts))
Require 'readiness_and_web_healthy' ($After.api.status -ceq 'ready' -and $After.gw_internal.status -ceq 'ready' -and $After.web_http -eq 200)
Require 'no_locks_or_diagnostic_containers' (@($After.maintenance_locks | Where-Object exists).Count -eq 0 -and @($After.diagnostic_containers).Count -eq 0)
$AllowedHashChanges = @('C:\ProgramData\Ruisheng\receipts\modbus-probe-release.json','C:\Ruisheng\tools\probe_modbus_rtu.py','C:\Ruisheng\tools\run_modbus_probe.ps1')
foreach ($OldHash in $Before.hashes) {
    $NewHash = @($After.hashes | Where-Object path -CEQ $OldHash.path)
    Require ('final_hash_present_' + $OldHash.path) ($NewHash.Count -eq 1)
    if ($OldHash.path -cnotin $AllowedHashChanges) {
        Require ('unchanged_' + $OldHash.path) ($OldHash.sha256 -ceq $NewHash[0].sha256)
    }
}
Require 'installed_tools_candidate_and_running_image' ($Receipt.candidate_id -ceq 'deploy-20260907.2' -and
    $Receipt.source_commit -ceq '4de3d31c5766f0f04905756965f83c564ecce4cc' -and
    $Receipt.gw_image_id -ceq (@($After.containers | Where-Object name -CEQ '/ruisheng-gw')[0].image))
foreach ($Name in @('probe_modbus_rtu.py','run_modbus_probe.ps1')) {
    $Expected = @($Transfer.metadata | Where-Object name -CEQ $Name)[0].sha256
    Require ('installed_signed_' + $Name) (@($After.hashes | Where-Object path -CEQ ('C:\Ruisheng\tools\' + $Name))[0].sha256 -ceq $Expected)
}
$Raw = @(Get-Content -LiteralPath (Join-Path $Evidence 'physical.jsonl') -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json })
$Runner = @(Get-Content -LiteralPath (Join-Path $Evidence $RunnerName) -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json })
$Dry = @(Get-Content -LiteralPath (Join-Path $Evidence $DryName) -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json })
$Finished = @($Runner | Where-Object event -CEQ 'probe_finished')[0]
$Preflight = @($Runner | Where-Object event -CEQ 'preflight_passed')[0]
Require 'runner_complete_and_audit_accepted' ($Runner[-1].event -ceq 'runner_completed' -and $Runner[-1].probe_exit_code -eq 0 -and
    $Finished.accepted_exit_code -eq 0 -and $Finished.probe_audit.valid -and
    $Finished.probe_audit.sha256 -ceq (Hash (Join-Path $Evidence 'physical.jsonl')))
Require 'probe_container_removed' ($Finished.container_cleanup.confirmed_absent -and $Finished.container_cleanup.final_count -eq 0)
Require 'runner_production_state_unchanged' ((Canonical $Preflight.production_state) -ceq (Canonical $Runner[-1].production_state))
Require 'production_gateway_no_serial_access' (@($Runner[-1].production_state.gateway.serial_environment).Count -eq 0 -and
    @($Runner[-1].production_state.gateway.devices).Count -eq 0 -and -not $Runner[-1].production_state.gateway.privileged)
Require 'dry_run_completed' ($Dry[-1].event -ceq 'runner_completed' -and $Dry[-1].probe_exit_code -eq 0)
Require 'raw_scope_bound_to_approved_config' ($Raw[0].config_sha256 -ceq (Hash (Join-Path $Evidence 'approved-config.json')) -and
    $Raw[0].approval_scope -ceq 'b10-address-correlation-fc3-r0-35-v1' -and $Raw[0].run_id -ceq $RunId)
$Tx = @($Raw | Where-Object event -CEQ 'request_tx')
$Rx = @($Raw | Where-Object event -CEQ 'response_rx')
Require 'exact_six_read_requests_no_retry' ($Tx.Count -eq 6 -and $Rx.Count -eq 6 -and
    @($Tx | Where-Object { $_.function_code -ne 3 -or $_.attempt -ne 0 }).Count -eq 0)
Require 'six_crc_valid_responses' (@($Rx | Where-Object { $_.classification -cne 'valid' -or -not $_.crc_valid }).Count -eq 0)
Require 'exact_terminal_accounting' ($Raw[-1].completed_tx_count -eq 6 -and $Raw[-1].attempted_write_bytes -eq 48 -and $Raw[-1].tx_count_known)
Require 'comparison_artifact_bound_to_audit' ($Comparison.run_id -ceq $RunId -and $Comparison.audit_sha256 -ceq (Hash (Join-Path $Evidence 'physical.jsonl')))
Require 'observed_comparison_counts' ($Comparison.groups[0].counts.MISMATCH -eq 8 -and $Comparison.groups[0].counts.DYNAMIC_REFERENCE -eq 7 -and
    $Comparison.groups[0].counts.MATCH_UNDISCRIMINATING -eq 6 -and $Comparison.groups[1].counts.MISMATCH -eq 6 -and
    $Comparison.groups[1].counts.MATCH_UNDISCRIMINATING -eq 3)
Require 'formal_acceptance_not_falsely_passed' ($Comparison.formal_acceptance -ceq 'BLOCKED' -and -not $Comparison.physical_meanings_confirmed)
$Result = [ordered]@{
    observed_at=(Get-Date).ToString('o'); check_count=$Checks.Count; checks=$Checks; run_id=$RunId;
    physical_run='COMPLETED'; address_correlation='INCONSISTENT'; stable_mismatches=14;
    nondiscriminating_matches=9; dynamic_reference_positions=7; formal_acceptance='BLOCKED';
    additional_physical_transmissions_authorized=$false
}
$Result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $Evidence 'completed-run-validation.json') -Encoding UTF8
[pscustomobject]$Result | Select-Object observed_at,check_count,physical_run,address_correlation,formal_acceptance | ConvertTo-Json -Compress
