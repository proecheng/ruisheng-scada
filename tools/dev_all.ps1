param(
  [switch]$SkipDocker,
  [switch]$SkipSeed
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

if (-not $SkipDocker) {
  docker compose -f docker-compose.dev.yml up -d --wait
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$setupArgs = @("-Migrate", "-NoStart")
if (-not $SkipSeed) { $setupArgs += "-Seed" }
& (Join-Path $PSScriptRoot "dev_api.ps1") @setupArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$apiJob = Start-Job -Name "ruisheng-api" -ScriptBlock {
  param($scriptPath)
  & $scriptPath
} -ArgumentList (Join-Path $PSScriptRoot "dev_api.ps1")

$webJob = Start-Job -Name "ruisheng-web" -ScriptBlock {
  param($scriptPath)
  & $scriptPath
} -ArgumentList (Join-Path $PSScriptRoot "dev_web.ps1")

Write-Host "API: http://127.0.0.1:8000/api/health/ready"
Write-Host "Web: http://127.0.0.1:5173"
Write-Host "Press Ctrl+C to stop both jobs."

try {
  while ($apiJob.State -eq "Running" -and $webJob.State -eq "Running") {
    Receive-Job -Job $apiJob, $webJob
    Start-Sleep -Seconds 1
  }
  Receive-Job -Job $apiJob, $webJob
}
finally {
  Stop-Job -Job $apiJob, $webJob -ErrorAction SilentlyContinue
  Remove-Job -Job $apiJob, $webJob -Force -ErrorAction SilentlyContinue
}
