[CmdletBinding()]
param(
  [ValidateRange(15, 600)][int]$DockerTimeoutSeconds = 180,
  [ValidateRange(30, 900)][int]$StartupTimeoutSeconds = 300,
  [ValidateRange(900, 3600)][int]$LeaseSeconds = 900,
  [switch]$NoBrowser,
  [switch]$NoUi
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$CandidateSitesRoot = "C:\Ruisheng\candidates"
$DockerContext = "desktop-linux"
$DockerEndpoint = "npipe:////./pipe/dockerDesktopLinuxEngine"
$AuditDirectory = "C:\Ruisheng\launcher-audit"
$AuditPath = Join-Path $AuditDirectory "desktop-launcher.jsonl"
$AuditLockPath = Join-Path $AuditDirectory ".desktop-launcher-audit.lock"
$PersistentServices = @("postgres", "redis", "gw", "api", "web")
$PolicyServices = @("postgres", "redis", "migrate", "gw", "api", "web")
$ContainerNames = [ordered]@{
  postgres = "ruisheng-postgres"
  redis    = "ruisheng-redis"
  gw       = "ruisheng-gw"
  api      = "ruisheng-api"
  web      = "ruisheng-web"
}
$OperationId = [Guid]::NewGuid().ToString("D")
$ProcessStartedAt = (Get-Process -Id $PID -ErrorAction Stop).StartTime.ToUniversalTime().ToString("o")
$AcquiredLocks = New-Object System.Collections.ArrayList
$VerifiedInputDirectory = ""
$SiteRoot = ""
$CandidateRoot = ""
$DockerPath = ""
$ExpectedImages = @{}
$PreserveLocksOnExit = $false
$VerifiedInputGuards = New-Object System.Collections.ArrayList

function Write-Stage {
  param([Parameter(Mandatory)][string]$Message)
  Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message)
}

function Show-LauncherError {
  param([Parameter(Mandatory)][string]$ErrorCode)
  if ($NoUi) { return }
  try {
    Add-Type -AssemblyName PresentationFramework -ErrorAction Stop
    [void][Windows.MessageBox]::Show(
      "Ruisheng could not start. Error code: $ErrorCode`nContact the administrator with this code.",
      "Ruisheng",
      [Windows.MessageBoxButton]::OK,
      [Windows.MessageBoxImage]::Error
    )
  }
  catch { }
}

function Get-Sha256Text {
  param([Parameter(Mandatory)][AllowEmptyString()][string]$Text)
  $sha = [Security.Cryptography.SHA256]::Create()
  try {
    return ([BitConverter]::ToString(
        $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text))
    )).Replace("-", "").ToLowerInvariant()
  }
  finally { $sha.Dispose() }
}

function ConvertFrom-JsonPreservingDateStrings {
  param([Parameter(Mandatory)][string]$Json)
  $convertCommand = Get-Command ConvertFrom-Json -ErrorAction Stop
  if ($convertCommand.Parameters.ContainsKey("DateKind")) {
    return $Json | ConvertFrom-Json -DateKind String
  }
  return $Json | ConvertFrom-Json
}

function Get-AllowedSids {
  return @(
    [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    "S-1-5-18"
    "S-1-5-32-544"
  ) | Select-Object -Unique
}

function Assert-RestrictedDirectory {
  param([Parameter(Mandatory)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
    throw "restricted_directory_missing"
  }
  $item = Get-Item -LiteralPath $Path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "restricted_directory_linked"
  }
  $allowed = @{}
  foreach ($sid in @(Get-AllowedSids)) { $allowed[$sid] = $false }
  $acl = Get-Acl -LiteralPath $Path
  if (-not $acl.AreAccessRulesProtected) { throw "restricted_acl_inheritance_enabled" }
  try { $ownerSid = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value }
  catch { throw "restricted_acl_owner_invalid" }
  if (-not $allowed.ContainsKey($ownerSid)) { throw "restricted_acl_owner_invalid" }
  foreach ($rule in @($acl.Access)) {
    try { $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value }
    catch { throw "restricted_acl_invalid" }
    if (
      $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
      -not $allowed.ContainsKey($sid) -or
      ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0 -or
      ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne
        [Security.AccessControl.FileSystemRights]::FullControl
    ) { throw "restricted_acl_invalid" }
    $allowed[$sid] = $true
  }
  foreach ($sid in @($allowed.Keys)) {
    if (-not $allowed[$sid]) { throw "restricted_acl_required_identity_missing" }
  }
}

function Assert-RestrictedFile {
  param([Parameter(Mandatory)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "restricted_file_missing" }
  $item = Get-Item -LiteralPath $Path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "restricted_file_linked"
  }
  $allowed = @{}
  foreach ($sid in @(Get-AllowedSids)) { $allowed[$sid] = $false }
  $acl = Get-Acl -LiteralPath $Path
  try { $ownerSid = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value }
  catch { throw "restricted_acl_owner_invalid" }
  if (-not $allowed.ContainsKey($ownerSid)) { throw "restricted_acl_owner_invalid" }
  foreach ($rule in @($acl.Access)) {
    try { $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value }
    catch { throw "restricted_acl_invalid" }
    if (
      $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
      -not $allowed.ContainsKey($sid) -or
      ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne
        [Security.AccessControl.FileSystemRights]::FullControl
    ) { throw "restricted_acl_invalid" }
    $allowed[$sid] = $true
  }
  foreach ($sid in @($allowed.Keys)) {
    if (-not $allowed[$sid]) { throw "restricted_acl_required_identity_missing" }
  }
}

function Assert-TrustedContainerRoot {
  param([Parameter(Mandatory)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
    throw "trusted_root_missing"
  }
  $item = Get-Item -LiteralPath $Path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "trusted_root_linked"
  }
  $allowedWriters = @(Get-AllowedSids)
  $acl = Get-Acl -LiteralPath $Path
  try { $ownerSid = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value }
  catch { throw "trusted_root_owner_invalid" }
  if ($ownerSid -notin $allowedWriters) { throw "trusted_root_owner_invalid" }
  $writeRights = [Security.AccessControl.FileSystemRights]::CreateFiles -bor `
    [Security.AccessControl.FileSystemRights]::CreateDirectories -bor `
    [Security.AccessControl.FileSystemRights]::WriteData -bor `
    [Security.AccessControl.FileSystemRights]::AppendData -bor `
    [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor `
    [Security.AccessControl.FileSystemRights]::Delete -bor `
    [Security.AccessControl.FileSystemRights]::ChangePermissions -bor `
    [Security.AccessControl.FileSystemRights]::TakeOwnership
  foreach ($rule in @($acl.Access)) {
    if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
        ($rule.FileSystemRights -band $writeRights) -eq 0) { continue }
    try { $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value }
    catch { throw "trusted_root_acl_invalid" }
    if ($sid -notin $allowedWriters) { throw "trusted_root_unapproved_writer" }
  }
}

function Resolve-SiteRoot {
  if (-not (Test-Path -LiteralPath $CandidateSitesRoot -PathType Container)) {
    throw "site_root_discovery_root_missing"
  }
  $root = Get-Item -LiteralPath $CandidateSitesRoot -Force
  if (($root.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "site_root_discovery_root_linked"
  }
  # The machine root may inherit an Administrators/SYSTEM-only ACL. Its children remain strict.
  Assert-TrustedContainerRoot -Path $CandidateSitesRoot
  $siteMatches = New-Object System.Collections.ArrayList
  foreach ($entry in @(Get-ChildItem -LiteralPath $CandidateSitesRoot -Directory -Force)) {
    if ([string]$entry.Name -notmatch '^site(?:-[a-z0-9][a-z0-9._-]{0,57})?$') { continue }
    if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "active_site_root_reparse_point"
    }
    $pointer = Join-Path $entry.FullName ".remote-maintenance-state\active-release.json"
    if (Test-Path -LiteralPath $pointer -PathType Leaf) { [void]$siteMatches.Add($entry) }
  }
  if ($siteMatches.Count -eq 0) { throw "active_site_root_not_found" }
  if ($siteMatches.Count -ne 1) { throw "active_site_root_ambiguous" }
  $resolved = [string]$siteMatches[0].FullName
  Assert-RestrictedDirectory -Path $resolved
  Assert-RestrictedDirectory -Path (Join-Path $resolved ".remote-maintenance-state")
  Assert-RestrictedFile -Path (Join-Path $resolved ".remote-maintenance-state\active-release.json")
  return $resolved
}

function Test-ExactJsonObjectKeys {
  param(
    [Parameter(Mandatory)][AllowNull()]$Value,
    [Parameter(Mandatory)][string[]]$ExpectedKeys
  )
  if ($null -eq $Value -or $Value -isnot [PSCustomObject]) { return $false }
  $actual = @($Value.PSObject.Properties.Name)
  if ($actual.Count -ne $ExpectedKeys.Count) { return $false }
  foreach ($key in $ExpectedKeys) {
    if ($actual -cnotcontains $key) { return $false }
  }
  return $true
}

function Assert-CandidateManifest {
  param([Parameter(Mandatory)]$Manifest)
  $baseKeys = @(
    "schema_version", "candidate_id", "source_commit", "generated_at", "target_os",
    "target_architecture", "alembic_head", "logical_identity", "tools", "authenticity", "images"
  )
  if ($Manifest.schema_version -is [bool] -or
      $Manifest.schema_version -isnot [int] -and $Manifest.schema_version -isnot [long]) {
    throw "manifest_schema_invalid"
  }
  $schemaVersion = [int64]$Manifest.schema_version
  $expectedKeys = if ($schemaVersion -eq 2) { $baseKeys } elseif ($schemaVersion -eq 3) {
    @($baseKeys) + "qualification_toolchain"
  } else { throw "manifest_schema_invalid" }
  if (-not (Test-ExactJsonObjectKeys -Value $Manifest -ExpectedKeys $expectedKeys)) {
    throw "manifest_schema_invalid"
  }
  if (
    $Manifest.candidate_id -isnot [string] -or
    [string]$Manifest.candidate_id -notmatch '^[a-z0-9][a-z0-9._-]{0,62}$' -or
    $Manifest.source_commit -isnot [string] -or
    [string]$Manifest.source_commit -notmatch '^[0-9a-f]{40}$' -or
    $Manifest.logical_identity -isnot [string] -or
    [string]$Manifest.logical_identity -notmatch '^sha256:[0-9a-f]{64}$' -or
    [string]$Manifest.target_os -cne "linux" -or
    [string]$Manifest.target_architecture -cne "amd64" -or
    $Manifest.images -isnot [Array]
  ) { throw "manifest_identity_invalid" }
  if (
    $null -eq $Manifest.authenticity -or
    [string]$Manifest.authenticity.status -cne "SIGNED" -or
    [string]$Manifest.authenticity.scheme -cne "openssh-sshsig" -or
    [string]$Manifest.authenticity.publisher -cne "ruisheng-release" -or
    [string]$Manifest.authenticity.namespace -cne "ruisheng-candidate-v1"
  ) { throw "manifest_authenticity_invalid" }

  $imageKeys = @(
    "component", "source_reference", "repo_digest", "candidate_reference", "image_id",
    "os", "architecture", "archive", "sha256"
  )
  $images = @{}
  foreach ($image in @($Manifest.images)) {
    if (-not (Test-ExactJsonObjectKeys -Value $image -ExpectedKeys $imageKeys)) {
      throw "manifest_schema_invalid"
    }
    $component = [string]$image.component
    if ($component -notin $PersistentServices -or $images.ContainsKey($component)) {
      throw "manifest_images_invalid"
    }
    if (
      [string]$image.candidate_reference -notmatch '^[^\s]+$' -or
      [string]$image.image_id -notmatch '^sha256:[0-9a-f]{64}$' -or
      [string]$image.os -cne "linux" -or
      [string]$image.architecture -cne "amd64"
    ) { throw "manifest_images_invalid" }
    $images[$component] = $image
  }
  if ($images.Count -ne $PersistentServices.Count) { throw "manifest_images_invalid" }
  return $images
}

function Resolve-ActiveRelease {
  param([Parameter(Mandatory)][string]$ResolvedSiteRoot)
  $pointerPath = Join-Path $ResolvedSiteRoot ".remote-maintenance-state\active-release.json"
  Assert-RestrictedFile -Path $pointerPath
  try { $active = Get-Content -LiteralPath $pointerPath -Raw -Encoding UTF8 | ConvertFrom-Json }
  catch { throw "active_release_pointer_invalid" }
  $keys = @(
    "schema_version", "candidate_id", "logical_identity", "source_commit",
    "candidate_root", "site_root", "committed_at", "operation_id"
  )
  if (
    -not (Test-ExactJsonObjectKeys -Value $active -ExpectedKeys $keys) -or
    $active.schema_version -is [bool] -or [int64]$active.schema_version -ne 1 -or
    $active.candidate_id -isnot [string] -or
    [string]$active.candidate_id -notmatch '^[a-z0-9][a-z0-9._-]{0,62}$' -or
    $active.logical_identity -isnot [string] -or
    [string]$active.logical_identity -notmatch '^sha256:[0-9a-f]{64}$' -or
    $active.source_commit -isnot [string] -or
    [string]$active.source_commit -notmatch '^[0-9a-f]{40}$' -or
    $active.candidate_root -isnot [string] -or
    [string]$active.candidate_root -notmatch '^[A-Za-z]:\\[^\r\n]*$' -or
    $active.site_root -isnot [string] -or
    [string]$active.site_root -cne $ResolvedSiteRoot -or
    $active.operation_id -isnot [string] -or
    [string]$active.operation_id -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
  ) { throw "active_release_pointer_invalid" }

  $candidate = [IO.Path]::GetFullPath([string]$active.candidate_root).TrimEnd('\')
  $parent = [IO.Path]::GetFullPath((Split-Path -Parent $candidate)).TrimEnd('\')
  $expectedParent = [IO.Path]::GetFullPath($CandidateSitesRoot).TrimEnd('\')
  if (-not $parent.Equals($expectedParent, [StringComparison]::OrdinalIgnoreCase)) {
    throw "active_release_candidate_outside_root"
  }
  if ((Split-Path -Leaf $candidate) -cne [string]$active.candidate_id) {
    throw "active_release_pointer_invalid"
  }
  Assert-RestrictedDirectory -Path $candidate
  $manifestPath = Join-Path $candidate "MANIFEST.json"
  Assert-RestrictedFile -Path $manifestPath
  try { $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json }
  catch { throw "active_release_manifest_invalid" }
  $images = Assert-CandidateManifest -Manifest $manifest
  if (
    [string]$manifest.candidate_id -cne [string]$active.candidate_id -or
    [string]$manifest.logical_identity -cne [string]$active.logical_identity -or
    [string]$manifest.source_commit -cne [string]$active.source_commit
  ) { throw "active_release_identity_drift" }
  return [pscustomobject]@{ Pointer = $active; Manifest = $manifest; Images = $images; Root = $candidate }
}

function Assert-ActiveReleaseUnchanged {
  param([Parameter(Mandatory)]$Before, [Parameter(Mandatory)]$After)
  foreach ($field in @(
      "schema_version", "candidate_id", "logical_identity", "source_commit",
      "candidate_root", "site_root", "committed_at", "operation_id"
  )) {
    if ([string]$Before.$field -cne [string]$After.$field) {
      throw "active_release_identity_drift"
    }
  }
}

function ConvertTo-NativeArgument {
  param([Parameter(Mandatory)][AllowEmptyString()][string]$Value)
  if ($Value -and $Value -notmatch '[\s"]') { return $Value }
  $builder = New-Object Text.StringBuilder
  [void]$builder.Append('"')
  $slashes = 0
  foreach ($character in $Value.ToCharArray()) {
    if ($character -eq '\') { $slashes++; continue }
    if ($character -eq '"') {
      [void]$builder.Append(('\' * (($slashes * 2) + 1)) + '"')
      $slashes = 0
      continue
    }
    if ($slashes -gt 0) { [void]$builder.Append('\' * $slashes); $slashes = 0 }
    [void]$builder.Append($character)
  }
  if ($slashes -gt 0) { [void]$builder.Append('\' * ($slashes * 2)) }
  [void]$builder.Append('"')
  return $builder.ToString()
}

function Invoke-NativeResult {
  param(
    [Parameter(Mandatory)][string]$FilePath,
    [Parameter(Mandatory)][string[]]$Arguments,
    [ValidateRange(1, 900)][int]$TimeoutSeconds = 120
  )
  $startInfo = New-Object Diagnostics.ProcessStartInfo
  $startInfo.FileName = $FilePath
  $startInfo.Arguments = (@($Arguments | ForEach-Object { ConvertTo-NativeArgument -Value $_ }) -join " ")
  $startInfo.UseShellExecute = $false
  $startInfo.CreateNoWindow = $true
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  $process = New-Object Diagnostics.Process
  $process.StartInfo = $startInfo
  try {
    if (-not $process.Start()) { throw "native_start_failed" }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
      try { $process.Kill() } catch { }
      throw "native_command_timeout"
    }
    $process.WaitForExit()
    return [pscustomobject]@{
      ExitCode = $process.ExitCode
      Stdout   = [string]$stdoutTask.Result
      Stderr   = [string]$stderrTask.Result
    }
  }
  finally { $process.Dispose() }
}

function Invoke-DockerText {
  param(
    [Parameter(Mandatory)][string[]]$Arguments,
    [ValidateRange(1, 900)][int]$TimeoutSeconds = 120,
    [switch]$Mutation
  )
  try {
    $result = Invoke-NativeResult -FilePath $DockerPath `
      -Arguments (@("--host", $DockerEndpoint) + $Arguments) -TimeoutSeconds $TimeoutSeconds
  }
  catch {
    if ($Mutation -and [string]$_.Exception.Message -eq "native_command_timeout") {
      $script:PreserveLocksOnExit = $true
      throw "docker_mutation_timeout_uncertain"
    }
    throw
  }
  if ($result.ExitCode -ne 0) { throw "docker_command_failed" }
  return ([string]$result.Stdout).Trim()
}

function Get-RemainingTimeoutSeconds {
  param(
    [Parameter(Mandatory)][DateTimeOffset]$Deadline,
    [ValidateRange(1, 120)][int]$Maximum = 30,
    [Parameter(Mandatory)][string]$ErrorCode
  )
  $remaining = [int][Math]::Ceiling(($Deadline - [DateTimeOffset]::UtcNow).TotalSeconds)
  if ($remaining -le 0) { throw $ErrorCode }
  return [Math]::Min($Maximum, $remaining)
}

function Find-DockerExecutable {
  if (-not $env:ProgramFiles) { throw "docker_cli_missing" }
  $path = Join-Path $env:ProgramFiles "Docker\Docker\resources\bin\docker.exe"
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "docker_cli_missing" }
  $item = Get-Item -LiteralPath $path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "docker_cli_linked"
  }
  return $item.FullName
}

function Initialize-DockerEnvironment {
  foreach ($name in @(
      "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH",
      "DOCKER_API_VERSION", "DOCKER_DEFAULT_PLATFORM", "COMPOSE_FILE", "COMPOSE_PROFILES",
      "COMPOSE_PROJECT_NAME", "COMPOSE_ENV_FILES"
  )) {
    Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
  }
}

function Assert-LocalDockerContext {
  $result = Invoke-NativeResult -FilePath $DockerPath -Arguments @(
    "context", "inspect", $DockerContext, "--format", "{{.Endpoints.docker.Host}}"
  ) -TimeoutSeconds 15
  if ($result.ExitCode -ne 0 -or
      ([string]$result.Stdout).Trim() -cne $DockerEndpoint) {
    throw "docker_context_not_local"
  }
}

function Test-DockerReady {
  try {
    $result = Invoke-NativeResult -FilePath $DockerPath -Arguments @(
      "--host", $DockerEndpoint, "info", "--format", "{{.ServerVersion}}"
    ) -TimeoutSeconds 10
    return $result.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($result.Stdout)
  }
  catch { return $false }
}

function Start-OrReuseDockerDesktop {
  if (Test-DockerReady) { return }
  $desktopCandidates = @()
  if ($env:ProgramFiles) {
    $desktopCandidates += Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
  }
  if ($env:LOCALAPPDATA) {
    $desktopCandidates += Join-Path $env:LOCALAPPDATA "Docker\Docker Desktop.exe"
  }
  $desktopPath = $desktopCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1
  if (-not $desktopPath) { throw "docker_desktop_missing" }
  $desktopItem = Get-Item -LiteralPath $desktopPath -Force
  if (($desktopItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "docker_desktop_linked"
  }
  $sessionId = (Get-Process -Id $PID -ErrorAction Stop).SessionId
  $desktopProcess = Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue |
    Where-Object { $_.SessionId -eq $sessionId } | Select-Object -First 1
  if ($null -eq $desktopProcess) {
    Start-Process -FilePath $desktopItem.FullName | Out-Null
  }
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds($DockerTimeoutSeconds)
  do {
    Start-Sleep -Seconds 2
    if (Test-DockerReady) { return }
  } while ([DateTimeOffset]::UtcNow -lt $deadline)
  throw "docker_desktop_timeout"
}

function Get-FileHashes {
  return [ordered]@{
    compose  = (Get-FileHash -LiteralPath $SourceComposeFile -Algorithm SHA256).Hash.ToLowerInvariant()
    override = (Get-FileHash -LiteralPath $SourceOverrideFile -Algorithm SHA256).Hash.ToLowerInvariant()
    env      = (Get-FileHash -LiteralPath $SourceEnvFile -Algorithm SHA256).Hash.ToLowerInvariant()
    manifest = (Get-FileHash -LiteralPath $SourceManifestFile -Algorithm SHA256).Hash.ToLowerInvariant()
  }
}

function Assert-NoConfigurationDrift {
  param([Parameter(Mandatory)]$Expected)
  $actual = Get-FileHashes
  foreach ($name in @("compose", "override", "env", "manifest")) {
    if ([string]$actual[$name] -cne [string]$Expected[$name]) { throw "configuration_drift" }
  }
}

function Get-ComposeModel {
  param([Parameter(Mandatory)][string[]]$ComposeArguments)
  $json = Invoke-DockerText -Arguments ($ComposeArguments + @("config", "--format", "json"))
  try { return $json | ConvertFrom-Json }
  catch { throw "compose_model_invalid" }
}

function Assert-ComposePolicy {
  param([Parameter(Mandatory)]$Model, [Parameter(Mandatory)]$Images)
  $serviceNames = @($Model.services.PSObject.Properties.Name)
  if ($serviceNames.Count -ne $PolicyServices.Count -or
      @($serviceNames | Where-Object { $_ -notin $PolicyServices }).Count -gt 0) {
    throw "compose_service_unexpected"
  }
  foreach ($service in $PolicyServices) {
    $serviceModel = $Model.services.PSObject.Properties[$service].Value
    if ($null -eq $serviceModel) { throw "compose_service_missing" }
    if ($null -ne $serviceModel.PSObject.Properties["network_mode"] -and
        -not [string]::IsNullOrWhiteSpace([string]$serviceModel.network_mode)) {
      throw "compose_network_mode_invalid"
    }
    if ([string]$serviceModel.pull_policy -cne "never") { throw "compose_pull_policy_invalid" }
    $component = if ($service -eq "migrate") { "api" } else { $service }
    $expected = $Images[$component]
    $reference = [string]$serviceModel.image
    if (-not $reference -or $reference -match ':latest$' -or
        $reference -cne [string]$expected.candidate_reference) {
      throw "compose_manifest_image_mismatch"
    }
    if ($service -in $PersistentServices -and
        [string]$serviceModel.container_name -cne [string]$ContainerNames[$service]) {
      throw "compose_container_name_invalid"
    }
    foreach ($port in @($serviceModel.ports)) {
      if ($null -ne $port -and (
          $null -eq $port.PSObject.Properties["published"] -or
          [int]$port.published -le 0 -or
          [string]$port.host_ip -notin @("127.0.0.1", "::1")
      )) { throw "non_loopback_port" }
    }
    $expectedPorts = switch ($service) {
      "gw" { @("5020:5020", "9090:9090") }
      "web" { @("80:80") }
      default { @() }
    }
    $actualPorts = @(
      foreach ($port in @($serviceModel.ports)) {
        if ($null -ne $port) { "{0}:{1}" -f [int]$port.target, [int]$port.published }
      }
    )
    if ($actualPorts.Count -ne $expectedPorts.Count -or
        @($actualPorts | Where-Object { $_ -notin $expectedPorts }).Count -gt 0) {
      throw "published_port_set_invalid"
    }
  }
}

function Assert-LoadedImageIdentity {
  param([Parameter(Mandatory)]$Images)
  foreach ($component in $PersistentServices) {
    $image = $Images[$component]
    $actualId = Invoke-DockerText -Arguments @(
      "image", "inspect", "--format", "{{.Id}}", [string]$image.candidate_reference
    )
    if ($actualId -cne [string]$image.image_id) { throw "loaded_image_identity_mismatch" }
  }
}

function Get-ServiceState {
  param(
    [Parameter(Mandatory)][string]$Service,
    [Parameter(Mandatory)][DateTimeOffset]$Deadline,
    [string]$TimeoutErrorCode = "service_health_timeout"
  )
  $container = [string]$ContainerNames[$Service]
  $timeout = Get-RemainingTimeoutSeconds -Deadline $Deadline -Maximum 30 -ErrorCode $TimeoutErrorCode
  $result = Invoke-NativeResult -FilePath $DockerPath -Arguments @(
    "--host", $DockerEndpoint, "container", "inspect", "--format", "{{json .State}}", $container
  ) -TimeoutSeconds $timeout
  if ($result.ExitCode -ne 0) {
    $timeout = Get-RemainingTimeoutSeconds -Deadline $Deadline -Maximum 15 -ErrorCode $TimeoutErrorCode
    $names = Invoke-DockerText -Arguments @("ps", "-a", "--format", "{{.Names}}") `
      -TimeoutSeconds $timeout
    if (@($names -split "`r?`n") -notcontains $container) {
      return [pscustomobject]@{
        service = $Service; exists = $false; running = $false; health = "missing"
        config_image = ""; image_id = ""
      }
    }
    throw "service_state_unavailable"
  }
  try { $state = ([string]$result.Stdout) | ConvertFrom-Json }
  catch { throw "service_state_invalid" }
  if ($null -eq $state.PSObject.Properties["Running"]) { throw "service_state_invalid" }
  $timeout = Get-RemainingTimeoutSeconds -Deadline $Deadline -Maximum 15 -ErrorCode $TimeoutErrorCode
  $configImage = Invoke-DockerText -Arguments @(
    "container", "inspect", "--format", "{{.Config.Image}}", $container
  ) -TimeoutSeconds $timeout
  $timeout = Get-RemainingTimeoutSeconds -Deadline $Deadline -Maximum 15 -ErrorCode $TimeoutErrorCode
  $imageId = Invoke-DockerText -Arguments @(
    "container", "inspect", "--format", "{{.Image}}", $container
  ) -TimeoutSeconds $timeout
  $timeout = Get-RemainingTimeoutSeconds -Deadline $Deadline -Maximum 15 -ErrorCode $TimeoutErrorCode
  $projectLabel = Invoke-DockerText -Arguments @(
    "container", "inspect", "--format", '{{index .Config.Labels "com.docker.compose.project"}}', $container
  ) -TimeoutSeconds $timeout
  $timeout = Get-RemainingTimeoutSeconds -Deadline $Deadline -Maximum 15 -ErrorCode $TimeoutErrorCode
  $serviceLabel = Invoke-DockerText -Arguments @(
    "container", "inspect", "--format", '{{index .Config.Labels "com.docker.compose.service"}}', $container
  ) -TimeoutSeconds $timeout
  $health = if ($null -ne $state.Health) { [string]$state.Health.Status } else { "none" }
  return [pscustomobject]@{
    service = $Service; exists = $true; running = [bool]$state.Running; health = $health
    config_image = $configImage; image_id = $imageId
    compose_project = $projectLabel; compose_service = $serviceLabel
  }
}

function Assert-ServiceImageIdentity {
  param([Parameter(Mandatory)]$State)
  if (-not $State.exists) { return }
  $expected = $ExpectedImages[[string]$State.service]
  if (
    [string]$State.config_image -cne [string]$expected.candidate_reference -or
    [string]$State.image_id -cne [string]$expected.image_id -or
    [string]$State.compose_project -cne "ruisheng-prod" -or
    [string]$State.compose_service -cne [string]$State.service
  ) { throw "container_image_identity_mismatch" }
}

function Assert-NoUnexpectedProjectContainers {
  param([Parameter(Mandatory)][DateTimeOffset]$Deadline)
  $timeout = Get-RemainingTimeoutSeconds -Deadline $Deadline -Maximum 30 `
    -ErrorCode "service_health_timeout"
  $output = Invoke-DockerText -Arguments @(
    "ps", "-a", "--filter", "label=com.docker.compose.project=ruisheng-prod",
    "--format", '{{.Names}}|{{.Label "com.docker.compose.service"}}|{{.State}}'
  ) -TimeoutSeconds $timeout
  $seen = @{}
  foreach ($line in @($output -split "`r?`n" | Where-Object { $_ })) {
    $parts = @($line -split '\|', 3)
    if ($parts.Count -ne 3 -or [string]$parts[1] -notin $PolicyServices) {
      throw "unexpected_project_container"
    }
    $service = [string]$parts[1]
    if ($seen.ContainsKey($service)) { throw "unexpected_project_container" }
    if ($service -in $PersistentServices -and
        [string]$parts[0] -cne [string]$ContainerNames[$service]) {
      throw "unexpected_project_container"
    }
    if ($service -eq "migrate" -and [string]$parts[2] -cne "exited") {
      throw "unexpected_project_container"
    }
    $seen[$service] = $true
  }
}

function Assert-RunningPortBindings {
  param([Parameter(Mandatory)][DateTimeOffset]$Deadline)
  foreach ($service in $PersistentServices) {
    $state = Get-ServiceState -Service $service -Deadline $Deadline
    if (-not $state.exists) { continue }
    Assert-ServiceImageIdentity -State $state
    $container = [string]$ContainerNames[$service]
    $timeout = Get-RemainingTimeoutSeconds -Deadline $Deadline -Maximum 30 `
      -ErrorCode "service_health_timeout"
    $json = Invoke-DockerText -Arguments @(
      "container", "inspect", "--format", "{{json .HostConfig.PortBindings}}", $container
    ) -TimeoutSeconds $timeout
    try { $bindings = if (-not $json -or $json -eq "null") { [pscustomobject]@{} } else {
        $json | ConvertFrom-Json
      } }
    catch { throw "container_port_bindings_invalid" }
    $expectedBindings = switch ($service) {
      "gw" { [ordered]@{ "5020/tcp" = "5020"; "9090/tcp" = "9090" } }
      "web" { [ordered]@{ "80/tcp" = "80" } }
      default { [ordered]@{} }
    }
    $actualNames = @($bindings.PSObject.Properties | ForEach-Object { $_.Name })
    if ($actualNames.Count -ne $expectedBindings.Count -or
        @($actualNames | Where-Object { -not $expectedBindings.Contains($_) }).Count -gt 0) {
      throw "container_port_bindings_invalid"
    }
    foreach ($property in @($bindings.PSObject.Properties)) {
      $values = @($property.Value)
      if ($values.Count -ne 1) { throw "container_port_bindings_invalid" }
      foreach ($binding in $values) {
        if ($null -eq $binding -or
            [string]$binding.HostIp -notin @("127.0.0.1", "::1") -or
            [string]$binding.HostPort -cne [string]$expectedBindings[$property.Name]) {
          throw "non_loopback_runtime_port"
        }
      }
    }
  }
}

function Assert-HostWebReady {
  $response = $null
  try {
    $request = [Net.HttpWebRequest]::Create("http://127.0.0.1/")
    $request.Proxy = $null
    $request.AllowAutoRedirect = $false
    $request.Timeout = 5000
    $request.ReadWriteTimeout = 5000
    $response = $request.GetResponse()
    if ([int]$response.StatusCode -ne 200) { throw "host_web_health_failed" }
  }
  catch { throw "host_web_health_failed" }
  finally { if ($null -ne $response) { $response.Close() } }
}

function Get-HealthResult {
  param(
    [switch]$ActiveProbe,
    [Parameter(Mandatory)][DateTimeOffset]$Deadline,
    [string]$TimeoutErrorCode = "service_health_timeout"
  )
  $health = @()
  foreach ($service in $PersistentServices) {
    $state = Get-ServiceState -Service $service -Deadline $Deadline `
      -TimeoutErrorCode $TimeoutErrorCode
    Assert-ServiceImageIdentity -State $state
    $ready = $state.exists -and $state.running
    if ($service -in @("postgres", "redis") -and $state.health -cne "healthy") { $ready = $false }
    if ($ActiveProbe -and $ready -and $service -eq "api") {
      try {
        $timeout = Get-RemainingTimeoutSeconds -Deadline $Deadline -Maximum 15 `
          -ErrorCode $TimeoutErrorCode
        [void](Invoke-DockerText -Arguments @(
          "exec", "ruisheng-api", "python", "-m", "ruisheng_api.healthcheck"
        ) -TimeoutSeconds $timeout)
      }
      catch { $ready = $false }
    }
    if ($ActiveProbe -and $ready -and $service -eq "gw") {
      try {
        $timeout = Get-RemainingTimeoutSeconds -Deadline $Deadline -Maximum 15 `
          -ErrorCode $TimeoutErrorCode
        [void](Invoke-DockerText -Arguments @(
          "exec", "ruisheng-gw", "python", "-m", "ruisheng_gw.healthcheck"
        ) -TimeoutSeconds $timeout)
      }
      catch { $ready = $false }
    }
    if ($ActiveProbe -and $ready -and $service -eq "web") {
      try {
        $timeout = Get-RemainingTimeoutSeconds -Deadline $Deadline -Maximum 15 `
          -ErrorCode $TimeoutErrorCode
        [void](Invoke-DockerText -Arguments @(
          "exec", "ruisheng-web", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1/"
        ) -TimeoutSeconds $timeout)
      }
      catch { $ready = $false }
    }
    $health += ,[pscustomobject]@{
      service = $service; ready = [bool]$ready; running = [bool]$state.running
      health = [string]$state.health; image = [string]$state.config_image
    }
  }
  return $health
}

function Write-JsonAtomic {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Value)
  $temporary = "$Path.$PID.$([Guid]::NewGuid().ToString('N')).tmp"
  $utf8 = New-Object Text.UTF8Encoding($false)
  [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 8 -Compress), $utf8)
  if (Test-Path -LiteralPath $Path -PathType Leaf) {
    $backup = "$Path.$PID.$([Guid]::NewGuid().ToString('N')).replace.bak"
    try { [IO.File]::Replace($temporary, $Path, $backup) }
    finally {
      Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
      Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
  }
  else { [IO.File]::Move($temporary, $Path) }
}

function ConvertTo-ValidatedLockRecord {
  param([Parameter(Mandatory)]$Record, [Parameter(Mandatory)][string]$ExpectedName)
  try {
    $acquired = [DateTimeOffset]::Parse([string]$Record.acquired_at)
    $expires = [DateTimeOffset]::Parse([string]$Record.expires_at)
    if (
      [int]$Record.schema_version -ne 1 -or
      [string]$Record.lock_name -cne $ExpectedName -or
      [string]$Record.operation_id -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$' -or
      [string]$Record.action -notmatch '^(StopApp|StartApp|RestartApp|hotfix-(api|gw|web)|maintenance-security-prepare|full-upgrade)$' -or
      [int]$Record.pid -le 0 -or
      [string]$Record.target -notmatch '^[A-Za-z0-9._-]{1,255}$' -or
      $expires -le $acquired -or ($expires - $acquired).TotalSeconds -gt 3600
    ) { throw "invalid" }
    [void][DateTimeOffset]::Parse([string]$Record.process_started_at)
    return $Record
  }
  catch { throw "lock_conflict_unrecognized" }
}

function Test-MatchingProcess {
  param([Parameter(Mandatory)]$Record)
  try { $process = Get-Process -Id ([int]$Record.pid) -ErrorAction Stop }
  catch [Microsoft.PowerShell.Commands.ProcessCommandException] { return $false }
  catch { throw "lock_owner_uncertain" }
  try {
    $recorded = [DateTimeOffset]::Parse([string]$Record.process_started_at).UtcDateTime
    return [Math]::Abs(($process.StartTime.ToUniversalTime() - $recorded).TotalSeconds) -lt 1
  }
  catch { throw "lock_owner_uncertain" }
}

function New-LockRecord {
  param([Parameter(Mandatory)][string]$Name)
  return [ordered]@{
    schema_version = 1; lock_name = $Name; operation_id = $OperationId; action = "StartApp"
    pid = $PID; process_started_at = $ProcessStartedAt; target = [string]$env:COMPUTERNAME
    acquired_at = [DateTimeOffset]::UtcNow.ToString("o")
    expires_at = [DateTimeOffset]::UtcNow.AddSeconds($LeaseSeconds).ToString("o")
  }
}

function Acquire-LeasedLock {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Name)
  for ($attempt = 0; $attempt -lt 2; $attempt++) {
    $stream = $null
    try {
      $record = New-LockRecord -Name $Name
      $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(
        ($record | ConvertTo-Json -Depth 4 -Compress)
      )
      $stream = [IO.File]::Open(
        $Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None
      )
      $stream.Write($bytes, 0, $bytes.Length)
      $stream.Flush()
      $stream.Dispose()
      $stream = $null
      [void]$AcquiredLocks.Add([pscustomobject]@{ path = $Path; name = $Name })
      return
    }
    catch {
      if ($null -ne $stream) { $stream.Dispose() }
      if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "lock_acquire_failed" }
      try {
        $existing = ConvertTo-ValidatedLockRecord `
          -Record (ConvertFrom-JsonPreservingDateStrings -Json (
              Get-Content -LiteralPath $Path -Raw -Encoding UTF8
          )) -ExpectedName $Name
      }
      catch { throw "lock_conflict_unrecognized" }
      $expired = [DateTimeOffset]::Parse([string]$existing.expires_at) -lt [DateTimeOffset]::UtcNow
      if (-not $expired -or (Test-MatchingProcess -Record $existing)) { throw "lock_conflict_active" }
      $tombstone = "$Path.stale.$OperationId.$([Guid]::NewGuid().ToString('N'))"
      try { [IO.File]::Move($Path, $tombstone) }
      catch { throw "lock_conflict_race" }
    }
    finally { if ($null -ne $stream) { $stream.Dispose() } }
  }
  throw "lock_acquire_failed"
}

function Assert-LocksOwned {
  foreach ($held in @($AcquiredLocks)) {
    try {
      $record = ConvertTo-ValidatedLockRecord `
        -Record (ConvertFrom-JsonPreservingDateStrings -Json (
            Get-Content -LiteralPath $held.path -Raw -Encoding UTF8
        )) `
        -ExpectedName $held.name
    }
    catch { throw "lock_ownership_lost" }
    if ([string]$record.operation_id -cne $OperationId -or
        [string]$record.process_started_at -cne $ProcessStartedAt) {
      throw "lock_ownership_lost"
    }
  }
}

function Renew-Locks {
  Assert-LocksOwned
  foreach ($held in @($AcquiredLocks)) {
    $record = ConvertFrom-JsonPreservingDateStrings -Json (
      Get-Content -LiteralPath $held.path -Raw -Encoding UTF8
    )
    $record.expires_at = [DateTimeOffset]::UtcNow.AddSeconds($LeaseSeconds).ToString("o")
    Write-JsonAtomic -Path $held.path -Value $record
  }
  Assert-LocksOwned
}

function Release-Locks {
  $heldLocks = @($AcquiredLocks)
  [array]::Reverse($heldLocks)
  foreach ($held in $heldLocks) {
    try {
      $record = ConvertTo-ValidatedLockRecord `
        -Record (ConvertFrom-JsonPreservingDateStrings -Json (
            Get-Content -LiteralPath $held.path -Raw -Encoding UTF8
        )) `
        -ExpectedName $held.name
      if ([string]$record.operation_id -ceq $OperationId -and
          [string]$record.process_started_at -ceq $ProcessStartedAt) {
        Remove-Item -LiteralPath $held.path -Force
      }
    }
    catch { }
  }
}

function Open-AuditLock {
  Assert-RestrictedDirectory -Path $AuditDirectory
  Assert-RestrictedFile -Path $AuditLockPath
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
  do {
    try {
      return [IO.File]::Open(
        $AuditLockPath, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None
      )
    }
    catch [IO.IOException] {
      if ([DateTimeOffset]::UtcNow -ge $deadline) { throw "audit_lock_timeout" }
      Start-Sleep -Milliseconds 100
    }
  } while ($true)
}

function Write-LauncherAudit {
  param([Parameter(Mandatory)][string]$Event, [string]$Result = "", [string]$ErrorCode = "")
  $lock = Open-AuditLock
  try {
    $previousHash = "0" * 64
    $count = 0
    if (Test-Path -LiteralPath $AuditPath -PathType Leaf) {
      Assert-RestrictedFile -Path $AuditPath
      if ((Get-Item -LiteralPath $AuditPath).Length -gt 16MB) { throw "audit_file_limit_exceeded" }
      foreach ($line in Get-Content -LiteralPath $AuditPath -Encoding UTF8) {
        if (-not $line) { continue }
        $count++
        if ($count -gt 50000 -or [Text.Encoding]::UTF8.GetByteCount($line) -gt 65536) {
          throw "audit_limit_exceeded"
        }
        try {
          $record = ConvertFrom-JsonPreservingDateStrings -Json $line
          if ([string]$record.previous_hash -cne $previousHash) { throw "invalid" }
          $payload = [ordered]@{}
          foreach ($property in $record.PSObject.Properties) {
            if ($property.Name -cne "record_hash") { $payload[$property.Name] = $property.Value }
          }
          $calculated = Get-Sha256Text -Text ($payload | ConvertTo-Json -Depth 6 -Compress)
          if ($calculated -cne [string]$record.record_hash) { throw "invalid" }
          $previousHash = $calculated
        }
        catch { throw "audit_chain_invalid" }
      }
    }
    $payload = [ordered]@{
      schema_version = 1; operation_id = $OperationId
      recorded_at = [DateTimeOffset]::UtcNow.ToString("o"); event = $Event; result = $Result
      candidate_id = if ($CandidateRoot) { Split-Path -Leaf $CandidateRoot } else { "" }
      logical_identity = if ($null -ne $activeRelease) {
        [string]$activeRelease.Pointer.logical_identity
      } else { "" }
      source_commit = if ($null -ne $activeRelease) {
        [string]$activeRelease.Pointer.source_commit
      } else { "" }
      error_code = $ErrorCode; previous_hash = $previousHash
    }
    $json = $payload | ConvertTo-Json -Depth 6 -Compress
    $record = [ordered]@{}
    foreach ($entry in $payload.GetEnumerator()) { $record[$entry.Key] = $entry.Value }
    $record.record_hash = Get-Sha256Text -Text $json
    $line = ($record | ConvertTo-Json -Depth 6 -Compress) + [Environment]::NewLine
    if ([Text.Encoding]::UTF8.GetByteCount($line) -gt 65536) { throw "audit_line_limit_exceeded" }
    [IO.File]::AppendAllText($AuditPath, $line, (New-Object Text.UTF8Encoding($false)))
    Assert-RestrictedFile -Path $AuditPath
  }
  finally { $lock.Dispose() }
}

function Initialize-VerifiedInputs {
  param([Parameter(Mandatory)]$ExpectedHashes)
  if (Test-Path -LiteralPath $VerifiedInputDirectory) { throw "verified_inputs_conflict" }
  [void](New-Item -ItemType Directory -Path $VerifiedInputDirectory)
  $copies = [ordered]@{
    compose  = @($SourceComposeFile, (Join-Path $VerifiedInputDirectory "docker-compose.prod.yml"))
    override = @($SourceOverrideFile, (Join-Path $VerifiedInputDirectory "site-network.override.yml"))
    env      = @($SourceEnvFile, (Join-Path $VerifiedInputDirectory ".env.prod"))
    manifest = @($SourceManifestFile, (Join-Path $VerifiedInputDirectory "MANIFEST.json"))
  }
  foreach ($name in @($copies.Keys)) {
    Copy-Item -LiteralPath $copies[$name][0] -Destination $copies[$name][1]
    $copyHash = (Get-FileHash -LiteralPath $copies[$name][1] -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($copyHash -cne [string]$ExpectedHashes[$name]) { throw "configuration_drift" }
  }
  Assert-NoConfigurationDrift -Expected $ExpectedHashes
  return [pscustomobject]@{
    Compose = $copies.compose[1]; Override = $copies.override[1]
    Env = $copies.env[1]; Manifest = $copies.manifest[1]
  }
}

function Assert-VerifiedInputIntegrity {
  param([Parameter(Mandatory)]$Verified, [Parameter(Mandatory)]$ExpectedHashes)
  $paths = [ordered]@{
    compose = $Verified.Compose; override = $Verified.Override
    env = $Verified.Env; manifest = $Verified.Manifest
  }
  foreach ($name in @($paths.Keys)) {
    Assert-RestrictedFile -Path $paths[$name]
    $actual = (Get-FileHash -LiteralPath $paths[$name] -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -cne [string]$ExpectedHashes[$name]) { throw "verified_input_drift" }
  }
}

function Open-VerifiedInputGuards {
  param([Parameter(Mandatory)]$Verified, [Parameter(Mandatory)]$ExpectedHashes)
  Assert-VerifiedInputIntegrity -Verified $Verified -ExpectedHashes $ExpectedHashes
  foreach ($path in @($Verified.Compose, $Verified.Override, $Verified.Env, $Verified.Manifest)) {
    $stream = [IO.File]::Open(
      $path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
    )
    [void]$VerifiedInputGuards.Add($stream)
  }
  Assert-VerifiedInputIntegrity -Verified $Verified -ExpectedHashes $ExpectedHashes
}

function Close-VerifiedInputGuards {
  foreach ($stream in @($VerifiedInputGuards)) {
    try { $stream.Dispose() } catch { }
  }
  $VerifiedInputGuards.Clear()
}

function Wait-DependenciesHealthy {
  param([Parameter(Mandatory)]$ExpectedHashes, [Parameter(Mandatory)]$OriginalActive)
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds([Math]::Min(120, $StartupTimeoutSeconds))
  do {
    Assert-NoConfigurationDrift -Expected $ExpectedHashes
    Assert-LocksOwned
    Assert-ActiveReleaseUnchanged -Before $OriginalActive `
      -After (Resolve-ActiveRelease -ResolvedSiteRoot $SiteRoot).Pointer
    $states = Get-HealthResult -Deadline $deadline -TimeoutErrorCode "dependency_health_timeout" |
      Where-Object { $_.service -in @("postgres", "redis") }
    Assert-ActiveReleaseUnchanged -Before $OriginalActive `
      -After (Resolve-ActiveRelease -ResolvedSiteRoot $SiteRoot).Pointer
    Assert-NoConfigurationDrift -Expected $ExpectedHashes
    if (@($states | Where-Object { -not $_.ready }).Count -eq 0) { return }
    Renew-Locks
    Start-Sleep -Seconds 2
  } while ([DateTimeOffset]::UtcNow -lt $deadline)
  throw "dependency_health_timeout"
}

function Wait-AllHealthy {
  param([Parameter(Mandatory)]$ExpectedHashes, [Parameter(Mandatory)]$OriginalActive)
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds($StartupTimeoutSeconds)
  do {
    Assert-NoConfigurationDrift -Expected $ExpectedHashes
    Assert-LocksOwned
    Assert-ActiveReleaseUnchanged -Before $OriginalActive `
      -After (Resolve-ActiveRelease -ResolvedSiteRoot $SiteRoot).Pointer
    $health = Get-HealthResult -ActiveProbe -Deadline $deadline
    Assert-ActiveReleaseUnchanged -Before $OriginalActive `
      -After (Resolve-ActiveRelease -ResolvedSiteRoot $SiteRoot).Pointer
    Assert-NoConfigurationDrift -Expected $ExpectedHashes
    if (@($health | Where-Object { -not $_.ready }).Count -eq 0) { return $health }
    Renew-Locks
    Start-Sleep -Seconds 2
  } while ([DateTimeOffset]::UtcNow -lt $deadline)
  throw "service_health_timeout"
}

$activeRelease = $null
$auditReady = $false
$succeeded = $false
$resultCandidate = ""
try {
  Write-Stage "Checking Docker Desktop"
  Initialize-DockerEnvironment
  $DockerPath = Find-DockerExecutable
  Assert-LocalDockerContext
  Start-OrReuseDockerDesktop

  Write-Stage "Verifying the active release"
  $SiteRoot = Resolve-SiteRoot
  $activeRelease = Resolve-ActiveRelease -ResolvedSiteRoot $SiteRoot
  $CandidateRoot = [string]$activeRelease.Root
  $ExpectedImages = $activeRelease.Images
  $resultCandidate = [string]$activeRelease.Pointer.candidate_id
  $StateDirectory = Join-Path $SiteRoot ".remote-maintenance-state"
  $SharedLockPath = Join-Path $StateDirectory ".remote-maintenance.lock"
  $LegacyLockPath = Join-Path $SiteRoot ".remote-hotfix.lock"
  $SourceComposeFile = Join-Path $CandidateRoot "docker-compose.prod.yml"
  $SourceOverrideFile = Join-Path $CandidateRoot "site-network.override.yml"
  $SourceEnvFile = Join-Path $SiteRoot ".env.prod"
  $SourceManifestFile = Join-Path $CandidateRoot "MANIFEST.json"
  foreach ($path in @($SourceComposeFile, $SourceOverrideFile, $SourceEnvFile, $SourceManifestFile)) {
    Assert-RestrictedFile -Path $path
  }
  $sourceHashes = Get-FileHashes
  $sourceComposeBase = @(
    "compose", "--project-directory", $CandidateRoot,
    "-f", $SourceComposeFile, "-f", $SourceOverrideFile, "--env-file", $SourceEnvFile
  )
  $sourceModel = Get-ComposeModel -ComposeArguments $sourceComposeBase
  Assert-ComposePolicy -Model $sourceModel -Images $ExpectedImages
  Assert-LoadedImageIdentity -Images $ExpectedImages
  Assert-NoConfigurationDrift -Expected $sourceHashes
  Assert-ActiveReleaseUnchanged -Before $activeRelease.Pointer `
    -After (Resolve-ActiveRelease -ResolvedSiteRoot $SiteRoot).Pointer
  Assert-NoConfigurationDrift -Expected $sourceHashes

  Assert-RestrictedDirectory -Path $AuditDirectory
  Assert-RestrictedFile -Path $AuditLockPath
  $auditReady = $true
  Write-Stage "Acquiring maintenance leases"
  Acquire-LeasedLock -Path $SharedLockPath -Name "shared-maintenance"
  Acquire-LeasedLock -Path $LegacyLockPath -Name "legacy-hotfix"
  Assert-ActiveReleaseUnchanged -Before $activeRelease.Pointer `
    -After (Resolve-ActiveRelease -ResolvedSiteRoot $SiteRoot).Pointer
  Assert-NoConfigurationDrift -Expected $sourceHashes
  Assert-LoadedImageIdentity -Images $ExpectedImages
  $healthDeadline = [DateTimeOffset]::UtcNow.AddSeconds($StartupTimeoutSeconds)
  Assert-NoUnexpectedProjectContainers -Deadline $healthDeadline
  $health = Get-HealthResult -ActiveProbe -Deadline $healthDeadline
  Assert-ActiveReleaseUnchanged -Before $activeRelease.Pointer `
    -After (Resolve-ActiveRelease -ResolvedSiteRoot $SiteRoot).Pointer
  Assert-NoConfigurationDrift -Expected $sourceHashes
  Assert-NoUnexpectedProjectContainers -Deadline $healthDeadline
  Assert-RunningPortBindings -Deadline $healthDeadline
  if (@($health | Where-Object { -not $_.ready }).Count -eq 0) {
    $auditResult = "already_ready"
  }
  else {
    $VerifiedInputDirectory = Join-Path $StateDirectory "$OperationId.desktop-launcher-inputs"
    $verified = Initialize-VerifiedInputs -ExpectedHashes $sourceHashes
    $composeBase = @(
      "compose", "--project-directory", $CandidateRoot,
      "-f", $verified.Compose, "-f", $verified.Override, "--env-file", $verified.Env
    )
    Open-VerifiedInputGuards -Verified $verified -ExpectedHashes $sourceHashes
    $verifiedModel = Get-ComposeModel -ComposeArguments $composeBase
    Assert-ComposePolicy -Model $verifiedModel -Images $ExpectedImages
    Assert-LoadedImageIdentity -Images $ExpectedImages
    Write-LauncherAudit -Event "launcher_started" -Result "executing"

    Write-Stage "Starting database and cache"
    Assert-NoConfigurationDrift -Expected $sourceHashes
    Assert-VerifiedInputIntegrity -Verified $verified -ExpectedHashes $sourceHashes
    Assert-LoadedImageIdentity -Images $ExpectedImages
    Assert-LocksOwned
    Assert-ActiveReleaseUnchanged -Before $activeRelease.Pointer `
      -After (Resolve-ActiveRelease -ResolvedSiteRoot $SiteRoot).Pointer
    [void](Invoke-DockerText -Arguments ($composeBase + @(
      "up", "-d", "--no-build", "postgres", "redis"
    )) -TimeoutSeconds 180 -Mutation)
    Wait-DependenciesHealthy -ExpectedHashes $sourceHashes -OriginalActive $activeRelease.Pointer

    Write-Stage "Running database migration"
    Assert-NoConfigurationDrift -Expected $sourceHashes
    Assert-VerifiedInputIntegrity -Verified $verified -ExpectedHashes $sourceHashes
    Assert-LoadedImageIdentity -Images $ExpectedImages
    Renew-Locks
    Assert-ActiveReleaseUnchanged -Before $activeRelease.Pointer `
      -After (Resolve-ActiveRelease -ResolvedSiteRoot $SiteRoot).Pointer
    [void](Invoke-DockerText -Arguments ($composeBase + @(
      "up", "--no-build", "--no-deps", "--force-recreate", "--abort-on-container-exit",
      "--exit-code-from", "migrate", "migrate"
    )) -TimeoutSeconds 600 -Mutation)

    Write-Stage "Starting gateway, API, and web"
    Assert-NoConfigurationDrift -Expected $sourceHashes
    Assert-VerifiedInputIntegrity -Verified $verified -ExpectedHashes $sourceHashes
    Assert-LoadedImageIdentity -Images $ExpectedImages
    Renew-Locks
    Assert-ActiveReleaseUnchanged -Before $activeRelease.Pointer `
      -After (Resolve-ActiveRelease -ResolvedSiteRoot $SiteRoot).Pointer
    [void](Invoke-DockerText -Arguments ($composeBase + @(
      "up", "-d", "--no-build", "gw", "api", "web"
    )) -TimeoutSeconds 180 -Mutation)
    $health = Wait-AllHealthy -ExpectedHashes $sourceHashes -OriginalActive $activeRelease.Pointer
    Assert-NoConfigurationDrift -Expected $sourceHashes
    Assert-ActiveReleaseUnchanged -Before $activeRelease.Pointer `
      -After (Resolve-ActiveRelease -ResolvedSiteRoot $SiteRoot).Pointer
    Assert-NoConfigurationDrift -Expected $sourceHashes
    $finalValidationDeadline = [DateTimeOffset]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    Assert-NoUnexpectedProjectContainers -Deadline $finalValidationDeadline
    Assert-RunningPortBindings -Deadline $finalValidationDeadline
    $auditResult = "succeeded"
  }
  Assert-ActiveReleaseUnchanged -Before $activeRelease.Pointer `
    -After (Resolve-ActiveRelease -ResolvedSiteRoot $SiteRoot).Pointer
  Assert-NoConfigurationDrift -Expected $sourceHashes
  Assert-HostWebReady
  Write-Stage "Ruisheng is ready"
  if (-not $NoBrowser) { Start-Process -FilePath "http://127.0.0.1/" | Out-Null }
  Write-LauncherAudit -Event "launcher_completed" -Result $auditResult
  $succeeded = $true
}
catch {
  $errorCode = [string]$_.Exception.Message
  if ($errorCode -notmatch '^[a-z0-9_]+$') { $errorCode = "desktop_launcher_failed" }
  if ($auditReady) {
    try { Write-LauncherAudit -Event "launcher_completed" -Result "failed" -ErrorCode $errorCode }
    catch { }
  }
  Show-LauncherError -ErrorCode $errorCode
  Write-Error "Ruisheng launcher failed: $errorCode"
}
finally {
  Close-VerifiedInputGuards
  if (-not $PreserveLocksOnExit) {
    if ($VerifiedInputDirectory -and (Test-Path -LiteralPath $VerifiedInputDirectory -PathType Container)) {
      Remove-Item -LiteralPath $VerifiedInputDirectory -Recurse -Force -ErrorAction SilentlyContinue
    }
    Release-Locks
  }
}

if (-not $succeeded) { exit 1 }
Write-Output "READY candidate=$resultCandidate url=http://127.0.0.1/"
exit 0
