[CmdletBinding()]
param(
  [Parameter(Position = 0)]
  [ValidateSet("Start", "Stop", "Status", "Health", "Logs")]
  [string]$Action = "Start",
  [string]$Target = "lenovo@100.109.90.21",
  [ValidateRange(1, 65535)]
  [int]$WebPort = 18080,
  [ValidateRange(1, 65535)]
  [int]$GwHealthPort = 19090,
  [ValidateRange(1, 65535)]
  [int]$GwDevicePort = 15020,
  [string]$StateDirectory = ""
)

$ErrorActionPreference = "Stop"

if (-not $StateDirectory) {
  $StateDirectory = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "Ruisheng\remote-debug"
}
$StateFile = Join-Path $StateDirectory "tunnel.json"
$StdoutLog = Join-Path $StateDirectory "ssh.stdout.log"
$StderrLog = Join-Path $StateDirectory "ssh.stderr.log"

function Test-TcpPort {
  param(
    [Parameter(Mandatory)]
    [string]$Address,
    [Parameter(Mandatory)]
    [int]$Port,
    [int]$TimeoutMilliseconds = 750
  )

  $client = [System.Net.Sockets.TcpClient]::new()
  try {
    $pending = $client.BeginConnect($Address, $Port, $null, $null)
    if (-not $pending.AsyncWaitHandle.WaitOne($TimeoutMilliseconds)) {
      return $false
    }
    $client.EndConnect($pending)
    return $true
  }
  catch {
    return $false
  }
  finally {
    $client.Dispose()
  }
}

function Read-TunnelState {
  if (-not (Test-Path -LiteralPath $StateFile -PathType Leaf)) {
    return $null
  }
  try {
    return Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
  }
  catch {
    throw "Tunnel state is invalid: $StateFile. Remove it after confirming no ssh tunnel is running."
  }
}

function Get-VerifiedTunnelProcess {
  param([Parameter(Mandatory)]$State)

  $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($State.pid)" -ErrorAction SilentlyContinue
  if ($null -eq $process) {
    return $null
  }
  $markers = @(
    "127.0.0.1:$($State.ports.web):127.0.0.1:80",
    "127.0.0.1:$($State.ports.gw_health):127.0.0.1:9090",
    "127.0.0.1:$($State.ports.gw_device):127.0.0.1:5020",
    [string]$State.target
  )
  if ($process.Name -notin @("ssh", "ssh.exe")) {
    return $null
  }
  foreach ($marker in $markers) {
    if ([string]$process.CommandLine -notlike "*$marker*") {
      return $null
    }
  }
  return $process
}

function Write-TunnelState {
  param([Parameter(Mandatory)]$State)

  New-Item -ItemType Directory -Path $StateDirectory -Force | Out-Null
  $temporary = "$StateFile.$PID.tmp"
  $State | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporary -Encoding utf8
  Move-Item -LiteralPath $temporary -Destination $StateFile -Force
}

function Show-TunnelStatus {
  param([switch]$FailWhenStopped)

  $state = Read-TunnelState
  if ($null -eq $state) {
    Write-Host "Remote debug tunnel: stopped"
    if ($FailWhenStopped) { throw "Remote debug tunnel is not running." }
    return
  }
  $process = Get-VerifiedTunnelProcess -State $state
  if ($null -eq $process) {
    Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
    Write-Host "Remote debug tunnel: stopped (stale state removed)"
    if ($FailWhenStopped) { throw "Remote debug tunnel is not running." }
    return
  }

  $checks = [ordered]@{
    web       = Test-TcpPort -Address "127.0.0.1" -Port ([int]$state.ports.web)
    gw_health = Test-TcpPort -Address "127.0.0.1" -Port ([int]$state.ports.gw_health)
    gw_device = Test-TcpPort -Address "127.0.0.1" -Port ([int]$state.ports.gw_device)
  }
  Write-Host "Remote debug tunnel: running (PID $($state.pid), target $($state.target))"
  Write-Host "Web UI:    http://127.0.0.1:$($state.ports.web)"
  Write-Host "API debug: http://127.0.0.1:$($state.ports.web)/api/meta/version"
  Write-Host "GW ops:    http://127.0.0.1:$($state.ports.gw_health) (site ACL still applies)"
  Write-Host "GW TCP:    127.0.0.1:$($state.ports.gw_device)"
  Write-Host "Forward checks: web=$($checks.web), gw-health=$($checks.gw_health), gw-device=$($checks.gw_device)"
  if ($checks.Values -contains $false) {
    throw "The ssh process is running, but one or more forwarded ports are unavailable. Run Logs."
  }
}

function Start-Tunnel {
  if ($Target -notmatch '^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$') {
    throw "Target must use the user@host form without whitespace."
  }
  if (@(@($WebPort, $GwHealthPort, $GwDevicePort) | Sort-Object -Unique).Count -ne 3) {
    throw "WebPort, GwHealthPort, and GwDevicePort must be distinct."
  }

  $existing = Read-TunnelState
  if ($null -ne $existing) {
    $process = Get-VerifiedTunnelProcess -State $existing
    if ($null -ne $process) {
      $sameConfiguration = (
        $existing.target -eq $Target -and
        [int]$existing.ports.web -eq $WebPort -and
        [int]$existing.ports.gw_health -eq $GwHealthPort -and
        [int]$existing.ports.gw_device -eq $GwDevicePort
      )
      if (-not $sameConfiguration) {
        throw "A tunnel is already running with different settings. Stop it before changing settings."
      }
      Show-TunnelStatus -FailWhenStopped
      return
    }
    Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
  }

  foreach ($port in @($WebPort, $GwHealthPort, $GwDevicePort)) {
    if (Test-TcpPort -Address "127.0.0.1" -Port $port -TimeoutMilliseconds 250) {
      throw "Local port $port is already in use. Choose another port."
    }
  }

  $ssh = Get-Command ssh.exe -ErrorAction Stop
  New-Item -ItemType Directory -Path $StateDirectory -Force | Out-Null
  Set-Content -LiteralPath $StdoutLog -Value "" -Encoding utf8
  Set-Content -LiteralPath $StderrLog -Value "" -Encoding utf8
  $sshArguments = @(
    "-N", "-T",
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=3",
    "-L", "127.0.0.1:${WebPort}:127.0.0.1:80",
    "-L", "127.0.0.1:${GwHealthPort}:127.0.0.1:9090",
    "-L", "127.0.0.1:${GwDevicePort}:127.0.0.1:5020",
    $Target
  )
  $process = Start-Process -FilePath $ssh.Source -ArgumentList $sshArguments -PassThru `
    -WindowStyle Hidden -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog
  $state = [ordered]@{
    schema_version = 1
    pid            = $process.Id
    target         = $Target
    started_at     = [DateTimeOffset]::Now.ToString("o")
    ports          = [ordered]@{
      web       = $WebPort
      gw_health = $GwHealthPort
      gw_device = $GwDevicePort
    }
    stdout_log     = $StdoutLog
    stderr_log     = $StderrLog
  }

  try {
    Write-TunnelState -State $state
    $deadline = [DateTime]::UtcNow.AddSeconds(12)
    do {
      Start-Sleep -Milliseconds 300
      $process.Refresh()
      if ($process.HasExited) {
        $details = (Get-Content -LiteralPath $StderrLog -Raw -ErrorAction SilentlyContinue).Trim()
        throw "ssh tunnel exited with code $($process.ExitCode): $details"
      }
      $ready = (
        (Test-TcpPort -Address "127.0.0.1" -Port $WebPort) -and
        (Test-TcpPort -Address "127.0.0.1" -Port $GwHealthPort) -and
        (Test-TcpPort -Address "127.0.0.1" -Port $GwDevicePort)
      )
    } while (-not $ready -and [DateTime]::UtcNow -lt $deadline)
    if (-not $ready) {
      throw "Timed out waiting for all forwarded ports."
    }
    Show-TunnelStatus -FailWhenStopped
  }
  catch {
    if (-not $process.HasExited) {
      Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
    throw
  }
}

function Stop-Tunnel {
  $state = Read-TunnelState
  if ($null -eq $state) {
    Write-Host "Remote debug tunnel is already stopped."
    return
  }
  $process = Get-VerifiedTunnelProcess -State $state
  if ($null -ne $process) {
    Stop-Process -Id ([int]$state.pid) -Force
    Wait-Process -Id ([int]$state.pid) -Timeout 5 -ErrorAction SilentlyContinue
  }
  Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
  Write-Host "Remote debug tunnel stopped."
}

function Test-RemoteHealth {
  if ($Target -notmatch '^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$') {
    throw "Target must use the user@host form without whitespace."
  }
  $remoteScript = @'
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
docker exec ruisheng-api python -m ruisheng_api.healthcheck
if ($LASTEXITCODE -ne 0) { throw "API health check failed." }
docker exec ruisheng-gw python -c "import urllib.request; print('gw=' + str(urllib.request.urlopen('http://127.0.0.1:9090/ready', timeout=5).status))"
if ($LASTEXITCODE -ne 0) { throw "GW health check failed." }
$web = Invoke-WebRequest -Uri "http://127.0.0.1/" -UseBasicParsing -TimeoutSec 5
Write-Output "web=$([int]$web.StatusCode)"
'@
  $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($remoteScript))
  & ssh.exe -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 `
    $Target powershell.exe `
    -NoLogo -NoProfile -NonInteractive -EncodedCommand $encoded
  if ($LASTEXITCODE -ne 0) { throw "Remote service health check failed." }
}

switch ($Action) {
  "Start" { Start-Tunnel }
  "Stop" { Stop-Tunnel }
  "Status" { Show-TunnelStatus }
  "Health" { Test-RemoteHealth }
  "Logs" {
    Write-Host "stdout: $StdoutLog"
    Write-Host "stderr: $StderrLog"
    if (Test-Path -LiteralPath $StdoutLog) { Get-Content -LiteralPath $StdoutLog }
    if (Test-Path -LiteralPath $StderrLog) { Get-Content -LiteralPath $StderrLog }
  }
}
