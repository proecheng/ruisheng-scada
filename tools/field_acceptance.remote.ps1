# Invoked only through collect_field_acceptance.ps1 after the existing entitlement guard.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$dockerBase = @('--host', 'npipe:////./pipe/dockerDesktopLinuxEngine')
$site = 'C:\Ruisheng\candidates\site-deploy-20260831.1'
$activePath = Join-Path $site '.remote-maintenance-state\active-release.json'
$activeBytes = [IO.File]::ReadAllText($activePath)
$active = $activeBytes | ConvertFrom-Json
$releaseAt = [DateTimeOffset]::Parse($active.committed_at).ToString('o')
$infoRaw = & $docker @dockerBase inspect ruisheng-gw ruisheng-api ruisheng-postgres ruisheng-redis ruisheng-web
if ($LASTEXITCODE -ne 0) { throw 'service_inspection_failed' }
# Windows PowerShell 5.1 emits a JSON array as one pipeline object. Wrapping the
# conversion in @() would make five services appear as a single nested array.
$info = $infoRaw | ConvertFrom-Json
if ($info.Count -ne 5) { throw 'service_inspection_failed' }
$services = @($info | ForEach-Object {
    [ordered]@{name=$_.Name;id=$_.Id;image=$_.Image;status=$_.State.Status;started=$_.State.StartedAt;restarts=$_.RestartCount}
})
$query = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__SQL_BASE64__'))
# SQL travels over stdin so psql variable quoting is applied to the release timestamp.
$raw = $query | & $docker @dockerBase exec -i ruisheng-postgres psql -XqAt -v ON_ERROR_STOP=1 -v "release_at=$releaseAt" -U ruisheng_admin -d ruisheng
if ($LASTEXITCODE -ne 0) { throw 'read_only_statistics_failed' }
# PostgreSQL json_agg(record) can place newlines inside a single JSON value.
# Quiet psql omits command tags; preserve every line of its one result value.
$jsonText = $raw -join "`n"
$db = $jsonText | ConvertFrom-Json
if ($null -eq $db.observed_at -or $null -eq $db.settings) { throw 'statistics_result_invalid' }
# Bound the log window and line count. Emit only known event labels, never arbitrary messages.
$ErrorActionPreference = 'Continue'
$logLines = @(& $docker @dockerBase logs --since 48h --tail 4000 --timestamps ruisheng-gw 2>&1 | ForEach-Object { [string]$_ })
$logExit = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($logExit -ne 0) { throw 'gateway_log_read_failed' }
$events = @()
foreach ($line in $logLines) {
    if ($line -match '^(\d{4}-\d{2}-\d{2}T\S+)\s+.*?(serial response timeout|serial ingestion timeout|serial ingestion failed|serial exception|serial send did not complete|serial retry attempt|serial retry recovered|serial retry exhausted)') {
        $events += [ordered]@{at=$Matches[1];event=$Matches[2]}
    }
}
$unchanged = [IO.File]::ReadAllText($activePath) -ceq $activeBytes
[ordered]@{
    schema_version=1;at=[DateTimeOffset]::Now.ToString('o');target=$env:COMPUTERNAME;
    read_only_business_data=$true;active_release_unchanged=$unchanged;
    active=$active;services=$services;db=$db;
    logs=@{line_count=$logLines.Count;limit=4000;possibly_truncated=($logLines.Count -ge 4000);events=$events};
    maintenance_locks=@{shared=(Test-Path (Join-Path $site '.remote-maintenance-state\.remote-maintenance.lock'));legacy=(Test-Path (Join-Path $site '.remote-hotfix.lock'))}
} | ConvertTo-Json -Depth 20 -Compress
