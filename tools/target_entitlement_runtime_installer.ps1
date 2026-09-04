[CmdletBinding()]
param(
  [Parameter(Mandatory)][string]$OperationId,
  [Parameter(Mandatory)][string]$SiteId
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$ExpectedFiles = @(
  "entitlement-public-key",
  "entitlement.py",
  "runtime-metadata.json",
  "target_entitlement_runtime_installer.ps1",
  "target_entitlement_verifier.ps1",
  "vendor-manifest.sha256",
  "vendor.zip"
)
$AllowedSids = @("S-1-5-18", "S-1-5-32-544")
$IncomingParent = "C:\ProgramData\Ruisheng\entitlement-bootstrap-incoming"
$TrustRoot = "C:\ProgramData\Ruisheng\trust"
$AllowedSigners = Join-Path $TrustRoot "release-allowed-signers"
$ReleaseFingerprint = Join-Path $TrustRoot "release-key-fingerprint"
$SshKeygen = "C:\Windows\System32\OpenSSH\ssh-keygen.exe"
$BinRoot = "C:\ProgramData\Ruisheng\bin"
$RuntimeRoot = "C:\ProgramData\Ruisheng\entitlement-runtime"
$BootstrapLockPath = "C:\ProgramData\Ruisheng\entitlement-bootstrap.lock"
$BootstrapJournalPath = "C:\ProgramData\Ruisheng\entitlement-bootstrap-journal.json"
$RuntimeStatePath = "C:\ProgramData\Ruisheng\entitlement-runtime-state.json"
$RuntimeUseLockPath = "C:\ProgramData\Ruisheng\entitlement-runtime-use.lock"
$PythonPath = "C:\ProgramData\Ruisheng\runtime\python.exe"
$SiteIdentityPath = Join-Path $TrustRoot "entitlement-site-id"
$EntitlementRoot = "C:\ProgramData\Ruisheng\entitlements"
$EntitlementTransactionLockPath = Join-Path $EntitlementRoot ".transaction.lock"
$RuntimeOperationsRoot = "C:\ProgramData\Ruisheng\entitlement-runtime-operations"
$MaxBundleFileBytes = 64MB
$MaxBundleBytes = 128MB
$MaxSumsBytes = 1MB
$MaxSignatureBytes = 64KB
$MaxVendorFiles = 10000
$MaxVendorBytes = 512MB
$MaxTreeItems = 20000
$MaxRuntimeOperationReceipts = 2048
$MaxRuntimeOperationBytes = 16MB
$MaxRuntimeOperationReceiptBytes = 8192

function Fail([string]$Code) { throw $Code }

function Get-OrdinalSortedStrings([string[]]$Values) {
  [string[]]$copy = @($Values)
  [Array]::Sort($copy, [StringComparer]::Ordinal)
  return $copy
}

function Get-Sid($Identity) {
  try {
    $reference = if ($Identity -is [Security.Principal.IdentityReference]) {
      $Identity
    } else {
      New-Object Security.Principal.NTAccount([string]$Identity)
    }
    return $reference.Translate([Security.Principal.SecurityIdentifier]).Value
  } catch { Fail "bootstrap_acl_identity_invalid" }
}

function Assert-ProtectedItem([string]$Path, [ValidateSet("File", "Directory")][string]$Kind) {
  $pathType = if ($Kind -eq "File") { "Leaf" } else { "Container" }
  if (-not (Test-Path -LiteralPath $Path -PathType $pathType)) { Fail "bootstrap_path_missing" }
  $item = Get-Item -LiteralPath $Path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    Fail "bootstrap_reparse_point"
  }
  $acl = Get-Acl -LiteralPath $Path
  if ($AllowedSids -notcontains (Get-Sid $acl.Owner) -or -not $acl.AreAccessRulesProtected) {
    Fail "bootstrap_acl_invalid"
  }
  $rules = @($acl.Access)
  if ($rules.Count -ne $AllowedSids.Count) { Fail "bootstrap_acl_invalid" }
  $seen = @{}
  foreach ($rule in $rules) {
    $sid = Get-Sid $rule.IdentityReference
    if ($AllowedSids -notcontains $sid -or $seen.ContainsKey($sid) -or $rule.IsInherited -or
        $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
        $rule.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl -or
        $rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None) {
      Fail "bootstrap_acl_invalid"
    }
    $expectedInheritance = if ($Kind -eq "Directory") {
      [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    } else { [Security.AccessControl.InheritanceFlags]::None }
    if ($rule.InheritanceFlags -ne $expectedInheritance) { Fail "bootstrap_acl_invalid" }
    $seen[$sid] = $true
  }
  if (@($AllowedSids | Where-Object { -not $seen.ContainsKey($_) }).Count -ne 0) {
    Fail "bootstrap_acl_invalid"
  }
}

function Set-ProtectedAcl([string]$Path, [ValidateSet("File", "Directory")][string]$Kind) {
  $security = if ($Kind -eq "Directory") {
    New-Object Security.AccessControl.DirectorySecurity
  } else { New-Object Security.AccessControl.FileSecurity }
  $security.SetAccessRuleProtection($true, $false)
  $security.SetOwner((New-Object Security.Principal.SecurityIdentifier("S-1-5-32-544")))
  foreach ($sidText in $AllowedSids) {
    $inheritance = if ($Kind -eq "Directory") {
      [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    } else { [Security.AccessControl.InheritanceFlags]::None }
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
      (New-Object Security.Principal.SecurityIdentifier($sidText)),
      [Security.AccessControl.FileSystemRights]::FullControl,
      $inheritance,
      [Security.AccessControl.PropagationFlags]::None,
      [Security.AccessControl.AccessControlType]::Allow
    )
    [void]$security.AddAccessRule($rule)
  }
  Set-Acl -LiteralPath $Path -AclObject $security
  Assert-ProtectedItem $Path $Kind
}

function Ensure-ProtectedDirectory([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) {
    [void](New-Item -ItemType Directory -Path $Path)
    Set-ProtectedAcl $Path "Directory"
  } else { Assert-ProtectedItem $Path "Directory" }
}

function Write-ProtectedAsciiAtomic([string]$Path, [string]$Text) {
  $temporary = "$Path.$([Guid]::NewGuid().ToString('N')).tmp"
  try {
    $bytes = [Text.Encoding]::ASCII.GetBytes($Text)
    $stream = [IO.File]::Open(
      $temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None
    )
    try {
      $stream.Write($bytes, 0, $bytes.Length)
      $stream.Flush($true)
    } finally { $stream.Dispose() }
    Set-ProtectedAcl $temporary "File"
    Move-Item -LiteralPath $temporary -Destination $Path -Force
    Assert-ProtectedItem $Path "File"
  } finally {
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
  }
}

function Ensure-SiteIdentity([string]$ExpectedSiteId) {
  $expected = "$ExpectedSiteId`n"
  if (Test-Path -LiteralPath $SiteIdentityPath) {
    Assert-ProtectedItem $SiteIdentityPath "File"
    if ((Get-StrictAscii $SiteIdentityPath) -cne $expected) { Fail "bootstrap_site_mismatch" }
    return
  }
  $temporary = "$SiteIdentityPath.$([Guid]::NewGuid().ToString('N')).tmp"
  try {
    $bytes = [Text.Encoding]::ASCII.GetBytes($expected)
    $stream = [IO.File]::Open(
      $temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None
    )
    try {
      $stream.Write($bytes, 0, $bytes.Length)
      $stream.Flush($true)
    } finally { $stream.Dispose() }
    Set-ProtectedAcl $temporary "File"
    Move-Item -LiteralPath $temporary -Destination $SiteIdentityPath
    Assert-ProtectedItem $SiteIdentityPath "File"
  } finally {
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
  }
}

function Get-StrictAscii([string]$Path, [int64]$MaxBytes = 1MB) {
  $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
  if ($item.PSIsContainer -or $item.Length -le 0 -or $item.Length -gt $MaxBytes) {
    Fail "bootstrap_text_size_invalid"
  }
  try {
    $encoding = [Text.Encoding]::GetEncoding(
      20127, [Text.EncoderFallback]::ExceptionFallback,
      [Text.DecoderFallback]::ExceptionFallback
    )
    $stream = [IO.File]::Open(
      $Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
    )
    try {
      $memory = New-Object IO.MemoryStream
      try {
        $buffer = New-Object byte[] 4096
        while (($count = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
          if ($memory.Length + $count -gt $MaxBytes) { Fail "bootstrap_text_size_invalid" }
          $memory.Write($buffer, 0, $count)
        }
        return $encoding.GetString($memory.ToArray())
      }
      finally { $memory.Dispose() }
    }
    finally { $stream.Dispose() }
  } catch {
    if ([string]$_.Exception.Message -eq "bootstrap_text_size_invalid") { throw }
    Fail "bootstrap_text_encoding_invalid"
  }
}

function Assert-SignedExecutable(
  [string]$Path,
  [string]$ExpectedName,
  [string]$Organization
) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    Fail "bootstrap_verification_input_invalid"
  }
  $item = Get-Item -LiteralPath $Path -Force
  if ($item.Name -cne $ExpectedName -or
      ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    Fail "bootstrap_verification_input_invalid"
  }
  try { $signature = Microsoft.PowerShell.Security\Get-AuthenticodeSignature -LiteralPath $Path }
  catch { Fail "bootstrap_executable_signature_invalid" }
  if ([string]$signature.Status -cne "Valid" -or $null -eq $signature.SignerCertificate -or
      [string]$signature.SignerCertificate.Subject -notmatch
        "(?:^|,\s*)O=$([regex]::Escape($Organization))(?:,|$)") {
    Fail "bootstrap_executable_signature_invalid"
  }
}

function Assert-ReleaseTrust {
  Assert-ProtectedItem $AllowedSigners "File"
  Assert-ProtectedItem $ReleaseFingerprint "File"
  $allowedText = Get-StrictAscii $AllowedSigners 4096
  $fingerprintText = Get-StrictAscii $ReleaseFingerprint 256
  $allowedMatch = [regex]::Match(
    $allowedText, '^ruisheng-release ssh-ed25519 ([A-Za-z0-9+/]+={0,2})\n$',
    [Text.RegularExpressions.RegexOptions]::CultureInvariant
  )
  if (-not $allowedMatch.Success -or $fingerprintText -cnotmatch '^SHA256:[A-Za-z0-9+/]{43}\n$') {
    Fail "bootstrap_release_trust_invalid"
  }
  try { $keyBytes = [Convert]::FromBase64String($allowedMatch.Groups[1].Value) }
  catch { Fail "bootstrap_release_trust_invalid" }
  $sha = [Security.Cryptography.SHA256]::Create()
  try {
    $actual = "SHA256:" + [Convert]::ToBase64String($sha.ComputeHash($keyBytes)).TrimEnd('=') + "`n"
  } finally { $sha.Dispose() }
  if ($actual -cne $fingerprintText) { Fail "bootstrap_release_trust_invalid" }
}

function Assert-EntitlementPublicKey([string]$Path) {
  $text = Get-StrictAscii $Path
  if ($text -cnotmatch '^ssh-ed25519 ([A-Za-z0-9+/]+={0,2}) ([A-Za-z0-9][A-Za-z0-9._:-]{0,127})\n$') {
    Fail "bootstrap_entitlement_key_invalid"
  }
  try { $blob = [Convert]::FromBase64String($Matches[1]) }
  catch { Fail "bootstrap_entitlement_key_invalid" }
  $expectedPrefix = [byte[]](0, 0, 0, 11) + [Text.Encoding]::ASCII.GetBytes("ssh-ed25519") +
    [byte[]](0, 0, 0, 32)
  if ($blob.Length -ne 51) { Fail "bootstrap_entitlement_key_invalid" }
  for ($index = 0; $index -lt $expectedPrefix.Length; $index++) {
    if ($blob[$index] -ne $expectedPrefix[$index]) { Fail "bootstrap_entitlement_key_invalid" }
  }
}

function Enter-BootstrapLock {
  $stream = $null
  try {
    if (Test-Path -LiteralPath $BootstrapLockPath) {
      Assert-ProtectedItem $BootstrapLockPath "File"
      $stream = [IO.File]::Open(
        $BootstrapLockPath, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None
      )
    } else {
      $stream = [IO.File]::Open(
        $BootstrapLockPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::None
      )
      Set-ProtectedAcl $BootstrapLockPath "File"
    }
    return $stream
  } catch {
    if ($null -ne $stream) { $stream.Dispose() }
    if ($_.Exception.Message -match '^bootstrap_') { throw }
    Fail "bootstrap_busy"
  }
}

function Enter-EntitlementTransactionLock {
  $stream = $null
  try {
    Ensure-ProtectedDirectory $EntitlementRoot
    if (Test-Path -LiteralPath $EntitlementTransactionLockPath) {
      Assert-ProtectedItem $EntitlementTransactionLockPath "File"
      $stream = [IO.File]::Open(
        $EntitlementTransactionLockPath, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::ReadWrite
      )
    } else {
      $stream = [IO.File]::Open(
        $EntitlementTransactionLockPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::ReadWrite
      )
      $stream.WriteByte(0)
      $stream.Flush($true)
      Set-ProtectedAcl $EntitlementTransactionLockPath "File"
    }
    if ($stream.Length -eq 0) {
      $stream.WriteByte(0)
      $stream.Flush($true)
    }
    $stream.Lock(0, 1)
    return $stream
  } catch {
    if ($null -ne $stream) { $stream.Dispose() }
    if ($_.Exception.Message -match '^bootstrap_') { throw }
    Fail "bootstrap_entitlement_busy"
  }
}

function Exit-EntitlementTransactionLock($Stream) {
  if ($null -eq $Stream) { return }
  try { $Stream.Unlock(0, 1) } catch { }
  $Stream.Dispose()
}

function Enter-RuntimeUseLock {
  if (-not (Test-Path -LiteralPath $RuntimeUseLockPath)) {
    Write-ProtectedAsciiAtomic $RuntimeUseLockPath "0`n"
  } else {
    Assert-ProtectedItem $RuntimeUseLockPath "File"
  }
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
  while ([DateTimeOffset]::UtcNow -lt $deadline) {
    $stream = $null
    try {
      $stream = [IO.File]::Open(
        $RuntimeUseLockPath, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::ReadWrite
      )
      $stream.Lock(1, 1)
      return $stream
    } catch {
      if ($null -ne $stream) { $stream.Dispose() }
      Start-Sleep -Milliseconds 50
    }
  }
  Fail "bootstrap_runtime_busy"
}

function Exit-RuntimeUseLock($Stream) {
  if ($null -eq $Stream) { return }
  try { $Stream.Unlock(1, 1) } catch { }
  $Stream.Dispose()
}

function Get-AuthenticatedSums([string]$BundleRoot) {
  $sumsPath = Join-Path $BundleRoot "SHA256SUMS"
  $signaturePath = Join-Path $BundleRoot "SHA256SUMS.sig"
  Assert-ReleaseTrust
  Assert-ProtectedItem $sumsPath "File"
  Assert-ProtectedItem $signaturePath "File"
  Assert-SignedExecutable $SshKeygen "ssh-keygen.exe" "Microsoft Corporation"
  foreach ($path in @($sumsPath, $signaturePath)) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    if (-not $item.PSIsContainer -and
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) { continue }
    Fail "bootstrap_verification_input_invalid"
  }
  $sumsLength = (Get-Item -LiteralPath $sumsPath -Force).Length
  $signatureLength = (Get-Item -LiteralPath $signaturePath -Force).Length
  if ($sumsLength -le 0 -or $sumsLength -gt $MaxSumsBytes -or
      $signatureLength -le 0 -or $signatureLength -gt $MaxSignatureBytes) {
    Fail "bootstrap_signature_metadata_size_invalid"
  }
  $sumsText = Get-StrictAscii $sumsPath $MaxSumsBytes
  if (-not $sumsText.EndsWith("`n") -or $sumsText.Contains("`r")) {
    Fail "bootstrap_sums_not_canonical"
  }
  $sums = @{}
  $ordered = New-Object Collections.Generic.List[string]
  foreach ($line in @($sumsText.Substring(0, $sumsText.Length - 1).Split("`n"))) {
    if ($line -notmatch '^([0-9a-f]{64})  ([A-Za-z0-9_.-]+)$') {
      Fail "bootstrap_sums_invalid"
    }
    $relative = $Matches[2]
    if ($sums.ContainsKey($relative)) { Fail "bootstrap_sums_invalid" }
    $sums[$relative] = $Matches[1]
    [void]$ordered.Add($relative)
  }
  [string[]]$sorted = @($ordered)
  [Array]::Sort($sorted, [StringComparer]::Ordinal)
  if (($ordered -join "`n") -cne ($sorted -join "`n") -or
      ($sorted -join "`n") -cne ($ExpectedFiles -join "`n")) {
    Fail "bootstrap_sums_file_set_invalid"
  }

  $start = New-Object Diagnostics.ProcessStartInfo
  $start.FileName = $SshKeygen
  $start.Arguments = "-Y verify -f `"$AllowedSigners`" -I ruisheng-release " +
    "-n ruisheng-entitlement-runtime-v1 -s `"$signaturePath`""
  $start.UseShellExecute = $false
  $start.CreateNoWindow = $true
  $start.RedirectStandardInput = $true
  $start.RedirectStandardOutput = $true
  $start.RedirectStandardError = $true
  $process = New-Object Diagnostics.Process
  $process.StartInfo = $start
  try {
    if (-not $process.Start()) { Fail "bootstrap_signature_verifier_failed" }
    $process.StandardInput.Write($sumsText)
    $process.StandardInput.Close()
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(30000)) {
      try { $process.Kill() } catch { }
      Fail "bootstrap_signature_timeout"
    }
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    if ($stdout.Length -gt 65536 -or $stderr.Length -gt 65536 -or $process.ExitCode -ne 0) {
      Fail "bootstrap_signature_invalid"
    }
  } finally {
    if (-not $process.HasExited) { try { $process.Kill() } catch { } }
    $process.Dispose()
  }
  $sha = [Security.Cryptography.SHA256]::Create()
  try {
    $script:BundleSumsSha256 = ([BitConverter]::ToString(
      $sha.ComputeHash([Text.Encoding]::ASCII.GetBytes($sumsText))
    )).Replace("-", "").ToLowerInvariant()
  } finally { $sha.Dispose() }
  return $sums
}

function Assert-Bundle([string]$BundleRoot, [hashtable]$Sums) {
  $actual = @(Get-ChildItem -LiteralPath $BundleRoot -Force)
  $allExpected = @($ExpectedFiles) + @("SHA256SUMS", "SHA256SUMS.sig")
  [string[]]$actualNames = @($actual.Name)
  [string[]]$expectedNames = @($allExpected)
  [Array]::Sort($actualNames, [StringComparer]::Ordinal)
  [Array]::Sort($expectedNames, [StringComparer]::Ordinal)
  if (($actualNames -join "`n") -cne ($expectedNames -join "`n")) {
    Fail "bootstrap_bundle_file_set_invalid"
  }
  [long]$bundleBytes = 0
  foreach ($item in $actual) {
    if ($item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $item.Length -le 0 -or $item.Length -gt $MaxBundleFileBytes) {
      Fail "bootstrap_bundle_file_invalid"
    }
    $bundleBytes += [long]$item.Length
    if ($bundleBytes -gt $MaxBundleBytes) { Fail "bootstrap_bundle_size_invalid" }
    Assert-ProtectedItem $item.FullName "File"
  }
  foreach ($relative in $ExpectedFiles) {
    $actualHash = (Get-FileHash -LiteralPath (Join-Path $BundleRoot $relative) `
      -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -cne $Sums[$relative]) { Fail "bootstrap_bundle_hash_invalid" }
  }
}

function Expand-AuthenticatedVendor([string]$ZipPath, [string]$Destination) {
  Add-Type -AssemblyName System.IO.Compression
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [void](New-Item -ItemType Directory -Path $Destination)
  $archive = [IO.Compression.ZipFile]::OpenRead($ZipPath)
  $seen = @{}
  [long]$total = 0
  try {
    foreach ($entry in $archive.Entries) {
      $relative = $entry.FullName.Replace('\', '/')
      if ($relative -notmatch '^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$' -or
          $entry.FullName.EndsWith('/') -or $relative.Split('/') -contains "." -or
          $relative.Split('/') -contains ".." -or $seen.ContainsKey($relative)) {
        Fail "bootstrap_vendor_archive_invalid"
      }
      $seen[$relative] = $true
      $total += $entry.Length
      if ($seen.Count -gt $MaxVendorFiles -or $total -gt $MaxVendorBytes) {
        Fail "bootstrap_vendor_archive_limit"
      }
      $destinationPath = Join-Path $Destination $relative.Replace('/', '\')
      $parent = Split-Path -Parent $destinationPath
      if (-not (Test-Path -LiteralPath $parent)) {
        [void](New-Item -ItemType Directory -Path $parent -Force)
      }
      [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destinationPath, $false)
    }
  } finally { $archive.Dispose() }
  if ($seen.Count -eq 0) { Fail "bootstrap_vendor_archive_invalid" }
}

function Get-RuntimeMetadata([string]$Path, $Sums) {
  $item = Get-Item -LiteralPath $Path -Force
  if ($item.Length -le 0 -or $item.Length -gt 1024) { Fail "bootstrap_runtime_metadata_invalid" }
  $text = Get-StrictAscii $Path 1024
  $match = [regex]::Match(
    $text,
    '^\{"entitlement_key_generation":([1-9][0-9]*),"runtime_epoch":([1-9][0-9]*),"schema_version":1\}\n$',
    [Text.RegularExpressions.RegexOptions]::CultureInvariant
  )
  if (-not $match.Success) { Fail "bootstrap_runtime_metadata_invalid" }
  try {
    $keyGeneration = [long]::Parse($match.Groups[1].Value, [Globalization.CultureInfo]::InvariantCulture)
    $runtimeEpoch = [long]::Parse($match.Groups[2].Value, [Globalization.CultureInfo]::InvariantCulture)
  } catch { Fail "bootstrap_runtime_metadata_invalid" }
  if ($keyGeneration -ne 1) { Fail "bootstrap_entitlement_key_generation_unsupported" }
  foreach ($name in @(
      "entitlement-public-key", "entitlement.py", "target_entitlement_verifier.ps1",
      "vendor-manifest.sha256"
    )) {
    if ([string]$Sums[$name] -notmatch '^[0-9a-f]{64}$') {
      Fail "bootstrap_runtime_metadata_invalid"
    }
  }
  Assert-SignedExecutable $PythonPath "python.exe" "Python Software Foundation"
  return [ordered]@{
    schema_version = 1
    runtime_epoch = $runtimeEpoch
    entitlement_key_generation = $keyGeneration
    public_key_sha256 = [string]$Sums["entitlement-public-key"]
    entitlement_sha256 = [string]$Sums["entitlement.py"]
    verifier_sha256 = [string]$Sums["target_entitlement_verifier.ps1"]
    vendor_manifest_sha256 = [string]$Sums["vendor-manifest.sha256"]
    python_sha256 = (Get-FileHash -LiteralPath $PythonPath -Algorithm SHA256).Hash.ToLowerInvariant()
    bundle_sums_sha256 = $script:BundleSumsSha256
  }
}

function Get-ExistingRuntimeState {
  if (-not (Test-Path -LiteralPath $RuntimeStatePath)) { return $null }
  Assert-ProtectedItem $RuntimeStatePath "File"
  $item = Get-Item -LiteralPath $RuntimeStatePath -Force
  if ($item.Length -le 0 -or $item.Length -gt 4096) { Fail "bootstrap_runtime_state_invalid" }
  $text = Get-StrictAscii $RuntimeStatePath
  try { $state = $text | ConvertFrom-Json } catch { Fail "bootstrap_runtime_state_invalid" }
  $fields = @(
    "schema_version", "runtime_epoch", "entitlement_key_generation", "public_key_sha256",
    "entitlement_sha256", "verifier_sha256", "vendor_manifest_sha256", "python_sha256",
    "bundle_sums_sha256"
  )
  [string[]]$actualFields = @($state.PSObject.Properties.Name)
  [string[]]$expectedFields = @($fields)
  [Array]::Sort($actualFields, [StringComparer]::Ordinal)
  [Array]::Sort($expectedFields, [StringComparer]::Ordinal)
  if (($actualFields -join "`n") -cne ($expectedFields -join "`n") -or
      $state.schema_version -ne 1 -or $state.runtime_epoch -isnot [ValueType] -or
      [long]$state.runtime_epoch -le 0 -or $state.entitlement_key_generation -isnot [ValueType] -or
      [long]$state.entitlement_key_generation -ne 1 -or
      @(
        @(
          $state.public_key_sha256, $state.entitlement_sha256, $state.verifier_sha256,
          $state.vendor_manifest_sha256, $state.python_sha256, $state.bundle_sums_sha256
        ) | Where-Object { [string]$_ -notmatch '^[0-9a-f]{64}$' }
      ).Count -ne 0) {
    Fail "bootstrap_runtime_state_invalid"
  }
  return $state
}

function Assert-SiteProvisioningState($ExistingState) {
  $siteExists = Test-Path -LiteralPath $SiteIdentityPath -PathType Leaf
  if ($null -ne $ExistingState -and -not $siteExists) {
    Fail "bootstrap_site_identity_missing"
  }
  if ($siteExists) { Ensure-SiteIdentity $SiteId }
}

function Assert-RuntimeAdvance($Requested, $Existing) {
  if ($null -eq $Existing) { return $false }
  $requestedEpoch = [long]$Requested.runtime_epoch
  $existingEpoch = [long]$Existing.runtime_epoch
  $requestedGeneration = [long]$Requested.entitlement_key_generation
  $existingGeneration = [long]$Existing.entitlement_key_generation
  if ($requestedGeneration -ne 1 -or $existingGeneration -ne 1) {
    Fail "bootstrap_entitlement_key_generation_unsupported"
  }
  if ([string]$Requested.public_key_sha256 -cne [string]$Existing.public_key_sha256) {
    Fail "bootstrap_entitlement_key_change_unsupported"
  }
  if ([string]$Requested.python_sha256 -cne [string]$Existing.python_sha256) {
    Fail "bootstrap_python_runtime_changed"
  }
  if ($requestedEpoch -lt $existingEpoch) {
    Fail "bootstrap_runtime_downgrade"
  }
  if ($requestedEpoch -eq $existingEpoch) {
    if ($requestedGeneration -eq $existingGeneration -and
        [string]$Requested.bundle_sums_sha256 -ceq [string]$Existing.bundle_sums_sha256) {
      return $true
    }
    Fail "bootstrap_runtime_epoch_conflict"
  }
  return $false
}

function ConvertTo-CanonicalState($State) {
  return '{"bundle_sums_sha256":"' + [string]$State.bundle_sums_sha256 +
    '","entitlement_key_generation":' + [string][long]$State.entitlement_key_generation +
    ',"entitlement_sha256":"' + [string]$State.entitlement_sha256 +
    '","public_key_sha256":"' + [string]$State.public_key_sha256 +
    '","python_sha256":"' + [string]$State.python_sha256 +
    '","runtime_epoch":' + [string][long]$State.runtime_epoch +
    ',"schema_version":1,"vendor_manifest_sha256":"' + [string]$State.vendor_manifest_sha256 +
    '","verifier_sha256":"' + [string]$State.verifier_sha256 + '"}' + "`n"
}

function Write-BootstrapJournal($Journal) {
  Write-ProtectedAsciiAtomic $BootstrapJournalPath (($Journal | ConvertTo-Json -Depth 8 -Compress) + "`n")
}

function Remove-ReplacementDestination($Entry) {
  if (Test-Path -LiteralPath ([string]$Entry.destination)) {
    Remove-Item -LiteralPath ([string]$Entry.destination) -Recurse -Force
  }
}

function Get-PathIdentity([string]$Path, [string]$Kind) {
  if (-not (Test-Path -LiteralPath $Path)) { return "absent" }
  $item = Get-Item -LiteralPath $Path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    Fail "bootstrap_transaction_uncertain"
  }
  if ($Kind -eq "File") {
    if ($item.PSIsContainer -or $item.Length -gt $MaxBundleFileBytes) {
      Fail "bootstrap_transaction_uncertain"
    }
    return "file:" + (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
  }
  if (-not $item.PSIsContainer) { Fail "bootstrap_transaction_uncertain" }
  $filesByRelative = New-Object Collections.Hashtable ([StringComparer]::Ordinal)
  [long]$totalBytes = 0
  foreach ($child in @(Get-ChildItem -LiteralPath $Path -Force -Recurse)) {
    if (($child.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      Fail "bootstrap_transaction_uncertain"
    }
    if ($child.PSIsContainer) { continue }
    $totalBytes += [long]$child.Length
    if ($filesByRelative.Count -ge $MaxTreeItems -or
        $totalBytes -gt ($MaxVendorBytes + $MaxSumsBytes)) {
      Fail "bootstrap_transaction_uncertain"
    }
    $relative = $child.FullName.Substring($Path.Length + 1).Replace('\', '/')
    $filesByRelative.Add($relative, $child)
  }
  [string[]]$relativePaths = @($filesByRelative.Keys | ForEach-Object { [string]$_ })
  [Array]::Sort($relativePaths, [StringComparer]::Ordinal)
  $builder = New-Object Text.StringBuilder
  foreach ($relative in $relativePaths) {
    $file = $filesByRelative[$relative]
    $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    [void]$builder.Append($relative).Append("`t").Append([string][long]$file.Length).Append("`t").Append($hash).Append("`n")
  }
  $sha = [Security.Cryptography.SHA256]::Create()
  try {
    $digest = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($builder.ToString()))
    return "directory:" + ([BitConverter]::ToString($digest)).Replace("-", "").ToLowerInvariant()
  }
  finally { $sha.Dispose() }
}

function Assert-RecognizedIdentity([string]$Path, [string]$Kind, [string[]]$Allowed) {
  $identity = Get-PathIdentity $Path $Kind
  if ($Allowed -cnotcontains $identity) { Fail "bootstrap_transaction_uncertain" }
  return $identity
}

function Copy-FileWithLimit([string]$Source, [string]$Destination, [long]$Maximum) {
  $item = Get-Item -LiteralPath $Source -Force -ErrorAction Stop
  if ($item.PSIsContainer -or
      ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
      $item.Length -le 0 -or $item.Length -gt $Maximum) {
    Fail "bootstrap_copy_source_invalid"
  }
  $input = [IO.File]::Open(
    $Source, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
  )
  try {
    $output = [IO.File]::Open(
      $Destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None
    )
    try {
      $buffer = New-Object byte[] 65536
      [long]$total = 0
      while (($count = $input.Read($buffer, 0, $buffer.Length)) -gt 0) {
        $total += $count
        if ($total -gt $Maximum) { Fail "bootstrap_copy_source_invalid" }
        $output.Write($buffer, 0, $count)
      }
      if ($total -le 0) { Fail "bootstrap_copy_source_invalid" }
      $output.Flush($true)
    }
    finally { $output.Dispose() }
  }
  finally { $input.Dispose() }
}

function Remove-TransactionRoot([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path)) { return }
  $item = Get-Item -LiteralPath $Path -Force
  if (-not $item.PSIsContainer -or
      ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    Fail "bootstrap_transaction_uncertain"
  }
  Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
  if (Test-Path -LiteralPath $Path) { Fail "bootstrap_transaction_uncertain" }
}

function Assert-BootstrapRestoreState($Journal) {
  foreach ($entry in @($Journal.replacements | Sort-Object index -Descending)) {
    $destination = [string]$entry.destination
    $backup = [string]$entry.backup
    $staged = [string]$entry.staged
    $oldIdentity = [string]$entry.old_identity
    $newIdentity = [string]$entry.new_identity
    if ([string]$Journal.phase -eq "prepared") {
      [void](Assert-RecognizedIdentity $destination ([string]$entry.kind) @($oldIdentity))
      continue
    }
    $allowedDestination = @($oldIdentity, $newIdentity, "absent") | Select-Object -Unique
    [void](Assert-RecognizedIdentity $destination ([string]$entry.kind) $allowedDestination)
    if (Test-Path -LiteralPath $backup) {
      [void](Assert-RecognizedIdentity $backup ([string]$entry.kind) @($oldIdentity))
      if (Test-Path -LiteralPath $staged) {
        [void](Assert-RecognizedIdentity $staged ([string]$entry.kind) @($newIdentity))
      }
    } elseif ([bool]$entry.existed) {
      [void](Assert-RecognizedIdentity $destination ([string]$entry.kind) @($oldIdentity))
    } else {
      if (Test-Path -LiteralPath $staged) {
        [void](Assert-RecognizedIdentity $staged ([string]$entry.kind) @($newIdentity))
      }
    }
  }
  $currentRuntimeState = if (Test-Path -LiteralPath $RuntimeStatePath -PathType Leaf) {
    Get-StrictAscii $RuntimeStatePath 4096
  } else { "" }
  $newState = [Text.Encoding]::ASCII.GetString(
    [Convert]::FromBase64String([string]$Journal.new_runtime_state_b64)
  )
  $oldState = [Text.Encoding]::ASCII.GetString(
    [Convert]::FromBase64String([string]$Journal.old_runtime_state_b64)
  )
  if (@($oldState, $newState, "") -cnotcontains $currentRuntimeState) {
    Fail "bootstrap_transaction_uncertain"
  }
  if ([bool]$Journal.site_identity_created) {
    if (Test-Path -LiteralPath $SiteIdentityPath) {
      if ((Get-StrictAscii $SiteIdentityPath 256) -cne "$SiteId`n") {
        Fail "bootstrap_transaction_uncertain"
      }
    }
  }
}

function Restore-BootstrapJournal($Journal) {
  Assert-BootstrapRestoreState $Journal
  foreach ($entry in @($Journal.replacements | Sort-Object index -Descending)) {
    if ([string]$Journal.phase -eq "prepared") { continue }
    $destination = [string]$entry.destination
    $backup = [string]$entry.backup
    if (Test-Path -LiteralPath $backup) {
      Remove-ReplacementDestination $entry
      Move-Item -LiteralPath $backup -Destination $destination
    } elseif (-not [bool]$entry.existed -and
        (Get-PathIdentity $destination ([string]$entry.kind)) -ceq [string]$entry.new_identity) {
      Remove-ReplacementDestination $entry
    }
  }
  $oldState = [Text.Encoding]::ASCII.GetString(
    [Convert]::FromBase64String([string]$Journal.old_runtime_state_b64)
  )
  if ([bool]$Journal.old_runtime_state_present) {
    Write-ProtectedAsciiAtomic $RuntimeStatePath $oldState
  } elseif (Test-Path -LiteralPath $RuntimeStatePath) {
    Remove-Item -LiteralPath $RuntimeStatePath -Force -ErrorAction Stop
    if (Test-Path -LiteralPath $RuntimeStatePath) { Fail "bootstrap_transaction_uncertain" }
  }
  if ([bool]$Journal.site_identity_created -and (Test-Path -LiteralPath $SiteIdentityPath)) {
    Remove-Item -LiteralPath $SiteIdentityPath -Force -ErrorAction Stop
    if (Test-Path -LiteralPath $SiteIdentityPath) { Fail "bootstrap_transaction_uncertain" }
  }
}

function Recover-BootstrapTransaction {
  if (-not (Test-Path -LiteralPath $BootstrapJournalPath)) { return }
  Assert-ProtectedItem $BootstrapJournalPath "File"
  try { $journal = (Get-StrictAscii $BootstrapJournalPath) | ConvertFrom-Json }
  catch { Fail "bootstrap_journal_invalid" }
  $fields = @(
    "schema_version", "operation_id", "phase", "transaction_root", "site_identity_created",
    "old_runtime_state_present", "old_runtime_state_b64", "new_runtime_state_b64", "replacements"
  )
  [string[]]$actualFields = @($journal.PSObject.Properties.Name)
  [string[]]$expectedFields = @($fields)
  [Array]::Sort($actualFields, [StringComparer]::Ordinal)
  [Array]::Sort($expectedFields, [StringComparer]::Ordinal)
  if (($actualFields -join "`n") -cne ($expectedFields -join "`n") -or
      $journal.schema_version -ne 1 -or
      [string]$journal.operation_id -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -or
      [string]$journal.transaction_root -cne "C:\ProgramData\Ruisheng\entitlement-bootstrap-$([string]$journal.operation_id)" -or
      [string]$journal.phase -notin @("prepared", "replacing", "validated", "committed") -or
      $journal.site_identity_created -isnot [bool] -or
      $journal.old_runtime_state_present -isnot [bool] -or $journal.replacements -isnot [Array] -or
      @($journal.replacements).Count -ne 4) {
    Fail "bootstrap_journal_invalid"
  }
  $expectedDestinations = @(
    (Join-Path $BinRoot "entitlement.py"),
    (Join-Path $BinRoot "target_entitlement_verifier.ps1"),
    (Join-Path $TrustRoot "entitlement-public-key"),
    $RuntimeRoot
  )
  for ($index = 0; $index -lt 4; $index++) {
    $entry = @($journal.replacements)[$index]
    $expectedKind = if ($index -eq 3) { "Directory" } else { "File" }
    if ($entry.index -ne $index -or [string]$entry.destination -cne $expectedDestinations[$index] -or
        [string]$entry.staged -cne (Join-Path ([string]$journal.transaction_root) "staged-$index") -or
        [string]$entry.backup -cne (Join-Path ([string]$journal.transaction_root) "backup-$index") -or
        [string]$entry.kind -cne $expectedKind -or $entry.existed -isnot [bool] -or
        [string]$entry.old_identity -notmatch '^(absent|file:[0-9a-f]{64}|directory:[0-9a-f]{64})$' -or
        ([string]$journal.phase -ne "prepared" -and
          [string]$entry.new_identity -notmatch '^(file:[0-9a-f]{64}|directory:[0-9a-f]{64})$')) {
      Fail "bootstrap_journal_invalid"
    }
  }
  if ([string]$journal.phase -eq "committed") {
    foreach ($entry in @($journal.replacements)) {
      [void](Assert-RecognizedIdentity ([string]$entry.destination) ([string]$entry.kind) @(
        [string]$entry.new_identity
      ))
    }
    $newState = [Text.Encoding]::ASCII.GetString(
      [Convert]::FromBase64String([string]$journal.new_runtime_state_b64)
    )
    if ((Get-StrictAscii $RuntimeStatePath 4096) -cne $newState) {
      Fail "bootstrap_transaction_uncertain"
    }
  } else { Restore-BootstrapJournal $journal }
  Remove-TransactionRoot ([string]$journal.transaction_root)
  Remove-Item -LiteralPath $BootstrapJournalPath -Force -ErrorAction Stop
}

function Set-ProtectedTree([string]$Root) {
  $items = @(Get-ChildItem -LiteralPath $Root -Force -Recurse)
  if ($items.Count -gt ($MaxVendorFiles * 2)) { Fail "bootstrap_tree_limit" }
  foreach ($item in @($items | Sort-Object { $_.FullName.Length } -Descending)) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
      Fail "bootstrap_reparse_point"
    }
    Set-ProtectedAcl $item.FullName $(if ($item.PSIsContainer) { "Directory" } else { "File" })
  }
  Set-ProtectedAcl $Root "Directory"
}

function Invoke-InstalledRuntimeValidation {
  $verifier = Join-Path $BinRoot "target_entitlement_verifier.ps1"
  $windowsPowerShell = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
  $start = New-Object Diagnostics.ProcessStartInfo
  $start.FileName = $windowsPowerShell
  $start.Arguments = "-NoLogo -NoProfile -NonInteractive -File `"$verifier`" " +
    "-Action ValidateRuntime -SiteId $SiteId"
  $start.UseShellExecute = $false
  $start.CreateNoWindow = $true
  $start.RedirectStandardOutput = $true
  $start.RedirectStandardError = $true
  $process = New-Object Diagnostics.Process
  $process.StartInfo = $start
  $started = $false
  try {
    $started = $process.Start()
    if (-not $started) { Fail "bootstrap_runtime_validation_failed" }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(60000)) {
      try { $process.Kill() } catch { }
      Fail "bootstrap_runtime_validation_failed"
    }
    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    if ($stdout.Length -gt 65536 -or $stderr.Length -gt 65536 -or
        $process.ExitCode -ne 0 -or -not [string]::IsNullOrWhiteSpace($stderr)) {
      Fail "bootstrap_runtime_validation_failed"
    }
    try { $result = $stdout.Trim() | ConvertFrom-Json } catch { Fail "bootstrap_runtime_validation_failed" }
    $fields = @("schema_version", "ok", "status", "site_id")
    [string[]]$actualFields = @($result.PSObject.Properties.Name)
    [string[]]$expectedFields = @($fields)
    [Array]::Sort($actualFields, [StringComparer]::Ordinal)
    [Array]::Sort($expectedFields, [StringComparer]::Ordinal)
    if (($actualFields -join "`n") -cne ($expectedFields -join "`n") -or
        $result.schema_version -ne 1 -or $result.ok -isnot [bool] -or -not $result.ok -or
        [string]$result.status -cne "runtime_validated" -or [string]$result.site_id -cne $SiteId) {
      Fail "bootstrap_runtime_validation_failed"
    }
  } finally {
    if ($started -and -not $process.HasExited) { try { $process.Kill() } catch { } }
    $process.Dispose()
  }
}

function Assert-InstalledRuntime([hashtable]$Sums, $RequestedState) {
  Ensure-SiteIdentity $SiteId
  $existingState = Get-ExistingRuntimeState
  if ($null -eq $existingState -or
      (ConvertTo-CanonicalState $existingState) -cne (ConvertTo-CanonicalState $RequestedState)) {
    Fail "bootstrap_runtime_state_mismatch"
  }
  $installed = @{
    "entitlement.py" = (Join-Path $BinRoot "entitlement.py")
    "target_entitlement_verifier.ps1" = (Join-Path $BinRoot "target_entitlement_verifier.ps1")
    "entitlement-public-key" = (Join-Path $TrustRoot "entitlement-public-key")
    "vendor-manifest.sha256" = (Join-Path $RuntimeRoot "vendor-manifest.sha256")
  }
  foreach ($relative in $installed.Keys) {
    Assert-ProtectedItem ([string]$installed[$relative]) "File"
    $actual = (Get-FileHash -LiteralPath ([string]$installed[$relative]) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -cne [string]$Sums[$relative]) { Fail "bootstrap_runtime_component_drift" }
  }
  Invoke-InstalledRuntimeValidation
}

function Assert-ExactRuntimeFields($Value, [string[]]$Expected, [string]$ErrorCode) {
  [string[]]$actual = @($Value.PSObject.Properties.Name)
  [string[]]$wanted = @($Expected)
  [Array]::Sort($actual, [StringComparer]::Ordinal)
  [Array]::Sort($wanted, [StringComparer]::Ordinal)
  if (($actual -join "`n") -cne ($wanted -join "`n")) { Fail $ErrorCode }
}

function New-RuntimeReceipt([hashtable]$Sums, $RequestedState) {
  return [pscustomobject][ordered]@{
    schema_version = 1
    ok = $true
    status = "runtime_installed"
    operation_id = $OperationId
    site_id = $SiteId
    entitlement_sha256 = [string]$Sums["entitlement.py"]
    verifier_sha256 = [string]$Sums["target_entitlement_verifier.ps1"]
    public_key_sha256 = [string]$Sums["entitlement-public-key"]
    vendor_archive_sha256 = [string]$Sums["vendor.zip"]
    runtime_epoch = [long]$RequestedState.runtime_epoch
    entitlement_key_generation = [long]$RequestedState.entitlement_key_generation
    services_restarted = $false
    device_configuration_changed = $false
  }
}

function Assert-RuntimeReceipt($Receipt, [string]$ExpectedOperation, [string]$ExpectedSite) {
  Assert-ExactRuntimeFields $Receipt @(
    "schema_version", "ok", "status", "operation_id", "site_id", "entitlement_sha256",
    "verifier_sha256", "public_key_sha256", "vendor_archive_sha256", "runtime_epoch",
    "entitlement_key_generation", "services_restarted", "device_configuration_changed"
  ) "bootstrap_operation_receipt_invalid"
  if ($Receipt.schema_version -isnot [ValueType] -or [long]$Receipt.schema_version -ne 1 -or
      $Receipt.ok -isnot [bool] -or -not $Receipt.ok -or
      [string]$Receipt.status -cne "runtime_installed" -or
      [string]$Receipt.operation_id -cne $ExpectedOperation -or
      [string]$Receipt.site_id -cne $ExpectedSite -or
      $Receipt.runtime_epoch -isnot [ValueType] -or [long]$Receipt.runtime_epoch -le 0 -or
      $Receipt.entitlement_key_generation -isnot [ValueType] -or
      [long]$Receipt.entitlement_key_generation -ne 1 -or
      $Receipt.services_restarted -isnot [bool] -or $Receipt.services_restarted -or
      $Receipt.device_configuration_changed -isnot [bool] -or
      $Receipt.device_configuration_changed -or
      @(
        @(
          $Receipt.entitlement_sha256, $Receipt.verifier_sha256,
          $Receipt.public_key_sha256, $Receipt.vendor_archive_sha256
        ) | Where-Object { [string]$_ -notmatch '^[0-9a-f]{64}$' }
      ).Count -ne 0) {
    Fail "bootstrap_operation_receipt_invalid"
  }
}

function Read-RuntimeOperationRecord([string]$Path) {
  Assert-ProtectedItem $Path "File"
  try {
    $record = (Get-StrictAscii $Path $MaxRuntimeOperationReceiptBytes) | ConvertFrom-Json
  } catch { Fail "bootstrap_operation_receipt_invalid" }
  $baseFields = @("bundle_sums_sha256", "operation_id", "schema_version", "site_id", "status")
  $expectedFields = if ([string]$record.status -ceq "terminal") {
    $baseFields + @("receipt")
  } else { $baseFields }
  Assert-ExactRuntimeFields $record $expectedFields "bootstrap_operation_receipt_invalid"
  if ($record.schema_version -isnot [ValueType] -or [long]$record.schema_version -ne 1 -or
      [string]$record.operation_id -notmatch
        '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -or
      [string]$record.site_id -notmatch '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$' -or
      [string]$record.bundle_sums_sha256 -notmatch '^[0-9a-f]{64}$' -or
      [string]$record.status -notin @("executing", "terminal")) {
    Fail "bootstrap_operation_receipt_invalid"
  }
  if ([string]$record.status -ceq "terminal") {
    Assert-RuntimeReceipt $record.receipt ([string]$record.operation_id) ([string]$record.site_id)
  }
  return $record
}

function Get-RuntimeOperationFiles {
  Assert-ProtectedItem $RuntimeOperationsRoot "Directory"
  $files = @(Get-ChildItem -LiteralPath $RuntimeOperationsRoot -Force)
  if ($files.Count -gt ($MaxRuntimeOperationReceipts * 2)) {
    Fail "bootstrap_operation_store_invalid"
  }
  [long]$total = 0
  foreach ($file in $files) {
    if ($file.PSIsContainer -or
        ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $file.Name -notmatch
          '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.json$' -or
        $file.Length -le 0 -or $file.Length -gt $MaxRuntimeOperationReceiptBytes) {
      Fail "bootstrap_operation_store_invalid"
    }
    Assert-ProtectedItem $file.FullName "File"
    $total += [long]$file.Length
    if ($total -gt ($MaxRuntimeOperationBytes * 2)) {
      Fail "bootstrap_operation_store_invalid"
    }
  }
  return $files
}

function Prepare-RuntimeOperation([switch]$Create) {
  if (-not (Test-Path -LiteralPath $RuntimeOperationsRoot)) {
    if (-not $Create) {
      return [pscustomobject]@{ exists = $false; terminal = $false; receipt = $null }
    }
    Ensure-ProtectedDirectory $RuntimeOperationsRoot
  }
  $path = Join-Path $RuntimeOperationsRoot "$OperationId.json"
  $files = @(Get-RuntimeOperationFiles)
  $entries = New-Object Collections.Generic.List[object]
  [long]$totalBytes = 0
  foreach ($file in $files) {
    $record = Read-RuntimeOperationRecord $file.FullName
    $entries.Add([pscustomobject]@{ item = $file; record = $record })
    $totalBytes += [long]$file.Length
  }
  $current = @($entries | Where-Object { $_.item.FullName -ceq $path })
  if ($current.Count -gt 1) { Fail "bootstrap_operation_store_invalid" }
  if ($current.Count -eq 1) {
    $record = $current[0].record
    if ([string]$record.operation_id -cne $OperationId -or
        [string]$record.site_id -cne $SiteId -or
        [string]$record.bundle_sums_sha256 -cne $script:BundleSumsSha256) {
      Fail "bootstrap_operation_conflict"
    }
    return [pscustomobject]@{
      exists = $true
      terminal = [string]$record.status -ceq "terminal"
      receipt = $record.receipt
    }
  }

  if (-not $Create) {
    return [pscustomobject]@{ exists = $false; terminal = $false; receipt = $null }
  }

  $capacityCount = $MaxRuntimeOperationReceipts - 1
  $capacityBytes = $MaxRuntimeOperationBytes - $MaxRuntimeOperationReceiptBytes
  $candidateByKey = @{}
  foreach ($entry in @($entries | Where-Object {
      [string]$_.record.status -ceq "terminal" -and $_.item.FullName -cne $path
    })) {
    $key = $entry.item.LastWriteTimeUtc.Ticks.ToString(
      "D19", [Globalization.CultureInfo]::InvariantCulture
    ) + "|" + $entry.item.Name
    $candidateByKey[$key] = $entry
  }
  [string[]]$candidateKeys = @($candidateByKey.Keys)
  [Array]::Sort($candidateKeys, [StringComparer]::Ordinal)
  $remainingCount = $files.Count
  foreach ($key in $candidateKeys) {
    if ($remainingCount -le $capacityCount -and $totalBytes -le $capacityBytes) { break }
    $entry = $candidateByKey[$key]
    Remove-Item -LiteralPath $entry.item.FullName -Force -ErrorAction Stop
    if (Test-Path -LiteralPath $entry.item.FullName) { Fail "bootstrap_operation_cleanup_failed" }
    $remainingCount--
    $totalBytes -= [long]$entry.item.Length
  }
  if ($remainingCount -gt $capacityCount -or $totalBytes -gt $capacityBytes) {
    Fail "bootstrap_operation_store_full"
  }

  $executing = [ordered]@{
    bundle_sums_sha256 = $script:BundleSumsSha256
    operation_id = $OperationId
    schema_version = 1
    site_id = $SiteId
    status = "executing"
  }
  Write-ProtectedAsciiAtomic $path (($executing | ConvertTo-Json -Compress) + "`n")
  return [pscustomobject]@{ exists = $true; terminal = $false; receipt = $null }
}

function Complete-RuntimeOperation($Receipt) {
  Assert-RuntimeReceipt $Receipt $OperationId $SiteId
  $record = [ordered]@{
    bundle_sums_sha256 = $script:BundleSumsSha256
    operation_id = $OperationId
    receipt = $Receipt
    schema_version = 1
    site_id = $SiteId
    status = "terminal"
  }
  $path = Join-Path $RuntimeOperationsRoot "$OperationId.json"
  Write-ProtectedAsciiAtomic $path (($record | ConvertTo-Json -Depth 4 -Compress) + "`n")
  if ((Get-Item -LiteralPath $path -Force).Length -gt $MaxRuntimeOperationReceiptBytes) {
    Fail "bootstrap_operation_receipt_invalid"
  }
}

if ($OperationId -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') {
  Fail "bootstrap_operation_id_invalid"
}
if ($SiteId -notmatch '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$') { Fail "bootstrap_site_id_invalid" }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if ($identity.User.Value -ne "S-1-5-18" -and
    -not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Fail "bootstrap_admin_required"
}

$bootstrapLock = Enter-BootstrapLock
$runtimeUseLock = $null
$entitlementLock = $null
try {
  $bundleRoot = Join-Path $IncomingParent $OperationId
  Assert-ProtectedItem "C:\ProgramData\Ruisheng" "Directory"
  Assert-ProtectedItem $IncomingParent "Directory"
  Assert-ProtectedItem $bundleRoot "Directory"
  $sums = Get-AuthenticatedSums $bundleRoot
  Assert-Bundle $bundleRoot $sums
  Assert-EntitlementPublicKey (Join-Path $bundleRoot "entitlement-public-key")
  $requestedState = Get-RuntimeMetadata `
    (Join-Path $bundleRoot "runtime-metadata.json") $sums
  $runtimeUseLock = Enter-RuntimeUseLock
  $entitlementLock = Enter-EntitlementTransactionLock
  if (Test-Path -LiteralPath (Join-Path $EntitlementRoot "transaction.json")) {
    Fail "bootstrap_entitlement_transaction_uncertain"
  }
  try { Recover-BootstrapTransaction } catch { Fail "bootstrap_transaction_uncertain" }
  $existingState = Get-ExistingRuntimeState
  Assert-SiteProvisioningState $existingState
  $operation = Prepare-RuntimeOperation
  if ($operation.terminal) {
    if ($null -ne $existingState -and
        [string]$existingState.bundle_sums_sha256 -ceq $script:BundleSumsSha256) {
      Assert-InstalledRuntime $sums $requestedState
    }
    $operation.receipt | ConvertTo-Json -Compress
    return
  }
  $idempotent = Assert-RuntimeAdvance $requestedState $existingState
  if ($idempotent) {
    Assert-InstalledRuntime $sums $requestedState
    [void](Prepare-RuntimeOperation -Create)
    $receipt = New-RuntimeReceipt $sums $requestedState
    Complete-RuntimeOperation $receipt
    $receipt | ConvertTo-Json -Compress
    return
  }
  [void](Prepare-RuntimeOperation -Create)

  Ensure-ProtectedDirectory "C:\ProgramData\Ruisheng"
  Ensure-ProtectedDirectory $BinRoot
  Ensure-ProtectedDirectory $TrustRoot

  $transactionRoot = "C:\ProgramData\Ruisheng\entitlement-bootstrap-$OperationId"
  if (Test-Path -LiteralPath $transactionRoot) { Fail "bootstrap_transaction_uncertain" }
  $replacements = @(
    [ordered]@{ index = 0; source = (Join-Path $bundleRoot "entitlement.py"); destination = (Join-Path $BinRoot "entitlement.py"); kind = "File" },
    [ordered]@{ index = 1; source = (Join-Path $bundleRoot "target_entitlement_verifier.ps1"); destination = (Join-Path $BinRoot "target_entitlement_verifier.ps1"); kind = "File" },
    [ordered]@{ index = 2; source = (Join-Path $bundleRoot "entitlement-public-key"); destination = (Join-Path $TrustRoot "entitlement-public-key"); kind = "File" },
    [ordered]@{ index = 3; source = (Join-Path $transactionRoot "entitlement-runtime"); destination = $RuntimeRoot; kind = "Directory" }
  )
  $journalReplacements = New-Object Collections.Generic.List[object]
  foreach ($replacement in $replacements) {
    $journalReplacements.Add([ordered]@{
      index = [int]$replacement.index
      destination = [string]$replacement.destination
      staged = (Join-Path $transactionRoot ("staged-$($replacement.index)"))
      backup = (Join-Path $transactionRoot ("backup-$($replacement.index)"))
      kind = [string]$replacement.kind
      existed = [bool](Test-Path -LiteralPath $replacement.destination)
      old_identity = Get-PathIdentity ([string]$replacement.destination) ([string]$replacement.kind)
      new_identity = ""
    })
  }
  [byte[]]$oldRuntimeState = [byte[]]::new(0)
  if ($null -ne $existingState) {
    $oldRuntimeState = [Text.Encoding]::ASCII.GetBytes((ConvertTo-CanonicalState $existingState))
  }
  $journal = [ordered]@{
    schema_version = 1
    operation_id = $OperationId
    phase = "prepared"
    transaction_root = $transactionRoot
    site_identity_created = $false
    old_runtime_state_present = [bool]($oldRuntimeState.Length -gt 0)
    old_runtime_state_b64 = [Convert]::ToBase64String($oldRuntimeState)
    new_runtime_state_b64 = [Convert]::ToBase64String(
      [Text.Encoding]::ASCII.GetBytes((ConvertTo-CanonicalState $requestedState))
    )
    replacements = $journalReplacements.ToArray()
  }
  Write-BootstrapJournal $journal
  try {
    [void](New-Item -ItemType Directory -Path $transactionRoot)
    Set-ProtectedAcl $transactionRoot "Directory"
    $newRuntime = Join-Path $transactionRoot "entitlement-runtime"
    [void](New-Item -ItemType Directory -Path $newRuntime)
    $newVendor = Join-Path $newRuntime "vendor"
    Expand-AuthenticatedVendor (Join-Path $bundleRoot "vendor.zip") $newVendor
    Copy-FileWithLimit (Join-Path $bundleRoot "vendor-manifest.sha256") `
      (Join-Path $newRuntime "vendor-manifest.sha256") $MaxSumsBytes
    Set-ProtectedTree $newRuntime
    foreach ($replacement in $replacements) {
      $entry = @($journal.replacements)[$replacement.index]
      if ($replacement.kind -eq "File") {
        Copy-FileWithLimit $replacement.source ([string]$entry.staged) $MaxBundleFileBytes
        Set-ProtectedAcl ([string]$entry.staged) "File"
      } else { Move-Item -LiteralPath $replacement.source -Destination ([string]$entry.staged) }
      $entry.new_identity = Get-PathIdentity ([string]$entry.staged) ([string]$entry.kind)
    }
    Write-BootstrapJournal $journal
    if (-not (Test-Path -LiteralPath $SiteIdentityPath)) {
      $journal.site_identity_created = $true
      Write-BootstrapJournal $journal
    }
    Ensure-SiteIdentity $SiteId
    $journal.phase = "replacing"
    Write-BootstrapJournal $journal
    foreach ($entry in $journal.replacements) {
      if ([bool]$entry.existed) {
        Move-Item -LiteralPath ([string]$entry.destination) -Destination ([string]$entry.backup)
      }
      Move-Item -LiteralPath ([string]$entry.staged) -Destination ([string]$entry.destination)
      if ([string]$entry.kind -eq "Directory") { Set-ProtectedTree ([string]$entry.destination) }
      else { Assert-ProtectedItem ([string]$entry.destination) "File" }
    }
    Write-ProtectedAsciiAtomic $RuntimeStatePath (ConvertTo-CanonicalState $requestedState)
    Assert-InstalledRuntime $sums $requestedState
    $journal.phase = "validated"
    Write-BootstrapJournal $journal
    $journal.phase = "committed"
    Write-BootstrapJournal $journal
  } catch {
    $failure = $_.Exception.Message
    try {
      Restore-BootstrapJournal $journal
      Remove-TransactionRoot $transactionRoot
      Remove-Item -LiteralPath $BootstrapJournalPath -Force -ErrorAction Stop
    } catch { Fail "bootstrap_transaction_uncertain" }
    Fail $failure
  }
  Remove-TransactionRoot $transactionRoot
  Remove-Item -LiteralPath $BootstrapJournalPath -Force -ErrorAction Stop
  $receipt = New-RuntimeReceipt $sums $requestedState
  Complete-RuntimeOperation $receipt
} finally {
  Exit-EntitlementTransactionLock $entitlementLock
  Exit-RuntimeUseLock $runtimeUseLock
  $bootstrapLock.Dispose()
}

$receipt | ConvertTo-Json -Compress
