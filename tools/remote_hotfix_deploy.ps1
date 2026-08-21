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
  if ($Capture) {
    $output = & docker @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    $text = (($output | ForEach-Object { "$_" }) -join [Environment]::NewLine).Trim()
    if ($exitCode -ne 0) { throw "Docker command failed with exit code $exitCode." }
    return $text
  }
  & docker @Arguments | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Docker command failed with exit code $LASTEXITCODE." }
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
  if ($Service -eq "web") {
    & docker exec $ContainerName wget -q -O /dev/null $HealthUrl
  }
  else {
    $pythonProbe = "import urllib.request; urllib.request.urlopen('$HealthUrl', timeout=5).read(1)"
    & docker exec $ContainerName python -c $pythonProbe
  }
  return $LASTEXITCODE -eq 0
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
$lockPath = Join-Path $SiteRoot ".remote-hotfix.lock"
$lockStream = $null
try {
  $lockStream = [IO.File]::Open($lockPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
  $lockBytes = [Text.Encoding]::ASCII.GetBytes("pid=$PID`nservice=$Service`n")
  $lockStream.Write($lockBytes, 0, $lockBytes.Length)
  $lockStream.Flush()
}
catch {
  if ($null -ne $lockStream) {
    $lockStream.Dispose()
    Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
  }
  throw "Another remote hotfix is running, or its lock requires inspection: $lockPath"
}
try {
$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ([int]$manifest.schema_version -ne 1 -or [string]$manifest.service -ne $Service) {
  throw "Hotfix manifest identity is invalid."
}
if ([string]$manifest.source_commit -ne $ExpectedCommit -or [string]$manifest.image_reference -ne $ExpectedImage) {
  throw "Hotfix manifest source identity does not match the requested deployment."
}
if ([string]$manifest.archive -ne $ArchiveName) { throw "Hotfix manifest archive name mismatch." }
$actualHash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
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
  $lines[$entryIndex] = "$EnvironmentKey=$ExpectedImage"
  [IO.File]::WriteAllLines($temporaryEnv, $lines, [Text.UTF8Encoding]::new($false))
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
  try {
    $rollbackTemp = "$EnvFile.$PID.rollback.tmp"
    Copy-Item -LiteralPath $backupPath -Destination $rollbackTemp
    [IO.File]::Replace($rollbackTemp, $EnvFile, $null)
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
  if ($null -ne $lockStream) { $lockStream.Dispose() }
  Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}
'@
  $remoteScript = $deploymentTemplate
  $replacements = [ordered]@{
    "__CANDIDATE_ROOT__"   = $CandidateRoot
    "__SITE_ROOT__"        = $SiteRoot
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
$preflight = Get-RemotePreflight
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
Write-Host "Building immutable hotfix image from $commit..."
$artifact = New-HotfixArtifact -Commit $commit -ShortCommit $shortCommit
Write-Host "Transferring verified artifact to $Target..."
$remoteDirectory = Send-HotfixArtifact -Artifact $artifact -ShortCommit $shortCommit
Write-Host "Deploying only the $Service service..."
$result = Invoke-RemoteDeployment -Artifact $artifact -RemoteDirectory $remoteDirectory
Write-Host $result
Write-Host "Remote hotfix deployment completed."
