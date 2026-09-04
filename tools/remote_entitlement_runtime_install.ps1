[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$Target,
  [Parameter(Mandatory)][string]$SiteId,
  [Parameter(Mandatory)][string]$BundlePath,
  [Parameter(Mandatory)][string]$OperationId,
  [switch]$Approved,
  [switch]$DryRun,
  [ValidateRange(10, 600)][int]$TransportTimeoutSeconds = 120,
  [string]$AuditDirectory = "$env:LOCALAPPDATA\Ruisheng\audit"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$script:ExpectedSignedFiles = @(
  "entitlement-public-key",
  "entitlement.py",
  "runtime-metadata.json",
  "target_entitlement_runtime_installer.ps1",
  "target_entitlement_verifier.ps1",
  "vendor-manifest.sha256",
  "vendor.zip"
)
$script:ExpectedBundleFiles = @($script:ExpectedSignedFiles) + @("SHA256SUMS", "SHA256SUMS.sig")
$script:RemoteIncomingParent = "C:\ProgramData\Ruisheng\entitlement-bootstrap-incoming"
$script:SshPath = "C:\Windows\System32\OpenSSH\ssh.exe"
$script:ScpPath = "C:\Windows\System32\OpenSSH\scp.exe"
$script:SshKeygenPath = "C:\Windows\System32\OpenSSH\ssh-keygen.exe"
$script:LocalTrustRoot = "C:\ProgramData\Ruisheng\publisher-trust"
$script:LocalAllowedSignersPath = "C:\ProgramData\Ruisheng\publisher-trust\release-allowed-signers"
$script:SnapshotRoot = "$env:LOCALAPPDATA\Ruisheng\entitlement-runtime-snapshots"
$script:MaxBundleFileBytes = 64MB
$script:MaxBundleBytes = 128MB
$script:MaxIncomingReservations = 2048
$script:MaxIncomingBytes = 128MB
$script:MaxOutputBytes = 65536
$script:ExecutionDispatched = $false
$script:ExplicitRejection = $false
$script:AuditWritten = $false
$script:BundleIdentity = $null
$script:SshTarget = ""
$script:ScpTarget = ""
$script:SnapshotHandles = New-Object Collections.Generic.List[IDisposable]
$script:SnapshotDirectory = ""
$script:RemotePowerShellBootstrap = '$ErrorActionPreference=''Stop'';$ProgressPreference=''SilentlyContinue'';[Console]::InputEncoding=[Text.Encoding]::UTF8;[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false);$encoded=[string]($input|Select-Object -First 1);if([string]::IsNullOrWhiteSpace($encoded)){throw ''stdin_payload_missing''};if($encoded.Length -gt 524288){throw ''stdin_payload_exceeded''};if($encoded -notmatch ''^[A-Za-z0-9+/]+={0,2}$''){throw ''stdin_payload_alphabet_invalid''};try{$bytes=[Convert]::FromBase64String($encoded)}catch{throw ''stdin_payload_decode_invalid''};if([Convert]::ToBase64String($bytes) -cne $encoded){throw ''stdin_payload_noncanonical''};$source=[Text.Encoding]::UTF8.GetString($bytes);& ([ScriptBlock]::Create($source))'

function Get-SafeCode([string]$Message) {
  if ($Message -match '^[A-Za-z0-9_]+$') { return $Message }
  return "remote_runtime_install_failed"
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
  if (-not [Net.IPAddress]::TryParse($hostText, [ref]$address)) { throw "target_not_tailscale" }
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
    [Parameter(Mandatory)][ValidateSet("Microsoft")][string]$Publisher
  )
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "fixed_executable_missing" }
  $item = Get-Item -LiteralPath $Path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "fixed_executable_invalid"
  }
  if (@("ssh.exe", "scp.exe", "ssh-keygen.exe") -cnotcontains $item.Name) {
    throw "fixed_executable_name_invalid"
  }
  try {
    $signature = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $item.FullName
  } catch { throw "fixed_executable_signature_invalid" }
  if ([string]$signature.Status -cne "Valid" -or $null -eq $signature.SignerCertificate) {
    throw "fixed_executable_signature_invalid"
  }
  if ([string]$signature.SignerCertificate.Subject -notmatch
      '(?:^|,\s*)O=Microsoft Corporation(?:,|$)') {
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
    [void]$security.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
      $sid, [Security.AccessControl.FileSystemRights]::FullControl, $inheritance,
      [Security.AccessControl.PropagationFlags]::None,
      [Security.AccessControl.AccessControlType]::Allow
    )))
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
  } catch { throw "local_release_trust_acl_invalid" }
}

function Assert-LocalReleaseTrustAcl {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][ValidateSet("File", "Directory")][string]$Kind,
    [switch]$RequireProtected
  )
  $pathType = if ($Kind -eq "File") { "Leaf" } else { "Container" }
  if (-not (Test-Path -LiteralPath $Path -PathType $pathType)) {
    throw "local_release_trust_acl_invalid"
  }
  $item = Get-Item -LiteralPath $Path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "local_release_trust_acl_invalid"
  }
  try { $acl = Get-Acl -LiteralPath $Path } catch { throw "local_release_trust_acl_invalid" }
  $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  $allowed = @{
    $currentSid = $true
    "S-1-5-18" = $true
    "S-1-5-32-544" = $true
  }
  if (-not $allowed.ContainsKey((Get-LocalPrincipalSid $acl.Owner)) -or
      ($RequireProtected -and -not $acl.AreAccessRulesProtected)) {
    throw "local_release_trust_acl_invalid"
  }
  $rules = @($acl.Access)
  if ($rules.Count -lt 2 -or $rules.Count -gt $allowed.Count) {
    throw "local_release_trust_acl_invalid"
  }
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
      throw "local_release_trust_acl_invalid"
    }
    $seen[$sid] = $true
  }
  if (-not $seen.ContainsKey("S-1-5-18") -or
      (-not $seen.ContainsKey($currentSid) -and -not $seen.ContainsKey("S-1-5-32-544"))) {
    throw "local_release_trust_acl_invalid"
  }
}

function New-ProtectedBundleSnapshot {
  $sourceRoot = Get-NormalizedLocalPath $BundlePath "bundle_path_invalid"
  if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) { throw "bundle_missing" }
  $sourceRootItem = Get-Item -LiteralPath $sourceRoot -Force
  if (($sourceRootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "bundle_reparse_point"
  }
  $sourceItems = @(Get-ChildItem -LiteralPath $sourceRoot -Force)
  [string[]]$sourceNames = @($sourceItems.Name)
  [string[]]$expectedNames = @($script:ExpectedBundleFiles)
  [Array]::Sort($sourceNames, [StringComparer]::Ordinal)
  [Array]::Sort($expectedNames, [StringComparer]::Ordinal)
  if (($sourceNames -join "`n") -cne ($expectedNames -join "`n")) {
    throw "bundle_file_set_invalid"
  }
  $snapshotRoot = Get-NormalizedLocalPath $script:SnapshotRoot "bundle_snapshot_root_invalid"
  if (-not (Test-Path -LiteralPath $script:SnapshotRoot)) {
    [void](New-Item -ItemType Directory -Path $script:SnapshotRoot -Force)
  }
  $snapshotRootItem = Get-Item -LiteralPath $snapshotRoot -Force
  if (-not $snapshotRootItem.PSIsContainer -or
      ($snapshotRootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "bundle_snapshot_root_invalid"
  }
  Set-LocalProtectedAcl $script:SnapshotRoot "Directory"
  $script:SnapshotDirectory = Join-Path $snapshotRoot (
    "$OperationId-$([Guid]::NewGuid().ToString('N'))"
  )
  [void](New-Item -ItemType Directory -Path $script:SnapshotDirectory)
  Set-LocalProtectedAcl $script:SnapshotDirectory "Directory"
  [long]$snapshotBytes = 0
  foreach ($name in $script:ExpectedBundleFiles) {
    $sourcePath = Join-Path $sourceRoot $name
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) { throw "bundle_file_set_invalid" }
    $sourceItem = Get-Item -LiteralPath $sourcePath -Force
    if (($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $sourceItem.Length -le 0 -or $sourceItem.Length -gt $script:MaxBundleFileBytes) {
      throw "bundle_file_invalid"
    }
    $source = [IO.File]::Open($sourcePath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $destination = $null
    try {
      $destinationPath = Join-Path $script:SnapshotDirectory $name
      $destination = [IO.File]::Open(
        $destinationPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::Read
      )
      $buffer = New-Object byte[] 65536
      [long]$total = 0
      while (($count = $source.Read($buffer, 0, $buffer.Length)) -gt 0) {
        $total += $count
        $snapshotBytes += $count
        if ($total -gt $script:MaxBundleFileBytes) { throw "bundle_file_invalid" }
        if ($snapshotBytes -gt $script:MaxBundleBytes) { throw "bundle_size_invalid" }
        $destination.Write($buffer, 0, $count)
      }
      if ($total -le 0) { throw "bundle_file_invalid" }
      $destination.Flush($true)
      $destination.Dispose()
      $destination = $null
      Set-LocalProtectedAcl $destinationPath "File"
      $destination = [IO.File]::Open(
        $destinationPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
      )
      $script:SnapshotHandles.Add($destination)
      $destination = $null
    } finally {
      if ($null -ne $destination) { $destination.Dispose() }
      $source.Dispose()
    }
  }
  return $script:SnapshotDirectory
}

function Remove-ProtectedBundleSnapshot {
  foreach ($handle in $script:SnapshotHandles) { $handle.Dispose() }
  $script:SnapshotHandles.Clear()
  if (-not [string]::IsNullOrEmpty($script:SnapshotDirectory)) {
    $root = Get-NormalizedLocalPath $script:SnapshotRoot "bundle_snapshot_cleanup_path_invalid"
    $snapshot = Get-NormalizedLocalPath $script:SnapshotDirectory "bundle_snapshot_cleanup_path_invalid"
    if ([IO.Path]::GetDirectoryName($snapshot) -cne $root) {
      throw "bundle_snapshot_cleanup_path_invalid"
    }
    if (Test-Path -LiteralPath $snapshot) {
      $item = Get-Item -LiteralPath $snapshot -Force
      if (-not $item.PSIsContainer -or
          ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "bundle_snapshot_cleanup_path_invalid"
      }
      Remove-Item -LiteralPath $snapshot -Recurse -Force -ErrorAction Stop
      if (Test-Path -LiteralPath $snapshot) { throw "bundle_snapshot_cleanup_failed" }
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
  try { return (New-Object Text.UTF8Encoding($false, $true)).GetString($Bytes) }
  catch { throw $ErrorCode }
}

function Invoke-BoundedProcess {
  param(
    [Parameter(Mandatory)][string]$FilePath,
    [Parameter(Mandatory)][string[]]$Arguments,
    [byte[]]$StandardInput = $null,
    [ValidateRange(10, 600)][int]$TimeoutSeconds = 120
  )
  $stdoutPath = [IO.Path]::GetTempFileName()
  $stderrPath = [IO.Path]::GetTempFileName()
  $stdoutStream = $null
  $stderrStream = $null
  $process = New-Object Diagnostics.Process
  $started = $false
  try {
    $stdoutStream = [IO.File]::Open($stdoutPath, "Create", "Write", "Read")
    $stderrStream = [IO.File]::Open($stderrPath, "Create", "Write", "Read")
    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = (@($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join " ")
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $null -ne $StandardInput
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process.StartInfo = $startInfo
    $started = $process.Start()
    if (-not $started) { throw "subprocess_start_failed" }
    $stdoutCopy = $process.StandardOutput.BaseStream.CopyToAsync($stdoutStream)
    $stderrCopy = $process.StandardError.BaseStream.CopyToAsync($stderrStream)
    $stdinTask = $null
    $stdinClosed = $false
    if ($null -ne $StandardInput) {
      $stdinTask = $process.StandardInput.BaseStream.WriteAsync(
        $StandardInput, 0, $StandardInput.Length
      )
    }
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while (-not $process.WaitForExit(100)) {
      if ($null -ne $stdinTask -and $stdinTask.IsCompleted -and -not $stdinClosed) {
        if ($stdinTask.IsFaulted -or $stdinTask.IsCanceled) { throw "subprocess_stdin_failed" }
        $process.StandardInput.BaseStream.Close()
        $stdinClosed = $true
      }
      if ($stdoutStream.Length -gt $script:MaxOutputBytes -or
          $stderrStream.Length -gt $script:MaxOutputBytes) {
        try { $process.Kill() } catch { }
        throw "subprocess_output_exceeded"
      }
      if ([DateTimeOffset]::UtcNow -ge $deadline) {
        try { $process.Kill() } catch { }
        throw "subprocess_timeout"
      }
    }
    if ($null -ne $stdinTask) {
      $remaining = [Math]::Max(0, [int][Math]::Ceiling(($deadline - [DateTimeOffset]::UtcNow).TotalMilliseconds))
      if (-not $stdinTask.Wait($remaining) -or $stdinTask.IsFaulted -or $stdinTask.IsCanceled) {
        throw "subprocess_stdin_failed"
      }
      if (-not $stdinClosed) {
        $process.StandardInput.BaseStream.Close()
        $stdinClosed = $true
      }
    }
    $remaining = [Math]::Max(0, [int][Math]::Ceiling(($deadline - [DateTimeOffset]::UtcNow).TotalMilliseconds))
    if (-not [Threading.Tasks.Task]::WaitAll(@($stdoutCopy, $stderrCopy), $remaining)) {
      throw "subprocess_stream_timeout"
    }
    $stdoutStream.Flush()
    $stderrStream.Flush()
    if ($stdoutStream.Length -gt $script:MaxOutputBytes -or
        $stderrStream.Length -gt $script:MaxOutputBytes) { throw "subprocess_output_exceeded" }
    $exitCode = $process.ExitCode
    $stdoutStream.Dispose(); $stdoutStream = $null
    $stderrStream.Dispose(); $stderrStream = $null
    return [pscustomobject]@{
      ExitCode = $exitCode
      Stdout = Get-StrictUtf8 ([IO.File]::ReadAllBytes($stdoutPath)) "subprocess_output_encoding_invalid"
      Stderr = Get-StrictUtf8 ([IO.File]::ReadAllBytes($stderrPath)) "subprocess_output_encoding_invalid"
    }
  } finally {
    if ($started -and -not $process.HasExited) { try { $process.Kill() } catch { } }
    $process.Dispose()
    if ($null -ne $stdoutStream) { $stdoutStream.Dispose() }
    if ($null -ne $stderrStream) { $stderrStream.Dispose() }
    Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
  }
}

function Get-SshOptions {
  return @(
    "-F", "NUL",
    "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
    "-o", "PreferredAuthentications=publickey", "-o", "PubkeyAuthentication=yes",
    "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
    "-o", "GSSAPIAuthentication=no", "-o", "HostbasedAuthentication=no",
    "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"
  )
}

function ConvertTo-PowerShellUtf8Expression([AllowEmptyString()][string]$Value) {
  $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Value))
  return "[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$encoded'))"
}

function ConvertFrom-ExactJson([string]$Text, [string]$ErrorCode) {
  if ([string]::IsNullOrWhiteSpace($Text)) { throw $ErrorCode }
  try { $value = $Text | ConvertFrom-Json } catch { throw $ErrorCode }
  if ($null -eq $value -or $value -is [Array]) { throw $ErrorCode }
  return $value
}

function Assert-ExactFields($Value, [string[]]$Expected, [string]$ErrorCode) {
  [string[]]$actual = @($Value.PSObject.Properties.Name)
  [string[]]$wanted = @($Expected)
  [Array]::Sort($actual, [StringComparer]::Ordinal)
  [Array]::Sort($wanted, [StringComparer]::Ordinal)
  if (($actual -join "`n") -cne ($wanted -join "`n")) { throw $ErrorCode }
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

function Get-LocalBundleIdentity {
  $root = [IO.Path]::GetFullPath($BundlePath).TrimEnd('\')
  if (-not (Test-Path -LiteralPath $root -PathType Container)) { throw "bundle_missing" }
  $rootItem = Get-Item -LiteralPath $root -Force
  if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "bundle_reparse_point"
  }
  $items = @(Get-ChildItem -LiteralPath $root -Force)
  [string[]]$itemNames = @($items.Name)
  [string[]]$expectedNames = @($script:ExpectedBundleFiles)
  [Array]::Sort($itemNames, [StringComparer]::Ordinal)
  [Array]::Sort($expectedNames, [StringComparer]::Ordinal)
  if (($itemNames -join "`n") -cne ($expectedNames -join "`n")) {
    throw "bundle_file_set_invalid"
  }
  [long]$bundleBytes = 0
  foreach ($item in $items) {
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $item.Length -le 0 -or $item.Length -gt $script:MaxBundleFileBytes) {
      throw "bundle_file_invalid"
    }
    $bundleBytes += [long]$item.Length
    if ($bundleBytes -gt $script:MaxBundleBytes) { throw "bundle_size_invalid" }
  }
  $sumsPath = Join-Path $root "SHA256SUMS"
  $signaturePath = Join-Path $root "SHA256SUMS.sig"
  if ((Get-Item -LiteralPath $sumsPath).Length -gt 1MB -or
      (Get-Item -LiteralPath $signaturePath).Length -gt 64KB) {
    throw "bundle_signature_metadata_size_invalid"
  }
  $ascii = [Text.Encoding]::GetEncoding(
    20127, [Text.EncoderFallback]::ExceptionFallback, [Text.DecoderFallback]::ExceptionFallback
  )
  try {
    $sumsText = $ascii.GetString((
      Read-LocalBoundedBytes $sumsPath 1MB "bundle_sums_encoding_invalid"
    ))
  }
  catch { throw "bundle_sums_encoding_invalid" }
  if (-not $sumsText.EndsWith("`n") -or $sumsText.Contains("`r")) {
    throw "bundle_sums_not_canonical"
  }
  $sums = [ordered]@{}
  foreach ($line in @($sumsText.Substring(0, $sumsText.Length - 1).Split("`n"))) {
    if ($line -notmatch '^([0-9a-f]{64})  ([A-Za-z0-9_.-]+)$' -or
        $sums.Contains($Matches[2])) { throw "bundle_sums_invalid" }
    $sums[$Matches[2]] = $Matches[1]
  }
  if (($sums.Keys -join "`n") -cne ($script:ExpectedSignedFiles -join "`n")) {
    throw "bundle_sums_file_set_invalid"
  }
  foreach ($relative in $script:ExpectedSignedFiles) {
    $actual = (Get-FileHash -LiteralPath (Join-Path $root $relative) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -cne $sums[$relative]) { throw "bundle_hash_invalid" }
  }
  if (-not (Test-Path -LiteralPath $script:LocalAllowedSignersPath -PathType Leaf)) {
    throw "local_release_trust_missing"
  }
  Assert-LocalReleaseTrustAcl -Path $script:LocalTrustRoot -Kind Directory -RequireProtected
  Assert-LocalReleaseTrustAcl -Path $script:LocalAllowedSignersPath -Kind File
  $trustItem = Get-Item -LiteralPath $script:LocalAllowedSignersPath -Force
  if (($trustItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
      $trustItem.Length -le 0 -or $trustItem.Length -gt 4KB) {
    throw "local_release_trust_invalid"
  }
  try {
    $trustText = $ascii.GetString((
      Read-LocalBoundedBytes $script:LocalAllowedSignersPath 4KB "local_release_trust_invalid"
    ))
  }
  catch { throw "local_release_trust_invalid" }
  if ($trustText -cnotmatch '^ruisheng-release ssh-ed25519 [A-Za-z0-9+/]+={0,2}\n$') {
    throw "local_release_trust_invalid"
  }
  Assert-FixedExecutable $script:SshKeygenPath "Microsoft"
  $verify = Invoke-BoundedProcess -FilePath $script:SshKeygenPath -Arguments @(
    "-Y", "verify", "-f", $script:LocalAllowedSignersPath, "-I", "ruisheng-release",
    "-n", "ruisheng-entitlement-runtime-v1", "-s", $signaturePath
  ) -StandardInput ([Text.Encoding]::ASCII.GetBytes($sumsText)) -TimeoutSeconds 30
  if ($verify.ExitCode -ne 0) { throw "bundle_signature_invalid" }
  $sha = [Security.Cryptography.SHA256]::Create()
  try {
    $sumsSha = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::ASCII.GetBytes($sumsText)))).Replace("-", "").ToLowerInvariant()
  } finally { $sha.Dispose() }
  $metadataPath = Join-Path $root "runtime-metadata.json"
  if ((Get-Item -LiteralPath $metadataPath -Force).Length -gt 1024) {
    throw "runtime_metadata_invalid"
  }
  try {
    $metadataText = $ascii.GetString((
      Read-LocalBoundedBytes $metadataPath 1024 "runtime_metadata_invalid"
    ))
  }
  catch { throw "runtime_metadata_invalid" }
  $metadataMatch = [regex]::Match(
    $metadataText,
    '^\{"entitlement_key_generation":([1-9][0-9]*),"runtime_epoch":([1-9][0-9]*),"schema_version":1\}\n$',
    [Text.RegularExpressions.RegexOptions]::CultureInvariant
  )
  if (-not $metadataMatch.Success) { throw "runtime_metadata_invalid" }
  if ($metadataMatch.Groups[1].Value -cne "1") {
    throw "entitlement_key_generation_unsupported"
  }
  $script:BundlePath = $root
  return [ordered]@{
    bundle_bytes = $bundleBytes
    sums_sha256 = $sumsSha
    installer_sha256 = $sums["target_entitlement_runtime_installer.ps1"]
    entitlement_sha256 = $sums["entitlement.py"]
    verifier_sha256 = $sums["target_entitlement_verifier.ps1"]
    public_key_sha256 = $sums["entitlement-public-key"]
    vendor_archive_sha256 = $sums["vendor.zip"]
    runtime_epoch = [long]::Parse(
      $metadataMatch.Groups[2].Value, [Globalization.CultureInfo]::InvariantCulture
    )
    entitlement_key_generation = [long]::Parse(
      $metadataMatch.Groups[1].Value, [Globalization.CultureInfo]::InvariantCulture
    )
  }
}

function Invoke-SshScript([string]$Script) {
  $bootstrap = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script:RemotePowerShellBootstrap))
  $arguments = @("-T") + (Get-SshOptions) + @(
    $script:SshTarget, "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    "-NoLogo", "-NoProfile", "-NonInteractive",
    "-OutputFormat", "Text", "-EncodedCommand", $bootstrap
  )
  $payload = [Text.Encoding]::ASCII.GetBytes(
    [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Script)) + "`n"
  )
  Assert-FixedExecutable $script:SshPath "Microsoft"
  return Invoke-BoundedProcess -FilePath $script:SshPath -Arguments $arguments -StandardInput $payload `
    -TimeoutSeconds $TransportTimeoutSeconds
}

function New-PrepareScript {
  $operationExpression = ConvertTo-PowerShellUtf8Expression $OperationId
  $siteExpression = ConvertTo-PowerShellUtf8Expression $SiteId
  $digestExpression = ConvertTo-PowerShellUtf8Expression ([string]$script:BundleIdentity.sums_sha256)
  $bundleBytesExpression = ([long]$script:BundleIdentity.bundle_bytes).ToString(
    [Globalization.CultureInfo]::InvariantCulture
  )
  return @"
`$ErrorActionPreference='Stop'
`$allowed=@('S-1-5-18','S-1-5-32-544')
`$parent='$($script:RemoteIncomingParent)'
`$lockPath='C:\ProgramData\Ruisheng\entitlement-bootstrap.lock'
`$maxReservations=$($script:MaxIncomingReservations)
`$maxIncomingBytes=$($script:MaxIncomingBytes)
`$maxBundleFileBytes=$($script:MaxBundleFileBytes)
`$expectedFiles=@('$($script:ExpectedBundleFiles -join "','")')
function Sid(`$identity){try{if(`$identity -is [Security.Principal.IdentityReference]){return `$identity.Translate([Security.Principal.SecurityIdentifier]).Value};return (New-Object Security.Principal.NTAccount([string]`$identity)).Translate([Security.Principal.SecurityIdentifier]).Value}catch{throw 'bootstrap_acl_identity_invalid'}}
function AssertItem([string]`$path,[string]`$kind){`$type=if(`$kind -eq 'File'){'Leaf'}else{'Container'};if(-not(Test-Path -LiteralPath `$path -PathType `$type)){throw 'bootstrap_path_missing'};`$item=Get-Item -LiteralPath `$path -Force;if((`$item.Attributes-band[IO.FileAttributes]::ReparsePoint)-ne 0){throw 'bootstrap_reparse_point'};`$acl=Get-Acl -LiteralPath `$path;if(`$allowed -notcontains (Sid `$acl.Owner)-or -not `$acl.AreAccessRulesProtected){throw 'bootstrap_acl_invalid'};`$rules=@(`$acl.Access);if(`$rules.Count-ne 2){throw 'bootstrap_acl_invalid'};`$seen=@{};foreach(`$rule in `$rules){`$sid=Sid `$rule.IdentityReference;`$inherit=if(`$kind -eq 'Directory'){[Security.AccessControl.InheritanceFlags]::ContainerInherit-bor[Security.AccessControl.InheritanceFlags]::ObjectInherit}else{[Security.AccessControl.InheritanceFlags]::None};if(`$allowed -notcontains `$sid-or `$seen.ContainsKey(`$sid)-or `$rule.IsInherited-or `$rule.AccessControlType-ne[Security.AccessControl.AccessControlType]::Allow-or `$rule.FileSystemRights-ne[Security.AccessControl.FileSystemRights]::FullControl-or `$rule.InheritanceFlags-ne `$inherit-or `$rule.PropagationFlags-ne[Security.AccessControl.PropagationFlags]::None){throw 'bootstrap_acl_invalid'};`$seen[`$sid]=`$true}}
function Protect([string]`$path,[string]`$kind){`$acl=if(`$kind -eq 'Directory'){New-Object Security.AccessControl.DirectorySecurity}else{New-Object Security.AccessControl.FileSecurity};`$acl.SetAccessRuleProtection(`$true,`$false);`$acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')));foreach(`$sidText in `$allowed){`$inherit=if(`$kind -eq 'Directory'){[Security.AccessControl.InheritanceFlags]::ContainerInherit-bor[Security.AccessControl.InheritanceFlags]::ObjectInherit}else{[Security.AccessControl.InheritanceFlags]::None};[void]`$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier(`$sidText)),[Security.AccessControl.FileSystemRights]::FullControl,`$inherit,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow))};Set-Acl -LiteralPath `$path -AclObject `$acl;AssertItem `$path `$kind}
function EnsureDir([string]`$path){if(Test-Path -LiteralPath `$path){AssertItem `$path 'Directory'}else{[void](New-Item -ItemType Directory -Path `$path);Protect `$path 'Directory'}}
function ReadBounded([string]`$path,[long]`$maximum){`$stream=[IO.File]::Open(`$path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);try{`$memory=New-Object IO.MemoryStream;try{`$buffer=New-Object byte[] 512;while((`$count=`$stream.Read(`$buffer,0,`$buffer.Length))-gt 0){if(`$memory.Length+`$count-gt `$maximum){throw 'bootstrap_reservation_invalid'};`$memory.Write(`$buffer,0,`$count)};return [Text.Encoding]::ASCII.GetString(`$memory.ToArray())}finally{`$memory.Dispose()}}finally{`$stream.Dispose()}}
function EnterLock{`$stream=`$null;try{if(Test-Path -LiteralPath `$lockPath){AssertItem `$lockPath 'File';`$stream=[IO.File]::Open(`$lockPath,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)}else{`$stream=[IO.File]::Open(`$lockPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None);Protect `$lockPath 'File'};return `$stream}catch{if(`$null-ne`$stream){`$stream.Dispose()};if(`$_.Exception.Message-match'^bootstrap_'){throw};throw 'bootstrap_busy'}}
function ReadReservation([string]`$path){AssertItem `$path 'File';`$text=ReadBounded `$path 512;try{`$record=`$text|ConvertFrom-Json}catch{throw 'bootstrap_reservation_invalid'};[string[]]`$fields=@('bundle_bytes','operation_id','schema_version','site_id','sums_sha256');[string[]]`$actual=@(`$record.PSObject.Properties.Name);[Array]::Sort(`$fields,[StringComparer]::Ordinal);[Array]::Sort(`$actual,[StringComparer]::Ordinal);if((`$fields-join"`n")-cne(`$actual-join"`n")-or`$record.schema_version-ne 1-or`$record.bundle_bytes-isnot[ValueType]-or[long]`$record.bundle_bytes-le 0-or[long]`$record.bundle_bytes-gt`$maxIncomingBytes-or[string]`$record.operation_id-notmatch'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'-or[string]`$record.site_id-notmatch'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'-or[string]`$record.sums_sha256-notmatch'^[0-9a-f]{64}$'){throw 'bootstrap_reservation_invalid'};`$canonical='{"bundle_bytes":'+([long]`$record.bundle_bytes).ToString([Globalization.CultureInfo]::InvariantCulture)+',"operation_id":"'+[string]`$record.operation_id+'","schema_version":1,"site_id":"'+[string]`$record.site_id+'","sums_sha256":"'+[string]`$record.sums_sha256+'"}'+"`n";if(`$text-cne`$canonical-or(Split-Path -Leaf `$path)-cne("`$(`$record.operation_id).reservation.json")){throw 'bootstrap_reservation_invalid'};return `$record}
`$identity=[Security.Principal.WindowsIdentity]::GetCurrent();`$principal=New-Object Security.Principal.WindowsPrincipal(`$identity);if(`$identity.User.Value-ne'S-1-5-18'-and -not `$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'bootstrap_admin_required'}
AssertItem 'C:\ProgramData\Ruisheng' 'Directory'
AssertItem 'C:\ProgramData\Ruisheng\trust' 'Directory'
AssertItem 'C:\ProgramData\Ruisheng\trust\release-allowed-signers' 'File'
AssertItem 'C:\ProgramData\Ruisheng\trust\release-key-fingerprint' 'File'
EnsureDir `$parent
`$operation=$operationExpression;`$site=$siteExpression;`$digest=$digestExpression;[long]`$bundleBytes=$bundleBytesExpression;`$bundle=Join-Path `$parent `$operation;`$reservation=Join-Path `$parent ("`$operation.reservation.json");`$reservationText='{"bundle_bytes":'+`$bundleBytes.ToString([Globalization.CultureInfo]::InvariantCulture)+',"operation_id":"'+`$operation+'","schema_version":1,"site_id":"'+`$site+'","sums_sha256":"'+`$digest+'"}' + "`n"
`$lock=EnterLock
try{
if(Test-Path -LiteralPath `$reservation){if((ReadBounded `$reservation 512)-cne`$reservationText){throw 'bootstrap_operation_conflict'}}
`$reservations=@{};`$bundles=@{};[long]`$reservedBytes=0
foreach(`$item in @(Get-ChildItem -LiteralPath `$parent -Force)){if((`$item.Attributes-band[IO.FileAttributes]::ReparsePoint)-ne 0){throw 'bootstrap_incoming_store_invalid'};if(`$item.PSIsContainer){if(`$item.Name-notmatch'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'-or`$bundles.ContainsKey(`$item.Name)){throw 'bootstrap_incoming_store_invalid'};AssertItem `$item.FullName 'Directory';`$bundles[`$item.Name]=`$item}else{if(`$item.Name-notmatch'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.reservation\.json$'){throw 'bootstrap_incoming_store_invalid'};`$record=ReadReservation `$item.FullName;if(`$reservations.ContainsKey([string]`$record.operation_id)){throw 'bootstrap_incoming_store_invalid'};`$reservations[[string]`$record.operation_id]=`$record;`$reservedBytes+=[long]`$record.bundle_bytes;if(`$reservedBytes-gt`$maxIncomingBytes){throw 'bootstrap_incoming_store_full'}}}
if(`$reservations.Count-gt`$maxReservations){throw 'bootstrap_incoming_store_full'}
foreach(`$bundleId in @(`$bundles.Keys)){if(-not`$reservations.ContainsKey(`$bundleId)){throw 'bootstrap_incoming_store_invalid'};`$record=`$reservations[`$bundleId];`$seen=@{};[long]`$actualBytes=0;foreach(`$item in @(Get-ChildItem -LiteralPath `$bundles[`$bundleId].FullName -Force)){if(`$item.PSIsContainer-or(`$item.Attributes-band[IO.FileAttributes]::ReparsePoint)-ne 0-or`$item.Length-le 0-or`$item.Length-gt`$maxBundleFileBytes-or`$expectedFiles-cnotcontains`$item.Name-or`$seen.ContainsKey(`$item.Name)){throw 'bootstrap_incoming_store_invalid'};`$seen[`$item.Name]=`$true;`$actualBytes+=[long]`$item.Length;if(`$actualBytes-gt[long]`$record.bundle_bytes){throw 'bootstrap_incoming_store_invalid'}}}
if(-not(Test-Path -LiteralPath `$reservation)){if(`$reservations.Count-ge`$maxReservations-or`$reservedBytes+`$bundleBytes-gt`$maxIncomingBytes){throw 'bootstrap_incoming_store_full'};`$temporary="`$reservation.`$([Guid]::NewGuid().ToString('N')).tmp";try{`$bytes=[Text.Encoding]::ASCII.GetBytes(`$reservationText);`$stream=[IO.File]::Open(`$temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None);try{`$stream.Write(`$bytes,0,`$bytes.Length);`$stream.Flush(`$true)}finally{`$stream.Dispose()};Protect `$temporary 'File';Move-Item -LiteralPath `$temporary -Destination `$reservation}catch{if(Test-Path -LiteralPath `$temporary){Remove-Item -LiteralPath `$temporary -Force};throw}}
if(Test-Path -LiteralPath `$bundle){AssertItem `$bundle 'Directory'}else{[void](New-Item -ItemType Directory -Path `$bundle);Protect `$bundle 'Directory'}
[ordered]@{schema_version=1;ok=`$true;status='prepared';operation_id=`$operation;site_id=`$site;incoming_path=`$bundle}|ConvertTo-Json -Compress
}finally{if(`$null-ne`$lock){`$lock.Dispose()}}
"@
}

function New-ExecuteScript {
  $operationExpression = ConvertTo-PowerShellUtf8Expression $OperationId
  $siteExpression = ConvertTo-PowerShellUtf8Expression $SiteId
  $digestExpression = ConvertTo-PowerShellUtf8Expression ([string]$script:BundleIdentity.sums_sha256)
  $bundleBytesExpression = ([long]$script:BundleIdentity.bundle_bytes).ToString(
    [Globalization.CultureInfo]::InvariantCulture
  )
  return @"
`$ErrorActionPreference='Stop';`$ProgressPreference='SilentlyContinue';`$operation=$operationExpression;`$site=$siteExpression;`$digest=$digestExpression;[long]`$bundleBytes=$bundleBytesExpression;`$bundle=Join-Path '$($script:RemoteIncomingParent)' `$operation;`$reservation=Join-Path '$($script:RemoteIncomingParent)' ("`$operation.reservation.json");`$installerReturned=`$false
try{
`$allowedSids=@('S-1-5-18','S-1-5-32-544');function Sid(`$identity){if(`$identity -is [Security.Principal.IdentityReference]){return `$identity.Translate([Security.Principal.SecurityIdentifier]).Value};return (New-Object Security.Principal.NTAccount([string]`$identity)).Translate([Security.Principal.SecurityIdentifier]).Value};function AssertItem([string]`$path,[string]`$kind){`$type=if(`$kind-eq'File'){'Leaf'}else{'Container'};if(-not(Test-Path -LiteralPath `$path -PathType `$type)){throw 'bootstrap_path_missing'};`$item=Get-Item -LiteralPath `$path -Force;if((`$item.Attributes-band[IO.FileAttributes]::ReparsePoint)-ne 0){throw 'bootstrap_reparse_point'};`$acl=Get-Acl -LiteralPath `$path;if(`$allowedSids-notcontains(Sid `$acl.Owner)-or -not `$acl.AreAccessRulesProtected){throw 'bootstrap_acl_invalid'};`$rules=@(`$acl.Access);if(`$rules.Count-ne 2){throw 'bootstrap_acl_invalid'};`$seen=@{};foreach(`$rule in `$rules){`$sid=Sid `$rule.IdentityReference;`$inherit=if(`$kind-eq'Directory'){[Security.AccessControl.InheritanceFlags]::ContainerInherit-bor[Security.AccessControl.InheritanceFlags]::ObjectInherit}else{[Security.AccessControl.InheritanceFlags]::None};if(`$allowedSids-notcontains `$sid-or `$seen.ContainsKey(`$sid)-or `$rule.IsInherited-or `$rule.AccessControlType-ne[Security.AccessControl.AccessControlType]::Allow-or `$rule.FileSystemRights-ne[Security.AccessControl.FileSystemRights]::FullControl-or `$rule.InheritanceFlags-ne `$inherit-or `$rule.PropagationFlags-ne[Security.AccessControl.PropagationFlags]::None){throw 'bootstrap_acl_invalid'};`$seen[`$sid]=`$true}};function ProtectFile([string]`$path){`$acl=New-Object Security.AccessControl.FileSecurity;`$acl.SetAccessRuleProtection(`$true,`$false);`$acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')));foreach(`$sidText in `$allowedSids){[void]`$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier(`$sidText)),[Security.AccessControl.FileSystemRights]::FullControl,[Security.AccessControl.InheritanceFlags]::None,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow))};Set-Acl -LiteralPath `$path -AclObject `$acl;AssertItem `$path 'File'};function ReadBounded([string]`$path,[long]`$maximum,[string]`$code){`$stream=[IO.File]::Open(`$path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);try{`$memory=New-Object IO.MemoryStream;try{`$buffer=New-Object byte[] 4096;while((`$count=`$stream.Read(`$buffer,0,`$buffer.Length))-gt 0){if(`$memory.Length+`$count-gt `$maximum){throw `$code};`$memory.Write(`$buffer,0,`$count)};if(`$memory.Length-le 0){throw `$code};return `$memory.ToArray()}finally{`$memory.Dispose()}}finally{`$stream.Dispose()}}
AssertItem 'C:\ProgramData\Ruisheng' 'Directory';AssertItem 'C:\ProgramData\Ruisheng\trust' 'Directory';AssertItem `$reservation 'File';`$reservationText=[Text.Encoding]::ASCII.GetString((ReadBounded `$reservation 512 'bootstrap_reservation_invalid'));`$expectedReservation='{"bundle_bytes":'+`$bundleBytes.ToString([Globalization.CultureInfo]::InvariantCulture)+',"operation_id":"'+`$operation+'","schema_version":1,"site_id":"'+`$site+'","sums_sha256":"'+`$digest+'"}' + "`n";if(`$reservationText-cne `$expectedReservation){throw 'bootstrap_operation_conflict'};AssertItem `$bundle 'Directory';[string[]]`$expected=@('$($script:ExpectedBundleFiles -join "','")');`$actual=@(Get-ChildItem -LiteralPath `$bundle -Force);[string[]]`$actualNames=@(`$actual.Name);[Array]::Sort(`$actualNames,[StringComparer]::Ordinal);[Array]::Sort(`$expected,[StringComparer]::Ordinal);if((`$actualNames-join"`n")-cne(`$expected-join"`n")){throw 'bootstrap_bundle_file_set_invalid'};[long]`$actualBytes=0;foreach(`$item in `$actual){if(`$item.PSIsContainer-or(`$item.Attributes-band[IO.FileAttributes]::ReparsePoint)-ne 0-or `$item.Length-le 0-or `$item.Length-gt 67108864){throw 'bootstrap_bundle_file_invalid'};`$actualBytes+=[long]`$item.Length;if(`$actualBytes-gt`$bundleBytes){throw 'bootstrap_bundle_size_invalid'};ProtectFile `$item.FullName};if(`$actualBytes-ne`$bundleBytes){throw 'bootstrap_bundle_size_invalid'}
`$allowed='C:\ProgramData\Ruisheng\trust\release-allowed-signers';`$fingerprint='C:\ProgramData\Ruisheng\trust\release-key-fingerprint';AssertItem `$allowed 'File';AssertItem `$fingerprint 'File';`$ascii=[Text.Encoding]::GetEncoding(20127,[Text.EncoderFallback]::ExceptionFallback,[Text.DecoderFallback]::ExceptionFallback);try{`$allowedText=`$ascii.GetString((ReadBounded `$allowed 4096 'bootstrap_release_trust_invalid'));`$fingerprintText=`$ascii.GetString((ReadBounded `$fingerprint 256 'bootstrap_release_trust_invalid'))}catch{throw 'bootstrap_release_trust_invalid'};`$match=[regex]::Match(`$allowedText,'^ruisheng-release ssh-ed25519 ([A-Za-z0-9+/]+={0,2})\n$');if(-not `$match.Success-or `$fingerprintText-cnotmatch'^SHA256:[A-Za-z0-9+/]{43}\n$'){throw 'bootstrap_release_trust_invalid'};`$sha=[Security.Cryptography.SHA256]::Create();try{`$actualFingerprint='SHA256:'+[Convert]::ToBase64String(`$sha.ComputeHash([Convert]::FromBase64String(`$match.Groups[1].Value))).TrimEnd('=')+"`n"}finally{`$sha.Dispose()};if(`$actualFingerprint-cne `$fingerprintText){throw 'bootstrap_release_trust_invalid'}
`$sumsPath=Join-Path `$bundle 'SHA256SUMS';`$sigPath=Join-Path `$bundle 'SHA256SUMS.sig';if((Get-Item -LiteralPath `$sigPath).Length-gt 65536){throw 'bootstrap_signature_metadata_size_invalid'};try{`$sumsText=`$ascii.GetString((ReadBounded `$sumsPath 1048576 'bootstrap_signature_metadata_size_invalid'))}catch{if(`$_.Exception.Message-eq'bootstrap_signature_metadata_size_invalid'){throw};throw 'bootstrap_sums_not_canonical'};if(-not `$sumsText.EndsWith("`n")-or `$sumsText.Contains("`r")){throw 'bootstrap_sums_not_canonical'};`$sums=@{};`$names=New-Object Collections.Generic.List[string];foreach(`$line in @(`$sumsText.Substring(0,`$sumsText.Length-1).Split("`n"))){if(`$line-notmatch'^([0-9a-f]{64})  ([A-Za-z0-9_.-]+)$'-or `$sums.ContainsKey(`$Matches[2])){throw 'bootstrap_sums_invalid'};`$sums[`$Matches[2]]=`$Matches[1];[void]`$names.Add(`$Matches[2])};`$signed=@('$($script:ExpectedSignedFiles -join "','")');if((`$names-join"`n")-cne(`$signed-join"`n")){throw 'bootstrap_sums_file_set_invalid'};foreach(`$name in `$signed){if((Get-FileHash -LiteralPath (Join-Path `$bundle `$name)-Algorithm SHA256).Hash.ToLowerInvariant()-cne `$sums[`$name]){throw 'bootstrap_bundle_hash_invalid'}}
`$sshKeygen='C:\Windows\System32\OpenSSH\ssh-keygen.exe';`$keygenItem=Get-Item -LiteralPath `$sshKeygen -Force;if(`$keygenItem.Name-cne'ssh-keygen.exe'-or(`$keygenItem.Attributes-band[IO.FileAttributes]::ReparsePoint)-ne 0){throw 'bootstrap_signature_verifier_invalid'};try{`$auth=Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath `$sshKeygen}catch{throw 'bootstrap_signature_verifier_invalid'};if([string]`$auth.Status-cne'Valid'-or `$null-eq`$auth.SignerCertificate-or[string]`$auth.SignerCertificate.Subject-notmatch'(?:^|,\s*)O=Microsoft Corporation(?:,|$)'){throw 'bootstrap_signature_verifier_invalid'};`$start=New-Object Diagnostics.ProcessStartInfo;`$start.FileName=`$sshKeygen;`$start.Arguments="-Y verify -f ```"`$allowed```" -I ruisheng-release -n ruisheng-entitlement-runtime-v1 -s ```"`$sigPath```"";`$start.UseShellExecute=`$false;`$start.CreateNoWindow=`$true;`$start.RedirectStandardInput=`$true;`$start.RedirectStandardOutput=`$true;`$start.RedirectStandardError=`$true;`$process=New-Object Diagnostics.Process;`$process.StartInfo=`$start;try{if(-not `$process.Start()){throw 'bootstrap_signature_verifier_failed'};`$process.StandardInput.Write(`$sumsText);`$process.StandardInput.Close();`$outTask=`$process.StandardOutput.ReadToEndAsync();`$errTask=`$process.StandardError.ReadToEndAsync();if(-not `$process.WaitForExit(30000)){try{`$process.Kill()}catch{};throw 'bootstrap_signature_timeout'};`$out=`$outTask.GetAwaiter().GetResult();`$err=`$errTask.GetAwaiter().GetResult();if(`$out.Length-gt 65536-or `$err.Length-gt 65536-or `$process.ExitCode-ne 0){throw 'bootstrap_signature_invalid'}}finally{if(-not `$process.HasExited){try{`$process.Kill()}catch{}};`$process.Dispose()}
`$installer=Join-Path `$bundle 'target_entitlement_runtime_installer.ps1';if((Get-FileHash -LiteralPath `$installer -Algorithm SHA256).Hash.ToLowerInvariant()-cne `$sums['target_entitlement_runtime_installer.ps1']){throw 'bootstrap_installer_changed'};`$lines=@(& `$installer -OperationId `$operation -SiteId `$site);`$installerReturned=`$true;if(`$lines.Count-ne 1){throw 'bootstrap_runtime_receipt_invalid'};`$receipt=`$lines[0]|ConvertFrom-Json;[string[]]`$fields=@('schema_version','ok','status','operation_id','site_id','entitlement_sha256','verifier_sha256','public_key_sha256','vendor_archive_sha256','runtime_epoch','entitlement_key_generation','services_restarted','device_configuration_changed');[string[]]`$receiptFields=@(`$receipt.PSObject.Properties.Name);[Array]::Sort(`$fields,[StringComparer]::Ordinal);[Array]::Sort(`$receiptFields,[StringComparer]::Ordinal);if((`$receiptFields-join"`n")-cne(`$fields-join"`n")-or `$receipt.schema_version-ne 1-or -not `$receipt.ok-or [string]`$receipt.status-cne'runtime_installed'-or [string]`$receipt.operation_id-cne `$operation-or [string]`$receipt.site_id-cne `$site-or `$receipt.runtime_epoch-isnot[ValueType]-or[long]`$receipt.runtime_epoch-le 0-or `$receipt.entitlement_key_generation-isnot[ValueType]-or[long]`$receipt.entitlement_key_generation-le 0-or `$receipt.services_restarted-or `$receipt.device_configuration_changed){throw 'bootstrap_runtime_receipt_invalid'};`$receipt|ConvertTo-Json -Compress
}catch{`$code=if(`$_.Exception.Message-match'^[A-Za-z0-9_]+$'){`$_.Exception.Message}else{'bootstrap_execution_failed'};`$status=if(`$installerReturned-or `$code-in@('bootstrap_transaction_uncertain','bootstrap_busy')){'uncertain'}else{'rejected'};[ordered]@{schema_version=1;ok=`$false;status=`$status;error_code=`$code;safety_preserved=`$true;collection_preserved=`$true;alarms_preserved=`$true;data_preserved=`$true}|ConvertTo-Json -Compress;exit 2}
"@
}

function New-CleanupScript([switch]$RetainReservation) {
  $operationExpression = ConvertTo-PowerShellUtf8Expression $OperationId
  $siteExpression = ConvertTo-PowerShellUtf8Expression $SiteId
  $digestExpression = ConvertTo-PowerShellUtf8Expression ([string]$script:BundleIdentity.sums_sha256)
  $bundleBytesExpression = ([long]$script:BundleIdentity.bundle_bytes).ToString(
    [Globalization.CultureInfo]::InvariantCulture
  )
  $reservationCleanup = if ($RetainReservation) {
    ""
  } else { "Remove-Item -LiteralPath `$reservation -Force;" }
  $reservationRemoved = if ($RetainReservation) {
    "(Test-Path -LiteralPath `$reservation)"
  } else { "(-not(Test-Path -LiteralPath `$reservation))" }
  return @"
`$ErrorActionPreference='Stop';`$operation=$operationExpression;`$site=$siteExpression;`$digest=$digestExpression;[long]`$bundleBytes=$bundleBytesExpression;`$parent='$($script:RemoteIncomingParent)';`$bundle=Join-Path `$parent `$operation;`$reservation=Join-Path `$parent ("`$operation.reservation.json");`$lockPath='C:\ProgramData\Ruisheng\entitlement-bootstrap.lock';`$allowed=@('S-1-5-18','S-1-5-32-544')
function Sid(`$identity){if(`$identity-is[Security.Principal.IdentityReference]){return `$identity.Translate([Security.Principal.SecurityIdentifier]).Value};return(New-Object Security.Principal.NTAccount([string]`$identity)).Translate([Security.Principal.SecurityIdentifier]).Value}
function AssertItem([string]`$path,[string]`$kind){`$type=if(`$kind-eq'File'){'Leaf'}else{'Container'};if(-not(Test-Path -LiteralPath `$path -PathType `$type)){throw 'bootstrap_cleanup_path_missing'};`$item=Get-Item -LiteralPath `$path -Force;if((`$item.Attributes-band[IO.FileAttributes]::ReparsePoint)-ne 0){throw 'bootstrap_reparse_point'};`$acl=Get-Acl -LiteralPath `$path;if(`$allowed-notcontains(Sid `$acl.Owner)-or-not `$acl.AreAccessRulesProtected){throw 'bootstrap_acl_invalid'};`$rules=@(`$acl.Access);if(`$rules.Count-ne 2){throw 'bootstrap_acl_invalid'};`$seen=@{};foreach(`$rule in `$rules){`$sid=Sid `$rule.IdentityReference;`$inherit=if(`$kind-eq'Directory'){[Security.AccessControl.InheritanceFlags]::ContainerInherit-bor[Security.AccessControl.InheritanceFlags]::ObjectInherit}else{[Security.AccessControl.InheritanceFlags]::None};if(`$allowed-notcontains`$sid-or`$seen.ContainsKey(`$sid)-or`$rule.IsInherited-or`$rule.AccessControlType-ne[Security.AccessControl.AccessControlType]::Allow-or`$rule.FileSystemRights-ne[Security.AccessControl.FileSystemRights]::FullControl-or`$rule.InheritanceFlags-ne`$inherit-or`$rule.PropagationFlags-ne[Security.AccessControl.PropagationFlags]::None){throw 'bootstrap_acl_invalid'};`$seen[`$sid]=`$true}}
function Protect([string]`$path,[string]`$kind){`$acl=if(`$kind-eq'Directory'){New-Object Security.AccessControl.DirectorySecurity}else{New-Object Security.AccessControl.FileSecurity};`$acl.SetAccessRuleProtection(`$true,`$false);`$acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')));foreach(`$sidText in `$allowed){`$inherit=if(`$kind-eq'Directory'){[Security.AccessControl.InheritanceFlags]::ContainerInherit-bor[Security.AccessControl.InheritanceFlags]::ObjectInherit}else{[Security.AccessControl.InheritanceFlags]::None};[void]`$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier(`$sidText)),[Security.AccessControl.FileSystemRights]::FullControl,`$inherit,[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow))};Set-Acl -LiteralPath `$path -AclObject `$acl;AssertItem `$path `$kind}
function EnterLock{`$stream=`$null;try{if(Test-Path -LiteralPath `$lockPath){AssertItem `$lockPath 'File';`$stream=[IO.File]::Open(`$lockPath,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)}else{`$stream=[IO.File]::Open(`$lockPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None);Protect `$lockPath 'File'};return `$stream}catch{if(`$null-ne`$stream){`$stream.Dispose()};if(`$_.Exception.Message-match'^bootstrap_'){throw};throw 'bootstrap_busy'}}
function ReadBounded([string]`$path,[long]`$maximum){`$stream=[IO.File]::Open(`$path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);try{`$memory=New-Object IO.MemoryStream;try{`$buffer=New-Object byte[] 512;while((`$count=`$stream.Read(`$buffer,0,`$buffer.Length))-gt 0){if(`$memory.Length+`$count-gt `$maximum){throw 'bootstrap_reservation_invalid'};`$memory.Write(`$buffer,0,`$count)};return [Text.Encoding]::ASCII.GetString(`$memory.ToArray())}finally{`$memory.Dispose()}}finally{`$stream.Dispose()}}
AssertItem 'C:\ProgramData\Ruisheng' 'Directory';AssertItem `$parent 'Directory';`$lock=EnterLock
try{AssertItem `$reservation 'File';`$expected='{"bundle_bytes":'+`$bundleBytes.ToString([Globalization.CultureInfo]::InvariantCulture)+',"operation_id":"'+`$operation+'","schema_version":1,"site_id":"'+`$site+'","sums_sha256":"'+`$digest+'"}' + "`n";if((ReadBounded `$reservation 512)-cne `$expected){throw 'bootstrap_operation_conflict'};if(Test-Path -LiteralPath `$bundle){AssertItem `$bundle 'Directory';Remove-Item -LiteralPath `$bundle -Recurse -Force;if(Test-Path -LiteralPath `$bundle){throw 'bootstrap_cleanup_failed'}};$reservationCleanup`$removed=(-not(Test-Path -LiteralPath `$bundle))-and$reservationRemoved;[ordered]@{schema_version=1;ok=`$true;status='cleaned';operation_id=`$operation;removed=`$removed}|ConvertTo-Json -Compress}finally{if(`$null-ne`$lock){`$lock.Dispose()}}
"@
}

function Invoke-BundleUpload {
  $sources = @($script:ExpectedBundleFiles | ForEach-Object { Join-Path $script:BundlePath $_ })
  $destination = "$($script:ScpTarget)`:C:/ProgramData/Ruisheng/entitlement-bootstrap-incoming/$OperationId/"
  $arguments = @("-q") + (Get-SshOptions) + $sources + @($destination)
  Assert-FixedExecutable $script:ScpPath "Microsoft"
  return Invoke-BoundedProcess -FilePath $script:ScpPath -Arguments $arguments `
    -TimeoutSeconds $TransportTimeoutSeconds
}

function Assert-PrepareReceipt($Result) {
  Assert-ExactFields $Result @("schema_version", "ok", "status", "operation_id", "site_id", "incoming_path") "prepare_receipt_invalid"
  $path = "$($script:RemoteIncomingParent)\$OperationId"
  if ($Result.schema_version -ne 1 -or -not $Result.ok -or
      [string]$Result.status -cne "prepared" -or [string]$Result.operation_id -cne $OperationId -or
      [string]$Result.site_id -cne $SiteId -or [string]$Result.incoming_path -cne $path) {
    throw "prepare_receipt_invalid"
  }
}

function Assert-RuntimeReceipt($Result) {
  $fields = @(
    "schema_version", "ok", "status", "operation_id", "site_id", "entitlement_sha256",
    "verifier_sha256", "public_key_sha256", "vendor_archive_sha256",
    "runtime_epoch", "entitlement_key_generation",
    "services_restarted", "device_configuration_changed"
  )
  Assert-ExactFields $Result $fields "runtime_receipt_invalid"
  if ($Result.schema_version -ne 1 -or -not $Result.ok -or
      [string]$Result.status -cne "runtime_installed" -or
      [string]$Result.operation_id -cne $OperationId -or [string]$Result.site_id -cne $SiteId -or
      [string]$Result.entitlement_sha256 -cne [string]$script:BundleIdentity.entitlement_sha256 -or
      [string]$Result.verifier_sha256 -cne [string]$script:BundleIdentity.verifier_sha256 -or
      [string]$Result.public_key_sha256 -cne [string]$script:BundleIdentity.public_key_sha256 -or
      [string]$Result.vendor_archive_sha256 -cne [string]$script:BundleIdentity.vendor_archive_sha256 -or
      $Result.runtime_epoch -isnot [ValueType] -or
      [long]$Result.runtime_epoch -ne [long]$script:BundleIdentity.runtime_epoch -or
      $Result.entitlement_key_generation -isnot [ValueType] -or
      [long]$Result.entitlement_key_generation -ne [long]$script:BundleIdentity.entitlement_key_generation -or
      $Result.services_restarted -isnot [bool] -or $Result.services_restarted -or
      $Result.device_configuration_changed -isnot [bool] -or $Result.device_configuration_changed) {
    throw "runtime_receipt_invalid"
  }
}

function Invoke-BestEffortRuntimeCleanup([switch]$RetainReservation) {
  $cleanup = Invoke-SshScript (New-CleanupScript -RetainReservation:$RetainReservation)
  if ($cleanup.ExitCode -ne 0 -or -not [string]::IsNullOrWhiteSpace($cleanup.Stderr)) {
    throw "runtime_cleanup_failed"
  }
  $result = ConvertFrom-ExactJson $cleanup.Stdout.Trim() "cleanup_receipt_invalid"
  Assert-ExactFields $result @("schema_version", "ok", "status", "operation_id", "removed") "cleanup_receipt_invalid"
  if ($result.schema_version -ne 1 -or -not $result.ok -or
      [string]$result.status -cne "cleaned" -or [string]$result.operation_id -cne $OperationId -or
      $result.removed -isnot [bool] -or -not $result.removed) { throw "cleanup_receipt_invalid" }
}

function Test-ExplicitRejection($Native) {
  if ($Native.ExitCode -ne 2 -or -not [string]::IsNullOrWhiteSpace($Native.Stderr)) { return $null }
  try {
    $result = ConvertFrom-ExactJson $Native.Stdout.Trim() "target_rejection_invalid"
    Assert-ExactFields $result @(
      "schema_version", "ok", "status", "error_code", "safety_preserved",
      "collection_preserved", "alarms_preserved", "data_preserved"
    ) "target_rejection_invalid"
    if ($result.schema_version -ne 1 -or $result.ok -isnot [bool] -or $result.ok -or
        [string]$result.status -cne "rejected" -or
        [string]$result.error_code -notmatch '^[A-Za-z0-9_]+$') { return $null }
    return [string]$result.error_code
  } catch { return $null }
}

function Write-LocalAudit([string]$Result, [string]$ErrorCode = "") {
  New-Item -ItemType Directory -Force -Path $AuditDirectory | Out-Null
  $record = [ordered]@{
    schema_version = 1
    recorded_at = [DateTimeOffset]::UtcNow.ToString("o")
    action = "InstallRuntime"
    target = $Target
    site_id = $SiteId
    operation_id = $OperationId
    sums_sha256 = if ($null -ne $script:BundleIdentity) { $script:BundleIdentity.sums_sha256 } else { "" }
    result = $Result
    error_code = $ErrorCode
  }
  Add-Content -LiteralPath (Join-Path $AuditDirectory "entitlement-runtime-install.jsonl") `
    -Value ($record | ConvertTo-Json -Compress) -Encoding UTF8
  $script:AuditWritten = $true
}

$finalOutput = $null
$finalExitCode = 0
try {
  if ($Target -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}@(?:[A-Za-z0-9][A-Za-z0-9._:-]{0,253}|\[[A-Fa-f0-9:]+\])$') {
    throw "target_invalid"
  }
  Assert-TailscaleTarget
  if ($SiteId -notmatch '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$') { throw "site_id_invalid" }
  $OperationId = $OperationId.ToLowerInvariant()
  if ($OperationId -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') {
    throw "operation_id_invalid"
  }
  $BundlePath = New-ProtectedBundleSnapshot
  $script:BundleIdentity = Get-LocalBundleIdentity
  if ($DryRun) {
    $finalOutput = [ordered]@{
      schema_version = 1
      ok = $true
      status = "planned"
      target = $Target
      site_id = $SiteId
      operation_id = $OperationId
      sums_sha256 = $script:BundleIdentity.sums_sha256
      runtime_epoch = $script:BundleIdentity.runtime_epoch
      entitlement_key_generation = $script:BundleIdentity.entitlement_key_generation
      services_restarted = $false
      device_configuration_changed = $false
    }
  } else {
    if (-not $Approved) { throw "approval_required" }
    $prepareNative = Invoke-SshScript (New-PrepareScript)
    if ($prepareNative.ExitCode -ne 0 -or -not [string]::IsNullOrWhiteSpace($prepareNative.Stderr)) {
      throw "prepare_transport_failed"
    }
    $prepare = ConvertFrom-ExactJson $prepareNative.Stdout.Trim() "prepare_receipt_invalid"
    Assert-PrepareReceipt $prepare
    $upload = Invoke-BundleUpload
    if ($upload.ExitCode -ne 0 -or -not [string]::IsNullOrWhiteSpace($upload.Stdout) -or
        -not [string]::IsNullOrWhiteSpace($upload.Stderr)) {
      Invoke-BestEffortRuntimeCleanup
      throw "bundle_upload_failed"
    }
    $script:ExecutionDispatched = $true
    $executeNative = Invoke-SshScript (New-ExecuteScript)
    if ($executeNative.ExitCode -ne 0) {
      $rejectCode = Test-ExplicitRejection $executeNative
      if ($null -ne $rejectCode) {
        $script:ExplicitRejection = $true
        Invoke-BestEffortRuntimeCleanup -RetainReservation
        throw $rejectCode
      }
      throw "runtime_execution_transport_failed"
    }
    if (-not [string]::IsNullOrWhiteSpace($executeNative.Stderr)) {
      throw "runtime_execution_transport_failed"
    }
    $receipt = ConvertFrom-ExactJson $executeNative.Stdout.Trim() "runtime_receipt_invalid"
    Assert-RuntimeReceipt $receipt
    Invoke-BestEffortRuntimeCleanup
    Remove-ProtectedBundleSnapshot
    Write-LocalAudit "runtime_installed"
    $finalOutput = $receipt
  }
} catch {
  $code = Get-SafeCode ([string]$_.Exception.Message)
  try { Remove-ProtectedBundleSnapshot }
  catch { $code = Get-SafeCode ([string]$_.Exception.Message) }
  if (-not $DryRun -and -not $script:AuditWritten) {
    $classification = if ($script:ExecutionDispatched -and -not $script:ExplicitRejection) {
      "ambiguous_commit"
    } else { "failed" }
    try { Write-LocalAudit $classification $code } catch { $code = "local_audit_failed" }
  }
  $status = if ($script:ExecutionDispatched -and -not $script:ExplicitRejection) {
    "uncertain"
  } else { "rejected" }
  $finalOutput = [ordered]@{
    schema_version = 1
    ok = $false
    status = $status
    error_code = $code
    safety_preserved = $true
    collection_preserved = $true
    alarms_preserved = $true
    data_preserved = $true
  }
  $finalExitCode = 2
}

try {
  Remove-ProtectedBundleSnapshot
} catch {
  $cleanupStatus = if ($script:ExecutionDispatched -and -not $script:ExplicitRejection) {
    "uncertain"
  } else { "rejected" }
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
