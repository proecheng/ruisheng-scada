[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [ValidateSet("Status", "Plan", "Initialize", "Apply", "Recover")]
  [string]$Action,
  [string]$CandidatePath = "",
  [string]$CurrentCandidateRoot = "",
  [Parameter(Mandatory)][string]$Target,
  [Parameter(Mandatory)][string]$SiteRoot,
  [Parameter(Mandatory)][string]$OperationId,
  [string]$Reason = "",
  [ValidateRange(120, 3600)][int]$LeaseSeconds = 900,
  [ValidateRange(1, 68719476736)][long]$MaxCandidateBytes = 34359738368,
  [switch]$DryRun,
  [switch]$Approved
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$script:LocalAuditMaxBytes = 16MB
$script:LocalAuditMaxRecords = 50000

function ConvertTo-PowerShellUtf8Expression {
  param([Parameter(Mandatory)][AllowEmptyString()][string]$Value)
  $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Value))
  return "[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$encoded'))"
}

function Get-Sha256Text {
  param([Parameter(Mandatory)][AllowEmptyString()][string]$Text)
  $sha = [Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
    return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
  }
  finally { $sha.Dispose() }
}

function Assert-RemotePath {
  param([Parameter(Mandatory)][string]$Path)
  if ($Path -notmatch '^[A-Za-z]:\\[^\r\n]*$') {
    throw "Remote paths must be absolute Windows paths."
  }
}

function Test-ExactKeys {
  param([Parameter(Mandatory)]$Value, [Parameter(Mandatory)][string[]]$Expected)
  if ($null -eq $Value -or $Value -isnot [PSCustomObject]) { return $false }
  $actual = @($Value.PSObject.Properties.Name)
  if ($actual.Count -ne $Expected.Count) { return $false }
  foreach ($key in $Expected) { if ($actual -cnotcontains $key) { return $false } }
  return $true
}

function Get-CandidateMetadata {
  param([Parameter(Mandatory)][string]$Path)
  $root = [IO.Path]::GetFullPath($Path).TrimEnd('\')
  if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    throw "CandidatePath must be an existing directory."
  }
  $rootItem = Get-Item -LiteralPath $root -Force
  if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "CandidatePath must not be a reparse point."
  }
  $candidateId = Split-Path -Leaf $root
  if ($candidateId -notmatch '^[a-z0-9][a-z0-9._-]{0,62}$') {
    throw "Candidate directory name is invalid."
  }
  $manifestPath = Join-Path $root "MANIFEST.json"
  $sumsPath = Join-Path $root "SHA256SUMS"
  $signaturePath = Join-Path $root "SHA256SUMS.sig"
  foreach ($required in @($manifestPath, $sumsPath, $signaturePath)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
      throw "Signed candidate is missing a required file."
    }
    $item = Get-Item -LiteralPath $required -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "Signed candidate contains a linked required file."
    }
  }
  if ((Get-Item -LiteralPath $manifestPath).Length -gt 4MB) {
    throw "Candidate manifest exceeds the size limit."
  }
  if ((Get-Item -LiteralPath $sumsPath).Length -gt 4MB -or
      (Get-Item -LiteralPath $signaturePath).Length -gt 64KB) {
    throw "Candidate signature metadata exceeds the size limit."
  }
  try { $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json }
  catch { throw "Candidate manifest is invalid JSON." }
  $baseKeys = @(
    "schema_version", "candidate_id", "source_commit", "generated_at", "target_os",
    "target_architecture", "alembic_head", "logical_identity", "tools", "authenticity", "images"
  )
  if ($manifest.schema_version -is [bool] -or
      ($manifest.schema_version -isnot [int] -and $manifest.schema_version -isnot [long])) {
    throw "Candidate manifest schema is invalid."
  }
  $schemaVersion = [int]$manifest.schema_version
  $expectedKeys = if ($schemaVersion -eq 2) { $baseKeys } elseif ($schemaVersion -eq 3) {
    @($baseKeys) + "qualification_toolchain"
  } else { throw "Candidate manifest schema is not supported." }
  if (-not (Test-ExactKeys -Value $manifest -Expected $expectedKeys) -or
      [string]$manifest.candidate_id -cne $candidateId -or
      [string]$manifest.source_commit -notmatch '^[0-9a-f]{40}$' -or
      [string]$manifest.logical_identity -notmatch '^sha256:[0-9a-f]{64}$' -or
      [string]$manifest.target_os -cne "linux" -or
      [string]$manifest.target_architecture -notin @("amd64", "arm64") -or
      [string]::IsNullOrWhiteSpace([string]$manifest.alembic_head) -or
      $manifest.images -isnot [Array] -or @($manifest.images).Count -ne 5) {
    throw "Candidate manifest identity is invalid."
  }
  $components = @($manifest.images | ForEach-Object { [string]$_.component })
  if (@($components | Sort-Object -Unique).Count -ne 5 -or
      @($components | Where-Object { $_ -notin @("postgres", "redis", "api", "gw", "web") }).Count) {
    throw "Candidate manifest image set is invalid."
  }
  $bytes = [long]0
  foreach ($item in @(Get-ChildItem -LiteralPath $root -Recurse -Force)) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "Candidate contains a reparse point."
    }
    if (-not $item.PSIsContainer) {
      $bytes += [long]$item.Length
      if ($bytes -gt $MaxCandidateBytes) { throw "Candidate exceeds the size limit." }
    }
  }
  $signatureFirst = Get-Content -LiteralPath $signaturePath -TotalCount 1 -Encoding ASCII
  if ([string]$signatureFirst -cne "-----BEGIN SSH SIGNATURE-----") {
    throw "Candidate signature armor is invalid."
  }
  return [ordered]@{
    root = $root
    candidate_id = $candidateId
    logical_identity = [string]$manifest.logical_identity
    source_commit = [string]$manifest.source_commit
    alembic_head = [string]$manifest.alembic_head
    platform = "$($manifest.target_os)/$($manifest.target_architecture)"
    package_bytes = $bytes
  }
}

function Invoke-SshScript {
  param([Parameter(Mandatory)][string]$Script)
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
  $output = $Script | & ssh.exe @sshArguments 2>&1
  $exitCode = $LASTEXITCODE
  $text = (($output | ForEach-Object { "$_" }) -join [Environment]::NewLine).Trim()
  if ($exitCode -ne 0) { throw "Remote upgrade transport failed with exit code $exitCode." }
  return $text
}

function Invoke-Updater {
  param(
    [Parameter(Mandatory)][string]$UpdaterSource,
    [Parameter(Mandatory)][AllowEmptyString()][string]$RemoteCandidateRoot,
    $Metadata = $null
  )
  $expectedCandidateId = if ($null -eq $Metadata) { "" } else { [string]$Metadata.candidate_id }
  $expectedIdentity = if ($null -eq $Metadata) { "" } else { [string]$Metadata.logical_identity }
  $expectedCommit = if ($null -eq $Metadata) { "" } else { [string]$Metadata.source_commit }
  $expectedHead = if ($null -eq $Metadata) { "" } else { [string]$Metadata.alembic_head }
  $expectedPlatform = if ($null -eq $Metadata) { "" } else { [string]$Metadata.platform }
  $packageBytes = if ($null -eq $Metadata) { 0 } else { [long]$Metadata.package_bytes }
  $transport = @"
& {
$UpdaterSource
} -Action $(ConvertTo-PowerShellUtf8Expression $Action) `
  -CandidateRoot $(ConvertTo-PowerShellUtf8Expression $RemoteCandidateRoot) `
  -SiteRoot $(ConvertTo-PowerShellUtf8Expression $SiteRoot) `
  -OperationId $(ConvertTo-PowerShellUtf8Expression $OperationId.ToLowerInvariant()) `
  -Reason $(ConvertTo-PowerShellUtf8Expression $Reason) `
  -ExpectedCandidateId $(ConvertTo-PowerShellUtf8Expression $expectedCandidateId) `
  -ExpectedLogicalIdentity $(ConvertTo-PowerShellUtf8Expression $expectedIdentity) `
  -ExpectedSourceCommit $(ConvertTo-PowerShellUtf8Expression $expectedCommit) `
  -ExpectedAlembicHead $(ConvertTo-PowerShellUtf8Expression $expectedHead) `
  -ExpectedPlatform $(ConvertTo-PowerShellUtf8Expression $expectedPlatform) `
  -PackageBytes $packageBytes -LeaseSeconds $LeaseSeconds -Approved:`$$([bool]$Approved)
"@
  $text = Invoke-SshScript -Script $transport
  if (-not $text) { throw "Remote upgrade returned no data." }
  try { $result = $text | ConvertFrom-Json }
  catch { throw "Remote upgrade returned invalid or non-allowlisted data." }
  $resultKeys = @(
    "schema_version", "ok", "status", "action", "operation_id", "error_code",
    "active_release", "candidate", "locks", "backup"
  )
  if (-not (Test-ExactKeys -Value $result -Expected $resultKeys) -or
      [int]$result.schema_version -ne 1 -or $result.ok -isnot [bool] -or
      [string]$result.operation_id -cne $OperationId.ToLowerInvariant() -or
      [string]$result.action -cne $Action -or
      [string]$result.status -notmatch '^(observed|planned|initialized|committed|rolled_back|recovery_failed|rejected|uncertain)$' -or
      [string]$result.error_code -notmatch '^$|^[a-z0-9_]+$') {
    throw "Remote upgrade returned invalid or non-allowlisted data."
  }
  return $result
}

function Set-RestrictedDirectory {
  param([Parameter(Mandatory)][string]$Path, [switch]$CreateAuditMutex)
  if (-not (Test-Path -LiteralPath $Path)) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
  }
  $item = Get-Item -LiteralPath $Path -Force
  if (-not $item.PSIsContainer -or
      ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Local audit directory is invalid."
  }
  $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
  $directoryAcl = New-Object Security.AccessControl.DirectorySecurity
  $directoryAcl.SetOwner($currentSid)
  $directoryAcl.SetAccessRuleProtection($true, $false)
  foreach ($sidValue in @($currentSid.Value, "S-1-5-18", "S-1-5-32-544") | Select-Object -Unique) {
    $sid = New-Object Security.Principal.SecurityIdentifier($sidValue)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
      $sid, [Security.AccessControl.FileSystemRights]::FullControl,
      ([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit),
      [Security.AccessControl.PropagationFlags]::None,
      [Security.AccessControl.AccessControlType]::Allow
    )
    [void]$directoryAcl.AddAccessRule($rule)
  }
  Set-Acl -LiteralPath $Path -AclObject $directoryAcl
  if ($CreateAuditMutex) {
    $mutex = Join-Path $Path ".full-upgrade-audit.lock"
    $stream = [IO.File]::Open($mutex, "OpenOrCreate", "ReadWrite", "None")
    $stream.Dispose()
    Set-RestrictedFile -Path $mutex
  }
}

function Set-RestrictedFile {
  param([Parameter(Mandatory)][string]$Path, [switch]$Create)
  if ($Create -and -not (Test-Path -LiteralPath $Path)) {
    $stream = [IO.File]::Open($Path, "CreateNew", "Write", "None")
    $stream.Dispose()
  }
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Local audit file is missing." }
  $item = Get-Item -LiteralPath $Path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Local audit file is invalid."
  }
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
  Set-Acl -LiteralPath $Path -AclObject $acl
}

function Write-LocalAudit {
  param([AllowNull()]$Result, [string]$TransportError = "")
  $directory = Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "Ruisheng\audit"
  Set-RestrictedDirectory -Path $directory -CreateAuditMutex
  $path = Join-Path $directory "remote-full-upgrade.jsonl"
  $lockPath = Join-Path $directory ".full-upgrade-audit.lock"
  Set-RestrictedFile -Path $lockPath
  Set-RestrictedFile -Path $path -Create
  $stream = [IO.File]::Open($lockPath, "Open", "ReadWrite", "None")
  try {
    $previousHash = "0" * 64
    if (Test-Path -LiteralPath $path -PathType Leaf) {
      if ((Get-Item -LiteralPath $path).Length -gt $script:LocalAuditMaxBytes) {
        throw "Local upgrade audit exceeds the size limit."
      }
      $count = 0
      foreach ($line in Get-Content -LiteralPath $path -Encoding UTF8) {
        if (-not $line) { continue }
        $count++
        if ($count -gt $script:LocalAuditMaxRecords) { throw "Local upgrade audit is too large." }
        try { $existing = $line | ConvertFrom-Json } catch { throw "Local upgrade audit is invalid." }
        if ([string]$existing.previous_hash -cne $previousHash) { throw "Local upgrade audit chain is invalid." }
        $payload = [ordered]@{}
        foreach ($property in $existing.PSObject.Properties) {
          if ($property.Name -ne "record_hash") { $payload[$property.Name] = $property.Value }
        }
        if ((Get-Sha256Text ($payload | ConvertTo-Json -Depth 8 -Compress)) -cne
            [string]$existing.record_hash) { throw "Local upgrade audit chain is invalid." }
        $previousHash = [string]$existing.record_hash
      }
    }
    $errorCode = if ($TransportError) { "transport_failed" } else { [string]$Result.error_code }
    $candidateIdentity = ""
    if ($null -ne $script:CandidateMetadata) {
      $candidateIdentity = [string]$script:CandidateMetadata.logical_identity
    }
    elseif ($null -ne $Result -and $null -ne $Result.active_release) {
      $candidateIdentity = [string]$Result.active_release.logical_identity
    }
    $payload = [ordered]@{
      schema_version = 1
      recorded_at = [DateTimeOffset]::UtcNow.ToString("o")
      operation_id = $OperationId.ToLowerInvariant()
      action = $Action
      target_hash = Get-Sha256Text $Target
      candidate_identity = $candidateIdentity
      reason_hash = Get-Sha256Text $Reason
      result = if ($TransportError) { "transport_failed" } else { [string]$Result.status }
      error_code = $errorCode
      previous_hash = $previousHash
    }
    $payload.record_hash = Get-Sha256Text ($payload | ConvertTo-Json -Depth 8 -Compress)
    [IO.File]::AppendAllText(
      $path, (($payload | ConvertTo-Json -Depth 8 -Compress) + [Environment]::NewLine),
      (New-Object Text.UTF8Encoding($false))
    )
  }
  finally { $stream.Dispose() }
}

if ($Action -in @("Initialize", "Apply", "Recover") -and -not $Approved) {
  throw "Initialize, Apply, and Recover require fresh approval through -Approved."
}
if ($Action -eq "Plan" -and -not $DryRun) {
  throw "Plan requires -DryRun."
}
if ($Action -in @("Initialize", "Apply", "Recover") -and
    ($Reason.Length -lt 8 -or $Reason.Length -gt 200 -or $Reason -match '[\x00-\x1f\x7f]')) {
  throw "Reason must contain 8-200 characters without control characters."
}
if ($Target -notmatch '^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$') {
  throw "Target must use the user@host form without whitespace."
}
if ($OperationId -notmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$') {
  throw "OperationId must be a UUID."
}
Assert-RemotePath -Path $SiteRoot
$SiteRoot = [IO.Path]::GetFullPath($SiteRoot).TrimEnd('\')
if ($SiteRoot -notmatch '^[A-Za-z]:\\Ruisheng\\candidates\\[^\\]+$') {
  throw "SiteRoot must be a direct child of C:\Ruisheng\candidates."
}
if ($Action -eq "Initialize") {
  if (-not $CurrentCandidateRoot) {
    throw "CurrentCandidateRoot is required for Initialize."
  }
  Assert-RemotePath -Path $CurrentCandidateRoot
}
if ($Action -in @("Plan", "Apply")) {
  if (-not $CandidatePath) { throw "CandidatePath is required for Plan and Apply." }
  $script:CandidateMetadata = Get-CandidateMetadata -Path $CandidatePath
}
else { $script:CandidateMetadata = $null }
foreach ($command in @("ssh.exe")) {
  if ($null -eq (Get-Command $command -ErrorAction SilentlyContinue)) {
    throw "Required command was not found: $command"
  }
}
if ($Action -eq "Apply" -and $null -eq (Get-Command "scp.exe" -ErrorAction SilentlyContinue)) {
  throw "Required command was not found: scp.exe"
}
$updaterPath = Join-Path $PSScriptRoot "remote_full_upgrade\target-updater.ps1"
if (-not (Test-Path -LiteralPath $updaterPath -PathType Leaf)) {
  throw "Target updater script was not found."
}
$updaterSource = Get-Content -LiteralPath $updaterPath -Raw -Encoding UTF8

if ($Action -eq "Status") {
  $result = Invoke-Updater -UpdaterSource $updaterSource -RemoteCandidateRoot ""
  $result | ConvertTo-Json -Depth 10
  if (-not [bool]$result.ok) { throw "Remote upgrade status was rejected: $($result.error_code)" }
  exit 0
}

if ($Action -eq "Plan") {
  $result = Invoke-Updater -UpdaterSource $updaterSource -RemoteCandidateRoot "" `
    -Metadata $script:CandidateMetadata
  $result | ConvertTo-Json -Depth 10
  if (-not [bool]$result.ok) { throw "Remote upgrade plan was rejected: $($result.error_code)" }
  exit 0
}

if ($Action -eq "Initialize") {
  $result = $null
  $transportError = ""
  try {
    $result = Invoke-Updater -UpdaterSource $updaterSource `
      -RemoteCandidateRoot $CurrentCandidateRoot
  }
  catch {
    $transportError = [string]$_.Exception.Message
    throw
  }
  finally { Write-LocalAudit -Result $result -TransportError $transportError }
  $result | ConvertTo-Json -Depth 10
  if (-not [bool]$result.ok) {
    throw "Remote upgrade initialization was rejected: $($result.error_code)"
  }
  exit 0
}

$result = $null
$transportError = ""
try {
  if ($Action -eq "Apply") {
    $incomingOperationRoot = "C:\Ruisheng\incoming\$($OperationId.ToLowerInvariant())"
    $remoteCandidateRoot = Join-Path $incomingOperationRoot $script:CandidateMetadata.candidate_id
    $prepare = @"
`$ErrorActionPreference = "Stop"
`$path = $(ConvertTo-PowerShellUtf8Expression $incomingOperationRoot)
if (Test-Path -LiteralPath `$path) { throw "incoming_operation_conflict" }
New-Item -ItemType Directory -Path `$path | Out-Null
`$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
`$acl = New-Object Security.AccessControl.DirectorySecurity
`$acl.SetOwner(`$sid)
`$acl.SetAccessRuleProtection(`$true, `$false)
foreach (`$value in @(`$sid.Value, "S-1-5-18", "S-1-5-32-544") | Select-Object -Unique) {
  `$identity = New-Object Security.Principal.SecurityIdentifier(`$value)
  `$rule = New-Object Security.AccessControl.FileSystemAccessRule(
    `$identity, [Security.AccessControl.FileSystemRights]::FullControl,
    ([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
      [Security.AccessControl.InheritanceFlags]::ObjectInherit),
    [Security.AccessControl.PropagationFlags]::None,
    [Security.AccessControl.AccessControlType]::Allow
  )
  [void]`$acl.AddAccessRule(`$rule)
}
Set-Acl -LiteralPath `$path -AclObject `$acl
"prepared"
"@
    [void](Invoke-SshScript -Script $prepare)
    $scpRoot = $incomingOperationRoot.Replace("\", "/") + "/"
    $scpArguments = @(
      "-r",
      "-o", "BatchMode=yes",
      "-o", "StrictHostKeyChecking=yes",
      "-o", "ConnectTimeout=10",
      $script:CandidateMetadata.root,
      "${Target}:$scpRoot"
    )
    & scp.exe @scpArguments
    if ($LASTEXITCODE -ne 0) { throw "Candidate upload failed with exit code $LASTEXITCODE." }
    $result = Invoke-Updater -UpdaterSource $updaterSource `
      -RemoteCandidateRoot $remoteCandidateRoot -Metadata $script:CandidateMetadata
  }
  else {
    $result = Invoke-Updater -UpdaterSource $updaterSource -RemoteCandidateRoot ""
  }
}
catch {
  $transportError = [string]$_.Exception.Message
  throw
}
finally {
  if ($Action -in @("Apply", "Recover")) {
    Write-LocalAudit -Result $result -TransportError $transportError
  }
}

$result | ConvertTo-Json -Depth 10
if (-not [bool]$result.ok) {
  throw "Remote upgrade was $($result.status): $($result.error_code)"
}
