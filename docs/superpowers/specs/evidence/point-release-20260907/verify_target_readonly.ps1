[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$DockerHost = 'npipe:////./pipe/docker_engine'
$Site = 'C:\Ruisheng\candidates\site-deploy-20260831.1'
$Candidate = 'C:\Ruisheng\candidates\deploy-20260907.1'
$State = Join-Path $Site '.remote-maintenance-state'
$Operation = '1c95b8c8-8f92-4e8b-962f-207800c7e9c9'
$Backup = Join-Path $Site ('backups\' + $Operation)

function Read-Json([string]$Path) {
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}
function Hash([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}
function Sql([string]$Database, [string]$Statement) {
    $Lines = @($Statement | & $Docker --host $DockerHost exec -i ruisheng-postgres psql -X -q -t -A -v ON_ERROR_STOP=1 -U ruisheng_admin -d $Database)
    if ($LASTEXITCODE -ne 0) { throw 'Read-only SQL check failed.' }
    return ($Lines -join '').Trim()
}
function Health([string]$Container, [string]$Module) {
    $Lines = @(& $Docker --host $DockerHost exec $Container python -m $Module)
    if ($LASTEXITCODE -ne 0) { throw 'Internal readiness check failed.' }
    return ($Lines -join '') | ConvertFrom-Json
}

$Active = Read-Json (Join-Path $State 'active-release.json')
$JournalPath = Join-Path $State ('full-upgrade-' + $Operation + '.json')
$Journal = Read-Json $JournalPath
if ($Active.candidate_id -cne 'deploy-20260907.1' -or
    $Active.source_commit -cne '2150b5ee904760ce0af4483c201009b8744669a2' -or
    $Active.operation_id -cne $Operation -or $Journal.status -cne 'committed') {
    throw 'Active release/journal mismatch.'
}
$Objects = (& $Docker --host $DockerHost inspect ruisheng-postgres ruisheng-redis ruisheng-api ruisheng-gw ruisheng-web) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Container inspection failed.' }
$Containers = @($Objects | ForEach-Object {
    [ordered]@{ name=$_.Name; id=$_.Id; image=$_.Image; started=$_.State.StartedAt;
        restart_count=$_.RestartCount; state=$_.State.Status; health=$_.State.Health.Status }
})
$Names = @(& $Docker --host $DockerHost ps -a --format '{{.Names}}')
if ($LASTEXITCODE -ne 0) { throw 'Container inventory failed.' }
$Diagnostics = @($Names | Where-Object { $_ -match 'point-pipeline|modbus-probe' })
$Locks = @(@((Join-Path $State '.remote-maintenance.lock'), (Join-Path $Site '.remote-hotfix.lock')) | ForEach-Object {
    [ordered]@{ path=$_; exists=(Test-Path -LiteralPath $_) }
})

# Compare complete environment text with only the updater's six fields masked.
# Neither the original values nor the environment contents are ever emitted.
$BeforeText = [IO.File]::ReadAllText((Join-Path $Backup '.env.prod.before'))
$AfterText = [IO.File]::ReadAllText((Join-Path $Site '.env.prod'))
$ChangedKeys = @()
$Allowed = @('TARGET_PLATFORM','POSTGRES_IMAGE','REDIS_IMAGE','API_IMAGE','GW_IMAGE','WEB_IMAGE')
foreach ($Key in $Allowed) {
    $Pattern = '(?m)^' + [regex]::Escape($Key) + '=[^\r\n]*(?=\r?$)'
    $BeforeMatch = [regex]::Matches($BeforeText, $Pattern)
    $AfterMatch = [regex]::Matches($AfterText, $Pattern)
    if ($BeforeMatch.Count -ne 1 -or $AfterMatch.Count -ne 1) { throw 'Unexpected environment key count.' }
    if ($BeforeMatch[0].Value -cne $AfterMatch[0].Value) { $ChangedKeys += $Key }
    $BeforeText = [regex]::Replace($BeforeText, $Pattern, ($Key + '=<release-field>'))
    $AfterText = [regex]::Replace($AfterText, $Pattern, ($Key + '=<release-field>'))
}
$EnvironmentSafe = $BeforeText -ceq $AfterText
$BeforeText = $null
$AfterText = $null

$Paths = @(
    (Join-Path $Site '.env.prod'), (Join-Path $State 'active-release.json'), $JournalPath,
    (Join-Path $Backup '.env.prod.before'), (Join-Path $Backup 'ruisheng.dump'), (Join-Path $Backup 'roles.sql'),
    (Join-Path $Candidate 'SHA256SUMS'), (Join-Path $Candidate 'SHA256SUMS.sig'),
    'C:\Ruisheng\site\serial-hardware.json', 'C:\Ruisheng\site\modbus-probe.json',
    'C:\Ruisheng\site\modbus-probe-extended-20260907.json',
    'C:\ProgramData\Ruisheng\entitlements\current.json',
    'C:\ProgramData\Ruisheng\receipts\modbus-probe-release.json',
    'C:\ProgramData\Ruisheng\trust\release-allowed-signers',
    'C:\ProgramData\Ruisheng\trust\release-key-fingerprint',
    'C:\Ruisheng\tools\probe_modbus_rtu.py', 'C:\Ruisheng\tools\run_modbus_probe.ps1',
    'C:\Ruisheng\audit\point-release-20260907-physical.jsonl',
    'C:\Ruisheng\audit\modbus-runner-3dec620b-2b08-4b4f-bc0c-b2c14482e92e.jsonl',
    'C:\Ruisheng\audit\modbus-runner-ba1ac2a2-f5da-42b9-b25f-aa3dfe088229.jsonl'
)
$Hashes = @($Paths | ForEach-Object { [ordered]@{ path=$_; sha256=(Hash $_) } })
$Fingerprint = ([IO.File]::ReadAllText('C:\ProgramData\Ruisheng\trust\release-key-fingerprint')).Trim()
$ActualKey = @(& C:\Windows\System32\OpenSSH\ssh-keygen.exe -l -E sha256 -f 'C:\ProgramData\Ruisheng\trust\release-allowed-signers')
if ($LASTEXITCODE -ne 0) { throw 'Trust key check failed.' }
$TrustMatches = $Fingerprint -ceq 'SHA256:Go/TiuSZ89zJvTCzVick7GT6gP6yrOK+CRdYMRp07Fk' -and
    ($ActualKey -join '') -match [regex]::Escape($Fingerprint)
$Sql = "SELECT json_build_object('users',(SELECT count(*) FROM users)," +
    "'devices',(SELECT count(*) FROM devices),'device_points',(SELECT count(*) FROM device_points)," +
    "'realtime',(SELECT count(*) FROM point_data_realtime),'history',(SELECT count(*) FROM point_data_history));"
$Counts = (Sql 'ruisheng' $Sql) | ConvertFrom-Json
$TestCounts = (Sql 'test_point_pipeline_20260907_d135806d' "SELECT json_build_object('realtime',(SELECT count(*) FROM point_data_realtime),'history',(SELECT count(*) FROM point_data_history));") | ConvertFrom-Json
$Api = Health 'ruisheng-api' 'ruisheng_api.healthcheck'
$Gw = Health 'ruisheng-gw' 'ruisheng_gw.healthcheck'
$Web = Invoke-WebRequest -Uri 'http://127.0.0.1/' -UseBasicParsing -TimeoutSec 5
$Result = [ordered]@{
    observed_at=(Get-Date).ToString('o'); computer=$env:COMPUTERNAME; active_release=$Active;
    containers=$Containers; diagnostic_containers=$Diagnostics; maintenance_locks=$Locks;
    environment_nonrelease_unchanged=$EnvironmentSafe; environment_changed_keys=$ChangedKeys;
    hashes=$Hashes; trust_key_matches=$TrustMatches; production_counts=$Counts;
    test_database='test_point_pipeline_20260907_d135806d'; test_counts=$TestCounts;
    database_head=(Sql 'ruisheng' 'SELECT version_num FROM alembic_version;');
    api=$Api; gw_internal=$Gw; web_http=[int]$Web.StatusCode
}
$Result | ConvertTo-Json -Depth 15 -Compress
if (-not $EnvironmentSafe -or -not $TrustMatches -or $Diagnostics.Count -ne 0 -or
    @($Locks | Where-Object exists).Count -ne 0) { exit 1 }
