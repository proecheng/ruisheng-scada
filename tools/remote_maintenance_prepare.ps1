[CmdletBinding()]
param(
  [string]$Target = "lenovo@100.109.90.21",
  [string]$SiteRoot = "C:\Ruisheng\candidates\site",
  [string]$RemoteAuditDirectory = "C:\Ruisheng\audit",
  [switch]$Approved
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

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

function Set-RestrictedDirectory {
  param(
    [Parameter(Mandatory)][string]$Path,
    [switch]$CreateAuditMutex,
    [switch]$NoRecursion
  )
  if (Test-Path -LiteralPath $Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
      throw "restricted_path_not_directory"
    }
    $existingItem = Get-Item -LiteralPath $Path -Force
    if (($existingItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "restricted_directory_reparse_point"
    }
  }
  else {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
  }

  $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
  $sidValues = @($currentSid.Value, "S-1-5-18", "S-1-5-32-544") | Select-Object -Unique
  $directoryAcl = New-Object Security.AccessControl.DirectorySecurity
  $directoryAcl.SetOwner($currentSid)
  $directoryAcl.SetAccessRuleProtection($true, $false)
  foreach ($sidValue in $sidValues) {
    $sid = New-Object Security.Principal.SecurityIdentifier($sidValue)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
      $sid,
      [Security.AccessControl.FileSystemRights]::FullControl,
      ([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit),
      [Security.AccessControl.PropagationFlags]::None,
      [Security.AccessControl.AccessControlType]::Allow
    )
    [void]$directoryAcl.AddAccessRule($rule)
  }
  if ($null -ne [IO.Directory].GetMethod(
      "SetAccessControl", [type[]]@([string], [Security.AccessControl.DirectorySecurity])
  )) {
    [IO.Directory]::SetAccessControl($Path, $directoryAcl)
  }
  else { Set-Acl -LiteralPath $Path -AclObject $directoryAcl }

  if ($CreateAuditMutex) {
    $mutexPath = Join-Path $Path ".remote-maintenance-audit.lock"
    $stream = [IO.File]::Open(
      $mutexPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None
    )
    $stream.Dispose()
  }
  if ($NoRecursion) { return }

  foreach ($item in @(Get-ChildItem -LiteralPath $Path -Recurse -Force)) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "restricted_directory_reparse_point"
    }
    if ($item.PSIsContainer) {
      $itemAcl = New-Object Security.AccessControl.DirectorySecurity
      $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor `
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    }
    else {
      $itemAcl = New-Object Security.AccessControl.FileSecurity
      $inheritance = [Security.AccessControl.InheritanceFlags]::None
    }
    $itemAcl.SetOwner($currentSid)
    $itemAcl.SetAccessRuleProtection($true, $false)
    foreach ($sidValue in $sidValues) {
      $sid = New-Object Security.Principal.SecurityIdentifier($sidValue)
      $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        $sid,
        [Security.AccessControl.FileSystemRights]::FullControl,
        $inheritance,
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Allow
      )
      [void]$itemAcl.AddAccessRule($rule)
    }
    if ($item.PSIsContainer -and $null -ne [IO.Directory].GetMethod(
        "SetAccessControl", [type[]]@([string], [Security.AccessControl.DirectorySecurity])
    )) {
      [IO.Directory]::SetAccessControl($item.FullName, $itemAcl)
    }
    elseif (-not $item.PSIsContainer -and $null -ne [IO.File].GetMethod(
        "SetAccessControl", [type[]]@([string], [Security.AccessControl.FileSecurity])
    )) {
      [IO.File]::SetAccessControl($item.FullName, $itemAcl)
    }
    else { Set-Acl -LiteralPath $item.FullName -AclObject $itemAcl }
  }
}

if (-not $Approved) { throw "Maintenance security preparation requires fresh approval through -Approved." }
if ($Target -notmatch '^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$') {
  throw "Target must use the user@host form without whitespace."
}
Assert-RemotePath -Path $SiteRoot
Assert-RemotePath -Path $RemoteAuditDirectory
$normalizedSiteRoot = [IO.Path]::GetFullPath($SiteRoot).TrimEnd('\')
if ($normalizedSiteRoot -notmatch '^[A-Za-z]:\\Ruisheng\\candidates\\[^\\]+$') {
  throw "SiteRoot must be a direct child of the Ruisheng candidates directory."
}
if (-not $RemoteAuditDirectory.Equals("C:\Ruisheng\audit", [StringComparison]::OrdinalIgnoreCase)) {
  throw "RemoteAuditDirectory must be C:\Ruisheng\audit."
}

$remoteTemplate = @'
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$SiteRoot = __SITE_ROOT__
$AuditDirectory = __AUDIT_DIRECTORY__
$StateDirectory = Join-Path $SiteRoot ".remote-maintenance-state"
$SharedLockPath = Join-Path $StateDirectory ".remote-maintenance.lock"
$LegacyLockPath = Join-Path $SiteRoot ".remote-hotfix.lock"
$OperationId = [Guid]::NewGuid().ToString("D")
$ProcessStartedAt = (Get-Process -Id $PID -ErrorAction Stop).StartTime.ToUniversalTime().ToString("o")
$AcquiredLocks = New-Object System.Collections.ArrayList

function Get-SshPosture {
  $settings = @{}
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
  if ($LASTEXITCODE -ne 0) { throw "ssh_effective_config_unavailable" }
  foreach ($line in $output) {
    if ("$line" -match '^([^\s]+)\s+(.+)$') {
      $settings[$matches[1].ToLowerInvariant()] = $matches[2].Trim().ToLowerInvariant()
    }
  }
  $password = if ($settings.ContainsKey("passwordauthentication")) { $settings["passwordauthentication"] } else { "unknown" }
  $keyboard = if ($settings.ContainsKey("kbdinteractiveauthentication")) { $settings["kbdinteractiveauthentication"] } else { "unknown" }
  $publicKey = if ($settings.ContainsKey("pubkeyauthentication")) { $settings["pubkeyauthentication"] } else { "unknown" }
  $methods = if ($settings.ContainsKey("authenticationmethods")) { $settings["authenticationmethods"] } else { "unknown" }
  $gssapi = if ($settings.ContainsKey("gssapiauthentication")) { $settings["gssapiauthentication"] } else { "unknown" }
  $hostBased = if ($settings.ContainsKey("hostbasedauthentication")) { $settings["hostbasedauthentication"] } else { "unknown" }
  return [ordered]@{
    password_authentication   = $password
    keyboard_interactive      = $keyboard
    public_key_authentication = $publicKey
    authentication_methods    = $methods
    gssapi_authentication     = $gssapi
    hostbased_authentication  = $hostBased
    mutation_allowed          = $password -eq "no" -and $keyboard -eq "no" -and `
      $publicKey -eq "yes" -and $methods -eq "publickey" -and $gssapi -eq "no" -and $hostBased -eq "no"
  }
}

function Set-RestrictedDirectory {
  param(
    [Parameter(Mandatory)][string]$Path,
    [switch]$CreateAuditMutex,
    [switch]$NoRecursion
  )
  if (Test-Path -LiteralPath $Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
      throw "restricted_path_not_directory"
    }
    $existingItem = Get-Item -LiteralPath $Path -Force
    if (($existingItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "restricted_directory_reparse_point"
    }
  }
  else {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
  }

  $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
  $sidValues = @($currentSid.Value, "S-1-5-18", "S-1-5-32-544") | Select-Object -Unique
  $directoryAcl = New-Object Security.AccessControl.DirectorySecurity
  $directoryAcl.SetOwner($currentSid)
  $directoryAcl.SetAccessRuleProtection($true, $false)
  foreach ($sidValue in $sidValues) {
    $sid = New-Object Security.Principal.SecurityIdentifier($sidValue)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
      $sid,
      [Security.AccessControl.FileSystemRights]::FullControl,
      ([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit),
      [Security.AccessControl.PropagationFlags]::None,
      [Security.AccessControl.AccessControlType]::Allow
    )
    [void]$directoryAcl.AddAccessRule($rule)
  }
  if ($null -ne [IO.Directory].GetMethod(
      "SetAccessControl", [type[]]@([string], [Security.AccessControl.DirectorySecurity])
  )) {
    [IO.Directory]::SetAccessControl($Path, $directoryAcl)
  }
  else { Set-Acl -LiteralPath $Path -AclObject $directoryAcl }

  if ($CreateAuditMutex) {
    $mutexPath = Join-Path $Path ".remote-maintenance-audit.lock"
    $stream = [IO.File]::Open(
      $mutexPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None
    )
    $stream.Dispose()
  }
  if ($NoRecursion) { return }

  foreach ($item in @(Get-ChildItem -LiteralPath $Path -Recurse -Force)) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "restricted_directory_reparse_point"
    }
    if ($item.PSIsContainer) {
      $itemAcl = New-Object Security.AccessControl.DirectorySecurity
      $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor `
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    }
    else {
      $itemAcl = New-Object Security.AccessControl.FileSecurity
      $inheritance = [Security.AccessControl.InheritanceFlags]::None
    }
    $itemAcl.SetOwner($currentSid)
    $itemAcl.SetAccessRuleProtection($true, $false)
    foreach ($sidValue in $sidValues) {
      $sid = New-Object Security.Principal.SecurityIdentifier($sidValue)
      $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        $sid,
        [Security.AccessControl.FileSystemRights]::FullControl,
        $inheritance,
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Allow
      )
      [void]$itemAcl.AddAccessRule($rule)
    }
    if ($item.PSIsContainer -and $null -ne [IO.Directory].GetMethod(
        "SetAccessControl", [type[]]@([string], [Security.AccessControl.DirectorySecurity])
    )) {
      [IO.Directory]::SetAccessControl($item.FullName, $itemAcl)
    }
    elseif (-not $item.PSIsContainer -and $null -ne [IO.File].GetMethod(
        "SetAccessControl", [type[]]@([string], [Security.AccessControl.FileSecurity])
    )) {
      [IO.File]::SetAccessControl($item.FullName, $itemAcl)
    }
    else { Set-Acl -LiteralPath $item.FullName -AclObject $itemAcl }
  }
}

function ConvertTo-ValidatedPreparationLock {
  param([Parameter(Mandatory)]$Record, [Parameter(Mandatory)][string]$ExpectedName)
  try {
    $acquired = [DateTimeOffset]::Parse([string]$Record.acquired_at)
    $expires = [DateTimeOffset]::Parse([string]$Record.expires_at)
    if (
      [int]$Record.schema_version -ne 1 -or
      [string]$Record.lock_name -ne $ExpectedName -or
      [string]$Record.operation_id -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$' -or
      [string]$Record.action -notmatch '^(StopApp|StartApp|RestartApp|hotfix-(api|gw|web)|maintenance-security-prepare)$' -or
      [int]$Record.pid -le 0 -or
      [string]$Record.target -notmatch '^[A-Za-z0-9._-]{1,255}$' -or
      $expires -le $acquired -or
      ($expires - $acquired).TotalSeconds -gt 3600
    ) { throw "invalid" }
    [void][DateTimeOffset]::Parse([string]$Record.process_started_at)
    return $Record
  }
  catch { throw "maintenance_lock_conflict_unrecognized" }
}

function Test-PreparationLockProcess {
  param([Parameter(Mandatory)]$Record)
  try { $process = Get-Process -Id ([int]$Record.pid) -ErrorAction Stop }
  catch [Microsoft.PowerShell.Commands.ProcessCommandException] { return $false }
  catch { throw "maintenance_lock_owner_uncertain" }
  try {
    $actualStart = $process.StartTime.ToUniversalTime()
    $expectedStart = [DateTimeOffset]::Parse([string]$Record.process_started_at).UtcDateTime
    return [Math]::Abs(($actualStart - $expectedStart).TotalSeconds) -lt 1
  }
  catch { throw "maintenance_lock_owner_uncertain" }
}

function Acquire-PreparationLock {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Name)
  for ($attempt = 0; $attempt -lt 2; $attempt++) {
    $stream = $null
    try {
      $record = [ordered]@{
        schema_version     = 1
        lock_name          = $Name
        operation_id      = $OperationId
        action            = "maintenance-security-prepare"
        pid               = $PID
        process_started_at = $ProcessStartedAt
        target            = [string]$env:COMPUTERNAME
        acquired_at       = [DateTimeOffset]::UtcNow.ToString("o")
        expires_at        = [DateTimeOffset]::UtcNow.AddMinutes(5).ToString("o")
      }
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
      [void]$AcquiredLocks.Add([ordered]@{ path = $Path; name = $Name })
      return
    }
    catch {
      if ($null -ne $stream) { $stream.Dispose() }
      if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "maintenance_lock_acquire_failed" }
      $existing = ConvertTo-ValidatedPreparationLock `
        -Record (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json) `
        -ExpectedName $Name
      $expired = [DateTimeOffset]::Parse([string]$existing.expires_at) -lt [DateTimeOffset]::UtcNow
      if (-not $expired -or (Test-PreparationLockProcess -Record $existing)) {
        throw "maintenance_lock_conflict"
      }
      $tombstone = "$Path.stale.$OperationId.$([Guid]::NewGuid().ToString('N'))"
      try { [IO.File]::Move($Path, $tombstone) }
      catch { throw "maintenance_lock_conflict_race" }
    }
    finally { if ($null -ne $stream) { $stream.Dispose() } }
  }
  throw "maintenance_lock_acquire_failed"
}

function Release-PreparationLocks {
  $paths = @($AcquiredLocks)
  [array]::Reverse($paths)
  foreach ($held in $paths) {
    try {
      $record = ConvertTo-ValidatedPreparationLock `
        -Record (Get-Content -LiteralPath $held.path -Raw -Encoding UTF8 | ConvertFrom-Json) `
        -ExpectedName $held.name
      if (
        [string]$record.operation_id -eq $OperationId -and
        [string]$record.process_started_at -eq $ProcessStartedAt
      ) {
        Remove-Item -LiteralPath $held.path -Force
      }
    }
    catch { }
  }
}

$posture = Get-SshPosture
if (-not $posture.mutation_allowed) { throw "ssh_not_key_only" }
if (-not (Test-Path -LiteralPath $SiteRoot -PathType Container)) { throw "site_root_missing" }
if (-not (Test-Path -LiteralPath $StateDirectory -PathType Container)) {
  New-Item -ItemType Directory -Path $StateDirectory -Force | Out-Null
}
Set-RestrictedDirectory -Path $SiteRoot -NoRecursion
Set-RestrictedDirectory -Path $StateDirectory
Acquire-PreparationLock -Path $SharedLockPath -Name "shared-maintenance"
try { Acquire-PreparationLock -Path $LegacyLockPath -Name "legacy-hotfix" }
catch {
  Release-PreparationLocks
  throw
}
try {
  Set-RestrictedDirectory -Path $AuditDirectory -CreateAuditMutex
}
finally { Release-PreparationLocks }
[ordered]@{
  schema_version = 1
  ok             = $true
  status         = "prepared"
  remote_user    = [string]$env:USERNAME
  remote_computer = [string]$env:COMPUTERNAME
  ssh_posture    = $posture
} | ConvertTo-Json -Depth 5 -Compress
'@

$remoteScript = $remoteTemplate
$remoteScript = $remoteScript.Replace("__SITE_ROOT__", (ConvertTo-PowerShellUtf8Expression $SiteRoot))
$remoteScript = $remoteScript.Replace(
  "__AUDIT_DIRECTORY__", (ConvertTo-PowerShellUtf8Expression $RemoteAuditDirectory)
)
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
$stdoutPath = Join-Path ([IO.Path]::GetTempPath()) "ruisheng-maintenance-prepare-$([Guid]::NewGuid().ToString('N')).stdout"
$stderrPath = Join-Path ([IO.Path]::GetTempPath()) "ruisheng-maintenance-prepare-$([Guid]::NewGuid().ToString('N')).stderr"
try {
  $transportScript | & ssh.exe @sshArguments 1> $stdoutPath 2> $stderrPath
  $exitCode = $LASTEXITCODE
  if ($exitCode -ne 0) { throw "Remote maintenance preparation failed with exit code $exitCode." }
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
if (-not $text) { throw "Remote maintenance preparation returned no data." }
try { $result = $text | ConvertFrom-Json }
catch { throw "Remote maintenance preparation returned invalid data." }
if (-not [bool]$result.ok) { throw "Remote maintenance preparation was rejected." }

$localAuditDirectory = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "Ruisheng\audit"
Set-RestrictedDirectory -Path $localAuditDirectory -CreateAuditMutex
$result | ConvertTo-Json -Depth 5
