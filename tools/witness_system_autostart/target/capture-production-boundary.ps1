$ErrorActionPreference = "Stop"
$expectedNames = @(
    "ruisheng-api", "ruisheng-gw", "ruisheng-postgres", "ruisheng-redis", "ruisheng-web"
)
$containers = @(docker.exe ps --no-trunc --format "{{.ID}}|{{.Names}}|{{.State}}")
if ($LASTEXITCODE -ne 0) { throw "docker ps failed" }
$byName = @{}
foreach ($line in $containers) {
    $parts = $line -split '\|', 3
    if ($parts.Count -ne 3) { throw "unexpected docker ps output" }
    if ($parts[1] -in $expectedNames) {
        if ($byName.ContainsKey($parts[1])) { throw "duplicate production container: $($parts[1])" }
        $byName[$parts[1]] = [pscustomobject]@{ id = $parts[0]; state = $parts[2] }
    }
}
$missing = @($expectedNames | Where-Object { -not $byName.ContainsKey($_) })
if ($missing.Count) { throw "production containers are missing: $($missing -join ', ')" }
$notRunning = @($expectedNames | Where-Object { [string]$byName[$_].state -cne "running" })
if ($notRunning.Count) { throw "production containers are not running: $($notRunning -join ', ')" }
$temporaryListeners = @(Get-NetTCPConnection -LocalPort 38477 -State Listen `
    -ErrorAction SilentlyContinue)
[pscustomobject][ordered]@{
    schema_version = 1
    artifact_type = "ruisheng.witness-system-autostart-production-boundary"
    captured_at = [DateTimeOffset]::UtcNow.ToString("o")
    container_ids = @($expectedNames | ForEach-Object { [string]$byName[$_].id })
    container_names = $expectedNames
    temporary_listener_count = $temporaryListeners.Count
} | ConvertTo-Json -Depth 5 -Compress
