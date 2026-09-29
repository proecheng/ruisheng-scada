[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Checks = [Collections.Generic.List[object]]::new()
function Read-Json([string]$Name) {
    return Get-Content -LiteralPath (Join-Path $PSScriptRoot $Name) -Raw -Encoding UTF8 | ConvertFrom-Json
}
function Check([string]$Name, [bool]$Pass) {
    $Checks.Add([ordered]@{ name=$Name; pass=$Pass })
}
function Equal-Json($Left, $Right) {
    return ($Left | ConvertTo-Json -Depth 16 -Compress) -ceq ($Right | ConvertTo-Json -Depth 16 -Compress)
}
$Result = Read-Json 'target-pipeline-results.json'
$Final = Read-Json 'target-final-state.json'
$Build = Read-Json 'release-build.json'
$Journal = Read-Json 'full-upgrade-1c95b8c8-8f92-4e8b-962f-207800c7e9c9.json'
$Candidates = @(Read-Json '../point-pipeline-20260907/candidate-summary.json')
$Prior = Read-Json '../point-pipeline-20260907/target-pipeline-results.json'
$Manifest = Get-Content -LiteralPath 'C:\ProgramData\Ruisheng\publisher-output\deploy-20260907.1\MANIFEST.json' -Raw -Encoding UTF8 | ConvertFrom-Json
$Report = $Result.reports[0]
Check 'one actual deployed-code report, no interpreter patches' ($Result.reports.Count -eq 1 -and $Report.mode -ceq 'deployed' -and $null -eq $Result.failure)
Check 'isolated retained database identity' ($Result.database -cmatch '^test_point_pipeline_20260907_[0-9a-f]{8}$' -and $Result.database -ceq $Report.database -and $Result.database -ceq $Final.test_database -and $Result.test_database_retained)
Check 'Timescale history hypertable exists' $Report.hypertable
Check '36 exact physical realtime/history readbacks' ($Report.physical_replay.Count -eq 36 -and @($Report.physical_replay | Where-Object { -not $_.realtime_pass -or -not $_.history_pass }).Count -eq 0)
Check '36 distinct requested addresses 0..35' ((@($Report.physical_replay | Select-Object -ExpandProperty address | Sort-Object -Unique) -join ',') -ceq ((0..35) -join ','))
Check 'physical row/event counts' ($Report.physical_counts.realtime -eq 36 -and $Report.physical_counts.history -eq 36 -and $Report.physical_counts.publisher_events -eq 36)
$Words = @($Report.points | Where-Object fc -eq 3)
$Coils = @($Report.points | Where-Object fc -eq 1)
Check 'all 42 synthetic address/scaling tests' ($Words.Count -eq 42 -and @($Words | Where-Object { -not $_.realtime_pass -or -not $_.history_pass -or $_.realtime_count -ne 1 -or $_.history_count -ne 2 -or $_.publisher_events -ne 2 }).Count -eq 0)
Check 'synthetic realtime carries latest history timestamp' (@($Words | Where-Object { $_.realtime[0].recorded_at -cne $_.history[1].recorded_at -or $_.history[0].recorded_at -ge $_.history[1].recorded_at }).Count -eq 0)
Check 'all 46 candidate IDs accounted for once' ($Report.points.Count -eq 46 -and @($Report.points.id | Sort-Object -Unique).Count -eq 46 -and (Equal-Json @($Report.points.id | Sort-Object) @($Candidates.id | Sort-Object)))
Check 'all four ambiguous coil configurations rejected' ($Coils.Count -eq 4 -and @($Coils | Where-Object { -not $_.rejection_pass -or $_.realtime_count -ne 0 -or $_.history_count -ne 0 -or $_.publisher_events -ne 0 }).Count -eq 0)
Check 'all 42 unsupported legacy types rejected' ($Report.legacy_types.Count -eq 42 -and @($Report.legacy_types | Where-Object { -not $_.rejection_pass }).Count -eq 0 -and $Report.legacy_counts.realtime -eq 0 -and $Report.legacy_counts.history -eq 0)
foreach ($Case in @('wrong-function','bad-crc')) {
    Check ($Case + ' no database rows') ($Report.$Case.rejection_pass -and $Report.$Case.realtime_count -eq 0 -and $Report.$Case.history_count -eq 0)
}
foreach ($Case in @('shutdown-race','shutdown-drain','transaction-rollback')) {
    Check $Case $Report.$Case.pass
}
Check 'retained SQL totals independently rechecked' ($Report.database_counts.realtime -eq 126 -and $Report.database_counts.history -eq 168 -and (Equal-Json $Report.database_counts $Final.test_counts))
$ExpectedSource = @{
    'ruisheng_gw.ingest'='23592de114389bf1586dee66ee735df9f4a6af9d48f38237141ab74721ae472f'
    'ruisheng_gw.persistence.batch_writer'='381a9e0786ec18eb34447750ff7e94aff009dc49d9718cb345859edba0e59468'
}
foreach ($Module in $ExpectedSource.Keys) {
    Check ('running tested source ' + $Module) ($Report.source_hashes.$Module.deployed -ceq $ExpectedSource[$Module])
}
Check 'production containers unchanged during database experiment' (Equal-Json $Result.production_before.containers $Result.production_after.containers)
Check 'protected hashes unchanged during database experiment' (Equal-Json $Result.production_before.hashes $Result.production_after.hashes)
foreach ($Counts in @($Prior.production_before.counts,$Result.production_before.counts,$Result.production_after.counts,$Final.production_counts)) {
    Check 'production users/devices/points/telemetry remain zero' (@($Counts.PSObject.Properties.Value | Where-Object { $_ -ne 0 }).Count -eq 0)
}
foreach ($Container in $Final.containers) {
    $Expected = @($Manifest.images | Where-Object { ('/ruisheng-' + $_.component) -ceq $Container.name })
    $ObservedBefore = @($Result.production_before.containers | Where-Object name -eq $Container.name)
    Check ('final image/running/start identity ' + $Container.name) ($Expected.Count -eq 1 -and $Container.image -ceq $Expected[0].image_id -and $Container.state -ceq 'running' -and $Container.restart_count -eq 0 -and $Container.id -ceq $ObservedBefore[0].id -and $Container.started -ceq $ObservedBefore[0].started)
}
Check 'live API/GW readiness and Web 200' ($Final.api.status -ceq 'ready' -and $Final.gw_internal.status -ceq 'ready' -and $Final.web_http -eq 200)
Check 'no diagnostic containers or maintenance leases remain' ($Final.diagnostic_containers.Count -eq 0 -and @($Final.maintenance_locks | Where-Object exists).Count -eq 0)
Check 'committed active release/source/identity agree' ($Journal.status -ceq 'committed' -and $Final.active_release.candidate_id -ceq $Build.candidate_id -and $Final.active_release.source_commit -ceq $Build.source_commit -and $Final.active_release.logical_identity -ceq $Build.logical_identity -and $Manifest.logical_identity -ceq $Build.logical_identity)
Check 'schema head unchanged' ($Final.database_head -ceq $Build.alembic_head -and $Final.database_head -ceq $Journal.backup.database_head)
Check 'only allowed release environment fields changed' ($Final.environment_nonrelease_unchanged -and @($Final.environment_changed_keys | Where-Object { $_ -notin @('TARGET_PLATFORM','POSTGRES_IMAGE','REDIS_IMAGE','API_IMAGE','GW_IMAGE','WEB_IMAGE') }).Count -eq 0)
Check 'release trust key unchanged' $Final.trust_key_matches
$Copies = [Collections.Generic.List[object]]::new()
foreach ($Entry in $Final.hashes) {
    $Name = [IO.Path]::GetFileName($Entry.path)
    $Local = Join-Path $PSScriptRoot $Name
    if (Test-Path -LiteralPath $Local -PathType Leaf) {
        $Hash = (Get-FileHash -LiteralPath $Local -Algorithm SHA256).Hash.ToLowerInvariant()
        Check ('exact target/local evidence copy ' + $Name) ($Hash -ceq $Entry.sha256)
        $Copies.Add([ordered]@{ file=$Name; sha256=$Hash })
    }
}
Check 'all six retained target audit/config/journal copies verified' ($Copies.Count -eq 6)
foreach ($Backup in @($Journal.environment_backup,$Journal.backup.database,$Journal.backup.roles)) {
    $Observed = @($Final.hashes | Where-Object path -eq $Backup.path)
    Check ('backup hash matches committed receipt ' + [IO.Path]::GetFileName($Backup.path)) ($Observed.Count -eq 1 -and $Observed[0].sha256 -ceq $Backup.sha256)
}
foreach ($Entry in @($Prior.production_before.hashes | Where-Object { $_.path -match 'serial-hardware.json$|modbus-probe.json$|entitlements\\current.json$' })) {
    $Observed = @($Final.hashes | Where-Object path -eq $Entry.path)
    Check ('predeployment protected state unchanged ' + [IO.Path]::GetFileName($Entry.path)) ($Observed.Count -eq 1 -and $Observed[0].sha256 -ieq $Entry.sha256)
}
foreach ($Pair in @(@('SHA256SUMS','sha256sums_sha256'),@('SHA256SUMS.sig','signature_sha256'),@('probe_modbus_rtu.py','probe_sha256'),@('run_modbus_probe.ps1','runner_sha256'))) {
    $Observed = @($Final.hashes | Where-Object { [IO.Path]::GetFileName($_.path) -ceq $Pair[0] })
    Check ('deployed signed artifact identity ' + $Pair[0]) ($Observed.Count -eq 1 -and $Observed[0].sha256 -ceq $Build.($Pair[1]))
}
$Audit = @(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'point-release-20260907-physical.jsonl') -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json })
$Starts = @($Audit | Where-Object event -eq run_started)
$Requests = @($Audit | Where-Object event -eq request_tx)
$Responses = @($Audit | Where-Object event -eq response_rx)
$Completed = @($Audit | Where-Object event -eq completed)
Check 'physical run binds exact profile and deployed image' ($Starts.Count -eq 1 -and $Starts[0].run_id -ceq $Result.physical_run_id -and $Starts[0].approval_scope -ceq 'b09-9600-8n1-unit1-fc3-r0-5-r6-26-r27-35' -and $Starts[0].image_id -ceq $Build.gw_image_id)
Check 'exact three approved read frames, zero retries/writes' ($Requests.Count -eq 3 -and ($Requests.tx_hex -join ',') -ceq '010300000006c5c8,0103000600156404,0103001b0009f5cb' -and @($Requests | Where-Object { $_.function_code -ne 3 -or $_.attempt -ne 0 }).Count -eq 0)
Check 'physical audit completes with valid CRC responses' ($Responses.Count -eq 3 -and @($Responses | Where-Object { -not $_.crc_valid -or $_.classification -cne 'valid' }).Count -eq 0 -and $Completed.Count -eq 1 -and $Completed[0].result -ceq 'valid' -and $Completed[0].completed_tx_count -eq 3)
$RawMatch = $true
foreach ($Response in $Responses) {
    $Request = @($Requests | Where-Object tx_number -eq $Response.tx_number)
    if ($Request.Count -ne 1 -or $Request[0].register_count -ne $Response.registers.Count) { $RawMatch = $false; continue }
    for ($Index=0; $Index -lt $Response.registers.Count; $Index++) {
        $Address = $Request[0].start_address + $Index
        $Row = @($Report.physical_replay | Where-Object address -eq $Address)
        if ($Row.Count -ne 1 -or $Row[0].raw -ne $Response.registers[$Index]) { $RawMatch = $false }
    }
}
Check 'all physical database values bound to captured request/response pairs' $RawMatch
$Failures = @($Checks | Where-Object { -not $_.pass })
$Summary = [ordered]@{ validated_at=(Get-Date).ToString('o'); passed=$Checks.Count-$Failures.Count;
    failed=$Failures.Count; checks=@($Checks.ToArray()); exact_target_copies=@($Copies.ToArray());
    production_acceptance_complete=$false; meaning_scaling_signedness_confirmed=$false;
    fc1_physical_tested=$false; continuous_production_polling_enabled=$false }
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'validation-summary.json'),($Summary | ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))
Write-Output ('Evidence checks: ' + $Summary.passed + ' passed, ' + $Summary.failed + ' failed')
if ($Failures.Count) { $Failures | ConvertTo-Json -Depth 5; exit 1 }
