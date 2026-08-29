[CmdletBinding()]
param(
  [Parameter(Position = 0)]
  [ValidateSet("Status", "StopApp", "StartApp", "RestartApp")]
  [string]$Action = "Status",
  [string]$Reason = "",
  [string]$OperationId = "",
  [string]$Target = "lenovo@100.109.90.21",
  [string]$CandidateRoot = "C:\Ruisheng\candidates\deploy-20260821.1",
  [string]$SiteRoot = "C:\Ruisheng\candidates\site",
  [ValidateRange(120, 3600)]
  [int]$LeaseSeconds = 900,
  [switch]$DryRun,
  [switch]$Approved
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$script:AuditMaxFileBytes = 16 * 1024 * 1024
$script:AuditMaxLineBytes = 64 * 1024
$script:AuditMaxRecords = 50000

function ConvertTo-PowerShellUtf8Expression {
  param([Parameter(Mandatory)][AllowEmptyString()][string]$Value)
  $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Value))
  return "[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$encoded'))"
}

function Assert-RemotePath {
  param([Parameter(Mandatory)][string]$Path)
  if ($Path -notmatch '^[A-Za-z]:\\[^\r\n]*$') {
    throw "Remote paths must be absolute Windows paths."
  }
}

function Get-RestrictedDirectorySids {
  return @(
    [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    "S-1-5-18"       # Local System
    "S-1-5-32-544"   # Built-in Administrators
  ) | Select-Object -Unique
}

function Assert-RestrictedDirectory {
  param([Parameter(Mandatory)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
    throw "restricted_directory_missing"
  }

  $allowed = @{}
  foreach ($sid in @(Get-RestrictedDirectorySids)) { $allowed[$sid] = $false }
  $acl = Get-Acl -LiteralPath $Path
  if (-not $acl.AreAccessRulesProtected) { throw "restricted_acl_inheritance_enabled" }
  try { $ownerSid = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value }
  catch { throw "restricted_acl_owner_invalid" }
  if (-not $allowed.ContainsKey($ownerSid)) { throw "restricted_acl_owner_invalid" }
  foreach ($rule in @($acl.Access)) {
    try {
      $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
    }
    catch { throw "restricted_acl_identity_invalid" }
    if (
      $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
      -not $allowed.ContainsKey($sid) -or
      ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0 -or
      ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne
        [Security.AccessControl.FileSystemRights]::FullControl
    ) {
      throw "restricted_acl_invalid"
    }
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
    throw "restricted_file_reparse_point"
  }
  $allowed = @{}
  foreach ($sid in @(Get-RestrictedDirectorySids)) { $allowed[$sid] = $false }
  $acl = Get-Acl -LiteralPath $Path
  try { $ownerSid = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value }
  catch { throw "restricted_acl_owner_invalid" }
  if (-not $allowed.ContainsKey($ownerSid)) { throw "restricted_acl_owner_invalid" }
  foreach ($rule in @($acl.Access)) {
    try {
      $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
    }
    catch { throw "restricted_acl_identity_invalid" }
    if (
      $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
      -not $allowed.ContainsKey($sid) -or
      ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne
        [Security.AccessControl.FileSystemRights]::FullControl
    ) {
      throw "restricted_acl_invalid"
    }
    $allowed[$sid] = $true
  }
  foreach ($sid in @($allowed.Keys)) {
    if (-not $allowed[$sid]) { throw "restricted_acl_required_identity_missing" }
  }
}

function Open-ExclusiveAuditLock {
  param(
    [Parameter(Mandatory)][string]$Path,
    [ValidateRange(100, 60000)][int]$TimeoutMilliseconds = 30000
  )
  Assert-RestrictedFile -Path $Path
  $deadline = [DateTimeOffset]::UtcNow.AddMilliseconds($TimeoutMilliseconds)
  do {
    try {
      return [IO.File]::Open(
        $Path, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None
      )
    }
    catch [IO.IOException] {
      if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "audit_append_lock_missing" }
      if ([DateTimeOffset]::UtcNow -ge $deadline) { throw "audit_append_lock_timeout" }
      Start-Sleep -Milliseconds 50
    }
  } while ($true)
}

function Write-OperatorAudit {
  param(
    [Parameter(Mandatory)]$Result,
    [Parameter(Mandatory)][string]$RequestedAction,
    [Parameter(Mandatory)][string]$RequestedTarget,
    [string]$AuditDirectory = ""
  )

  if (-not $AuditDirectory) {
    $AuditDirectory = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "Ruisheng\audit"
  }
  Assert-RestrictedDirectory -Path $AuditDirectory
  $auditPath = Join-Path $AuditDirectory "remote-maintenance.jsonl"
  $auditLockPath = Join-Path $AuditDirectory ".remote-maintenance-audit.lock"
  Assert-RestrictedFile -Path $auditLockPath
  $appendLock = Open-ExclusiveAuditLock -Path $auditLockPath
  try {
    $previousHash = "0" * 64
    $duplicate = $false
    $auditLength = 0
    $recordCount = 0
    if (Test-Path -LiteralPath $auditPath -PathType Leaf) {
      Assert-RestrictedFile -Path $auditPath
      $auditItem = Get-Item -LiteralPath $auditPath -Force
      $auditLength = [long]$auditItem.Length
      if ($auditLength -gt $script:AuditMaxFileBytes) {
        throw "operator_audit_file_limit_exceeded"
      }
      foreach ($existingLine in Get-Content -LiteralPath $auditPath -Encoding UTF8 -ErrorAction Stop) {
        if (-not $existingLine) { continue }
        if ([Text.Encoding]::UTF8.GetByteCount($existingLine) -gt $script:AuditMaxLineBytes) {
          throw "operator_audit_line_limit_exceeded"
        }
        $recordCount++
        if ($recordCount -gt $script:AuditMaxRecords) {
          throw "operator_audit_record_limit_exceeded"
        }
        try {
          $previous = $existingLine | ConvertFrom-Json
          if ([string]$previous.previous_hash -ne $previousHash) { throw "invalid link" }
          $verifiedPayload = [ordered]@{}
          foreach ($property in $previous.PSObject.Properties) {
            if ($property.Name -ne "record_hash") {
              $verifiedPayload[$property.Name] = $property.Value
            }
          }
          $verifiedJson = $verifiedPayload | ConvertTo-Json -Depth 4 -Compress
          $verifier = [Security.Cryptography.SHA256]::Create()
          try {
            $verifiedBytes = [Text.Encoding]::UTF8.GetBytes($verifiedJson)
            $verifiedHash = ([BitConverter]::ToString($verifier.ComputeHash($verifiedBytes))).Replace("-", "").ToLowerInvariant()
          }
          finally { $verifier.Dispose() }
          if ($verifiedHash -ne [string]$previous.record_hash) { throw "invalid hash" }
          if ([string]$previous.operation_id -eq [string]$Result.operation_id) {
            if (
              [string]$previous.audit_id -ne [string]$Result.audit_id -or
              [string]$previous.action -ne $RequestedAction -or
              [string]$previous.target -ne $RequestedTarget -or
              [string]$previous.result -ne [string]$Result.status
            ) {
              throw "operation mirror conflict"
            }
            $duplicate = $true
          }
          $previousHash = $verifiedHash
        }
        catch {
          throw "Operator audit chain is invalid; refusing to append."
        }
      }
    }
    if ($duplicate) { return }

    $payload = [ordered]@{
      schema_version  = 1
      recorded_at     = [DateTimeOffset]::UtcNow.ToString("o")
      operation_id    = [string]$Result.operation_id
      audit_id        = [string]$Result.audit_id
      action          = $RequestedAction
      target          = $RequestedTarget
      result          = [string]$Result.status
      remote_user     = [string]$Result.identity.user
      remote_computer = [string]$Result.identity.computer
      previous_hash   = $previousHash
    }
    $payloadJson = $payload | ConvertTo-Json -Depth 4 -Compress
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
      $bytes = [Text.Encoding]::UTF8.GetBytes($payloadJson)
      $recordHash = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    }
    finally { $sha.Dispose() }

    $record = [ordered]@{}
    foreach ($property in $payload.GetEnumerator()) { $record[$property.Key] = $property.Value }
    $record.record_hash = $recordHash
    $line = $record | ConvertTo-Json -Depth 4 -Compress
    $utf8 = New-Object Text.UTF8Encoding($false)
    $appendText = $line + [Environment]::NewLine
    $appendBytes = [Text.Encoding]::UTF8.GetByteCount($appendText)
    if ([Text.Encoding]::UTF8.GetByteCount($line) -gt $script:AuditMaxLineBytes) {
      throw "operator_audit_line_limit_exceeded"
    }
    if ($recordCount -ge $script:AuditMaxRecords) {
      throw "operator_audit_record_limit_exceeded"
    }
    if ($auditLength + $appendBytes -gt $script:AuditMaxFileBytes) {
      throw "operator_audit_file_limit_exceeded"
    }
    [IO.File]::AppendAllText($auditPath, $appendText, $utf8)
  }
  finally { $appendLock.Dispose() }
}

if ($Target -notmatch '^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$') {
  throw "Target must use the user@host form without whitespace."
}
Assert-RemotePath -Path $CandidateRoot
Assert-RemotePath -Path $SiteRoot

$isLifecycle = $Action -ne "Status"
if ($isLifecycle) {
  if (
    [string]::IsNullOrWhiteSpace($Reason) -or $Reason.Length -lt 8 -or
    $Reason.Length -gt 200 -or $Reason -match '[\x00-\x1f\x7f]'
  ) {
    throw "Reason must contain 8-200 characters without control characters."
  }
}
elseif ($Reason) {
  throw "Reason is accepted only for lifecycle actions."
}
if (-not $OperationId) { $OperationId = [Guid]::NewGuid().ToString("D") }
if ($OperationId -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$') {
  throw "OperationId must be a canonical UUID."
}
if ($isLifecycle -and -not $DryRun -and -not $Approved) {
  throw "A real lifecycle action requires fresh approval through -Approved."
}
if ($isLifecycle) { Write-Host "Operation ID: $OperationId" }
$operatorAuditDirectory = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "Ruisheng\audit"
if ($isLifecycle -and -not $DryRun) {
  Assert-RestrictedDirectory -Path $operatorAuditDirectory
  Assert-RestrictedFile -Path (Join-Path $operatorAuditDirectory ".remote-maintenance-audit.lock")
}

$remoteTemplate = @'
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Action = __ACTION__
$Reason = __REASON__
$OperationId = __OPERATION_ID__
$RequestedTarget = __TARGET__
$CandidateRoot = __CANDIDATE_ROOT__
$SiteRoot = __SITE_ROOT__
$LeaseSeconds = __LEASE_SECONDS__
$DryRun = __DRY_RUN__
$Approved = __APPROVED__
$script:AuditMaxFileBytes = 16 * 1024 * 1024
$script:AuditMaxLineBytes = 64 * 1024
$script:AuditMaxRecords = 50000
$script:TargetAuditSnapshot = $null

$ComposeFile = Join-Path $CandidateRoot "docker-compose.prod.yml"
$OverrideFile = Join-Path $CandidateRoot "site-network.override.yml"
$EnvFile = Join-Path $SiteRoot ".env.prod"
$ManifestFile = Join-Path $CandidateRoot "MANIFEST.json"
$SourceComposeFile = $ComposeFile
$SourceOverrideFile = $OverrideFile
$SourceEnvFile = $EnvFile
$SourceManifestFile = $ManifestFile
$StateDirectory = Join-Path $SiteRoot ".remote-maintenance-state"
$SharedLockPath = Join-Path $StateDirectory ".remote-maintenance.lock"
$LegacyLockPath = Join-Path $SiteRoot ".remote-hotfix.lock"
$AuditDirectory = "C:\Ruisheng\audit"
$AuditPath = Join-Path $AuditDirectory "remote-maintenance.jsonl"
$OperationPath = Join-Path $StateDirectory "$OperationId.json"
$VerifiedInputDirectory = Join-Path $StateDirectory "$OperationId.inputs"
$PersistentServices = @("postgres", "redis", "gw", "api", "web")
$PolicyServices = @("postgres", "redis", "migrate", "gw", "api", "web")
$StopOrder = @("web", "api", "gw", "redis", "postgres")
$ContainerNames = [ordered]@{
  postgres = "ruisheng-postgres"
  redis    = "ruisheng-redis"
  gw       = "ruisheng-gw"
  api      = "ruisheng-api"
  web      = "ruisheng-web"
}
$composeBase = @(
  "compose", "-f", $ComposeFile, "-f", $OverrideFile, "--env-file", $EnvFile
)
$ProcessStartedAt = (Get-Process -Id $PID -ErrorAction Stop).StartTime.ToUniversalTime().ToString("o")
$AcquiredLocks = New-Object System.Collections.ArrayList
$ReclaimedLocks = New-Object System.Collections.ArrayList
$auditId = ""

function Get-Sha256Text {
  param([Parameter(Mandatory)][AllowEmptyString()][string]$Text)
  $sha = [Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
    return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
  }
  finally { $sha.Dispose() }
}

function Get-RestrictedDirectorySids {
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
  $allowed = @{}
  foreach ($sid in @(Get-RestrictedDirectorySids)) { $allowed[$sid] = $false }
  $acl = Get-Acl -LiteralPath $Path
  if (-not $acl.AreAccessRulesProtected) { throw "restricted_acl_inheritance_enabled" }
  try { $ownerSid = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value }
  catch { throw "restricted_acl_owner_invalid" }
  if (-not $allowed.ContainsKey($ownerSid)) { throw "restricted_acl_owner_invalid" }
  foreach ($rule in @($acl.Access)) {
    try {
      $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
    }
    catch { throw "restricted_acl_identity_invalid" }
    if (
      $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
      -not $allowed.ContainsKey($sid) -or
      ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0 -or
      ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne
        [Security.AccessControl.FileSystemRights]::FullControl
    ) {
      throw "restricted_acl_invalid"
    }
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
    throw "restricted_file_reparse_point"
  }
  $allowed = @{}
  foreach ($sid in @(Get-RestrictedDirectorySids)) { $allowed[$sid] = $false }
  $acl = Get-Acl -LiteralPath $Path
  try { $ownerSid = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value }
  catch { throw "restricted_acl_owner_invalid" }
  if (-not $allowed.ContainsKey($ownerSid)) { throw "restricted_acl_owner_invalid" }
  foreach ($rule in @($acl.Access)) {
    try {
      $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
    }
    catch { throw "restricted_acl_identity_invalid" }
    if (
      $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
      -not $allowed.ContainsKey($sid) -or
      ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne
        [Security.AccessControl.FileSystemRights]::FullControl
    ) {
      throw "restricted_acl_invalid"
    }
    $allowed[$sid] = $true
  }
  foreach ($sid in @($allowed.Keys)) {
    if (-not $allowed[$sid]) { throw "restricted_acl_required_identity_missing" }
  }
}

function Open-ExclusiveAuditLock {
  param(
    [Parameter(Mandatory)][string]$Path,
    [ValidateRange(100, 60000)][int]$TimeoutMilliseconds = 30000
  )
  Assert-RestrictedFile -Path $Path
  $deadline = [DateTimeOffset]::UtcNow.AddMilliseconds($TimeoutMilliseconds)
  do {
    try {
      return [IO.File]::Open(
        $Path, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None
      )
    }
    catch [IO.IOException] {
      if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "audit_append_lock_missing" }
      if ([DateTimeOffset]::UtcNow -ge $deadline) { throw "audit_append_lock_timeout" }
      Start-Sleep -Milliseconds 50
    }
  } while ($true)
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
  else {
    [IO.File]::Move($temporary, $Path)
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

function Invoke-DockerText {
  param(
    [Parameter(Mandatory)][string[]]$Arguments,
    [ValidateRange(1, 900)][int]$TimeoutSeconds = 120
  )
  if ($null -ne (Get-Command docker -CommandType Function -ErrorAction SilentlyContinue)) {
    $output = & docker @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    $text = (($output | ForEach-Object { "$_" }) -join [Environment]::NewLine).Trim()
  }
  else {
    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = "docker.exe"
    $startInfo.Arguments = (@($Arguments | ForEach-Object { ConvertTo-NativeArgument -Value $_ }) -join " ")
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
      if (-not $process.Start()) { throw "docker_start_failed" }
      $stdoutTask = $process.StandardOutput.ReadToEndAsync()
      $stderrTask = $process.StandardError.ReadToEndAsync()
      if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        try { $process.Kill() } catch { }
        throw "docker_timeout"
      }
      $process.WaitForExit()
      $exitCode = $process.ExitCode
      $text = (([string]$stdoutTask.Result) + ([string]$stderrTask.Result)).Trim()
    }
    finally { $process.Dispose() }
  }
  if ($exitCode -ne 0) { throw "docker_command_failed" }
  return $text
}

function Invoke-DockerInspectText {
  param([Parameter(Mandatory)][string[]]$Arguments)
  try { return Invoke-DockerText -Arguments $Arguments }
  catch {
    if ([string]$_.Exception.Message -eq "docker_command_failed") {
      # Missing containers are expected during first start; probe existence separately.
      $container = [string]$Arguments[-1]
      $names = Invoke-DockerText -Arguments @("ps", "-a", "--format", "{{.Names}}")
      if (@($names -split "`r?`n") -notcontains $container) { return $null }
    }
    throw "service_state_unavailable"
  }
}

function Get-SshPosture {
  $settings = @{}
  $available = $false
  try {
    $connectionParts = @(([string]$env:SSH_CONNECTION).Split(" ", [StringSplitOptions]::RemoveEmptyEntries))
    if ($connectionParts.Count -lt 4 -or $connectionParts[0] -notmatch '^[0-9a-fA-F:.]+$') {
      throw "ssh_connection_unavailable"
    }
    $clientHost = $connectionParts[0]
    try { $clientHost = [Net.Dns]::GetHostEntry($connectionParts[0]).HostName }
    catch { }
    $connectionContext = @(
      "user=$env:USERNAME"
      "host=$clientHost"
      "addr=$($connectionParts[0])"
      "laddr=$($connectionParts[2])"
      "lport=$($connectionParts[3])"
    ) -join ","
    $output = & sshd.exe -T -C $connectionContext 2>$null
    if ($LASTEXITCODE -eq 0) {
      $available = $true
      foreach ($line in $output) {
        if ("$line" -match '^([^\s]+)\s+(.+)$') {
          $settings[$matches[1].ToLowerInvariant()] = $matches[2].Trim().ToLowerInvariant()
        }
      }
    }
  }
  catch { $available = $false }

  $password = if ($settings.ContainsKey("passwordauthentication")) { $settings["passwordauthentication"] } else { "unknown" }
  $keyboard = if ($settings.ContainsKey("kbdinteractiveauthentication")) { $settings["kbdinteractiveauthentication"] } else { "unknown" }
  $publicKey = if ($settings.ContainsKey("pubkeyauthentication")) { $settings["pubkeyauthentication"] } else { "unknown" }
  $methods = if ($settings.ContainsKey("authenticationmethods")) { $settings["authenticationmethods"] } else { "unknown" }
  $gssapi = if ($settings.ContainsKey("gssapiauthentication")) { $settings["gssapiauthentication"] } else { "unknown" }
  $hostBased = if ($settings.ContainsKey("hostbasedauthentication")) { $settings["hostbasedauthentication"] } else { "unknown" }
  $safe = $available -and $password -eq "no" -and $keyboard -eq "no" -and `
    $publicKey -eq "yes" -and $methods -eq "publickey" -and $gssapi -eq "no" -and $hostBased -eq "no"
  return [ordered]@{
    effective_config_available = $available
    password_authentication    = $password
    keyboard_interactive       = $keyboard
    public_key_authentication  = $publicKey
    authentication_methods     = $methods
    gssapi_authentication      = $gssapi
    hostbased_authentication   = $hostBased
    mutation_allowed           = $safe
  }
}

function Get-RemoteIdentity {
  return [ordered]@{
    user           = [string]$env:USERNAME
    computer       = [string]$env:COMPUTERNAME
    ssh_connection = [string]$env:SSH_CONNECTION
  }
}

function ConvertTo-ValidatedLockRecord {
  param(
    [Parameter(Mandatory)]$Record,
    [Parameter(Mandatory)][string]$ExpectedName
  )
  try {
    $operationId = [string]$Record.operation_id
    $action = [string]$Record.action
    $target = [string]$Record.target
    $pidValue = [int]$Record.pid
    $processStarted = [DateTimeOffset]::Parse([string]$Record.process_started_at)
    $acquired = [DateTimeOffset]::Parse([string]$Record.acquired_at)
    $expires = [DateTimeOffset]::Parse([string]$Record.expires_at)
    if (
      [int]$Record.schema_version -ne 1 -or
      [string]$Record.lock_name -ne $ExpectedName -or
      $operationId -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$' -or
      $action -notmatch '^(StopApp|StartApp|RestartApp|hotfix-(api|gw|web)|maintenance-security-prepare)$' -or
      $pidValue -le 0 -or
      $target -notmatch '^[A-Za-z0-9._-]{1,255}$' -or
      $expires -le $acquired -or
      ($expires - $acquired).TotalSeconds -gt 3600
    ) {
      throw "invalid"
    }
    return [pscustomobject]@{
      operation_id      = $operationId
      action            = $action
      target            = $target
      pid               = $pidValue
      process_started_at = [string]$Record.process_started_at
      acquired_at       = [string]$Record.acquired_at
      expires_at        = [string]$Record.expires_at
      lock_name          = $ExpectedName
      schema_version     = 1
    }
  }
  catch { throw "lock_conflict_unrecognized" }
}

function Get-LockInfo {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$Name
  )
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    return [ordered]@{ present = $false; state = "absent" }
  }
  try {
    $rawRecord = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    $record = ConvertTo-ValidatedLockRecord -Record $rawRecord -ExpectedName $Name
    $now = [DateTimeOffset]::UtcNow
    $acquired = [DateTimeOffset]::Parse([string]$record.acquired_at)
    $expires = [DateTimeOffset]::Parse([string]$record.expires_at)
    $lockState = if ($expires -gt $now) { "active" } else { "expired" }
    if ($expires -le $now) {
      try { if (Test-MatchingProcess -Record $record) { $lockState = "active" } else { $lockState = "stale" } }
      catch { $lockState = "uncertain" }
    }
    return [ordered]@{
      present      = $true
      state        = $lockState
      operation_id = [string]$record.operation_id
      action       = [string]$record.action
      age_seconds  = [Math]::Max(0, [int][Math]::Floor(($now - $acquired).TotalSeconds))
      expires_at   = [string]$record.expires_at
    }
  }
  catch {
    return [ordered]@{ present = $true; state = "unrecognized" }
  }
}

function Get-ServiceState {
  param([Parameter(Mandatory)][string]$Service)
  $container = [string]$ContainerNames[$Service]
  $inspectText = Invoke-DockerInspectText -Arguments @(
    "inspect", "--format", "{{json .State}}", $container
  )
  if ($null -eq $inspectText) {
    return [ordered]@{
      service = $Service
      exists  = $false
      running = $false
      health  = "missing"
      image   = ""
    }
  }
  try { $state = $inspectText | ConvertFrom-Json }
  catch { throw "service_state_invalid" }
  if ($null -eq $state.PSObject.Properties["Running"]) { throw "service_state_invalid" }
  $health = "none"
  if ($null -ne $state.Health) { $health = [string]$state.Health.Status }
  $image = Invoke-DockerInspectText -Arguments @(
    "inspect", "--format", "{{.Config.Image}}", $container
  )
  if ($null -eq $image -or -not $image) { throw "service_state_changed" }
  return [ordered]@{
    service = $Service
    exists  = $true
    running = [bool]$state.Running
    health  = $health
    image   = $image
  }
}

function Get-FileIdentity {
  param([Parameter(Mandatory)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return "missing" }
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-DirectoryIdentity {
  param([Parameter(Mandatory)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return "absent" }
  $entries = @(
    Get-ChildItem -LiteralPath $Path -File -ErrorAction Stop |
      Sort-Object Name |
      ForEach-Object { "$($_.Name):$($_.Length):$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)" }
  )
  return Get-Sha256Text -Text ($entries -join "|")
}

function Get-Snapshot {
  $serviceStates = @()
  foreach ($service in $PersistentServices) { $serviceStates += ,(Get-ServiceState -Service $service) }
  $auditLength = 0
  if (Test-Path -LiteralPath $AuditPath -PathType Leaf) {
    $auditLength = (Get-Item -LiteralPath $AuditPath).Length
  }
  $snapshotData = [ordered]@{
    compose_hash  = Get-FileIdentity -Path $ComposeFile
    override_hash = Get-FileIdentity -Path $OverrideFile
    env_hash      = Get-FileIdentity -Path $EnvFile
    manifest_hash = Get-FileIdentity -Path $ManifestFile
    shared_lock   = Get-FileIdentity -Path $SharedLockPath
    legacy_lock   = Get-FileIdentity -Path $LegacyLockPath
    state_hash    = Get-DirectoryIdentity -Path $StateDirectory
    audit_length  = $auditLength
    services      = $serviceStates
  }
  $json = $snapshotData | ConvertTo-Json -Depth 8 -Compress
  return [ordered]@{
    identity = Get-Sha256Text -Text $json
    data     = $snapshotData
  }
}

function Assert-RequiredFiles {
  foreach ($path in @($SourceComposeFile, $SourceOverrideFile, $SourceEnvFile, $SourceManifestFile)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "required_file_missing" }
  }
}

function Assert-DockerAvailable {
  [void](Invoke-DockerText -Arguments @("info", "--format", "{{.ServerVersion}}"))
}

function Get-ComposeModel {
  $rendered = Invoke-DockerText -Arguments ($composeBase + @("config", "--format", "json"))
  return $rendered | ConvertFrom-Json
}

function Assert-ComposePolicy {
  param([Parameter(Mandatory)]$Model)
  $serviceNames = @($Model.services.PSObject.Properties.Name)
  if (@($serviceNames | Where-Object { $_ -notin $PolicyServices }).Count -gt 0) {
    throw "compose_service_unexpected"
  }
  foreach ($service in $PolicyServices) {
    $serviceModel = $Model.services.PSObject.Properties[$service].Value
    if ($null -eq $serviceModel) { throw "compose_service_missing" }
    if ([string]$serviceModel.pull_policy -ne "never") { throw "compose_pull_policy_invalid" }
    $image = [string]$serviceModel.image
    if (-not $image -or $image -match ':latest$') { throw "mutable_image_reference" }
    foreach ($port in @($serviceModel.ports)) {
      if (
        $null -ne $port -and (
          $null -eq $port.PSObject.Properties["published"] -or
          [int]$port.published -le 0 -or
          [string]$port.host_ip -notin @("127.0.0.1", "::1")
        )
      ) {
        throw "non_loopback_port"
      }
    }
  }
}

function Test-ExactJsonObjectKeys {
  param(
    [Parameter(Mandatory)][AllowNull()]$Value,
    [Parameter(Mandatory)][string[]]$ExpectedKeys
  )
  if ($null -eq $Value -or $Value -isnot [PSCustomObject]) { return $false }
  $actualKeys = @($Value.PSObject.Properties | ForEach-Object { [string]$_.Name })
  if ($actualKeys.Count -ne $ExpectedKeys.Count) { return $false }
  foreach ($key in $ExpectedKeys) {
    if ($actualKeys -cnotcontains $key) { return $false }
  }
  return $true
}

function Assert-QualificationToolchainDescriptor {
  param([Parameter(Mandatory)][AllowNull()]$Descriptor)

  $descriptorKeys = @(
    "path", "sha256", "format", "semantic_validator", "schema", "validator",
    "producer", "receipt_producer", "toolchain_manifest"
  )
  if (-not (Test-ExactJsonObjectKeys -Value $Descriptor -ExpectedKeys $descriptorKeys)) {
    throw "manifest_schema_invalid"
  }
  if (
    $Descriptor.path -isnot [string] -or
    $Descriptor.path -cne "qualification-toolchain.tar.gz" -or
    $Descriptor.sha256 -isnot [string] -or
    $Descriptor.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
    $Descriptor.format -isnot [string] -or
    $Descriptor.format -cne "tar+gzip" -or
    $Descriptor.semantic_validator -isnot [string] -or
    $Descriptor.semantic_validator -cne "ruisheng.device-point-profile-validator/v5"
  ) {
    throw "manifest_schema_invalid"
  }

  $identityPaths = [ordered]@{
    schema = "schemas/point-profile/point-profile-v1.schema.json"
    validator = "tools/validate_device_point_profile.py"
    producer = "tools/release_artifacts.py"
    receipt_producer = "tools/release_verification_receipt.py"
    toolchain_manifest = "qualification-toolchain-manifest.json"
  }
  foreach ($name in $identityPaths.Keys) {
    $identity = $Descriptor.$name
    if (
      -not (Test-ExactJsonObjectKeys -Value $identity -ExpectedKeys @("path", "sha256")) -or
      $identity.path -isnot [string] -or
      $identity.path -cne $identityPaths[$name] -or
      $identity.sha256 -isnot [string] -or
      $identity.sha256 -cnotmatch '^[0-9a-f]{64}$'
    ) {
      throw "manifest_schema_invalid"
    }
  }
}

function Assert-CandidateManifestSchema {
  param([Parameter(Mandatory)]$Manifest)
  $baseKeys = @(
    "schema_version", "candidate_id", "source_commit", "generated_at", "target_os",
    "target_architecture", "alembic_head", "logical_identity", "tools", "authenticity",
    "images"
  )
  $isInteger =
    $Manifest.schema_version -isnot [bool] -and (
      $Manifest.schema_version -is [int] -or $Manifest.schema_version -is [long]
    )
  if (-not $isInteger) { throw "manifest_schema_invalid" }
  $schemaVersion = [int64]$Manifest.schema_version
  if ($schemaVersion -eq 2) {
    $expectedKeys = $baseKeys
  }
  elseif ($schemaVersion -eq 3) {
    $expectedKeys = @($baseKeys) + "qualification_toolchain"
  }
  else {
    throw "manifest_schema_invalid"
  }
  if (-not (Test-ExactJsonObjectKeys -Value $Manifest -ExpectedKeys $expectedKeys)) {
    throw "manifest_schema_invalid"
  }
  if ($schemaVersion -eq 3) {
    Assert-QualificationToolchainDescriptor -Descriptor $Manifest.qualification_toolchain
  }
  if ($Manifest.images -isnot [Array]) { throw "manifest_schema_invalid" }
  $imageKeys = @(
    "component", "source_reference", "repo_digest", "candidate_reference", "image_id",
    "os", "architecture", "archive", "sha256"
  )
  foreach ($image in @($Manifest.images)) {
    if (-not (Test-ExactJsonObjectKeys -Value $image -ExpectedKeys $imageKeys)) {
      throw "manifest_schema_invalid"
    }
  }
}

function Assert-ManifestImageIdentity {
  param([Parameter(Mandatory)]$Model)
  try { $manifest = Get-Content -LiteralPath $ManifestFile -Raw -Encoding UTF8 -ErrorAction Stop | ConvertFrom-Json }
  catch { throw "manifest_invalid" }
  Assert-CandidateManifestSchema -Manifest $manifest
  if (
    $manifest.candidate_id -isnot [string] -or
    [string]$manifest.candidate_id -notmatch '^[a-z0-9][a-z0-9._-]{0,62}$' -or
    [string]$manifest.candidate_id -ne (Split-Path -Leaf $CandidateRoot) -or
    $manifest.source_commit -isnot [string] -or
    [string]$manifest.source_commit -notmatch '^[0-9a-f]{40}$' -or
    $manifest.logical_identity -isnot [string] -or
    [string]$manifest.logical_identity -notmatch '^sha256:[0-9a-f]{64}$'
  ) {
    throw "manifest_identity_invalid"
  }
  $manifestImages = @{}
  foreach ($image in @($manifest.images)) {
    $component = [string]$image.component
    if ($component -notin $PersistentServices -or $manifestImages.ContainsKey($component)) {
      throw "manifest_images_invalid"
    }
    if (
      [string]$image.candidate_reference -notmatch '^[^\s]+$' -or
      [string]$image.image_id -notmatch '^sha256:[0-9a-f]{64}$'
    ) {
      throw "manifest_images_invalid"
    }
    $manifestImages[$component] = $image
  }
  if ($manifestImages.Count -ne $PersistentServices.Count) { throw "manifest_images_invalid" }

  foreach ($service in $PolicyServices) {
    $component = if ($service -eq "migrate") { "api" } else { $service }
    $manifestImage = $manifestImages[$component]
    $composeReference = [string]$Model.services.PSObject.Properties[$service].Value.image
    if ($composeReference -ne [string]$manifestImage.candidate_reference) {
      throw "compose_manifest_image_mismatch"
    }
    $actualId = Invoke-DockerText -Arguments @("image", "inspect", "--format", "{{.Id}}", $composeReference)
    if ($actualId -ne [string]$manifestImage.image_id) { throw "loaded_image_identity_mismatch" }
  }
}

function Get-ConfigHashes {
  return [ordered]@{
    compose  = Get-FileIdentity -Path $SourceComposeFile
    override = Get-FileIdentity -Path $SourceOverrideFile
    env      = Get-FileIdentity -Path $SourceEnvFile
    manifest = Get-FileIdentity -Path $SourceManifestFile
  }
}

function Remove-VerifiedInputs {
  if (-not (Test-Path -LiteralPath $VerifiedInputDirectory -PathType Container)) { return }
  foreach ($name in @("docker-compose.prod.yml", "site-network.override.yml", ".env.prod", "MANIFEST.json")) {
    Remove-Item -LiteralPath (Join-Path $VerifiedInputDirectory $name) -Force -ErrorAction SilentlyContinue
  }
  Remove-Item -LiteralPath $VerifiedInputDirectory -Force -ErrorAction SilentlyContinue
}

function Initialize-VerifiedInputs {
  param([Parameter(Mandatory)]$ExpectedHashes)
  if (Test-Path -LiteralPath $VerifiedInputDirectory) { throw "verified_inputs_conflict" }
  New-Item -ItemType Directory -Path $VerifiedInputDirectory | Out-Null
  try {
    $copies = [ordered]@{
      compose  = @($SourceComposeFile, (Join-Path $VerifiedInputDirectory "docker-compose.prod.yml"))
      override = @($SourceOverrideFile, (Join-Path $VerifiedInputDirectory "site-network.override.yml"))
      env      = @($SourceEnvFile, (Join-Path $VerifiedInputDirectory ".env.prod"))
      manifest = @($SourceManifestFile, (Join-Path $VerifiedInputDirectory "MANIFEST.json"))
    }
    foreach ($name in @($copies.Keys)) {
      Copy-Item -LiteralPath $copies[$name][0] -Destination $copies[$name][1]
      if ((Get-FileIdentity -Path $copies[$name][1]) -ne [string]$ExpectedHashes[$name]) {
        throw "configuration_drift"
      }
    }
    Assert-NoDrift -Expected $ExpectedHashes
    Set-Variable -Name ComposeFile -Value $copies.compose[1] -Scope 1
    Set-Variable -Name OverrideFile -Value $copies.override[1] -Scope 1
    Set-Variable -Name EnvFile -Value $copies.env[1] -Scope 1
    Set-Variable -Name ManifestFile -Value $copies.manifest[1] -Scope 1
    Set-Variable -Name composeBase -Scope 1 -Value @(
      "compose", "-f", $copies.compose[1], "-f", $copies.override[1],
      "--env-file", $copies.env[1]
    )
    $verifiedModel = Get-ComposeModel
    Assert-ComposePolicy -Model $verifiedModel
    Assert-ManifestImageIdentity -Model $verifiedModel
  }
  catch {
    Remove-VerifiedInputs
    throw
  }
}

function Assert-NoDrift {
  param([Parameter(Mandatory)]$Expected)
  $actual = Get-ConfigHashes
  foreach ($name in @("compose", "override", "env", "manifest")) {
    if ([string]$actual[$name] -ne [string]$Expected[$name]) { throw "configuration_drift" }
  }
  $currentModel = Get-ComposeModel
  Assert-ComposePolicy -Model $currentModel
  Assert-ManifestImageIdentity -Model $currentModel
}

function Test-MatchingProcess {
  param([Parameter(Mandatory)]$Record)
  try {
    $process = Get-Process -Id ([int]$Record.pid) -ErrorAction Stop
  }
  catch [Microsoft.PowerShell.Commands.ProcessCommandException] { return $false }
  catch { throw "lock_owner_uncertain" }
  try {
    $actualStart = $process.StartTime.ToUniversalTime()
    $recordedStart = [DateTimeOffset]::Parse([string]$Record.process_started_at).UtcDateTime
    return [Math]::Abs(($actualStart - $recordedStart).TotalSeconds) -lt 1
  }
  catch { throw "lock_owner_uncertain" }
}

function New-LockRecord {
  param([Parameter(Mandatory)][string]$Name)
  return [ordered]@{
    schema_version     = 1
    lock_name          = $Name
    operation_id      = $OperationId
    action            = $Action
    pid               = $PID
    process_started_at = $ProcessStartedAt
    target            = [string]$env:COMPUTERNAME
    acquired_at       = [DateTimeOffset]::UtcNow.ToString("o")
    expires_at        = [DateTimeOffset]::UtcNow.AddSeconds($LeaseSeconds).ToString("o")
  }
}

function Acquire-LeasedLock {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Name)
  for ($attempt = 0; $attempt -lt 2; $attempt++) {
    $stream = $null
    try {
      $record = New-LockRecord -Name $Name
      $json = $record | ConvertTo-Json -Depth 4 -Compress
      $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes($json)
      $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
      $stream.Write($bytes, 0, $bytes.Length)
      $stream.Flush()
      $stream.Dispose()
      $stream = $null
      [void]$AcquiredLocks.Add([ordered]@{ path = $Path; name = $Name })
      return
    }
    catch {
      if ($null -ne $stream) { $stream.Dispose() }
      if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "lock_acquire_failed" }
      try {
        $rawExisting = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
        $existing = ConvertTo-ValidatedLockRecord -Record $rawExisting -ExpectedName $Name
      }
      catch { throw "lock_conflict_unrecognized" }
      try { $expired = [DateTimeOffset]::Parse([string]$existing.expires_at) -lt [DateTimeOffset]::UtcNow }
      catch { throw "lock_conflict_unrecognized" }
      if (-not $expired -or (Test-MatchingProcess -Record $existing)) { throw "lock_conflict_active" }
      $tombstone = "$Path.stale.$OperationId.$([Guid]::NewGuid().ToString('N'))"
      try { [IO.File]::Move($Path, $tombstone) }
      catch { throw "lock_conflict_race" }
      [void]$ReclaimedLocks.Add([ordered]@{
        lock_name = $Name
        owner     = [string]$existing.operation_id
        action    = [string]$existing.action
      })
    }
    finally {
      if ($null -ne $stream) { $stream.Dispose() }
    }
  }
  throw "lock_acquire_failed"
}

function Renew-Locks {
  foreach ($held in @($AcquiredLocks)) {
    try {
      $rawRecord = Get-Content -LiteralPath $held.path -Raw | ConvertFrom-Json
      $record = ConvertTo-ValidatedLockRecord -Record $rawRecord -ExpectedName $held.name
    }
    catch { throw "lock_ownership_lost" }
    if ([string]$record.operation_id -ne $OperationId -or [string]$record.process_started_at -ne $ProcessStartedAt) {
      throw "lock_ownership_lost"
    }
    $record.expires_at = [DateTimeOffset]::UtcNow.AddSeconds($LeaseSeconds).ToString("o")
    Write-JsonAtomic -Path $held.path -Value $record
  }
}

function Release-Locks {
  $heldLocks = @($AcquiredLocks)
  [array]::Reverse($heldLocks)
  foreach ($held in $heldLocks) {
    try {
      $rawRecord = Get-Content -LiteralPath $held.path -Raw | ConvertFrom-Json
      $record = ConvertTo-ValidatedLockRecord -Record $rawRecord -ExpectedName $held.name
      if ([string]$record.operation_id -eq $OperationId -and [string]$record.process_started_at -eq $ProcessStartedAt) {
        Remove-Item -LiteralPath $held.path -Force
      }
    }
    catch { }
  }
}

function Read-TargetAuditSnapshot {
  $previousHash = "0" * 64
  $records = New-Object System.Collections.ArrayList
  $auditLength = 0L
  $lastWriteTicks = 0L
  $contentSha256 = ""
  $auditExists = $false
  if (Test-Path -LiteralPath $AuditPath -PathType Leaf) {
    Assert-RestrictedFile -Path $AuditPath
    $beforeItem = Get-Item -LiteralPath $AuditPath -Force
    $auditExists = $true
    $auditLength = [long]$beforeItem.Length
    $lastWriteTicks = [long]$beforeItem.LastWriteTimeUtc.Ticks
    if ($auditLength -gt $script:AuditMaxFileBytes) {
      throw "audit_file_limit_exceeded"
    }
    $recordCount = 0
    foreach ($existingLine in Get-Content -LiteralPath $AuditPath -Encoding UTF8 -ErrorAction Stop) {
      if (-not $existingLine) { continue }
      if ([Text.Encoding]::UTF8.GetByteCount($existingLine) -gt $script:AuditMaxLineBytes) {
        throw "audit_line_limit_exceeded"
      }
      $recordCount++
      if ($recordCount -gt $script:AuditMaxRecords) {
        throw "audit_record_limit_exceeded"
      }
      try {
        $previous = $existingLine | ConvertFrom-Json
        if ([string]$previous.previous_hash -ne $previousHash) { throw "invalid" }
        $verifiedPayload = [ordered]@{}
        foreach ($property in $previous.PSObject.Properties) {
          if ($property.Name -ne "record_hash") {
            $verifiedPayload[$property.Name] = $property.Value
          }
        }
        $verifiedJson = $verifiedPayload | ConvertTo-Json -Depth 6 -Compress
        $verifiedHash = Get-Sha256Text -Text $verifiedJson
        if ($verifiedHash -ne [string]$previous.record_hash) { throw "invalid" }
        $previousHash = $verifiedHash
        [void]$records.Add($previous)
      }
      catch { throw "audit_chain_invalid" }
    }
    Assert-RestrictedFile -Path $AuditPath
    $afterItem = Get-Item -LiteralPath $AuditPath -Force
    if (
      [long]$afterItem.Length -ne $auditLength -or
      [long]$afterItem.LastWriteTimeUtc.Ticks -ne $lastWriteTicks
    ) {
      throw "audit_chain_changed"
    }
    $contentSha256 = (Get-FileHash -LiteralPath $AuditPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Assert-RestrictedFile -Path $AuditPath
    $hashedItem = Get-Item -LiteralPath $AuditPath -Force
    if (
      [long]$hashedItem.Length -ne $auditLength -or
      [long]$hashedItem.LastWriteTimeUtc.Ticks -ne $lastWriteTicks
    ) {
      throw "audit_chain_changed"
    }
  }
  return [pscustomobject]@{
    TailHash          = $previousHash
    Records           = @($records)
    RecordCount       = $records.Count
    FileLength        = $auditLength
    LastWriteUtcTicks = $lastWriteTicks
    ContentSha256     = $contentSha256
    Exists            = $auditExists
  }
}

function Get-TargetAuditSnapshot {
  param([switch]$ForceRefresh)
  if (-not $ForceRefresh -and $null -ne $script:TargetAuditSnapshot) {
    $exists = Test-Path -LiteralPath $AuditPath -PathType Leaf
    if (-not $exists -and -not [bool]$script:TargetAuditSnapshot.Exists) {
      return $script:TargetAuditSnapshot
    }
    if ($exists -and [bool]$script:TargetAuditSnapshot.Exists) {
      Assert-RestrictedFile -Path $AuditPath
      $item = Get-Item -LiteralPath $AuditPath -Force
      if (
        [long]$item.Length -eq [long]$script:TargetAuditSnapshot.FileLength -and
        [long]$item.LastWriteTimeUtc.Ticks -eq [long]$script:TargetAuditSnapshot.LastWriteUtcTicks -and
        (Get-FileHash -LiteralPath $AuditPath -Algorithm SHA256).Hash.ToLowerInvariant() -ceq
          [string]$script:TargetAuditSnapshot.ContentSha256
      ) {
        return $script:TargetAuditSnapshot
      }
    }
  }
  $script:TargetAuditSnapshot = Read-TargetAuditSnapshot
  return $script:TargetAuditSnapshot
}

function Assert-TargetAuditChain {
  $appendLock = Open-ExclusiveAuditLock -Path (Join-Path $AuditDirectory ".remote-maintenance-audit.lock")
  try { [void](Get-TargetAuditSnapshot -ForceRefresh) }
  finally { $appendLock.Dispose() }
}

function Assert-OperationAuditCorrelation {
  param([Parameter(Mandatory)]$Record)
  Assert-TargetAuditChain
  $matched = $false
  foreach ($entry in @($script:TargetAuditSnapshot.Records)) {
    if (
      [string]$entry.operation_id -eq [string]$Record.operation_id -and
      [string]$entry.audit_id -eq [string]$Record.audit_id -and
      [string]$entry.result -eq [string]$Record.status -and
      [string]$entry.event -in @("lifecycle_completed", "lifecycle_rejected")
    ) {
      $matched = $true
    }
  }
  if (-not $matched) { throw "operation_audit_correlation_missing" }
}

function Write-TargetAudit {
  param(
    [Parameter(Mandatory)][string]$Event,
    [Parameter(Mandatory)][string]$Result,
    [string[]]$Stopped = @(),
    [string[]]$Started = @(),
    [string[]]$Remaining = @(),
    [string]$ErrorCode = ""
  )
  $appendLock = Open-ExclusiveAuditLock -Path (Join-Path $AuditDirectory ".remote-maintenance-audit.lock")
  try {
    $snapshot = Get-TargetAuditSnapshot
    $previousHash = [string]$snapshot.TailHash
    if (-not $auditId) { $script:auditId = [Guid]::NewGuid().ToString("D") }
    $identity = Get-RemoteIdentity
    $payload = [ordered]@{
      schema_version  = 1
      audit_id        = $auditId
      operation_id    = $OperationId
      recorded_at     = [DateTimeOffset]::UtcNow.ToString("o")
      event           = $Event
      action          = $Action
      result          = $Result
      reason          = $Reason
      target          = $RequestedTarget
      remote_user     = $identity.user
      remote_computer = $identity.computer
      ssh_connection  = $identity.ssh_connection
      stopped         = @($Stopped)
      started         = @($Started)
      remaining       = @($Remaining)
      error_code      = $ErrorCode
      previous_hash   = $previousHash
    }
    $payloadJson = $payload | ConvertTo-Json -Depth 6 -Compress
    $recordHash = Get-Sha256Text -Text $payloadJson
    $record = [ordered]@{}
    foreach ($property in $payload.GetEnumerator()) { $record[$property.Key] = $property.Value }
    $record.record_hash = $recordHash
    $line = $record | ConvertTo-Json -Depth 6 -Compress
    $utf8 = New-Object Text.UTF8Encoding($false)
    $appendText = $line + [Environment]::NewLine
    $lineBytes = [Text.Encoding]::UTF8.GetByteCount($line)
    $appendBytes = [Text.Encoding]::UTF8.GetByteCount($appendText)
    if ($lineBytes -gt $script:AuditMaxLineBytes) { throw "audit_line_limit_exceeded" }
    if ([int]$snapshot.RecordCount -ge $script:AuditMaxRecords) {
      throw "audit_record_limit_exceeded"
    }
    if ([long]$snapshot.FileLength + $appendBytes -gt $script:AuditMaxFileBytes) {
      throw "audit_file_limit_exceeded"
    }
    [IO.File]::AppendAllText($AuditPath, $appendText, $utf8)
    Assert-RestrictedFile -Path $AuditPath
    $updatedItem = Get-Item -LiteralPath $AuditPath -Force
    $script:TargetAuditSnapshot = [pscustomobject]@{
      TailHash          = $recordHash
      Records           = @($snapshot.Records) + @([pscustomobject]$record)
      RecordCount       = [int]$snapshot.RecordCount + 1
      FileLength        = [long]$updatedItem.Length
      LastWriteUtcTicks = [long]$updatedItem.LastWriteTimeUtc.Ticks
      ContentSha256     = (Get-FileHash -LiteralPath $AuditPath -Algorithm SHA256).Hash.ToLowerInvariant()
      Exists            = $true
    }
  }
  finally { $appendLock.Dispose() }
}

function Read-ExistingOperation {
  if (-not (Test-Path -LiteralPath $OperationPath -PathType Leaf)) { return $null }
  Assert-RestrictedFile -Path $OperationPath
  try {
    $record = Get-Content -LiteralPath $OperationPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
      [int]$record.schema_version -ne 1 -or
      [string]$record.operation_id -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$' -or
      [string]$record.action -notmatch '^(StopApp|StartApp|RestartApp)$' -or
      [string]$record.target -notmatch '^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$' -or
      [string]$record.candidate_id -notmatch '^[A-Za-z0-9._-]{1,255}$' -or
      [string]$record.reason_hash -notmatch '^[0-9a-f]{64}$' -or
      [string]$record.status -notmatch '^(executing|succeeded|failed|partial|rejected|uncertain)$' -or
      $record.ok -isnot [bool] -or
      [string]$record.audit_id -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$'
    ) { throw "invalid" }
    [void][DateTimeOffset]::Parse([string]$record.started_at)
    if ([string]$record.status -ne "executing") {
      [void][DateTimeOffset]::Parse([string]$record.completed_at)
    }
    foreach ($name in @("stopped", "started", "remaining")) {
      $seen = @{}
      foreach ($service in @($record.$name)) {
        if ([string]$service -notin $PersistentServices -or $seen.ContainsKey([string]$service)) {
          throw "invalid"
        }
        $seen[[string]$service] = $true
      }
    }
    return $record
  }
  catch { throw "operation_state_invalid" }
}

function Assert-OperationIdentity {
  param([Parameter(Mandatory)]$Record, [Parameter(Mandatory)][string]$ReasonHash)
  if (
    [string]$Record.action -ne $Action -or
    [string]$Record.target -ne $RequestedTarget -or
    [string]$Record.candidate_id -ne (Split-Path -Leaf $CandidateRoot) -or
    [string]$Record.reason_hash -ne $ReasonHash
  ) {
    throw "operation_identity_conflict"
  }
}

function Find-TerminalOperationAudit {
  param([Parameter(Mandatory)]$Record)
  $matched = $null
  if ($null -eq $script:TargetAuditSnapshot) { Assert-TargetAuditChain }
  foreach ($entry in @($script:TargetAuditSnapshot.Records)) {
    if (
      [string]$entry.operation_id -eq [string]$Record.operation_id -and
      [string]$entry.audit_id -eq [string]$Record.audit_id -and
      [string]$entry.event -eq "lifecycle_completed" -and
      [string]$entry.result -in @("succeeded", "failed", "partial", "uncertain")
    ) {
      if ($null -ne $matched -and [string]$matched.result -ne [string]$entry.result) {
        throw "operation_audit_conflict"
      }
      $matched = $entry
    }
  }
  return $matched
}

function Resolve-InterruptedOperation {
  param([Parameter(Mandatory)]$Record)
  $script:auditId = [string]$Record.audit_id
  $terminal = Find-TerminalOperationAudit -Record $Record
  try { $liveServices = Get-HealthResult }
  catch { $liveServices = @() }
  if ($null -ne $terminal) {
    $Record.status = [string]$terminal.result
    $Record.ok = [string]$terminal.result -eq "succeeded"
    $Record.stopped = @($terminal.stopped)
    $Record.started = @($terminal.started)
    $Record.remaining = @($terminal.remaining)
    $Record.error_code = [string]$terminal.error_code
    $Record.recovery_hint = if ($Record.ok) { "" } else {
      "Inspect status, then use a new operation identity for the approved recovery action."
    }
    $Record | Add-Member -NotePropertyName completed_at `
      -NotePropertyValue ([string]$terminal.recorded_at) -Force
  }
  else {
    $Record.status = "uncertain"
    $Record.ok = $false
    $Record.error_code = "operation_result_uncertain"
    $Record.recovery_hint = "Inspect status, then use a new operation identity for the approved recovery action."
    $Record | Add-Member -NotePropertyName completed_at `
      -NotePropertyValue ([DateTimeOffset]::UtcNow.ToString("o")) -Force
    Write-TargetAudit -Event "lifecycle_completed" -Result "uncertain" `
      -Stopped @($Record.stopped) -Started @($Record.started) `
      -Remaining @($Record.remaining) -ErrorCode "operation_result_uncertain"
  }
  $Record.services = @($liveServices)
  Write-JsonAtomic -Path $OperationPath -Value $Record
  return $Record
}

function Get-HealthResult {
  param([switch]$ActiveProbe)
  $services = @()
  foreach ($service in $PersistentServices) {
    $state = Get-ServiceState -Service $service
    $ready = $state.exists -and $state.running
    if ($service -in @("postgres", "redis") -and $state.health -ne "healthy") { $ready = $false }
    if ($ActiveProbe -and $service -eq "api" -and $ready) {
      try {
        [void](Invoke-DockerText -Arguments @(
          "exec", "ruisheng-api", "python", "-c",
          "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready', timeout=5).read(1)"
        ))
      }
      catch { $ready = $false }
    }
    if ($ActiveProbe -and $service -eq "gw" -and $ready) {
      try {
        [void](Invoke-DockerText -Arguments @(
          "exec", "ruisheng-gw", "python", "-c",
          "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9090/ready', timeout=5).read(1)"
        ))
      }
      catch { $ready = $false }
    }
    if ($ActiveProbe -and $service -eq "web" -and $ready) {
      try {
        [void](Invoke-DockerText -Arguments @(
          "exec", "ruisheng-web", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1/"
        ))
      }
      catch { $ready = $false }
    }
    $services += ,[ordered]@{
      service = $service
      running = [bool]$state.running
      health  = [string]$state.health
      image   = [string]$state.image
      ready   = [bool]$ready
    }
  }
  return $services
}

function Wait-AllHealthy {
  param(
    [Parameter(Mandatory)]$ExpectedHashes,
    [ValidateRange(1, 300)][int]$TimeoutSeconds = 120
  )
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
  do {
    Assert-NoDrift -Expected $ExpectedHashes
    $health = Get-HealthResult -ActiveProbe
    if (@($health | Where-Object { -not $_.ready }).Count -eq 0) { return $health }
    Renew-Locks
    Start-Sleep -Seconds 2
  } while ([DateTimeOffset]::UtcNow -lt $deadline)
  throw "service_health_failed"
}

function New-SafeResult {
  param(
    [Parameter(Mandatory)][bool]$Ok,
    [Parameter(Mandatory)][string]$Status,
    [Parameter(Mandatory)]$Identity,
    [Parameter(Mandatory)]$Posture,
    [string]$ErrorCode = "",
    [string[]]$Stopped = @(),
    [string[]]$Started = @(),
    [string[]]$Remaining = @(),
    [string]$RecoveryHint = "",
    $Services = @(),
    $Locks = $null,
    $Plan = $null,
    $Preflight = $null,
    [string]$SnapshotBefore = "",
    [string]$SnapshotAfter = ""
  )
  return [ordered]@{
    schema_version  = 1
    ok              = $Ok
    status          = $Status
    operation_id    = $OperationId
    audit_id        = $auditId
    action          = $Action
    site            = (Split-Path -Leaf $CandidateRoot)
    identity        = $Identity
    ssh_posture     = $Posture
    locks           = $Locks
    services        = @($Services)
    plan            = $Plan
    preflight       = $Preflight
    stopped         = @($Stopped)
    started         = @($Started)
    remaining       = @($Remaining)
    recovery_hint   = $RecoveryHint
    error_code      = $ErrorCode
    snapshot_before = $SnapshotBefore
    snapshot_after  = $SnapshotAfter
    snapshot_equal  = ($SnapshotBefore -and $SnapshotBefore -eq $SnapshotAfter)
  }
}

function Get-AllowlistedPlan {
  $plannedStopOrder = @()
  $startPhases = @()
  $runsMigration = $false
  if ($Action -in @("StopApp", "RestartApp")) { $plannedStopOrder = @($StopOrder) }
  if ($Action -in @("StartApp", "RestartApp")) {
    $startPhases = @(
      [ordered]@{ phase = "dependencies"; services = @("postgres", "redis") }
      [ordered]@{ phase = "migration"; services = @("migrate") }
      [ordered]@{ phase = "applications"; services = @("gw", "api", "web") }
      [ordered]@{ phase = "health"; services = @($PersistentServices) }
    )
    $runsMigration = $true
  }
  return [ordered]@{
    stop_order       = @($plannedStopOrder)
    start_phases     = @($startPhases)
    runs_migration   = $runsMigration
    preserves_volumes = $true
    mutation_allowed = [bool]$posture.mutation_allowed -and -not $preflightError
  }
}

$identity = Get-RemoteIdentity
$posture = Get-SshPosture
$locks = [ordered]@{
  shared = Get-LockInfo -Path $SharedLockPath -Name "shared-maintenance"
  legacy = Get-LockInfo -Path $LegacyLockPath -Name "legacy-hotfix"
}
$auditReady = $false
$operationStarted = $false
$reasonHash = ""

try {
  if ($Action -ne "Status" -and -not $DryRun) {
    $reasonHash = Get-Sha256Text -Text $Reason
    if (Test-Path -LiteralPath $StateDirectory -PathType Container) {
      Assert-RestrictedDirectory -Path $StateDirectory
    }
    $existingOperation = Read-ExistingOperation
    if ($null -ne $existingOperation) {
      Assert-OperationIdentity -Record $existingOperation -ReasonHash $reasonHash
      if ([string]$existingOperation.status -eq "executing") {
        # Reconciliation requires the maintenance lock and protected target audit.
      }
      else {
        Assert-RestrictedDirectory -Path $AuditDirectory
        Assert-OperationAuditCorrelation -Record $existingOperation
        $result = New-SafeResult -Ok ([bool]$existingOperation.ok) -Status ([string]$existingOperation.status) `
          -Identity $identity -Posture $posture -ErrorCode ([string]$existingOperation.error_code) `
          -Stopped @($existingOperation.stopped) -Started @($existingOperation.started) `
          -Remaining @($existingOperation.remaining) `
          -RecoveryHint ([string]$existingOperation.recovery_hint) `
          -Services @($existingOperation.services) -Locks $locks
        $result.audit_id = [string]$existingOperation.audit_id
        Write-Output ($result | ConvertTo-Json -Depth 10 -Compress)
        exit 0
      }
    }
  }

  if ($Action -eq "Status" -or $DryRun) {
    Assert-RequiredFiles
    Assert-DockerAvailable
    $preflightError = ""
    try {
      $model = Get-ComposeModel
      Assert-ComposePolicy -Model $model
      Assert-ManifestImageIdentity -Model $model
    }
    catch {
      $preflightError = [string]$_.Exception.Message
      if ($preflightError -notmatch '^[a-z0-9_]+$') { $preflightError = "maintenance_preflight_failed" }
    }
    $preflight = [ordered]@{ ok = -not [bool]$preflightError; error_code = $preflightError }
    $before = Get-Snapshot

    if ($Action -eq "Status") {
      $health = Get-HealthResult -ActiveProbe
      $result = New-SafeResult -Ok $true -Status "observed" -Identity $identity -Posture $posture `
        -Services $health -Locks $locks -Preflight $preflight `
        -SnapshotBefore $before.identity -SnapshotAfter $before.identity
      Write-Output ($result | ConvertTo-Json -Depth 10 -Compress)
      exit 0
    }

    $health = Get-HealthResult -ActiveProbe
    $after = Get-Snapshot
    if ($before.identity -ne $after.identity) { throw "dry_run_state_changed" }
    $status = "planned"
    $errorCode = ""
    if (-not $posture.mutation_allowed) {
      $status = "security_blocked"
      $errorCode = "ssh_not_key_only"
    }
    elseif ($preflightError) {
      $status = "preflight_blocked"
      $errorCode = $preflightError
    }
    $result = New-SafeResult -Ok $true -Status $status -Identity $identity -Posture $posture `
      -Services $health -Locks $locks -Plan (Get-AllowlistedPlan) -Preflight $preflight `
      -ErrorCode $errorCode `
      -SnapshotBefore $before.identity -SnapshotAfter $after.identity
    Write-Output ($result | ConvertTo-Json -Depth 10 -Compress)
    exit 0
  }

  if (-not $Approved) { throw "approval_required" }
  if (-not $posture.mutation_allowed) { throw "ssh_not_key_only" }

  Assert-RestrictedDirectory -Path $SiteRoot
  Assert-RestrictedDirectory -Path $StateDirectory
  Assert-RestrictedDirectory -Path $AuditDirectory
  Assert-TargetAuditChain
  Acquire-LeasedLock -Path $SharedLockPath -Name "shared-maintenance"
  try {
    Acquire-LeasedLock -Path $LegacyLockPath -Name "legacy-hotfix"
  }
  catch {
    Release-Locks
    throw
  }
  $auditReady = $true

  $existingOperation = Read-ExistingOperation
  if ($null -ne $existingOperation) {
    Assert-OperationIdentity -Record $existingOperation -ReasonHash $reasonHash
    if ([string]$existingOperation.status -eq "executing") {
      $existingOperation = Resolve-InterruptedOperation -Record $existingOperation
    }
    if ([string]$existingOperation.status -in @("succeeded", "failed", "partial", "rejected", "uncertain")) {
      Assert-OperationAuditCorrelation -Record $existingOperation
      $result = New-SafeResult -Ok ([bool]$existingOperation.ok) -Status ([string]$existingOperation.status) `
        -Identity $identity -Posture $posture -ErrorCode ([string]$existingOperation.error_code) `
        -Stopped @($existingOperation.stopped) -Started @($existingOperation.started) `
        -Remaining @($existingOperation.remaining) `
        -RecoveryHint ([string]$existingOperation.recovery_hint) `
        -Services @($existingOperation.services) -Locks $locks
      $result.audit_id = [string]$existingOperation.audit_id
      Release-Locks
      Write-Output ($result | ConvertTo-Json -Depth 10 -Compress)
      exit 0
    }
    throw "operation_state_invalid"
  }

  Assert-RequiredFiles
  Assert-DockerAvailable
  $model = Get-ComposeModel
  Assert-ComposePolicy -Model $model
  Assert-ManifestImageIdentity -Model $model
  $configHashes = Get-ConfigHashes
  $auditId = [Guid]::NewGuid().ToString("D")
  Initialize-VerifiedInputs -ExpectedHashes $configHashes
  $operationRecord = [ordered]@{
    schema_version = 1
    operation_id   = $OperationId
    action         = $Action
    target         = $RequestedTarget
    candidate_id   = (Split-Path -Leaf $CandidateRoot)
    reason_hash    = $reasonHash
    status         = "executing"
    ok             = $false
    audit_id       = $auditId
    started_at     = [DateTimeOffset]::UtcNow.ToString("o")
    stopped        = @()
    started        = @()
    remaining      = @()
    recovery_hint  = ""
    error_code     = ""
    services       = @()
  }
  Write-JsonAtomic -Path $OperationPath -Value $operationRecord
  $operationStarted = $true
  foreach ($reclaimed in @($ReclaimedLocks)) {
    Write-TargetAudit -Event "stale_lock_reclaimed" -Result "reclaimed" -ErrorCode ([string]$reclaimed.lock_name)
  }
  Write-TargetAudit -Event "lifecycle_started" -Result "executing"

  $stopped = New-Object System.Collections.ArrayList
  $started = New-Object System.Collections.ArrayList
  $remaining = New-Object System.Collections.ArrayList
  try {
    if ($Action -in @("StopApp", "RestartApp")) {
      foreach ($service in $StopOrder) {
        Assert-NoDrift -Expected $configHashes
        Renew-Locks
        try {
          [void](Invoke-DockerText -Arguments ($composeBase + @("stop", $service)))
          if ((Get-ServiceState -Service $service).running) { throw "service_stop_verification_failed" }
          [void]$stopped.Add($service)
          Assert-NoDrift -Expected $configHashes
        }
        catch {
          $stopError = [string]$_.Exception.Message
          foreach ($candidate in $StopOrder) {
            if ($stopped -notcontains $candidate) { [void]$remaining.Add($candidate) }
          }
          if ($stopError -match '^[a-z0-9_]+$') { throw $stopError }
          throw "partial_stop"
        }
      }
    }

    $health = @()
    if ($Action -in @("StartApp", "RestartApp")) {
      Assert-NoDrift -Expected $configHashes
      Renew-Locks
      [void](Invoke-DockerText -Arguments ($composeBase + @("up", "-d", "postgres", "redis")))
      [void]$started.Add("postgres")
      [void]$started.Add("redis")
      $dependencyDeadline = [DateTimeOffset]::UtcNow.AddSeconds(90)
      do {
        $dependencyHealth = Get-HealthResult | Where-Object { $_.service -in @("postgres", "redis") }
        if (@($dependencyHealth | Where-Object { -not $_.ready }).Count -eq 0) { break }
        Renew-Locks
        Start-Sleep -Seconds 2
      } while ([DateTimeOffset]::UtcNow -lt $dependencyDeadline)
      if (@($dependencyHealth | Where-Object { -not $_.ready }).Count -ne 0) { throw "dependency_health_failed" }

      Assert-NoDrift -Expected $configHashes
      [void](Invoke-DockerText -TimeoutSeconds 600 -Arguments ($composeBase + @(
        "up", "--no-deps", "--force-recreate", "--abort-on-container-exit",
        "--exit-code-from", "migrate", "migrate"
      )))
      Assert-NoDrift -Expected $configHashes
      [void](Invoke-DockerText -Arguments ($composeBase + @("up", "-d", "gw", "api", "web")))
      [void]$started.Add("gw")
      [void]$started.Add("api")
      [void]$started.Add("web")
      $health = Wait-AllHealthy -ExpectedHashes $configHashes
    }
    else {
      $health = Get-HealthResult
    }

    $operationRecord.status = "succeeded"
    $operationRecord.ok = $true
    $operationRecord.stopped = @($stopped)
    $operationRecord.started = @($started)
    $operationRecord.services = @($health)
    $operationRecord.completed_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-TargetAudit -Event "lifecycle_completed" -Result "succeeded" -Stopped @($stopped) -Started @($started)
    Write-JsonAtomic -Path $OperationPath -Value $operationRecord
    $result = New-SafeResult -Ok $true -Status "succeeded" -Identity $identity -Posture $posture `
      -Stopped @($stopped) -Started @($started) -Services $health -Locks $locks
  }
  catch {
    $errorCode = [string]$_.Exception.Message
    if ($errorCode -notmatch '^[a-z0-9_]+$') { $errorCode = "lifecycle_failed" }
    $status = "failed"
    $hint = "Inspect status and health before retrying with a new operation identity."
    if ($stopped.Count -gt 0 -or $started.Count -gt 0) {
      $status = "partial"
      $hint = "Inspect status, then use a new operation identity for the approved recovery action."
      if ($remaining.Count -eq 0 -and $Action -in @("StopApp", "RestartApp")) {
        foreach ($candidate in $StopOrder) {
          if ($stopped -notcontains $candidate) { [void]$remaining.Add($candidate) }
        }
      }
    }
    $operationRecord.status = $status
    $operationRecord.ok = $false
    $operationRecord.error_code = $errorCode
    $operationRecord.stopped = @($stopped)
    $operationRecord.started = @($started)
    $operationRecord.remaining = @($remaining)
    $operationRecord.recovery_hint = $hint
    $operationRecord.completed_at = [DateTimeOffset]::UtcNow.ToString("o")
    try { $failureHealth = Get-HealthResult -ActiveProbe }
    catch { $failureHealth = @() }
    $operationRecord.services = @($failureHealth)
    Write-TargetAudit -Event "lifecycle_completed" -Result $status -Stopped @($stopped) `
      -Started @($started) -Remaining @($remaining) -ErrorCode $errorCode
    Write-JsonAtomic -Path $OperationPath -Value $operationRecord
    $result = New-SafeResult -Ok $false -Status $status -Identity $identity -Posture $posture `
      -ErrorCode $errorCode -Stopped @($stopped) -Started @($started) -Remaining @($remaining) `
      -RecoveryHint $hint -Services $failureHealth -Locks $locks
  }
  finally {
    Remove-VerifiedInputs
    Release-Locks
  }

  Write-Output ($result | ConvertTo-Json -Depth 10 -Compress)
  exit 0
}
catch {
  $errorCode = [string]$_.Exception.Message
  if ($errorCode -notmatch '^[a-z0-9_]+$') { $errorCode = "preflight_failed" }
  if ($auditReady -and -not $operationStarted) {
    try {
      Write-TargetAudit -Event "lifecycle_rejected" -Result "rejected" -ErrorCode $errorCode
      $rejectedRecord = [ordered]@{
        schema_version = 1
        operation_id   = $OperationId
        action         = $Action
        target         = $RequestedTarget
        candidate_id   = (Split-Path -Leaf $CandidateRoot)
        reason_hash    = $reasonHash
        status         = "rejected"
        ok             = $false
        audit_id       = $auditId
        started_at     = [DateTimeOffset]::UtcNow.ToString("o")
        completed_at   = [DateTimeOffset]::UtcNow.ToString("o")
        stopped        = @()
        started        = @()
        remaining      = @()
        recovery_hint  = "Inspect the rejected preflight before retrying with a new operation identity."
        error_code     = $errorCode
        services       = @()
      }
      Write-JsonAtomic -Path $OperationPath -Value $rejectedRecord
    }
    catch {
      $auditId = ""
      $errorCode = "rejection_audit_failed"
    }
  }
  if ($operationStarted) { $auditId = "" }
  Release-Locks
  $result = New-SafeResult -Ok $false -Status "rejected" -Identity $identity -Posture $posture `
    -ErrorCode $errorCode -Locks ([ordered]@{
      shared = Get-LockInfo -Path $SharedLockPath -Name "shared-maintenance"
      legacy = Get-LockInfo -Path $LegacyLockPath -Name "legacy-hotfix"
    })
  Write-Output ($result | ConvertTo-Json -Depth 10 -Compress)
  exit 0
}
'@

$remoteScript = $remoteTemplate
$replacements = [ordered]@{
  "__ACTION__"         = ConvertTo-PowerShellUtf8Expression $Action
  "__REASON__"         = ConvertTo-PowerShellUtf8Expression $Reason
  "__OPERATION_ID__"   = ConvertTo-PowerShellUtf8Expression $OperationId.ToLowerInvariant()
  "__TARGET__"         = ConvertTo-PowerShellUtf8Expression $Target
  "__CANDIDATE_ROOT__" = ConvertTo-PowerShellUtf8Expression $CandidateRoot
  "__SITE_ROOT__"      = ConvertTo-PowerShellUtf8Expression $SiteRoot
  "__LEASE_SECONDS__"  = [string]$LeaseSeconds
  "__DRY_RUN__"        = if ($DryRun) { '$true' } else { '$false' }
  "__APPROVED__"       = if ($Approved) { '$true' } else { '$false' }
}
foreach ($replacement in $replacements.GetEnumerator()) {
  $remoteScript = $remoteScript.Replace([string]$replacement.Key, [string]$replacement.Value)
}
$transportScript = "& {`r`n$remoteScript`r`n}`r`n"

$sshArguments = @(
  "-T",
  "-o", "BatchMode=yes",
  "-o", "StrictHostKeyChecking=yes",
  "-o", "ConnectTimeout=10",
  "-o", "ServerAliveInterval=15",
  "-o", "ServerAliveCountMax=3",
  $Target,
  "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "-"
)
$stdoutPath = Join-Path ([IO.Path]::GetTempPath()) "ruisheng-maintenance-$([Guid]::NewGuid().ToString('N')).stdout"
$stderrPath = Join-Path ([IO.Path]::GetTempPath()) "ruisheng-maintenance-$([Guid]::NewGuid().ToString('N')).stderr"
try {
  $transportScript | & ssh.exe @sshArguments 1> $stdoutPath 2> $stderrPath
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) { throw "Remote maintenance transport failed with exit code $exitCode." }
  $rawOutput = if (Test-Path -LiteralPath $stdoutPath) {
    Get-Content -LiteralPath $stdoutPath -Raw -ErrorAction Stop
  }
  else { "" }
  $text = if ($null -eq $rawOutput) { "" } else { ([string]$rawOutput).Trim() }
}
finally {
  Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
}
if (-not $text) { throw "Remote maintenance returned no data." }
try { $result = $text | ConvertFrom-Json }
catch { throw "Remote maintenance returned invalid or non-allowlisted data." }
if ($null -eq $result -or $null -eq $result.PSObject.Properties["ok"]) {
  throw "Remote maintenance returned invalid or non-allowlisted data."
}

if ($isLifecycle -and -not $DryRun -and [string]$result.audit_id) {
  Write-OperatorAudit -Result $result -RequestedAction $Action -RequestedTarget $Target
}
$result | ConvertTo-Json -Depth 10
if (-not [bool]$result.ok) {
  throw "Remote maintenance was $($result.status): $($result.error_code)"
}
