[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$OutputEncoding = [Text.UTF8Encoding]::new($false)
$Repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../../../'))
$ReportPath = Join-Path $PSScriptRoot 'target-pipeline-results.json'
if (Test-Path -LiteralPath $ReportPath) { throw 'A retained report already exists; do not overwrite it.' }
$Candidates = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $PSScriptRoot '../point-pipeline-20260907/candidate-summary.json') | ConvertFrom-Json
$Audit = @(Get-Content -Encoding UTF8 -LiteralPath (Join-Path $PSScriptRoot 'point-release-20260907-physical.jsonl') | ForEach-Object { $_ | ConvertFrom-Json })
$Start = @($Audit | Where-Object event -eq 'run_started')
$End = @($Audit | Where-Object event -eq 'completed')
if ($Start.Count -ne 1 -or $End.Count -ne 1 -or $End[0].result -ne 'valid') { throw 'Physical evidence is incomplete.' }
$Samples = @($Audit | Where-Object { $_.event -eq 'response_rx' -and $_.classification -eq 'valid' } | ForEach-Object {
    $Response = $_
    $Request = @($Audit | Where-Object { $_.event -eq 'request_tx' -and $_.tx_number -eq $Response.tx_number })
    if ($Request.Count -ne 1 -or -not $Response.crc_valid) { throw 'Physical sample is not bound to one valid request.' }
    @{ start_address = $Request[0].start_address; registers = $Response.registers; rx_hex = $Response.rx_hex; captured_at = $Response.timestamp }
})
if ($Samples.Count -ne 3 -or $Candidates.Count -ne 46) { throw 'Unexpected evidence coverage.' }
$Sources = @{}
$SourceHashes = @{}
foreach ($Pair in @(
    @('ruisheng_gw.persistence.batch_writer', 'ruisheng-gw/src/ruisheng_gw/persistence/batch_writer.py'),
    @('ruisheng_gw.ingest', 'ruisheng-gw/src/ruisheng_gw/ingest.py')
)) {
    $Source = [IO.File]::ReadAllText((Join-Path $Repo $Pair[1]))
    $Sources[$Pair[0]] = $Source
    $Sha = [Security.Cryptography.SHA256]::Create()
    try { $SourceHashes[$Pair[0]] = ([BitConverter]::ToString($Sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Source)))).Replace('-', '').ToLowerInvariant() }
    finally { $Sha.Dispose() }
}
$Payload = @{
    script = [IO.File]::ReadAllText((Join-Path $PSScriptRoot '../point-pipeline-20260907/verify_pipeline.py'))
    candidates = $Candidates
    physical_samples = $Samples
    physical_run_id = $Start[0].run_id
    sources = $Sources
    source_hashes = $SourceHashes
    test_database = 'test_point_pipeline_20260907_' + [Guid]::NewGuid().ToString('N').Substring(0,8)
}
$PayloadBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($Payload | ConvertTo-Json -Depth 30 -Compress)))
$RemoteScript = @'
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$OutputEncoding = [Text.UTF8Encoding]::new($false)
$Docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$DockerHost = 'npipe:////./pipe/docker_engine'
$Payload = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PAYLOAD__')) | ConvertFrom-Json
$Database = $Payload.test_database
if ($Database -cnotmatch '^test_point_pipeline_20260907_[0-9a-f]{8}$') { throw 'Unsafe database name.' }
function Sql([string]$Db, [string]$Statement) {
    $Rows = @($Statement | & $Docker --host $DockerHost exec -i ruisheng-postgres psql -X -q -t -A -v ON_ERROR_STOP=1 -U ruisheng_admin -d $Db)
    if ($LASTEXITCODE -ne 0) { throw 'Target SQL operation failed.' }
    return $Rows
}
function Snapshot {
    $Objects = (& $Docker --host $DockerHost inspect ruisheng-postgres ruisheng-redis ruisheng-api ruisheng-gw ruisheng-web) | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Container snapshot failed.' }
    $Containers = @($Objects | ForEach-Object {
        @{ name = $_.Name; id = $_.Id; image = $_.Image; started = $_.State.StartedAt; restart_count = $_.RestartCount; state = $_.State.Status; health = $_.State.Health.Status }
    })
    $Hashes = @(@(
        'C:\Ruisheng\candidates\site-deploy-20260831.1\.env.prod',
        'C:\Ruisheng\candidates\site-deploy-20260831.1\.remote-maintenance-state\active-release.json',
        'C:\Ruisheng\site\serial-hardware.json',
        'C:\Ruisheng\site\modbus-probe.json',
        'C:\ProgramData\Ruisheng\entitlements\current.json'
    ) | ForEach-Object { @{ path = $_; sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $_).Hash } })
    $CountSql = "SELECT json_build_object('users',(SELECT count(*) FROM users)," +
        "'devices',(SELECT count(*) FROM devices),'device_points',(SELECT count(*) FROM device_points)," +
        "'realtime',(SELECT count(*) FROM point_data_realtime),'history',(SELECT count(*) FROM point_data_history));"
    $Counts = (Sql 'ruisheng' $CountSql) -join ''
    return @{ observed_at = (Get-Date).ToString('o'); containers = $Containers; hashes = $Hashes; counts = ($Counts | ConvertFrom-Json) }
}
$Before = Snapshot
$Gw = ((& $Docker --host $DockerHost inspect ruisheng-gw) | ConvertFrom-Json)[0]
$PinnedImage = 'sha256:becb5435ecff9449f7fe9d39e655d0d8d33c7cb14629a33247ed0a0b29c420b6'
if ($Gw.Image -cne $PinnedImage -or $Gw.State.Status -ne 'running') { throw 'Unexpected production GW state.' }
$SecretEntries = @($Gw.Config.Env | Where-Object { $_.StartsWith('GW_DATABASE_URL=') })
if ($SecretEntries.Count -ne 1) { throw 'Missing gateway database connection.' }
$Payload | Add-Member -NotePropertyName database_url -NotePropertyValue ($SecretEntries[0].Substring('GW_DATABASE_URL='.Length))
$Exists = (Sql 'postgres' "SELECT count(*) FROM pg_database WHERE datname = '$Database';") -join ''
if ($Exists.Trim() -ne '0') { throw 'Test database already exists; it will not be modified.' }
$null = Sql 'postgres' "CREATE DATABASE $Database OWNER ruisheng_gw;"
$null = Sql $Database 'CREATE EXTENSION IF NOT EXISTS timescaledb;'
$Reports = @()
$Failure = $null
try {
    foreach ($Mode in @('deployed')) {
        $Payload | Add-Member -NotePropertyName mode -NotePropertyValue $Mode -Force
        $InputJson = $Payload | ConvertTo-Json -Depth 30 -Compress
        $Python = "import json,sys; p=json.load(sys.stdin); s={'__name__':'point_pipeline_check'}; exec(compile(p.pop('script'),'pipeline-check','exec'),s); s['run'](p)"
        $ContainerName = "point-pipeline-$Mode-" + $Database.Substring($Database.Length - 8)
        $Output = @($InputJson | & $Docker --host $DockerHost run --rm -i --pull never --read-only --tmpfs /tmp:rw,size=16m --cap-drop ALL --security-opt no-new-privileges --memory 256m --cpus 0.5 --network container:ruisheng-gw --name $ContainerName $PinnedImage python -c $Python)
        if ($LASTEXITCODE -ne 0) {
            $Failure = @{ mode = $Mode; output = ($Output -join "`n"); exit_code = $LASTEXITCODE }
            break
        }
        $Reports += (($Output -join "`n") | ConvertFrom-Json)
    }
} finally {
    $After = Snapshot
    $Result = @{ database = $Database; production_before = $Before; production_after = $After; physical_run_id = $Payload.physical_run_id; reports = $Reports; failure = $Failure; test_database_retained = $true }
    Write-Output ('PIPELINE_JSON:' + ($Result | ConvertTo-Json -Depth 35 -Compress))
}
'@
$RemoteScript = $RemoteScript.Replace('__PAYLOAD__', $PayloadBase64)
$GeneratedPath = Join-Path $PSScriptRoot ('remote-experiment-' + $Payload.test_database + '.generated.ps1')
[IO.File]::WriteAllText($GeneratedPath, $RemoteScript, [Text.UTF8Encoding]::new($false))
$RemotePath = 'C:/Users/lenovo/AppData/Local/Temp/' + [IO.Path]::GetFileName($GeneratedPath)
& C:\Windows\System32\OpenSSH\scp.exe -F NUL -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 $GeneratedPath ('lenovo@100.109.90.21:' + $RemotePath)
if ($LASTEXITCODE -ne 0) { throw 'Diagnostic script transfer failed.' }
$Bootstrap = '$ProgressPreference="SilentlyContinue"; & ' + "'$RemotePath'"
$Encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Bootstrap))
$Lines = @(& C:\Windows\System32\OpenSSH\ssh.exe -n -T -F NUL -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=2 lenovo@100.109.90.21 powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand $Encoded)
$ExitCode = $LASTEXITCODE
$ResultLines = @($Lines | Where-Object { $_.StartsWith('PIPELINE_JSON:') })
if ($ResultLines.Count -ne 1) { throw "No target report received (SSH exit $ExitCode)." }
$Result = $ResultLines[0].Substring('PIPELINE_JSON:'.Length) | ConvertFrom-Json
[IO.File]::WriteAllText($ReportPath, ($Result | ConvertTo-Json -Depth 35), [Text.UTF8Encoding]::new($false))
Write-Output "Retained target report: $ReportPath"
Write-Output "Isolated database: $($Result.database)"
Write-Output "Completed variants: $($Result.reports.Count)"
if ($Result.failure -or $ExitCode -ne 0) { Write-Output ($Result.failure | ConvertTo-Json -Compress); exit 1 }
