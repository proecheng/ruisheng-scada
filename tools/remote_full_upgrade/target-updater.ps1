[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [ValidateSet("Status", "Plan", "Initialize", "Apply", "Recover")]
  [string]$Action,
  [string]$CandidateRoot = "",
  [Parameter(Mandatory)][string]$SiteRoot,
  [Parameter(Mandatory)][string]$OperationId,
  [string]$Reason = "",
  [string]$ExpectedCandidateId = "",
  [string]$ExpectedLogicalIdentity = "",
  [string]$ExpectedSourceCommit = "",
  [string]$ExpectedAlembicHead = "",
  [string]$ExpectedPlatform = "",
  [long]$PackageBytes = 0,
  [ValidateRange(120, 3600)][int]$LeaseSeconds = 900,
  [switch]$Approved
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$StateDirectory = Join-Path $SiteRoot ".remote-maintenance-state"
$ActiveReleasePath = Join-Path $StateDirectory "active-release.json"
$SharedLockPath = Join-Path $StateDirectory ".remote-maintenance.lock"
$LegacyLockPath = Join-Path $SiteRoot ".remote-hotfix.lock"
$EnvFile = Join-Path $SiteRoot ".env.prod"
$AuditDirectory = "C:\Ruisheng\audit"
$AuditPath = Join-Path $AuditDirectory "full-upgrade.jsonl"
$AuditLockPath = Join-Path $AuditDirectory ".remote-maintenance-audit.lock"
$JournalPath = Join-Path $StateDirectory "full-upgrade-$OperationId.json"
$BackupDirectory = Join-Path $SiteRoot "backups\$OperationId"
$IncomingOperationRoot = "C:\Ruisheng\incoming\$OperationId"
$StableCandidatesRoot = "C:\Ruisheng\candidates"
$ProspectiveEnvPath = Join-Path $StateDirectory ".prospective-$OperationId.env"
$VerifierPath = "C:\ProgramData\Ruisheng\bin\verify-publisher.ps1"
$AllowedFields = @(
  "TARGET_PLATFORM", "POSTGRES_IMAGE", "REDIS_IMAGE", "API_IMAGE", "GW_IMAGE", "WEB_IMAGE"
)
$PersistentServices = @("postgres", "redis", "gw", "api", "web")
$PolicyServices = @("postgres", "redis", "migrate", "gw", "api", "web")
$ProcessStartedAt = (Get-Process -Id $PID -ErrorAction Stop).StartTime.ToUniversalTime().ToString("o")
$AcquiredLocks = New-Object System.Collections.ArrayList
$SafeToRemoveIncoming = $false
$MaintenanceStatePath = Join-Path $StateDirectory "full-upgrade-maintenance.json"
$BoundedSourceHead = "0012_alarm_notification_runtime"
$BoundedTargetHead = "0013_serial_polling_profile"
$BoundedMigrationSha256 = "df20fff11e1b8ea04ef4ce69126c29021e221685ad27a138bbfafe10143ac5d1"

function Get-Sha256Text {
  param([Parameter(Mandatory)][AllowEmptyString()][string]$Text)
  $sha = [Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
    return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
  }
  finally { $sha.Dispose() }
}

function Assert-EntitlementFeature([string]$Feature) {
  $sitePath = "C:\ProgramData\Ruisheng\trust\entitlement-site-id"
  $verifier = "C:\ProgramData\Ruisheng\bin\target_entitlement_verifier.ps1"
  if (-not (Test-Path -LiteralPath $sitePath -PathType Leaf) -or
      (Get-Item -LiteralPath $sitePath -Force).Length -gt 256) {
    throw "entitlement_feature_denied"
  }
  $site = ([IO.File]::ReadAllText($sitePath, [Text.Encoding]::ASCII)).TrimEnd("`n")
  $windowsPowerShell = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
  $start = New-Object Diagnostics.ProcessStartInfo
  $start.FileName = $windowsPowerShell
  $start.Arguments = @(
    "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
    ('"' + $verifier.Replace('"', '\"') + '"'),
    "-Action", "Authorize", "-SiteId", ('"' + $site.Replace('"', '\"') + '"'),
    "-Feature", ('"' + $Feature.Replace('"', '\"') + '"')
  ) -join " "
  $start.UseShellExecute = $false
  $start.CreateNoWindow = $true
  $start.RedirectStandardOutput = $true
  $start.RedirectStandardError = $true
  $process = New-Object Diagnostics.Process
  $process.StartInfo = $start
  try {
    if (-not $process.Start()) { throw "entitlement_feature_denied" }
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(60000)) {
      try { $process.Kill() } catch { }
      throw "entitlement_feature_timeout"
    }
    $text = $stdout.GetAwaiter().GetResult() + $stderr.GetAwaiter().GetResult()
    $exitCode = $process.ExitCode
  }
  finally { $process.Dispose() }
  if ($exitCode -ne 0) {
    throw "entitlement_feature_denied"
  }
  $authorization = @($text.Trim() -split "`r?`n" | Where-Object { $_ })
  if ($authorization.Count -ne 1) { throw "entitlement_feature_denied" }
  try { $receipt = $authorization[0] | ConvertFrom-Json }
  catch { throw "entitlement_feature_denied" }
  if (-not $receipt.ok -or [string]$receipt.status -cne "authorized" -or
      [string]$receipt.feature -cne $Feature) {
    throw "entitlement_feature_denied"
  }
}

function Get-AuditLineHashMaterial {
  param([Parameter(Mandatory)][string]$Line)
  $match = [regex]::Match(
    $Line, '^(?<payload>\{.*),"record_hash":"(?<hash>[0-9a-f]{64})"\}$'
  )
  if (-not $match.Success) { return $null }
  return [pscustomobject]@{
    payload = $match.Groups["payload"].Value + "}"
    record_hash = $match.Groups["hash"].Value
  }
}

function Assert-AbsoluteRemotePath {
  param([Parameter(Mandatory)][string]$Path)
  if ($Path -notmatch '^[A-Za-z]:\\[^\r\n]*$') { throw "remote_path_invalid" }
}

function Test-ExactKeys {
  param([Parameter(Mandatory)]$Value, [Parameter(Mandatory)][string[]]$Expected)
  if ($null -eq $Value -or $Value -isnot [PSCustomObject]) { return $false }
  $actual = @($Value.PSObject.Properties.Name)
  if ($actual.Count -ne $Expected.Count) { return $false }
  foreach ($key in $Expected) { if ($actual -cnotcontains $key) { return $false } }
  return $true
}

function Get-AllowedSids {
  return @(
    [Security.Principal.WindowsIdentity]::GetCurrent().User.Value,
    "S-1-5-18", "S-1-5-32-544"
  ) | Select-Object -Unique
}

function Test-IsSharedAuditRoot {
  param([Parameter(Mandatory)][string]$Path)
  return [IO.Path]::GetFullPath($Path).TrimEnd('\').Equals(
    'C:\Ruisheng\audit', [StringComparison]::OrdinalIgnoreCase
  )
}

function Assert-SharedAuditRoot {
  param([Parameter(Mandatory)][string]$Path)
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "restricted_acl_required_admin_token"
  }
  if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
    throw "restricted_directory_missing"
  }
  $item = Get-Item -LiteralPath $Path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "restricted_directory_linked"
  }
  $acl = Get-Acl -LiteralPath $Path
  if (-not $acl.AreAccessRulesProtected) { throw "restricted_acl_inheritance_enabled" }
  try { $ownerSid = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value }
  catch { throw "restricted_acl_owner_invalid" }
  $ownerAllowed = @('S-1-5-18', 'S-1-5-32-544') -contains $ownerSid
  if (-not $ownerAllowed) { throw "restricted_acl_owner_invalid" }
  $seen = @{}
  foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) { $seen[$sid] = $false }
  $rules = @($acl.Access)
  if ($rules.Count -ne 2) { throw "restricted_acl_invalid" }
  foreach ($rule in $rules) {
    try { $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value }
    catch { throw "restricted_acl_invalid" }
    if (-not $seen.ContainsKey($sid) -or $seen[$sid] -or $rule.IsInherited -or
        $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
        $rule.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl -or
        $rule.InheritanceFlags -ne ([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
          [Security.AccessControl.InheritanceFlags]::ObjectInherit) -or
        $rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None) {
      throw "restricted_acl_invalid"
    }
    $seen[$sid] = $true
  }
  if ($seen.Values -contains $false) { throw "restricted_acl_required_identity_missing" }
}

function Assert-SharedAuditFile {
  param([Parameter(Mandatory)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "restricted_file_missing" }
  $item = Get-Item -LiteralPath $Path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "restricted_file_linked"
  }
  $parent = Split-Path -Parent ([IO.Path]::GetFullPath($Path))
  Assert-SharedAuditRoot -Path $parent
  $acl = Get-Acl -LiteralPath $Path
  if ([IO.Path]::GetFileName($Path) -ceq '.remote-maintenance-audit.lock') {
    $allowed = @{}
    foreach ($sid in @(Get-AllowedSids)) { $allowed[$sid] = $false }
    if (-not $acl.AreAccessRulesProtected) { throw "restricted_acl_invalid" }
    try { $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value }
    catch { throw "restricted_acl_owner_invalid" }
    if (-not $allowed.ContainsKey($owner)) { throw "restricted_acl_owner_invalid" }
    $rules = @($acl.Access)
    if ($rules.Count -ne $allowed.Count) { throw "restricted_acl_invalid" }
    foreach ($rule in $rules) {
      try { $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value }
      catch { throw "restricted_acl_invalid" }
      if (-not $allowed.ContainsKey($sid) -or $allowed[$sid] -or $rule.IsInherited -or
          $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
          $rule.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl -or
          $rule.InheritanceFlags -ne [Security.AccessControl.InheritanceFlags]::None -or
          $rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None) {
        throw "restricted_acl_invalid"
      }
      $allowed[$sid] = $true
    }
    if ($allowed.Values -contains $false) { throw "restricted_acl_required_identity_missing" }
    return
  }
  $seen = @{'S-1-5-18' = $false; 'S-1-5-32-544' = $false}
  $rules = @($acl.Access)
  if ($rules.Count -ne 2) { throw "restricted_acl_invalid" }
  foreach ($rule in $rules) {
    try { $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value }
    catch { throw "restricted_acl_invalid" }
    if (-not $seen.ContainsKey($sid) -or $seen[$sid] -or -not $rule.IsInherited -or
        $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
        $rule.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl -or
        $rule.InheritanceFlags -ne [Security.AccessControl.InheritanceFlags]::None -or
        $rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None) {
      throw "restricted_acl_invalid"
    }
    $seen[$sid] = $true
  }
  if ($seen.Values -contains $false) { throw "restricted_acl_required_identity_missing" }
}

function Test-IsSharedAuditFilePath {
  param([Parameter(Mandatory)][string]$Path)
  $full = [IO.Path]::GetFullPath($Path)
  return @(
    (Join-Path $AuditDirectory 'full-upgrade.jsonl')
    (Join-Path $AuditDirectory '.remote-maintenance-audit.lock')
  ) -contains $full
}

function Set-DirectoryAccessControl {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][Security.AccessControl.DirectorySecurity]$Acl
  )
  if ($null -ne [IO.Directory].GetMethod(
      "SetAccessControl", [type[]]@([string], [Security.AccessControl.DirectorySecurity])
  )) {
    [IO.Directory]::SetAccessControl($Path, $Acl)
  }
  else { Set-Acl -LiteralPath $Path -AclObject $Acl }
}

function Set-FileAccessControl {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][Security.AccessControl.FileSecurity]$Acl
  )
  if ($null -ne [IO.File].GetMethod(
      "SetAccessControl", [type[]]@([string], [Security.AccessControl.FileSecurity])
  )) {
    [IO.File]::SetAccessControl($Path, $Acl)
  }
  else { Set-Acl -LiteralPath $Path -AclObject $Acl }
}

function Assert-RestrictedDirectory {
  param([Parameter(Mandatory)][string]$Path)
  if (Test-IsSharedAuditRoot -Path $Path) {
    Assert-SharedAuditRoot -Path $Path
    return
  }
  if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
    throw "restricted_directory_missing"
  }
  $allowed = @{}
  foreach ($sid in @(Get-AllowedSids)) { $allowed[$sid] = $false }
  $item = Get-Item -LiteralPath $Path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "restricted_directory_linked"
  }
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
    ) {
      throw "restricted_acl_invalid"
    }
    $allowed[$sid] = $true
  }
  foreach ($sid in @($allowed.Keys)) {
    if (-not $allowed[$sid]) { throw "restricted_acl_required_identity_missing" }
  }
}

function Set-RestrictedTree {
  param([Parameter(Mandatory)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
    throw "restricted_directory_missing"
  }
  $renewAt = [DateTimeOffset]::UtcNow.AddSeconds([Math]::Max(30, [int]($LeaseSeconds / 3)))
  foreach ($item in @((Get-Item -LiteralPath $Path -Force)) +
      @(Get-ChildItem -LiteralPath $Path -Recurse -Force)) {
    if ($AcquiredLocks.Count -gt 0 -and [DateTimeOffset]::UtcNow -ge $renewAt) {
      Renew-Locks
      $renewAt = [DateTimeOffset]::UtcNow.AddSeconds([Math]::Max(30, [int]($LeaseSeconds / 3)))
    }
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "restricted_directory_linked"
    }
  }
  $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
  $sidValues = @($currentSid.Value, "S-1-5-18", "S-1-5-32-544") | Select-Object -Unique
  foreach ($item in @((Get-Item -LiteralPath $Path -Force)) +
      @(Get-ChildItem -LiteralPath $Path -Recurse -Force)) {
    if ($item.PSIsContainer) {
      $acl = New-Object Security.AccessControl.DirectorySecurity
      $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor `
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    }
    else {
      $acl = New-Object Security.AccessControl.FileSecurity
      $inheritance = [Security.AccessControl.InheritanceFlags]::None
    }
    $acl.SetOwner($currentSid)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sidValue in $sidValues) {
      $sid = New-Object Security.Principal.SecurityIdentifier($sidValue)
      $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        $sid, [Security.AccessControl.FileSystemRights]::FullControl, $inheritance,
        [Security.AccessControl.PropagationFlags]::None,
        [Security.AccessControl.AccessControlType]::Allow
      )
      [void]$acl.AddAccessRule($rule)
    }
    if ($item.PSIsContainer) {
      Set-DirectoryAccessControl -Path $item.FullName -Acl $acl
    }
    else { Set-FileAccessControl -Path $item.FullName -Acl $acl }
  }
}

function Assert-RestrictedFile {
  param([Parameter(Mandatory)][string]$Path)
  $fullPath = [IO.Path]::GetFullPath($Path)
  if (Test-IsSharedAuditFilePath -Path $fullPath) {
    Assert-SharedAuditFile -Path $fullPath
    return
  }
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
    ) {
      throw "restricted_acl_invalid"
    }
    $allowed[$sid] = $true
  }
  foreach ($sid in @($allowed.Keys)) {
    if (-not $allowed[$sid]) { throw "restricted_acl_required_identity_missing" }
  }
}

function Assert-ProtectedVerifierFile {
  param([Parameter(Mandatory)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "publisher_verifier_missing"
  }
  $item = Get-Item -LiteralPath $Path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "publisher_verifier_acl_invalid"
  }
  $allowed = @{
    "S-1-5-18" = $false
    "S-1-5-32-544" = $false
  }
  $acl = Get-Acl -LiteralPath $Path
  if (-not $acl.AreAccessRulesProtected) { throw "publisher_verifier_acl_invalid" }
  try { $ownerSid = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value }
  catch { throw "publisher_verifier_acl_invalid" }
  if (-not $allowed.ContainsKey($ownerSid)) { throw "publisher_verifier_acl_invalid" }
  foreach ($rule in @($acl.Access)) {
    try { $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value }
    catch { throw "publisher_verifier_acl_invalid" }
    if ($rule.IsInherited -or $rule.AccessControlType -ne
        [Security.AccessControl.AccessControlType]::Allow -or
        -not $allowed.ContainsKey($sid) -or $allowed[$sid] -or
        $rule.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl -or
        $rule.InheritanceFlags -ne [Security.AccessControl.InheritanceFlags]::None -or
        $rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None) {
      throw "publisher_verifier_acl_invalid"
    }
    $allowed[$sid] = $true
  }
  if ($allowed.Values -contains $false) { throw "publisher_verifier_acl_invalid" }
}

function Set-RestrictedFileAcl {
  param([Parameter(Mandatory)][string]$Path)
  $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
  $acl = New-Object Security.AccessControl.FileSecurity
  $acl.SetOwner($currentSid)
  $acl.SetAccessRuleProtection($true, $false)
  foreach ($sidValue in @($currentSid.Value, "S-1-5-18", "S-1-5-32-544") | Select-Object -Unique) {
    $sid = New-Object Security.Principal.SecurityIdentifier($sidValue)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
      $sid, [Security.AccessControl.FileSystemRights]::FullControl,
      [Security.AccessControl.AccessControlType]::Allow
    )
    [void]$acl.AddAccessRule($rule)
  }
  Set-FileAccessControl -Path $Path -Acl $acl
}

function Write-JsonAtomic {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)]$Value)
  if ($AcquiredLocks.Count -gt 0) { Assert-LocksOwned }
  $temporary = "$Path.$PID.$([Guid]::NewGuid().ToString('N')).tmp"
  $utf8 = New-Object Text.UTF8Encoding($false)
  $bytes = $utf8.GetBytes(($Value | ConvertTo-Json -Depth 14 -Compress))
  $stream = [IO.File]::Open($temporary, "CreateNew", "Write", "None")
  try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) }
  finally { $stream.Dispose() }
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

function Get-SshPosture {
  $values = @{}
  $connectionParts = @(
    ([string]$env:SSH_CONNECTION).Split(" ", [StringSplitOptions]::RemoveEmptyEntries)
  )
  if ($connectionParts.Count -lt 4 -or $connectionParts[0] -notmatch '^[0-9a-fA-F:.]+$') {
    throw "ssh_connection_unavailable"
  }
  $clientHost = $connectionParts[0]
  try { $clientHost = [Net.Dns]::GetHostEntry($connectionParts[0]).HostName }
  catch { }
  $connectionContext = @(
    "user=$env:USERNAME", "host=$clientHost", "addr=$($connectionParts[0])",
    "laddr=$($connectionParts[2])", "lport=$($connectionParts[3])"
  ) -join ","
  $output = & "$env:SystemRoot\System32\OpenSSH\sshd.exe" -T -C $connectionContext 2>&1
  if ($LASTEXITCODE -ne 0) { throw "ssh_posture_unavailable" }
  foreach ($line in @($output)) {
    $parts = ([string]$line).Trim() -split '\s+', 2
    if ($parts.Count -eq 2) { $values[$parts[0].ToLowerInvariant()] = $parts[1].ToLowerInvariant() }
  }
  $safe =
    $values.passwordauthentication -eq "no" -and
    $values.kbdinteractiveauthentication -eq "no" -and
    $values.pubkeyauthentication -eq "yes" -and
    $values.authenticationmethods -eq "publickey" -and
    $values.gssapiauthentication -eq "no" -and
    $values.hostbasedauthentication -eq "no"
  return [ordered]@{ mutation_allowed = [bool]$safe }
}

function Read-ActiveRelease {
  Assert-RestrictedFile -Path $ActiveReleasePath
  try { $active = Convert-UpgradeJson (Get-Content -LiteralPath $ActiveReleasePath -Raw -Encoding UTF8) }
  catch { throw "active_release_pointer_invalid" }
  $keys = @(
    "schema_version", "candidate_id", "logical_identity", "source_commit",
    "candidate_root", "site_root", "committed_at", "operation_id"
  )
  if (
    -not (Test-ExactKeys $active $keys) -or
    $active.schema_version -is [bool] -or [int64]$active.schema_version -ne 1 -or
    [string]$active.candidate_id -notmatch '^[a-z0-9][a-z0-9._-]{0,62}$' -or
    [string]$active.logical_identity -notmatch '^sha256:[0-9a-f]{64}$' -or
    [string]$active.source_commit -notmatch '^[0-9a-f]{40}$' -or
    [string]$active.candidate_root -notmatch '^[A-Za-z]:\\[^\r\n]*$' -or
    [string]$active.site_root -cne $SiteRoot -or
    [string]$active.operation_id -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -or
    (Split-Path -Leaf ([string]$active.candidate_root)) -cne [string]$active.candidate_id
  ) { throw "active_release_pointer_invalid" }
  return $active
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

function Assert-CandidateManifest {
  param([Parameter(Mandatory)]$Manifest, [Parameter(Mandatory)][string]$Root)
  $base = @(
    "schema_version", "candidate_id", "source_commit", "generated_at", "target_os",
    "target_architecture", "alembic_head", "logical_identity", "tools", "authenticity", "images"
  )
  if ($Manifest.schema_version -is [bool] -or $Manifest.schema_version -isnot [int] -and
      $Manifest.schema_version -isnot [long]) { throw "manifest_schema_invalid" }
  $version = [int]$Manifest.schema_version
  $keys = if ($version -eq 2) { $base } elseif ($version -eq 3) {
    @($base) + "qualification_toolchain"
  } else { throw "manifest_schema_invalid" }
  if (-not (Test-ExactKeys $Manifest $keys)) { throw "manifest_schema_invalid" }
  if (
    [string]$Manifest.candidate_id -notmatch '^[a-z0-9][a-z0-9._-]{0,62}$' -or
    [string]$Manifest.candidate_id -cne (Split-Path -Leaf $Root) -or
    [string]$Manifest.source_commit -notmatch '^[0-9a-f]{40}$' -or
    [string]$Manifest.logical_identity -notmatch '^sha256:[0-9a-f]{64}$' -or
    -not [string]$Manifest.alembic_head -or
    [string]$Manifest.target_os -cne "linux" -or
    [string]$Manifest.target_architecture -notin @("amd64", "arm64") -or
    $Manifest.images -isnot [Array]
  ) { throw "manifest_identity_invalid" }
  $components = @{}
  foreach ($image in @($Manifest.images)) {
    if (
      -not (Test-ExactKeys $image @(
        "component", "source_reference", "repo_digest", "candidate_reference", "image_id",
        "os", "architecture", "archive", "sha256"
      )) -or
      [string]$image.component -notin $PersistentServices -or
      $components.ContainsKey([string]$image.component) -or
      [string]$image.candidate_reference -cne
        "ruisheng-candidate/$([string]$image.component):$([string]$Manifest.candidate_id)" -or
      [string]$image.candidate_reference -match ':latest$' -or
      [string]$image.image_id -notmatch '^sha256:[0-9a-f]{64}$' -or
      [string]$image.sha256 -notmatch '^[0-9a-f]{64}$'
    ) { throw "manifest_images_invalid" }
    $components[[string]$image.component] = $image
  }
  if ($components.Count -ne 5) { throw "manifest_images_invalid" }
  return $components
}

function Get-ReleaseValues {
  param([Parameter(Mandatory)]$Manifest, [Parameter(Mandatory)][hashtable]$Images)
  return @{
    TARGET_PLATFORM = "$($Manifest.target_os)/$($Manifest.target_architecture)"
    POSTGRES_IMAGE = [string]$Images.postgres.candidate_reference
    REDIS_IMAGE = [string]$Images.redis.candidate_reference
    API_IMAGE = [string]$Images.api.candidate_reference
    GW_IMAGE = [string]$Images.gw.candidate_reference
    WEB_IMAGE = [string]$Images.web.candidate_reference
  }
}

function Stop-StartedProcess {
  param([Parameter(Mandatory)]$Process, [Parameter(Mandatory)][bool]$Started)
  if (-not $Started) { return }
  if ($null -ne $Process.PSObject.Properties['UpgradeJob']) {
    $Process.UpgradeJob.StopAndConfirm()
    return
  }
  if (-not $Process.HasExited) {
    try { $Process.Kill() } catch { }
    try { [void]$Process.WaitForExit(5000) } catch { }
  }
}

function Initialize-UpgradeProcessJob {
  if ('Ruisheng.Upgrade.ProcessJob' -as [type]) { return }
  Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;
namespace Ruisheng.Upgrade {
  public sealed class ProcessJob : IDisposable {
    [StructLayout(LayoutKind.Sequential)] struct BasicLimits {
      public long ProcessTime, JobTime; public uint Flags;
      public UIntPtr Minimum, Maximum; public uint ActiveLimit;
      public UIntPtr Affinity; public uint Priority, Scheduling;
    }
    [StructLayout(LayoutKind.Sequential)] struct Limits {
      public BasicLimits Basic; public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
      public UIntPtr ProcessMemory, JobMemory, PeakProcess, PeakJob;
    }
    [StructLayout(LayoutKind.Sequential)] struct Accounting {
      public long UserTime, KernelTime, PeriodUserTime, PeriodKernelTime;
      public uint PageFaults, TotalProcesses, ActiveProcesses, TerminatedProcesses;
    }
    [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job, int kind, ref Limits value, uint length);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool QueryInformationJobObject(IntPtr job, int kind, out Accounting value, uint length, IntPtr returned);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool result);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateJobObject(IntPtr job, uint code);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    [DllImport("shell32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CommandLineToArgvW(string value, out int count);
    [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr value);
    public static string[] SplitArguments(string value) {
      int count; IntPtr pointer=CommandLineToArgvW("task.exe "+value,out count);
      if(pointer==IntPtr.Zero) throw new Win32Exception();
      try {
        string[] result=new string[count-1];
        for(int i=1;i<count;i++) result[i-1]=Marshal.PtrToStringUni(Marshal.ReadIntPtr(pointer,i*IntPtr.Size));
        return result;
      } finally { LocalFree(pointer); }
    }
    IntPtr handle;
    public ProcessJob() {
      handle=CreateJobObject(IntPtr.Zero,null);
      if(handle==IntPtr.Zero) throw new Win32Exception();
      Limits limits=new Limits(); limits.Basic.Flags=0x2000;
      if(!SetInformationJobObject(handle,9,ref limits,(uint)Marshal.SizeOf(typeof(Limits)))) {
        Dispose(); throw new Win32Exception();
      }
    }
    public void Assign(Process process) {
      bool assigned;
      if(!AssignProcessToJobObject(handle,process.Handle) || !IsProcessInJob(process.Handle,handle,out assigned) || !assigned)
        throw new Win32Exception();
    }
    public bool WaitForEmpty(int milliseconds) {
      Stopwatch clock=Stopwatch.StartNew();
      do {
        Accounting value;
        if(!QueryInformationJobObject(handle,1,out value,(uint)Marshal.SizeOf(typeof(Accounting)),IntPtr.Zero))
          throw new InvalidOperationException("docker_process_tree_stop_uncertain");
        if(value.ActiveProcesses==0) return true;
        Thread.Sleep(20);
      } while(clock.ElapsedMilliseconds<milliseconds);
      return false;
    }
    public void StopAndConfirm() {
      if(handle==IntPtr.Zero || !TerminateJobObject(handle,1) || !WaitForEmpty(5000))
        throw new InvalidOperationException("docker_process_tree_stop_uncertain");
    }
    public void Dispose() { if(handle!=IntPtr.Zero) { CloseHandle(handle); handle=IntPtr.Zero; } }
  }
}
'@
}

function Invoke-ContainedUpgradeProcess {
  param([Parameter(Mandatory)][string]$FileName, [Parameter(Mandatory)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Arguments,
    [ValidateRange(1, 900)][int]$TimeoutSeconds = 120)
  if ($AcquiredLocks.Count -gt 0) { Assert-LocksOwned -RenewIfDue }
  Initialize-UpgradeProcessJob
  # The fixed wrapper cannot spawn a child until the parent assigns its job and closes stdin.
  $bootstrap = @'
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::InputEncoding=New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$text=[Console]::In.ReadToEnd()
if ($text.Length -gt 262144) { exit 125 }
# Windows PowerShell's redirected input writer can prefix UTF-8 with a BOM.
if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { $text=$text.Substring(1) }
$payload=$text | ConvertFrom-Json
function ConvertTo-NativeArgument {
    param([AllowEmptyString()][string]$Value)
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    $builder = New-Object Text.StringBuilder
    [void]$builder.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
      if ($character -eq '\') { $slashes++; continue }
      if ($character -eq '"') {
        [void]$builder.Append(('\' * (($slashes * 2) + 1)))
        [void]$builder.Append('"'); $slashes = 0; continue
      }
      if ($slashes) { [void]$builder.Append(('\' * $slashes)); $slashes = 0 }
      [void]$builder.Append($character)
    }
    if ($slashes) { [void]$builder.Append(('\' * ($slashes * 2))) }
    [void]$builder.Append('"')
    return $builder.ToString()
}
$start=New-Object Diagnostics.ProcessStartInfo
$start.FileName=[string]$payload.file
$start.Arguments=(@($payload.arguments | ForEach-Object { ConvertTo-NativeArgument ([string]$_) }) -join ' ')
if (($start.FileName.Length+$start.Arguments.Length) -gt 32000) { exit 125 }
$start.UseShellExecute=$false
$child=[Diagnostics.Process]::Start($start)
$child.WaitForExit()
exit $child.ExitCode
'@
  $payload = @{ file=$FileName; arguments=@($Arguments) } | ConvertTo-Json -Depth 3 -Compress
  if ($payload.Length -gt 262144) { throw 'docker_arguments_exceeded' }
  $startInfo = New-Object Diagnostics.ProcessStartInfo
  $startInfo.FileName = (Get-Process -Id $PID).Path
  $startInfo.Arguments = '-NoLogo -NoProfile -NonInteractive -EncodedCommand ' + [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($bootstrap))
  $startInfo.UseShellExecute = $false
  $startInfo.CreateNoWindow = $true
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  $startInfo.RedirectStandardInput = $true
  $startInfo.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
  $startInfo.StandardErrorEncoding = New-Object Text.UTF8Encoding($false)
  $process = New-Object Diagnostics.Process
  $process.StartInfo = $startInfo
  $started = $false
  $job = New-Object Ruisheng.Upgrade.ProcessJob
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
  try {
    $started = $process.Start()
    if (-not $started) { throw "docker_command_failed" }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $job.Assign($process)
    $process | Add-Member -NotePropertyName UpgradeJob -NotePropertyValue $job
    if ($AcquiredLocks.Count -gt 0) { Assert-LocksOwned -RenewIfDue }
    $bytes = [Text.Encoding]::UTF8.GetBytes($payload)
    $inputTask=$process.StandardInput.BaseStream.WriteAsync($bytes, 0, $bytes.Length)
    while (-not $inputTask.Wait(100)) {
      if ([DateTimeOffset]::UtcNow -ge $deadline) { throw 'docker_command_timeout' }
      if ($AcquiredLocks.Count -gt 0) { Assert-LocksOwned -RenewIfDue }
    }
    [void]$inputTask.GetAwaiter().GetResult()
    $process.StandardInput.Close()
    while (-not $process.WaitForExit(1000)) {
      if ([DateTimeOffset]::UtcNow -ge $deadline) {
        throw "docker_command_timeout"
      }
      if ($AcquiredLocks.Count -gt 0) { Assert-LocksOwned -RenewIfDue }
    }
    # Forced cleanup proves local containment, but cannot prove a daemon request completed.
    if (-not $job.WaitForEmpty(250)) {
      $job.StopAndConfirm()
      throw 'docker_process_tree_incomplete'
    }
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    if ($AcquiredLocks.Count -gt 0) { Assert-LocksOwned -RenewIfDue }
    if ($process.ExitCode -ne 0) { throw "docker_command_failed" }
    return (([string]$stdout) + ([string]$stderr)).Trim()
  }
  catch {
    Stop-StartedProcess -Process $process -Started $started
    throw
  }
  finally { $job.Dispose(); $process.Dispose() }
}

# BEGIN site serial compose
# Embedded in each remote script so historical signed candidates stay immutable.
function Assert-SiteSerialPath {
  param([Parameter(Mandatory)][string]$Path)
  $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw 'site_serial_path_linked'
  }
  $acl = Get-Acl -LiteralPath $Path
  $trusted = @('S-1-5-18', 'S-1-5-32-544')
  if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -cnotin $trusted) {
    throw 'site_serial_owner_invalid'
  }
  $write = [Security.AccessControl.FileSystemRights]'Write,Delete,DeleteSubdirectoriesAndFiles,ChangePermissions,TakeOwnership'
  foreach ($rule in @($acl.Access)) {
    if ($rule.AccessControlType -eq 'Allow' -and ($rule.FileSystemRights -band $write) -ne 0 -and
        $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -cnotin $trusted) {
      throw 'site_serial_writer_invalid'
    }
  }
}

function Assert-SiteSerialContent {
  param([Parameter(Mandatory)][string]$Json)
  # Requiring the canonical representation also rejects duplicate keys, hidden
  # overrides, interpolation and fields that could broaden container privileges.
  try {
    $model = $Json | ConvertFrom-Json
    $ports = @($model.services.gw.environment.GW_SERIAL_PORTS | ConvertFrom-Json)
  }
  catch { throw 'site_serial_json_invalid' }
  if ($ports.Count -ne 1 -or $ports[0].port -isnot [string] -or
      $ports[0].port -cnotmatch '^/dev/ruisheng-[A-Za-z0-9._-]{1,64}$' -or
      ($ports[0].baud_rate -isnot [int] -and $ports[0].baud_rate -isnot [long]) -or
      $ports[0].baud_rate -notin @(1200,2400,4800,9600,19200,38400,57600,115200)) {
    throw 'site_serial_parameters_invalid'
  }
  $port = [string]$ports[0].port
  $baud = [string]$ports[0].baud_rate
  $expected = '{"services":{"gw":{"environment":{"GW_SERIAL_PORTS":"[{\"port\":\"' + $port +
    '\",\"baud_rate\":' + $baud + '}]"},"devices":[{"source":"' + $port +
    '","target":"' + $port + '","permissions":"rw"}]}}}'
  if ($Json.Trim() -cne $expected) { throw 'site_serial_override_invalid' }
}

function Get-SiteSerialOverride {
  $path = 'C:\Ruisheng\site\site-serial.override.json'
  $exists = $true
  try { $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop }
  catch [System.Management.Automation.ItemNotFoundException] { $exists = $false }
  if ($null -ne $script:SiteSerialSnapshot) {
    if ($exists -ne $script:SiteSerialSnapshot.exists) { throw 'site_serial_configuration_changed' }
    if (-not $exists) { return '' }
    foreach ($part in @('C:\Ruisheng','C:\Ruisheng\site',$path)) { Assert-SiteSerialPath $part }
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -cne $script:SiteSerialSnapshot.hash) {
      throw 'site_serial_configuration_changed'
    }
    return $path
  }
  if (-not $exists) {
    $script:SiteSerialSnapshot = @{ exists = $false }
    return ''
  }
  if ($item.PSIsContainer -or $item.Length -gt 4096) { throw 'site_serial_file_invalid' }
  foreach ($part in @('C:\Ruisheng','C:\Ruisheng\site',$path)) { Assert-SiteSerialPath $part }
  $stream = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
  try {
    $json = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8)
    Assert-SiteSerialContent $json
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    # Retain the read handle until process exit. Compose must read exactly the
    # validated file, including during recovery and asynchronous native calls.
    $script:SiteSerialSnapshot = @{ exists = $true; hash = $hash; guard = $stream }
  }
  catch { $stream.Dispose(); throw }
  return $path
}

function Add-SiteSerialComposeArguments {
  param([Parameter(Mandatory)][string[]]$Arguments)
  if ($Arguments[0] -cne 'compose') { return ,$Arguments }
  $path = Get-SiteSerialOverride
  if (-not $path) { return ,$Arguments }
  $index = 1
  while ($index -lt $Arguments.Count -and $Arguments[$index].StartsWith('-')) {
    if ($Arguments[$index] -cin @('-f','--file','--env-file','--project-directory','-p','--project-name','--profile','--ansi','--progress','--parallel')) {
      if ($index + 1 -ge $Arguments.Count) { throw 'site_serial_compose_arguments_invalid' }
      if ($Arguments[$index] -cin @('-f','--file') -and $Arguments[$index+1] -ieq $path) {
        throw 'site_serial_override_already_present'
      }
      $index += 2
    }
    else { throw 'site_serial_compose_option_unsupported' }
  }
  if ($index -ge $Arguments.Count) { throw 'site_serial_compose_command_missing' }
  return ,(@($Arguments[0..($index-1)]) + @('-f',$path) + @($Arguments[$index..($Arguments.Count-1)]))
}
# END site serial compose
function Invoke-DockerText {
  param([Parameter(Mandatory)][string[]]$Arguments, [ValidateRange(1, 900)][int]$TimeoutSeconds = 120)
  if ($Arguments[0] -ceq 'compose') { $Arguments = Add-SiteSerialComposeArguments -Arguments $Arguments }
  if ($AcquiredLocks.Count -gt 0) { Assert-LocksOwned -RenewIfDue }
  if ($null -ne $script:BoundedSnapshotDeadline) {
    $remaining=[int][Math]::Floor(($script:BoundedSnapshotDeadline-[DateTimeOffset]::UtcNow).TotalSeconds)
    if ($remaining -lt 1) { throw 'backup_snapshot_deadline_exceeded' }
    $TimeoutSeconds=[Math]::Min($TimeoutSeconds,$remaining)
  }
  $detachedExec=$false
  if ($Arguments[0] -ceq 'exec') {
    for ($option=1; $option -lt $Arguments.Count -and $Arguments[$option].StartsWith('-'); $option++) {
      if ($Arguments[$option] -cin @('-d','--detach')) { $detachedExec=$true }
      if ($Arguments[$option] -cin @('-e','--env','--env-file','-u','--user','-w','--workdir','--detach-keys')) { $option++ }
    }
  }
  $roleRestoreExec=$Arguments[0] -ceq 'exec' -and $Arguments[-1] -cmatch '\bALTER ROLE ruisheng_(api|gw) LOGIN;'
  $restartRelease=$Arguments[0] -ceq 'update' -and
    @($Arguments | Where-Object { $_ -cmatch '^--restart=(always|unless-stopped|on-failure(:[0-9]+)?)$' }).Count -gt 0
  $hasIntent = $null -ne $Journal -and $null -ne $Journal.migration -and
    ($Arguments[0] -cin @('run','create','start') -or
      $detachedExec -or $roleRestoreExec -or $restartRelease -or
      ($Arguments[0] -ceq 'compose' -and @($Arguments | Where-Object { $_ -cin @('run','up','start','restart') }).Count -gt 0))
  if ($Arguments[0] -ceq 'run' -and $Arguments -ccontains '--read-only' -and $Arguments -ccontains 'none' -and
      @($Arguments | Where-Object { $_ -cmatch "^ruisheng-(migration|storage)-proof-$OperationId-[0-9a-f]{32}$" }).Count -eq 1) {
    $hasIntent=$false
  }
  if ($hasIntent) {
    if ($null -ne $Journal.migration.docker_intent) { throw 'docker_mutation_completion_unknown' }
    $intent = @{ command=$Arguments[0]; phase=[string]$Journal.migration.phase; issued_at=[DateTimeOffset]::UtcNow.ToString('o'); scope='production' }
    if ($roleRestoreExec) { $intent.scope='role_restore' }
    if ($restartRelease) { $intent.scope='restart_restore' }
    if ($detachedExec -and $null -ne $Journal.migration.snapshot_holder -and
        $Arguments -ccontains "PGAPPNAME=ruisheng-upgrade-snapshot-$OperationId") {
      $intent.scope='snapshot'; $intent.application_name="ruisheng-upgrade-snapshot-$OperationId"
    }
    if ($null -ne $Journal.backup -and $Arguments[0] -cin @('create','start') -and
        $Arguments -ccontains "ruisheng-restore-$OperationId") {
      $intent.scope='restore'; $intent.name="ruisheng-restore-$OperationId"; $intent.token=[string]$Journal.backup.restore_asset_token
    }
    if ($Journal.migration -is [Collections.IDictionary]) { $Journal.migration.docker_intent=$intent }
    else { $Journal.migration | Add-Member -NotePropertyName docker_intent -NotePropertyValue $intent -Force }
    Write-JsonAtomic $JournalPath $Journal
  }
  $result = Invoke-ContainedUpgradeProcess (Get-Command docker.exe -ErrorAction Stop).Source $Arguments $TimeoutSeconds
  if ($AcquiredLocks.Count -gt 0) { Assert-LocksOwned -RenewIfDue }
  if ($hasIntent -and [string]$intent.scope -cne 'snapshot') {
    $Journal.migration.docker_intent=$null
    try { Write-JsonAtomic $JournalPath $Journal }
    catch { $Journal.migration.docker_intent=$intent; throw }
  }
  return $result
}

function Get-DatabaseHead {
  return Invoke-DockerText @(
    "exec", "ruisheng-postgres", "psql", "-U", "ruisheng_admin", "-d", "ruisheng",
    "-Atqc", "SELECT version_num FROM alembic_version"
  )
}

function Get-DatabaseBackupEstimate {
  $text = Invoke-DockerText @(
    "exec", "ruisheng-postgres", "psql", "-U", "ruisheng_admin", "-d", "ruisheng",
    "-Atqc", "SELECT pg_database_size('ruisheng')"
  )
  [long]$databaseBytes = 0
  if (-not [long]::TryParse($text, [ref]$databaseBytes) -or $databaseBytes -le 0) {
    throw "database_size_invalid"
  }
  $rolesAllowance = 64MB
  return [ordered]@{
    database_bytes = $databaseBytes
    roles_allowance_bytes = [long]$rolesAllowance
    required_bytes = [long][Math]::Max(5GB, ($databaseBytes * 2) + $rolesAllowance)
  }
}

function Get-DockerPlatform {
  $dockerPlatform = Invoke-DockerText @("info", "--format", "{{.OSType}}/{{.Architecture}}")
  if ($dockerPlatform -eq "linux/x86_64") { $dockerPlatform = "linux/amd64" }
  return $dockerPlatform
}

function Get-BoundedResources {
  param([Parameter(Mandatory)]$Estimate)
  $info = Convert-UpgradeJson (Invoke-DockerText @("info", "--format", "{{json .}}"))
  $freeText = Invoke-DockerText @("exec", "ruisheng-postgres", "sh", "-c", "df -Pk /var/lib/postgresql/data | tail -1 | awk '{print `$4}'")
  $memoryText = Invoke-DockerText @("exec", "ruisheng-postgres", "sh", "-c", "awk '/^MemAvailable:/ {print `$2}' /proc/meminfo")
  [long]$freeKb = 0; [long]$availableKb = 0
  if (-not [long]::TryParse($freeText, [ref]$freeKb) -or -not [long]::TryParse($memoryText, [ref]$availableKb)) {
    throw "bounded_restore_resources_unknown"
  }
  return [ordered]@{
    docker_memory = [ordered]@{ total_bytes = [long]$info.MemTotal; free_bytes = $availableKb * 1KB
      required_bytes = [long](2GB); sufficient = [long]$info.MemTotal -ge 3GB -and $availableKb * 1KB -ge 2GB }
    docker_data_volume = [ordered]@{ free_bytes = $freeKb * 1KB; required_bytes = [long]$Estimate.required_bytes
      sufficient = $freeKb * 1KB -ge [long]$Estimate.required_bytes }
  }
}

function Get-LockSummary {
  param([Parameter(Mandatory)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    return [ordered]@{ present = $false; state = "absent" }
  }
  try {
    $record = Convert-UpgradeJson (Get-Content -LiteralPath $Path -Raw -Encoding UTF8)
    $expires = [DateTimeOffset]::Parse([string]$record.expires_at)
    return [ordered]@{
      present = $true
      state = if ($expires -gt [DateTimeOffset]::UtcNow) { "active" } else { "expired" }
      operation_id = [string]$record.operation_id
      action = [string]$record.action
    }
  }
  catch { return [ordered]@{ present = $true; state = "unrecognized" } }
}

function Test-MatchingLockProcess {
  param([Parameter(Mandatory)]$Record)
  try { $process = Get-Process -Id ([int]$Record.pid) -ErrorAction Stop }
  catch { return $false }
  $actual = $process.StartTime.ToUniversalTime()
  $expected = [DateTimeOffset]::Parse([string]$Record.process_started_at).UtcDateTime
  return [Math]::Abs(($actual - $expected).TotalSeconds) -lt 1
}

function New-LockRecord {
  param([Parameter(Mandatory)][string]$Name)
  $now = [DateTimeOffset]::UtcNow
  return [ordered]@{
    schema_version = 1; lock_name = $Name; operation_id = $OperationId
    action = "full-upgrade"; pid = $PID; process_started_at = $ProcessStartedAt
    target = [string]$env:COMPUTERNAME; acquired_at = $now.ToString("o")
    expires_at = $now.AddSeconds($LeaseSeconds).ToString("o")
  }
}

function Acquire-LeasedLock {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Name)
  $record = New-LockRecord $Name
  $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(
    ($record | ConvertTo-Json -Depth 4 -Compress)
  )
  for ($attempt = 0; $attempt -lt 2; $attempt++) {
    $stream = $null
    try {
      $stream = [IO.File]::Open($Path, "CreateNew", "Write", "None")
      $stream.Write($bytes, 0, $bytes.Length)
      $stream.Dispose(); $stream = $null
      [void]$AcquiredLocks.Add([ordered]@{ path = $Path; name = $Name })
      return
    }
    catch {
      if ($null -ne $stream) { $stream.Dispose() }
      if ($attempt -ne 0) { throw "upgrade_lock_conflict" }
      try {
        $existing = Convert-UpgradeJson (Get-Content -LiteralPath $Path -Raw -Encoding UTF8)
        if (
          [int]$existing.schema_version -ne 1 -or [string]$existing.lock_name -ne $Name -or
          [string]$existing.operation_id -notmatch '^[0-9a-f-]{36}$' -or
          [string]$existing.action -notmatch '^(full-upgrade|StopApp|StartApp|RestartApp|hotfix-(api|gw|web))$'
        ) { throw "upgrade_lock_unrecognized" }
        $expired = [DateTimeOffset]::Parse([string]$existing.expires_at) -le [DateTimeOffset]::UtcNow
        if (-not $expired -or (Test-MatchingLockProcess $existing)) { throw "upgrade_lock_conflict" }
        Move-Item -LiteralPath $Path -Destination "$Path.stale.$([Guid]::NewGuid().ToString('N'))"
      }
      catch { if ($_.Exception.Message -match '^upgrade_lock_') { throw }; throw "upgrade_lock_unrecognized" }
    }
  }
}

function Assert-LocksOwned {
  param([switch]$RenewIfDue)
  $renew = $false
  foreach ($held in @($AcquiredLocks)) {
    try {
      $record = Convert-UpgradeJson (Get-Content -LiteralPath $held.path -Raw -Encoding UTF8)
      $expires = [DateTimeOffset]::Parse([string]$record.expires_at)
    }
    catch { throw "upgrade_lock_lost" }
    if (
      [string]$record.operation_id -cne $OperationId -or [int]$record.pid -ne $PID -or
      [string]$record.process_started_at -cne $ProcessStartedAt -or
      $expires -le [DateTimeOffset]::UtcNow
    ) { throw "upgrade_lock_lost" }
    # The persisted expiry belongs to the operation, so short calls cannot reset
    # the renewal deadline. Validate every lock before renewing either record.
    if ($RenewIfDue -and $expires -le [DateTimeOffset]::UtcNow.AddSeconds($LeaseSeconds * 2.0 / 3)) {
      $renew = $true
    }
  }
  if ($renew) { Renew-Locks }
}

function Renew-Locks {
  Assert-LocksOwned
  foreach ($held in @($AcquiredLocks)) {
    $record = Convert-UpgradeJson (Get-Content -LiteralPath $held.path -Raw -Encoding UTF8)
    $record.expires_at = [DateTimeOffset]::UtcNow.AddSeconds($LeaseSeconds).ToString("o")
    Write-JsonAtomic -Path $held.path -Value $record
  }
}

function Release-Locks {
  $locks = @($AcquiredLocks); [array]::Reverse($locks)
  foreach ($held in $locks) {
    try {
      $record = Convert-UpgradeJson (Get-Content -LiteralPath $held.path -Raw -Encoding UTF8)
      if ([string]$record.operation_id -ceq $OperationId -and [int]$record.pid -eq $PID -and
          [string]$record.process_started_at -ceq $ProcessStartedAt) {
        Remove-Item -LiteralPath $held.path -Force
      }
    }
    catch { }
  }
  $AcquiredLocks.Clear()
}

function Write-Audit {
  param([Parameter(Mandatory)][string]$Event, [Parameter(Mandatory)][string]$Result,
    [Parameter(Mandatory)][AllowEmptyString()][string]$CandidateIdentity,
    [string]$ErrorCode = "", $Evidence = $null)
  if ($AcquiredLocks.Count -gt 0) { Assert-LocksOwned }
  $stream = [IO.File]::Open($AuditLockPath, "Open", "ReadWrite", "None")
  try {
    $previousHash = "0" * 64
    $duplicate = $false
    $reasonHash = Get-Sha256Text $Reason
    if (Test-Path -LiteralPath $AuditPath -PathType Leaf) {
      if ((Get-Item -LiteralPath $AuditPath).Length -gt 16MB) { throw "audit_file_limit_exceeded" }
      $count = 0
      foreach ($line in Get-Content -LiteralPath $AuditPath -Encoding UTF8) {
        if (-not $line) { continue }; $count++
        if ($count -gt 50000 -or [Text.Encoding]::UTF8.GetByteCount($line) -gt 64KB) {
          throw "audit_budget_exceeded"
        }
        try {
          $existing = Convert-UpgradeJson $line
          if ([string]$existing.previous_hash -cne $previousHash) { throw "invalid" }
          $hashMaterial = Get-AuditLineHashMaterial -Line $line
          if ($null -eq $hashMaterial -or
              [string]$existing.record_hash -cne [string]$hashMaterial.record_hash -or
              (Get-Sha256Text ([string]$hashMaterial.payload)) -cne
                [string]$hashMaterial.record_hash) { throw "invalid" }
          if (
            [string]$existing.operation_id -ceq $OperationId -and
            [string]$existing.event -ceq $Event -and
            [string]$existing.result -ceq $Result -and
            [string]$existing.candidate_identity -ceq $CandidateIdentity -and
            [string]$existing.reason_hash -ceq $reasonHash -and
            [string]$existing.error_code -ceq $ErrorCode -and
            (ConvertTo-Json -InputObject $existing.evidence -Depth 12 -Compress) -ceq
              (ConvertTo-Json -InputObject $Evidence -Depth 12 -Compress)
          ) { $duplicate = $true }
          $previousHash = [string]$existing.record_hash
        }
        catch { throw "audit_chain_invalid" }
      }
    }
    if ($duplicate) { return }
    $payload = [ordered]@{
      schema_version = 1; recorded_at = [DateTimeOffset]::UtcNow.ToString("o")
      operation_id = $OperationId; event = $Event; result = $Result
      candidate_identity = $CandidateIdentity; reason_hash = $reasonHash
      remote_user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
      remote_computer = [string]$env:COMPUTERNAME; error_code = $ErrorCode
      previous_hash = $previousHash
    }
    if ($null -ne $Evidence) { $payload.evidence = $Evidence }
    $payload.record_hash = Get-Sha256Text ($payload | ConvertTo-Json -Depth 8 -Compress)
    if ($AcquiredLocks.Count -gt 0) { Assert-LocksOwned }
    [IO.File]::AppendAllText(
      $AuditPath, (($payload | ConvertTo-Json -Depth 8 -Compress) + [Environment]::NewLine),
      (New-Object Text.UTF8Encoding($false))
    )
  }
  finally { $stream.Dispose() }
}

function Assert-NetworkBoundary {
  param([Parameter(Mandatory)]$Model)
  $names = @($Model.services.PSObject.Properties.Name)
  if (@($names | Where-Object { $_ -notin $PolicyServices }).Count -ne 0 -or
      @($PolicyServices | Where-Object { $_ -notin $names }).Count -ne 0) {
    throw "network_boundary_service_set_invalid"
  }
  foreach ($service in $PolicyServices) {
    $value = $Model.services.PSObject.Properties[$service].Value
    if ($null -ne $value.PSObject.Properties["network_mode"] -and
        -not [string]::IsNullOrWhiteSpace([string]$value.network_mode)) {
      throw "network_boundary_network_mode_invalid"
    }
    if ([string]$value.pull_policy -ne "never") { throw "network_boundary_pull_policy_invalid" }
    if ([string]$value.image -match ':latest$') { throw "mutable_image_reference" }
    $portsProperty = $value.PSObject.Properties["ports"]
    if ($null -eq $portsProperty -or $null -eq $portsProperty.Value) { continue }
    foreach ($port in @($portsProperty.Value)) {
      if ($null -eq $port -or $null -eq $port.PSObject.Properties["published"] -or
          [int]$port.published -le 0) {
        throw "network_boundary_published_port_invalid"
      }
      if ($null -eq $port.PSObject.Properties["host_ip"] -or
          [string]$port.host_ip -notin @("127.0.0.1", "::1")) {
        throw "network_boundary_non_loopback_port"
      }
    }
  }
}

function Assert-ComposeManifestImages {
  param([Parameter(Mandatory)]$Model, [Parameter(Mandatory)][hashtable]$Images)
  foreach ($service in $PolicyServices) {
    $component = if ($service -eq "migrate") { "api" } else { $service }
    if ([string]$Model.services.PSObject.Properties[$service].Value.image -cne
        [string]$Images[$component].candidate_reference) {
      throw "compose_manifest_image_mismatch"
    }
  }
}

function Assert-CurrentContainerIdentity {
  param([Parameter(Mandatory)][hashtable]$Images)
  foreach ($service in $PersistentServices) {
    $actual = Invoke-DockerText @(
      "inspect", "--format", "{{.Config.Image}}|{{.Image}}", "ruisheng-$service"
    )
    $identity = @($actual -split '\|', 2)
    if (
      $identity.Count -ne 2 -or
      [string]$identity[0] -cne [string]$Images[$service].candidate_reference -or
      [string]$identity[1] -cne [string]$Images[$service].image_id
    ) {
      throw "running_container_identity_mismatch"
    }
  }
}

function Invoke-PublisherVerification {
  param(
    [Parameter(Mandatory)][string]$Root,
    [Parameter(Mandatory)][string]$EnvironmentPath
  )
  Assert-ProtectedVerifierFile -Path $VerifierPath
  $startInfo = New-Object Diagnostics.ProcessStartInfo
  $startInfo.FileName = (Get-Command pwsh.exe -ErrorAction Stop).Source
  $startInfo.Arguments = @(
    "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
    ('"' + $VerifierPath.Replace('"', '\"') + '"'),
    ('"' + $Root.Replace('"', '\"') + '"'),
    ('"' + $EnvironmentPath.Replace('"', '\"') + '"')
  ) -join " "
  $startInfo.UseShellExecute = $false
  $startInfo.CreateNoWindow = $true
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  $process = New-Object Diagnostics.Process
  $process.StartInfo = $startInfo
  $started = $false
  try {
    $started = $process.Start()
    if (-not $started) { throw "publisher_verification_failed" }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $deadline = [DateTimeOffset]::UtcNow.AddMinutes(15)
    $renewAt = [DateTimeOffset]::UtcNow.AddSeconds([Math]::Max(30, [int]($LeaseSeconds / 3)))
    while (-not $process.WaitForExit(1000)) {
      if ([DateTimeOffset]::UtcNow -ge $deadline) { throw "publisher_verification_timeout" }
      if ($AcquiredLocks.Count -gt 0 -and [DateTimeOffset]::UtcNow -ge $renewAt) {
        Renew-Locks
        $renewAt = [DateTimeOffset]::UtcNow.AddSeconds([Math]::Max(30, [int]($LeaseSeconds / 3)))
      }
    }
    $process.WaitForExit()
    $publisherExitCode = $process.ExitCode
    $text = $stdoutTask.GetAwaiter().GetResult() + $stderrTask.GetAwaiter().GetResult()
  }
  catch {
    Stop-StartedProcess -Process $process -Started $started
    throw
  }
  finally { $process.Dispose() }
  if ($publisherExitCode -ne 2) { throw "publisher_verification_failed" }
  if (-not $text.Contains("[publisher] VERIFIED:") -or -not $text.Contains("B-04 remains BLOCKED")) {
    throw "publisher_verification_markers_missing"
  }
}

function New-DatabaseBackup {
  param([Parameter(Mandatory)]$Manifest)
  if (Test-Path -LiteralPath $BackupDirectory) { throw "backup_directory_conflict" }
  New-Item -ItemType Directory -Path $BackupDirectory | Out-Null
  $dumpInContainer = "/tmp/ruisheng-$OperationId.dump"
  $rolesInContainer = "/tmp/ruisheng-$OperationId-roles.sql"
  $dumpPath = Join-Path $BackupDirectory "ruisheng.dump"
  $rolesPath = Join-Path $BackupDirectory "roles.sql"
  try {
    [void](Invoke-DockerText @(
      "exec", "ruisheng-postgres", "pg_dump", "-U", "ruisheng_admin", "-d", "ruisheng",
      "--format=custom", "--file=$dumpInContainer"
    ) 600)
    [void](Invoke-DockerText @(
      "exec", "ruisheng-postgres", "pg_dumpall", "-U", "ruisheng_admin", "--roles-only",
      "--file=$rolesInContainer"
    ) 300)
    [void](Invoke-DockerText @(
      "exec", "ruisheng-postgres", "pg_restore", "--list", $dumpInContainer
    ) 300)
    [void](Invoke-DockerText @("cp", "ruisheng-postgres:$dumpInContainer", $dumpPath) 300)
    [void](Invoke-DockerText @("cp", "ruisheng-postgres:$rolesInContainer", $rolesPath) 120)
  }
  finally {
    try { [void](Invoke-DockerText @("exec", "ruisheng-postgres", "rm", "-f", $dumpInContainer, $rolesInContainer)) }
    catch { }
  }
  if ((Get-Item -LiteralPath $dumpPath).Length -le 0 -or (Get-Item -LiteralPath $rolesPath).Length -le 0) {
    throw "backup_empty"
  }
  $receipt = [ordered]@{
    schema_version = 1; operation_id = $OperationId
    candidate_identity = [string]$Manifest.logical_identity
    source_identity = [string](Read-ActiveRelease).logical_identity
    database_head = Get-DatabaseHead; created_at = [DateTimeOffset]::UtcNow.ToString("o")
    database = [ordered]@{ path = $dumpPath; sha256 = (Get-FileHash -Algorithm SHA256 $dumpPath).Hash.ToLowerInvariant() }
    roles = [ordered]@{ path = $rolesPath; sha256 = (Get-FileHash -Algorithm SHA256 $rolesPath).Hash.ToLowerInvariant() }
  }
  Write-JsonAtomic -Path (Join-Path $BackupDirectory "backup-receipt.json") -Value $receipt
  return $receipt
}

# BEGIN environment switch
function Get-ProspectiveEnvironmentBytes {
  param(
    [Parameter(Mandatory)][string]$SourcePath,
    [Parameter(Mandatory)][hashtable]$Values
  )
  $allowed = @(
    "TARGET_PLATFORM", "POSTGRES_IMAGE", "REDIS_IMAGE", "API_IMAGE", "GW_IMAGE", "WEB_IMAGE"
  )
  if ($Values.Count -ne $allowed.Count -or
      @($Values.Keys | Where-Object { $_ -notin $allowed }).Count -ne 0) {
    throw "release_environment_values_invalid"
  }
  $sourceItem = Get-Item -LiteralPath $SourcePath -Force
  if (($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "release_environment_linked"
  }
  $bytes = [IO.File]::ReadAllBytes($SourcePath)
  $hasBom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and
    $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
  $offset = if ($hasBom) { 3 } else { 0 }
  try {
    $text = (New-Object Text.UTF8Encoding($false, $true)).GetString(
      $bytes, $offset, ($bytes.Length - $offset)
    )
  }
  catch { throw "release_environment_not_utf8" }
  foreach ($key in $allowed) {
    $pattern = "(?m)^$([regex]::Escape($key))=[^\r\n]*(?=\r?$)"
    $matches = [regex]::Matches($text, $pattern)
    if ($matches.Count -eq 0) { throw "release_environment_key_missing" }
    if ($matches.Count -ne 1) { throw "release_environment_duplicate_key" }
    $replacement = "$key=$($Values[$key])"
    if ($replacement -match '[\r\n]' -or $replacement.Length -gt 1024) {
      throw "release_environment_value_invalid"
    }
    $match = $matches[0]
    $text = $text.Substring(0, $match.Index) + $replacement +
      $text.Substring($match.Index + $match.Length)
  }
  $output = (New-Object Text.UTF8Encoding($false)).GetBytes($text)
  if ($hasBom) { $output = [byte[]]@(0xEF, 0xBB, 0xBF) + $output }
  return ,$output
}

function Write-ProspectiveEnvironment {
  param([Parameter(Mandatory)][string]$SourcePath,
    [Parameter(Mandatory)][string]$DestinationPath, [Parameter(Mandatory)][hashtable]$Values)
  if (Test-Path -LiteralPath $DestinationPath) { throw "prospective_environment_conflict" }
  $output = Get-ProspectiveEnvironmentBytes $SourcePath $Values
  $stream = [IO.File]::Open($DestinationPath, "CreateNew", "Write", "None")
  $stream.Dispose()
  Set-RestrictedFileAcl -Path $DestinationPath
  $stream = [IO.File]::Open($DestinationPath, "Open", "Write", "None")
  try {
    $stream.Write($output, 0, $output.Length)
    $stream.Flush($true)
  }
  finally {
    $stream.Dispose()
  }
  Assert-RestrictedFile -Path $DestinationPath
}

function Get-EnvironmentReleaseValues {
  param([Parameter(Mandatory)][string]$Path)
  $values = @{}
  foreach ($line in [IO.File]::ReadAllLines($Path)) {
    foreach ($key in $AllowedFields) {
      if ($line -match "^$([regex]::Escape($key))=(.*)$") {
        if ($values.ContainsKey($key)) { throw "release_environment_duplicate_key" }
        $values[$key] = [string]$matches[1]
      }
    }
  }
  if ($values.Count -ne $AllowedFields.Count) { throw "release_environment_key_missing" }
  return $values
}

function New-EnvironmentBackupReceipt {
  param(
    [Parameter(Mandatory)][string]$SourcePath,
    [Parameter(Mandatory)][string]$BackupPath
  )
  if (Test-Path -LiteralPath $BackupPath) { throw "environment_backup_conflict" }
  Copy-Item -LiteralPath $SourcePath -Destination $BackupPath
  Set-RestrictedFileAcl -Path $BackupPath
  $sourceHash = (Get-FileHash -Algorithm SHA256 $SourcePath).Hash.ToLowerInvariant()
  $backupHash = (Get-FileHash -Algorithm SHA256 $BackupPath).Hash.ToLowerInvariant()
  if ($sourceHash -cne $backupHash) { throw "environment_backup_invalid" }
  return [ordered]@{ path = $BackupPath; sha256 = $backupHash }
}

function Prepare-EnvironmentSwitch {
  param(
    [Parameter(Mandatory)]$Journal,
    [Parameter(Mandatory)][string]$JournalFile,
    [Parameter(Mandatory)][string]$SourcePath,
    [Parameter(Mandatory)][string]$BackupPath
  )
  $Journal.environment_backup = New-EnvironmentBackupReceipt `
    -SourcePath $SourcePath -BackupPath $BackupPath
  $Journal.status = "switching"
  Write-JsonAtomic -Path $JournalFile -Value $Journal
}

function Set-ReleaseEnvironment {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$ProspectivePath,
    [string]$ExpectedPathSha256 = ""
  )
  if ($AcquiredLocks.Count -gt 0) { Assert-LocksOwned }
  Assert-RestrictedFile -Path $ProspectivePath
  $temporary = "$Path.$PID.$([Guid]::NewGuid().ToString('N')).tmp"
  $replaceBackup = "$Path.$PID.$([Guid]::NewGuid().ToString('N')).replace.bak"
  try {
    Copy-Item -LiteralPath $ProspectivePath -Destination $temporary
    Set-Acl -LiteralPath $temporary -AclObject (Get-Acl -LiteralPath $Path)
    if ($AcquiredLocks.Count -gt 0) { Assert-LocksOwned }
    if ($ExpectedPathSha256 -and (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ExpectedPathSha256) {
      throw "site_environment_identity_drift"
    }
    [IO.File]::Replace($temporary, $Path, $replaceBackup)
  }
  finally {
    if ($AcquiredLocks.Count -gt 0) { Assert-LocksOwned }
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $replaceBackup -Force -ErrorAction SilentlyContinue
  }
}
# END environment switch

function Get-ComposeBase {
  param(
    [Parameter(Mandatory)][string]$Root,
    [Parameter(Mandatory)][string]$EnvironmentPath
  )
  return @(
    "compose", "-f", (Join-Path $Root "docker-compose.prod.yml"),
    "-f", (Join-Path $Root "site-network.override.yml"), "--env-file", $EnvironmentPath
  )
}

function Wait-AllHealthy {
  param([Parameter(Mandatory)][string[]]$ComposeBase, [int]$TimeoutSeconds = 120)
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
  do {
    Assert-LocksOwned
    $failed = $false
    foreach ($service in @("postgres", "redis")) {
      $name = "ruisheng-$service"
      try {
        $state = Invoke-DockerText @("inspect", "--format", "{{json .State}}", $name) | ConvertFrom-Json
        if (-not [bool]$state.Running -or [string]$state.Health.Status -ne "healthy") { $failed = $true }
      }
      catch { $failed = $true }
    }
    try { [void](Invoke-DockerText @("exec", "ruisheng-api", "python", "-m", "ruisheng_api.healthcheck")) }
    catch {
      try { [void](Invoke-DockerText @(
          "exec", "ruisheng-api", "python", "-c",
          "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/meta/version',timeout=5).read(1)"
      )) }
      catch { $failed = $true }
    }
    try { [void](Invoke-DockerText @("exec", "ruisheng-gw", "python", "-m", "ruisheng_gw.healthcheck")) }
    catch {
      try { [void](Invoke-DockerText @(
          "exec", "ruisheng-gw", "python", "-c",
          "import urllib.request,urllib.error; u='http://127.0.0.1:9090/health'; ok=False; exec(`"try:\n r=urllib.request.urlopen(u,timeout=5); ok=r.status<500\nexcept urllib.error.HTTPError as e:\n ok=e.code in (401,403)`"); raise SystemExit(0 if ok else 1)"
      )) }
      catch { $failed = $true }
    }
    try { [void](Invoke-DockerText @("exec", "ruisheng-web", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1/")) }
    catch { $failed = $true }
    if (-not $failed) { return }
    Renew-Locks
    Start-Sleep -Seconds 2
  } while ([DateTimeOffset]::UtcNow -lt $deadline)
  throw "service_health_failed"
}

function Restore-PreviousRelease {
  param([Parameter(Mandatory)]$Journal)
  Assert-LocksOwned
  $active = Read-ActiveRelease
  Assert-ActiveReleaseUnchanged -Before $Journal.previous_release -After $active
  $backupPath = [string]$Journal.environment_backup.path
  Assert-RestrictedFile $backupPath
  if ((Get-FileHash -Algorithm SHA256 $backupPath).Hash.ToLowerInvariant() -cne
      [string]$Journal.environment_backup.sha256) { throw "recovery_environment_backup_invalid" }
  $oldRoot = [IO.Path]::GetFullPath([string]$Journal.previous_release.candidate_root).TrimEnd('\')
  if ((Split-Path -Parent $oldRoot) -cne $StableCandidatesRoot) {
    throw "recovery_candidate_path_invalid"
  }
  Assert-RestrictedDirectory $oldRoot
  $oldManifestPath = Join-Path $oldRoot "MANIFEST.json"
  Assert-RestrictedFile $oldManifestPath
  if ((Get-Item -LiteralPath $oldManifestPath).Length -gt 4MB) { throw "manifest_size_exceeded" }
  try { $oldManifest = Get-Content -LiteralPath $oldManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json }
  catch { throw "manifest_invalid" }
  $oldImages = Assert-CandidateManifest -Manifest $oldManifest -Root $oldRoot
  if (
    [string]$oldManifest.candidate_id -cne [string]$active.candidate_id -or
    [string]$oldManifest.logical_identity -cne [string]$active.logical_identity -or
    [string]$oldManifest.source_commit -cne [string]$active.source_commit
  ) { throw "recovery_candidate_identity_drift" }
  Invoke-PublisherVerification -Root $oldRoot -EnvironmentPath $backupPath
  $oldBase = Get-ComposeBase $oldRoot $backupPath
  $oldModel = Invoke-DockerText ($oldBase + @("config", "--format", "json")) | ConvertFrom-Json
  Assert-NetworkBoundary $oldModel
  Assert-ComposeManifestImages -Model $oldModel -Images $oldImages
  $temporary = "$EnvFile.$PID.rollback.tmp"
  $replaceBackup = "$EnvFile.$PID.rollback.bak"
  try {
    Copy-Item -LiteralPath $backupPath -Destination $temporary
    [IO.File]::Replace($temporary, $EnvFile, $replaceBackup)
  }
  finally {
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $replaceBackup -Force -ErrorAction SilentlyContinue
  }
  $oldBase = Get-ComposeBase $oldRoot $EnvFile
  [void](Invoke-DockerText ($oldBase + @("up", "-d", "postgres", "redis")))
  [void](Invoke-DockerText ($oldBase + @(
    "up", "--no-deps", "--force-recreate", "--abort-on-container-exit",
    "--exit-code-from", "migrate", "migrate"
  )) 600)
  [void](Invoke-DockerText ($oldBase + @("up", "-d", "gw", "api", "web")))
  Wait-AllHealthy $oldBase
  Assert-CurrentContainerIdentity -Images $oldImages
}

function Remove-UncommittedCandidate {
  param([Parameter(Mandatory)]$Journal)
  if ([string]$Journal.upgrade_kind -ceq "bounded_0012_0013") { return }
  if ([bool]$Journal.switched -or [string]$Journal.status -notin @("preflighted", "candidate_staged", "rejected")) {
    return
  }
  $path = [IO.Path]::GetFullPath([string]$Journal.candidate.candidate_root).TrimEnd('\')
  if ((Split-Path -Parent $path) -cne $StableCandidatesRoot -or
      (Split-Path -Leaf $path) -cne [string]$Journal.candidate.candidate_id) {
    throw "uncommitted_candidate_path_invalid"
  }
  if (Test-Path -LiteralPath $ActiveReleasePath -PathType Leaf) {
    $current = Read-ActiveRelease
    if ([string]$current.candidate_root -ceq $path) { throw "uncommitted_candidate_is_active" }
  }
  if (Test-Path -LiteralPath $path -PathType Container) {
    Remove-Item -LiteralPath $path -Recurse -Force
  }
}

function Test-SafeIncomingCandidateCleanup {
  param([AllowEmptyString()][string]$CandidatePath)
  if (-not $CandidatePath) { return $false }
  try {
    $path = [IO.Path]::GetFullPath($CandidatePath).TrimEnd('\')
    $incoming = [IO.Path]::GetFullPath($IncomingOperationRoot).TrimEnd('\')
    return (Split-Path -Parent $path) -ceq $incoming
  }
  catch { return $false }
}

function Assert-JournalCandidateIdentity {
  param([Parameter(Mandatory)]$Journal)
  if (
    [string]$Journal.candidate.candidate_id -cne $ExpectedCandidateId -or
    [string]$Journal.candidate.logical_identity -cne $ExpectedLogicalIdentity -or
    [string]$Journal.candidate.source_commit -cne $ExpectedSourceCommit -or
    [string]$Journal.candidate.alembic_head -cne $ExpectedAlembicHead -or
    [string]$Journal.candidate.platform -cne $ExpectedPlatform
  ) { throw "upgrade_candidate_identity_conflict" }
}

function Complete-UpgradeCommit {
  param(
    [Parameter(Mandatory)]$Journal,
    [AllowNull()]$Pointer,
    [Parameter(Mandatory)][string]$CandidateIdentity
  )
  if ($null -ne $Pointer) { Write-JsonAtomic -Path $ActiveReleasePath -Value $Pointer }
  try { Write-Audit "upgrade_committed" "committed" $CandidateIdentity }
  catch {
    $Journal.status = "uncertain"; $Journal.error_code = "commit_audit_incomplete"
    Write-JsonAtomic -Path $JournalPath -Value $Journal
    return $false
  }
  $Journal.status = "committed"; $Journal.error_code = ""
  $Journal.completed_at = [DateTimeOffset]::UtcNow.ToString("o")
  Write-JsonAtomic -Path $JournalPath -Value $Journal
  return $true
}

function Complete-UpgradeRollback {
  param(
    [Parameter(Mandatory)]$Journal,
    [Parameter(Mandatory)][string]$CandidateIdentity,
    [Parameter(Mandatory)][string]$DeploymentError
  )
  Assert-LocksOwned
  try {
    Restore-PreviousRelease $Journal
    $Journal.status = "rolled_back"; $Journal.error_code = $DeploymentError
    Write-Audit "upgrade_rolled_back" "rolled_back" $CandidateIdentity $DeploymentError
  }
  catch {
    $Journal.status = "recovery_failed"; $Journal.error_code = "recovery_failed"
    Write-Audit "upgrade_recovery_failed" "recovery_failed" $CandidateIdentity "recovery_failed"
  }
  $Journal.completed_at = [DateTimeOffset]::UtcNow.ToString("o")
  Write-JsonAtomic -Path $JournalPath -Value $Journal
  return $Journal
}

function Complete-ActiveReleaseInitialization {
  param(
    [Parameter(Mandatory)]$Pointer,
    [AllowNull()]$Existing,
    [Parameter(Mandatory)][string]$CandidateIdentity
  )
  if ($null -ne $Existing) {
    foreach ($field in @(
        "schema_version", "candidate_id", "logical_identity", "source_commit",
        "candidate_root", "site_root", "operation_id"
    )) {
      if ([string]$Existing.$field -cne [string]$Pointer.$field) {
        throw "active_release_already_initialized"
      }
    }
    $Pointer = $Existing
  }
  else { Write-JsonAtomic -Path $ActiveReleasePath -Value $Pointer }
  Write-Audit "active_release_initialized" "initialized" $CandidateIdentity
  return $Pointer
}

function New-SafeResult {
  param([bool]$Ok, [string]$Status, [string]$ErrorCode = "", $Active = $null, $Candidate = $null,
    $Locks = $null, $Backup = $null)
  return [ordered]@{
    schema_version = 1; ok = $Ok; status = $Status; action = $Action
    operation_id = $OperationId; error_code = $ErrorCode
    active_release = $Active; candidate = $Candidate; locks = $Locks; backup = $Backup
  }
}

function Get-SchemaUpgradeKind {
  param([string]$SourceHead, [string]$TargetHead)
  if ($SourceHead -ceq $TargetHead -and $SourceHead -match '^[a-z0-9_]+$') { return "same_head" }
  if ($SourceHead -ceq $BoundedSourceHead -and $TargetHead -ceq $BoundedTargetHead) {
    return "bounded_0012_0013"
  }
  throw "schema_head_changed"
}

function Convert-UpgradeJson {
  param([Parameter(Mandatory)][string]$Text)
  $options = @{}
  if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey("DateKind")) { $options.DateKind = "String" }
  return ConvertFrom-Json -InputObject $Text @options
}

function Assert-MaintenanceState {
  param([switch]$Recovering)
  $item = Get-Item -LiteralPath $MaintenanceStatePath -Force -ErrorAction SilentlyContinue
  if ($null -eq $item) { return }
  if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw "full_upgrade_maintenance_invalid"
  }
  Assert-RestrictedFile $MaintenanceStatePath
  if ((Get-Item -LiteralPath $MaintenanceStatePath).Length -gt 16KB) { throw "full_upgrade_maintenance_invalid" }
  try { $state = Convert-UpgradeJson (Get-Content -LiteralPath $MaintenanceStatePath -Raw -Encoding UTF8) }
  catch { throw "full_upgrade_maintenance_invalid" }
  $keys = @("schema_version", "operation_id", "site_root", "status", "source_identity", "candidate_identity",
    "source_head", "target_head", "journal_path", "updated_at")
  if (-not (Test-ExactKeys $state $keys) -or $state.schema_version -is [bool] -or
      ($state.schema_version -isnot [int] -and $state.schema_version -isnot [long]) -or
      [int]$state.schema_version -ne 1) { throw "full_upgrade_maintenance_invalid" }
  foreach ($key in @($keys | Where-Object { $_ -ne "schema_version" })) {
    if ($state.$key -isnot [string]) { throw "full_upgrade_maintenance_invalid" }
  }
  if ([string]$state.site_root -cne $SiteRoot -or
      [string]$state.operation_id -cnotmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -or
      [string]$state.source_identity -cnotmatch '^sha256:[0-9a-f]{64}$' -or
      [string]$state.candidate_identity -cnotmatch '^sha256:[0-9a-f]{64}$' -or
      [string]$state.source_head -cne $BoundedSourceHead -or [string]$state.target_head -cne $BoundedTargetHead -or
      [string]$state.journal_path -cne (Join-Path $StateDirectory "full-upgrade-$($state.operation_id).json") -or
      [string]$state.status -cnotin @("active", "committed", "rolled_back")) {
    throw "full_upgrade_maintenance_invalid"
  }
  [DateTimeOffset]$updatedAt = [DateTimeOffset]::MinValue
  if (-not [DateTimeOffset]::TryParseExact([string]$state.updated_at, "o", [Globalization.CultureInfo]::InvariantCulture,
      [Globalization.DateTimeStyles]::None, [ref]$updatedAt)) { throw "full_upgrade_maintenance_invalid" }
  if ([string]$state.status -ne "active") { return }
  if (-not $Recovering -or [string]$state.operation_id -cne $OperationId) {
    throw "full_upgrade_maintenance_active"
  }
}

function Save-BoundedPhase {
  param([Parameter(Mandatory)]$Journal, [Parameter(Mandatory)][string]$Phase)
  Assert-LocksOwned
  $Journal.migration.phase = $Phase
  $Journal.migration.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
  Write-JsonAtomic -Path $JournalPath -Value $Journal
}

function Write-MaintenanceState {
  param([Parameter(Mandatory)]$Journal, [string]$Status = "active")
  Assert-LocksOwned
  Write-JsonAtomic -Path $MaintenanceStatePath -Value ([ordered]@{
    schema_version = 1; operation_id = $OperationId; site_root = $SiteRoot
    status = $Status; source_identity = [string]$Journal.previous_release.logical_identity
    candidate_identity = [string]$Journal.candidate.logical_identity
    source_head = $BoundedSourceHead; target_head = $BoundedTargetHead
    journal_path = $JournalPath; updated_at = [DateTimeOffset]::UtcNow.ToString("o")
  })
}

function Assert-InstalledMaintenanceGuards {
  $receiptPath = "C:\Program Files\Ruisheng\Launcher\schema-upgrade-guard.json"
  $launcherPath = "C:\Program Files\Ruisheng\Launcher\start_ruisheng_local.ps1"
  foreach ($path in @($receiptPath, $launcherPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or
        ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
      throw "installed_maintenance_guards_missing"
    }
    $acl = Get-Acl -LiteralPath $path
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @("S-1-5-18", "S-1-5-32-544")) {
      throw "installed_maintenance_guards_acl_invalid"
    }
    foreach ($rule in @($acl.Access)) {
      $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
      $write = [Security.AccessControl.FileSystemRights]::Write -bor [Security.AccessControl.FileSystemRights]::Delete -bor
        [Security.AccessControl.FileSystemRights]::ChangePermissions -bor [Security.AccessControl.FileSystemRights]::TakeOwnership
      if ($rule.AccessControlType -eq "Allow" -and ($rule.FileSystemRights -band $write) -ne 0 -and
          $sid -notin @("S-1-5-18", "S-1-5-32-544")) { throw "installed_maintenance_guards_acl_invalid" }
    }
    # Preserve the stricter leaf owner policy above, and prove every ancestor
    # before consuming the receipt or allowing the installed business launcher.
    try {
      $sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
      Assert-StartupScriptIdentity $path $sha256
    } catch { throw "installed_maintenance_guards_acl_invalid" }
  }
  $receipt = Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
  if (-not (Test-ExactKeys $receipt @("schema_version", "guard_version", "launcher", "launcher_sha256", "installed_at")) -or
      [int]$receipt.schema_version -ne 1 -or [string]$receipt.guard_version -cne "bounded-schema-v1" -or
      [string]$receipt.launcher -cne $launcherPath -or [string]$receipt.launcher_sha256 -notmatch '^[0-9a-f]{64}$') {
    throw "installed_maintenance_guards_invalid"
  }
  if ((Get-FileHash -LiteralPath $launcherPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne
      [string]$receipt.launcher_sha256) { throw "installed_maintenance_guard_drift" }
  foreach ($task in @(Get-ScheduledTask -ErrorAction Stop)) {
    foreach ($actionEntry in @($task.Actions)) {
      if (Test-UnprotectedStartupAction ([string]$actionEntry.Execute) ([string]$actionEntry.Arguments) $launcherPath -WorkingDirectory ([string]$actionEntry.WorkingDirectory)) {
        throw "unprotected_startup_task_present"
      }
    }
  }
  return (Get-FileHash -LiteralPath $receiptPath -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Expand-StartupEnvironment {
  param([string]$Value)
  # Only machine paths have a bounded meaning across task accounts. A private
  # variable in this verifier's process is not proof of another account's value.
  $windows = [Environment]::GetFolderPath([Environment+SpecialFolder]::Windows)
  $fixed = @{SystemRoot=$windows;windir=$windows;ComSpec=(Join-Path ([Environment]::SystemDirectory) 'cmd.exe')}
  try { $expanded = [regex]::Replace($Value, '%([^%]+)%', {
    param($match)
    $name = $match.Groups[1].Value
    if (-not $fixed.ContainsKey($name)) { throw 'startup_task_arguments_invalid' }
    $replacement = [Environment]::GetEnvironmentVariable($name)
    if (-not $replacement -or $replacement -ine $fixed[$name]) { throw 'startup_task_arguments_invalid' }
    return $replacement
  }) } catch { throw 'startup_task_arguments_invalid' }
  if ($expanded.Contains('%') -or $expanded.Length -gt 65536) { throw 'startup_task_arguments_invalid' }
  return $expanded
}

function Assert-StartupScriptIdentity {
  param([string]$Path, [string]$Sha256, [switch]$FixedWindowsBinary)
  if ($Path -notmatch '^[A-Za-z]:\\' -or [IO.Path]::GetFullPath($Path) -ine $Path) {
    throw 'startup_script_identity_invalid'
  }
  $trusted = @('S-1-5-18','S-1-5-32-544',
    'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464') # Windows TrustedInstaller
  # The larger bound is internal to the fixed native-host proof below; script
  # allowlist callers retain the original 256KB bound.
  $maximumBytes=if ($FixedWindowsBinary) { 2MB } else { 256KB }
  $current = $Path; $leaf = $true
  while ($current) {
    $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        ($leaf -and ($item.PSIsContainer -or $item.Length -gt $maximumBytes)) -or (-not $leaf -and -not $item.PSIsContainer)) {
      throw 'startup_script_identity_invalid'
    }
    $acl = Get-Acl -LiteralPath $current -ErrorAction Stop
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted) { throw 'startup_script_identity_invalid' }
    # Creating unrelated children cannot replace an existing directory. Delete on
    # each existing object and DeleteChild on each ancestor can; check both.
    $unsafe = [int64]([Security.AccessControl.FileSystemRights]::Delete -bor
      [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
      [Security.AccessControl.FileSystemRights]::TakeOwnership -bor
      [Security.AccessControl.FileSystemRights]::WriteAttributes -bor
      [Security.AccessControl.FileSystemRights]::WriteExtendedAttributes) -bor 0x10000000 -bor 0x40000000
    if ($leaf) { $unsafe = $unsafe -bor [int64][Security.AccessControl.FileSystemRights]::Write }
    else { $unsafe = $unsafe -bor [int64][Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles }
    if ($FixedWindowsBinary -and -not $leaf -and $current -ieq [IO.Path]::GetDirectoryName($Path)) {
      # Native .local/.manifest absence must remain protected against creation.
      # Existing-script identity does not require this unrelated-child policy.
      $unsafe=$unsafe -bor [int64]([Security.AccessControl.FileSystemRights]::CreateFiles -bor
        [Security.AccessControl.FileSystemRights]::CreateDirectories)
    }
    foreach ($rule in @($acl.Access)) {
      if ($rule.AccessControlType -ne 'Allow' -or
          ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -or
          ([int64]$rule.FileSystemRights -band $unsafe) -eq 0) { continue }
      if ($rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $trusted) {
        throw 'startup_script_identity_invalid'
      }
    }
    $leaf = $false
    $current = [IO.Path]::GetDirectoryName($current)
  }
  if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant() -cne $Sha256) {
    throw 'startup_script_identity_invalid'
  }
}

function Test-ApprovedStartupScript {
  param([string]$Path, [AllowEmptyCollection()][string[]]$Arguments)
  # Closed deployment facts: never accept a candidate-supplied path/hash or trust
  # a basename/Status flag as proof about a script's contents.
  if ($Path -ieq 'C:\Ruisheng\tools\start-docker-ruisheng.ps1' -and $Arguments.Count -eq 0) {
    $sha256 = '63806068e41fc1c3521a540c130a0eab747d99d9e0c8a3ada06a9e6a0569c388'
  }
  elseif ($Path -ieq 'C:\Ruisheng\tools\serial_hardware_attach.ps1' -and $Arguments.Count -eq 2 -and
      $Arguments[0] -ieq '-ConfigPath' -and $Arguments[1] -ieq 'C:\Ruisheng\site\serial-hardware.json') {
    $sha256 = '429cb0902fa6a24aa604b30ac9e0d53dc37575afb576159d9cd3176017be38ac'
  }
  elseif ($Path -ieq 'C:\Windows\System32\hpatchmonTask.cmd' -and $Arguments.Count -eq 0) {
    $sha256 = '5347ad556fbc6bb1faf408b466b6bd10180b9299923325794e9e5afb0118408f'
  }
  else { return $false }
  try { Assert-StartupScriptIdentity $Path $sha256 }
  catch { return $false }
  return $true
}

function Test-ApprovedWindowsStartupHost {
  param([string]$Path, [string]$Arguments, [string]$WorkingDirectory)
  # Fixed observations on the deployed 64-bit Windows installation. Servicing
  # changes fail closed and require review; candidates cannot supply identities.
  $systemDirectory='C:\Windows\System32'
  $hostPath="$systemDirectory\rundll32.exe"
  if (-not [Environment]::Is64BitProcess -or $Path -ine $hostPath -or $WorkingDirectory) { return $false }
  $entries=@(
    @('C:\Windows\System32\PcaSvc.dll,PcaPatchSdbTask','PcaSvc.dll','b4bab2558ff39033131fca4fede16bc6958e04f150802132a4e895f6f8d50680'),
    @('Startupscan.dll,SusRunTask','Startupscan.dll','2280a8aaa510c6f05d7bcfd8bc02fdae737415665c98c26c9305ff65e109dbba'),
    @('Windows.Storage.ApplicationData.dll,CleanupTemporaryState','Windows.Storage.ApplicationData.dll','05f3ba69ee74f7d4346b3a1e59d5b68d3298cfd79d102b61d31961bf8627c33b'),
    @('C:\Windows\System32\AppxDeploymentClient.dll,AppxPreStageCleanupRunTask','AppxDeploymentClient.dll','2e01fea8ea24980884ff8309ef60318a3dfc183dc0fc58f666cd0df6e8b1fd77'),
    @('/d acproxy.dll,PerformAutochkOperations','acproxy.dll','eaba99f4fc722276267f3e36b7ce6f906340ce2ec70ecd061a3e3b795ad8ca77'),
    @('C:\Windows\System32\CapabilityAccessManager.dll,CapabilityAccessManagerDoStoreMaintenance','CapabilityAccessManager.dll','dbaf1077e2ff38e51d56c48b52011c90d2058e8f2f53697b4db2efde4582a5c4'),
    @('dfdts.dll,DfdGetDefaultPolicyAndSMART','dfdts.dll','389d2736a967d51fe50ec033f23d7f14ff62b73d8a7c1b4fffd8f7db2f488a29'),
    @('C:\Windows\System32\pcrpf.dll,NotifyFirmwareUpdateStaged','pcrpf.dll','e38886c9984453403ba137b5e1dc2ec756d99c2f74b162c837ee979400c50ec8'),
    @('C:\Windows\System32\Windows.StateRepositoryClient.dll,StateRepositoryDoMaintenanceTasks','Windows.StateRepositoryClient.dll','c4277c4088f124b241707fee55ebbca31a4382de55688e77318d4fd2e63607c1'),
    @('sysmain.dll,PfSvWsSwapAssessmentTask','sysmain.dll','a1c8ac760fb68bab5b24136632b7cf317bfabb86631d25fe48f42c6b7b433a51'),
    @('bfe.dll,BfeOnServiceStartTypeChange','bfe.dll','a27a54e3a6b3c9961d0ef1fa6bc082352debf33fb69d9bd8eaa87e2d887b1128')
  )
  try {
    $expanded=Expand-StartupEnvironment $Arguments
    $matched=$null
    foreach ($entry in $entries) {
      # Windows paths are case-insensitive; the export/remaining arguments are
      # case-sensitive and must match exactly, without alternate quoting/flags.
      $comma=$expanded.IndexOf(','); $expectedComma=$entry[0].IndexOf(',')
      if ($comma -ge 0 -and $expanded.Substring(0,$comma) -ieq $entry[0].Substring(0,$expectedComma) -and
          $expanded.Substring($comma) -ceq $entry[0].Substring($expectedComma)) { $matched=$entry; break }
    }
    if ($null -eq $matched) { return $false }
    foreach ($sidecar in @("$hostPath.local","$hostPath.manifest")) {
      try { $sidecarItem=Get-Item -LiteralPath $sidecar -Force -ErrorAction Stop; return $false }
      catch [Management.Automation.ItemNotFoundException] { }
    }
    # A bare DLL name resolves in this protected host's application directory
    # before cwd/PATH. Require the actual file there, no alternate local manifest
    # redirection, and the exact reviewed host/DLL bytes and protected ancestry.
    Assert-StartupScriptIdentity $hostPath 'f3e73f8a59b991fa8cdb91d4b37e11110c33a9eabdb25af8ac8ab5ccf9e3bbf5' -FixedWindowsBinary
    Assert-StartupScriptIdentity "$systemDirectory\$($matched[1])" $matched[2] -FixedWindowsBinary
    return $true
  }
  catch { return $false }
}

function Test-UnprotectedStartupAction {
  param([string]$Execute, [string]$Arguments, [string]$LauncherPath, [int]$Depth = 0,
    [AllowEmptyCollection()][string[]]$ParsedArguments = $null, [hashtable]$KnownAliases = $null,
    [string]$WorkingDirectory = '')
  if ($Depth -gt 8 -or $Arguments.Length -gt 65536) { throw 'startup_task_arguments_invalid' }
  Initialize-UpgradeProcessJob
  # Explicit execution hosts are not ordinary native utilities. This does not
  # claim to identify arbitrary renamed executables; supported hosts below have
  # their own grammar and all other known interpreters require a separate proof.
  $unsupportedHost = '^(wscript|cscript|mshta|rundll32|forfiles|powershell_ise|python(?:\d+(?:\.\d+)*)?w?|pyw?|wsl|bash|sh|dash|ash|zsh|ksh|fish|node|nodejs|deno|bun|perl|ruby|php|lua(?:\d+(?:\.\d+)*)?|luajit|tclsh|wish|java|javaw|jshell|dotnet|csi|fsi|msbuild|installutil|regsvr32)(\.(exe|com))?$'
  # Execute is a single Task Scheduler path slot, not CMD text. These directory
  # placeholders leave an unambiguous literal native basename; classification
  # does not depend on resolving another task account's profile (which may be
  # absent). Never apply this rule in a shell, to a script/host, or to a dynamic
  # executable name. Native binary behavior retains the existing scope below.
  $pathSlot = $Execute
  if ($pathSlot.StartsWith('"') -and $pathSlot.EndsWith('"') -and $pathSlot.Length -gt 1) {
    $pathSlot = $pathSlot.Substring(1,$pathSlot.Length-2)
  }
  if ($Depth -eq 0 -and $null -eq $ParsedArguments -and
      $pathSlot -imatch '^%(localappdata|programfiles|programfiles\(x86\))%\\(?:[A-Za-z0-9 _.-]+\\)*[A-Za-z0-9 _.-]+\.exe$' -and
      @($pathSlot.Split('\') | Where-Object { $_ -in @('.','..') }).Count -eq 0 -and
      [IO.Path]::GetFileName($pathSlot) -inotmatch 'docker|powershell|pwsh|cmd|ruisheng|entrypoint-migrate' -and
      [IO.Path]::GetFileName($pathSlot) -inotmatch $unsupportedHost) {
    return $false
  }
  $Execute = Expand-StartupEnvironment $Execute
  $program = [IO.Path]::GetFileName($Execute.Trim('"')).TrimEnd(' ','.').ToLowerInvariant()
  if ($program.Contains(':')) { return $true }
  if ($program -ceq 'rundll32.exe' -and $Depth -eq 0 -and $null -eq $ParsedArguments -and
      (Test-ApprovedWindowsStartupHost $Execute.Trim('"') $Arguments $WorkingDirectory)) { return $false }
  # Arbitrary script/DLL host content has no proven identity. Only the exact
  # top-level Windows entrypoints checked above are within the native exception.
  if ($program -imatch $unsupportedHost) { return $true }
  if ($Depth -eq 0 -and $null -eq $ParsedArguments -and
      ($program -cin @('docker','docker.exe','docker-compose','docker-compose.exe','cmd','cmd.exe','powershell','powershell.exe','pwsh','pwsh.exe') -or
       [IO.Path]::GetExtension($program) -iin @('.ps1','.cmd','.bat'))) {
    $Arguments = Expand-StartupEnvironment $Arguments
  }
  $parts = if ($null -ne $ParsedArguments) { @($ParsedArguments) } else { @([Ruisheng.Upgrade.ProcessJob]::SplitArguments($Arguments)) }
  $parts = @($parts)
  if ($program -cin @('docker','docker.exe','docker-compose','docker-compose.exe')) {
    for ($i=0; $i -lt $parts.Count; $i++) {
      $part=[string]$parts[$i]
      if ($part.StartsWith('-')) {
        if ($part -cin @('-c','--context','-H','--host','--config','--log-level','-l',
            '-f','--file','-p','--project-name','--profile','--env-file','--project-directory','--parallel')) { $i++ }
        continue
      }
      if ($part -ceq 'compose') { continue }
      if ($part -cin @('container','image','network','volume','context','system','builder','buildx')) { continue }
      if ($part -cin @('inspect','ps','ls','list','show','images','version','info','stats','logs','events','top','port','history','config','help')) { return $false }
      return $true
    }
    return $false
  }
  if ($program -match '^(remote_hotfix_deploy|remote_admin_bootstrap|remote_maintenance|entrypoint-migrate)(\.ps1|\.sh)?$') { return $true }
  if ($program -ceq 'start_ruisheng_local.ps1') { return $Execute.Trim('"') -ine $LauncherPath }
  if ([IO.Path]::GetExtension($program) -iin @('.ps1','.cmd','.bat')) {
    return -not (Test-ApprovedStartupScript $Execute.Trim('"') $parts)
  }
  # Process.Start/Start-Process may shell-open document and script extensions.
  # The same proof boundary applies to every nested launch, including omitted
  # extensions; only ordinary native executables and supported hosts remain.
  if ($Depth -gt 0 -and $program -cnotin @('cmd','cmd.exe','powershell','powershell.exe','pwsh','pwsh.exe') -and
      [IO.Path]::GetExtension($program) -inotmatch '^\.(exe|com)$') { return $true }
  if ($program -cin @('cmd','cmd.exe')) {
    $command = $Arguments
    if ($null -ne $ParsedArguments) { $command = $ParsedArguments -join ' ' }
    $command = Expand-StartupEnvironment $command
    if ($command.Contains('!')) { throw 'startup_task_arguments_invalid' }
    if ($command -notmatch '(?is)^\s*((?:(?:/[dqsau]|/[efv]:(?:on|off))\s+)*)/([ck])\s*(.*)$') {
      throw 'startup_task_arguments_invalid'
    }
    if (-not [regex]::IsMatch($Matches[1], '(?i)(?:^|\s)/d\s')) { return $true }
    if ($Matches[2] -ieq 'k') { return $true }
    $command = $Matches[3].Trim()
    if ($command.Length -ge 2 -and $command.StartsWith('"') -and $command.EndsWith('"') -and
        $command -notmatch '^"(?:[A-Za-z]:\\[^"]+|\\\\[^"]+|[^"\\\s]+)\.(exe|com|bat|cmd|ps1)"$') { $command = $command.Substring(1,$command.Length-2) }
    # CMD operators are active only outside quotes and caret escapes. Redirection operands
    # are never executable words; retain all compound command segments for inspection.
    $segments = New-Object Collections.ArrayList
    $words = New-Object Collections.ArrayList
    $quoteFlags = New-Object Collections.ArrayList
    $word = New-Object Text.StringBuilder
    $quoted=$false; $escaped=$false; $hasWord=$false; $hadQuotes=$false; $redirect=$false
    for ($i=0; $i -le $command.Length; $i++) {
      $ch = if ($i -lt $command.Length) { $command[$i] } else { [char]0 }
      if ($escaped) {
        if ($ch -eq [char]0) { throw 'startup_task_arguments_invalid' }
        [void]$word.Append($ch); $hasWord=$true; $escaped=$false; continue
      }
      if ($ch -eq '^' -and -not $quoted) { $escaped=$true; continue }
      if ($ch -eq '"') { $quoted=-not $quoted; $hasWord=$true; $hadQuotes=$true; continue }
      $operator = -not $quoted -and $ch -cin @('&','|','(',')',"`r","`n",[char]0)
      $redirection = -not $quoted -and $ch -cin @('<','>')
      if ($operator -or $redirection -or (-not $quoted -and [char]::IsWhiteSpace($ch))) {
        if ($hasWord) {
          if (-not $redirect -and -not ($redirection -and $word.ToString() -match '^\d+$')) {
            [void]$words.Add($word.ToString()); [void]$quoteFlags.Add($hadQuotes)
          }
          $redirect=$false; [void]$word.Clear(); $hasWord=$false; $hadQuotes=$false
        }
        if ($redirection) {
          $redirect=$true
          if (($i+1) -lt $command.Length -and $command[$i+1] -eq $ch) { $i++ }
          if (($i+1) -lt $command.Length -and $command[$i+1] -eq '&') { $i++ }
        }
        if ($operator) {
          if ($words.Count) {
            [void]$segments.Add(@{parts=@($words.ToArray());quotes=@($quoteFlags.ToArray())})
            $words.Clear(); $quoteFlags.Clear()
          }
          $redirect=$false
        }
        continue
      }
      [void]$word.Append($ch); $hasWord=$true
    }
    if ($quoted -or $escaped) { throw 'startup_task_arguments_invalid' }
    foreach ($segment in $segments) {
      $commandParts=@($segment.parts); $commandQuotes=@($segment.quotes)
      $name=([string]$commandParts[0]).TrimStart('@')
      if ($name -iin @('echo','echo.','rem','::')) { continue }
      if ($name -ieq 'call') {
        $commandParts=@($commandParts | Select-Object -Skip 1)
        $commandQuotes=@($commandQuotes | Select-Object -Skip 1)
        if (-not $commandParts.Count) { continue }
        $name=[string]$commandParts[0]
      }
      if ($name -ieq 'start') {
        $commandParts=@($commandParts | Select-Object -Skip 1)
        $commandQuotes=@($commandQuotes | Select-Object -Skip 1)
        $titleConsumed=$false
        while ($commandParts.Count) {
          if ($commandQuotes[0] -and -not $titleConsumed) { $titleConsumed=$true }
          elseif ($commandParts[0] -in @('/b','/wait','/min','/max','/low','/normal','/high','/realtime','/abovenormal','/belownormal','/i')) { }
          elseif ($commandParts[0] -in @('/d','/node','/affinity','/machine') -and $commandParts.Count -gt 1) {
            $commandParts=@($commandParts | Select-Object -Skip 1)
            $commandQuotes=@($commandQuotes | Select-Object -Skip 1)
          }
          else { break }
          $commandParts=@($commandParts | Select-Object -Skip 1)
          $commandQuotes=@($commandQuotes | Select-Object -Skip 1)
        }
        if (-not $commandParts.Count) { return $true }
        $name=[string]$commandParts[0]
      }
      if ($name -iin @('if','for','set')) { return $true }
      # CMD's PATHEXT lookup may resolve an extensionless word to a batch file.
      # Only known builtins/hosts have a bounded meaning without resolving PATH.
      if (-not [IO.Path]::GetExtension($name)) {
        if ($name -iin @('ver','dir','type','cd','chdir','cls','exit','pause','title','color')) { continue }
        if ($name -inotmatch '^(docker|docker-compose|powershell|pwsh|cmd)$') { return $true }
      }
      if (Test-UnprotectedStartupAction $name '' $LauncherPath ($Depth+1) -ParsedArguments @($commandParts | Select-Object -Skip 1)) {
        return $true
      }
    }
    return $false
  }
  if ($program -cin @('powershell','powershell.exe','pwsh','pwsh.exe')) {
    $command = $null
    $noProfile = $false
    for ($i=0; $i -lt $parts.Count; $i++) {
      $word = [string]$parts[$i]
      if (-not $word.StartsWith('-') -and -not $word.StartsWith('/')) {
        if (-not $noProfile) { return $true }
        if ($program -cin @('pwsh','pwsh.exe')) {
          if ([IO.Path]::GetExtension($word) -ine '.ps1') { return $true }
          return Test-UnprotectedStartupAction $word '' $LauncherPath ($Depth+1) -ParsedArguments @($parts | Select-Object -Skip ($i+1))
        }
        $command = @($parts | Select-Object -Skip $i) -join ' '
        break
      }
      $option = $word.Substring(1).ToLowerInvariant()
      $aliases = @{e='encodedcommand';ec='encodedcommand';ep='executionpolicy';wd='workingdirectory'}
      if ($aliases.ContainsKey($option)) { $option = $aliases[$option] }
      $options = @('file','command','encodedcommand','executionpolicy','noprofile','nologo','noexit','noninteractive',
        'windowstyle','inputformat','outputformat','version','sta','mta','workingdirectory')
      $matching = @($options | Where-Object { $option -and $_.StartsWith($option,[StringComparison]::OrdinalIgnoreCase) })
      if ($matching.Count -ne 1) { throw 'startup_task_arguments_invalid' }
      $option = $matching[0]
      if ($option -ceq 'noprofile') { $noProfile = $true; continue }
      if ($option -ceq 'noexit') { return $true }
      if ($option -cin @('noprofile','nologo','noexit','noninteractive','sta','mta')) { continue }
      if (($i+1) -ge $parts.Count) { throw 'startup_task_arguments_invalid' }
      if ($option -ceq 'file') {
        if (-not $noProfile) { return $true }
        if ([IO.Path]::GetExtension($parts[$i+1]) -ine '.ps1') { return $true }
        return Test-UnprotectedStartupAction $parts[$i+1] '' $LauncherPath ($Depth+1) -ParsedArguments @($parts | Select-Object -Skip ($i+2))
      }
      if ($option -cin @('command','encodedcommand')) {
        if (-not $noProfile) { return $true }
        $command = ($parts[($i+1)..($parts.Count-1)] -join ' ')
        if ($option -ceq 'encodedcommand') {
          if (($i+2) -ne $parts.Count) { throw 'startup_task_arguments_invalid' }
          try { $command = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($command)) }
          catch { throw 'startup_task_arguments_invalid' }
        }
        break
      }
      # Remaining recognized host options consume one value, never a command word.
      $i++
      if ([string]::IsNullOrWhiteSpace($parts[$i]) -or $parts[$i].StartsWith('-')) { throw 'startup_task_arguments_invalid' }
    }
    if ($null -eq $command -or $command.Trim() -ceq '-') { return $true }
    return Test-StartupPowerShellCommand $command $LauncherPath $Depth -KnownAliases $KnownAliases
  }
  return $false
}

function Test-StartupPowerShellCommand {
  param([string]$Command, [string]$LauncherPath, [int]$Depth = 0, [hashtable]$KnownAliases = $null)
  # Analyze an existing literal body only. A nested real host goes back through
  # Test-UnprotectedStartupAction and must supply its own startup options/input.
  if ($Depth -gt 8 -or $Command.Length -gt 65536) { throw 'startup_task_arguments_invalid' }
  $tokens=$null; $errors=$null
  $ast=[Management.Automation.Language.Parser]::ParseInput($command,[ref]$tokens,[ref]$errors)
  if ($errors.Count) { throw 'startup_task_arguments_invalid' }
  # This is a closed proof grammar, not an interpreter. Definitions,
  # imports, assignments and conversions can alter command resolution or
  # invoke user code without a directly visible executable command.
  if (@($ast.FindAll({param($n)
    $n -is [Management.Automation.Language.FunctionDefinitionAst] -or
    $n -is [Management.Automation.Language.TypeDefinitionAst] -or
    $n -is [Management.Automation.Language.UsingStatementAst] -or
    $n -is [Management.Automation.Language.AssignmentStatementAst] -or
    $n -is [Management.Automation.Language.ConvertExpressionAst] -or
    $n -is [Management.Automation.Language.AttributeAst] -or
    $n -is [Management.Automation.Language.ParamBlockAst] -or
    $n -is [Management.Automation.Language.RedirectionAst]
  },$true)).Count) { return $true }
  foreach ($memberExpression in @($ast.FindAll({param($n) $n -is [Management.Automation.Language.MemberExpressionAst]},$true))) {
    # Property access can execute a getter, and instance/member invocation
    # may evaluate dynamically constructed code. Only this explicit static
    # process launch has a supported, recursively checked binding below.
    if ($memberExpression -isnot [Management.Automation.Language.InvokeMemberExpressionAst] -or
        -not $memberExpression.Static -or
        $memberExpression.Expression -isnot [Management.Automation.Language.TypeExpressionAst] -or
        $memberExpression.Expression.TypeName.FullName -inotmatch '^(System\.)?Diagnostics\.Process$') { return $true }
  }
  foreach ($invocation in @($ast.FindAll({param($n) $n -is [Management.Automation.Language.InvokeMemberExpressionAst]},$true))) {
    try { $member=[string]$invocation.Member.SafeGetValue() } catch { return $true }
    if ($member -ine 'Start') { return $true }
    if ($invocation.Arguments.Count -lt 1 -or $invocation.Arguments.Count -gt 2) { return $true }
    try {
      $file=$invocation.Arguments[0].SafeGetValue()
      $processArguments=if ($invocation.Arguments.Count -eq 2) { $invocation.Arguments[1].SafeGetValue() } else { '' }
    } catch { return $true }
    if ($file -isnot [string] -or $processArguments -isnot [string]) { return $true }
    if (Test-UnprotectedStartupAction $file $processArguments $LauncherPath ($Depth+1)) { return $true }
  }
  $aliases=if ($null -ne $KnownAliases) { $KnownAliases.Clone() } else { @{} }
  $hostAliases=@([Management.Automation.Runspaces.InitialSessionState]::CreateDefault().Commands |
    Where-Object { $_ -is [Management.Automation.Runspaces.SessionStateAliasEntry] } |
    ForEach-Object { $_.Name })
  $harmlessCommands=@{
    'Write-Output'='Microsoft.PowerShell.Utility'; 'Write-Host'='Microsoft.PowerShell.Utility';
    'Get-Date'='Microsoft.PowerShell.Utility'; 'Get-Process'='Microsoft.PowerShell.Management';
    'Get-Service'='Microsoft.PowerShell.Management'; 'Get-Location'='Microsoft.PowerShell.Management'
  }
  $builtinAliases=@{echo='Write-Output';write='Write-Output';gps='Get-Process';ps='Get-Process';gsv='Get-Service';gl='Get-Location';pwd='Get-Location'}
  foreach ($node in @($ast.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst]},$true))) {
    $name=$node.GetCommandName()
    if (-not $name) {
      if ($node.CommandElements[0] -is [Management.Automation.Language.ScriptBlockExpressionAst]) { continue }
      try { $name=[string]$node.CommandElements[0].SafeGetValue() } catch { return $true }
      if (-not $name) { return $true }
    }
    for ($aliasDepth=0; $aliases.ContainsKey($name); $aliasDepth++) {
      if ($aliasDepth -ge 8) { return $true }
      $name=[string]$aliases[$name]
    }
    # A module-qualified name must identify the same trusted builtin. A
    # basename match could otherwise import arbitrary module initialization.
    if ($name.Contains('\') -and $name -notmatch '^(?:[A-Za-z]:|\\|\.|/)') {
      $qualified=$name.Split('\')
      if ($qualified.Count -ne 2) { return $true }
      $expectedModule=switch ($qualified[1]) {
        { $_ -iin @('Set-Alias','New-Alias','Invoke-Expression') } { 'Microsoft.PowerShell.Utility' }
        'Start-Process' { 'Microsoft.PowerShell.Management' }
        default { $harmlessCommands[$qualified[1]] }
      }
      if (-not $expectedModule -or $qualified[0] -ine $expectedModule) { return $true }
      $name=$qualified[1]
    }
    if ($name -iin @('Set-Alias','New-Alias','sal','nal')) {
      # A declaration inside a conditional, loop, invoked scriptblock or
      # pipeline is not proof that the alias exists for later commands.
      if ($node.Parent -isnot [Management.Automation.Language.PipelineAst] -or
          $node.Parent.PipelineElements.Count -ne 1 -or $node.Parent.Parent -ne $ast.EndBlock) { return $true }
      $aliasValues=@{}; $positional=New-Object Collections.ArrayList
      $metadata=(Get-Command Microsoft.PowerShell.Utility\Set-Alias -CommandType Cmdlet).Parameters
      $elements=@($node.CommandElements | Select-Object -Skip 1)
      for ($j=0; $j -lt $elements.Count; $j++) {
        $element=$elements[$j]
        if ($element -is [Management.Automation.Language.CommandParameterAst]) {
          $parameter=$element.ParameterName
          $matches=@($metadata.Keys | Where-Object { $_ -ieq $parameter -or $metadata[$_].Aliases -icontains $parameter })
          if (-not $matches.Count) { $matches=@($metadata.Keys | Where-Object { $_.StartsWith($parameter,[StringComparison]::OrdinalIgnoreCase) }) }
          if ($matches.Count -ne 1) { return $true }
          $key=[string]$matches[0]
          if ($key -notin @('Name','Value')) { return $true }
          $valueAst=$element.Argument
          if ($null -eq $valueAst -and $metadata[$key].ParameterType -ne [Management.Automation.SwitchParameter]) {
            $j++; if ($j -ge $elements.Count) { return $true }; $valueAst=$elements[$j]
          }
          if ($key -in @('Name','Value')) {
            if ($aliasValues.ContainsKey($key)) { return $true }
            try { $aliasValues[$key]=[string]$valueAst.SafeGetValue() } catch { return $true }
          }
        }
        else { try { [void]$positional.Add([string]$element.SafeGetValue()) } catch { return $true } }
      }
      foreach ($key in @('Name','Value')) {
        if (-not $aliasValues.ContainsKey($key) -and $positional.Count) { $aliasValues[$key]=$positional[0]; $positional.RemoveAt(0) }
      }
      if (-not $aliasValues.Name -or -not $aliasValues.Value -or $positional.Count -or
          $aliases.ContainsKey($aliasValues.Name) -or $aliasValues.Name -iin $hostAliases -or
          $aliasValues.Name -in @('sal','nal','iex','saps','start') -or
          $aliasValues.Name -match '[:\\/]') { return $true }
      $aliases[$aliasValues.Name]=$aliasValues.Value
      continue
    }
    if ($name -iin @('Invoke-Expression','iex')) {
      $elements=@($node.CommandElements | Select-Object -Skip 1)
      if (-not $elements.Count) { return $true }
      $element=$elements[0]
      if ($element -is [Management.Automation.Language.CommandParameterAst]) {
        if (-not 'Command'.StartsWith($element.ParameterName,[StringComparison]::OrdinalIgnoreCase)) { return $true }
        $element=$element.Argument
        if ($null -eq $element) { if ($elements.Count -lt 2) { return $true }; $element=$elements[1] }
      }
      try { $expression=[string]$element.SafeGetValue() } catch { return $true }
      if (Test-StartupPowerShellCommand $expression $LauncherPath ($Depth+1) -KnownAliases $aliases) { return $true }
      continue
    }
    if ($name -iin @('Start-Process','saps','start')) {
      $bound=@{}; $positional=New-Object Collections.ArrayList
      $metadata=(Get-Command Microsoft.PowerShell.Management\Start-Process -CommandType Cmdlet).Parameters
      $elements=@($node.CommandElements | Select-Object -Skip 1)
      for ($j=0; $j -lt $elements.Count; $j++) {
        $element=$elements[$j]
        if ($element -is [Management.Automation.Language.CommandParameterAst]) {
          $parameter=$element.ParameterName
          $matches=@($metadata.Keys | Where-Object { $_ -ieq $parameter -or $metadata[$_].Aliases -icontains $parameter })
          if (-not $matches.Count) { $matches=@($metadata.Keys | Where-Object { $_.StartsWith($parameter,[StringComparison]::OrdinalIgnoreCase) }) }
          if ($matches.Count -ne 1) { return $true }
          $parameter=[string]$matches[0]
          if ($parameter -notin @('FilePath','ArgumentList','Wait','NoNewWindow','WindowStyle')) { return $true }
          if ($bound.ContainsKey($parameter)) { return $true }
          $valueAst=$element.Argument
          if ($null -eq $valueAst -and $metadata[$parameter].ParameterType -ne [Management.Automation.SwitchParameter]) {
            $j++; if ($j -ge $elements.Count) { return $true }; $valueAst=$elements[$j]
          }
          try { $bound[$parameter]=if ($null -eq $valueAst) { $true } else { $valueAst.SafeGetValue() } }
          catch { $bound[$parameter]=$null }
        }
        else { try { [void]$positional.Add($element.SafeGetValue()) } catch { [void]$positional.Add($null) } }
      }
      foreach ($parameter in @('FilePath','ArgumentList')) {
        if (-not $bound.ContainsKey($parameter) -and $positional.Count) {
          $bound[$parameter]=$positional[0]; $positional.RemoveAt(0)
        }
      }
      if (-not $bound.FilePath -or $bound.FilePath -isnot [string] -or $positional.Count) { return $true }
      $file=[string]$bound.FilePath
      if ($bound.ContainsKey('ArgumentList') -and $null -eq $bound.ArgumentList) {
        return $true
      }
      if (-not [IO.Path]::GetExtension($file) -and $file -inotmatch '^(docker|docker-compose|powershell|pwsh|cmd)$') { return $true }
      if (Test-UnprotectedStartupAction $file (@($bound.ArgumentList) -join ' ') $LauncherPath ($Depth+1)) { return $true }
      continue
    }
    if ($builtinAliases.ContainsKey($name)) { $name=$builtinAliases[$name] }
    if (-not $harmlessCommands.ContainsKey($name)) {
      # PowerShell resolves omitted extensions to .ps1/.cmd/.bat. Relative,
      # missing and PATH-resolved names cannot establish the fixed script
      # identity; do not resolve or execute them in the verifier account.
      $extension=[IO.Path]::GetExtension($name)
      if ($extension -inotmatch '^\.(exe|com|ps1|cmd|bat)$' -and
          $name -inotmatch '^(docker|docker-compose|powershell|pwsh|cmd)$') { return $true }
    }
    $values=@(); $unknown=$false
    foreach ($element in @($node.CommandElements | Select-Object -Skip 1)) {
      if ($element -is [Management.Automation.Language.CommandParameterAst]) { $values += $element.Extent.Text }
      else { try { $values += @($element.SafeGetValue()) } catch { $unknown=$true } }
    }
    if ($unknown -and [IO.Path]::GetFileName($name) -match '(?i)docker|powershell|pwsh|cmd|ruisheng|entrypoint-migrate|\.(ps1|cmd|bat)$') { return $true }
    if ($harmlessCommands.ContainsKey($name)) { continue }
    if (Test-UnprotectedStartupAction $name '' $LauncherPath ($Depth+1) -ParsedArguments $values) { return $true }
  }
  return $false
}

function Invoke-DatabaseSql {
  param([Parameter(Mandatory)][string]$Sql, [string]$Container = "ruisheng-postgres",
    [string]$User = "ruisheng_admin", [string]$Database = "ruisheng", [int]$TimeoutSeconds = 120)
  return Invoke-DockerText @("exec", $Container, "psql", "-X", "-v", "ON_ERROR_STOP=1",
    "-U", $User, "-d", $Database, "-Atqc", $Sql) $TimeoutSeconds
}

function Assert-BoundedSchema {
  param([Parameter(Mandatory)][string]$Head)
  if ((Get-DatabaseHead) -cne $Head) { throw "recovery_database_head_unknown" }
  $sql = @'
SET search_path=pg_catalog;
SELECT json_build_object(
 'columns', (SELECT json_agg(json_build_object('name',a.attname,'type',format_type(a.atttypid,a.atttypmod),
   'required',a.attnotnull,'default',pg_get_expr(d.adbin,d.adrelid,false),'generated',a.attgenerated,'identity',a.attidentity,
   'reviewed_collation',CASE WHEN a.attname IN ('read_profile','transport_type','serial_port') THEN
     EXISTS(SELECT 1 FROM pg_collation c JOIN pg_namespace n ON n.oid=c.collnamespace
       WHERE c.oid=a.attcollation AND n.nspname='pg_catalog' AND c.collname='default'
       AND c.collprovider='d' AND c.collisdeterministic)
     ELSE a.attcollation=0 END) ORDER BY a.attname)
   FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
   WHERE a.attrelid='public.devices'::regclass AND NOT a.attisdropped AND a.attname IN ('read_profile','transport_type','serial_port','modbus_addr','deleted_at')),
 'checks', (SELECT json_agg(json_build_object('name',c.conname,'valid',c.convalidated,'expression',pg_get_expr(c.conbin,c.conrelid,false)) ORDER BY c.conname)
   FROM pg_constraint c WHERE c.conrelid='public.devices'::regclass AND c.contype='c'
   AND (c.conname IN ('ck_devices_read_profile','ck_devices_read_profile_transport') OR
     c.conkey && ARRAY(SELECT attnum FROM pg_attribute WHERE attrelid=c.conrelid AND attname='read_profile'))),
 'index', (SELECT json_build_object('valid',i.indisvalid AND i.indisready AND i.indislive AND i.indisunique AND NOT i.indnullsnotdistinct,
   'keys',ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(attnum,n) JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum ORDER BY k.n),
   'key_count',i.indnkeyatts,'total_count',i.indnatts,'method',am.amname,'expression',pg_get_expr(i.indexprs,i.indrelid,false),
   'predicate',pg_get_expr(i.indpred,i.indrelid,false),
   'default_ops',NOT EXISTS(SELECT 1 FROM unnest(i.indclass) o(oid) JOIN pg_opclass c ON c.oid=o.oid WHERE NOT c.opcdefault),
   'default_order',i.indoption::text='0 0',
   'column_collations',NOT EXISTS(SELECT 1 FROM unnest(i.indkey,i.indcollation) k(attnum,collation_id)
     JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum WHERE a.attcollation<>k.collation_id))
   FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_am am ON am.oid=c.relam
   WHERE i.indexrelid=to_regclass('public.uq_devices_serial_port_modbus_addr') AND i.indrelid='public.devices'::regclass));
'@
  $shape = Invoke-DatabaseSql $sql | ConvertFrom-Json
  # Deparser text alone omits implicit comparison rules. Bind collatable columns
  # to the system database-default identity, and require no collation on the rest.
  # Complete PostgreSQL deparser output is compared under pg_catalog search_path; no substring normalization.
  $columns = @{
    deleted_at=@('timestamp with time zone',$false,$null)
    modbus_addr=@('smallint',$true,$null)
    serial_port=@('character varying(50)',$false,$null)
    transport_type=@('character varying(10)',$true,"'tcp'::character varying")
  }
  if ($Head -ceq $BoundedTargetHead) { $columns.read_profile=@('character varying(20)',$true,"'point_groups'::character varying") }
  elseif ($Head -cne $BoundedSourceHead) { throw 'recovery_database_head_unknown' }
  if (@($shape.columns).Count -ne $columns.Count) { throw 'recovery_database_structure_invalid' }
  foreach ($column in @($shape.columns)) {
    $expected=$columns[[string]$column.name]
    if ($null -eq $expected -or [string]$column.type -cne $expected[0] -or $column.required -isnot [bool] -or
        $column.required -ne $expected[1] -or [string]$column.default -cne [string]$expected[2] -or
        [string]$column.generated -or [string]$column.identity -or
        $column.reviewed_collation -ne $true) { throw 'recovery_database_structure_invalid' }
  }
  $index=$shape.index
  $predicate="((transport_type)::text = 'serial'::text)"
  if ($Head -ceq $BoundedTargetHead) {
    $predicate="(((transport_type)::text = 'serial'::text) AND (deleted_at IS NULL))"
    $checks=@{
      ck_devices_read_profile="((read_profile)::text = ANY ((ARRAY['point_groups'::character varying, 'zero_origin_38'::character varying])::text[]))"
      ck_devices_read_profile_transport="(((read_profile)::text <> 'zero_origin_38'::text) OR ((transport_type)::text = 'serial'::text))"
    }
    if (@($shape.checks).Count -ne 2) { throw 'recovery_database_structure_invalid' }
    foreach ($check in @($shape.checks)) {
      if ($check.valid -ne $true -or -not $checks.ContainsKey([string]$check.name) -or
          [string]$check.expression -cne [string]$checks[[string]$check.name]) { throw 'recovery_database_structure_invalid' }
    }
  }
  elseif ($null -ne $shape.checks) { throw 'recovery_database_structure_invalid' }
  if ($index.valid -ne $true -or $index.default_ops -ne $true -or $index.default_order -ne $true -or
      $index.column_collations -ne $true -or [int]$index.key_count -ne 2 -or [int]$index.total_count -ne 2 -or
      [string]$index.method -cne 'btree' -or $null -ne $index.expression -or
      (@($index.keys) -join ',') -cne 'serial_port,modbus_addr' -or [string]$index.predicate -cne $predicate) {
    throw 'recovery_database_structure_invalid'
  }
}

function Assert-BoundedMigrationImage {
  param([Parameter(Mandatory)][hashtable]$Images)
  $script = @'
import ast,hashlib,pathlib
from alembic.config import Config
from alembic.script import ScriptDirectory
p=pathlib.Path('alembic/versions/20260907_0013_serial_polling_profile.py')
assert hashlib.sha256(p.read_bytes()).hexdigest()=='df20fff11e1b8ea04ef4ce69126c29021e221685ad27a138bbfafe10143ac5d1'
values={n.targets[0].id:ast.literal_eval(n.value) for n in ast.parse(p.read_text()).body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name)}
assert values['revision']=='0013_serial_polling_profile' and values['down_revision']=='0012_alarm_notification_runtime'
assert ScriptDirectory.from_config(Config('alembic.ini')).get_heads()==['0013_serial_polling_profile']
print('bounded_migration_verified')
'@
  $name = "ruisheng-migration-proof-$OperationId-$([Guid]::NewGuid().ToString('N'))"
  $result = Invoke-DockerText @("run", "--name", $name, "--network", "none", "--read-only",
    "--label", "com.ruisheng.upgrade.operation=$OperationId", "--restart", "no",
    "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "64",
    "--memory", "256m", "--entrypoint", "python", [string]$Images.api.image_id, "-c", $script)
  if ($result -cne "bounded_migration_verified") { throw "bounded_migration_identity_invalid" }
}

function Get-BoundedRelease {
  param([Parameter(Mandatory)]$Identity, [Parameter(Mandatory)][string]$EnvironmentPath)
  $root = [IO.Path]::GetFullPath([string]$Identity.candidate_root).TrimEnd('\')
  if ((Split-Path -Parent $root) -cne $StableCandidatesRoot) { throw "recovery_candidate_path_invalid" }
  Assert-RestrictedDirectory $root
  Assert-RestrictedFile (Join-Path $root "MANIFEST.json")
  $manifest = Get-Content -LiteralPath (Join-Path $root "MANIFEST.json") -Raw -Encoding UTF8 | ConvertFrom-Json
  $images = Assert-CandidateManifest $manifest $root
  foreach ($key in @("candidate_id", "source_commit", "logical_identity")) {
    if ([string]$manifest.$key -cne [string]$Identity.$key) { throw "recovery_candidate_identity_drift" }
  }
  Invoke-PublisherVerification $root $EnvironmentPath
  $base = Get-ComposeBase $root $EnvironmentPath
  $model = Invoke-DockerText ($base + @("config", "--format", "json")) | ConvertFrom-Json
  Assert-NetworkBoundary $model
  Assert-ComposeManifestImages $model $images
  foreach ($service in $PersistentServices) {
    if ((Invoke-DockerText @("image", "inspect", "--format", "{{.Id}}", [string]$images.$service.candidate_reference)) -cne
        [string]$images.$service.image_id) { throw "recovery_candidate_image_drift" }
  }
  return @{ root = $root; manifest = $manifest; images = $images; model = $model; base = $base }
}

function Get-ManagedApplications {
  $containers = @()
  foreach ($service in @("gw", "api", "web")) {
    $info = @(Invoke-DockerText @("inspect", "ruisheng-$service") | ConvertFrom-Json)[0]
    $containers += [ordered]@{ name = "ruisheng-$service"; id = [string]$info.Id
      image_id = [string]$info.Image; restart = [string]$info.HostConfig.RestartPolicy.Name
      retries = [int]$info.HostConfig.RestartPolicy.MaximumRetryCount }
  }
  return $containers
}

function Set-ApplicationRoleFence {
  param([Parameter(Mandatory)]$Journal)
  Assert-LocksOwned
  [void](Assert-BoundedDatabaseIdentity $Journal)
  [void](Invoke-DatabaseSql "ALTER ROLE ruisheng_api NOLOGIN; ALTER ROLE ruisheng_gw NOLOGIN;")
  Assert-LocksOwned
  [void](Invoke-DatabaseSql "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename IN ('ruisheng_api','ruisheng_gw') AND pid <> pg_backend_pid();")
  if ((Invoke-DatabaseSql "SELECT count(*) FROM pg_stat_activity WHERE usename IN ('ruisheng_api','ruisheng_gw')") -cne "0") {
    throw "application_role_fence_failed"
  }
  if ((Invoke-DatabaseSql "SELECT count(*) FROM pg_roles WHERE rolname IN ('ruisheng_api','ruisheng_gw') AND NOT rolcanlogin") -cne "2") {
    throw "application_role_fence_failed"
  }
}

function Assert-BoundedDatabaseIdentity {
  param([Parameter(Mandatory)]$Journal, [switch]$AllowStopped)
  Assert-LocksOwned
  $entries = @($Journal.migration.dependencies | Where-Object { [string]$_.service -ceq 'postgres' })
  if ($entries.Count -ne 1) { throw 'dependency_storage_receipt_invalid' }
  if (-not (Invoke-DockerText @('ps','-aq','--filter','name=^/ruisheng-postgres$'))) {
    if ($AllowStopped) { return $false }
    throw 'dependency_database_identity_invalid'
  }
  $entry = $entries[0]
  $info = @(Convert-UpgradeJson (Invoke-DockerText @('inspect','ruisheng-postgres')))[0]
  Assert-BoundedDependencyContainer $info $entry 'postgres'
  $volume = @(Convert-UpgradeJson (Invoke-DockerText @('volume','inspect',[string]$entry.volume)))[0]
  if ([string]$volume.Name -cne [string]$entry.volume -or [string]$volume.Driver -cne 'local' -or
      [string]$volume.CreatedAt -cne [string]$entry.volume_created_at -or
      [string]$volume.Mountpoint -cne [string]$entry.volume_mountpoint -or
      ($null -ne $volume.Options -and @($volume.Options.PSObject.Properties).Count)) { throw 'dependency_storage_identity_invalid' }
  if (-not [bool]$info.State.Running) {
    if ($AllowStopped) { return $false }
    throw 'dependency_database_identity_invalid'
  }
  if ((Invoke-DatabaseSql 'SELECT system_identifier::text FROM pg_control_system()') -cne
      [string]$Journal.migration.database_system_identifier) { throw 'dependency_database_identity_invalid' }
  return $true
}

function Assert-NoUnknownWriters {
  $sql = "SELECT count(*) FROM pg_stat_activity WHERE datname='ruisheng' AND pid<>pg_backend_pid() AND backend_type='client backend' AND application_name<>'ruisheng-upgrade-snapshot-$OperationId'"
  $wait = [Diagnostics.Stopwatch]::StartNew()
  while ($true) {
    Assert-LocksOwned
    $remaining = [Math]::Floor(15 - $wait.Elapsed.TotalSeconds)
    if ($remaining -lt 1) { throw "unknown_database_writer_present" }
    $count = Invoke-DatabaseSql $sql -TimeoutSeconds ([int]$remaining)
    if ($count -isnot [string] -or $count -cnotmatch '^(0|[1-9][0-9]*)$') {
      throw "unknown_database_writer_present"
    }
    if ($count -ceq "0") {
      Assert-LocksOwned
      if ($wait.Elapsed.TotalSeconds -ge 15) { throw "unknown_database_writer_present" }
      return
    }
    $remainingMilliseconds = [Math]::Floor(15000 - $wait.Elapsed.TotalMilliseconds)
    if ($remainingMilliseconds -le 0) { throw "unknown_database_writer_present" }
    Start-Sleep -Milliseconds ([int][Math]::Min(250, $remainingMilliseconds))
  }
}

function Stop-BoundedApplications {
  param([Parameter(Mandatory)]$Journal, [switch]$SkipWriterCheck, [switch]$ContainersOnly)
  Assert-LocksOwned
  $failures = @()
  if (-not $ContainersOnly) {
    try { Set-ApplicationRoleFence $Journal } catch { $failures += $_ }
  }
  foreach ($service in @("gw", "api", "web")) {
    try {
    Assert-LocksOwned
    if (-not (Invoke-DockerText @("ps", "-aq", "--filter", "name=^/ruisheng-$service`$"))) { continue }
    $info = @(Invoke-DockerText @("inspect", "ruisheng-$service") | ConvertFrom-Json)[0]
    $allowed = @([string]$Journal.migration.source_images.$service, [string]$Journal.migration.target_images.$service)
    if ([string]$info.Image -notin $allowed -or
        [string]$info.Config.Labels.'com.docker.compose.project' -cne "ruisheng-prod" -or
        [string]$info.Config.Labels.'com.docker.compose.service' -cne $service) { throw "maintenance_application_identity_drift" }
    Assert-LocksOwned
    try { [void](Invoke-DockerText @("update", "--restart=no", "ruisheng-$service")) }
    catch { $failures += $_ }
    Assert-LocksOwned
    [void](Invoke-DockerText @("stop", "--time", "30", "ruisheng-$service"))
    $state = Invoke-DockerText @("inspect", "--format", "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}", "ruisheng-$service")
    if ($state -cne "false|no") { throw "maintenance_application_stop_uncertain" }
    }
    catch { $failures += $_ }
  }
  if ($failures.Count) { throw $failures[0] }
  if ($ContainersOnly) { return }
  if (-not $SkipWriterCheck) { Assert-NoUnknownWriters }
}

function Restore-ApplicationRoles {
  param([Parameter(Mandatory)]$Journal)
  foreach ($name in @("ruisheng_api", "ruisheng_gw")) {
    Assert-LocksOwned
    $roles = @($Journal.migration.roles | Where-Object { [string]$_.name -ceq $name })
    if ($roles.Count -ne 1 -or $roles[0].login -isnot [bool]) { throw "recovery_role_receipt_invalid" }
    $login = if ([bool]$roles[0].login) { "LOGIN" } else { "NOLOGIN" }
    [void](Invoke-DatabaseSql "ALTER ROLE $name $login;")
  }
}

function Assert-BoundedMigrationStopped {
  param([Parameter(Mandatory)]$Journal, [switch]$SkipWriterCheck)
  $knownMigrators = @($Journal.migration.existing_migrators | ForEach-Object { [string]$_.id })
  $runningMigrators = Invoke-DockerText @("ps", "-aq", "--filter", "label=com.docker.compose.project=ruisheng-prod",
    "--filter", "label=com.docker.compose.service=migrate")
  foreach ($id in @($runningMigrators -split '\r?\n' | Where-Object { $_ })) {
    $observed = @(Convert-UpgradeJson (Invoke-DockerText @("inspect", $id)))[0]
    if ([string]$observed.Id -notin $knownMigrators -and
        [string]$observed.Name -cne "/$($Journal.migration.container_name)") { throw "unknown_migration_container_present" }
  }
  foreach ($entry in @($Journal.migration.existing_migrators)) {
    if ($null -eq $entry) { continue }
    $prior = @(Convert-UpgradeJson (Invoke-DockerText @("inspect", [string]$entry.id)))[0]
    if ([string]$prior.Id -cne [string]$entry.id -or [string]$prior.Image -cne [string]$entry.image_id -or
        [string]$prior.Image -cne [string]$Journal.migration.source_images.api -or
        [bool]$prior.State.Running -or [bool]$prior.State.Restarting -or
        [string]$prior.HostConfig.RestartPolicy.Name -cne "no") { throw "existing_migration_execution_uncertain" }
  }
  $name = [string]$Journal.migration.container_name
  if ($name -cne "ruisheng-migrate-$OperationId") { throw "migration_container_identity_invalid" }
  $ids = Invoke-DockerText @("ps", "-aq", "--filter", "name=^/$name`$")
  if ($ids) {
    $info = @(Invoke-DockerText @("inspect", $name) | ConvertFrom-Json)[0]
    if ([string]$info.Image -cne [string]$Journal.migration.target_images.api -or
        [string]$info.Config.Labels.'com.ruisheng.upgrade.operation' -cne $OperationId -or
        ([string]$Journal.migration.container_id -and [string]$Journal.migration.container_id -cne [string]$info.Id)) {
      throw "migration_container_identity_invalid"
    }
    if ([bool]$info.State.Running -or [bool]$info.State.Restarting) { throw "migration_execution_uncertain" }
  }
  elseif ([string]$Journal.migration.container_id -or
      [string]$Journal.migration.phase -cin @("migration_start_intent", "migration_running", "migration_verified")) {
    throw "migration_container_missing"
  }
  if (-not $SkipWriterCheck) { Assert-NoUnknownWriters }
}

function Get-BoundedRestoreDecision {
  param([Parameter(Mandatory)]$Journal, [Parameter(Mandatory)][string]$Head)
  if ($Head -ceq $BoundedTargetHead) { return "forward" }
  if ($Head -ceq $BoundedSourceHead -and -not [bool]$Journal.migration.application_start_attempted -and
      [string]$Journal.migration.phase -cne "migration_verified") { return "previous" }
  throw "recovery_database_head_unknown"
}

function Invoke-ContainerScript {
  param([string]$Container, [string]$Source, [int]$TimeoutSeconds = 120)
  $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Source))
  return Invoke-DockerText @("exec", $Container, "bash", "-c", "set -o pipefail; printf %s '$encoded' | base64 -d | bash") $TimeoutSeconds
}

function Get-DatabaseFingerprintSql {
  $restoreRole = "rs_restore_" + $OperationId.Replace("-", "")
  $sql = @'
SET timezone='UTC'; SET extra_float_digits=3; SET search_path=pg_catalog,public;
SELECT 'database|'||row_to_json(t)::text FROM (SELECT d.datname,pg_get_userbyid(d.datdba) AS owner,pg_encoding_to_char(d.encoding) AS encoding,d.datlocprovider,d.datcollate,d.datctype,d.daticulocale,d.datcollversion,d.datistemplate,d.datallowconn,d.datconnlimit,s.spcname AS tablespace,shobj_description(d.oid,'pg_database') AS comment,(SELECT jsonb_agg(jsonb_build_array(pg_get_userbyid(grantor),CASE WHEN grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(grantee)::text END,privilege_type,is_grantable) ORDER BY pg_get_userbyid(grantor),grantee=0,pg_get_userbyid(grantee),privilege_type,is_grantable) FROM aclexplode(COALESCE(d.datacl,acldefault('d',d.datdba)))) AS acl FROM pg_database d JOIN pg_tablespace s ON s.oid=d.dattablespace WHERE d.datname=current_database()) t;
SELECT 'database_settings|'||row_to_json(t)::text FROM (SELECT CASE WHEN s.setrole=0 THEN NULL ELSE pg_get_userbyid(s.setrole)::text END AS role,ARRAY(SELECT v FROM unnest(s.setconfig) v ORDER BY v) AS settings FROM pg_db_role_setting s WHERE s.setdatabase=(SELECT oid FROM pg_database WHERE datname=current_database()) ORDER BY s.setrole=0,pg_get_userbyid(s.setrole)) t;
SELECT format('SELECT %L || md5(row_to_json(t)::text) FROM %I.%I t ORDER BY 1;', n.nspname||'.'||c.relname||'|', n.nspname,c.relname)
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY n.nspname,c.relname
\gexec
SELECT 'columns|'||row_to_json(t)::text FROM (SELECT table_name,column_name,ordinal_position,column_default,is_nullable,data_type,character_maximum_length FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position) t;
SELECT 'constraints|'||row_to_json(t)::text FROM (SELECT c.relname,k.conname,k.contype,k.convalidated,k.condeferrable,k.condeferred,k.connoinherit,CASE WHEN k.contype<>'c' THEN pg_get_constraintdef(k.oid) END AS definition FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname,k.conname) t;
SELECT 'indexes|'||row_to_json(t)::text FROM (SELECT c.relname AS tablename,x.relname AS indexname,a.amname,i.indnatts,i.indnkeyatts,i.indisunique,i.indnullsnotdistinct,i.indisprimary,i.indisexclusion,i.indimmediate,i.indisclustered,i.indisvalid,i.indcheckxmin,i.indisready,i.indislive,i.indisreplident,i.indoption::text,x.reloptions FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_class x ON x.oid=i.indexrelid JOIN pg_am a ON a.oid=x.relam WHERE n.nspname='public' ORDER BY c.relname,x.relname) t;
SELECT 'rls|'||row_to_json(t)::text FROM (SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY relname) t;
SELECT 'policies|'||row_to_json(t)::text FROM (SELECT tablename,policyname,permissive,roles,cmd,qual,with_check FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname) t;
SELECT 'routines|'||row_to_json(t)::text FROM (SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) AS arguments,pg_get_functiondef(p.oid) AS definition,pg_get_userbyid(p.proowner) AS owner,p.prokind,p.prosecdef,p.proleakproof,p.proisstrict,p.proretset,p.provolatile,p.proparallel,p.procost,p.prorows,p.proconfig,CASE WHEN p.prosupport=0 THEN NULL ELSE p.prosupport::regprocedure::text END AS support,(SELECT jsonb_agg(jsonb_build_array(pg_get_userbyid(grantor),CASE WHEN grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(grantee)::text END,privilege_type,is_grantable) ORDER BY pg_get_userbyid(grantor),grantee=0,pg_get_userbyid(grantee),privilege_type,is_grantable) FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner)))) AS acl FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind IN ('f','p','w') ORDER BY n.nspname,p.proname,pg_get_function_identity_arguments(p.oid)) t;
SELECT 'triggers|'||row_to_json(t)::text FROM (SELECT c.relname,CASE WHEN NOT g.tgisinternal THEN g.tgname END AS name,CASE WHEN NOT g.tgisinternal THEN pg_get_triggerdef(g.oid,false) END AS definition,g.tgenabled,g.tgisinternal,g.tgtype,g.tgdeferrable,g.tginitdeferred,g.tgnargs,g.tgargs,CASE WHEN g.tgisinternal THEN pg_get_expr(g.tgqual,g.tgrelid) END AS condition,g.tgoldtable,g.tgnewtable,p.oid::regprocedure::text AS routine,k.conname,rc.oid::regclass::text AS referenced_relation,ARRAY(SELECT a.attname FROM unnest(g.tgattr) WITH ORDINALITY x(attnum,ord) JOIN pg_attribute a ON a.attrelid=g.tgrelid AND a.attnum=x.attnum ORDER BY x.ord) AS columns FROM pg_trigger g JOIN pg_class c ON c.oid=g.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=g.tgfoid LEFT JOIN pg_constraint k ON k.oid=g.tgconstraint LEFT JOIN pg_class rc ON rc.oid=g.tgconstrrelid WHERE n.nspname='public' ORDER BY c.relname,g.tgisinternal,name,k.conname,routine,g.tgtype,referenced_relation) t;
SELECT 'sequences|'||row_to_json(t)::text FROM (SELECT sequencename,sequenceowner,data_type,start_value,min_value,max_value,increment_by,cycle,cache_size,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename) t;
SELECT 'roles|'||row_to_json(t)::text FROM (SELECT a.rolname,a.rolsuper,a.rolinherit,a.rolcreaterole,a.rolcreatedb,a.rolcanlogin,a.rolreplication,a.rolconnlimit,a.rolbypassrls,a.rolvaliduntil,a.rolpassword,r.rolconfig FROM pg_authid a JOIN pg_roles r USING(oid) WHERE a.rolname <> '__RESTORE_ROLE__' ORDER BY a.rolname) t;
SELECT 'members|'||(to_jsonb(a)-'roleid'-'member'-'grantor'||jsonb_build_object('role',pg_get_userbyid(roleid),'member',pg_get_userbyid(member),'grantor',CASE WHEN grantor=10 AND pg_get_userbyid(member)='pg_monitor' AND pg_get_userbyid(roleid) IN ('pg_read_all_settings','pg_read_all_stats','pg_stat_scan_tables') AND NOT admin_option THEN '<initdb>' ELSE pg_get_userbyid(grantor)::text END))::text FROM pg_auth_members a WHERE pg_get_userbyid(roleid)<>'__RESTORE_ROLE__' AND pg_get_userbyid(member)<>'__RESTORE_ROLE__' ORDER BY pg_get_userbyid(roleid),pg_get_userbyid(member),pg_get_userbyid(grantor);
SELECT 'relation_acl|'||row_to_json(t)::text FROM (SELECT c.relname,c.relkind,pg_get_userbyid(c.relowner) AS owner,c.relacl IS NULL AS default_acl,(SELECT jsonb_agg(jsonb_build_array(pg_get_userbyid(grantor),CASE WHEN grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(grantee)::text END,privilege_type,is_grantable) ORDER BY pg_get_userbyid(grantor),grantee=0,pg_get_userbyid(grantee),privilege_type,is_grantable) FROM aclexplode(c.relacl)) AS acl FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname) t;
SELECT 'schema_acl|'||row_to_json(t)::text FROM (SELECT nspname,pg_get_userbyid(nspowner) AS owner,nspacl IS NULL AS default_acl,(SELECT jsonb_agg(jsonb_build_array(pg_get_userbyid(grantor),CASE WHEN grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(grantee)::text END,privilege_type,is_grantable) ORDER BY pg_get_userbyid(grantor),grantee=0,pg_get_userbyid(grantee),privilege_type,is_grantable) FROM aclexplode(nspacl)) AS acl FROM pg_namespace WHERE nspname='public' ORDER BY nspname) t;
SELECT 'default_acl|'||row_to_json(t)::text FROM (SELECT pg_get_userbyid(defaclrole) AS role,COALESCE(nspname,'') AS schema,defaclobjtype,(SELECT jsonb_agg(jsonb_build_array(pg_get_userbyid(grantor),CASE WHEN grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(grantee)::text END,privilege_type,is_grantable) ORDER BY pg_get_userbyid(grantor),grantee=0,pg_get_userbyid(grantee),privilege_type,is_grantable) FROM aclexplode(defaclacl)) AS acl FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace WHERE pg_get_userbyid(defaclrole)<>'__RESTORE_ROLE__' ORDER BY role,schema,defaclobjtype) t;
SELECT format('SELECT %L || row_to_json(t)::text FROM (SELECT last_value,is_called FROM %I.%I) t;', 'sequence_state|'||c.relname||'|', n.nspname,c.relname)
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='S' ORDER BY c.relname
\gexec
SELECT 'hypertables|'||row_to_json(t)::text FROM (SELECT * FROM _timescaledb_catalog.hypertable ORDER BY id) t;
SELECT 'chunks|'||row_to_json(t)::text FROM (SELECT * FROM _timescaledb_catalog.chunk ORDER BY id) t;
SELECT 'extensions|'||row_to_json(t)::text FROM (SELECT extname,extversion,pg_get_userbyid(extowner) AS owner,nspname FROM pg_extension JOIN pg_namespace ON pg_namespace.oid=extnamespace WHERE extname='timescaledb' ORDER BY extname) t;
'@
  return $sql.Replace("__RESTORE_ROLE__", $restoreRole)
}

function Get-DatabasePropertiesSql {
  # This emits an inert, protected SQL asset from the dump's exported snapshot.
  # Creation happens before Timescale pre_restore; ACL/settings are replayed only
  # after post_restore. Unsupported creation semantics fail before migration.
  return @'
SET search_path=pg_catalog;
DO $check$
DECLARE d pg_database%ROWTYPE;
BEGIN
  SELECT * INTO STRICT d FROM pg_database WHERE datname=current_database();
  IF d.datname<>'ruisheng' OR d.datistemplate OR NOT d.datallowconn OR d.datconnlimit < -1
     OR d.datlocprovider<>'c' OR d.daticulocale IS NOT NULL
     OR d.dattablespace<>(SELECT oid FROM pg_tablespace WHERE spcname='pg_default')
     OR d.datcollversion IS DISTINCT FROM pg_database_collation_actual_version(d.oid)
     OR EXISTS(SELECT 1 FROM pg_shseclabel WHERE classoid='pg_database'::regclass AND objoid=d.oid)
     OR EXISTS(SELECT 1 FROM pg_db_role_setting s,unnest(s.setconfig) v
       WHERE s.setdatabase=d.oid AND split_part(v,'=',1) IN
       ('session_preload_libraries','local_preload_libraries','shared_preload_libraries','timescaledb.restoring'))
  THEN RAISE EXCEPTION 'backup_database_properties_unsupported'; END IF;
  IF EXISTS(
    WITH RECURSIVE acl AS (SELECT * FROM aclexplode(COALESCE(d.datacl,acldefault('d',d.datdba)))),
    paths(role,privilege,seen) AS (
      SELECT d.datdba,p,ARRAY[d.datdba] FROM unnest(ARRAY['CONNECT','CREATE','TEMPORARY']) p
      UNION ALL
      SELECT a.grantee,a.privilege_type,p.seen||a.grantee FROM paths p JOIN acl a
        ON a.grantor=p.role AND a.privilege_type=p.privilege AND a.is_grantable
        WHERE a.grantee<>0 AND NOT a.grantee=ANY(p.seen)
    )
    SELECT 1 FROM acl a WHERE NOT EXISTS(SELECT 1 FROM paths p WHERE p.role=a.grantor AND p.privilege=a.privilege_type)
  ) THEN RAISE EXCEPTION 'backup_database_acl_unsupported'; END IF;
END $check$;
SELECT E'\\if :restore_create';
SELECT format('CREATE DATABASE %I OWNER %I TEMPLATE template0 ENCODING %L LOCALE_PROVIDER libc LC_COLLATE %L LC_CTYPE %L;',datname,pg_get_userbyid(datdba),pg_encoding_to_char(encoding),datcollate,datctype) FROM pg_database WHERE datname=current_database();
SELECT E'\\else';
SELECT format('ALTER DATABASE %I CONNECTION LIMIT %s; COMMENT ON DATABASE %I IS %L; REVOKE ALL ON DATABASE %I FROM PUBLIC; REVOKE ALL ON DATABASE %I FROM %I;',datname,datconnlimit,datname,shobj_description(oid,'pg_database'),datname,datname,pg_get_userbyid(datdba)) FROM pg_database WHERE datname=current_database();
WITH RECURSIVE d AS (SELECT * FROM pg_database WHERE datname=current_database()),
acl AS (SELECT a.* FROM d,aclexplode(COALESCE(d.datacl,acldefault('d',d.datdba))) a),
paths(role,privilege,depth,seen) AS (
  SELECT d.datdba,p,0,ARRAY[d.datdba] FROM d,unnest(ARRAY['CONNECT','CREATE','TEMPORARY']) p
  UNION ALL
  SELECT a.grantee,a.privilege_type,p.depth+1,p.seen||a.grantee FROM paths p JOIN acl a
    ON a.grantor=p.role AND a.privilege_type=p.privilege AND a.is_grantable
    WHERE a.grantee<>0 AND NOT a.grantee=ANY(p.seen)
), ordered_acl AS (
  SELECT a.*,(SELECT min(p.depth) FROM paths p WHERE p.role=a.grantor AND p.privilege=a.privilege_type) AS depth FROM acl a
)
SELECT CASE WHEN a.depth IS NULL THEN 'DO $reject$ BEGIN RAISE EXCEPTION ''backup_database_acl_unsupported''; END $reject$;'
  ELSE format('SET ROLE %I; GRANT %s ON DATABASE %I TO %s%s; RESET ROLE;',pg_get_userbyid(a.grantor),a.privilege_type,d.datname,
    CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(a.grantee)) END,
    CASE WHEN a.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END) END
FROM ordered_acl a,d ORDER BY a.depth NULLS FIRST,pg_get_userbyid(a.grantor),a.grantee=0,pg_get_userbyid(a.grantee),a.privilege_type,a.is_grantable;
SELECT format('ALTER DATABASE %I RESET ALL;',current_database());
SELECT format('DO %L;',format('BEGIN PERFORM pg_catalog.set_config(%L,%L,true); EXECUTE %L; END;',split_part(v,'=',1),substr(v,strpos(v,'=')+1),
  CASE WHEN s.setrole=0 THEN format('ALTER DATABASE %I SET %I FROM CURRENT;',current_database(),split_part(v,'=',1))
  ELSE format('ALTER ROLE %I IN DATABASE %I SET %I FROM CURRENT;',pg_get_userbyid(s.setrole),current_database(),split_part(v,'=',1)) END))
FROM pg_db_role_setting s,unnest(s.setconfig) v WHERE s.setdatabase=(SELECT oid FROM pg_database WHERE datname=current_database())
ORDER BY s.setrole=0,pg_get_userbyid(s.setrole),v;
SELECT E'\\endif';
'@
}

function Get-DatabaseExpressionProofSql {
  # PostgreSQL can deparse casts differently after a dump; reparse only on the isolated clone.
  return @'
SET search_path=pg_catalog,public;
SELECT $proof$
BEGIN;
SET LOCAL search_path=pg_catalog,public;
CREATE FUNCTION pg_temp.rs_verify_expression(kind text, tab text, obj text, ddl text) RETURNS void LANGUAGE plpgsql AS $body$
DECLARE expected text; actual text; probe oid; restored oid; pos integer;
BEGIN
  EXECUTE format('CREATE TEMP TABLE rs_expression_probe (LIKE public.%I)',tab);
  EXECUTE ddl;
  IF kind='check' THEN
    SELECT pg_get_constraintdef(oid) INTO expected FROM pg_constraint WHERE conrelid='pg_temp.rs_expression_probe'::regclass AND conname='rs_expression_check';
    SELECT pg_get_constraintdef(k.oid) INTO actual FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=tab AND k.conname=obj;
    IF expected IS DISTINCT FROM actual THEN RAISE EXCEPTION 'restore_expression_mismatch'; END IF;
  ELSE
    probe := 'pg_temp.rs_expression_index'::regclass;
    restored := to_regclass(format('public.%I',obj));
    IF restored IS NULL THEN RAISE EXCEPTION 'restore_expression_mismatch'; END IF;
    FOR pos IN 1..(SELECT indnatts FROM pg_index WHERE indexrelid=probe) LOOP
      IF pg_get_indexdef(probe,pos,false) IS DISTINCT FROM pg_get_indexdef(restored,pos,false) THEN RAISE EXCEPTION 'restore_expression_mismatch'; END IF;
    END LOOP;
    SELECT pg_get_expr(indpred,indrelid) INTO expected FROM pg_index WHERE indexrelid=probe;
    SELECT pg_get_expr(indpred,indrelid) INTO actual FROM pg_index WHERE indexrelid=restored;
    IF expected IS DISTINCT FROM actual THEN RAISE EXCEPTION 'restore_expression_mismatch'; END IF;
  END IF;
  DROP TABLE pg_temp.rs_expression_probe;
END $body$;
$proof$;
SELECT format('SELECT pg_temp.rs_verify_expression(%L,%L,%L,%L);','check',c.relname,k.conname,format('ALTER TABLE pg_temp.rs_expression_probe ADD CONSTRAINT rs_expression_check %s',pg_get_constraintdef(k.oid)))
FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND k.contype='c' ORDER BY c.relname,k.conname;
SELECT format('SELECT pg_temp.rs_verify_expression(%L,%L,%L,%L);','index',c.relname,x.relname,
  format('CREATE %s INDEX rs_expression_index ON pg_temp.rs_expression_probe USING %I (%s)%s%s%s',
    CASE WHEN i.indisunique THEN 'UNIQUE' ELSE '' END,a.amname,
    (SELECT string_agg(pg_get_indexdef(i.indexrelid,pos,false),', ' ORDER BY pos) FROM generate_series(1,i.indnkeyatts) pos),
    CASE WHEN i.indnatts>i.indnkeyatts THEN ' INCLUDE ('||(SELECT string_agg(pg_get_indexdef(i.indexrelid,pos,false),', ' ORDER BY pos) FROM generate_series(i.indnkeyatts+1,i.indnatts) pos)||')' ELSE '' END,
    CASE WHEN i.indnullsnotdistinct THEN ' NULLS NOT DISTINCT' ELSE '' END,
    CASE WHEN i.indpred IS NOT NULL THEN ' WHERE '||pg_get_expr(i.indpred,i.indrelid) ELSE '' END))
FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_class x ON x.oid=i.indexrelid JOIN pg_am a ON a.oid=x.relam WHERE n.nspname='public' ORDER BY c.relname,x.relname;
SELECT 'ROLLBACK;';
'@
}

function New-BoundedDatabaseBackup {
  param([Parameter(Mandatory)]$Journal)
  Assert-LocksOwned
  if (Test-Path -LiteralPath $BackupDirectory) { throw "backup_directory_conflict" }
  New-Item -ItemType Directory -Path $BackupDirectory | Out-Null
  Set-RestrictedTree $BackupDirectory
  $prefix = "/tmp/ruisheng-upgrade-$OperationId"
  # All sequential commands share 2400 seconds; the helper has a finite 60-second shutdown reserve.
  $snapshotDeadline=[DateTimeOffset]::UtcNow.AddSeconds(2400)
  $snapshotHolder=@{ application_name="ruisheng-upgrade-snapshot-$OperationId"; lifetime_seconds=2460;
    deadline=$snapshotDeadline.ToString('o'); observed_start=$false; stopped=$false }
  if ($Journal.migration -is [Collections.IDictionary]) { $Journal.migration.snapshot_holder=$snapshotHolder }
  else { $Journal.migration | Add-Member -NotePropertyName snapshot_holder -NotePropertyValue $snapshotHolder -Force }
  Save-BoundedPhase $Journal "backup_snapshot_intent"
  $snapshotSql = "SET statement_timeout='2460s';`nBEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;`n\o $prefix.snapshot`nSELECT pg_export_snapshot();`n\o /dev/null`nSELECT pg_sleep(2460);`nROLLBACK;`n"
  $encodedSql = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($snapshotSql))
  [void](Invoke-DockerText @("exec", "ruisheng-postgres", "bash", "-c",
    "umask 077; printf %s '$encodedSql' | base64 -d > '$prefix.sql'"))
  try {
    $script:BoundedSnapshotDeadline=$snapshotDeadline
    [void](Invoke-DockerText @("exec", "-d", "-e", "PGAPPNAME=ruisheng-upgrade-snapshot-$OperationId",
      "ruisheng-postgres", "timeout", "2465", "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "ruisheng_admin",
      "-d", "ruisheng", "-Atq", "-f", "$prefix.sql"))
    $snapshot = ""
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
      try { $snapshot = Invoke-DockerText @("exec", "ruisheng-postgres", "cat", "$prefix.snapshot") 10 }
      catch { $snapshot = "" }
      if ($snapshot -match '^[0-9A-F]+-[0-9A-F]+-[0-9]+$') { break }
      Start-Sleep -Milliseconds 200
    }
    if ($snapshot -notmatch '^[0-9A-F]+-[0-9A-F]+-[0-9]+$') { throw "backup_snapshot_unavailable" }
    $snapshotHolder.observed_start=$true
    Save-BoundedPhase $Journal 'backup_snapshot_running'
    $fingerprintSql = "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;`nSET TRANSACTION SNAPSHOT '$snapshot';`n" + (Get-DatabaseFingerprintSql) + "`nROLLBACK;"
    $fingerprintEncoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($fingerprintSql))
    $fingerprint = Invoke-ContainerScript "ruisheng-postgres" @"
set -euo pipefail
umask 077
printf %s '$fingerprintEncoded' | base64 -d | psql -X -qAt -v ON_ERROR_STOP=1 -U ruisheng_admin -d ruisheng | sha256sum | cut -d' ' -f1
"@ 300
    if ($fingerprint -notmatch '^[0-9a-f]{64}$') { throw "backup_fingerprint_invalid" }
    $proofSql = "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;`nSET TRANSACTION SNAPSHOT '$snapshot';`n" + (Get-DatabaseExpressionProofSql) + "`nROLLBACK;"
    $proofEncoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($proofSql))
    $propertiesSql = "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;`nSET TRANSACTION SNAPSHOT '$snapshot';`n" + (Get-DatabasePropertiesSql) + "`nROLLBACK;"
    $propertiesEncoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($propertiesSql))
    [void](Invoke-ContainerScript "ruisheng-postgres" @"
set -euo pipefail
umask 077
printf %s '$proofEncoded' | base64 -d | psql -X -qAt -v ON_ERROR_STOP=1 -U ruisheng_admin -d ruisheng > '$prefix.expressions.sql'
printf %s '$propertiesEncoded' | base64 -d | psql -X -qAt -v ON_ERROR_STOP=1 -U ruisheng_admin -d ruisheng > '$prefix.database-properties.sql'
"@ 300)
    [void](Invoke-ContainerScript "ruisheng-postgres" "set -e; umask 077; pg_dump -U ruisheng_admin -d ruisheng --format=custom --snapshot='$snapshot' --file='$prefix.dump'" 600)
    [void](Invoke-ContainerScript "ruisheng-postgres" "set -e; umask 077; pg_dumpall -U ruisheng_admin --roles-only --file='$prefix.roles.sql'" 120)
    $version = Invoke-DatabaseSql "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET TRANSACTION SNAPSHOT '$snapshot'; SELECT json_build_object('postgres',current_setting('server_version_num'),'timescale',(SELECT extversion FROM pg_extension WHERE extname='timescaledb'),'owner',(SELECT pg_get_userbyid(extowner) FROM pg_extension WHERE extname='timescaledb')); ROLLBACK;" | ConvertFrom-Json
    if ([string]$version.owner -cne 'ruisheng_admin') { throw "backup_timescale_owner_invalid" }
    $databasePath = Join-Path $BackupDirectory "ruisheng.dump"
    $rolesPath = Join-Path $BackupDirectory "roles.sql"
    $proofPath = Join-Path $BackupDirectory "expressions.sql"
    $propertiesPath = Join-Path $BackupDirectory "database-properties.sql"
    [void](Invoke-DockerText @("cp", "ruisheng-postgres:$prefix.dump", $databasePath) 300)
    [void](Invoke-DockerText @("cp", "ruisheng-postgres:$prefix.roles.sql", $rolesPath))
    [void](Invoke-DockerText @("cp", "ruisheng-postgres:$prefix.expressions.sql", $proofPath))
    [void](Invoke-DockerText @("cp", "ruisheng-postgres:$prefix.database-properties.sql", $propertiesPath))
    Set-RestrictedFileAcl $databasePath
    Set-RestrictedFileAcl $rolesPath
    Set-RestrictedFileAcl $proofPath
    Set-RestrictedFileAcl $propertiesPath
    $receipt = [ordered]@{
      schema_version = 4; operation_id = $OperationId; snapshot_id = $snapshot
      source_identity = [string]$Journal.previous_release.logical_identity
      candidate_identity = [string]$Journal.candidate.logical_identity
      database_head = $BoundedSourceHead; fingerprint_sha256 = $fingerprint
      postgres_version = [string]$version.postgres; timescale_version = [string]$version.timescale
      timescale_owner = [string]$version.owner; postgres_image = [string]$Journal.migration.source_images.postgres
      database = [ordered]@{ path = $databasePath; sha256 = (Get-FileHash $databasePath -Algorithm SHA256).Hash.ToLowerInvariant() }
      roles = [ordered]@{ path = $rolesPath; sha256 = (Get-FileHash $rolesPath -Algorithm SHA256).Hash.ToLowerInvariant() }
      expressions = [ordered]@{ path = $proofPath; sha256 = (Get-FileHash $proofPath -Algorithm SHA256).Hash.ToLowerInvariant() }
      database_properties = [ordered]@{ path = $propertiesPath; sha256 = (Get-FileHash $propertiesPath -Algorithm SHA256).Hash.ToLowerInvariant() }
      restore_verified = $false; restore_container = "ruisheng-restore-$OperationId"
      restore_container_id = ''; restore_volume = "ruisheng-restore-$OperationId"
      restore_volume_created_at = ''; restore_asset_token = [Guid]::NewGuid().ToString('D')
      created_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-JsonAtomic (Join-Path $BackupDirectory "backup-receipt.json") $receipt
    return $receipt
  }
  finally {
    $script:BoundedSnapshotDeadline=$null
    Stop-BoundedSnapshot $Journal
  }
}

function Stop-BoundedSnapshot {
  param([Parameter(Mandatory)]$Journal)
  if ($null -eq $Journal.migration.snapshot_holder -or [bool]$Journal.migration.snapshot_holder.stopped) { return }
  $holder=$Journal.migration.snapshot_holder
  if ([string]$holder.application_name -cne "ruisheng-upgrade-snapshot-$OperationId" -or
      [int]$holder.lifetime_seconds -ne 2460) { throw 'backup_snapshot_identity_invalid' }
  Assert-LocksOwned
  [void](Assert-BoundedDatabaseIdentity $Journal)
  $filter="application_name='ruisheng-upgrade-snapshot-$OperationId' AND usename='ruisheng_admin' AND datname='ruisheng' AND backend_type='client backend'"
  $count=Invoke-DatabaseSql "SELECT count(*) FROM pg_stat_activity WHERE $filter"
  if ($count -ceq '0' -and -not [bool]$holder.observed_start) { throw 'backup_snapshot_start_completion_unknown' }
  if ($count -cne '0') {
    if ($count -cne '1' -or (Invoke-DatabaseSql "SELECT count(*) FROM pg_stat_activity WHERE $filter AND query='SELECT pg_sleep(2460);'") -cne '1') {
      throw 'backup_snapshot_stop_uncertain'
    }
    $holder.observed_start=$true
    Assert-LocksOwned
    [void](Invoke-DatabaseSql "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE $filter AND query='SELECT pg_sleep(2460);'")
    for ($attempt=0; $attempt -lt 20; $attempt++) {
      if ((Invoke-DatabaseSql "SELECT count(*) FROM pg_stat_activity WHERE $filter") -ceq '0') { break }
      Start-Sleep -Milliseconds 100
    }
    if ((Invoke-DatabaseSql "SELECT count(*) FROM pg_stat_activity WHERE $filter") -cne '0') { throw 'backup_snapshot_stop_uncertain' }
  }
  $holder.stopped=$true
  Assert-LocksOwned
  $intent=$Journal.migration.docker_intent
  if ($null -ne $intent -and [string]$intent.scope -ceq 'snapshot') {
    if ([string]$intent.application_name -cne [string]$holder.application_name) { throw 'backup_snapshot_identity_invalid' }
    $Journal.migration.docker_intent=$null
  }
  try { Write-JsonAtomic $JournalPath $Journal }
  catch {
    $holder.stopped=$false
    if ($null -ne $intent) { $Journal.migration.docker_intent=$intent }
    throw
  }
}

function Test-BoundedBackupRestore {
  param([Parameter(Mandatory)]$Journal)
  Assert-LocksOwned
  Assert-BoundedBackupReceipt $Journal
  $receipt = $Journal.backup
  $name = [string]$receipt.restore_container
  if ($name -cne "ruisheng-restore-$OperationId") { throw "restore_container_identity_invalid" }
  if ((Invoke-DockerText @("ps", "-aq", "--filter", "name=^/$name`$")) -or
      (Invoke-DockerText @("volume", "ls", "-q", "--filter", "name=^$name`$"))) {
    throw "restore_asset_conflict"
  }
  $user = "rs_restore_" + $OperationId.Replace("-", "")
  if ((Invoke-DatabaseSql "SELECT count(*) FROM pg_roles WHERE rolname='$user'") -cne "0") {
    throw "restore_bootstrap_role_conflict"
  }
  $image = [string]$Journal.migration.source_images.postgres
  if ((Invoke-DockerText @("inspect", "--format", "{{.Image}}", "ruisheng-postgres")) -cne $image) {
    throw "restore_source_image_mismatch"
  }
  $estimate = Get-DatabaseBackupEstimate
  $dockerInfo = Invoke-DockerText @("info", "--format", "{{json .}}") | ConvertFrom-Json
  if ([long]$dockerInfo.MemTotal -lt 3GB) { throw "restore_memory_insufficient" }
  $freeText = Invoke-DockerText @("exec", "ruisheng-postgres", "sh", "-c", "df -Pk /var/lib/postgresql/data | tail -1 | awk '{print `$4}'")
  [long]$freeKb = 0
  if (-not [long]::TryParse($freeText, [ref]$freeKb) -or ($freeKb * 1KB) -lt [long]$estimate.required_bytes) {
    throw "restore_docker_disk_insufficient"
  }
  Save-BoundedPhase $Journal "restore_proof_intent"
  $token = [string]$receipt.restore_asset_token
  [void](Invoke-DockerText @("volume", "create", "--label", "com.ruisheng.upgrade.operation=$OperationId",
    "--label", "com.ruisheng.upgrade.restore-token=$token", $name))
  $volume = @(Convert-UpgradeJson (Invoke-DockerText @("volume", "inspect", $name)))[0]
  if ([string]$volume.Name -cne $name -or [string]$volume.Driver -cne 'local' -or
      ($null -ne $volume.Options -and @($volume.Options.PSObject.Properties).Count -ne 0) -or
      [string]$volume.Labels.'com.ruisheng.upgrade.operation' -cne $OperationId -or
      [string]$volume.Labels.'com.ruisheng.upgrade.restore-token' -cne $token) {
    throw "restore_volume_identity_invalid"
  }
  $receipt.restore_volume_created_at = [string]$volume.CreatedAt
  Write-JsonAtomic (Join-Path $BackupDirectory "backup-receipt.json") $receipt
  Save-BoundedPhase $Journal "restore_volume_created"
  try {
    Assert-LocksOwned
    [void](Invoke-DockerText @("create", "--name", $name, "--network", "none", "--restart", "no",
      "--label", "com.ruisheng.upgrade.operation=$OperationId", "--label", "com.ruisheng.upgrade.restore-token=$token",
      "--memory", "2g", "--pids-limit", "128", "--mount", "type=volume,src=$name,dst=/var/lib/postgresql/data",
      "-e", "POSTGRES_USER=$user", "-e", "POSTGRES_DB=postgres", "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
      $image, "postgres", "-c", "timescaledb.max_background_workers=0", "-c", "timescaledb.telemetry_level=off"))
    $container = Assert-BoundedRestoreAssets $receipt
    $receipt.restore_container_id = [string]$container.Id
    Write-JsonAtomic (Join-Path $BackupDirectory "backup-receipt.json") $receipt
    Save-BoundedPhase $Journal "restore_container_created"
    [void](Invoke-DockerText @("start", $name))
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
      Assert-LocksOwned
      # The initdb temporary postmaster accepts sockets, but does not listen on TCP.
      try {
        $probe = Invoke-DockerText @("exec", $name, "psql", "-h", "127.0.0.1", "-X", "-v", "ON_ERROR_STOP=1",
          "-U", $user, "-d", "postgres", "-Atqc", "SELECT 1")
        if ($probe -ceq '1') { $ready = $true; break }
      }
      catch { Start-Sleep -Seconds 1 }
    }
    if (-not $ready) { throw "restore_database_not_ready" }
    $version = Invoke-DatabaseSql "SELECT current_setting('server_version_num')" $name $user "postgres"
    if ($version -cne [string]$receipt.postgres_version) { throw "restore_postgres_version_mismatch" }
    [void](Invoke-DockerText @("cp", [string]$receipt.roles.path, "${name}:/tmp/roles.sql"))
    [void](Invoke-DockerText @("cp", [string]$receipt.database.path, "${name}:/tmp/database.dump") 300)
    [void](Invoke-DockerText @("exec", $name, "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", $user,
      "-d", "postgres", "-f", "/tmp/roles.sql") 120)
    [void](Invoke-DockerText @("cp", [string]$receipt.database_properties.path, "${name}:/tmp/database-properties.sql"))
    [void](Invoke-DockerText @("exec", $name, "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-v", "restore_create=1", "-U", $user,
      "-d", "postgres", "-f", "/tmp/database-properties.sql") 120)
    [void](Invoke-DatabaseSql "CREATE EXTENSION timescaledb; SELECT public.timescaledb_pre_restore();" $name 'ruisheng_admin')
    $extension = Invoke-DatabaseSql "SELECT extversion FROM pg_extension WHERE extname='timescaledb'" $name $user
    if ($extension -cne [string]$receipt.timescale_version) { throw "restore_timescale_version_mismatch" }
    [void](Invoke-DockerText @("exec", $name, "pg_restore", "--exit-on-error", "--jobs=1",
      "-U", $user, "-d", "ruisheng", "/tmp/database.dump") 600)
    [void](Invoke-DatabaseSql "SELECT public.timescaledb_post_restore();" $name $user)
    Assert-BoundedBackupReceipt $Journal
    [void](Invoke-DockerText @("exec", $name, "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-v", "restore_create=0", "-U", $user,
      "-d", "postgres", "-f", "/tmp/database-properties.sql") 120)
    [void](Invoke-DockerText @("cp", [string]$receipt.expressions.path, "${name}:/tmp/expressions.sql"))
    [void](Invoke-DockerText @("exec", $name, "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-U", $user,
      "-d", "ruisheng", "-f", "/tmp/expressions.sql") 300)
    $fingerprintEncoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((Get-DatabaseFingerprintSql)))
    $fingerprint = Invoke-ContainerScript $name @"
set -euo pipefail
printf %s '$fingerprintEncoded' | base64 -d | psql -X -qAt -v ON_ERROR_STOP=1 -U '$user' -d ruisheng | sha256sum | cut -d' ' -f1
"@ 300
    if ($fingerprint -cne [string]$receipt.fingerprint_sha256 -or
        (Invoke-DatabaseSql "SELECT version_num FROM alembic_version" $name $user) -cne $BoundedSourceHead) {
      throw "restore_fingerprint_mismatch"
    }
    $receipt.restore_verified = $true
    Write-JsonAtomic (Join-Path $BackupDirectory "backup-receipt.json") $receipt
    Save-BoundedPhase $Journal "backup_restore_verified"
  }
  finally {
    Stop-BoundedRestoreAssets $Journal
  }
}

function Stop-BoundedRestoreAssets {
  param([Parameter(Mandatory)]$Journal)
  Assert-LocksOwned
  if ($null -eq $Journal.backup) { return }
  Assert-BoundedBackupReceipt $Journal
  $receipt=$Journal.backup
  $path=Join-Path $BackupDirectory 'backup-receipt.json'
  Assert-RestrictedFile $path
  $stored=Convert-UpgradeJson (Get-Content -LiteralPath $path -Raw -Encoding UTF8)
  $proof=@{ backup=$stored; migration=$Journal.migration; previous_release=$Journal.previous_release; candidate=$Journal.candidate }
  Assert-BoundedBackupReceipt $proof
  foreach ($property in @($stored.PSObject.Properties)) {
    $key=$property.Name
    if ($key -cin @('restore_container_id','restore_volume_created_at')) {
      if ([string]$receipt.$key -and [string]$receipt.$key -cne [string]$stored.$key) { throw 'restore_asset_identity_invalid' }
    }
    elseif ($key -ceq 'restore_verified') {
      if ([bool]$receipt.restore_verified -and -not [bool]$stored.restore_verified) { throw 'restore_asset_identity_invalid' }
    }
    elseif (($receipt.$key | ConvertTo-Json -Depth 4 -Compress) -cne ($stored.$key | ConvertTo-Json -Depth 4 -Compress)) {
      throw 'restore_asset_identity_invalid'
    }
  }
  $Journal.backup=$stored
  $name="ruisheng-restore-$OperationId"
  if (Invoke-DockerText @('ps','-aq','--filter',"name=^/$name`$")) {
    $container=Assert-BoundedRestoreAssets $stored
    Assert-LocksOwned
    [void](Invoke-DockerText @('stop','--time','30',[string]$container.Id))
    $after=Assert-BoundedRestoreAssets $stored
    if ([string]$after.Id -cne [string]$container.Id -or [bool]$after.State.Running -or [bool]$after.State.Restarting) {
      throw 'restore_asset_stop_uncertain'
    }
    $intent=$Journal.migration.docker_intent
    if ($null -ne $intent -and [string]$intent.scope -ceq 'restore') {
      if ([string]$intent.name -cne $name -or [string]$intent.token -cne [string]$stored.restore_asset_token -or
          [string]$intent.command -cnotin @('create','start')) { throw 'docker_mutation_completion_unknown' }
      $observed = if ([string]$intent.command -ceq 'create') { [string]$container.Created } else { [string]$container.State.StartedAt }
      [DateTimeOffset]$eventTime=[DateTimeOffset]::MinValue
      [DateTimeOffset]$issuedAt=[DateTimeOffset]::MinValue
      if (-not [DateTimeOffset]::TryParse($observed,[ref]$eventTime) -or
          -not [DateTimeOffset]::TryParse([string]$intent.issued_at,[ref]$issuedAt) -or $eventTime -lt $issuedAt) {
        throw 'docker_mutation_completion_unknown'
      }
      $stored.restore_container_id=[string]$container.Id
      Assert-LocksOwned
      Write-JsonAtomic $path $stored
      $Journal.migration.docker_intent=$null
      try { Assert-LocksOwned; Write-JsonAtomic $JournalPath $Journal }
      catch { $Journal.migration.docker_intent=$intent; throw }
    }
  }
}

function Assert-BoundedRestoreAssets {
  param([Parameter(Mandatory)]$Receipt)
  $name = "ruisheng-restore-$OperationId"
  $container = @(Convert-UpgradeJson (Invoke-DockerText @("container", "inspect", $name)))[0]
  $volume = @(Convert-UpgradeJson (Invoke-DockerText @("volume", "inspect", $name)))[0]
  $mounts = @($container.Mounts)
  if ([string]$container.Name -cne "/$name" -or [string]$container.Image -cne [string]$Receipt.postgres_image -or
      [string]$container.Config.Labels.'com.ruisheng.upgrade.operation' -cne $OperationId -or
      [string]$container.Config.Labels.'com.ruisheng.upgrade.restore-token' -cne [string]$Receipt.restore_asset_token -or
      ([string]$Receipt.restore_container_id -and [string]$container.Id -cne [string]$Receipt.restore_container_id) -or
      [string]$container.HostConfig.NetworkMode -cne 'none' -or
      (@($container.NetworkSettings.Networks.PSObject.Properties.Name) -join ',') -cne 'none' -or
      [string]$container.HostConfig.RestartPolicy.Name -cne 'no' -or
      ($null -ne $container.HostConfig.PortBindings -and @($container.HostConfig.PortBindings.PSObject.Properties).Count -ne 0) -or
      $mounts.Count -ne 1 -or [string]$mounts[0].Type -cne 'volume' -or
      [string]$mounts[0].Name -cne $name -or [string]$mounts[0].Destination -cne '/var/lib/postgresql/data' -or
      -not [bool]$mounts[0].RW -or [string]$mounts[0].Source -cne [string]$volume.Mountpoint -or
      [string]$volume.Name -cne $name -or [string]$volume.Driver -cne 'local' -or
      ($null -ne $volume.Options -and @($volume.Options.PSObject.Properties).Count -ne 0) -or
      [string]$volume.CreatedAt -cne [string]$Receipt.restore_volume_created_at -or
      [string]$volume.Labels.'com.ruisheng.upgrade.operation' -cne $OperationId -or
      [string]$volume.Labels.'com.ruisheng.upgrade.restore-token' -cne [string]$Receipt.restore_asset_token) {
    throw "restore_asset_identity_invalid"
  }
  return $container
}

function Assert-BoundedBackupReceipt {
  param([Parameter(Mandatory)]$Journal, [switch]$RequireVerified)
  if ($null -eq $Journal.backup) {
    if ($RequireVerified) { throw "recovery_backup_missing" }
    return
  }
  $receipt = Convert-UpgradeJson ($Journal.backup | ConvertTo-Json -Depth 14 -Compress)
  if (-not (Test-ExactKeys $receipt @('schema_version','operation_id','snapshot_id','source_identity','candidate_identity',
      'database_head','fingerprint_sha256','postgres_version','timescale_version','timescale_owner','postgres_image',
      'database','roles','expressions','database_properties','restore_verified','restore_container','restore_container_id','restore_volume',
      'restore_volume_created_at','restore_asset_token','created_at')) -or
      ($receipt.schema_version -isnot [int] -and $receipt.schema_version -isnot [long]) -or
      [int]$receipt.schema_version -ne 4 -or
      [string]$receipt.operation_id -cne $OperationId -or
      [string]$receipt.database_head -cne $BoundedSourceHead -or
      [string]$receipt.snapshot_id -cnotmatch '^[0-9A-F]+-[0-9A-F]+-[0-9]+$' -or
      [string]$receipt.fingerprint_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
      [string]$receipt.postgres_image -cnotmatch '^sha256:[0-9a-f]{64}$' -or
      [string]$receipt.postgres_image -cne [string]$Journal.migration.source_images.postgres -or
      [string]$receipt.postgres_version -cnotmatch '^[0-9]{6}$' -or
      [string]$receipt.timescale_version -cnotmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
      [string]$receipt.timescale_owner -cne 'ruisheng_admin' -or
      [string]$receipt.restore_container -cne "ruisheng-restore-$OperationId" -or
      [string]$receipt.restore_volume -cne "ruisheng-restore-$OperationId" -or
      [string]$receipt.restore_asset_token -cnotmatch '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$' -or
      ($receipt.restore_container_id -and [string]$receipt.restore_container_id -cnotmatch '^[0-9a-f]{64}$') -or
      $receipt.restore_verified -isnot [bool] -or
      [string]$Journal.backup.source_identity -cne [string]$Journal.previous_release.logical_identity -or
      [string]$Journal.backup.candidate_identity -cne [string]$Journal.candidate.logical_identity -or
      ($RequireVerified -and -not [bool]$Journal.backup.restore_verified)) { throw "recovery_backup_receipt_invalid" }
  foreach ($field in @('operation_id','snapshot_id','source_identity','candidate_identity','database_head',
      'fingerprint_sha256','postgres_version','timescale_version','timescale_owner','postgres_image',
      'restore_container','restore_container_id','restore_volume','restore_volume_created_at','restore_asset_token','created_at')) {
    if ($receipt.$field -isnot [string]) { throw "recovery_backup_receipt_invalid" }
  }
  if ([bool]$receipt.restore_verified -and (-not $receipt.restore_container_id -or -not $receipt.restore_volume_created_at)) {
    throw "recovery_backup_receipt_invalid"
  }
  foreach ($kind in @("database", "roles", "expressions", "database_properties")) {
    $entry = $receipt.$kind
    $file = @{ database='ruisheng.dump'; roles='roles.sql'; expressions='expressions.sql'; database_properties='database-properties.sql' }[$kind]
    if (-not (Test-ExactKeys $entry @('path','sha256')) -or
        [string]$entry.path -cne (Join-Path $BackupDirectory $file) -or
        [string]$entry.sha256 -cnotmatch '^[0-9a-f]{64}$') { throw "recovery_backup_receipt_invalid" }
    Assert-RestrictedFile ([string]$entry.path)
    if ((Get-FileHash -LiteralPath ([string]$entry.path) -Algorithm SHA256).Hash.ToLowerInvariant() -cne
        [string]$entry.sha256) { throw "recovery_backup_hash_invalid" }
  }
}

function Start-BoundedApplications {
  param([Parameter(Mandatory)]$Journal, [Parameter(Mandatory)]$Release, [switch]$Previous)
  Assert-LocksOwned
  $override = Join-Path $StateDirectory "full-upgrade-$OperationId.restart.json"
  Write-JsonAtomic $override ([ordered]@{ services = [ordered]@{
    gw = @{ restart = "no" }; api = @{ restart = "no" }; web = @{ restart = "no" }
  } })
  Assert-RestrictedFile $override
  $compose = @($Release.base) + @("-f", $override)
  $Journal.migration.application_start_attempted = -not [bool]$Previous
  Save-BoundedPhase $Journal "application_prepare_intent"
  [void](Invoke-DockerText ($compose + @("up", "--no-start", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", "gw", "api", "web")))
  $applicationIds = @()
  foreach ($service in @("gw", "api", "web")) {
    $info = @(Invoke-DockerText @("inspect", "ruisheng-$service") | ConvertFrom-Json)[0]
    if ([string]$info.Id -cnotmatch '^[a-f0-9]{64}$' -or
        [string]$info.State.Status -cne 'created' -or [bool]$info.State.Running -or
        [bool]$info.State.Restarting -or [int]$info.State.Pid -ne 0 -or
        [string]$info.Image -cne [string]$Release.images.$service.image_id -or
        [string]$info.HostConfig.RestartPolicy.Name -cne "no" -or
        [string]$info.Config.Labels.'com.docker.compose.project' -cne 'ruisheng-prod' -or
        [string]$info.Config.Labels.'com.docker.compose.service' -cne $service -or
        [string]$info.Id -cin $applicationIds) { throw "application_prepare_invalid" }
    $applicationIds += [string]$info.Id
  }
  Assert-BoundedMaintenanceGuards $Journal
  Save-BoundedPhase $Journal "application_start_intent"
  Restore-ApplicationRoles $Journal
  Assert-LocksOwned
  # Compose start traverses depends_on and can rerun the retained source migrator.
  # Start only the exact application containers prepared and verified above.
  foreach ($applicationId in $applicationIds) {
    # Web resolves api at startup, so finish starting api before starting web.
    [void](Invoke-DockerText @("start", $applicationId))
  }
  Wait-BoundedHealthy $compose
  Assert-CurrentContainerIdentity $Release.images
}

function Get-LegacyApplyFailure {
  param([Parameter(Mandatory)]$Journal)
  Assert-LocksOwned
  # The latest recovery error is mutable. Only the original Apply outcome in
  # the complete audit chain can establish that the old command did not time out.
  $stream = [IO.File]::Open($AuditLockPath, 'Open', 'ReadWrite', 'None')
  try {
    if (-not (Test-Path -LiteralPath $AuditPath -PathType Leaf) -or
        (Get-Item -LiteralPath $AuditPath).Length -gt 16MB) { throw 'legacy_apply_failure_unverified' }
    Assert-RestrictedFile $AuditPath
    $previousHash='0'*64; $count=0; $failures=@()
    foreach ($line in Get-Content -LiteralPath $AuditPath -Encoding UTF8) {
      if (-not $line) { continue }; $count++
      if ($count -gt 50000 -or [Text.Encoding]::UTF8.GetByteCount($line) -gt 64KB) {
        throw 'audit_budget_exceeded'
      }
      try {
        $record=Convert-UpgradeJson $line
        $material=Get-AuditLineHashMaterial $line
        if ($null -eq $material -or [string]$record.previous_hash -cne $previousHash -or
            [string]$record.record_hash -cne [string]$material.record_hash -or
            (Get-Sha256Text ([string]$material.payload)) -cne [string]$material.record_hash) { throw 'invalid' }
        $previousHash=[string]$record.record_hash
      } catch { throw 'audit_chain_invalid' }
      if ([string]$record.operation_id -ceq $OperationId -and [string]$record.event -ceq 'upgrade_apply_failed') {
        $failures+=,$record
      }
    }
    if ($failures.Count -ne 1 -or [string]$failures[0].result -cne 'failed' -or
        [string]$failures[0].candidate_identity -cne [string]$Journal.candidate.logical_identity -or
        [string]$failures[0].reason_hash -cne (Get-Sha256Text $Reason)) { throw 'legacy_apply_failure_unverified' }
    try {
      $recorded=[DateTimeOffset]::Parse([string]$failures[0].recorded_at,[Globalization.CultureInfo]::InvariantCulture)
      $issued=[DateTimeOffset]::Parse([string]$Journal.migration.docker_intent.issued_at,[Globalization.CultureInfo]::InvariantCulture)
    } catch { throw 'legacy_apply_failure_unverified' }
    if ($recorded -lt $issued -or $recorded -gt [DateTimeOffset]::UtcNow) { throw 'legacy_apply_failure_unverified' }
    return $failures[0]
  } finally { $stream.Dispose() }
}

function Resolve-LegacyComposeStartFailure {
  param([Parameter(Mandatory)]$Journal)
  $intent = $Journal.migration.docker_intent
  # This reconciles one observed legacy failure, never a timeout or a generic
  # daemon mutation. Every other pending intent retains the existing refusal.
  if ($null -eq $intent -or [string]$intent.scope -cne 'production' -or
      [string]$intent.command -cne 'compose' -or [string]$intent.phase -cne 'application_start_intent' -or
      [string]$Journal.migration.phase -cne 'application_start_intent' -or
      [string]$Journal.status -cne 'recovery_failed' -or
      $Journal.migration.application_start_attempted -isnot [bool] -or
      -not $Journal.migration.application_start_attempted) { return }
  Assert-LocksOwned
  $originalFailure=Get-LegacyApplyFailure $Journal
  if ([string]$originalFailure.error_code -cne 'docker_command_failed') { return }
  [void](Assert-BoundedDatabaseIdentity $Journal)
  Assert-BoundedBackupReceipt $Journal -RequireVerified
  Assert-BoundedSchema $BoundedTargetHead
  Assert-BoundedMigrationStopped $Journal
  if ((Invoke-DatabaseSql "SELECT count(*) FROM pg_roles WHERE rolname IN ('ruisheng_api','ruisheng_gw') AND NOT rolcanlogin") -cne '2') {
    throw 'docker_mutation_completion_unknown'
  }
  $prior = @($Journal.migration.existing_migrators | Where-Object { $null -ne $_ })
  if ($prior.Count -ne 1 -or [string]$Journal.migration.container_id -cnotmatch '^[a-f0-9]{64}$') {
    throw 'docker_mutation_completion_unknown'
  }
  $migration = @(Convert-UpgradeJson (Invoke-DockerText @('inspect', [string]$Journal.migration.container_id)))[0]
  if ([string]$migration.Id -cne [string]$Journal.migration.container_id -or
      [string]$migration.State.Status -cne 'exited' -or [int]$migration.State.ExitCode -ne 0) {
    throw 'docker_mutation_completion_unknown'
  }
  $legacy = @(Convert-UpgradeJson (Invoke-DockerText @('inspect', [string]$prior[0].id)))[0]
  if ([string]$legacy.State.Status -cne 'exited' -or [int]$legacy.State.ExitCode -eq 0 -or
      [string]$legacy.Config.Labels.'com.docker.compose.project' -cne 'ruisheng-prod' -or
      [string]$legacy.Config.Labels.'com.docker.compose.service' -cne 'migrate') {
    throw 'docker_mutation_completion_unknown'
  }
  try {
    $issued = [DateTimeOffset]::Parse([string]$intent.issued_at)
    $migrated = [DateTimeOffset]::Parse([string]$migration.State.FinishedAt)
    $started = [DateTimeOffset]::Parse([string]$legacy.State.StartedAt)
    $finished = [DateTimeOffset]::Parse([string]$legacy.State.FinishedAt)
  } catch { throw 'docker_mutation_completion_unknown' }
  if ($migrated -le [DateTimeOffset]::MinValue -or $migrated -ge $issued -or $started -lt $issued -or $finished -lt $started -or
      $finished -gt [DateTimeOffset]::Parse([string]$originalFailure.recorded_at,[Globalization.CultureInfo]::InvariantCulture) -or
      $finished -gt [DateTimeOffset]::UtcNow) { throw 'docker_mutation_completion_unknown' }
  $applicationIds = @()
  foreach ($service in @('gw','api','web')) {
    $info = @(Convert-UpgradeJson (Invoke-DockerText @('inspect', "ruisheng-$service")))[0]
    if ([string]$info.Id -cnotmatch '^[a-f0-9]{64}$' -or [string]$info.State.Status -cne 'created' -or
        [bool]$info.State.Running -or [bool]$info.State.Restarting -or [int]$info.State.Pid -ne 0 -or
        [string]$info.State.StartedAt -cnotmatch '^0001-01-01T00:00:00(?:\.0+)?Z$' -or
        [string]$info.HostConfig.RestartPolicy.Name -cne 'no' -or
        [string]$info.Image -cne [string]$Journal.migration.target_images.$service -or
        [string]$info.Config.Labels.'com.docker.compose.project' -cne 'ruisheng-prod' -or
        [string]$info.Config.Labels.'com.docker.compose.service' -cne $service -or
        [string]$info.Id -cin $applicationIds) {
      throw 'docker_mutation_completion_unknown'
    }
    $applicationIds += [string]$info.Id
  }
  Assert-NoUnknownWriters
  Assert-LocksOwned
  $observation = [ordered]@{
    kind='legacy_compose_dependency_failure'; observed_at=[DateTimeOffset]::UtcNow.ToString('o')
    prior_intent=$intent; migration_id=[string]$migration.Id; legacy_migrator_id=[string]$legacy.Id
    legacy_exit_code=[int]$legacy.State.ExitCode; application_ids=$applicationIds
    original_failure_record_hash=[string]$originalFailure.record_hash
  }
  Write-Audit 'upgrade_application_start_failure_observed' 'observed' ([string]$Journal.candidate.logical_identity) -Evidence $observation
  $dictionary = $Journal.migration -is [Collections.IDictionary]
  $previousObservation = $Journal.migration.application_start_observation
  if ($dictionary) { $Journal.migration.application_start_observation=$observation }
  else { $Journal.migration | Add-Member -NotePropertyName application_start_observation -NotePropertyValue $observation -Force }
  $Journal.migration.docker_intent=$null
  try { Write-JsonAtomic $JournalPath $Journal }
  catch {
    $Journal.migration.docker_intent=$intent
    if ($null -ne $previousObservation) { $Journal.migration.application_start_observation=$previousObservation }
    elseif ($dictionary) { $Journal.migration.Remove('application_start_observation') }
    else { $Journal.migration.PSObject.Properties.Remove('application_start_observation') }
    throw
  }
}

function Get-BoundedDependencyConfigurationHash {
  param([Parameter(Mandatory)]$Container)
  return Get-Sha256Text ([ordered]@{
    environment = @($Container.Config.Env | Sort-Object); command = $Container.Config.Cmd
    entrypoint = $Container.Config.Entrypoint; user = [string]$Container.Config.User
    healthcheck = $Container.Config.Healthcheck; ports = $Container.HostConfig.PortBindings
    network = [string]$Container.HostConfig.NetworkMode
  } | ConvertTo-Json -Depth 12 -Compress)
}

function Get-BoundedDependencyEvidence {
  $evidence = @()
  foreach ($service in @("postgres", "redis")) {
    $info = @(Convert-UpgradeJson (Invoke-DockerText @("inspect", "ruisheng-$service")))[0]
    $mounts = @($info.Mounts)
    $destination = if ($service -eq "postgres") { "/var/lib/postgresql/data" } else { "/data" }
    if ($mounts.Count -ne 1 -or [string]$mounts[0].Type -cne "volume" -or
        [string]$mounts[0].Destination -cne $destination -or -not [bool]$mounts[0].RW -or
        [string]$info.Config.Labels.'com.docker.compose.project' -cne "ruisheng-prod" -or
        [string]$info.Config.Labels.'com.docker.compose.service' -cne $service) { throw "dependency_storage_identity_invalid" }
    $volume = @(Convert-UpgradeJson (Invoke-DockerText @("volume", "inspect", [string]$mounts[0].Name)))[0]
    if ([string]$volume.Driver -cne "local" -or ($null -ne $volume.Options -and @($volume.Options.PSObject.Properties).Count)) {
      throw "dependency_storage_identity_invalid"
    }
    $evidence += [ordered]@{ service = $service; image_id = [string]$info.Image
      volume = [string]$volume.Name; volume_created_at = [string]$volume.CreatedAt
      volume_mountpoint = [string]$volume.Mountpoint; destination = $destination
      restart = [string]$info.HostConfig.RestartPolicy.Name; retries = [int]$info.HostConfig.RestartPolicy.MaximumRetryCount
      configuration_sha256 = Get-BoundedDependencyConfigurationHash $info }
  }
  return $evidence
}

function Assert-BoundedDependencyContainer {
  param([Parameter(Mandatory)]$Info, [Parameter(Mandatory)]$Entry, [Parameter(Mandatory)][string]$Service)
  $mounts = @($Info.Mounts)
  if ([string]$Info.Image -cne [string]$Entry.image_id -or $mounts.Count -ne 1 -or
      [string]$mounts[0].Type -cne "volume" -or [string]$mounts[0].Name -cne [string]$Entry.volume -or
      [string]$mounts[0].Destination -cne [string]$Entry.destination -or -not [bool]$mounts[0].RW -or
      [string]$Info.Config.Labels.'com.docker.compose.project' -cne "ruisheng-prod" -or
      [string]$Info.Config.Labels.'com.docker.compose.service' -cne $Service -or
      (Get-BoundedDependencyConfigurationHash $Info) -cne [string]$Entry.configuration_sha256) {
    throw "dependency_container_identity_invalid"
  }
}

function Prepare-BoundedDependencies {
  param([Parameter(Mandatory)]$Journal, [Parameter(Mandatory)][string[]]$ComposeBase, [switch]$AlignReference)
  Assert-LocksOwned
  $model = Convert-UpgradeJson (Invoke-DockerText ($ComposeBase + @("config", "--format", "json")))
  foreach ($service in @("postgres", "redis")) {
    $entries = @($Journal.migration.dependencies | Where-Object { [string]$_.service -ceq $service })
    if ($entries.Count -ne 1) { throw "dependency_storage_receipt_invalid" }
    $entry = $entries[0]
    if ([string]$entry.image_id -cne [string]$Journal.migration.source_images.$service -or
        [string]$entry.image_id -cne [string]$Journal.migration.target_images.$service -or
        [string]$entry.volume -cnotmatch '^[a-zA-Z0-9][a-zA-Z0-9_.-]+$') { throw "dependency_storage_receipt_invalid" }
    $volume = @(Convert-UpgradeJson (Invoke-DockerText @("volume", "inspect", [string]$entry.volume)))[0]
    if ([string]$volume.Name -cne [string]$entry.volume -or [string]$volume.Driver -cne "local" -or
        [string]$volume.CreatedAt -cne [string]$entry.volume_created_at -or
        [string]$volume.Mountpoint -cne [string]$entry.volume_mountpoint -or
        ($null -ne $volume.Options -and @($volume.Options.PSObject.Properties).Count)) { throw "dependency_storage_identity_invalid" }
    $reference = [string]$model.services.$service.image
    if ((Invoke-DockerText @("image", "inspect", "--format", "{{.Id}}", $reference)) -cne [string]$entry.image_id) {
      throw "bounded_dependency_image_changed"
    }
    $present = [bool](Invoke-DockerText @("ps", "-aq", "--filter", "name=^/ruisheng-$service`$"))
    $recreate = -not $present
    if ($present) {
      $info = @(Convert-UpgradeJson (Invoke-DockerText @("inspect", "ruisheng-$service")))[0]
      Assert-BoundedDependencyContainer $info $entry $service
      $recreate = $AlignReference -and [string]$info.Config.Image -cne $reference
    }
    if ($recreate) {
      $intent = @{ service = $service; candidate_reference = $reference; original_volume = [string]$entry.volume }
      if ($Journal.migration -is [Collections.IDictionary]) { $Journal.migration.dependency_intent = $intent }
      else { $Journal.migration | Add-Member -NotePropertyName dependency_intent -NotePropertyValue $intent -Force }
      Write-JsonAtomic $JournalPath $Journal
      if ($present) {
        if ([bool]$info.State.Running) { Assert-BoundedMigrationStopped $Journal }
        Assert-LocksOwned
        [void](Invoke-DockerText @("update", "--restart=no", "ruisheng-$service"))
        Assert-LocksOwned
        if ([bool]$info.State.Running) { Assert-NoUnknownWriters }
        [void](Invoke-DockerText @("stop", "--time", "30", "ruisheng-$service"))
        if ((Invoke-DockerText @("inspect", "--format", "{{.State.Running}}", "ruisheng-$service")) -cne "false") {
          throw "dependency_stop_uncertain"
        }
      }
      # External volumes make compose fail rather than initialize a missing data volume.
      $override = Join-Path $StateDirectory "full-upgrade-$OperationId.$service.json"
      $volumeKey = "ruisheng-" + $(if ($service -eq "postgres") { "pgdata" } else { "redisdata" })
      $services = @{}; $services[$service] = @{ image = $reference; restart = "no" }
      $volumes = @{}; $volumes[$volumeKey] = @{ external = $true; name = [string]$entry.volume }
      Write-JsonAtomic $override @{ services = $services; volumes = $volumes }
      Assert-LocksOwned
      [void](Invoke-DockerText ($ComposeBase + @("-f", $override, "up", "--no-start", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", $service)))
    }
    $info = @(Convert-UpgradeJson (Invoke-DockerText @("inspect", "ruisheng-$service")))[0]
    Assert-BoundedDependencyContainer $info $entry $service
    if ($AlignReference -and [string]$info.Config.Image -cne $reference) { throw "bounded_dependency_reference_changed" }
    if (-not [bool]$info.State.Running) {
      if ($service -eq "postgres") {
        Assert-LocksOwned
        $probeName = "ruisheng-storage-proof-$OperationId-$([Guid]::NewGuid().ToString('N'))"
        $systemIdentifier = [string]$Journal.migration.database_system_identifier
        if ($systemIdentifier -cnotmatch '^[0-9]{10,20}$') { throw "dependency_database_identity_invalid" }
        $probe = Invoke-DockerText @("run", "--name", $probeName, "--network", "none", "--read-only", "--restart", "no",
          "--label", "com.ruisheng.upgrade.operation=$OperationId", "--entrypoint", "sh",
          "--mount", "type=volume,src=$($entry.volume),dst=/proof,readonly", [string]$entry.image_id,
          "-c", "test -s /proof/PG_VERSION && LC_ALL=C pg_controldata /proof")
        if ($probe -cnotmatch "(?m)^Database system identifier:\s+$systemIdentifier\s*$") {
          throw "dependency_database_identity_invalid"
        }
      }
      Assert-LocksOwned
      [void](Invoke-DockerText @("start", "ruisheng-$service"))
      if ($service -eq "postgres") {
        $databaseReady = $false
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
          Assert-LocksOwned
          try {
            if ((Invoke-DatabaseSql "SELECT system_identifier::text FROM pg_control_system()") -ceq $systemIdentifier) {
              $databaseReady = $true; break
            }
          }
          catch { if ($_.Exception.Message -ceq "upgrade_lock_lost") { throw } }
          Renew-Locks
          Start-Sleep -Seconds 2
        }
        if (-not $databaseReady) { throw "dependency_database_identity_invalid" }
      }
    }
  }
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds(120)
  do {
    Assert-LocksOwned
    $ready = $true
    foreach ($service in @("postgres", "redis")) {
      $info = @(Invoke-DockerText @("inspect", "ruisheng-$service") | ConvertFrom-Json)[0]
      if ([string]$info.Image -cne [string]$Journal.migration.source_images.$service) {
        throw "bounded_dependency_image_changed"
      }
      if (-not [bool]$info.State.Running -or [string]$info.State.Health.Status -cne "healthy") { $ready = $false }
    }
    if ($ready) {
      if ((Invoke-DatabaseSql "SELECT system_identifier::text FROM pg_control_system()") -cne
          [string]$Journal.migration.database_system_identifier) { throw "dependency_database_identity_invalid" }
      return
    }
    Renew-Locks
    Start-Sleep -Seconds 2
  } while ([DateTimeOffset]::UtcNow -lt $deadline)
  throw "dependency_start_uncertain"
}

function Wait-BoundedHealthy {
  param([Parameter(Mandatory)][string[]]$ComposeBase, [int]$TimeoutSeconds = 120)
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
  do {
    Assert-LocksOwned
    $ready = $true
    foreach ($service in @("postgres", "redis")) {
      try {
        $state = Invoke-DockerText @("inspect", "--format", "{{json .State}}", "ruisheng-$service") | ConvertFrom-Json
        if (-not [bool]$state.Running -or [string]$state.Health.Status -cne "healthy") { $ready = $false }
      }
      catch { $ready = $false }
    }
    foreach ($service in @("api", "gw")) {
      try {
        $health = Invoke-DockerText @("exec", "ruisheng-$service", "python", "-m", "ruisheng_$service.healthcheck") | ConvertFrom-Json
        $keys = if ($service -eq "api") { @("status", "database", "redis", "service") } else { @("status", "database", "redis", "service", "batch", "outbox") }
        foreach ($key in $keys) { if ([string]$health.$key -cne "ready") { $ready = $false } }
      }
      catch { $ready = $false }
    }
    try { [void](Invoke-DockerText @("exec", "ruisheng-web", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1/")) }
    catch { $ready = $false }
    if ($ready) { return }
    Renew-Locks
    Start-Sleep -Seconds 2
  } while ([DateTimeOffset]::UtcNow -lt $deadline)
  throw "service_health_failed"
}

function Complete-BoundedMaintenance {
  param([Parameter(Mandatory)]$Journal, [Parameter(Mandatory)][string]$Status)
  Assert-LocksOwned
  Stop-BoundedRestoreAssets $Journal
  Stop-BoundedSnapshot $Journal
  foreach ($service in @("gw", "api", "web")) {
    $info = @(Convert-UpgradeJson (Invoke-DockerText @("inspect", "ruisheng-$service")))[0]
    $expectedImages = if ($Status -ceq "committed") { $Journal.migration.target_images } else { $Journal.migration.source_images }
    if ([string]$info.Image -cne [string]$expectedImages.$service -or
        [string]$info.Config.Labels.'com.docker.compose.project' -cne "ruisheng-prod" -or
        [string]$info.Config.Labels.'com.docker.compose.service' -cne $service) { throw "maintenance_application_identity_drift" }
  }
  foreach ($service in @("gw", "api", "web")) {
    $entries = @($Journal.migration.applications | Where-Object { [string]$_.name -ceq "ruisheng-$service" })
    if ($entries.Count -ne 1 -or [string]$entries[0].restart -notin @("no", "always", "unless-stopped", "on-failure")) {
      throw "restart_policy_receipt_invalid"
    }
    $policy = [string]$entries[0].restart
    if ($policy -eq "on-failure") { $policy += ":$([int]$entries[0].retries)" }
    Assert-LocksOwned
    [void](Invoke-DockerText @("update", "--restart=$policy", "ruisheng-$service"))
  }
  foreach ($service in @("postgres", "redis")) {
    $entries = @($Journal.migration.dependencies | Where-Object { [string]$_.service -ceq $service })
    if ($entries.Count -ne 1 -or [string]$entries[0].restart -cnotin @("no", "always", "unless-stopped", "on-failure") -or
        [int]$entries[0].retries -lt 0) { throw "restart_policy_receipt_invalid" }
    $policy = [string]$entries[0].restart
    if ($policy -ceq "on-failure") { $policy += ":$([int]$entries[0].retries)" }
    Assert-LocksOwned
    [void](Invoke-DockerText @("update", "--restart=$policy", "ruisheng-$service"))
  }
  Save-BoundedPhase $Journal "completed"
  Write-MaintenanceState $Journal $Status
  $script:BoundedCleanupAuthority = $null
}

function Assert-BoundedEnvironment {
  param([Parameter(Mandatory)]$Journal, [Parameter(Mandatory)][hashtable]$Values)
  Assert-LocksOwned
  $backupPath = [string]$Journal.environment_backup.path
  if ($backupPath -cne (Join-Path $StateDirectory "full-upgrade-$OperationId.env.before") -or
      [string]$Journal.environment_backup.sha256 -cne [string]$Journal.source_environment_sha256 -or
      [string]$Journal.source_environment_sha256 -cnotmatch '^[0-9a-f]{64}$') { throw "recovery_environment_backup_invalid" }
  $handles = @()
  try {
    foreach ($path in @($backupPath, $EnvFile)) {
      Assert-RestrictedFile $path
      $handles += [IO.File]::Open($path, "Open", "Read", "Read")
    }
    if ((Get-FileHash -LiteralPath $backupPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne
        [string]$Journal.source_environment_sha256) { throw "recovery_environment_backup_invalid" }
    $bytes = Get-ProspectiveEnvironmentBytes $backupPath $Values
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $targetHash = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
    $currentHash = (Get-FileHash -LiteralPath $EnvFile -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($currentHash -cne [string]$Journal.source_environment_sha256 -and $currentHash -cne $targetHash) {
      throw "site_environment_identity_drift"
    }
    if (Test-Path -LiteralPath $ProspectiveEnvPath) {
      Assert-RestrictedFile $ProspectiveEnvPath
      if ((Get-FileHash -LiteralPath $ProspectiveEnvPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $targetHash) {
        throw "prospective_environment_identity_drift"
      }
    }
    else {
      Assert-LocksOwned
      Write-ProspectiveEnvironment $backupPath $ProspectiveEnvPath $Values
    }
    return $currentHash
  }
  finally { foreach ($handle in $handles) { $handle.Dispose() } }
}

function Set-BoundedEnvironment {
  param([Parameter(Mandatory)]$Journal, [Parameter(Mandatory)][hashtable]$Values,
    [Parameter(Mandatory)][string]$DesiredPath)
  $currentHash = Assert-BoundedEnvironment $Journal $Values
  Assert-LocksOwned
  Set-ReleaseEnvironment $EnvFile $DesiredPath -ExpectedPathSha256 $currentHash
}

function Get-BoundedCleanupJournal {
  param([Parameter(Mandatory)]$Journal)
  Assert-LocksOwned
  Assert-MaintenanceState -Recovering
  if (-not (Test-Path -LiteralPath $MaintenanceStatePath -PathType Leaf)) { return $null }
  $marker = Convert-UpgradeJson (Get-Content -LiteralPath $MaintenanceStatePath -Raw -Encoding UTF8)
  if ([string]$marker.status -cne 'active') { return $null }
  Assert-RestrictedDirectory $StateDirectory
  Assert-RestrictedFile $JournalPath
  if ((Get-Item -LiteralPath $JournalPath).Length -gt 1MB) { throw 'upgrade_journal_size_exceeded' }
  $stored = Convert-UpgradeJson (Get-Content -LiteralPath $JournalPath -Raw -Encoding UTF8)
  if ([string]$stored.operation_id -cne $OperationId -or
      [string]$stored.reason_hash -cne (Get-Sha256Text $Reason) -or
      [string]$stored.previous_release.site_root -cne $SiteRoot -or
      [string]$stored.migration.kind -cne 'bounded_0012_0013' -or
      [string]$stored.migration.script_sha256 -cne $BoundedMigrationSha256 -or
      [string]$marker.source_identity -cne [string]$stored.previous_release.logical_identity -or
      [string]$marker.candidate_identity -cne [string]$stored.candidate.logical_identity -or
      [string]$marker.journal_path -cne $JournalPath -or
      [string]$Journal.operation_id -cne [string]$stored.operation_id -or
      [string]$Journal.previous_release.logical_identity -cne [string]$stored.previous_release.logical_identity -or
      [string]$Journal.candidate.logical_identity -cne [string]$stored.candidate.logical_identity) {
    throw 'full_upgrade_maintenance_identity_invalid'
  }
  $current = Read-ActiveRelease
  if ([string]$current.logical_identity -ceq [string]$stored.previous_release.logical_identity) {
    Assert-ActiveReleaseUnchanged $stored.previous_release $current
  }
  elseif ([string]$current.logical_identity -cne [string]$stored.candidate.logical_identity -or
      [string]$current.operation_id -cne $OperationId -or [string]$current.site_root -cne $SiteRoot -or
      [string]$current.candidate_id -cne [string]$stored.candidate.candidate_id -or
      [string]$current.candidate_root -cne [string]$stored.candidate.candidate_root -or
      [string]$current.source_commit -cne [string]$stored.candidate.source_commit) {
    throw 'active_release_identity_drift'
  }
  return $stored
}

function Assert-BoundedMaintenanceGuards {
  param([Parameter(Mandatory)]$Journal)
  Assert-LocksOwned
  $expected = [string]$Journal.migration.guard_receipt_sha256
  if ($expected -cnotmatch '^[0-9a-f]{64}$' -or
      (Assert-InstalledMaintenanceGuards) -cne $expected) { throw 'installed_maintenance_guard_drift' }
}

function Test-BoundedCleanupAuthority {
  param([Parameter(Mandatory)]$Journal)
  $authority=$script:BoundedCleanupAuthority
  return $null -ne $authority -and [Object]::ReferenceEquals($authority.journal,$Journal) -and
    [string]$authority.operation_id -ceq $OperationId -and [string]$authority.site_root -ceq $SiteRoot -and
    [string]$authority.source_identity -ceq [string]$Journal.previous_release.logical_identity -and
    [string]$authority.candidate_identity -ceq [string]$Journal.candidate.logical_identity
}

function Register-BoundedCleanupAuthority {
  param([Parameter(Mandatory)]$Journal)
  # Call only after active maintenance binding is proved, before any maintenance mutation.
  $script:BoundedCleanupAuthority=@{
    journal=$Journal; operation_id=$OperationId; site_root=$SiteRoot
    source_identity=[string]$Journal.previous_release.logical_identity
    candidate_identity=[string]$Journal.candidate.logical_identity
  }
}

function Record-BoundedFailure {
  param([Parameter(Mandatory)]$Journal, [Parameter(Mandatory)][string]$Failure)
  $cleanupJournal = $null
  try {
    if (Test-BoundedCleanupAuthority $Journal) { $cleanupJournal=$Journal }
    else { $cleanupJournal = Get-BoundedCleanupJournal $Journal }
  } catch { }
  if ($null -eq $cleanupJournal -and [string]$Journal.migration.phase -ceq 'completed' -and
      [string]$Journal.status -cin @('committed','rolled_back')) {
    try { Assert-LocksOwned; Write-Audit 'upgrade_recovery_failed' ([string]$Journal.status) ([string]$Journal.candidate.logical_identity) $Failure } catch { }
    return
  }
  $Journal.status = "recovery_failed"; $Journal.error_code = $Failure
  # Each action owns its lock/identity checks; disk or one container failure cannot skip another.
  if ($null -ne $cleanupJournal) {
    $cleanupJournal.status = 'recovery_failed'; $cleanupJournal.error_code=$Failure
    try { Assert-LocksOwned; Set-ApplicationRoleFence $cleanupJournal } catch { }
    try { Assert-LocksOwned; Stop-BoundedApplications $cleanupJournal -ContainersOnly } catch { }
    try { Assert-LocksOwned; Stop-BoundedRestoreAssets $cleanupJournal } catch { }
    try { Assert-LocksOwned; Stop-BoundedSnapshot $cleanupJournal } catch { }
  }
  $failureJournal=if ($null -ne $cleanupJournal) { $cleanupJournal } else { $Journal }
  try { Assert-LocksOwned; Write-JsonAtomic $JournalPath $failureJournal } catch { }
  try { Assert-LocksOwned; Write-Audit "upgrade_recovery_failed" "recovery_failed" ([string]$Journal.candidate.logical_identity) $Failure } catch { }
}

function Invoke-BoundedRecovery {
  param([Parameter(Mandatory)]$Journal)
  Assert-LocksOwned
  Assert-MaintenanceState -Recovering
  if (-not (Test-BoundedCleanupAuthority $Journal)) {
    $cleanupEvidence=Get-BoundedCleanupJournal $Journal
    if ($null -ne $cleanupEvidence) { Register-BoundedCleanupAuthority $Journal }
  }
  Assert-EntitlementFeature "software-updates"
  if ([string]$Journal.migration.kind -cne "bounded_0012_0013" -or
      [string]$Journal.migration.script_sha256 -cne $BoundedMigrationSha256) {
    throw "recovery_migration_identity_invalid"
  }
  $root = [string]$Journal.candidate.candidate_root
  Assert-RestrictedFile (Join-Path $root "MANIFEST.json")
  $manifest = Convert-UpgradeJson (Get-Content -LiteralPath (Join-Path $root "MANIFEST.json") -Raw -Encoding UTF8)
  $images = Assert-CandidateManifest $manifest $root
  $values = Get-ReleaseValues $manifest $images
  $currentEnvironmentHash = Assert-BoundedEnvironment $Journal $values
  $backupPath = [string]$Journal.environment_backup.path
  $old = Get-BoundedRelease $Journal.previous_release $backupPath
  $candidateRelease = Get-BoundedRelease $Journal.candidate $ProspectiveEnvPath
  if ([string]$old.manifest.alembic_head -cne $BoundedSourceHead -or
      [string]$candidateRelease.manifest.alembic_head -cne $BoundedTargetHead) { throw "recovery_migration_identity_invalid" }
  foreach ($service in $PersistentServices) {
    if ([string]$old.images.$service.image_id -cne [string]$Journal.migration.source_images.$service -or
        [string]$candidateRelease.images.$service.image_id -cne [string]$Journal.migration.target_images.$service) {
      throw "recovery_candidate_identity_drift"
    }
  }
  Assert-BoundedMigrationImage $candidateRelease.images
  Assert-BoundedBackupReceipt $Journal
  Stop-BoundedRestoreAssets $Journal
  $current = Read-ActiveRelease
  $marker = $null
  if (Test-Path -LiteralPath $MaintenanceStatePath -PathType Leaf) {
    $marker = Convert-UpgradeJson (Get-Content -LiteralPath $MaintenanceStatePath -Raw -Encoding UTF8)
  }
  if ($null -eq $marker -or [string]$marker.operation_id -cne $OperationId) {
    if ([string]$Journal.migration.phase -cne "prepared" -or [bool]$Journal.migration.application_start_attempted -or
        [string]$Journal.migration.container_id -or [bool]$Journal.switched -or
        $currentEnvironmentHash -cne [string]$Journal.source_environment_sha256) { throw "full_upgrade_maintenance_missing" }
    if (Invoke-DockerText @("ps", "-aq", "--filter", "name=^/$($Journal.migration.container_name)`$")) {
      throw "full_upgrade_maintenance_missing"
    }
    Assert-ActiveReleaseUnchanged $Journal.previous_release $current
    Assert-CurrentContainerIdentity $old.images
    Assert-BoundedSchema $BoundedSourceHead
    Write-MaintenanceState $Journal
    Register-BoundedCleanupAuthority $Journal
  }
  elseif ([string]$marker.source_identity -cne [string]$Journal.previous_release.logical_identity -or
      [string]$marker.candidate_identity -cne [string]$Journal.candidate.logical_identity -or
      [string]$marker.journal_path -cne $JournalPath) { throw "full_upgrade_maintenance_identity_invalid" }
  if ([string]$current.logical_identity -cne [string]$Journal.previous_release.logical_identity -and
      ([string]$current.logical_identity -cne [string]$Journal.candidate.logical_identity -or [string]$current.operation_id -cne $OperationId)) {
    throw "active_release_identity_drift"
  }
  if ($null -ne $Journal.migration.docker_intent) {
    # A prior start may already have unfenced roles. Establish independently verified cleanup
    # responsibility before refusing to infer whether the daemon has finished that request.
    $script:BoundedRecoveryMutationStarted=$true
    if (Assert-BoundedDatabaseIdentity $Journal -AllowStopped) {
      $script:BoundedRecoveryDatabaseVerified=$true
      if ([string]$Journal.migration.docker_intent.scope -ceq 'snapshot') { Stop-BoundedSnapshot $Journal }
      else { Resolve-LegacyComposeStartFailure $Journal }
    }
    if ($null -ne $Journal.migration.docker_intent) { throw 'docker_mutation_completion_unknown' }
  }
  if ($null -ne $marker -and [string]$marker.operation_id -ceq $OperationId -and
      [string]$marker.status -cin @("committed", "rolled_back") -and
      [string]$marker.status -ceq [string]$Journal.status -and [string]$Journal.migration.phase -ceq "completed") {
    Stop-BoundedSnapshot $Journal
    if ([string]$marker.status -ceq "committed") {
      if ([string]$current.logical_identity -cne [string]$Journal.candidate.logical_identity -or
          [string]$current.operation_id -cne $OperationId) { throw "active_release_identity_drift" }
      Assert-BoundedSchema $BoundedTargetHead
    }
    else {
      Assert-ActiveReleaseUnchanged $Journal.previous_release $current
      Assert-BoundedSchema $BoundedSourceHead
    }
    return
  }
  if ((Get-FileHash -LiteralPath $EnvFile -Algorithm SHA256).Hash.ToLowerInvariant() -cne $currentEnvironmentHash) {
    throw "site_environment_identity_drift"
  }
  $script:BoundedRecoveryMutationStarted = $true
  $databaseRunning = Assert-BoundedDatabaseIdentity $Journal -AllowStopped
  if ($databaseRunning) { $script:BoundedRecoveryDatabaseVerified = $true }
  Stop-BoundedApplications $Journal -ContainersOnly:(-not $databaseRunning) -SkipWriterCheck
  Assert-BoundedMigrationStopped $Journal -SkipWriterCheck
  Prepare-BoundedDependencies $Journal $old.base
  $script:BoundedRecoveryDatabaseVerified = $true
  Set-ApplicationRoleFence $Journal
  Stop-BoundedSnapshot $Journal
  Assert-BoundedMigrationStopped $Journal
  $head = Get-DatabaseHead
  $decision = Get-BoundedRestoreDecision $Journal $head
  Assert-BoundedSchema $head
  Assert-BoundedBackupReceipt $Journal -RequireVerified:($decision -eq "forward")
  if ($decision -eq "previous") {
    Assert-ActiveReleaseUnchanged $Journal.previous_release $current
    $release = $old
    Set-BoundedEnvironment $Journal $values $backupPath
    $release.base = Get-ComposeBase $release.root $EnvFile
    Prepare-BoundedDependencies $Journal $release.base -AlignReference
    Start-BoundedApplications $Journal $release -Previous
    Assert-BoundedSchema $BoundedSourceHead
    Write-Audit "upgrade_rolled_back" "rolled_back" ([string]$Journal.candidate.logical_identity)
    $Journal.status = "rolled_back"; $Journal.error_code = ""
    Write-JsonAtomic $JournalPath $Journal
    Complete-BoundedMaintenance $Journal "rolled_back"
    return
  }
  $release = $candidateRelease
  Set-BoundedEnvironment $Journal $values $ProspectiveEnvPath
  $release.base = Get-ComposeBase $release.root $EnvFile
  Prepare-BoundedDependencies $Journal $release.base -AlignReference
  Start-BoundedApplications $Journal $release
  Assert-BoundedSchema $BoundedTargetHead
  $pointer = [ordered]@{
    schema_version = 1; candidate_id = [string]$Journal.candidate.candidate_id
    logical_identity = [string]$Journal.candidate.logical_identity
    source_commit = [string]$Journal.candidate.source_commit; candidate_root = [string]$Journal.candidate.candidate_root
    site_root = $SiteRoot; committed_at = [DateTimeOffset]::UtcNow.ToString("o"); operation_id = $OperationId
  }
  if (-not (Complete-UpgradeCommit $Journal $pointer ([string]$Journal.candidate.logical_identity))) {
    throw "commit_audit_incomplete"
  }
  Complete-BoundedMaintenance $Journal "committed"
}

function Invoke-BoundedApply {
  param([Parameter(Mandatory)]$Journal, [Parameter(Mandatory)]$Manifest, [Parameter(Mandatory)][hashtable]$Images)
  Assert-LocksOwned
  $old = Get-BoundedRelease $Journal.previous_release $EnvFile
  if ([string]$old.manifest.alembic_head -cne $BoundedSourceHead) { throw "schema_head_changed" }
  Assert-CurrentContainerIdentity $old.images
  foreach ($service in @("postgres", "redis")) {
    if ([string]$old.images.$service.image_id -cne [string]$Images.$service.image_id) {
      throw "bounded_dependency_image_changed"
    }
  }
  $guardHash = Assert-InstalledMaintenanceGuards
  $resources = Get-BoundedResources (Get-DatabaseBackupEstimate)
  if (-not $resources.docker_memory.sufficient -or -not $resources.docker_data_volume.sufficient) {
    throw "bounded_restore_resources_insufficient"
  }
  Assert-BoundedMigrationImage $Images
  Assert-BoundedSchema $BoundedSourceHead
  $sourceImages = [ordered]@{}; $targetImages = [ordered]@{}
  foreach ($service in $PersistentServices) {
    $sourceImages[$service] = [string]$old.images.$service.image_id
    $targetImages[$service] = [string]$Images.$service.image_id
  }
  $roles = Convert-UpgradeJson (Invoke-DatabaseSql "SELECT json_agg(t ORDER BY name) FROM (SELECT rolname AS name,rolcanlogin AS login FROM pg_roles WHERE rolname IN ('ruisheng_api','ruisheng_gw')) t")
  $roles = @($roles)
  if ($roles.Count -ne 2) { throw "application_roles_missing" }
  $applications = @(Get-ManagedApplications)
  foreach ($entry in $applications) {
    if ([string]$entry.restart -cnotin @("no", "always", "unless-stopped", "on-failure") -or [int]$entry.retries -lt 0) {
      throw "restart_policy_receipt_invalid"
    }
  }
  $dependencies = @(Get-BoundedDependencyEvidence)
  $databaseSystemIdentifier = Invoke-DatabaseSql "SELECT system_identifier::text FROM pg_control_system()"
  $existingMigrators = @()
  $migrationIds = Invoke-DockerText @("ps", "-aq", "--filter", "label=com.docker.compose.project=ruisheng-prod", "--filter", "label=com.docker.compose.service=migrate")
  foreach ($id in @($migrationIds -split '\r?\n' | Where-Object { $_ })) {
    $info = @(Invoke-DockerText @("inspect", $id) | ConvertFrom-Json)[0]
    if ([string]$info.Image -cne [string]$old.images.api.image_id -or [bool]$info.State.Running -or
        [string]$info.HostConfig.RestartPolicy.Name -cne "no") {
      throw "existing_migration_execution_uncertain"
    }
    $existingMigrators += [ordered]@{ id = [string]$info.Id; image_id = [string]$info.Image }
  }
  $Journal.environment_backup = New-EnvironmentBackupReceipt $EnvFile (Join-Path $StateDirectory "full-upgrade-$OperationId.env.before")
  if ([string]$Journal.environment_backup.sha256 -cne [string]$Journal.source_environment_sha256) {
    throw "site_environment_identity_drift"
  }
  $Journal.migration = [ordered]@{
    kind = "bounded_0012_0013"; script_sha256 = $BoundedMigrationSha256; phase = "prepared"
    updated_at = [DateTimeOffset]::UtcNow.ToString("o"); roles = $roles; applications = $applications
    source_images = $sourceImages; target_images = $targetImages; guard_receipt_sha256 = $guardHash
    container_name = "ruisheng-migrate-$OperationId"; container_id = ""; existing_migrators = $existingMigrators
    application_start_attempted = $false
    dependencies = $dependencies; database_system_identifier = $databaseSystemIdentifier
  }
  Write-JsonAtomic $JournalPath $Journal
  Write-MaintenanceState $Journal
  Register-BoundedCleanupAuthority $Journal
  try {
    $script:BoundedRecoveryDatabaseVerified = $true
    $script:BoundedRecoveryMutationStarted = $true
    foreach ($entry in $existingMigrators) {
      Assert-LocksOwned
      [void](Invoke-DockerText @("update", "--restart=no", [string]$entry.id))
    }
    Save-BoundedPhase $Journal "fence_intent"
    Stop-BoundedApplications $Journal
    Save-BoundedPhase $Journal "writers_fenced"
    $Journal.backup = New-BoundedDatabaseBackup $Journal
    Write-JsonAtomic $JournalPath $Journal
    Test-BoundedBackupRestore $Journal
    Assert-LocksOwned
    Assert-BoundedBackupReceipt $Journal -RequireVerified
    Assert-BoundedSchema $BoundedSourceHead
    Assert-NoUnknownWriters
    if ((Get-FileHash $EnvFile -Algorithm SHA256).Hash.ToLowerInvariant() -cne [string]$Journal.source_environment_sha256) {
      throw "site_environment_identity_drift"
    }
    Save-BoundedPhase $Journal "environment_switch_intent"
    Set-BoundedEnvironment $Journal (Get-ReleaseValues $Manifest $Images) $ProspectiveEnvPath
    $Journal.switched = $true
    $base = Get-ComposeBase ([string]$Journal.candidate.candidate_root) $EnvFile
    Save-BoundedPhase $Journal "dependency_start_intent"
    Prepare-BoundedDependencies $Journal $base -AlignReference
    Assert-NoUnknownWriters
    Save-BoundedPhase $Journal "migration_start_intent"
    [void](Invoke-DockerText ($base + @("run", "-d", "--no-deps", "--name", [string]$Journal.migration.container_name,
      "--label", "com.ruisheng.upgrade.operation=$OperationId", "--entrypoint", "python", "migrate",
      "-m", "alembic", "upgrade", $BoundedTargetHead)))
    $info = @(Invoke-DockerText @("inspect", [string]$Journal.migration.container_name) | ConvertFrom-Json)[0]
    $Journal.migration.container_id = [string]$info.Id
    Save-BoundedPhase $Journal "migration_running"
    [void](Invoke-DockerText @("wait", [string]$Journal.migration.container_name) 600)
    Assert-BoundedMigrationStopped $Journal
    Assert-BoundedSchema $BoundedTargetHead
    Save-BoundedPhase $Journal "migration_verified"
    Invoke-BoundedRecovery $Journal
  }
  catch {
    $failure = [string]$_.Exception.Message
    if ($failure -notmatch '^[a-z0-9_]+$') { $failure = "bounded_upgrade_failed" }
    try {
      Assert-LocksOwned
      Write-Audit "upgrade_apply_failed" "failed" ([string]$Journal.candidate.logical_identity) $failure
    }
    catch { }
    try {
      Assert-LocksOwned
      if ($failure -eq "upgrade_lock_lost" -or $failure -eq "migration_execution_uncertain") { throw $failure }
      Invoke-BoundedRecovery $Journal
    }
    catch {
      Record-BoundedFailure $Journal $failure
    }
  }
}

$active = $null
$locks = $null
$recoverPreflightCleanupEligible = $false
$auditCandidateIdentity = $ExpectedLogicalIdentity
try {
  if ($OperationId -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') {
    throw "operation_id_invalid"
  }
  Assert-AbsoluteRemotePath $SiteRoot
  $SiteRoot = [IO.Path]::GetFullPath($SiteRoot).TrimEnd('\')
  if ($SiteRoot -notmatch '^[A-Za-z]:\\Ruisheng\\candidates\\[^\\]+$') {
    throw "site_root_invalid"
  }
  Assert-RestrictedDirectory $SiteRoot
  # A fresh authorized Recover must reach the independently bound cleanup path
  # when only an environment/audit asset is broken. All other actions retain
  # their original pre-lock checks; recovery/startup still requires every gate.
  if ($Action -ne 'Recover') { Assert-RestrictedFile $EnvFile }
  if ($CandidateRoot) { Assert-AbsoluteRemotePath $CandidateRoot }
  if ($Action -in @("Initialize", "Apply", "Recover")) {
    if (-not $Approved) { throw "approval_required" }
    if ($Reason.Length -lt 8 -or $Reason.Length -gt 200 -or $Reason -match '[\x00-\x1f\x7f]') {
      throw "Reason must contain 8-200 characters without control characters."
    }
  }

  if (Test-Path -LiteralPath $ActiveReleasePath -PathType Leaf) {
    $active = Read-ActiveRelease
  }
  elseif ($Action -ne "Initialize") { throw "active_release_pointer_missing" }
  $locks = [ordered]@{
    shared = Get-LockSummary $SharedLockPath
    legacy = Get-LockSummary $LegacyLockPath
  }
  if ($Action -eq "Status") {
    $journal = $null
    if (Test-Path -LiteralPath $JournalPath -PathType Leaf) {
      Assert-RestrictedFile $JournalPath
      if ((Get-Item -LiteralPath $JournalPath).Length -gt 1MB) { throw "upgrade_journal_size_exceeded" }
      try { $journal = Convert-UpgradeJson (Get-Content -LiteralPath $JournalPath -Raw -Encoding UTF8) }
      catch { throw "upgrade_journal_invalid" }
    }
    New-SafeResult $true "observed" -Active $active -Candidate $journal -Locks $locks |
      ConvertTo-Json -Depth 10 -Compress
    exit 0
  }

  if ($Action -eq "Plan") {
    # Plan is read-only, but must expose the same fixed-path ACL gate as Apply.
    Assert-RestrictedDirectory $StateDirectory
    Assert-RestrictedDirectory $AuditDirectory
    Assert-RestrictedFile $AuditLockPath
    if (
      $ExpectedCandidateId -notmatch '^[a-z0-9][a-z0-9._-]{0,62}$' -or
      $ExpectedLogicalIdentity -notmatch '^sha256:[0-9a-f]{64}$' -or
      $ExpectedSourceCommit -notmatch '^[0-9a-f]{40}$' -or -not $ExpectedAlembicHead -or
      $ExpectedPlatform -notmatch '^linux/(amd64|arm64)$' -or $PackageBytes -le 0
    ) { throw "plan_candidate_identity_invalid" }
    $databaseHead = Get-DatabaseHead
    $schemaKind = "unsupported"
    try { $schemaKind = Get-SchemaUpgradeKind $databaseHead $ExpectedAlembicHead } catch { }
    Assert-MaintenanceState
    if ($schemaKind -eq "bounded_0012_0013") { [void](Assert-InstalledMaintenanceGuards) }
    $backupEstimate = Get-DatabaseBackupEstimate
    $platform = Get-DockerPlatform
    $candidateDrive = Get-PSDrive -Name ([IO.Path]::GetPathRoot($StableCandidatesRoot).Substring(0, 1))
    $backupDrive = Get-PSDrive -Name ([IO.Path]::GetPathRoot($BackupDirectory).Substring(0, 1))
    $candidateRequired = [long][Math]::Max(1GB, $PackageBytes * 2)
    $backupRequired = [long]$backupEstimate.required_bytes
    $boundedResources = $null
    if ($schemaKind -eq "bounded_0012_0013") { $boundedResources = Get-BoundedResources $backupEstimate }
    $candidate = [ordered]@{
      candidate_id = $ExpectedCandidateId; logical_identity = $ExpectedLogicalIdentity
      source_commit = $ExpectedSourceCommit; alembic_head = $ExpectedAlembicHead
      platform = $ExpectedPlatform; package_bytes = $PackageBytes
      schema_compatible = $schemaKind -ne "unsupported"
      schema_upgrade_kind = $schemaKind
      migration_verification = if ($schemaKind -eq "bounded_0012_0013") { "required_before_stopping" } else { "not_required" }
      platform_compatible = $platform -ceq $ExpectedPlatform
      authenticity = "requires_target_verification_on_apply"
      resources = [ordered]@{
        candidate_volume = [ordered]@{
          free_bytes = [long]$candidateDrive.Free; required_bytes = $candidateRequired
          sufficient = [long]$candidateDrive.Free -ge $candidateRequired
        }
        backup_volume = [ordered]@{
          free_bytes = [long]$backupDrive.Free; required_bytes = $backupRequired
          sufficient = [long]$backupDrive.Free -ge $backupRequired
          database_bytes = [long]$backupEstimate.database_bytes
          roles_allowance_bytes = [long]$backupEstimate.roles_allowance_bytes
        }
        docker_memory = $boundedResources.docker_memory
        docker_data_volume = $boundedResources.docker_data_volume
      }
      steps = if ($schemaKind -eq "bounded_0012_0013") { @(
        "verify_identity_resources_migration_and_installed_guards", "acquire_shared_and_legacy_locks",
        "persist_maintenance_marker_and_original_roles_dependencies", "stop_applications_and_fence_roles",
        "snapshot_backup_and_full_isolated_restore_verification", "migrate_exact_0012_to_0013",
        "recover_by_observed_head_without_database_restore", "verify_internal_readiness_and_images",
        "commit_pointer_and_audit_then_complete_maintenance"
      ) } else { @(
        "verify_identity_and_resources", "acquire_shared_and_legacy_locks",
        "verify_publisher_and_network", "backup_database_and_environment",
        "switch_environment_and_services", "verify_health_and_image_identity", "commit_pointer_and_audit"
      ) }
    }
    New-SafeResult $true "planned" -Active $active -Candidate $candidate -Locks $locks |
      ConvertTo-Json -Depth 10 -Compress
    exit 0
  }

  $posture = Get-SshPosture
  if (-not $posture.mutation_allowed) { throw "ssh_not_key_only" }
  Assert-RestrictedDirectory $StateDirectory
  if ($Action -ne 'Recover') {
    Assert-RestrictedDirectory $AuditDirectory
    Assert-RestrictedFile $AuditLockPath
  }
  Assert-MaintenanceState -Recovering:($Action -eq "Recover")
  Acquire-LeasedLock -Path $SharedLockPath -Name "shared-maintenance"
  try { Acquire-LeasedLock -Path $LegacyLockPath -Name "legacy-hotfix" }
  catch { Release-Locks; throw }
  Assert-MaintenanceState -Recovering:($Action -eq "Recover")
  if ($Action -eq "Initialize") {
    if (Test-Path -LiteralPath $ActiveReleasePath -PathType Leaf) {
      $lockedActive = Read-ActiveRelease
      if ($null -ne $active) { Assert-ActiveReleaseUnchanged -Before $active -After $lockedActive }
      $active = $lockedActive
    }
    elseif ($null -ne $active) { throw "active_release_initialization_race" }
  }
  else {
    $lockedActive = Read-ActiveRelease
    Assert-ActiveReleaseUnchanged -Before $active -After $lockedActive
    $active = $lockedActive
  }
  if ($Action -eq 'Recover') {
    $recoverPreflightCleanupEligible = $true
    Assert-RestrictedFile $EnvFile
    Assert-RestrictedDirectory $AuditDirectory
    Assert-RestrictedFile $AuditLockPath
  }
}
catch {
  $errorCode = [string]$_.Exception.Message
  if ($errorCode -notmatch '^[a-z0-9_]+$') { $errorCode = "upgrade_preflight_failed" }
  $preflightFailureJournal = $null
  if ($recoverPreflightCleanupEligible) {
    try {
      Assert-LocksOwned
      Assert-RestrictedFile $JournalPath
      if ((Get-Item -LiteralPath $JournalPath).Length -gt 1MB) { throw 'upgrade_journal_size_exceeded' }
      $cleanupInput = Convert-UpgradeJson (Get-Content -LiteralPath $JournalPath -Raw -Encoding UTF8)
      $cleanupEvidence = Get-BoundedCleanupJournal $cleanupInput
      if ($null -ne $cleanupEvidence) {
        Record-BoundedFailure $cleanupEvidence $errorCode
        $preflightFailureJournal = $cleanupEvidence
      }
    }
    catch { }
  }
  Release-Locks
  $preflightStatus = if ($null -ne $preflightFailureJournal) { 'recovery_failed' } else { 'rejected' }
  New-SafeResult $false $preflightStatus -ErrorCode $errorCode -Active $active -Locks $locks -Candidate $preflightFailureJournal |
    ConvertTo-Json -Depth 10 -Compress
  exit 0
}

try {
  if ($Action -eq "Initialize") {
    Assert-EntitlementFeature "software-updates"
    if (-not $CandidateRoot -or -not (Test-Path -LiteralPath $CandidateRoot -PathType Container)) {
      throw "candidate_directory_missing"
    }
    $CandidateRoot = [IO.Path]::GetFullPath($CandidateRoot).TrimEnd('\')
    if ((Split-Path -Parent $CandidateRoot) -cne $StableCandidatesRoot) {
      throw "initial_candidate_path_invalid"
    }
    $candidateItem = Get-Item -LiteralPath $CandidateRoot -Force
    if (($candidateItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "candidate_directory_linked"
    }
    $manifestPath = Join-Path $CandidateRoot "MANIFEST.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf) -or
        (Get-Item -LiteralPath $manifestPath).Length -gt 4MB) {
      throw "manifest_invalid"
    }
    try { $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "manifest_invalid" }
    $images = Assert-CandidateManifest $manifest $CandidateRoot
    $auditCandidateIdentity = [string]$manifest.logical_identity
    $expectedValues = Get-ReleaseValues -Manifest $manifest -Images $images
    $actualValues = Get-EnvironmentReleaseValues -Path $EnvFile
    foreach ($field in $AllowedFields) {
      if ([string]$actualValues[$field] -cne [string]$expectedValues[$field]) {
        throw "current_environment_identity_mismatch"
      }
    }
    Invoke-PublisherVerification -Root $CandidateRoot -EnvironmentPath $EnvFile
    if ((Get-DockerPlatform) -cne [string]$expectedValues.TARGET_PLATFORM) {
      throw "candidate_platform_mismatch"
    }
    if ((Get-DatabaseHead) -cne [string]$manifest.alembic_head) { throw "schema_head_changed" }
    $currentBase = Get-ComposeBase $CandidateRoot $EnvFile
    $model = Invoke-DockerText ($currentBase + @("config", "--format", "json")) | ConvertFrom-Json
    Assert-NetworkBoundary $model
    Assert-ComposeManifestImages -Model $model -Images $images
    Assert-CurrentContainerIdentity -Images $images
    Assert-LocksOwned
    $pointer = [ordered]@{
      schema_version = 1; candidate_id = [string]$manifest.candidate_id
      logical_identity = [string]$manifest.logical_identity
      source_commit = [string]$manifest.source_commit; candidate_root = $CandidateRoot
      site_root = $SiteRoot; committed_at = [DateTimeOffset]::UtcNow.ToString("o")
      operation_id = $OperationId
    }
    $pointer = Complete-ActiveReleaseInitialization -Pointer $pointer -Existing $active `
      -CandidateIdentity ([string]$manifest.logical_identity)
    New-SafeResult $true "initialized" -Active $pointer -Candidate ([ordered]@{
      candidate_id = [string]$manifest.candidate_id
      logical_identity = [string]$manifest.logical_identity
      source_commit = [string]$manifest.source_commit
      alembic_head = [string]$manifest.alembic_head
      candidate_root = $CandidateRoot
    }) | ConvertTo-Json -Depth 10 -Compress
    exit 0
  }

  if (Test-Path -LiteralPath $JournalPath -PathType Leaf) {
    Assert-RestrictedFile $JournalPath
    if ((Get-Item -LiteralPath $JournalPath).Length -gt 1MB) { throw "upgrade_journal_size_exceeded" }
    $journal = Convert-UpgradeJson (Get-Content -LiteralPath $JournalPath -Raw -Encoding UTF8)
    if ([string]$journal.operation_id -cne $OperationId -or
        [string]$journal.reason_hash -cne (Get-Sha256Text $Reason)) {
      throw "upgrade_operation_identity_conflict"
    }
    if ($Action -eq "Apply") { Assert-JournalCandidateIdentity $journal }
    if ([string]$journal.migration.kind -ceq "bounded_0012_0013") {
      $markerComplete = $false
      if (Test-Path -LiteralPath $MaintenanceStatePath -PathType Leaf) {
        $marker = Convert-UpgradeJson (Get-Content -LiteralPath $MaintenanceStatePath -Raw -Encoding UTF8)
        $markerComplete = [string]$marker.operation_id -ceq $OperationId -and [string]$marker.status -ceq [string]$journal.status
      }
      if ($markerComplete -and [string]$journal.migration.phase -ceq "completed" -and [string]$journal.status -in @("committed", "rolled_back")) {
        Invoke-BoundedRecovery $journal
        New-SafeResult $true ([string]$journal.status) -Active $active -Candidate $journal | ConvertTo-Json -Depth 12 -Compress
        exit 0
      }
      if ($Action -ne "Recover") { throw "interrupted_upgrade_requires_recovery" }
      try { Invoke-BoundedRecovery $journal }
      catch {
        $failure = [string]$_.Exception.Message
        if ($failure -notmatch '^[a-z0-9_]+$') { $failure = "bounded_recovery_failed" }
        Record-BoundedFailure $journal $failure
      }
      New-SafeResult ([string]$journal.status -in @("committed", "rolled_back")) ([string]$journal.status) `
        -ErrorCode ([string]$journal.error_code) -Active (Read-ActiveRelease) -Candidate $journal | ConvertTo-Json -Depth 12 -Compress
      exit 0
    }
    if (
      [string]$active.operation_id -ceq $OperationId -and
      [string]$active.logical_identity -ceq [string]$journal.candidate.logical_identity
    ) {
      if (-not (Complete-UpgradeCommit -Journal $journal -Pointer $null `
          -CandidateIdentity ([string]$active.logical_identity))) {
        New-SafeResult $false "uncertain" -ErrorCode $journal.error_code -Active $active `
          -Candidate $journal | ConvertTo-Json -Depth 10 -Compress
        exit 0
      }
      New-SafeResult $true "committed" -Active $active -Candidate $journal |
        ConvertTo-Json -Depth 10 -Compress
      exit 0
    }
    if ([string]$journal.status -in @("committed", "rolled_back", "rejected")) {
      New-SafeResult ([string]$journal.status -in @("committed", "rolled_back")) `
        ([string]$journal.status) -ErrorCode ([string]$journal.error_code) `
        -Active (Read-ActiveRelease) -Candidate $journal | ConvertTo-Json -Depth 10 -Compress
      exit 0
    }
    if ($Action -ne "Recover") {
      $journal.status = "uncertain"
      $journal.error_code = "interrupted_upgrade_requires_recovery"
      Write-JsonAtomic -Path $JournalPath -Value $journal
      New-SafeResult $false "uncertain" -ErrorCode $journal.error_code -Active $active -Candidate $journal |
        ConvertTo-Json -Depth 10 -Compress
      exit 0
    }
    if ([string]$journal.status -in @("preflighted", "candidate_staged")) {
      Remove-UncommittedCandidate $journal
      $SafeToRemoveIncoming = $true
      $journal.status = "rejected"; $journal.error_code = "interrupted_before_switch"
      $journal.completed_at = [DateTimeOffset]::UtcNow.ToString("o")
      Write-Audit "upgrade_recovered" "rejected" ([string]$journal.candidate.logical_identity) `
        "interrupted_before_switch"
      Write-JsonAtomic -Path $JournalPath -Value $journal
      New-SafeResult $false "rejected" -ErrorCode $journal.error_code -Active $active `
        -Candidate $journal | ConvertTo-Json -Depth 10 -Compress
      exit 0
    }
    try {
      Restore-PreviousRelease $journal
      $journal.status = "rolled_back"; $journal.error_code = ""
      $journal.completed_at = [DateTimeOffset]::UtcNow.ToString("o")
      Write-Audit "upgrade_recovered" "rolled_back" ([string]$journal.candidate.logical_identity)
    }
    catch {
      $journal.status = "recovery_failed"; $journal.error_code = "recovery_failed"
      $journal.completed_at = [DateTimeOffset]::UtcNow.ToString("o")
      Write-Audit "upgrade_recovery_failed" "recovery_failed" `
        ([string]$journal.candidate.logical_identity) "recovery_failed"
    }
    Write-JsonAtomic -Path $JournalPath -Value $journal
    New-SafeResult ([string]$journal.status -eq "rolled_back") ([string]$journal.status) `
      -ErrorCode ([string]$journal.error_code) -Active (Read-ActiveRelease) -Candidate $journal |
      ConvertTo-Json -Depth 10 -Compress
    exit 0
  }

  if ($Action -ne "Apply") { throw "recover_journal_missing" }
  Assert-EntitlementFeature "software-updates"
  if (-not $CandidateRoot -or -not (Test-Path -LiteralPath $CandidateRoot -PathType Container)) {
    throw "candidate_directory_missing"
  }
  $normalizedCandidateRoot = [IO.Path]::GetFullPath($CandidateRoot).TrimEnd('\')
  $normalizedIncomingRoot = [IO.Path]::GetFullPath($IncomingOperationRoot).TrimEnd('\')
  if (-not $normalizedCandidateRoot.StartsWith(
      ($normalizedIncomingRoot + '\'), [StringComparison]::OrdinalIgnoreCase) -or
      (Split-Path -Parent $normalizedCandidateRoot) -cne $normalizedIncomingRoot) {
    throw "candidate_incoming_path_invalid"
  }
  $CandidateRoot = $normalizedCandidateRoot
  $candidateItem = Get-Item -LiteralPath $CandidateRoot -Force
  if (($candidateItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "candidate_directory_linked"
  }
  $manifestPath = Join-Path $CandidateRoot "MANIFEST.json"
  if ((Get-Item -LiteralPath $manifestPath).Length -gt 4MB) { throw "manifest_size_exceeded" }
  try { $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json }
  catch { throw "manifest_invalid" }
  $images = Assert-CandidateManifest $manifest $CandidateRoot
  $values = Get-ReleaseValues -Manifest $manifest -Images $images
  $candidateBytes = [long](@(Get-ChildItem -LiteralPath $CandidateRoot -File -Recurse -Force |
    Measure-Object -Property Length -Sum).Sum)
  if (
    [string]$manifest.candidate_id -cne $ExpectedCandidateId -or
    [string]$manifest.logical_identity -cne $ExpectedLogicalIdentity -or
    [string]$manifest.source_commit -cne $ExpectedSourceCommit -or
    [string]$manifest.alembic_head -cne $ExpectedAlembicHead -or
    "$($manifest.target_os)/$($manifest.target_architecture)" -cne $ExpectedPlatform -or
    $candidateBytes -ne $PackageBytes
  ) { throw "candidate_transport_identity_drift" }
  if ((Get-DockerPlatform) -cne $ExpectedPlatform) { throw "candidate_platform_mismatch" }
  $databaseHead = Get-DatabaseHead
  $backupEstimate = Get-DatabaseBackupEstimate
  $schemaKind = Get-SchemaUpgradeKind $databaseHead $ExpectedAlembicHead
  if ($schemaKind -eq "bounded_0012_0013") {
    $boundedResources = Get-BoundedResources $backupEstimate
    if (-not $boundedResources.docker_memory.sufficient -or -not $boundedResources.docker_data_volume.sufficient) {
      throw "bounded_restore_resources_insufficient"
    }
  }
  $candidateDrive = Get-PSDrive -Name ([IO.Path]::GetPathRoot($StableCandidatesRoot).Substring(0, 1))
  $backupDrive = Get-PSDrive -Name ([IO.Path]::GetPathRoot($BackupDirectory).Substring(0, 1))
  if ([long]$candidateDrive.Free -lt [Math]::Max(1GB, $candidateBytes * 2) -or
      [long]$backupDrive.Free -lt [long]$backupEstimate.required_bytes) {
    throw "upgrade_disk_space_insufficient"
  }
  $prospectiveSourceHash = (Get-FileHash -Algorithm SHA256 $EnvFile).Hash.ToLowerInvariant()
  Write-ProspectiveEnvironment -SourcePath $EnvFile -DestinationPath $ProspectiveEnvPath `
    -Values $values
  Invoke-PublisherVerification -Root $CandidateRoot -EnvironmentPath $ProspectiveEnvPath

  $candidateBase = Get-ComposeBase $CandidateRoot $ProspectiveEnvPath
  $model = Invoke-DockerText ($candidateBase + @("config", "--format", "json")) | ConvertFrom-Json
  Assert-NetworkBoundary $model
  Assert-ComposeManifestImages -Model $model -Images $images

  $stableCandidateRoot = Join-Path $StableCandidatesRoot ([string]$manifest.candidate_id)
  if (Test-Path -LiteralPath $stableCandidateRoot) { throw "stable_candidate_conflict" }
  $journal = [ordered]@{
    schema_version = 1; operation_id = $OperationId; action = "full-upgrade"
    reason_hash = Get-Sha256Text $Reason; status = "preflighted"; error_code = ""
    started_at = [DateTimeOffset]::UtcNow.ToString("o"); completed_at = ""; previous_release = $active
    candidate = [ordered]@{
      candidate_id = [string]$manifest.candidate_id
      logical_identity = [string]$manifest.logical_identity
      source_commit = [string]$manifest.source_commit
      alembic_head = [string]$manifest.alembic_head
      platform = "$($manifest.target_os)/$($manifest.target_architecture)"
      candidate_root = $stableCandidateRoot
    }
    source_environment_sha256 = $prospectiveSourceHash
    upgrade_kind = $schemaKind; migration = $null
    environment_backup = $null; backup = $null; switched = $false
  }
  Write-JsonAtomic -Path $JournalPath -Value $journal
  Write-Audit "upgrade_started" "executing" ([string]$manifest.logical_identity)
  Set-RestrictedTree -Path $CandidateRoot
  Move-Item -LiteralPath $CandidateRoot -Destination $stableCandidateRoot
  $CandidateRoot = $stableCandidateRoot
  Assert-RestrictedDirectory -Path $CandidateRoot
  $journal.status = "candidate_staged"
  Write-JsonAtomic -Path $JournalPath -Value $journal
  Renew-Locks

  if ($schemaKind -eq "bounded_0012_0013") {
    Invoke-BoundedApply $journal $manifest $images
    New-SafeResult ([string]$journal.status -in @("committed", "rolled_back")) ([string]$journal.status) `
      -ErrorCode ([string]$journal.error_code) -Active (Read-ActiveRelease) -Candidate $journal `
      -Backup $journal.backup | ConvertTo-Json -Depth 12 -Compress
    return
  }

  $pointerCommitted = $false
  $environmentMutationStarted = $false
  try {
    $backup = New-DatabaseBackup $manifest
    $envBackup = Join-Path $BackupDirectory ".env.prod.before"
    $journal.backup = $backup
    Write-JsonAtomic -Path $JournalPath -Value $journal
    Assert-LocksOwned
    if ((Get-FileHash -Algorithm SHA256 $EnvFile).Hash.ToLowerInvariant() -cne
        [string]$journal.source_environment_sha256) { throw "site_environment_identity_drift" }
    Prepare-EnvironmentSwitch -Journal $journal -JournalFile $JournalPath `
      -SourcePath $EnvFile -BackupPath $envBackup
    if ((Get-FileHash -Algorithm SHA256 $EnvFile).Hash.ToLowerInvariant() -cne
        [string]$journal.source_environment_sha256) { throw "site_environment_identity_drift" }
    $environmentMutationStarted = $true
    Set-ReleaseEnvironment -Path $EnvFile -ProspectivePath $ProspectiveEnvPath
    $journal.status = "switched"; $journal.switched = $true
    Write-JsonAtomic -Path $JournalPath -Value $journal
    $candidateBase = Get-ComposeBase $CandidateRoot $EnvFile
    [void](Invoke-DockerText ($candidateBase + @("up", "-d", "postgres", "redis")))
    [void](Invoke-DockerText ($candidateBase + @(
      "up", "--no-deps", "--force-recreate", "--abort-on-container-exit",
      "--exit-code-from", "migrate", "migrate"
    )) 600)
    [void](Invoke-DockerText ($candidateBase + @("up", "-d", "gw", "api", "web")))
    Wait-AllHealthy $candidateBase
    Assert-CurrentContainerIdentity -Images $images
    Assert-LocksOwned
    $pointer = [ordered]@{
      schema_version = 1; candidate_id = [string]$manifest.candidate_id
      logical_identity = [string]$manifest.logical_identity
      source_commit = [string]$manifest.source_commit; candidate_root = $CandidateRoot
      site_root = $SiteRoot; committed_at = [DateTimeOffset]::UtcNow.ToString("o")
      operation_id = $OperationId
    }
    Write-JsonAtomic -Path $ActiveReleasePath -Value $pointer
    $pointerCommitted = $true
    $commitSucceeded = Complete-UpgradeCommit -Journal $journal -Pointer $null `
      -CandidateIdentity ([string]$manifest.logical_identity)
    if (-not $commitSucceeded) {
      New-SafeResult $false "uncertain" -ErrorCode $journal.error_code `
        -Active (Read-ActiveRelease) -Candidate $journal -Backup $backup |
        ConvertTo-Json -Depth 10 -Compress
      return
    }
    $SafeToRemoveIncoming = $true
    New-SafeResult $true "committed" -Active $pointer -Candidate $journal -Backup $backup |
      ConvertTo-Json -Depth 10 -Compress
  }
  catch {
    $deploymentError = [string]$_.Exception.Message
    if ($pointerCommitted) {
      $journal.status = "uncertain"; $journal.error_code = "commit_audit_incomplete"
      try { Write-JsonAtomic -Path $JournalPath -Value $journal } catch { }
      New-SafeResult $false "uncertain" -ErrorCode $journal.error_code `
        -Active (Read-ActiveRelease) -Candidate $journal -Backup $backup |
        ConvertTo-Json -Depth 10 -Compress
    }
    elseif ($environmentMutationStarted) {
      $journal = Complete-UpgradeRollback -Journal $journal `
        -CandidateIdentity ([string]$manifest.logical_identity) -DeploymentError $deploymentError
    }
    else {
      $journal.status = "rejected"; $journal.error_code = $deploymentError
      Remove-UncommittedCandidate $journal
      $SafeToRemoveIncoming = $true
      Write-Audit "upgrade_rejected" "rejected" ([string]$manifest.logical_identity) $deploymentError
    }
    if (-not $environmentMutationStarted) {
      $journal.completed_at = [DateTimeOffset]::UtcNow.ToString("o")
      Write-JsonAtomic -Path $JournalPath -Value $journal
    }
    New-SafeResult $false ([string]$journal.status) -ErrorCode ([string]$journal.error_code) `
      -Active (Read-ActiveRelease) -Candidate $journal -Backup $backup |
      ConvertTo-Json -Depth 10 -Compress
  }
}
catch {
  $errorCode = [string]$_.Exception.Message
  if ($errorCode -notmatch '^[a-z0-9_]+$') { $errorCode = "upgrade_failed" }
  $finalStatus = "rejected"
  $journalExists = Test-Path -LiteralPath $JournalPath -PathType Leaf
  if ($journalExists) {
    try {
      $journal = Convert-UpgradeJson (Get-Content -LiteralPath $JournalPath -Raw -Encoding UTF8)
      if ([string]$journal.status -in @("switching", "switched") -or $null -ne $journal.migration) {
        $finalStatus = "uncertain"
      }
      if ([string]$journal.status -notin @("committed", "rolled_back", "recovery_failed")) {
        Assert-LocksOwned
        $journal.status = $finalStatus; $journal.error_code = $errorCode
        if ($finalStatus -eq "rejected") {
          Remove-UncommittedCandidate $journal
          $SafeToRemoveIncoming = $true
        }
        Write-JsonAtomic -Path $JournalPath -Value $journal
      }
    }
    catch { }
  }
  elseif ($Action -eq "Apply" -and (Test-SafeIncomingCandidateCleanup $CandidateRoot)) {
    $SafeToRemoveIncoming = $true
  }
  try { Write-Audit "upgrade_rejected" $finalStatus $auditCandidateIdentity $errorCode }
  catch { }
  New-SafeResult $false $finalStatus -ErrorCode $errorCode -Active $active -Locks $locks |
    ConvertTo-Json -Depth 10 -Compress
}
finally {
  try {
    Assert-LocksOwned
    if ($ExpectedAlembicHead -cne $BoundedTargetHead -and [string]$journal.upgrade_kind -cne "bounded_0012_0013") {
      Remove-Item -LiteralPath $ProspectiveEnvPath -Force -ErrorAction SilentlyContinue
      if ($Action -eq "Apply" -and $SafeToRemoveIncoming -and
          (Test-Path -LiteralPath $IncomingOperationRoot -PathType Container)) {
        Remove-Item -LiteralPath $IncomingOperationRoot -Recurse -Force
      }
    }
  }
  catch { }
  finally { Release-Locks }
}
