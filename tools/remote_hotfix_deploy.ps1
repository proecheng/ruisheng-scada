[CmdletBinding()]
param(
  [Parameter(Mandatory, Position = 0)]
  [ValidateSet("api", "gw", "web")]
  [string]$Service,
  [string]$Target = "lenovo@100.109.90.21",
  [string]$CandidateRoot = "C:\Ruisheng\candidates\deploy-20260821.1",
  [string]$SiteRoot = "C:\Ruisheng\candidates\site",
  [string]$RemoteHotfixRoot = "C:\Ruisheng\hotfix",
  [string]$Platform = "linux/amd64",
  [switch]$DryRun,
  [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$HotfixOperationId = [Guid]::NewGuid().ToString("D")

$ServiceConfiguration = @{
  api = @{
    env_key        = "API_IMAGE"
    dockerfile     = "ruisheng-api/Dockerfile"
    context        = "."
    health_url     = "http://127.0.0.1:8000/api/health/ready"
    container_name = "ruisheng-api"
  }
  gw = @{
    env_key        = "GW_IMAGE"
    dockerfile     = "ruisheng-gw/Dockerfile"
    context        = "."
    health_url     = "http://127.0.0.1:9090/ready"
    container_name = "ruisheng-gw"
  }
  web = @{
    env_key        = "WEB_IMAGE"
    dockerfile     = "Dockerfile"
    context        = "ruisheng-web"
    health_url     = "http://127.0.0.1/"
    container_name = "ruisheng-web"
  }
}
$Configuration = $ServiceConfiguration[$Service]

function Assert-Command {
  param([Parameter(Mandatory)][string]$Name)
  if ($null -eq (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "Required command was not found: $Name"
  }
}

function Invoke-NativeText {
  param(
    [Parameter(Mandatory)][string]$FilePath,
    [Parameter(Mandatory)][string[]]$ArgumentList,
    [string]$WorkingDirectory = $RepositoryRoot
  )

  Push-Location $WorkingDirectory
  try {
    $output = & $FilePath @ArgumentList 2>&1
    $exitCode = $LASTEXITCODE
  }
  finally {
    Pop-Location
  }
  $text = (($output | ForEach-Object { "$_" }) -join [Environment]::NewLine).Trim()
  if ($exitCode -ne 0) {
    throw "$FilePath failed with exit code ${exitCode}: $text"
  }
  return $text
}

function Invoke-NativeLive {
  param(
    [Parameter(Mandatory)][string]$FilePath,
    [Parameter(Mandatory)][string[]]$ArgumentList,
    [string]$WorkingDirectory = $RepositoryRoot
  )

  Push-Location $WorkingDirectory
  try {
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
      throw "$FilePath failed with exit code $LASTEXITCODE"
    }
  }
  finally {
    Pop-Location
  }
}

function ConvertTo-PowerShellLiteral {
  param([Parameter(Mandatory)][string]$Value)
  return "'" + $Value.Replace("'", "''") + "'"
}

function Invoke-RemotePowerShell {
  param([Parameter(Mandatory)][string]$Script)

  $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Script))
  return Invoke-NativeText -FilePath "ssh.exe" -ArgumentList @(
    "-T",
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=3",
    $Target,
    "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", $encoded
  )
}

function Assert-RemoteMaintenanceLocksAvailable {
  $stateDirectory = Join-Path $SiteRoot ".remote-maintenance-state"
  $sharedLock = Join-Path $stateDirectory ".remote-maintenance.lock"
  $legacyLock = Join-Path $SiteRoot ".remote-hotfix.lock"
  $script = @"
`$ErrorActionPreference = 'Stop'
`$stateDirectory = $(ConvertTo-PowerShellLiteral $stateDirectory)
`$sharedLock = $(ConvertTo-PowerShellLiteral $sharedLock)
`$legacyLock = $(ConvertTo-PowerShellLiteral $legacyLock)
if (-not (Test-Path -LiteralPath `$stateDirectory -PathType Container)) {
  throw 'Maintenance security preparation is required before hotfix deployment.'
}
`$allowed = @{}
@([Security.Principal.WindowsIdentity]::GetCurrent().User.Value, 'S-1-5-18', 'S-1-5-32-544') |
  Select-Object -Unique | ForEach-Object { `$allowed[`$_] = `$true }
`$siteAcl = Get-Acl -LiteralPath $(ConvertTo-PowerShellLiteral $SiteRoot)
if (-not `$siteAcl.AreAccessRulesProtected) {
  throw 'Maintenance security preparation is required before hotfix deployment.'
}
foreach (`$rule in @(`$siteAcl.Access)) {
  `$sid = `$rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
  if (`$rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
      -not `$allowed.ContainsKey(`$sid)) {
    throw 'Maintenance security preparation is required before hotfix deployment.'
  }
}
if (Test-Path -LiteralPath `$sharedLock -PathType Leaf) {
  throw 'Another maintenance or hotfix operation is active, or its shared lock requires inspection.'
}
if (Test-Path -LiteralPath `$legacyLock -PathType Leaf) {
  throw 'Another maintenance or hotfix operation is active, or its legacy lock requires inspection.'
}
'available'
"@
  [void](Invoke-RemotePowerShell -Script $script)
}

function Start-RemoteHotfixReservation {
  $template = @'
$ErrorActionPreference = "Stop"
$SiteRoot = __SITE_ROOT__
$StateDirectory = Join-Path $SiteRoot ".remote-maintenance-state"
$SharedLockPath = Join-Path $StateDirectory ".remote-maintenance.lock"
$LegacyLockPath = Join-Path $SiteRoot ".remote-hotfix.lock"
$OperationId = __OPERATION_ID__
$Action = __ACTION__
$ProcessStartedAt = (Get-Process -Id $PID -ErrorAction Stop).StartTime.ToUniversalTime().ToString("o")
$Acquired = New-Object System.Collections.ArrayList

function Acquire-Lock {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Name)
  $acquiredAt = [DateTimeOffset]::UtcNow
  $record = [ordered]@{
    schema_version=1; lock_name=$Name; operation_id=$OperationId; action=$Action;
    pid=$PID; process_started_at=$ProcessStartedAt; target=[string]$env:COMPUTERNAME;
    acquired_at=$acquiredAt.ToString("o"); expires_at=$acquiredAt.AddMinutes(5).ToString("o")
  }
  $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(
    ($record | ConvertTo-Json -Depth 4 -Compress)
  )
  $stream = $null
  try {
    $stream = [IO.File]::Open(
      $Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None
    )
    $stream.Write($bytes, 0, $bytes.Length)
    $stream.Flush()
    $stream.Dispose()
    $stream = $null
    [void]$Acquired.Add($Path)
  }
  catch {
    if ($null -ne $stream) { $stream.Dispose() }
    throw "hotfix_lock_conflict"
  }
}

function Renew-Locks {
  foreach ($path in @($Acquired)) {
    $temporary = "$path.$PID.$([Guid]::NewGuid().ToString('N')).renew.tmp"
    $backup = "$path.$PID.$([Guid]::NewGuid().ToString('N')).renew.bak"
    try {
      $record = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
      if (
        [int]$record.schema_version -ne 1 -or
        [string]$record.operation_id -ne $OperationId -or
        [string]$record.action -ne $Action -or
        [int]$record.pid -ne $PID -or
        [string]$record.process_started_at -ne $ProcessStartedAt
      ) { throw "hotfix_lock_ownership_lost" }
      $record.expires_at = [DateTimeOffset]::UtcNow.AddMinutes(5).ToString("o")
      [IO.File]::WriteAllText(
        $temporary, ($record | ConvertTo-Json -Depth 4 -Compress),
        (New-Object Text.UTF8Encoding($false))
      )
      [IO.File]::Replace($temporary, $path, $backup)
    }
    finally {
      Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
      Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
    }
  }
}

function Release-Locks {
  foreach ($path in @($Acquired)) {
    try {
      $record = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
      if (
        [string]$record.operation_id -eq $OperationId -and
        [string]$record.action -eq $Action -and
        [string]$record.phase -eq "deployment" -and
        [int]$record.pid -ne $PID
      ) {
        return
      }
    }
    catch { }
  }
  $paths = @($Acquired)
  [array]::Reverse($paths)
  foreach ($path in $paths) {
    try {
      $record = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
      if (
        [string]$record.operation_id -eq $OperationId -and
        [string]$record.action -eq $Action -and
        [int]$record.pid -eq $PID -and
        [string]$record.process_started_at -eq $ProcessStartedAt
      ) {
        Remove-Item -LiteralPath $path -Force
      }
    }
    catch { }
  }
}

try {
  if (-not (Test-Path -LiteralPath $StateDirectory -PathType Container)) {
    throw "maintenance_security_preparation_required"
  }
  Acquire-Lock -Path $SharedLockPath -Name "shared-maintenance"
  Acquire-Lock -Path $LegacyLockPath -Name "legacy-hotfix"
  [ordered]@{ ok=$true; operation_id=$OperationId; action=$Action } | ConvertTo-Json -Compress
  [Console]::Out.Flush()
  $deadline = [DateTimeOffset]::UtcNow.AddHours(6)
  while ([DateTimeOffset]::UtcNow -lt $deadline) {
    Start-Sleep -Seconds 30
    Renew-Locks
  }
  throw "hotfix_reservation_timeout"
}
finally { Release-Locks }
'@
  $script = $template.Replace("__SITE_ROOT__", (ConvertTo-PowerShellLiteral $SiteRoot))
  $script = $script.Replace(
    "__OPERATION_ID__", (ConvertTo-PowerShellLiteral $HotfixOperationId)
  )
  $script = $script.Replace("__ACTION__", (ConvertTo-PowerShellLiteral "hotfix-$Service"))
  $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
  if ($encoded.Length -gt 24000) { throw "Hotfix reservation command is too large." }
  $startInfo = New-Object Diagnostics.ProcessStartInfo
  $startInfo.FileName = "ssh.exe"
  $startInfo.Arguments = @(
    "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
    "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=3", $Target, "powershell.exe", "-NoLogo",
    "-NoProfile", "-NonInteractive", "-EncodedCommand", $encoded
  ) -join " "
  $startInfo.UseShellExecute = $false
  $startInfo.CreateNoWindow = $true
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  $process = New-Object Diagnostics.Process
  $process.StartInfo = $startInfo
  try {
    if (-not $process.Start()) { throw "Failed to start the hotfix reservation session." }
    $readyTask = $process.StandardOutput.ReadLineAsync()
    if (-not $readyTask.Wait(20000)) { throw "Timed out waiting for the hotfix reservation." }
    $readyLine = [string]$readyTask.Result
    if (-not $readyLine) { throw "The hotfix reservation ended without confirmation." }
    $result = $readyLine | ConvertFrom-Json
    if (-not [bool]$result.ok -or [string]$result.operation_id -ne $HotfixOperationId) {
      throw "Target did not confirm the hotfix lock reservation."
    }
    return $process
  }
  catch {
    if (-not $process.HasExited) { try { $process.Kill() } catch { } }
    try { $process.WaitForExit() } catch { }
    $process.Dispose()
    throw
  }
}

function Assert-RemoteHotfixReservationAlive {
  param([Parameter(Mandatory)][Diagnostics.Process]$Process)
  if ($Process.HasExited) { throw "The remote hotfix lock reservation was lost." }
}

function Stop-RemoteHotfixReservation {
  param([Diagnostics.Process]$Process)
  if ($null -eq $Process) { return }
  try {
    if (-not $Process.HasExited) { $Process.Kill() }
    $Process.WaitForExit()
  }
  catch { }
  finally { $Process.Dispose() }
}

function Release-RemoteHotfixReservation {
  $stateDirectory = Join-Path $SiteRoot ".remote-maintenance-state"
  $paths = @(
    (Join-Path $SiteRoot ".remote-hotfix.lock"),
    (Join-Path $stateDirectory ".remote-maintenance.lock")
  )
  $template = @'
$ErrorActionPreference = "Stop"
$OperationId = __OPERATION_ID__
$Action = __ACTION__
$Paths = @(__LOCK_PATHS__)
foreach ($path in $Paths) {
  try {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
    $record = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$record.operation_id -eq $OperationId -and [string]$record.action -eq $Action) {
      Remove-Item -LiteralPath $path -Force
    }
  }
  catch { }
}
foreach ($path in $Paths) {
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
  try { $record = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json }
  catch { continue }
  if ([string]$record.operation_id -eq $OperationId -and [string]$record.action -eq $Action) {
    throw "hotfix_lock_release_failed"
  }
}
'released'
'@
  $script = $template.Replace(
    "__OPERATION_ID__", (ConvertTo-PowerShellLiteral $HotfixOperationId)
  )
  $script = $script.Replace("__ACTION__", (ConvertTo-PowerShellLiteral "hotfix-$Service"))
  $lockLiterals = @($paths | ForEach-Object { ConvertTo-PowerShellLiteral $_ }) -join ","
  $script = $script.Replace("__LOCK_PATHS__", $lockLiterals)
  [void](Invoke-RemotePowerShell -Script $script)
}

function Get-RemotePreflight {
  $template = @'
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$CandidateRoot = __CANDIDATE_ROOT__
$SiteRoot = __SITE_ROOT__
$Service = __SERVICE__
$EnvironmentKey = __ENVIRONMENT_KEY__

$ComposeFile = Join-Path $CandidateRoot "docker-compose.prod.yml"
$OverrideFile = Join-Path $CandidateRoot "site-network.override.yml"
$CandidateManifest = Join-Path $CandidateRoot "MANIFEST.json"
$EnvFile = Join-Path $SiteRoot ".env.prod"
foreach ($path in @($ComposeFile, $OverrideFile, $CandidateManifest, $EnvFile)) {
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw "Required deployment file is missing: $path"
  }
}

$manifest = Get-Content -LiteralPath $CandidateManifest -Raw | ConvertFrom-Json
if ([string]$manifest.source_commit -notmatch '^[0-9a-f]{40}$') {
  throw "Candidate manifest has an invalid source commit."
}
$targetPlatform = "$($manifest.target_os)/$($manifest.target_architecture)"

$matchingLines = @(
  [IO.File]::ReadAllLines($EnvFile) | Where-Object { $_ -match "^$([regex]::Escape($EnvironmentKey))=" }
)
if ($matchingLines.Count -ne 1) {
  throw "Expected exactly one $EnvironmentKey entry in the site environment file."
}
$currentImage = ($matchingLines[0] -split "=", 2)[1]
if ([string]::IsNullOrWhiteSpace($currentImage)) {
  throw "$EnvironmentKey is empty."
}

$composeArguments = @(
  "compose", "-f", $ComposeFile, "-f", $OverrideFile,
  "--env-file", $EnvFile, "config", "--format", "json"
)
$rendered = & docker @composeArguments 2>&1
if ($LASTEXITCODE -ne 0) { throw "Existing production Compose configuration does not render." }
$model = (($rendered | ForEach-Object { "$_" }) -join [Environment]::NewLine) | ConvertFrom-Json
$serviceModel = $model.services.PSObject.Properties[$Service].Value
if ($null -eq $serviceModel) { throw "Service is absent from the rendered Compose model: $Service" }
if ([string]$serviceModel.image -ne $currentImage) { throw "Rendered service image differs from the site environment file." }
if ([string]$serviceModel.pull_policy -ne "never") { throw "Offline service must retain pull_policy=never." }
foreach ($port in @($serviceModel.ports)) {
  if ($null -ne $port -and [int]$port.published -gt 0 -and [string]$port.host_ip -notin @("127.0.0.1", "::1")) {
    throw "Rendered service publishes a non-loopback port."
  }
}

$dockerPlatform = (& docker info --format '{{.OSType}}/{{.Architecture}}').Trim()
if ($LASTEXITCODE -ne 0) { throw "Docker is not available on the target computer." }
if ($dockerPlatform -eq "linux/x86_64") { $dockerPlatform = "linux/amd64" }

[ordered]@{
  source_commit   = [string]$manifest.source_commit
  target_platform = $targetPlatform
  docker_platform = $dockerPlatform
  current_image   = $currentImage
} | ConvertTo-Json -Compress
'@
  $remoteScript = $template
  $remoteScript = $remoteScript.Replace("__CANDIDATE_ROOT__", (ConvertTo-PowerShellLiteral $CandidateRoot))
  $remoteScript = $remoteScript.Replace("__SITE_ROOT__", (ConvertTo-PowerShellLiteral $SiteRoot))
  $remoteScript = $remoteScript.Replace("__SERVICE__", (ConvertTo-PowerShellLiteral $Service))
  $remoteScript = $remoteScript.Replace(
    "__ENVIRONMENT_KEY__", (ConvertTo-PowerShellLiteral ([string]$Configuration.env_key))
  )
  $result = Invoke-RemotePowerShell -Script $remoteScript
  try {
    return $result | ConvertFrom-Json
  }
  catch {
    throw "Target preflight returned invalid data: $result"
  }
}

function Invoke-ServiceTests {
  if ($SkipTests) {
    Write-Warning "Local tests were explicitly skipped."
    return
  }
  Write-Host "Running local tests for $Service..."
  switch ($Service) {
    "api" {
      Invoke-NativeLive -FilePath "uv" -ArgumentList @(
        "run", "pytest", "-x", "ruisheng-shared/tests", "ruisheng-api/tests/unit"
      )
      Invoke-NativeLive -FilePath "uv" -ArgumentList @(
        "run", "ruff", "check", "ruisheng-shared", "ruisheng-api"
      )
    }
    "gw" {
      Invoke-NativeLive -FilePath "uv" -ArgumentList @(
        "run", "pytest", "-x", "ruisheng-shared/tests", "ruisheng-gw/tests/unit",
        "ruisheng-gw/tests/property"
      )
      Invoke-NativeLive -FilePath "uv" -ArgumentList @(
        "run", "ruff", "check", "ruisheng-shared", "ruisheng-gw"
      )
    }
    "web" {
      Invoke-NativeLive -FilePath "pnpm" -ArgumentList @("test") `
        -WorkingDirectory (Join-Path $RepositoryRoot "ruisheng-web")
      Invoke-NativeLive -FilePath "pnpm" -ArgumentList @("lint") `
        -WorkingDirectory (Join-Path $RepositoryRoot "ruisheng-web")
      Invoke-NativeLive -FilePath "pnpm" -ArgumentList @("build") `
        -WorkingDirectory (Join-Path $RepositoryRoot "ruisheng-web")
    }
  }
}

function Compress-TarArchive {
  param(
    [Parameter(Mandatory)][string]$TarPath,
    [Parameter(Mandatory)][string]$ArchivePath
  )

  $inputStream = $null
  $outputStream = $null
  $gzipStream = $null
  try {
    $inputStream = [IO.File]::OpenRead($TarPath)
    $outputStream = [IO.File]::Create($ArchivePath)
    $gzipStream = [IO.Compression.GZipStream]::new(
      $outputStream, [IO.Compression.CompressionMode]::Compress
    )
    $inputStream.CopyTo($gzipStream)
  }
  finally {
    if ($null -ne $gzipStream) { $gzipStream.Dispose() }
    if ($null -ne $outputStream) { $outputStream.Dispose() }
    if ($null -ne $inputStream) { $inputStream.Dispose() }
  }
}

function New-HotfixArtifact {
  param(
    [Parameter(Mandatory)][string]$Commit,
    [Parameter(Mandatory)][string]$ShortCommit
  )

  $imageReference = "ruisheng-hotfix/${Service}:$ShortCommit"
  & docker.exe image inspect $imageReference *> $null
  if ($LASTEXITCODE -eq 0) {
    throw "Immutable hotfix image tag already exists locally: $imageReference"
  }

  $dockerfile = Join-Path $RepositoryRoot ([string]$Configuration.dockerfile)
  $context = Join-Path $RepositoryRoot ([string]$Configuration.context)
  Invoke-NativeLive -FilePath "docker.exe" -ArgumentList @(
    "build", "--pull", "--platform", $Platform,
    "--label", "org.opencontainers.image.revision=$Commit",
    "--label", "com.ruisheng.hotfix.service=$Service",
    "--tag", $imageReference,
    "--file", $dockerfile,
    $context
  )

  $inspectText = Invoke-NativeText -FilePath "docker.exe" -ArgumentList @(
    "image", "inspect", $imageReference, "--format", "{{json .}}"
  )
  $inspect = $inspectText | ConvertFrom-Json
  $expectedParts = $Platform.Split("/", 2)
  if ([string]$inspect.Os -ne $expectedParts[0] -or [string]$inspect.Architecture -ne $expectedParts[1]) {
    throw "Built image platform mismatch: expected $Platform, got $($inspect.Os)/$($inspect.Architecture)"
  }
  if (@($inspect.RepoTags) -notcontains $imageReference) {
    throw "Built image does not contain its immutable tag."
  }
  if ([string]$inspect.Config.Labels.'org.opencontainers.image.revision' -ne $Commit) {
    throw "Built image revision label does not match the source commit."
  }

  $outputDirectory = Join-Path $RepositoryRoot "dist\hotfix\$ShortCommit\$Service"
  if (Test-Path -LiteralPath $outputDirectory) {
    throw "Hotfix output directory already exists: $outputDirectory"
  }
  New-Item -ItemType Directory -Path $outputDirectory | Out-Null
  $baseName = "ruisheng-$Service-hotfix-$ShortCommit"
  $tarPath = Join-Path $outputDirectory "$baseName.tar"
  $archivePath = "$tarPath.gz"
  $manifestPath = Join-Path $outputDirectory "$baseName.json"

  try {
    Invoke-NativeLive -FilePath "docker.exe" -ArgumentList @(
      "image", "save", "--output", $tarPath, $imageReference
    )
    Compress-TarArchive -TarPath $tarPath -ArchivePath $archivePath
  }
  finally {
    Remove-Item -LiteralPath $tarPath -Force -ErrorAction SilentlyContinue
  }

  $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
  $manifest = [ordered]@{
    schema_version  = 1
    service         = $Service
    source_commit   = $Commit
    image_reference = $imageReference
    image_id        = [string]$inspect.Id
    os              = [string]$inspect.Os
    architecture    = [string]$inspect.Architecture
    archive         = [IO.Path]::GetFileName($archivePath)
    sha256           = $archiveHash
    generated_at     = [DateTimeOffset]::UtcNow.ToString("o")
  }
  $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding utf8
  return [pscustomobject]@{
    ImageReference = $imageReference
    ImageId        = [string]$inspect.Id
    ArchivePath    = $archivePath
    ManifestPath   = $manifestPath
    Manifest       = $manifest
  }
}

function Send-HotfixArtifact {
  param(
    [Parameter(Mandatory)]$Artifact,
    [Parameter(Mandatory)][string]$ShortCommit
  )

  $remoteDirectory = Join-Path (Join-Path $RemoteHotfixRoot $ShortCommit) $Service
  $prepareTemplate = @'
$ErrorActionPreference = "Stop"
$directory = __REMOTE_DIRECTORY__
$archive = Join-Path $directory __ARCHIVE_NAME__
$manifest = Join-Path $directory __MANIFEST_NAME__
New-Item -ItemType Directory -Path $directory -Force | Out-Null
if ((Test-Path -LiteralPath $archive) -or (Test-Path -LiteralPath $manifest)) {
  throw "Remote hotfix artifact already exists; refusing to overwrite it."
}
'@
  $prepareScript = $prepareTemplate
  $prepareScript = $prepareScript.Replace(
    "__REMOTE_DIRECTORY__", (ConvertTo-PowerShellLiteral $remoteDirectory)
  )
  $prepareScript = $prepareScript.Replace(
    "__ARCHIVE_NAME__", (ConvertTo-PowerShellLiteral ([IO.Path]::GetFileName($Artifact.ArchivePath)))
  )
  $prepareScript = $prepareScript.Replace(
    "__MANIFEST_NAME__", (ConvertTo-PowerShellLiteral ([IO.Path]::GetFileName($Artifact.ManifestPath)))
  )
  Invoke-RemotePowerShell -Script $prepareScript | Out-Null

  $scpDirectory = $remoteDirectory.Replace("\", "/")
  Invoke-NativeLive -FilePath "scp.exe" -ArgumentList @(
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "ConnectTimeout=10",
    $Artifact.ArchivePath,
    $Artifact.ManifestPath,
    "${Target}:$scpDirectory/"
  )
  return $remoteDirectory
}

function Invoke-RemoteDeployment {
  param(
    [Parameter(Mandatory)]$Artifact,
    [Parameter(Mandatory)][string]$RemoteDirectory
  )

  $deploymentTemplate = @'
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$CandidateRoot = __CANDIDATE_ROOT__
$SiteRoot = __SITE_ROOT__
$RemoteDirectory = __REMOTE_DIRECTORY__
$Service = __SERVICE__
$EnvironmentKey = __ENVIRONMENT_KEY__
$ExpectedCommit = __EXPECTED_COMMIT__
$ExpectedImage = __EXPECTED_IMAGE__
$ExpectedPlatform = __EXPECTED_PLATFORM__
$ArchiveName = __ARCHIVE_NAME__
$ManifestName = __MANIFEST_NAME__
$ContainerName = __CONTAINER_NAME__
$HealthUrl = __HEALTH_URL__

$ComposeFile = Join-Path $CandidateRoot "docker-compose.prod.yml"
$OverrideFile = Join-Path $CandidateRoot "site-network.override.yml"
$EnvFile = Join-Path $SiteRoot ".env.prod"
$ArchivePath = Join-Path $RemoteDirectory $ArchiveName
$ManifestPath = Join-Path $RemoteDirectory $ManifestName
$composeBase = @(
  "compose", "-f", $ComposeFile, "-f", $OverrideFile, "--env-file", $EnvFile
)

function Invoke-Docker {
  param([Parameter(Mandatory)][string[]]$Arguments, [switch]$Capture)
  $resolvedDocker = Get-Command docker.exe -ErrorAction SilentlyContinue
  if ($null -eq $resolvedDocker) { $resolvedDocker = Get-Command docker -ErrorAction Stop }
  $startInfo = New-Object Diagnostics.ProcessStartInfo
  $startInfo.FileName = $resolvedDocker.Source
  $startInfo.UseShellExecute = $false
  $startInfo.CreateNoWindow = $true
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  $quotedArguments = foreach ($argument in $Arguments) {
    if ($argument -notmatch '[\s"]') { $argument; continue }
    $escaped = [regex]::Replace($argument, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    '"' + $escaped + '"'
  }
  $startInfo.Arguments = $quotedArguments -join " "
  $process = New-Object Diagnostics.Process
  $process.StartInfo = $startInfo
  try {
    Maintain-TransitionLocks -Force
    if (-not $process.Start()) { throw "Docker process did not start." }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    while (-not $process.WaitForExit(1000)) { Maintain-TransitionLocks }
    $process.WaitForExit()
    Maintain-TransitionLocks -Force
    $text = (($stdoutTask.Result, $stderrTask.Result) -join [Environment]::NewLine).Trim()
    if ($process.ExitCode -ne 0) {
      throw "Docker command failed with exit code $($process.ExitCode)."
    }
    if ($Capture) { return $text }
  }
  catch {
    if (-not $process.HasExited) { try { $process.Kill() } catch { } }
    throw
  }
  finally {
    if (-not $process.HasExited) { try { $process.Kill() } catch { } }
    $process.Dispose()
  }
}

function Get-LeaseGuardedFileSha256 {
  param([Parameter(Mandatory)][string]$Path)
  $stream = $null
  $sha = $null
  try {
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    $buffer = New-Object byte[] (1024 * 1024)
    Maintain-TransitionLocks -Force
    while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
      [void]$sha.TransformBlock($buffer, 0, $read, $buffer, 0)
      Maintain-TransitionLocks
    }
    [void]$sha.TransformFinalBlock($buffer, 0, 0)
    Maintain-TransitionLocks -Force
    return ([BitConverter]::ToString($sha.Hash)).Replace("-", "").ToLowerInvariant()
  }
  finally {
    if ($null -ne $sha) { $sha.Dispose() }
    if ($null -ne $stream) { $stream.Dispose() }
  }
}

function Get-ComposeModel {
  $rendered = Invoke-Docker -Arguments ($composeBase + @("config", "--format", "json")) -Capture
  return $rendered | ConvertFrom-Json
}

function Assert-ServicePolicy {
  param([Parameter(Mandatory)]$Model, [Parameter(Mandatory)][string]$Image)
  $serviceModel = $Model.services.PSObject.Properties[$Service].Value
  if ($null -eq $serviceModel) { throw "Service is absent from rendered Compose: $Service" }
  if ([string]$serviceModel.image -ne $Image) { throw "Rendered image does not match the hotfix image." }
  if ([string]$serviceModel.platform -ne $ExpectedPlatform) { throw "Rendered platform changed unexpectedly." }
  if ([string]$serviceModel.pull_policy -ne "never") { throw "Offline pull policy changed unexpectedly." }
  foreach ($port in @($serviceModel.ports)) {
    if ($null -ne $port -and [int]$port.published -gt 0 -and [string]$port.host_ip -notin @("127.0.0.1", "::1")) {
      throw "Hotfix would publish a non-loopback port."
    }
  }
}

function Test-ServiceReady {
  $stateText = Invoke-Docker -Arguments @("inspect", "--format", "{{json .State}}", $ContainerName) -Capture
  $state = $stateText | ConvertFrom-Json
  if (-not [bool]$state.Running) { return $false }
  if ($null -ne $state.Health -and [string]$state.Health.Status -ne "healthy") { return $false }
  try {
    if ($Service -eq "web") {
      Invoke-Docker -Arguments @("exec", $ContainerName, "wget", "-q", "-O", "/dev/null", $HealthUrl)
    }
    else {
      $pythonProbe = "import urllib.request; urllib.request.urlopen('$HealthUrl', timeout=5).read(1)"
      Invoke-Docker -Arguments @("exec", $ContainerName, "python", "-c", $pythonProbe)
    }
    return $true
  }
  catch {
    if ($_.Exception.Message -eq "deployment_lock_unavailable") { throw }
    return $false
  }
}

function Wait-ServiceReady {
  param([int]$TimeoutSeconds = 90)
  $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
  do {
    if (Test-ServiceReady) { return }
    Start-Sleep -Seconds 2
  } while ([DateTime]::UtcNow -lt $deadline)
  throw "Service did not become ready within $TimeoutSeconds seconds: $Service"
}

foreach ($path in @($ComposeFile, $OverrideFile, $EnvFile, $ArchivePath, $ManifestPath)) {
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required file is missing: $path" }
}
$maintenanceStateDirectory = Join-Path $SiteRoot ".remote-maintenance-state"
$sharedLockPath = Join-Path $maintenanceStateDirectory ".remote-maintenance.lock"
$legacyLockPath = Join-Path $SiteRoot ".remote-hotfix.lock"
$hotfixOperationId = __OPERATION_ID__
$processStartedAt = (Get-Process -Id $PID -ErrorAction Stop).StartTime.ToUniversalTime().ToString("o")
$acquiredLocks = New-Object System.Collections.ArrayList
$lockLeaseMinutes = 5
$nextLockCheckAt = [DateTimeOffset]::MinValue
$nextLockRenewalAt = [DateTimeOffset]::MinValue

function Read-ReservedLock {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Name)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "The reserved $Name lock was lost before deployment."
  }
  try {
    $record = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
      [int]$record.schema_version -ne 1 -or
      [string]$record.lock_name -ne $Name -or
      [string]$record.operation_id -ne $hotfixOperationId -or
      [string]$record.action -ne "hotfix-$Service" -or
      [DateTimeOffset]::Parse([string]$record.expires_at) -le [DateTimeOffset]::UtcNow
    ) { throw "invalid" }
    return $record
  }
  catch {
    throw "Another maintenance or hotfix operation is active, or its $Name lock requires inspection."
  }
}

function Write-TransitionLockAtomic {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Record)
  $temporary = "$Path.$PID.$([Guid]::NewGuid().ToString('N')).transition.tmp"
  $backup = "$Path.$PID.$([Guid]::NewGuid().ToString('N')).transition.bak"
  try {
    [IO.File]::WriteAllText(
      $temporary, ($Record | ConvertTo-Json -Depth 4 -Compress),
      (New-Object Text.UTF8Encoding($false))
    )
    [IO.File]::Replace($temporary, $Path, $backup)
  }
  finally {
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
  }
}

function Acquire-TransitionLock {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$Name,
    [Parameter(Mandatory)][int]$ReservedPid,
    [Parameter(Mandatory)][string]$ReservedProcessStartedAt
  )
  $record = Read-ReservedLock -Path $Path -Name $Name
  if (
    [int]$record.pid -ne $ReservedPid -or
    [string]$record.process_started_at -ne $ReservedProcessStartedAt
  ) {
    throw "The reserved $Name lock changed during deployment handoff."
  }
  $record.pid = $PID
  $record.process_started_at = $processStartedAt
  $record.expires_at = [DateTimeOffset]::UtcNow.AddMinutes($lockLeaseMinutes).ToString("o")
  $record | Add-Member -NotePropertyName phase -NotePropertyValue "deployment" -Force
  Write-TransitionLockAtomic -Path $Path -Record $record
  [void]$acquiredLocks.Add($Path)
}

function Assert-TransitionLocksOwned {
  foreach ($path in @($acquiredLocks)) {
    $record = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
      [int]$record.schema_version -ne 1 -or
      [string]$record.operation_id -ne $hotfixOperationId -or
      [string]$record.action -ne "hotfix-$Service" -or
      [int]$record.pid -ne $PID -or
      [string]$record.process_started_at -ne $processStartedAt -or
      [string]$record.phase -ne "deployment" -or
      [DateTimeOffset]::Parse([string]$record.expires_at) -le [DateTimeOffset]::UtcNow
    ) {
      throw "The deployment lock ownership was lost."
    }
  }
}

function Renew-TransitionLocks {
  foreach ($path in @($acquiredLocks)) {
    $record = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
      [int]$record.schema_version -ne 1 -or
      [string]$record.operation_id -ne $hotfixOperationId -or
      [string]$record.action -ne "hotfix-$Service" -or
      [int]$record.pid -ne $PID -or
      [string]$record.process_started_at -ne $processStartedAt -or
      [string]$record.phase -ne "deployment"
    ) {
      throw "The deployment lock ownership was lost."
    }
    $record.expires_at = [DateTimeOffset]::UtcNow.AddMinutes($lockLeaseMinutes).ToString("o")
    Write-TransitionLockAtomic -Path $path -Record $record
  }
}

function Maintain-TransitionLocks {
  param([switch]$Force)
  try {
    $now = [DateTimeOffset]::UtcNow
    if (-not $Force -and $now -lt $nextLockCheckAt) { return }
    Assert-TransitionLocksOwned
    if ($Force -or $now -ge $nextLockRenewalAt) {
      Renew-TransitionLocks
      $script:nextLockRenewalAt = $now.AddSeconds(30)
    }
    $script:nextLockCheckAt = $now.AddSeconds(1)
  }
  catch {
    throw "deployment_lock_unavailable"
  }
}

function Release-TransitionLocks {
  $paths = @($acquiredLocks)
  [array]::Reverse($paths)
  foreach ($path in $paths) {
    try {
      $record = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
      if (
        [string]$record.operation_id -eq $hotfixOperationId -and
        [string]$record.action -eq "hotfix-$Service" -and
        [int]$record.pid -eq $PID -and
        [string]$record.process_started_at -eq $processStartedAt
      ) {
        Remove-Item -LiteralPath $path -Force
      }
    }
    catch { }
  }
}

if (-not (Test-Path -LiteralPath $maintenanceStateDirectory -PathType Container)) {
  throw "Maintenance security preparation is required before hotfix deployment."
}
$sharedReservation = Read-ReservedLock -Path $sharedLockPath -Name "shared-maintenance"
$legacyReservation = Read-ReservedLock -Path $legacyLockPath -Name "legacy-hotfix"
if (
  [int]$sharedReservation.pid -ne [int]$legacyReservation.pid -or
  [string]$sharedReservation.process_started_at -ne [string]$legacyReservation.process_started_at
) {
  throw "The reserved lock owners do not match."
}
$reservedPid = [int]$sharedReservation.pid
$reservedProcessStartedAt = [string]$sharedReservation.process_started_at
$reservedOwner = Get-Process -Id $reservedPid -ErrorAction Stop
$reservedOwnerStartedAt = $reservedOwner.StartTime.ToUniversalTime()
$reservedRecordStartedAt = [DateTimeOffset]::Parse($reservedProcessStartedAt).UtcDateTime
if ([Math]::Abs(($reservedOwnerStartedAt - $reservedRecordStartedAt).TotalSeconds) -ge 1) {
  throw "The reserved lock process identity is invalid."
}
Acquire-TransitionLock -Path $sharedLockPath -Name "shared-maintenance" `
  -ReservedPid $reservedPid -ReservedProcessStartedAt $reservedProcessStartedAt
try {
  Acquire-TransitionLock -Path $legacyLockPath -Name "legacy-hotfix" `
    -ReservedPid $reservedPid -ReservedProcessStartedAt $reservedProcessStartedAt
}
catch {
  Release-TransitionLocks
  throw
}
try {
$nextLockCheckAt = [DateTimeOffset]::MinValue
$nextLockRenewalAt = [DateTimeOffset]::MinValue
Maintain-TransitionLocks -Force
$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ([int]$manifest.schema_version -ne 1 -or [string]$manifest.service -ne $Service) {
  throw "Hotfix manifest identity is invalid."
}
if ([string]$manifest.source_commit -ne $ExpectedCommit -or [string]$manifest.image_reference -ne $ExpectedImage) {
  throw "Hotfix manifest source identity does not match the requested deployment."
}
if ([string]$manifest.archive -ne $ArchiveName) { throw "Hotfix manifest archive name mismatch." }
$actualHash = Get-LeaseGuardedFileSha256 -Path $ArchivePath
if ($actualHash -ne [string]$manifest.sha256) { throw "Hotfix archive SHA-256 mismatch." }

Invoke-Docker -Arguments @("image", "load", "--input", $ArchivePath)
$inspectText = Invoke-Docker -Arguments @("image", "inspect", $ExpectedImage, "--format", "{{json .}}") -Capture
$inspect = $inspectText | ConvertFrom-Json
if ([string]$inspect.Id -ne [string]$manifest.image_id) { throw "Loaded image ID does not match the manifest." }
if ("$($inspect.Os)/$($inspect.Architecture)" -ne $ExpectedPlatform) { throw "Loaded image platform mismatch." }
if (@($inspect.RepoTags) -notcontains $ExpectedImage) { throw "Loaded image is missing its immutable tag." }
if ([string]$inspect.Config.Labels.'org.opencontainers.image.revision' -ne $ExpectedCommit) {
  throw "Loaded image revision label mismatch."
}

$lines = [IO.File]::ReadAllLines($EnvFile)
$matchingIndexes = @(for ($index = 0; $index -lt $lines.Length; $index++) {
  if ($lines[$index] -match "^$([regex]::Escape($EnvironmentKey))=") { $index }
})
if ($matchingIndexes.Count -ne 1) { throw "Expected exactly one $EnvironmentKey entry." }
$entryIndex = [int]$matchingIndexes[0]
$oldImage = ($lines[$entryIndex] -split "=", 2)[1]
if ([string]::IsNullOrWhiteSpace($oldImage)) { throw "Existing image reference is empty." }
if ($oldImage -eq $ExpectedImage) { throw "The requested hotfix image is already configured." }

$timestamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$backupPath = "$EnvFile.pre-hotfix-$Service-$timestamp.bak"
$temporaryEnv = "$EnvFile.$PID.tmp"
$environmentChanged = $false
try {
  Maintain-TransitionLocks -Force
  $lines[$entryIndex] = "$EnvironmentKey=$ExpectedImage"
  [IO.File]::WriteAllLines($temporaryEnv, $lines, [Text.UTF8Encoding]::new($false))
  Maintain-TransitionLocks -Force
  [IO.File]::Replace($temporaryEnv, $EnvFile, $backupPath)
  $environmentChanged = $true

  $model = Get-ComposeModel
  Assert-ServicePolicy -Model $model -Image $ExpectedImage
  Invoke-Docker -Arguments ($composeBase + @("up", "-d", "--no-deps", "--force-recreate", $Service))
  Wait-ServiceReady

  [ordered]@{
    status       = "deployed"
    service      = $Service
    image        = $ExpectedImage
    previous     = $oldImage
    env_backup   = $backupPath
    health_url   = $HealthUrl
  } | ConvertTo-Json -Compress
}
catch {
  $deploymentError = $_.Exception.Message
  Remove-Item -LiteralPath $temporaryEnv -Force -ErrorAction SilentlyContinue
  if (-not $environmentChanged) { throw }
  if ($deploymentError -eq "deployment_lock_unavailable") {
    throw "Deployment lock was lost after mutation; no unlocked rollback was attempted. Inspect the target before retrying."
  }
  try {
    Maintain-TransitionLocks -Force
    $rollbackTemp = "$EnvFile.$PID.rollback.tmp"
    Copy-Item -LiteralPath $backupPath -Destination $rollbackTemp
    $rollbackBackup = "$EnvFile.rollback-replace-$Service-$timestamp.bak"
    try {
      Maintain-TransitionLocks -Force
      [IO.File]::Replace($rollbackTemp, $EnvFile, $rollbackBackup)
    }
    finally {
      Remove-Item -LiteralPath $rollbackBackup -Force -ErrorAction SilentlyContinue
      Remove-Item -LiteralPath $rollbackTemp -Force -ErrorAction SilentlyContinue
    }
    $rollbackModel = Get-ComposeModel
    Assert-ServicePolicy -Model $rollbackModel -Image $oldImage
    Invoke-Docker -Arguments ($composeBase + @("up", "-d", "--no-deps", "--force-recreate", $Service))
    Wait-ServiceReady
  }
  catch {
    throw "Deployment failed: $deploymentError Rollback also failed: $($_.Exception.Message)"
  }
  throw "Deployment failed and the previous image was restored: $deploymentError"
}
}
finally {
  Release-TransitionLocks
}
'@
  $remoteScript = $deploymentTemplate
  $replacements = [ordered]@{
    "__CANDIDATE_ROOT__"   = $CandidateRoot
    "__SITE_ROOT__"        = $SiteRoot
    "__OPERATION_ID__"     = $HotfixOperationId
    "__REMOTE_DIRECTORY__" = $RemoteDirectory
    "__SERVICE__"          = $Service
    "__ENVIRONMENT_KEY__"  = [string]$Configuration.env_key
    "__EXPECTED_COMMIT__"  = [string]$Artifact.Manifest.source_commit
    "__EXPECTED_IMAGE__"   = [string]$Artifact.ImageReference
    "__EXPECTED_PLATFORM__" = $Platform
    "__ARCHIVE_NAME__"     = [IO.Path]::GetFileName($Artifact.ArchivePath)
    "__MANIFEST_NAME__"    = [IO.Path]::GetFileName($Artifact.ManifestPath)
    "__CONTAINER_NAME__"   = [string]$Configuration.container_name
    "__HEALTH_URL__"       = [string]$Configuration.health_url
  }
  foreach ($replacement in $replacements.GetEnumerator()) {
    $remoteScript = $remoteScript.Replace(
      [string]$replacement.Key, (ConvertTo-PowerShellLiteral ([string]$replacement.Value))
    )
  }
  return Invoke-RemotePowerShell -Script $remoteScript
}

if ($Target -notmatch '^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$') {
  throw "Target must use the user@host form without whitespace."
}
if ($Platform -notmatch '^linux/(amd64|arm64)$') {
  throw "Platform must be linux/amd64 or linux/arm64."
}
foreach ($remotePath in @($CandidateRoot, $SiteRoot, $RemoteHotfixRoot)) {
  if ($remotePath -notmatch '^[A-Za-z]:\\[^\r\n]*$') {
    throw "Remote paths must be absolute Windows paths."
  }
}
foreach ($command in @("git.exe", "docker.exe", "ssh.exe", "scp.exe")) {
  Assert-Command -Name $command
}
if (-not $SkipTests) {
  if ($Service -eq "web") { Assert-Command -Name "pnpm" } else { Assert-Command -Name "uv" }
}

$commit = Invoke-NativeText -FilePath "git.exe" -ArgumentList @("rev-parse", "HEAD")
if ($commit -notmatch '^[0-9a-f]{40}$') { throw "HEAD is not a full Git commit." }
$shortCommit = $commit.Substring(0, 12)
$worktree = Invoke-NativeText -FilePath "git.exe" -ArgumentList @(
  "status", "--porcelain", "--untracked-files=all"
)
if ($worktree) { throw "The worktree must be clean before building an immutable hotfix." }

Write-Host "Checking target deployment..."
$reservationHeld = $false
$reservationProcess = $null
if ($DryRun) { Assert-RemoteMaintenanceLocksAvailable }
else {
  Assert-RemoteMaintenanceLocksAvailable
  $reservationProcess = Start-RemoteHotfixReservation
  $reservationHeld = $true
  Write-Host "Reserved maintenance locks: operation=$HotfixOperationId"
}
try {
  if ($reservationHeld) { Assert-RemoteHotfixReservationAlive -Process $reservationProcess }
  $preflight = Get-RemotePreflight
  if ($reservationHeld) { Assert-RemoteHotfixReservationAlive -Process $reservationProcess }
  if ([string]$preflight.target_platform -ne $Platform) {
    throw "Target candidate platform is $($preflight.target_platform), not $Platform."
  }
  if ([string]$preflight.docker_platform -ne $Platform) {
    throw "Target Docker platform is $($preflight.docker_platform), not $Platform."
  }
  if ($Service -eq "api") {
    Invoke-NativeText -FilePath "git.exe" -ArgumentList @(
      "cat-file", "-e", "$($preflight.source_commit)^{commit}"
    ) | Out-Null
    $migrationChanges = Invoke-NativeText -FilePath "git.exe" -ArgumentList @(
      "diff", "--name-only", "$($preflight.source_commit)..$commit", "--", "alembic"
    )
    if ($migrationChanges) {
      throw "API hotfix contains database migration changes and must use the full deployment workflow."
    }
  }

  Write-Host "Target preflight passed: service=$Service, current=$($preflight.current_image), platform=$Platform"
  if ($DryRun) {
    Write-Host "Dry run complete. No image was built, transferred, or deployed."
    exit 0
  }

  Invoke-ServiceTests
  Assert-RemoteHotfixReservationAlive -Process $reservationProcess
  Write-Host "Building immutable hotfix image from $commit..."
  $artifact = New-HotfixArtifact -Commit $commit -ShortCommit $shortCommit
  Assert-RemoteHotfixReservationAlive -Process $reservationProcess
  Write-Host "Transferring verified artifact to $Target..."
  $remoteDirectory = Send-HotfixArtifact -Artifact $artifact -ShortCommit $shortCommit
  Assert-RemoteHotfixReservationAlive -Process $reservationProcess
  Write-Host "Deploying only the $Service service..."
  $result = Invoke-RemoteDeployment -Artifact $artifact -RemoteDirectory $remoteDirectory
  Write-Host $result
  Write-Host "Remote hotfix deployment completed."
}
finally {
  if ($reservationHeld) {
    try { Release-RemoteHotfixReservation }
    finally { Stop-RemoteHotfixReservation -Process $reservationProcess }
  }
}
