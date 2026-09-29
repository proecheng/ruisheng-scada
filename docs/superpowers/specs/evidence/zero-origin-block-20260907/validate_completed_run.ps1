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
$Comparison = Json 'whole-block-comparison.json'
$Receipt = Json 'installed-receipt.json'
$Transfer = Json 'transfer-manifest.json'
$Source = Json 'source-manifest.json'
$Config = Json 'approved-config.json'
$RemoteHashes = Json 'target-evidence-hashes.json'
$Names = Json 'runner-names.json'
foreach ($Copy in $RemoteHashes) {
    Require ('exact_remote_copy_' + $Copy.local_name) ((Hash (Join-Path $Evidence $Copy.local_name)) -ceq $Copy.sha256)
}
Require 'five_remote_copies' (@($RemoteHashes).Count -eq 5)
Require 'installed_config_exact' ((Hash (Join-Path $Evidence 'approved-config.json')) -ceq (Hash (Join-Path $Evidence 'installed-config.json')))
Require 'production_release_unchanged' ((Canonical $Before.active_release) -ceq (Canonical $After.active_release))
Require 'production_still_release_1' ($After.active_release.candidate_id -ceq 'deploy-20260907.1')
Require 'five_running_container_identities_unchanged' (@($After.containers).Count -eq 5 -and (Canonical $Before.containers) -ceq (Canonical $After.containers))
Require 'production_tables_remain_empty' (@($After.production_counts.PSObject.Properties | Where-Object Value -NE 0).Count -eq 0)
Require 'previous_test_database_unchanged' ((Canonical $Before.test_counts) -ceq (Canonical $After.test_counts))
Require 'database_migration_head_unchanged' ($Before.database_head -ceq $After.database_head)
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
Require 'installed_tools_candidate_and_source' ($Receipt.candidate_id -ceq 'deploy-20260907.3' -and $Receipt.source_commit -ceq $Source.source_commit -and $Receipt.source_commit -ceq $Transfer.source_commit)
Require 'diagnostic_runtime_is_deployed_image' ($Receipt.gw_image_id -ceq @($After.containers | Where-Object name -CEQ '/ruisheng-gw')[0].image)
foreach ($Image in $Transfer.images) {
    $Running = @($After.containers | Where-Object name -CEQ ('/ruisheng-' + $Image.component))
    Require ('candidate_reuses_deployed_' + $Image.component) ($Running.Count -eq 1 -and $Running[0].image -ceq $Image.image_id)
}
foreach ($Name in @('probe_modbus_rtu.py','run_modbus_probe.ps1')) {
    $Expected = @($Transfer.metadata | Where-Object name -CEQ $Name)[0].sha256
    Require ('installed_signed_' + $Name) (@($After.hashes | Where-Object path -CEQ ('C:\Ruisheng\tools\' + $Name))[0].sha256 -ceq $Expected)
}
$Raw = @(Get-Content -LiteralPath (Join-Path $Evidence 'physical.jsonl') -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json })
$Runner = @(Get-Content -LiteralPath (Join-Path $Evidence $Names.physical) -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json })
$Dry = @(Get-Content -LiteralPath (Join-Path $Evidence $Names.dryrun) -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json })
$Finished = @($Runner | Where-Object event -CEQ 'probe_finished')[0]
$Preflight = @($Runner | Where-Object event -CEQ 'preflight_passed')[0]
$DryFinished = @($Dry | Where-Object event -CEQ 'probe_finished')[0]
$RunId = $Raw[0].run_id
Require 'all_audit_events_same_run' (@($Raw | Where-Object run_id -CNE $RunId).Count -eq 0 -and @($Runner | Where-Object run_id -CNE $RunId).Count -eq 0)
Require 'runner_complete_and_audit_accepted' ($Runner[-1].event -ceq 'runner_completed' -and $Runner[-1].probe_exit_code -eq 0 -and $Finished.accepted_exit_code -eq 0 -and $Finished.probe_audit.valid -and $Finished.probe_audit.sha256 -ceq (Hash (Join-Path $Evidence 'physical.jsonl')))
Require 'probe_container_removed' ($Finished.container_cleanup.confirmed_absent -and $Finished.container_cleanup.final_count -eq 0)
Require 'runner_production_state_unchanged' ((Canonical $Preflight.production_state) -ceq (Canonical $Runner[-1].production_state))
Require 'production_gateway_no_serial_access' (@($Runner[-1].production_state.gateway.serial_environment).Count -eq 0 -and @($Runner[-1].production_state.gateway.devices).Count -eq 0 -and -not $Runner[-1].production_state.gateway.privileged)
Require 'dry_run_completed_without_execution' ($Dry[0].execute -eq $false -and $Dry[-1].event -ceq 'runner_completed' -and $DryFinished.dry_run.mode -ceq 'dry-run' -and $DryFinished.container_cleanup.confirmed_absent)
Require 'raw_scope_bound_to_approved_config' ($Raw[0].config_sha256 -ceq (Hash (Join-Path $Evidence 'approved-config.json')) -and $Raw[0].approval_scope -ceq 'b11-zero-origin-block-fc3-r0-35-v1')
Require 'raw_receipt_bound' ($Raw[0].receipt_sha256 -ceq (Hash (Join-Path $Evidence 'installed-receipt.json')))
Require 'raw_receive_and_tx_budgets_bound' ($Raw[0].plan.budget.max_response_bytes -eq 80 -and $Raw[0].plan.budget.max_requests -eq 10)
$Tx = @($Raw | Where-Object event -CEQ 'request_tx')
$Rx = @($Raw | Where-Object event -CEQ 'response_rx')
Require 'bounded_read_only_zero_origin_requests' ($Tx.Count -ge 5 -and $Tx.Count -le 10 -and $Rx.Count -eq $Tx.Count -and @($Tx | Where-Object { $_.function_code -ne 3 -or $_.start_address -ne 0 }).Count -eq 0)
Require 'exact_terminal_accounting' ($Raw[-1].completed_tx_count -eq $Tx.Count -and $Raw[-1].attempted_write_bytes -eq (8*$Tx.Count) -and $Raw[-1].tx_count_known)
Require 'three_complete_77_byte_frames' (@($Comparison.frames | Where-Object { $_.count -eq 36 -and $_.rx_bytes -eq 77 -and @($_.registers).Count -eq 36 }).Count -eq 3)
Require 'comparison_artifact_bound_to_audit' ($Comparison.run_id -ceq $RunId -and $Comparison.audit_sha256 -ceq (Hash (Join-Path $Evidence 'physical.jsonl')))
Require 'comparison_covers_27_and_6_positions' (@($Comparison.groups[0].rows).Count -eq 27 -and @($Comparison.groups[1].rows).Count -eq 6 -and @($Comparison.full_block_repeatability).Count -eq 36)
Require 'formal_acceptance_not_falsely_passed' ($Comparison.formal_acceptance -ceq 'BLOCKED' -and -not $Comparison.physical_meanings_confirmed -and -not $Comparison.nonzero_address_issue_closed)
Require 'single_run_authorization_not_reused' ((Json 'single-run-dispatch.json').dispatch_once -and (Json 'single-run-dispatch.json').automatic_retry_of_run_forbidden)
Require 'temporary_signer_stopped' ((Json 'signer-lifecycle.json').stopped -and $null -eq (Get-Process -Id (Json 'signer-lifecycle.json').process_id -ErrorAction SilentlyContinue))
$Result = [ordered]@{
    observed_at=(Get-Date).ToString('o'); check_count=$Checks.Count; checks=$Checks; run_id=$RunId;
    physical_run='COMPLETED'; whole_block_frames='VALID'; formal_acceptance='BLOCKED';
    additional_physical_transmissions_authorized=$false
}
$Result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $Evidence 'completed-run-validation.json') -Encoding UTF8
[pscustomobject]$Result | Select-Object observed_at,check_count,physical_run,whole_block_frames,formal_acceptance | ConvertTo-Json -Compress
