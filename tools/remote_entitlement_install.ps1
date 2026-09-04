[CmdletBinding()]
param(
  [ValidateSet("Install", "Status")]
  [string]$Action = "Status",
  [Parameter(Mandatory)][string]$Target,
  [string]$GrantPath = "",
  [Parameter(Mandatory)][string]$SiteId,
  [string]$OperationId = "",
  [string]$Reason = "",
  [switch]$Approved,
  [switch]$DryRun,
  [ValidateRange(1, 300)][int]$TransportTimeoutSeconds = 60,
  [string]$AuditDirectory = "$env:LOCALAPPDATA\Ruisheng\audit",
  [string]$LocalPythonPath = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$script:RemoteVerifierPath = "C:\ProgramData\Ruisheng\bin\target_entitlement_verifier.ps1"
$script:RemoteEntitlementRoot = "C:\ProgramData\Ruisheng\entitlements"
$script:RemoteIncomingRoot = "C:\ProgramData\Ruisheng\entitlements\incoming"
$script:RemoteStatePath = "C:\ProgramData\Ruisheng\entitlements\current.json"
$script:SshPath = "C:\Windows\System32\OpenSSH\ssh.exe"
$script:ScpPath = "C:\Windows\System32\OpenSSH\scp.exe"
$script:PythonPath = ""
$script:DefaultPythonPath = Join-Path (Split-Path -Parent $PSScriptRoot) ".venv\Scripts\python.exe"
$script:LocalVendorRoot = "C:\ProgramData\Ruisheng\entitlement-build\vendor"
$script:LocalVendorManifestPath = "C:\ProgramData\Ruisheng\entitlement-build\vendor-manifest.sha256"
$script:LocalBuildRoot = "C:\ProgramData\Ruisheng\entitlement-build"
$script:SnapshotRoot = "$env:LOCALAPPDATA\Ruisheng\entitlement-snapshots"
$script:MaxGrantBytes = 1MB
$script:MaxOutputBytes = 65536
$script:MaxVendorBytes = 128MB
$script:MaxVendorFiles = 4096
$script:MaxVendorTreeItems = 8192
$script:MaxVendorManifestBytes = 1MB
$script:InstallDispatched = $false
$script:GrantIdentity = $null
$script:ReasonSha256 = ""
$script:AuditWritten = $false
$script:ExplicitRejection = $false
$script:SshTarget = ""
$script:ScpTarget = ""
$script:SnapshotHandle = $null
$script:SnapshotDirectory = ""
$script:Phase = "validation"

function Get-SafeCode([string]$Message) {
  if ($Message -match '^[A-Za-z0-9_]+$') { return $Message }
  return "remote_entitlement_failed"
}

function Get-TextSha256([string]$Value) {
  $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
  $hash = [Security.Cryptography.SHA256]::Create()
  try { return ([BitConverter]::ToString($hash.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant() }
  finally { $hash.Dispose() }
}

function Get-OrdinalSortedStrings([string[]]$Values) {
  [string[]]$copy = @($Values)
  [Array]::Sort($copy, [StringComparer]::Ordinal)
  return $copy
}

function Assert-TailscaleTarget {
  $separator = $Target.IndexOf('@')
  if ($separator -le 0 -or $separator -ge ($Target.Length - 1)) { throw "target_invalid" }
  $userText = $Target.Substring(0, $separator)
  $hostText = $Target.Substring($separator + 1)
  if ($hostText.StartsWith('[') -and $hostText.EndsWith(']')) {
    $hostText = $hostText.Substring(1, $hostText.Length - 2)
  }
  if ($hostText -match '^[A-Za-z0-9][A-Za-z0-9.-]*\.ts\.net$') {
    $script:SshTarget = "$userText@$hostText"
    $script:ScpTarget = $script:SshTarget
    return
  }
  $address = $null
  if (-not [Net.IPAddress]::TryParse($hostText, [ref]$address)) {
    throw "target_not_tailscale"
  }
  $bytes = $address.GetAddressBytes()
  if ($address.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork -and
      $bytes[0] -eq 100 -and $bytes[1] -ge 64 -and $bytes[1] -le 127) {
    $script:SshTarget = "$userText@$hostText"
    $script:ScpTarget = $script:SshTarget
    return
  }
  if ($address.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetworkV6 -and
      $bytes.Length -eq 16 -and $bytes[0] -eq 0xfd -and $bytes[1] -eq 0x7a -and
      $bytes[2] -eq 0x11 -and $bytes[3] -eq 0x5c -and $bytes[4] -eq 0xa1 -and
      $bytes[5] -eq 0xe0) {
    $script:SshTarget = "$userText@$hostText"
    $script:ScpTarget = "$userText@[$hostText]"
    return
  }
  throw "target_not_tailscale"
}

function Assert-FixedExecutable {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][ValidateSet("Microsoft", "PythonSoftwareFoundation")][string]$Publisher
  )
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "fixed_executable_missing" }
  $item = Get-Item -LiteralPath $Path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "fixed_executable_invalid"
  }
  $expectedName = if ($Publisher -eq "Microsoft") { @("ssh.exe", "scp.exe") } else { @("python.exe") }
  if ($expectedName -cnotcontains $item.Name) { throw "fixed_executable_name_invalid" }
  try {
    $signature = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $item.FullName
  } catch { throw "fixed_executable_signature_invalid" }
  if ([string]$signature.Status -cne "Valid" -or $null -eq $signature.SignerCertificate) {
    throw "fixed_executable_signature_invalid"
  }
  $organization = if ($Publisher -eq "Microsoft") {
    "Microsoft Corporation"
  } else { "Python Software Foundation" }
  $escaped = [regex]::Escape($organization)
  if ([string]$signature.SignerCertificate.Subject -notmatch "(?:^|,\s*)O=$escaped(?:,|$)") {
    throw "fixed_executable_publisher_invalid"
  }
}

function Get-NormalizedLocalPath([string]$Path, [string]$ErrorCode) {
  if ($Path -notmatch '^[A-Za-z]:\\') { throw $ErrorCode }
  try { return [IO.Path]::GetFullPath($Path).TrimEnd('\') }
  catch { throw $ErrorCode }
}

function Set-LocalProtectedAcl([string]$Path, [ValidateSet("File", "Directory")][string]$Kind) {
  $security = if ($Kind -eq "Directory") {
    New-Object Security.AccessControl.DirectorySecurity
  } else { New-Object Security.AccessControl.FileSecurity }
  $security.SetAccessRuleProtection($true, $false)
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
  foreach ($sid in @(
      $identity,
      (New-Object Security.Principal.SecurityIdentifier("S-1-5-18")),
      (New-Object Security.Principal.SecurityIdentifier("S-1-5-32-544"))
    )) {
    $inheritance = if ($Kind -eq "Directory") {
      [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    } else { [Security.AccessControl.InheritanceFlags]::None }
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
      $sid, [Security.AccessControl.FileSystemRights]::FullControl, $inheritance,
      [Security.AccessControl.PropagationFlags]::None,
      [Security.AccessControl.AccessControlType]::Allow
    )
    [void]$security.AddAccessRule($rule)
  }
  Set-Acl -LiteralPath $Path -AclObject $security
}

function Get-LocalPrincipalSid($Identity) {
  try {
    if ($Identity -is [Security.Principal.IdentityReference]) {
      return $Identity.Translate([Security.Principal.SecurityIdentifier]).Value
    }
    return (New-Object Security.Principal.NTAccount([string]$Identity)).Translate(
      [Security.Principal.SecurityIdentifier]
    ).Value
  } catch { throw "local_acl_invalid" }
}

function Assert-LocalProtectedAcl {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][ValidateSet("File", "Directory")][string]$Kind,
    [switch]$RequireProtected
  )
  $pathType = if ($Kind -eq "File") { "Leaf" } else { "Container" }
  if (-not (Test-Path -LiteralPath $Path -PathType $pathType)) { throw "local_acl_invalid" }
  $item = Get-Item -LiteralPath $Path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "local_acl_invalid"
  }
  try { $acl = Get-Acl -LiteralPath $Path } catch { throw "local_acl_invalid" }
  $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  $allowed = @{
    $currentSid = $true
    "S-1-5-18" = $true
    "S-1-5-32-544" = $true
  }
  if (-not $allowed.ContainsKey((Get-LocalPrincipalSid $acl.Owner))) { throw "local_acl_invalid" }
  if ($RequireProtected -and -not $acl.AreAccessRulesProtected) { throw "local_acl_invalid" }
  $rules = @($acl.Access)
  if ($rules.Count -lt 2 -or $rules.Count -gt $allowed.Count) { throw "local_acl_invalid" }
  $seen = @{}
  foreach ($rule in $rules) {
    $sid = Get-LocalPrincipalSid $rule.IdentityReference
    $inheritance = if ($Kind -eq "Directory") {
      [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    } else { [Security.AccessControl.InheritanceFlags]::None }
    if (-not $allowed.ContainsKey($sid) -or $seen.ContainsKey($sid) -or
        $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
        $rule.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl -or
        $rule.InheritanceFlags -ne $inheritance -or
        $rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None -or
        ($RequireProtected -and $rule.IsInherited)) {
      throw "local_acl_invalid"
    }
    $seen[$sid] = $true
  }
  if (-not $seen.ContainsKey("S-1-5-18") -or
      (-not $seen.ContainsKey($currentSid) -and -not $seen.ContainsKey("S-1-5-32-544"))) {
    throw "local_acl_invalid"
  }
}

function Get-SafeLocalTreeItems([string]$Root) {
  $items = New-Object 'Collections.Generic.List[IO.FileSystemInfo]'
  $pending = New-Object 'Collections.Generic.Queue[string]'
  $pending.Enqueue($Root)
  while ($pending.Count -gt 0) {
    $directory = $pending.Dequeue()
    foreach ($item in @(Get-ChildItem -LiteralPath $directory -Force)) {
      if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "local_vendor_reparse_point"
      }
      [void]$items.Add($item)
      if ($items.Count -gt $script:MaxVendorTreeItems) { throw "local_vendor_tree_limit" }
      if ($item.PSIsContainer) { $pending.Enqueue($item.FullName) }
    }
  }
  return $items
}

function Get-StrictLocalAscii([byte[]]$Bytes, [string]$ErrorCode) {
  try {
    $encoding = [Text.Encoding]::GetEncoding(
      20127, [Text.EncoderFallback]::ExceptionFallback, [Text.DecoderFallback]::ExceptionFallback
    )
    return $encoding.GetString($Bytes)
  } catch { throw $ErrorCode }
}

function Read-LocalBoundedBytes {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][long]$Maximum,
    [Parameter(Mandatory)][string]$ErrorCode
  )
  $stream = $null
  $memory = $null
  try {
    $stream = [IO.File]::Open(
      $Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
    )
    $memory = New-Object IO.MemoryStream
    $buffer = New-Object byte[] 4096
    while (($count = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
      if ($memory.Length + $count -gt $Maximum) { throw $ErrorCode }
      $memory.Write($buffer, 0, $count)
    }
    return $memory.ToArray()
  } catch {
    if ($_.Exception.Message -ceq $ErrorCode) { throw }
    throw $ErrorCode
  } finally {
    if ($null -ne $memory) { $memory.Dispose() }
    if ($null -ne $stream) { $stream.Dispose() }
  }
}

function Assert-LocalVendorSet {
  Assert-LocalProtectedAcl -Path $script:LocalBuildRoot -Kind Directory -RequireProtected
  Assert-LocalProtectedAcl -Path $script:LocalVendorRoot -Kind Directory
  Assert-LocalProtectedAcl -Path $script:LocalVendorManifestPath -Kind File
  $manifest = Get-Item -LiteralPath $script:LocalVendorManifestPath -Force
  if ($manifest.Length -le 0 -or $manifest.Length -gt $script:MaxVendorManifestBytes) {
    throw "local_vendor_manifest_invalid"
  }
  $text = Get-StrictLocalAscii (
    Read-LocalBoundedBytes $manifest.FullName $script:MaxVendorManifestBytes `
      "local_vendor_manifest_invalid"
  ) "local_vendor_manifest_invalid"
  if (-not $text.EndsWith("`n") -or $text.Contains("`r")) { throw "local_vendor_manifest_invalid" }
  $entries = @{}
  $orderedPaths = New-Object Collections.Generic.List[string]
  foreach ($line in @($text.Substring(0, $text.Length - 1).Split("`n"))) {
    if ($line -notmatch '^([0-9a-f]{64})\t([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)$' -or
        $entries.ContainsKey($Matches[2])) { throw "local_vendor_manifest_invalid" }
    $entries[$Matches[2]] = $Matches[1]
    [void]$orderedPaths.Add($Matches[2])
  }
  [string[]]$sortedPaths = @($orderedPaths)
  [Array]::Sort($sortedPaths, [StringComparer]::Ordinal)
  if ($entries.Count -le 0 -or $entries.Count -gt $script:MaxVendorFiles -or
      ($orderedPaths -join "`n") -cne ($sortedPaths -join "`n")) {
    throw "local_vendor_manifest_invalid"
  }
  $actualFiles = @{}
  $actualDirectories = @{}
  [long]$totalBytes = 0
  foreach ($item in @(Get-SafeLocalTreeItems $script:LocalVendorRoot)) {
    $kind = if ($item.PSIsContainer) { "Directory" } else { "File" }
    Assert-LocalProtectedAcl -Path $item.FullName -Kind $kind
    $relative = $item.FullName.Substring($script:LocalVendorRoot.Length + 1).Replace('\', '/')
    if ($item.PSIsContainer) {
      $actualDirectories[$relative] = $true
      continue
    }
    $totalBytes += $item.Length
    if ($totalBytes -gt $script:MaxVendorBytes) { throw "local_vendor_size_limit" }
    $actualFiles[$relative] = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
  }
  if ($actualFiles.Count -ne $entries.Count) { throw "local_vendor_file_set_invalid" }
  $expectedDirectories = @{}
  foreach ($relative in $entries.Keys) {
    if (-not $actualFiles.ContainsKey($relative) -or $actualFiles[$relative] -cne $entries[$relative]) {
      throw "local_vendor_hash_invalid"
    }
    $parts = $relative.Split('/')
    for ($index = 1; $index -lt $parts.Length; $index++) {
      $expectedDirectories[($parts[0..($index - 1)] -join '/')] = $true
    }
  }
  if ($actualDirectories.Count -ne $expectedDirectories.Count) { throw "local_vendor_file_set_invalid" }
  foreach ($relative in $actualDirectories.Keys) {
    if (-not $expectedDirectories.ContainsKey($relative)) { throw "local_vendor_file_set_invalid" }
  }
}

function New-ProtectedGrantSnapshot {
  $stage = "root"
  try {
    $snapshotRoot = Get-NormalizedLocalPath $script:SnapshotRoot "grant_snapshot_root_invalid"
    if (-not (Test-Path -LiteralPath $script:SnapshotRoot)) {
      [void](New-Item -ItemType Directory -Path $script:SnapshotRoot -Force)
    }
    $rootItem = Get-Item -LiteralPath $snapshotRoot -Force
    if (-not $rootItem.PSIsContainer -or
        ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      throw "grant_snapshot_root_invalid"
    }
    Set-LocalProtectedAcl $script:SnapshotRoot "Directory"
    $stage = "directory"
    $script:SnapshotDirectory = Join-Path $snapshotRoot (
      "$OperationId-$([Guid]::NewGuid().ToString('N'))"
    )
    [void](New-Item -ItemType Directory -Path $script:SnapshotDirectory)
    Set-LocalProtectedAcl $script:SnapshotDirectory "Directory"
    $snapshotPath = Join-Path $script:SnapshotDirectory "grant.json"
    $stage = "copy"
    $source = [IO.File]::Open(
      $GrantPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
    )
  } catch {
    if ([string]$_.Exception.Message -match '^[A-Za-z0-9_]+$') { throw }
    throw "grant_snapshot_$($stage)_failed"
  }
  try {
    $script:SnapshotHandle = [IO.File]::Open(
      $snapshotPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::Read
    )
    $buffer = New-Object byte[] 65536
    [long]$total = 0
    while (($count = $source.Read($buffer, 0, $buffer.Length)) -gt 0) {
      $total += $count
      if ($total -gt $script:MaxGrantBytes) { throw "grant_size_invalid" }
      $script:SnapshotHandle.Write($buffer, 0, $count)
    }
    if ($total -le 0) { throw "grant_size_invalid" }
    $script:SnapshotHandle.Flush($true)
    $script:SnapshotHandle.Dispose()
    $script:SnapshotHandle = $null
    Set-LocalProtectedAcl $snapshotPath "File"
    $stage = "reopen"
    $script:SnapshotHandle = [IO.File]::Open(
      $snapshotPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
    )
    return $snapshotPath
  } catch {
    if ([string]$_.Exception.Message -match '^[A-Za-z0-9_]+$') { throw }
    throw "grant_snapshot_$($stage)_failed"
  } finally { $source.Dispose() }
}

function Remove-ProtectedGrantSnapshot {
  if ($null -ne $script:SnapshotHandle) {
    $script:SnapshotHandle.Dispose()
    $script:SnapshotHandle = $null
  }
  if (-not [string]::IsNullOrEmpty($script:SnapshotDirectory)) {
    $root = Get-NormalizedLocalPath $script:SnapshotRoot "grant_snapshot_cleanup_path_invalid"
    $snapshot = Get-NormalizedLocalPath $script:SnapshotDirectory "grant_snapshot_cleanup_path_invalid"
    if ([IO.Path]::GetDirectoryName($snapshot) -cne $root) {
      throw "grant_snapshot_cleanup_path_invalid"
    }
    if (Test-Path -LiteralPath $snapshot) {
      $item = Get-Item -LiteralPath $snapshot -Force
      if (-not $item.PSIsContainer -or
          ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "grant_snapshot_cleanup_path_invalid"
      }
      Remove-Item -LiteralPath $snapshot -Recurse -Force -ErrorAction Stop
      if (Test-Path -LiteralPath $snapshot) { throw "grant_snapshot_cleanup_failed" }
    }
    $script:SnapshotDirectory = ""
  }
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

function Get-StrictUtf8([byte[]]$Bytes, [string]$ErrorCode) {
  if ($null -eq $Bytes -or $Bytes.Length -eq 0) { return "" }
  try {
    $utf8 = New-Object Text.UTF8Encoding($false, $true)
    return $utf8.GetString($Bytes)
  }
  catch {
    throw $ErrorCode
  }
}

function Invoke-BoundedProcess {
  param(
    [Parameter(Mandatory)][string]$FilePath,
    [Parameter(Mandatory)][string[]]$Arguments,
    [ValidateRange(1, 300)][int]$TimeoutSeconds = 60
  )
  $stdoutPath = [IO.Path]::GetTempFileName()
  $stderrPath = [IO.Path]::GetTempFileName()
  $stdoutStream = $null
  $stderrStream = $null
  $process = New-Object Diagnostics.Process
  $started = $false
  try {
    $stdoutStream = New-Object IO.FileStream(
      $stdoutPath, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::Read
    )
    $stderrStream = New-Object IO.FileStream(
      $stderrPath, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::Read
    )
    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = (@($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join " ")
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process.StartInfo = $startInfo
    $started = $process.Start()
    if (-not $started) { throw "subprocess_start_failed" }
    $stdoutCopy = $process.StandardOutput.BaseStream.CopyToAsync($stdoutStream)
    $stderrCopy = $process.StandardError.BaseStream.CopyToAsync($stderrStream)
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while (-not $process.WaitForExit(100)) {
      if ($stdoutStream.Length -gt $script:MaxOutputBytes -or
          $stderrStream.Length -gt $script:MaxOutputBytes) {
        try { $process.Kill() } catch { }
        try { [void]$process.WaitForExit(5000) } catch { }
        throw "subprocess_output_exceeded"
      }
      if ([DateTimeOffset]::UtcNow -ge $deadline) {
        try { $process.Kill() } catch { }
        try { [void]$process.WaitForExit(5000) } catch { }
        throw "subprocess_timeout"
      }
    }
    if (-not [Threading.Tasks.Task]::WaitAll(@($stdoutCopy, $stderrCopy), 10000)) {
      throw "subprocess_stream_timeout"
    }
    $stdoutStream.Flush()
    $stderrStream.Flush()
    if ($stdoutStream.Length -gt $script:MaxOutputBytes -or
        $stderrStream.Length -gt $script:MaxOutputBytes) {
      throw "subprocess_output_exceeded"
    }
    $exitCode = $process.ExitCode
    $stdoutStream.Dispose()
    $stdoutStream = $null
    $stderrStream.Dispose()
    $stderrStream = $null
    $stdoutBytes = [IO.File]::ReadAllBytes($stdoutPath)
    $stderrBytes = [IO.File]::ReadAllBytes($stderrPath)
    return [pscustomobject]@{
      ExitCode = $exitCode
      Stdout = Get-StrictUtf8 -Bytes $stdoutBytes -ErrorCode "subprocess_output_encoding_invalid"
      Stderr = Get-StrictUtf8 -Bytes $stderrBytes -ErrorCode "subprocess_output_encoding_invalid"
    }
  }
  finally {
    if ($started -and -not $process.HasExited) {
      try { $process.Kill() } catch { }
    }
    $process.Dispose()
    if ($null -ne $stdoutStream) { $stdoutStream.Dispose() }
    if ($null -ne $stderrStream) { $stderrStream.Dispose() }
    Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
  }
}

function Assert-ExactFields($Value, [string[]]$Expected, [string]$ErrorCode) {
  [string[]]$actual = @($Value.PSObject.Properties.Name)
  [string[]]$wanted = @($Expected)
  [Array]::Sort($actual, [StringComparer]::Ordinal)
  [Array]::Sort($wanted, [StringComparer]::Ordinal)
  if (($actual -join "`n") -cne ($wanted -join "`n")) { throw $ErrorCode }
}

function ConvertFrom-ExactJson([string]$Text, [string]$ErrorCode) {
  if ([string]::IsNullOrWhiteSpace($Text)) { throw $ErrorCode }
  try { $value = $Text | ConvertFrom-Json }
  catch { throw $ErrorCode }
  if ($null -eq $value -or $value -is [Array]) { throw $ErrorCode }
  return $value
}

function Get-LocalGrantIdentity {
  $scriptPath = Join-Path $PSScriptRoot "entitlement.py"
  if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) { throw "local_inspector_missing" }
  Assert-FixedExecutable $script:PythonPath "PythonSoftwareFoundation"
  Assert-LocalVendorSet
  $vendorPathBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($script:LocalVendorRoot))
  $bootstrap = "import base64,runpy,sys;path=sys.argv.pop(1);sys.path.insert(0,base64.b64decode('$vendorPathBase64').decode('utf-8'));runpy.run_path(path,run_name='__main__')"
  $arguments = @("-I", "-S", "-B", "-c", $bootstrap, $scriptPath, "inspect", "--grant", $GrantPath)
  $native = Invoke-BoundedProcess -FilePath $script:PythonPath -Arguments $arguments -TimeoutSeconds 30
  if ($native.ExitCode -ne 0 -or -not [string]::IsNullOrWhiteSpace($native.Stderr)) {
    throw "grant_inspection_failed"
  }
  $identity = ConvertFrom-ExactJson -Text $native.Stdout.Trim() -ErrorCode "grant_inspection_invalid"
  $fields = @(
    "schema_version", "ok", "status", "site_id", "grant_id", "grant_sha256",
    "serial", "starts_at", "expires_at", "grace_until"
  )
  Assert-ExactFields -Value $identity -Expected $fields -ErrorCode "grant_inspection_fields_invalid"
  if ($identity.schema_version -ne 1 -or $identity.ok -isnot [bool] -or -not $identity.ok -or
      [string]$identity.status -cne "inspected" -or [string]$identity.site_id -cne $SiteId -or
      [string]$identity.grant_id -notmatch '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' -or
      [string]$identity.grant_sha256 -notmatch '^[0-9a-f]{64}$' -or
      $identity.serial -isnot [ValueType] -or [long]$identity.serial -le 0) {
    throw "grant_identity_invalid"
  }
  return $identity
}

function Get-SshOptions {
  return @(
    "-F", "NUL",
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "PreferredAuthentications=publickey",
    "-o", "PubkeyAuthentication=yes",
    "-o", "PasswordAuthentication=no",
    "-o", "KbdInteractiveAuthentication=no",
    "-o", "GSSAPIAuthentication=no",
    "-o", "HostbasedAuthentication=no",
    "-o", "IdentitiesOnly=yes",
    "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=3"
  )
}

function ConvertTo-RemoteLiteral([string]$Value) {
  return "'" + $Value.Replace("'", "''") + "'"
}

function New-RemoteVerifierCommand([string]$RemoteAction) {
  $parts = @(
    "`$ErrorActionPreference='Stop'",
    "& $(ConvertTo-RemoteLiteral $script:RemoteVerifierPath)",
    "-Action $(ConvertTo-RemoteLiteral $RemoteAction)",
    "-SiteId $(ConvertTo-RemoteLiteral $SiteId)"
  )
  if ($RemoteAction -in @("Prepare", "Install", "Cleanup")) {
    $parts += "-OperationId $(ConvertTo-RemoteLiteral $OperationId)"
    $parts += "-GrantSha256 $(ConvertTo-RemoteLiteral ([string]$script:GrantIdentity.grant_sha256))"
    $parts += "-ReasonSha256 $(ConvertTo-RemoteLiteral $script:ReasonSha256)"
  }
  if ($RemoteAction -eq "Install") {
    $incomingPath = "$($script:RemoteIncomingRoot)\$OperationId-$($script:GrantIdentity.grant_sha256).json"
    $parts += "-GrantPath $(ConvertTo-RemoteLiteral $incomingPath)"
    $parts += "-Reason $(ConvertTo-RemoteLiteral $Reason)"
  }
  return $parts -join " "
}

function Invoke-RemoteVerifier([string]$RemoteAction) {
  $command = New-RemoteVerifierCommand -RemoteAction $RemoteAction
  $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
  $arguments = @("-T") + (Get-SshOptions) + @(
    $script:SshTarget, "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    "-NoLogo", "-NoProfile", "-NonInteractive",
    "-OutputFormat", "Text", "-EncodedCommand", $encoded
  )
  Assert-FixedExecutable $script:SshPath "Microsoft"
  return Invoke-BoundedProcess -FilePath $script:SshPath -Arguments $arguments `
    -TimeoutSeconds $TransportTimeoutSeconds
}

function Invoke-GrantUpload {
  $remotePath = "C:/ProgramData/Ruisheng/entitlements/incoming/$OperationId-$($script:GrantIdentity.grant_sha256).json"
  $arguments = @("-q") + (Get-SshOptions) + @($GrantPath, "$($script:ScpTarget)`:$remotePath")
  Assert-FixedExecutable $script:ScpPath "Microsoft"
  return Invoke-BoundedProcess -FilePath $script:ScpPath -Arguments $arguments `
    -TimeoutSeconds $TransportTimeoutSeconds
}

function Assert-PrepareReceipt($Result) {
  $fields = @("schema_version", "ok", "status", "operation_id", "site_id", "incoming_path")
  Assert-ExactFields -Value $Result -Expected $fields -ErrorCode "prepare_receipt_fields_invalid"
  $expectedPath = "$($script:RemoteIncomingRoot)\$OperationId-$($script:GrantIdentity.grant_sha256).json"
  if ($Result.schema_version -ne 1 -or $Result.ok -isnot [bool] -or -not $Result.ok -or
      [string]$Result.status -cne "prepared" -or [string]$Result.operation_id -cne $OperationId -or
      [string]$Result.site_id -cne $SiteId -or [string]$Result.incoming_path -cne $expectedPath) {
    throw "prepare_receipt_invalid"
  }
}

function Assert-CleanupReceipt($Result) {
  $fields = @("schema_version", "ok", "status", "operation_id", "site_id", "incoming_path", "removed")
  Assert-ExactFields -Value $Result -Expected $fields -ErrorCode "cleanup_receipt_fields_invalid"
  $expectedPath = "$($script:RemoteIncomingRoot)\$OperationId-$($script:GrantIdentity.grant_sha256).json"
  if ($Result.schema_version -ne 1 -or $Result.ok -isnot [bool] -or -not $Result.ok -or
      [string]$Result.status -cne "cleaned" -or [string]$Result.operation_id -cne $OperationId -or
      [string]$Result.site_id -cne $SiteId -or [string]$Result.incoming_path -cne $expectedPath -or
      $Result.removed -isnot [bool]) {
    throw "cleanup_receipt_invalid"
  }
}

function Assert-InstallReceipt($Result) {
  $fields = @(
    "schema_version", "ok", "status", "idempotent", "operation_id", "site_id",
    "grant_id", "grant_sha256", "serial", "starts_at", "expires_at", "grace_until",
    "safety_preserved", "collection_preserved", "alarms_preserved", "data_preserved"
  )
  Assert-ExactFields -Value $Result -Expected $fields -ErrorCode "install_receipt_fields_invalid"
  if ($Result.schema_version -ne 1 -or $Result.ok -isnot [bool] -or -not $Result.ok -or
      [string]$Result.status -cne "installed" -or $Result.idempotent -isnot [bool] -or
      [string]$Result.operation_id -cne $OperationId -or [string]$Result.site_id -cne $SiteId) {
    throw "install_receipt_invalid"
  }
  foreach ($field in @("grant_id", "grant_sha256", "serial", "starts_at", "expires_at", "grace_until")) {
    if ([string]$Result.$field -cne [string]$script:GrantIdentity.$field) {
      throw "install_receipt_mismatch"
    }
  }
  foreach ($field in @("safety_preserved", "collection_preserved", "alarms_preserved", "data_preserved")) {
    if ($Result.$field -isnot [bool] -or -not $Result.$field) { throw "install_receipt_invalid" }
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
  Assert-ExactFields -Value $Result -Expected $expected -ErrorCode "status_receipt_fields_invalid"
  if ($Result.schema_version -ne 1 -or $Result.ok -isnot [bool] -or
      [string]$Result.site_id -cne $SiteId -or
      $status -notin @("missing", "uncertain", "pending", "active", "grace", "expired") -or
      $Result.ok -ne ($status -ne "uncertain") -or $Result.entitlement_dependent -isnot [bool] -or
      $Result.entitlement_dependent -ne ($status -in @("grace", "expired"))) {
    throw "status_receipt_invalid"
  }
  if ($hasGrant -and (
      [string]$Result.grant_id -notmatch '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' -or
      [string]$Result.grant_sha256 -notmatch '^[0-9a-f]{64}$' -or
      $Result.serial -isnot [ValueType] -or [long]$Result.serial -le 0)) {
    throw "status_receipt_invalid"
  }
  if ($Result.features -isnot [Array] -or
      @($Result.features | Where-Object {
        $_ -isnot [string] -or [string]$_ -notmatch '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
      }).Count -ne 0) {
    throw "status_receipt_invalid"
  }
  [string[]]$features = @($Result.features)
  [string[]]$sortedFeatures = @($features)
  [Array]::Sort($sortedFeatures, [StringComparer]::Ordinal)
  if (($features -join "`n") -cne ($sortedFeatures -join "`n")) {
    throw "status_receipt_invalid"
  }
  foreach ($field in @("safety_preserved", "collection_preserved", "alarms_preserved", "data_preserved")) {
    if ($Result.$field -isnot [bool] -or -not $Result.$field) { throw "status_receipt_invalid" }
  }
}

function Get-RemoteResult($Native, [string]$InvalidCode) {
  if (-not [string]::IsNullOrWhiteSpace($Native.Stderr)) { throw $InvalidCode }
  $result = ConvertFrom-ExactJson -Text $Native.Stdout.Trim() -ErrorCode $InvalidCode
  return $result
}

function Test-ExplicitRejection($Native) {
  if ($Native.ExitCode -ne 2 -or -not [string]::IsNullOrWhiteSpace($Native.Stderr)) { return $null }
  try {
    $result = ConvertFrom-ExactJson -Text $Native.Stdout.Trim() -ErrorCode "target_rejection_invalid"
    $fields = @(
      "schema_version", "ok", "status", "error_code", "safety_preserved",
      "collection_preserved", "alarms_preserved", "data_preserved"
    )
    Assert-ExactFields -Value $result -Expected $fields -ErrorCode "target_rejection_invalid"
    if ($result.schema_version -ne 1 -or $result.ok -isnot [bool] -or $result.ok -or
        [string]$result.status -cne "rejected" -or
        [string]$result.error_code -notmatch '^[A-Za-z0-9_]+$' -or
        [string]$result.error_code -eq "transaction_uncertain") {
      return $null
    }
    return [string]$result.error_code
  }
  catch { return $null }
}

function Write-LocalAudit([string]$Result, [string]$ErrorCode = "") {
  New-Item -ItemType Directory -Force -Path $AuditDirectory | Out-Null
  $grantSha = if ($null -ne $script:GrantIdentity) {
    [string]$script:GrantIdentity.grant_sha256
  }
  else { "" }
  $record = [ordered]@{
    schema_version = 1
    recorded_at = [DateTimeOffset]::UtcNow.ToString("o")
    action = $Action
    target = $Target
    site_id = $SiteId
    operation_id = $OperationId
    grant_sha256 = $grantSha
    result = $Result
    error_code = $ErrorCode
    reason_sha256 = if ($Reason) {
      $bytes = [Text.Encoding]::UTF8.GetBytes($Reason)
      $hash = [Security.Cryptography.SHA256]::Create()
      try { ([BitConverter]::ToString($hash.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant() }
      finally { $hash.Dispose() }
    }
    else { "" }
  }
  $line = $record | ConvertTo-Json -Compress
  Add-Content -LiteralPath (Join-Path $AuditDirectory "entitlement-install.jsonl") `
    -Value $line -Encoding UTF8
  $script:AuditWritten = $true
}

function Invoke-BestEffortCleanup {
  $native = Invoke-RemoteVerifier -RemoteAction "Cleanup"
  if ($native.ExitCode -ne 0) { throw "remote_cleanup_failed" }
  $result = Get-RemoteResult -Native $native -InvalidCode "cleanup_receipt_invalid"
  Assert-CleanupReceipt $result
}

$finalOutput = $null
$finalExitCode = 0
try {
  if ($Target -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}@(?:[A-Za-z0-9][A-Za-z0-9._:-]{0,253}|\[[A-Fa-f0-9:]+\])$') {
    throw "target_invalid"
  }
  Assert-TailscaleTarget
  if ($SiteId -notmatch '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$') { throw "site_id_invalid" }
  $pythonCandidate = if ([string]::IsNullOrWhiteSpace($LocalPythonPath)) {
    $script:DefaultPythonPath
  } else { $LocalPythonPath }
  $script:PythonPath = Get-NormalizedLocalPath $pythonCandidate "local_python_path_invalid"
  if ($Action -eq "Install") {
    $OperationId = $OperationId.ToLowerInvariant()
    if ($OperationId -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') {
      throw "operation_id_invalid"
    }
    if ($Reason.Length -lt 8 -or $Reason.Length -gt 200 -or $Reason -match '[\x00-\x1F\x7F]') {
      throw "reason_invalid"
    }
    $script:ReasonSha256 = Get-TextSha256 $Reason
    if (-not (Test-Path -LiteralPath $GrantPath -PathType Leaf)) { throw "grant_missing" }
    $grantItem = Get-Item -LiteralPath $GrantPath -Force
    if (($grantItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "grant_reparse_point" }
    if ($grantItem.Length -le 0 -or $grantItem.Length -gt $script:MaxGrantBytes) { throw "grant_size_invalid" }
    $GrantPath = $grantItem.FullName
    if (-not $Approved -and -not $DryRun) { throw "approval_required" }
    try { $GrantPath = New-ProtectedGrantSnapshot }
    catch {
      if ([string]$_.Exception.Message -match '^[A-Za-z0-9_]+$') { throw }
      throw "grant_snapshot_failed"
    }
    $script:Phase = "grant_inspection"
    $script:GrantIdentity = Get-LocalGrantIdentity
  }

  if ($DryRun) {
    $finalOutput = [ordered]@{
      schema_version = 1
      ok = $true
      status = "planned"
      action = $Action
      target = $Target
      site_id = $SiteId
      operation_id = $OperationId
      remote_verifier = $script:RemoteVerifierPath
      remote_state = $script:RemoteStatePath
      grant_sha256 = if ($null -ne $script:GrantIdentity) { $script:GrantIdentity.grant_sha256 } else { "" }
      payment_credentials_transferred = $false
    }
  }
  elseif ($Action -eq "Status") {
    $native = Invoke-RemoteVerifier -RemoteAction "Status"
    if ($native.ExitCode -notin @(0, 2)) { throw "status_transport_failed" }
    $result = Get-RemoteResult -Native $native -InvalidCode "status_receipt_invalid"
    Assert-StatusReceipt $result
    if (($native.ExitCode -eq 0 -and [string]$result.status -ceq "uncertain") -or
        ($native.ExitCode -eq 2 -and [string]$result.status -cne "uncertain")) {
      throw "status_receipt_invalid"
    }
    Write-LocalAudit -Result "status_observed"
    $finalOutput = $result
  }
  else {
    $script:Phase = "prepare"
    $prepareNative = Invoke-RemoteVerifier -RemoteAction "Prepare"
    if ($prepareNative.ExitCode -ne 0) {
      $rejectCode = Test-ExplicitRejection $prepareNative
      if ($null -ne $rejectCode) { throw $rejectCode }
      throw "prepare_transport_failed"
    }
    $prepare = Get-RemoteResult -Native $prepareNative -InvalidCode "prepare_receipt_invalid"
    Assert-PrepareReceipt $prepare

    $script:Phase = "upload"
    $upload = Invoke-GrantUpload
    if ($upload.ExitCode -ne 0 -or -not [string]::IsNullOrWhiteSpace($upload.Stdout) -or
        -not [string]::IsNullOrWhiteSpace($upload.Stderr)) {
      Invoke-BestEffortCleanup
      throw "grant_upload_failed"
    }

    $script:InstallDispatched = $true
    $script:Phase = "install"
    $installNative = Invoke-RemoteVerifier -RemoteAction "Install"
    if ($installNative.ExitCode -ne 0) {
      $rejectCode = Test-ExplicitRejection $installNative
      if ($null -ne $rejectCode) {
        $script:ExplicitRejection = $true
        Invoke-BestEffortCleanup
        Write-LocalAudit -Result "failed" -ErrorCode $rejectCode
        $script:AuditWritten = $true
        throw $rejectCode
      }
      throw "install_transport_failed"
    }
    $installed = Get-RemoteResult -Native $installNative -InvalidCode "install_receipt_invalid"
    Assert-InstallReceipt $installed
    Invoke-BestEffortCleanup
    Remove-ProtectedGrantSnapshot
    Write-LocalAudit -Result "installed"
    $finalOutput = $installed
  }
}
catch {
  $code = Get-SafeCode ([string]$_.Exception.Message)
  try { Remove-ProtectedGrantSnapshot }
  catch { $code = Get-SafeCode ([string]$_.Exception.Message) }
  if ($code -eq "remote_entitlement_failed") { $code = "remote_entitlement_$($script:Phase)_failed" }
  if ($Action -eq "Install" -and -not $DryRun -and $script:InstallDispatched) {
    try { Invoke-BestEffortCleanup }
    catch { $code = Get-SafeCode ([string]$_.Exception.Message) }
  }
  if ($Action -eq "Install" -and -not $DryRun -and -not $script:AuditWritten) {
    $classification = if ($script:InstallDispatched) { "ambiguous_commit" } else { "failed" }
    try { Write-LocalAudit -Result $classification -ErrorCode $code }
    catch { $code = "local_audit_failed" }
  }
  $failureStatus = if ($Action -eq "Install" -and -not $DryRun -and
      $script:InstallDispatched -and -not $script:ExplicitRejection) { "uncertain" } else { "rejected" }
  $finalOutput = [ordered]@{
    schema_version = 1
    ok = $false
    status = $failureStatus
    error_code = $code
    safety_preserved = $true
    collection_preserved = $true
    alarms_preserved = $true
    data_preserved = $true
  }
  $finalExitCode = 2
}

try {
  Remove-ProtectedGrantSnapshot
} catch {
  $cleanupStatus = if ($Action -eq "Install" -and -not $DryRun -and
      $script:InstallDispatched -and -not $script:ExplicitRejection) { "uncertain" } else { "rejected" }
  $finalOutput = [ordered]@{
    schema_version = 1; ok = $false; status = $cleanupStatus
    error_code = (Get-SafeCode ([string]$_.Exception.Message))
    safety_preserved = $true; collection_preserved = $true
    alarms_preserved = $true; data_preserved = $true
  }
  $finalExitCode = 2
}
$finalOutput | ConvertTo-Json -Depth 8 -Compress
exit $finalExitCode
