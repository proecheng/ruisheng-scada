[CmdletBinding()]
param(
  [ValidateSet("Install", "Status", "Authorize", "Prepare", "Cleanup", "ValidateRuntime")]
  [string]$Action = "Status",
  [string]$GrantPath = "",
  [Parameter(Mandatory)][string]$SiteId,
  [string]$OperationId = "",
  [string]$Reason = "",
  [string]$GrantSha256 = "",
  [string]$ReasonSha256 = "",
  [string]$Feature = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$script:PythonPath = "C:\ProgramData\Ruisheng\runtime\python.exe"
$script:VendorRoot = "C:\ProgramData\Ruisheng\entitlement-runtime\vendor"
$script:VendorManifestPath = "C:\ProgramData\Ruisheng\entitlement-runtime\vendor-manifest.sha256"
$script:EntitlementScript = "C:\ProgramData\Ruisheng\bin\entitlement.py"
$script:VerifierPath = $PSCommandPath
$script:PublicKeyPath = "C:\ProgramData\Ruisheng\trust\entitlement-public-key"
$script:SiteIdentityPath = "C:\ProgramData\Ruisheng\trust\entitlement-site-id"
$script:RuntimeStatePath = "C:\ProgramData\Ruisheng\entitlement-runtime-state.json"
$script:RuntimeUseLockPath = "C:\ProgramData\Ruisheng\entitlement-runtime-use.lock"
$script:BootstrapJournalPath = "C:\ProgramData\Ruisheng\entitlement-bootstrap-journal.json"
$script:EntitlementRoot = "C:\ProgramData\Ruisheng\entitlements"
$script:IncomingRoot = "C:\ProgramData\Ruisheng\entitlements\incoming"
$script:IncomingLockPath = Join-Path $script:IncomingRoot ".incoming.lock"
$script:StatePath = "C:\ProgramData\Ruisheng\entitlements\current.json"
$script:AuditPath = "C:\ProgramData\Ruisheng\entitlements\audit.jsonl"
$script:TimeStatePath = "C:\ProgramData\Ruisheng\entitlements\last-seen.json"
$script:AllowedSids = @("S-1-5-18", "S-1-5-32-544")
$script:MaxGrantBytes = 1MB
$script:MaxIncomingBytes = 128MB
$script:MaxIncomingReservations = 2048
$script:MaxReservationBytes = 512
$script:MaxManifestBytes = 8MB
$script:MaxVendorFiles = 10000
$script:MaxVendorBytes = 512MB
$script:MaxTreeItems = 20000
$script:MaxOutputCharacters = 65536
$script:ToolTimeoutSeconds = 60
$script:CleanupPath = ""
$script:ReservationPath = ""
$script:MutationDispatched = $false
$script:ToolRejected = $false
$script:PinnedSiteId = ""

function New-Failure([string]$Code, [string]$Status = "rejected") {
  return [ordered]@{
    schema_version = 1
    ok = $false
    status = $Status
    error_code = $Code
    safety_preserved = $true
    collection_preserved = $true
    alarms_preserved = $true
    data_preserved = $true
  }
}

function Get-SafeCode([string]$Message) {
  if ($Message -match '^[A-Za-z0-9_]+$') { return $Message }
  return "target_verifier_failed"
}

function Get-OrdinalSortedStrings([string[]]$Values) {
  [string[]]$copy = @($Values)
  [Array]::Sort($copy, [StringComparer]::Ordinal)
  return $copy
}

function Get-IdentitySid($Identity) {
  try {
    $reference = if ($Identity -is [Security.Principal.IdentityReference]) {
      $Identity
    }
    else {
      New-Object Security.Principal.NTAccount([string]$Identity)
    }
    return $reference.Translate([Security.Principal.SecurityIdentifier]).Value
  }
  catch {
    throw "path_acl_identity_invalid"
  }
}

function Assert-ProtectedItem {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][ValidateSet("File", "Directory")][string]$Kind
  )
  $pathType = if ($Kind -eq "File") { "Leaf" } else { "Container" }
  if (-not (Test-Path -LiteralPath $Path -PathType $pathType)) { throw "protected_item_missing" }
  $item = Get-Item -LiteralPath $Path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "path_reparse_point"
  }
  $acl = Get-Acl -LiteralPath $Path
  $ownerSid = Get-IdentitySid $acl.Owner
  if ($script:AllowedSids -notcontains $ownerSid) { throw "path_owner_invalid" }
  if (-not $acl.AreAccessRulesProtected) { throw "path_acl_unprotected" }
  $rules = @($acl.Access)
  if ($rules.Count -ne $script:AllowedSids.Count) { throw "path_acl_invalid" }
  $seen = @{}
  foreach ($rule in $rules) {
    $sid = Get-IdentitySid $rule.IdentityReference
    if ($script:AllowedSids -notcontains $sid -or $seen.ContainsKey($sid)) {
      throw "path_acl_invalid"
    }
    if ($rule.IsInherited -or
        $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
        $rule.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl -or
        $rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None) {
      throw "path_acl_invalid"
    }
    if ($Kind -eq "Directory") {
      $expectedInheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
      if ($rule.InheritanceFlags -ne $expectedInheritance) { throw "path_acl_invalid" }
    }
    elseif ($rule.InheritanceFlags -ne [Security.AccessControl.InheritanceFlags]::None) {
      throw "path_acl_invalid"
    }
    $seen[$sid] = $true
  }
  foreach ($sid in $script:AllowedSids) {
    if (-not $seen.ContainsKey($sid)) { throw "path_acl_invalid" }
  }
}

function Set-ProtectedAcl {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][ValidateSet("File", "Directory")][string]$Kind
  )
  $security = if ($Kind -eq "Directory") {
    New-Object Security.AccessControl.DirectorySecurity
  }
  else {
    New-Object Security.AccessControl.FileSecurity
  }
  $security.SetAccessRuleProtection($true, $false)
  $owner = New-Object Security.Principal.SecurityIdentifier("S-1-5-32-544")
  $security.SetOwner($owner)
  foreach ($sidText in $script:AllowedSids) {
    $sid = New-Object Security.Principal.SecurityIdentifier($sidText)
    $inheritance = if ($Kind -eq "Directory") {
      [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    }
    else {
      [Security.AccessControl.InheritanceFlags]::None
    }
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
      $sid,
      [Security.AccessControl.FileSystemRights]::FullControl,
      $inheritance,
      [Security.AccessControl.PropagationFlags]::None,
      [Security.AccessControl.AccessControlType]::Allow
    )
    [void]$security.AddAccessRule($rule)
  }
  Set-Acl -LiteralPath $Path -AclObject $security
  Assert-ProtectedItem -Path $Path -Kind $Kind
}

function Ensure-ProtectedDirectory([string]$Path) {
  if (Test-Path -LiteralPath $Path) {
    Assert-ProtectedItem -Path $Path -Kind Directory
    return
  }
  [void](New-Item -ItemType Directory -Path $Path)
  Set-ProtectedAcl -Path $Path -Kind Directory
}

function Get-TextSha256([string]$Value) {
  $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
  $hash = [Security.Cryptography.SHA256]::Create()
  try { return ([BitConverter]::ToString($hash.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant() }
  finally { $hash.Dispose() }
}

function Get-GrantIncomingPaths {
  return [pscustomobject]@{
    grant = "$($script:IncomingRoot)\$OperationId-$GrantSha256.json"
    reservation = "$($script:IncomingRoot)\$OperationId.reservation.json"
  }
}

function Enter-IncomingLock {
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
  while ([DateTimeOffset]::UtcNow -lt $deadline) {
    $stream = $null
    try {
      if (Test-Path -LiteralPath $script:IncomingLockPath) {
        Assert-ProtectedItem -Path $script:IncomingLockPath -Kind File
        $stream = [IO.File]::Open(
          $script:IncomingLockPath, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite,
          [IO.FileShare]::ReadWrite
        )
      } else {
        $stream = [IO.File]::Open(
          $script:IncomingLockPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite,
          [IO.FileShare]::ReadWrite
        )
        $stream.WriteByte(0)
        $stream.Flush($true)
        Set-ProtectedAcl -Path $script:IncomingLockPath -Kind File
      }
      $stream.Lock(0, 1)
      return $stream
    } catch {
      if ($null -ne $stream) { $stream.Dispose() }
      if ([string]$_.Exception.Message -match '^(path_|protected_)') { throw }
      Start-Sleep -Milliseconds 50
    }
  }
  throw "incoming_busy"
}

function Exit-IncomingLock($Stream) {
  if ($null -eq $Stream) { return }
  try { $Stream.Unlock(0, 1) } catch { }
  $Stream.Dispose()
}

function Get-GrantReservationText {
  return '{"grant_sha256":"' + $GrantSha256 + '","operation_id":"' + $OperationId +
    '","reason_sha256":"' + $ReasonSha256 + '","schema_version":1,"site_id":"' +
    $SiteId + '"}' + "`n"
}

function Read-GrantReservation([string]$Path) {
  Assert-ProtectedItem -Path $Path -Kind File
  $item = Get-Item -LiteralPath $Path -Force
  if ($item.Length -le 0 -or $item.Length -gt $script:MaxReservationBytes) {
    throw "reservation_invalid"
  }
  $text = Get-StrictAscii -Bytes (
    Read-BoundedBytes $Path $script:MaxReservationBytes "reservation_invalid"
  ) -ErrorCode "reservation_invalid"
  try { $record = $text | ConvertFrom-Json }
  catch { throw "reservation_invalid" }
  Assert-ExactFields -Value $record -Expected @(
    "grant_sha256", "operation_id", "reason_sha256", "schema_version", "site_id"
  )
  if ($record.schema_version -ne 1 -or
      [string]$record.operation_id -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -or
      [string]$record.site_id -notmatch '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' -or
      [string]$record.grant_sha256 -notmatch '^[0-9a-f]{64}$' -or
      [string]$record.reason_sha256 -notmatch '^[0-9a-f]{64}$' -or
      $text -cne ('{"grant_sha256":"' + [string]$record.grant_sha256 +
        '","operation_id":"' + [string]$record.operation_id + '","reason_sha256":"' +
        [string]$record.reason_sha256 + '","schema_version":1,"site_id":"' +
        [string]$record.site_id + '"}' + "`n") -or
      (Split-Path -Leaf $Path) -cne "$($record.operation_id).reservation.json") {
    throw "reservation_invalid"
  }
  return $record
}

function Assert-GrantReservation {
  $record = Read-GrantReservation $script:ReservationPath
  if ([string]$record.operation_id -cne $OperationId -or
      [string]$record.site_id -cne $SiteId -or
      [string]$record.grant_sha256 -cne $GrantSha256 -or
      [string]$record.reason_sha256 -cne $ReasonSha256 -or
      (Get-GrantReservationText) -cne (
        Get-StrictAscii -Bytes (
          Read-BoundedBytes $script:ReservationPath $script:MaxReservationBytes "reservation_invalid"
        ) -ErrorCode "reservation_invalid"
      )) {
    throw "operation_conflict"
  }
}

function Write-GrantReservation {
  $temporary = "$($script:ReservationPath).$([Guid]::NewGuid().ToString('N')).tmp"
  try {
    $bytes = [Text.Encoding]::ASCII.GetBytes((Get-GrantReservationText))
    $stream = [IO.File]::Open(
      $temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None
    )
    try {
      $stream.Write($bytes, 0, $bytes.Length)
      $stream.Flush($true)
    } finally { $stream.Dispose() }
    Set-ProtectedAcl -Path $temporary -Kind File
    Move-Item -LiteralPath $temporary -Destination $script:ReservationPath
  } finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
  }
}

function Prepare-GrantReservation {
  $reservations = New-Object Collections.Generic.List[object]
  [long]$incomingBytes = 0
  foreach ($item in @(Get-ChildItem -LiteralPath $script:IncomingRoot -Force)) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.PSIsContainer) {
      throw "incoming_store_invalid"
    }
    if ($item.FullName -ceq $script:IncomingLockPath) {
      Assert-ProtectedItem -Path $item.FullName -Kind File
      continue
    }
    if ($item.Name -match '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.reservation\.json$') {
      $record = Read-GrantReservation $item.FullName
      $reservations.Add([pscustomobject]@{ item = $item; record = $record })
      continue
    }
    if ($item.Name -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}-[0-9a-f]{64}\.json$' -or
        $item.Length -le 0 -or $item.Length -gt $script:MaxGrantBytes) {
      throw "incoming_store_invalid"
    }
    $incomingBytes += [long]$item.Length
    if ($incomingBytes -gt $script:MaxIncomingBytes) { throw "incoming_store_full" }
  }
  if (Test-Path -LiteralPath $script:ReservationPath) {
    Assert-GrantReservation
    return
  }
  $candidateByKey = New-Object Collections.Hashtable ([StringComparer]::Ordinal)
  foreach ($entry in $reservations) {
    $grantPath = "$($script:IncomingRoot)\$($entry.record.operation_id)-$($entry.record.grant_sha256).json"
    if (-not (Test-Path -LiteralPath $grantPath)) {
      $key = $entry.item.LastWriteTimeUtc.Ticks.ToString(
        "D19", [Globalization.CultureInfo]::InvariantCulture
      ) + "|" + $entry.item.Name
      $candidateByKey[$key] = $entry
    }
  }
  [string[]]$candidateKeys = @($candidateByKey.Keys)
  [Array]::Sort($candidateKeys, [StringComparer]::Ordinal)
  $reservationCount = $reservations.Count
  [long]$reservedBytes = $incomingBytes +
    ([long]$candidateByKey.Count * [long]$script:MaxGrantBytes)
  foreach ($key in $candidateKeys) {
    if ($reservationCount -lt $script:MaxIncomingReservations -and
        $reservedBytes + $script:MaxGrantBytes -le $script:MaxIncomingBytes) {
      break
    }
    $candidate = $candidateByKey[$key]
    Remove-Item -LiteralPath $candidate.item.FullName -Force
    if (Test-Path -LiteralPath $candidate.item.FullName) { throw "incoming_cleanup_failed" }
    $reservationCount--
    $reservedBytes -= $script:MaxGrantBytes
  }
  if ($reservationCount -ge $script:MaxIncomingReservations -or
      $reservedBytes + $script:MaxGrantBytes -gt $script:MaxIncomingBytes) {
    throw "incoming_store_full"
  }
  Write-GrantReservation
  Assert-GrantReservation
}

function Enter-RuntimeUseLock {
  Assert-ProtectedItem -Path $script:RuntimeUseLockPath -Kind File
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
  while ([DateTimeOffset]::UtcNow -lt $deadline) {
    $stream = $null
    try {
      $stream = [IO.File]::Open(
        $script:RuntimeUseLockPath, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::ReadWrite
      )
      $stream.Lock(1, 1)
      return $stream
    } catch {
      if ($null -ne $stream) { $stream.Dispose() }
      Start-Sleep -Milliseconds 50
    }
  }
  throw "runtime_busy"
}

function Exit-RuntimeUseLock($Stream) {
  if ($null -eq $Stream) { return }
  try { $Stream.Unlock(1, 1) } catch { }
  $Stream.Dispose()
}

function Get-SafeTreeItems([string]$Root) {
  $items = New-Object 'Collections.Generic.List[IO.FileSystemInfo]'
  $pending = New-Object 'Collections.Generic.Queue[string]'
  $pending.Enqueue($Root)
  while ($pending.Count -gt 0) {
    $directory = $pending.Dequeue()
    foreach ($item in @(Get-ChildItem -LiteralPath $directory -Force)) {
      if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "path_reparse_point"
      }
      [void]$items.Add($item)
      if ($items.Count -gt $script:MaxTreeItems) { throw "path_tree_limit_exceeded" }
      if ($item.PSIsContainer) { $pending.Enqueue($item.FullName) }
    }
  }
  return $items
}

function Assert-ProtectedTree([string]$Root) {
  Assert-ProtectedItem -Path $Root -Kind Directory
  foreach ($item in @(Get-SafeTreeItems -Root $Root)) {
    $kind = if ($item.PSIsContainer) { "Directory" } else { "File" }
    Assert-ProtectedItem -Path $item.FullName -Kind $kind
  }
}

function Set-ProtectedTree([string]$Root) {
  if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return }
  $items = @(Get-SafeTreeItems -Root $Root)
  foreach ($item in $items) {
    $kind = if ($item.PSIsContainer) { "Directory" } else { "File" }
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "path_reparse_point"
    }
    Set-ProtectedAcl -Path $item.FullName -Kind $kind
  }
  Set-ProtectedAcl -Path $Root -Kind Directory
}

function Repair-InheritedTransactionAcl(
  [string]$Path,
  [ValidateSet("File", "Directory")][string]$Kind = "File"
) {
  $pathType = if ($Kind -eq "File") { "Leaf" } else { "Container" }
  if (-not (Test-Path -LiteralPath $Path -PathType $pathType)) { return }
  $item = Get-Item -LiteralPath $Path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return }
  $acl = Get-Acl -LiteralPath $Path
  if ($acl.AreAccessRulesProtected) { return }
  $rules = @($acl.Access)
  if ($rules.Count -ne $script:AllowedSids.Count) { return }
  $seen = @{}
  foreach ($rule in $rules) {
    $sid = Get-IdentitySid $rule.IdentityReference
    if ($script:AllowedSids -notcontains $sid -or $seen.ContainsKey($sid) -or
        -not $rule.IsInherited -or
        $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
        $rule.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl -or
        $rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None) { return }
    $seen[$sid] = $true
  }
  Set-ProtectedAcl -Path $Path -Kind $Kind
}

function Repair-InheritedTransactionAcls {
  if (-not (Test-Path -LiteralPath $script:EntitlementRoot -PathType Container)) { return }
  Assert-ProtectedItem -Path $script:EntitlementRoot -Kind Directory
  foreach ($path in @(
      $script:StatePath,
      $script:TimeStatePath,
      $script:AuditPath,
      "$($script:AuditPath).1",
      "$($script:AuditPath).2",
      (Join-Path $script:EntitlementRoot ".transaction.lock"),
      (Join-Path $script:EntitlementRoot "transaction.json")
  )) {
    Repair-InheritedTransactionAcl $path
  }
  $operations = Join-Path $script:EntitlementRoot "operations"
  if (Test-Path -LiteralPath $operations -PathType Container) {
    Repair-InheritedTransactionAcl $operations "Directory"
    foreach ($file in @(Get-ChildItem -LiteralPath $operations -File -Force)) {
      if ($file.Name -match '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.json$') {
        Repair-InheritedTransactionAcl $file.FullName
      }
    }
  }
}

function Get-StrictAscii([byte[]]$Bytes, [string]$ErrorCode) {
  try {
    $encoding = [Text.Encoding]::GetEncoding(
      20127,
      [Text.EncoderFallback]::ExceptionFallback,
      [Text.DecoderFallback]::ExceptionFallback
    )
    return $encoding.GetString($Bytes)
  }
  catch {
    throw $ErrorCode
  }
}

function Read-BoundedBytes([string]$Path, [long]$Maximum, [string]$ErrorCode) {
  $stream = [IO.File]::Open(
    $Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
  )
  try {
    $memory = New-Object IO.MemoryStream
    try {
      $buffer = New-Object byte[] 4096
      while (($count = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
        if ($memory.Length + $count -gt $Maximum) { throw $ErrorCode }
        $memory.Write($buffer, 0, $count)
      }
      if ($memory.Length -le 0) { throw $ErrorCode }
      return $memory.ToArray()
    }
    finally { $memory.Dispose() }
  }
  finally { $stream.Dispose() }
}

function Assert-RuntimeState {
  Assert-ProtectedItem -Path $script:RuntimeStatePath -Kind File
  $item = Get-Item -LiteralPath $script:RuntimeStatePath -Force
  if ($item.Length -le 0 -or $item.Length -gt 4096) { throw "runtime_state_invalid" }
  $text = Get-StrictAscii -Bytes (Read-BoundedBytes $script:RuntimeStatePath 4096 "runtime_state_invalid") `
    -ErrorCode "runtime_state_invalid"
  $match = [regex]::Match(
    $text,
    '^\{"bundle_sums_sha256":"([0-9a-f]{64})","entitlement_key_generation":([1-9][0-9]*),"entitlement_sha256":"([0-9a-f]{64})","public_key_sha256":"([0-9a-f]{64})","python_sha256":"([0-9a-f]{64})","runtime_epoch":([1-9][0-9]*),"schema_version":1,"vendor_manifest_sha256":"([0-9a-f]{64})","verifier_sha256":"([0-9a-f]{64})"\}\n$',
    [Text.RegularExpressions.RegexOptions]::CultureInvariant
  )
  if (-not $match.Success) { throw "runtime_state_invalid" }
  if ($match.Groups[2].Value -cne "1") { throw "entitlement_key_generation_unsupported" }
  try {
    [void][long]::Parse($match.Groups[6].Value, [Globalization.CultureInfo]::InvariantCulture)
  } catch { throw "runtime_state_invalid" }
  $expected = @{
    $script:EntitlementScript = $match.Groups[3].Value
    $script:PublicKeyPath = $match.Groups[4].Value
    $script:PythonPath = $match.Groups[5].Value
    $script:VendorManifestPath = $match.Groups[7].Value
    $script:VerifierPath = $match.Groups[8].Value
  }
  foreach ($path in $expected.Keys) {
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -cne [string]$expected[$path]) {
      if ([string]$path -ceq $script:PublicKeyPath) { throw "runtime_public_key_mismatch" }
      throw "runtime_component_mismatch"
    }
  }
}

function Assert-VendorSet {
  Assert-ProtectedItem -Path $script:VendorRoot -Kind Directory
  Assert-ProtectedItem -Path $script:VendorManifestPath -Kind File
  $manifestItem = Get-Item -LiteralPath $script:VendorManifestPath -Force
  if ($manifestItem.Length -le 0 -or $manifestItem.Length -gt $script:MaxManifestBytes) {
    throw "vendor_manifest_size_invalid"
  }
  $bytes = Read-BoundedBytes $script:VendorManifestPath $script:MaxManifestBytes `
    "vendor_manifest_size_invalid"
  $text = Get-StrictAscii -Bytes $bytes -ErrorCode "vendor_manifest_encoding_invalid"
  if (-not $text.EndsWith("`n") -or $text.Contains("`r")) {
    throw "vendor_manifest_not_canonical"
  }
  $entries = @{}
  $orderedPaths = New-Object Collections.Generic.List[string]
  foreach ($line in @($text.Substring(0, $text.Length - 1).Split("`n"))) {
    if ($line -notmatch '^([0-9a-f]{64})\t([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)$') {
      throw "vendor_manifest_invalid"
    }
    $relative = $Matches[2]
    if ($relative.Split('/') -contains ".." -or $relative.Split('/') -contains "." -or
        $entries.ContainsKey($relative)) {
      throw "vendor_manifest_invalid"
    }
    $entries[$relative] = $Matches[1]
    [void]$orderedPaths.Add($relative)
  }
  if ($entries.Count -le 0 -or $entries.Count -gt $script:MaxVendorFiles) {
    throw "vendor_manifest_count_invalid"
  }
  [string[]]$sortedPaths = @($orderedPaths)
  [Array]::Sort($sortedPaths, [StringComparer]::Ordinal)
  if (($orderedPaths -join "`n") -cne ($sortedPaths -join "`n")) {
    throw "vendor_manifest_not_canonical"
  }

  $actualFiles = @{}
  $actualDirectories = @{}
  [long]$totalBytes = 0
  foreach ($item in @(Get-SafeTreeItems -Root $script:VendorRoot)) {
    $kind = if ($item.PSIsContainer) { "Directory" } else { "File" }
    Assert-ProtectedItem -Path $item.FullName -Kind $kind
    $relative = $item.FullName.Substring($script:VendorRoot.Length + 1).Replace('\', '/')
    if ($item.PSIsContainer) {
      $actualDirectories[$relative] = $true
      continue
    }
    $totalBytes += $item.Length
    if ($totalBytes -gt $script:MaxVendorBytes) { throw "vendor_size_limit_exceeded" }
    $actualFiles[$relative] = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
  }
  if ($actualFiles.Count -ne $entries.Count) { throw "vendor_file_set_invalid" }
  $expectedDirectories = @{}
  foreach ($relative in $entries.Keys) {
    if (-not $actualFiles.ContainsKey($relative) -or $actualFiles[$relative] -cne $entries[$relative]) {
      throw "vendor_file_hash_invalid"
    }
    $parts = $relative.Split('/')
    for ($index = 1; $index -lt $parts.Length; $index++) {
      $expectedDirectories[($parts[0..($index - 1)] -join '/')] = $true
    }
  }
  if ($actualDirectories.Count -ne $expectedDirectories.Count) { throw "vendor_file_set_invalid" }
  foreach ($relative in $actualDirectories.Keys) {
    if (-not $expectedDirectories.ContainsKey($relative)) { throw "vendor_file_set_invalid" }
  }
}

function Assert-PinnedRuntime {
  Assert-ProtectedItem -Path "C:\ProgramData\Ruisheng" -Kind Directory
  Assert-ProtectedItem -Path "C:\ProgramData\Ruisheng\runtime" -Kind Directory
  Assert-ProtectedItem -Path "C:\ProgramData\Ruisheng\entitlement-runtime" -Kind Directory
  Assert-ProtectedItem -Path "C:\ProgramData\Ruisheng\bin" -Kind Directory
  Assert-ProtectedItem -Path "C:\ProgramData\Ruisheng\trust" -Kind Directory
  if ((Test-Path -LiteralPath $script:RuntimeStatePath -PathType Leaf) -and
      -not (Test-Path -LiteralPath $script:SiteIdentityPath -PathType Leaf)) {
    throw "site_identity_missing"
  }
  Assert-ProtectedItem -Path $script:PythonPath -Kind File
  Assert-ProtectedItem -Path $script:EntitlementScript -Kind File
  Assert-ProtectedItem -Path $script:PublicKeyPath -Kind File
  Assert-ProtectedItem -Path $script:SiteIdentityPath -Kind File
  Assert-RuntimeState
  Assert-VendorSet
}

function Get-PinnedSiteIdentity {
  $item = Get-Item -LiteralPath $script:SiteIdentityPath -Force
  if ($item.Length -le 0 -or $item.Length -gt 256) { throw "site_identity_invalid" }
  $bytes = Read-BoundedBytes $script:SiteIdentityPath 256 "site_identity_invalid"
  $text = Get-StrictAscii -Bytes $bytes -ErrorCode "site_identity_invalid"
  if ($text -notmatch '^([A-Za-z0-9][A-Za-z0-9._:-]{0,127})\n$') {
    throw "site_identity_invalid"
  }
  return [string]$Matches[1]
}

function Assert-PinnedSiteIdentity {
  $script:PinnedSiteId = Get-PinnedSiteIdentity
  if ($script:PinnedSiteId -cne $SiteId) { throw "site_mismatch" }
}

function ConvertTo-NativeArgument([AllowEmptyString()][string]$Value) {
  if ($Value -notmatch '[\s"]') { return $Value }
  $builder = New-Object Text.StringBuilder
  [void]$builder.Append('"')
  $slashes = 0
  foreach ($character in $Value.ToCharArray()) {
    if ($character -eq '\') { $slashes++; continue }
    if ($character -eq '"') {
      [void]$builder.Append(('\' * (($slashes * 2) + 1)))
      [void]$builder.Append('"')
      $slashes = 0
      continue
    }
    if ($slashes) { [void]$builder.Append(('\' * $slashes)); $slashes = 0 }
    [void]$builder.Append($character)
  }
  if ($slashes) { [void]$builder.Append(('\' * ($slashes * 2))) }
  [void]$builder.Append('"')
  return $builder.ToString()
}

function Invoke-FixedPython([string[]]$Arguments) {
  $bootstrap = "import runpy,sys;sys.path.insert(0,r'C:\ProgramData\Ruisheng\entitlement-runtime\vendor');runpy.run_path(r'C:\ProgramData\Ruisheng\bin\entitlement.py',run_name='__main__')"
  $nativeArguments = @("-I", "-S", "-B", "-c", $bootstrap) + $Arguments
  $startInfo = New-Object Diagnostics.ProcessStartInfo
  $startInfo.FileName = $script:PythonPath
  $startInfo.Arguments = (@($nativeArguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join " ")
  $startInfo.UseShellExecute = $false
  $startInfo.CreateNoWindow = $true
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true
  $utf8 = New-Object Text.UTF8Encoding($false, $true)
  $startInfo.StandardOutputEncoding = $utf8
  $startInfo.StandardErrorEncoding = $utf8
  $process = New-Object Diagnostics.Process
  $process.StartInfo = $startInfo
  $started = $false
  $stdoutMemory = New-Object IO.MemoryStream
  $stderrMemory = New-Object IO.MemoryStream
  try {
    $started = $process.Start()
    if (-not $started) { throw "verifier_start_failed" }
    $stdoutBuffer = New-Object byte[] 4096
    $stderrBuffer = New-Object byte[] 4096
    $stdoutTask = $process.StandardOutput.BaseStream.ReadAsync($stdoutBuffer, 0, $stdoutBuffer.Length)
    $stderrTask = $process.StandardError.BaseStream.ReadAsync($stderrBuffer, 0, $stderrBuffer.Length)
    $stdoutClosed = $false
    $stderrClosed = $false
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($script:ToolTimeoutSeconds)
    while (-not ($process.HasExited -and $stdoutClosed -and $stderrClosed)) {
      if (-not $stdoutClosed -and $stdoutTask.IsCompleted) {
        $count = $stdoutTask.GetAwaiter().GetResult()
        if ($count -eq 0) {
          $stdoutClosed = $true
        }
        else {
          $stdoutMemory.Write($stdoutBuffer, 0, $count)
          if ($stdoutMemory.Length -gt $script:MaxOutputCharacters) { throw "verifier_output_exceeded" }
          $stdoutTask = $process.StandardOutput.BaseStream.ReadAsync($stdoutBuffer, 0, $stdoutBuffer.Length)
        }
      }
      if (-not $stderrClosed -and $stderrTask.IsCompleted) {
        $count = $stderrTask.GetAwaiter().GetResult()
        if ($count -eq 0) {
          $stderrClosed = $true
        }
        else {
          $stderrMemory.Write($stderrBuffer, 0, $count)
          if ($stderrMemory.Length -gt $script:MaxOutputCharacters) { throw "verifier_output_exceeded" }
          $stderrTask = $process.StandardError.BaseStream.ReadAsync($stderrBuffer, 0, $stderrBuffer.Length)
        }
      }
      if ([DateTimeOffset]::UtcNow -ge $deadline) { throw "verifier_timeout" }
      Start-Sleep -Milliseconds 10
    }
    $process.WaitForExit()
    $stdout = $utf8.GetString($stdoutMemory.ToArray())
    $stderr = $utf8.GetString($stderrMemory.ToArray())
    if (-not [string]::IsNullOrWhiteSpace($stderr)) { throw "verifier_stderr_invalid" }
    return [pscustomobject]@{ ExitCode = $process.ExitCode; Output = $stdout.Trim() }
  }
  finally {
    if ($started -and -not $process.HasExited) {
      try { $process.Kill() } catch { }
    }
    $process.Dispose()
    $stdoutMemory.Dispose()
    $stderrMemory.Dispose()
  }
}

function ConvertFrom-ToolResult($NativeResult) {
  try { $result = $NativeResult.Output | ConvertFrom-Json }
  catch { throw "verifier_output_invalid" }
  if ($null -eq $result) { throw "verifier_output_invalid" }
  if ($NativeResult.ExitCode -ne 0) {
    if ($NativeResult.ExitCode -eq 2 -and
        [string]$result.status -in @("rejected", "uncertain") -and
        [string]$result.error_code -match '^[A-Za-z0-9_]+$') {
      $script:ToolRejected = [string]$result.status -ceq "rejected"
      throw ([string]$result.error_code)
    }
    throw "entitlement_verification_failed"
  }
  return $result
}

function Assert-ExactFields($Value, [string[]]$Expected) {
  [string[]]$actual = @($Value.PSObject.Properties.Name)
  [string[]]$wanted = @($Expected)
  [Array]::Sort($actual, [StringComparer]::Ordinal)
  [Array]::Sort($wanted, [StringComparer]::Ordinal)
  if (($actual -join "`n") -cne ($wanted -join "`n")) { throw "verifier_receipt_fields_invalid" }
}

function Assert-InstallReceipt($Result) {
  $fields = @(
    "schema_version", "ok", "status", "idempotent", "operation_id", "site_id",
    "grant_id", "grant_sha256", "serial", "starts_at", "expires_at", "grace_until",
    "safety_preserved", "collection_preserved", "alarms_preserved", "data_preserved"
  )
  Assert-ExactFields -Value $Result -Expected $fields
  if ($Result.schema_version -ne 1 -or $Result.ok -isnot [bool] -or -not $Result.ok -or
      [string]$Result.status -cne "installed" -or $Result.idempotent -isnot [bool] -or
      [string]$Result.operation_id -cne $OperationId -or [string]$Result.site_id -cne $SiteId -or
      [string]$Result.grant_id -notmatch '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' -or
      [string]$Result.grant_sha256 -notmatch '^[0-9a-f]{64}$' -or
      $Result.serial -isnot [ValueType] -or [long]$Result.serial -le 0) {
    throw "verifier_receipt_invalid"
  }
  foreach ($field in @("starts_at", "expires_at", "grace_until")) {
    if ([string]$Result.$field -notmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$') {
      throw "verifier_receipt_invalid"
    }
  }
  foreach ($field in @("safety_preserved", "collection_preserved", "alarms_preserved", "data_preserved")) {
    if ($Result.$field -isnot [bool] -or -not $Result.$field) { throw "verifier_receipt_invalid" }
  }
}

function Assert-StatusReceipt($Result) {
  $base = @(
    "schema_version", "ok", "status", "site_id", "entitlement_dependent",
    "features", "safety_preserved", "collection_preserved", "alarms_preserved", "data_preserved"
  )
  $withGrant = $base + @("grant_id", "grant_sha256", "serial", "starts_at", "expires_at", "grace_until")
  $status = [string]$Result.status
  $hasGrant = $status -in @("pending", "active", "grace", "expired")
  $expected = if ($hasGrant) { $withGrant } else { $base }
  Assert-ExactFields -Value $Result -Expected $expected
  if ($Result.schema_version -ne 1 -or $Result.ok -isnot [bool] -or
      [string]$Result.site_id -cne $SiteId -or
      $status -notin @("missing", "uncertain", "pending", "active", "grace", "expired") -or
      $Result.ok -ne ($status -ne "uncertain") -or $Result.entitlement_dependent -isnot [bool] -or
      $Result.entitlement_dependent -ne ($status -in @("grace", "expired"))) {
    throw "verifier_receipt_invalid"
  }
  if ($hasGrant -and (
      [string]$Result.grant_id -notmatch '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' -or
      [string]$Result.grant_sha256 -notmatch '^[0-9a-f]{64}$' -or
      $Result.serial -isnot [ValueType] -or [long]$Result.serial -le 0)) {
    throw "verifier_receipt_invalid"
  }
  if ($Result.features -isnot [Array] -or
      @($Result.features | Where-Object {
        $_ -isnot [string] -or [string]$_ -notmatch '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
      }).Count -ne 0 -or
      (@($Result.features) -join "`n") -cne
        ((Get-OrdinalSortedStrings @($Result.features)) -join "`n")) {
    throw "verifier_receipt_invalid"
  }
  foreach ($field in @("safety_preserved", "collection_preserved", "alarms_preserved", "data_preserved")) {
    if ($Result.$field -isnot [bool] -or -not $Result.$field) { throw "verifier_receipt_invalid" }
  }
}

function Get-VerifiedEntitlementStatus {
  Repair-InheritedTransactionAcls
  if (Test-Path -LiteralPath $script:EntitlementRoot) {
    Assert-ProtectedTree -Root $script:EntitlementRoot
  }
  try {
    $native = Invoke-FixedPython @(
      "status", "--state", $script:StatePath, "--public-key", $script:PublicKeyPath,
      "--site-id", $SiteId, "--time-state", $script:TimeStatePath
    )
  } finally {
    if (Test-Path -LiteralPath $script:TimeStatePath -PathType Leaf) {
      Set-ProtectedAcl -Path $script:TimeStatePath -Kind File
    }
  }
  $result = ConvertFrom-ToolResult $native
  Assert-StatusReceipt $result
  return $result
}

$output = $null
$exitCode = 0
$runtimeUseLock = $null
$incomingLock = $null
try {
  if ($SiteId -notmatch '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$') { throw "site_id_invalid" }
  if ($Action -in @("Prepare", "Install", "Cleanup")) {
    $OperationId = $OperationId.ToLowerInvariant()
    if ($OperationId -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') {
      throw "operation_id_invalid"
    }
    if ($GrantSha256 -notmatch '^[0-9a-f]{64}$') { throw "grant_sha256_invalid" }
    if ($ReasonSha256 -notmatch '^[0-9a-f]{64}$') { throw "reason_sha256_invalid" }
    $incomingPaths = Get-GrantIncomingPaths
    $script:ReservationPath = $incomingPaths.reservation
  }
  if ($Action -eq "Install") {
    if ($Reason.Length -lt 8 -or $Reason.Length -gt 200 -or $Reason -match '[\x00-\x1F\x7F]') {
      throw "reason_invalid"
    }
    if ((Get-TextSha256 $Reason) -cne $ReasonSha256) { throw "reason_sha256_mismatch" }
    $expectedGrantPath = $incomingPaths.grant
    if (-not [string]::Equals($GrantPath, $expectedGrantPath, [StringComparison]::Ordinal)) {
      throw "grant_path_invalid"
    }
    $script:CleanupPath = $expectedGrantPath
  }
  if ($Action -ne "ValidateRuntime") {
    $runtimeUseLock = Enter-RuntimeUseLock
  }

  if ($Action -in @("Status", "Authorize", "Install") -and
      (Test-Path -LiteralPath $script:BootstrapJournalPath)) {
    try { Assert-ProtectedItem -Path $script:BootstrapJournalPath -Kind File }
    catch { throw "bootstrap_transaction_uncertain" }
    if ($Action -in @("Status", "Authorize")) {
      try { [void](Get-VerifiedEntitlementStatus) } catch { }
    }
    throw "bootstrap_transaction_uncertain"
  }
  Assert-PinnedRuntime
  $script:PinnedSiteId = Get-PinnedSiteIdentity
  if ($script:PinnedSiteId -cne $SiteId -and
      $Action -notin @("Prepare", "Install", "Cleanup")) {
      throw "site_mismatch"
  }
  if ($Action -eq "Prepare") {
    Ensure-ProtectedDirectory -Path $script:EntitlementRoot
    Ensure-ProtectedDirectory -Path $script:IncomingRoot
    $incomingLock = Enter-IncomingLock
  }
  elseif ($Action -in @("Install", "Cleanup")) {
    Assert-ProtectedItem -Path $script:EntitlementRoot -Kind Directory
    Assert-ProtectedItem -Path $script:IncomingRoot -Kind Directory
    $incomingLock = Enter-IncomingLock
  }
  if ($Action -eq "Authorize" -and
      $Feature -notin @("remote-support", "software-updates", "security-patches")) {
    throw "entitlement_feature_invalid"
  }
  if ($Action -eq "ValidateRuntime") {
    Assert-PinnedSiteIdentity
    if (Test-Path -LiteralPath $script:EntitlementRoot) {
      Assert-ProtectedTree -Root $script:EntitlementRoot
    }
    if (Test-Path -LiteralPath $script:StatePath) {
      $native = Invoke-FixedPython @(
        "verify", "--grant", $script:StatePath, "--public-key", $script:PublicKeyPath,
        "--site-id", $SiteId
      )
      $verified = ConvertFrom-ToolResult $native
      if ([string]$verified.status -cne "verified" -or
          [string]$verified.grant_sha256 -notmatch '^[0-9a-f]{64}$') {
        throw "runtime_grant_validation_failed"
      }
    }
    $output = [ordered]@{
      schema_version = 1
      ok = $true
      status = "runtime_validated"
      site_id = $SiteId
    }
  }
  elseif ($Action -eq "Prepare") {
    Prepare-GrantReservation
    $output = [ordered]@{
      schema_version = 1
      ok = $true
      status = "prepared"
      operation_id = $OperationId
      site_id = $SiteId
      incoming_path = $incomingPaths.grant
    }
  }
  elseif ($Action -eq "Cleanup") {
    Assert-GrantReservation
    $cleanupPath = $incomingPaths.grant
    $removed = $false
    if (Test-Path -LiteralPath $cleanupPath) {
      $cleanupItem = Get-Item -LiteralPath $cleanupPath -Force
      if ($cleanupItem.PSIsContainer -or
          ($cleanupItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "cleanup_path_invalid"
      }
      Remove-Item -LiteralPath $cleanupPath -Force
      if (Test-Path -LiteralPath $cleanupPath) { throw "cleanup_failed" }
      $removed = $true
    }
    $output = [ordered]@{
      schema_version = 1
      ok = $true
      status = "cleaned"
      operation_id = $OperationId
      site_id = $SiteId
      incoming_path = $cleanupPath
      removed = $removed
    }
  }
  elseif ($Action -eq "Status") {
    $output = Get-VerifiedEntitlementStatus
  }
  elseif ($Action -eq "Authorize") {
    $statusResult = Get-VerifiedEntitlementStatus
    if ([string]$statusResult.status -notin @("active", "grace") -or
        @($statusResult.features) -cnotcontains $Feature) {
      throw "entitlement_feature_denied"
    }
    $output = [ordered]@{
      schema_version = 1
      ok = $true
      status = "authorized"
      site_id = $SiteId
      feature = $Feature
      entitlement_status = [string]$statusResult.status
      grant_id = [string]$statusResult.grant_id
      grant_sha256 = [string]$statusResult.grant_sha256
      serial = [long]$statusResult.serial
      valid_until = [string]$statusResult.grace_until
    }
  }
  else {
    Assert-GrantReservation
    if (-not (Test-Path -LiteralPath $script:CleanupPath -PathType Leaf)) { throw "grant_missing" }
    $grantItem = Get-Item -LiteralPath $script:CleanupPath -Force
    if (($grantItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "path_reparse_point" }
    if ($grantItem.Length -le 0 -or $grantItem.Length -gt $script:MaxGrantBytes) { throw "grant_size_invalid" }
    if ((Get-FileHash -LiteralPath $script:CleanupPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne
        $GrantSha256) {
      throw "grant_sha256_mismatch"
    }
    Set-ProtectedAcl -Path $script:CleanupPath -Kind File
    Repair-InheritedTransactionAcls
    Assert-ProtectedTree -Root $script:EntitlementRoot
    try {
      $script:MutationDispatched = $true
      $native = Invoke-FixedPython @(
        "install", "--grant", $script:CleanupPath, "--public-key", $script:PublicKeyPath,
        "--state", $script:StatePath, "--audit", $script:AuditPath,
        "--site-id", $script:PinnedSiteId,
        "--claimed-site-id", $SiteId,
        "--operation-id", $OperationId, "--reason", $Reason, "--actor", "remote-operator",
        "--time-state", $script:TimeStatePath
      )
    }
    finally {
      Set-ProtectedTree -Root $script:EntitlementRoot
    }
    $output = ConvertFrom-ToolResult $native
    Assert-InstallReceipt $output
  }
}
catch {
  $code = Get-SafeCode ([string]$_.Exception.Message)
  $status = if ($code -eq "bootstrap_transaction_uncertain") {
    "uncertain"
  }
  elseif ($script:MutationDispatched -and
      (-not $script:ToolRejected -or $code -eq "transaction_uncertain")) {
    "uncertain"
  }
  else {
    "rejected"
  }
  $output = New-Failure -Code $code -Status $status
  $exitCode = 2
}
finally {
  if (-not [string]::IsNullOrEmpty($script:CleanupPath) -and
      (Test-Path -LiteralPath $script:CleanupPath)) {
    try {
      Remove-Item -LiteralPath $script:CleanupPath -Force
      if (Test-Path -LiteralPath $script:CleanupPath) { throw "incoming_cleanup_failed" }
    }
    catch {
      $cleanupStatus = if ($script:MutationDispatched) { "uncertain" } else { "rejected" }
      $output = New-Failure -Code "incoming_cleanup_failed" -Status $cleanupStatus
      $exitCode = 2
    }
  }
  Exit-IncomingLock $incomingLock
  Exit-RuntimeUseLock $runtimeUseLock
}

$output | ConvertTo-Json -Depth 8 -Compress
exit $exitCode
