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

function Get-Sha256Text {
  param([Parameter(Mandatory)][AllowEmptyString()][string]$Text)
  $sha = [Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
    return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
  }
  finally { $sha.Dispose() }
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
  $temporary = "$Path.$PID.$([Guid]::NewGuid().ToString('N')).tmp"
  $utf8 = New-Object Text.UTF8Encoding($false)
  [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 10 -Compress), $utf8)
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
  try { $active = Get-Content -LiteralPath $ActiveReleasePath -Raw -Encoding UTF8 | ConvertFrom-Json }
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
  if (-not $Process.HasExited) {
    try { $Process.Kill() } catch { }
    try { [void]$Process.WaitForExit(5000) } catch { }
  }
}

function Invoke-DockerText {
  param([Parameter(Mandatory)][string[]]$Arguments, [ValidateRange(1, 900)][int]$TimeoutSeconds = 120)
  function ConvertTo-NativeArgument {
    param([AllowEmptyString()][string]$Value)
    if ($Value -notmatch '[\s"]') { return $Value }
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

  $startInfo = New-Object Diagnostics.ProcessStartInfo
  $startInfo.FileName = (Get-Command docker.exe -ErrorAction Stop).Source
  $startInfo.Arguments = (@($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join " ")
  $startInfo.UseShellExecute = $false
  $startInfo.CreateNoWindow = $true
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  $process = New-Object Diagnostics.Process
  $process.StartInfo = $startInfo
  $started = $false
  try {
    $started = $process.Start()
    if (-not $started) { throw "docker_command_failed" }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    $renewAt = [DateTimeOffset]::UtcNow.AddSeconds([Math]::Max(30, [int]($LeaseSeconds / 3)))
    while (-not $process.WaitForExit(1000)) {
      if ([DateTimeOffset]::UtcNow -ge $deadline) {
        try { $process.Kill() } catch { }
        [void]$process.WaitForExit(5000)
        throw "docker_command_timeout"
      }
      if ($AcquiredLocks.Count -gt 0 -and [DateTimeOffset]::UtcNow -ge $renewAt) {
        Renew-Locks
        $renewAt = [DateTimeOffset]::UtcNow.AddSeconds([Math]::Max(30, [int]($LeaseSeconds / 3)))
      }
    }
    $process.WaitForExit()
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    if ($process.ExitCode -ne 0) { throw "docker_command_failed" }
    return (([string]$stdout) + ([string]$stderr)).Trim()
  }
  catch {
    Stop-StartedProcess -Process $process -Started $started
    throw
  }
  finally { $process.Dispose() }
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

function Get-LockSummary {
  param([Parameter(Mandatory)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    return [ordered]@{ present = $false; state = "absent" }
  }
  try {
    $record = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
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
        $existing = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
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
  foreach ($held in @($AcquiredLocks)) {
    try { $record = Get-Content -LiteralPath $held.path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw "upgrade_lock_lost" }
    if (
      [string]$record.operation_id -cne $OperationId -or [int]$record.pid -ne $PID -or
      [string]$record.process_started_at -cne $ProcessStartedAt
    ) { throw "upgrade_lock_lost" }
  }
}

function Renew-Locks {
  Assert-LocksOwned
  foreach ($held in @($AcquiredLocks)) {
    $record = Get-Content -LiteralPath $held.path -Raw -Encoding UTF8 | ConvertFrom-Json
    $record.expires_at = [DateTimeOffset]::UtcNow.AddSeconds($LeaseSeconds).ToString("o")
    Write-JsonAtomic -Path $held.path -Value $record
  }
}

function Release-Locks {
  $locks = @($AcquiredLocks); [array]::Reverse($locks)
  foreach ($held in $locks) {
    try {
      $record = Get-Content -LiteralPath $held.path -Raw -Encoding UTF8 | ConvertFrom-Json
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
    [string]$ErrorCode = "")
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
          $existing = $line | ConvertFrom-Json
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
            [string]$existing.error_code -ceq $ErrorCode
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
    $payload.record_hash = Get-Sha256Text ($payload | ConvertTo-Json -Depth 8 -Compress)
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
    "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
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
function Write-ProspectiveEnvironment {
  param(
    [Parameter(Mandatory)][string]$SourcePath,
    [Parameter(Mandatory)][string]$DestinationPath,
    [Parameter(Mandatory)][hashtable]$Values
  )
  $allowed = @(
    "TARGET_PLATFORM", "POSTGRES_IMAGE", "REDIS_IMAGE", "API_IMAGE", "GW_IMAGE", "WEB_IMAGE"
  )
  if ($Values.Count -ne $allowed.Count -or
      @($Values.Keys | Where-Object { $_ -notin $allowed }).Count -ne 0) {
    throw "release_environment_values_invalid"
  }
  if (Test-Path -LiteralPath $DestinationPath) { throw "prospective_environment_conflict" }
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
  $stream = [IO.File]::Open($DestinationPath, "CreateNew", "Write", "None")
  $stream.Dispose()
  Set-RestrictedFileAcl -Path $DestinationPath
  $stream = [IO.File]::Open($DestinationPath, "Open", "Write", "None")
  try {
    $output = (New-Object Text.UTF8Encoding($false)).GetBytes($text)
    if ($hasBom) { $stream.Write([byte[]]@(0xEF, 0xBB, 0xBF), 0, 3) }
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
    [Parameter(Mandatory)][string]$ProspectivePath
  )
  Assert-RestrictedFile -Path $ProspectivePath
  $temporary = "$Path.$PID.$([Guid]::NewGuid().ToString('N')).tmp"
  $replaceBackup = "$Path.$PID.$([Guid]::NewGuid().ToString('N')).replace.bak"
  try {
    Copy-Item -LiteralPath $ProspectivePath -Destination $temporary
    Set-Acl -LiteralPath $temporary -AclObject (Get-Acl -LiteralPath $Path)
    [IO.File]::Replace($temporary, $Path, $replaceBackup)
  }
  finally {
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

$active = $null
$locks = $null
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
  Assert-RestrictedFile $EnvFile
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
      try { $journal = Get-Content -LiteralPath $JournalPath -Raw -Encoding UTF8 | ConvertFrom-Json }
      catch { throw "upgrade_journal_invalid" }
    }
    New-SafeResult $true "observed" -Active $active -Candidate $journal -Locks $locks |
      ConvertTo-Json -Depth 10 -Compress
    exit 0
  }

  if ($Action -eq "Plan") {
    if (
      $ExpectedCandidateId -notmatch '^[a-z0-9][a-z0-9._-]{0,62}$' -or
      $ExpectedLogicalIdentity -notmatch '^sha256:[0-9a-f]{64}$' -or
      $ExpectedSourceCommit -notmatch '^[0-9a-f]{40}$' -or -not $ExpectedAlembicHead -or
      $ExpectedPlatform -notmatch '^linux/(amd64|arm64)$' -or $PackageBytes -le 0
    ) { throw "plan_candidate_identity_invalid" }
    $databaseHead = Get-DatabaseHead
    $backupEstimate = Get-DatabaseBackupEstimate
    $platform = Get-DockerPlatform
    $candidateDrive = Get-PSDrive -Name ([IO.Path]::GetPathRoot($StableCandidatesRoot).Substring(0, 1))
    $backupDrive = Get-PSDrive -Name ([IO.Path]::GetPathRoot($BackupDirectory).Substring(0, 1))
    $candidateRequired = [long][Math]::Max(1GB, $PackageBytes * 2)
    $backupRequired = [long]$backupEstimate.required_bytes
    $candidate = [ordered]@{
      candidate_id = $ExpectedCandidateId; logical_identity = $ExpectedLogicalIdentity
      source_commit = $ExpectedSourceCommit; alembic_head = $ExpectedAlembicHead
      platform = $ExpectedPlatform; package_bytes = $PackageBytes
      schema_compatible = $databaseHead -ceq $ExpectedAlembicHead
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
      }
      steps = @(
        "verify_identity_and_resources", "acquire_shared_and_legacy_locks",
        "verify_publisher_and_network", "backup_database_and_environment",
        "switch_environment_and_services", "verify_health_and_image_identity", "commit_pointer_and_audit"
      )
    }
    New-SafeResult $true "planned" -Active $active -Candidate $candidate -Locks $locks |
      ConvertTo-Json -Depth 10 -Compress
    exit 0
  }

  $posture = Get-SshPosture
  if (-not $posture.mutation_allowed) { throw "ssh_not_key_only" }
  Assert-RestrictedDirectory $StateDirectory
  Assert-RestrictedDirectory $AuditDirectory
  Assert-RestrictedFile $AuditLockPath
  Acquire-LeasedLock -Path $SharedLockPath -Name "shared-maintenance"
  try { Acquire-LeasedLock -Path $LegacyLockPath -Name "legacy-hotfix" }
  catch { Release-Locks; throw }
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
}
catch {
  Release-Locks
  $errorCode = [string]$_.Exception.Message
  if ($errorCode -notmatch '^[a-z0-9_]+$') { $errorCode = "upgrade_preflight_failed" }
  New-SafeResult $false "rejected" -ErrorCode $errorCode -Active $active -Locks $locks |
    ConvertTo-Json -Depth 10 -Compress
  exit 0
}

try {
  if ($Action -eq "Initialize") {
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
    $journal = Get-Content -LiteralPath $JournalPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$journal.operation_id -cne $OperationId -or
        [string]$journal.reason_hash -cne (Get-Sha256Text $Reason)) {
      throw "upgrade_operation_identity_conflict"
    }
    if ($Action -eq "Apply") { Assert-JournalCandidateIdentity $journal }
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
    if ([string]$journal.status -in @("committed", "rolled_back", "recovery_failed", "rejected")) {
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
  if ($databaseHead -cne $ExpectedAlembicHead) { throw "schema_head_changed" }
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
    started_at = [DateTimeOffset]::UtcNow.ToString("o"); previous_release = $active
    candidate = [ordered]@{
      candidate_id = [string]$manifest.candidate_id
      logical_identity = [string]$manifest.logical_identity
      source_commit = [string]$manifest.source_commit
      alembic_head = [string]$manifest.alembic_head
      platform = "$($manifest.target_os)/$($manifest.target_architecture)"
      candidate_root = $stableCandidateRoot
    }
    source_environment_sha256 = $prospectiveSourceHash
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
      $journal = Get-Content -LiteralPath $JournalPath -Raw -Encoding UTF8 | ConvertFrom-Json
      if ([string]$journal.status -in @("switching", "switched")) {
        $finalStatus = "uncertain"
      }
      if ([string]$journal.status -notin @("committed", "rolled_back", "recovery_failed")) {
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
  Release-Locks
  Remove-Item -LiteralPath $ProspectiveEnvPath -Force -ErrorAction SilentlyContinue
  if ($Action -eq "Apply" -and $SafeToRemoveIncoming -and
      (Test-Path -LiteralPath $IncomingOperationRoot -PathType Container)) {
    try { Remove-Item -LiteralPath $IncomingOperationRoot -Recurse -Force }
    catch { }
  }
}
