[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$PackagePath = ".",

    [Parameter(Position = 1)]
    [string]$SiteEnvPath = ""
)

$ErrorActionPreference = "Stop"
function Fail([string]$Message) {
    throw "[verify] $Message"
}
if ($PSVersionTable.PSVersion -lt [version]"7.3") {
    Fail "PowerShell 7.3 or newer is required"
}

function ConvertTo-CmdSafePath([string]$Path, [string]$Label) {
    $UnsafeCharacters = [char[]]@(
        '"', ' ', "`t", '%', '!', '&', '|', '<', '>', '^', '(', ')', "`r", "`n"
    )
    if ($Path -cnotmatch '^[A-Za-z]:\\' -or $Path.IndexOfAny($UnsafeCharacters) -ge 0) {
        Fail "publisher authenticity FAILED: $Label cannot be safely passed to the system command processor"
    }
    return $Path
}

function Get-ApprovedTrustSids([switch]$AllowTrustedInstaller) {
    $AllowedSids = @("S-1-5-18", "S-1-5-32-544")
    if ($AllowTrustedInstaller) {
        $AllowedSids += "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
    }
    return $AllowedSids
}

function Assert-ProtectedTrustAcl(
    [string]$Path, [string]$Label = "trust path", [switch]$AllowTrustedInstaller
) {
    $Item = Get-Item -Force -LiteralPath $Path -ErrorAction Stop
    if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail "publisher authenticity FAILED: $Label is linked"
    }
    $AllowedSids = Get-ApprovedTrustSids -AllowTrustedInstaller:$AllowTrustedInstaller
    $Acl = Get-Acl -LiteralPath $Path
    $OwnerSid = $Acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($OwnerSid -notin $AllowedSids) {
        Fail "publisher authenticity FAILED: $Label has an unapproved owner: $OwnerSid"
    }
    $UnsafeRights = [Security.AccessControl.FileSystemRights]::CreateFiles -bor
        [Security.AccessControl.FileSystemRights]::CreateDirectories -bor
        [Security.AccessControl.FileSystemRights]::AppendData -bor
        [Security.AccessControl.FileSystemRights]::WriteData -bor
        [Security.AccessControl.FileSystemRights]::WriteAttributes -bor
        [Security.AccessControl.FileSystemRights]::WriteExtendedAttributes -bor
        [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
        [Security.AccessControl.FileSystemRights]::Delete -bor
        [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
        [Security.AccessControl.FileSystemRights]::TakeOwnership
    foreach ($Rule in $Acl.Access) {
        if ($Rule.AccessControlType -ne "Allow" -or
            ($Rule.FileSystemRights -band $UnsafeRights) -eq 0) {
            continue
        }
        try {
            $Sid = $Rule.IdentityReference.Translate(
                [Security.Principal.SecurityIdentifier]
            ).Value
        } catch {
            Fail "publisher authenticity FAILED: $Label has an unresolvable writable identity"
        }
        if ($Sid -notin $AllowedSids) {
            Fail "publisher authenticity FAILED: $Label is writable by $Sid"
        }
    }
}

function Assert-ProtectedTrustAncestors(
    [string]$Path, [string]$Label, [switch]$AllowTrustedInstaller
) {
    $AllowedSids = Get-ApprovedTrustSids -AllowTrustedInstaller:$AllowTrustedInstaller
    $Current = (Get-Item -Force -LiteralPath $Path -ErrorAction Stop).Parent
    $UnsafeParentRights = [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
        [Security.AccessControl.FileSystemRights]::Delete -bor
        [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
        [Security.AccessControl.FileSystemRights]::TakeOwnership
    while ($null -ne $Current) {
        if (($Current.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Fail "publisher authenticity FAILED: $Label ancestor is linked"
        }
        $Acl = Get-Acl -LiteralPath $Current.FullName
        $OwnerSid = $Acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
        if ($OwnerSid -notin $AllowedSids) {
            Fail "publisher authenticity FAILED: $Label ancestor has an unapproved owner"
        }
        foreach ($Rule in $Acl.Access) {
            if (($Rule.PropagationFlags -band
                    [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0) {
                continue
            }
            if ($Rule.AccessControlType -ne "Allow" -or
                ($Rule.FileSystemRights -band $UnsafeParentRights) -eq 0) {
                continue
            }
            try {
                $Sid = $Rule.IdentityReference.Translate(
                    [Security.Principal.SecurityIdentifier]
                ).Value
            } catch {
                Fail "publisher authenticity FAILED: $Label ancestor has an unresolvable replacement identity"
            }
            if ($Sid -notin $AllowedSids) {
                Fail "publisher authenticity FAILED: $Label ancestor permits replacement by $Sid"
            }
        }
        $Current = $Current.Parent
    }
}

function Set-ProtectedSnapshotAcl([string]$Path) {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $OwnerSidValue = if ($Identity.User.Value -eq "S-1-5-18") {
        "S-1-5-18"
    } else {
        "S-1-5-32-544"
    }
    $Security = [Security.AccessControl.DirectorySecurity]::new()
    $Security.SetAccessRuleProtection($true, $false)
    $Security.SetOwner([Security.Principal.SecurityIdentifier]::new($OwnerSidValue))
    $Inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    $Propagation = [Security.AccessControl.PropagationFlags]::None
    foreach ($SidValue in @("S-1-5-18", "S-1-5-32-544")) {
        $Sid = [Security.Principal.SecurityIdentifier]::new($SidValue)
        $Rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $Sid,
            [Security.AccessControl.FileSystemRights]::FullControl,
            $Inheritance,
            $Propagation,
            [Security.AccessControl.AccessControlType]::Allow
        )
        [void]$Security.AddAccessRule($Rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $Security
}

function New-ProtectedSnapshotRoot([string]$Prefix) {
    $RuishengRoot = "C:\ProgramData\Ruisheng"
    Assert-ProtectedTrustAcl $RuishengRoot "snapshot base"
    Assert-ProtectedTrustAncestors $RuishengRoot "snapshot base" -AllowTrustedInstaller
    $WorkRoot = Join-Path $RuishengRoot "work"
    if (-not (Test-Path -LiteralPath $WorkRoot)) {
        [void](New-Item -ItemType Directory -Path $WorkRoot)
    }
    $WorkItem = Get-Item -Force -LiteralPath $WorkRoot -ErrorAction Stop
    if (-not $WorkItem.PSIsContainer -or
        ($WorkItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail "publisher authenticity FAILED: snapshot work directory is missing or linked"
    }
    Set-ProtectedSnapshotAcl $WorkRoot
    Assert-ProtectedTrustAcl $WorkRoot "snapshot work directory"
    Assert-ProtectedTrustAncestors $WorkRoot "snapshot work directory" -AllowTrustedInstaller
    $Snapshot = Join-Path $WorkRoot ($Prefix + [Guid]::NewGuid().ToString("N"))
    [void](New-Item -ItemType Directory -Path $Snapshot)
    Set-ProtectedSnapshotAcl $Snapshot
    return $Snapshot
}

$Components = @("postgres", "redis", "api", "gw", "web")
$FixedFiles = @(
    ".env.prod.example",
    "MANIFEST.json",
    "MANIFEST.md",
    "SHA256SUMS",
    "SHA256SUMS.sig",
    "docker-compose.prod.yml",
    "nginx.conf",
    "site-acceptance-profile.md.example",
    "site-health-acl.conf.example",
    "site-network.override.yml",
    "setup-customer.md",
    "validate-network-boundary.py",
    "verify-candidate.ps1",
    "verify-candidate.sh"
)
$SnapshotExpectedFiles = [System.Collections.Generic.HashSet[string]]::new(
    [string[]]$FixedFiles, [System.StringComparer]::Ordinal
)
foreach ($Component in $Components) {
    [void]$SnapshotExpectedFiles.Add("images/$Component.tar.gz")
}

$PackageItem = Get-Item -Force -LiteralPath $PackagePath -ErrorAction Stop
if (-not $PackageItem.PSIsContainer -or
    ($PackageItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    Fail "publisher authenticity FAILED: candidate directory is missing or linked"
}
$SourcePackageRoot = $PackageItem.FullName.TrimEnd("\", "/")
$CurrentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$CurrentPrincipal = [Security.Principal.WindowsPrincipal]::new($CurrentIdentity)
if ($CurrentIdentity.User.Value -ne "S-1-5-18" -and -not $CurrentPrincipal.IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)) {
    Fail "publisher authenticity FAILED: verifier must run elevated to protect its snapshot"
}
$SnapshotRoot = New-ProtectedSnapshotRoot "verified-candidate-"
$DockerConfig = $null
try {
    [void](New-Item -ItemType Directory -Path (Join-Path $SnapshotRoot "images"))
    $DockerConfig = New-ProtectedSnapshotRoot "docker-config-"
    [IO.File]::WriteAllText((Join-Path $DockerConfig "config.json"), "{}`n", [Text.Encoding]::ASCII)
    $env:DOCKER_CONFIG = $DockerConfig
    Remove-Item Env:DOCKER_CLI_PLUGIN_EXTRA_DIRS -ErrorAction SilentlyContinue
    Remove-Item Env:DOCKER_HOST -ErrorAction SilentlyContinue
    Remove-Item Env:DOCKER_CONTEXT -ErrorAction SilentlyContinue

    $SnapshotActualFiles = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    Get-ChildItem -LiteralPath $SourcePackageRoot -Force -Recurse | ForEach-Object {
        $Relative = $_.FullName.Substring($SourcePackageRoot.Length).
            TrimStart("\", "/").Replace("\", "/")
        if (($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Fail "publisher authenticity FAILED: candidate contains a link: $Relative"
        }
        if ($_.PSIsContainer) {
            if ($Relative -cne "images") {
                Fail "publisher authenticity FAILED: candidate contains an extra directory: $Relative"
            }
        } else {
            [void]$SnapshotActualFiles.Add($Relative)
        }
    }
    $SnapshotMissing = @($SnapshotExpectedFiles | Where-Object {
        -not $SnapshotActualFiles.Contains($_)
    })
    $SnapshotExtra = @($SnapshotActualFiles | Where-Object {
        -not $SnapshotExpectedFiles.Contains($_)
    })
    if ($SnapshotMissing.Count -ne 0 -or $SnapshotExtra.Count -ne 0) {
        Fail "publisher authenticity FAILED: candidate file allowlist mismatch: missing=$($SnapshotMissing -join ','), extra=$($SnapshotExtra -join ',')"
    }
    $SourceLengths = @{}
    [Int64]$SnapshotBytes = 0
    foreach ($Relative in $SnapshotExpectedFiles) {
        $SourcePath = Join-Path $SourcePackageRoot $Relative
        $SourceItem = Get-Item -Force -LiteralPath $SourcePath -ErrorAction Stop
        if ($SourceItem.PSIsContainer -or
            ($SourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Fail "publisher authenticity FAILED: candidate file changed or linked: $Relative"
        }
        $SourceLengths[$Relative] = [Int64]$SourceItem.Length
        $SnapshotBytes += [Int64]$SourceItem.Length
    }
    [Int64]$SnapshotReserve = [Math]::Max(64MB, [Int64]($SnapshotBytes / 10))
    $SnapshotDrive = (Get-Item -LiteralPath $SnapshotRoot -ErrorAction Stop).PSDrive
    if ($null -eq $SnapshotDrive -or $SnapshotDrive.Free -lt ($SnapshotBytes + $SnapshotReserve)) {
        Fail "publisher authenticity FAILED: insufficient free space for protected candidate snapshot"
    }
    foreach ($Relative in $SnapshotExpectedFiles) {
        $SourcePath = Join-Path $SourcePackageRoot $Relative
        $DestinationPath = Join-Path $SnapshotRoot $Relative
        $SourceItem = Get-Item -Force -LiteralPath $SourcePath -ErrorAction Stop
        if ($SourceItem.PSIsContainer -or
            ($SourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Fail "publisher authenticity FAILED: candidate file changed or linked: $Relative"
        }
        $InputStream = $null
        $OutputStream = $null
        try {
            $InputStream = [IO.File]::Open(
                $SourcePath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
            )
            [Int64]$ExpectedLength = $SourceLengths[$Relative]
            if ($InputStream.Length -ne $ExpectedLength) {
                Fail "publisher authenticity FAILED: candidate file changed before snapshot: $Relative"
            }
            $OutputStream = [IO.File]::Open(
                $DestinationPath,
                [IO.FileMode]::CreateNew,
                [IO.FileAccess]::Write,
                [IO.FileShare]::None
            )
            $Buffer = [byte[]]::new(1MB)
            [Int64]$Copied = 0
            while ($Copied -lt $ExpectedLength) {
                $ReadLength = [int][Math]::Min($Buffer.Length, $ExpectedLength - $Copied)
                $Read = $InputStream.Read($Buffer, 0, $ReadLength)
                if ($Read -le 0) { break }
                $OutputStream.Write($Buffer, 0, $Read)
                $Copied += $Read
            }
            if ($Copied -ne $ExpectedLength -or $InputStream.ReadByte() -ne -1) {
                Fail "publisher authenticity FAILED: candidate file size changed during snapshot: $Relative"
            }
        } finally {
            if ($null -ne $OutputStream) { $OutputStream.Dispose() }
            if ($null -ne $InputStream) { $InputStream.Dispose() }
        }
    }
    $PackageRoot = $SnapshotRoot
$ComposeEnvPath = if ([string]::IsNullOrWhiteSpace($SiteEnvPath)) {
    Join-Path $PackageRoot ".env.prod.example"
} else {
    (Resolve-Path -LiteralPath $SiteEnvPath).Path
}
$TrustInput = Get-Item -Force -LiteralPath "C:\ProgramData\Ruisheng\trust" -ErrorAction Stop
if (-not $TrustInput.PSIsContainer -or
    ($TrustInput.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    Fail "publisher authenticity FAILED: external trust directory is missing or linked"
}
$TrustRoot = $TrustInput.FullName.TrimEnd("\", "/")
if ($TrustRoot -eq $SourcePackageRoot -or
    $TrustRoot.StartsWith("$SourcePackageRoot\", [StringComparison]::OrdinalIgnoreCase)) {
    Fail "publisher authenticity FAILED: trust directory must be outside the candidate package"
}
$AllowedSigners = Join-Path $TrustRoot "release-allowed-signers"
$FingerprintPath = Join-Path $TrustRoot "release-key-fingerprint"
Assert-ProtectedTrustAcl $TrustRoot "trust directory"
Assert-ProtectedTrustAncestors $TrustRoot "trust directory" -AllowTrustedInstaller
foreach ($TrustFile in @($AllowedSigners, $FingerprintPath)) {
    $Item = Get-Item -Force -LiteralPath $TrustFile -ErrorAction Stop
    if ($Item.PSIsContainer -or ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail "publisher authenticity FAILED: trust file is missing or linked"
    }
    Assert-ProtectedTrustAcl $TrustFile "trust file"
}
$Ascii = [Text.Encoding]::ASCII
$AllowedText = $Ascii.GetString([IO.File]::ReadAllBytes($AllowedSigners))
if ($AllowedText -cnotmatch '^ruisheng-release ssh-ed25519 ([A-Za-z0-9+/]+={0,2})\n$') {
    Fail "publisher authenticity FAILED: release-allowed-signers is not the approved single identity"
}
try { [byte[]]$KeyBlob = [Convert]::FromBase64String($Matches[1]) } catch {
    Fail "publisher authenticity FAILED: release public key is not valid base64"
}
function Read-SshString([byte[]]$Value, [ref]$Offset) {
    if ($Value.Length - $Offset.Value -lt 4) {
        Fail "publisher authenticity FAILED: release public key blob is truncated"
    }
    $Length = [Net.IPAddress]::NetworkToHostOrder(
        [BitConverter]::ToInt32($Value, $Offset.Value)
    )
    $Offset.Value += 4
    if ($Length -lt 0 -or $Value.Length - $Offset.Value -lt $Length) {
        Fail "publisher authenticity FAILED: release public key blob is truncated"
    }
    [byte[]]$Result = $Value[$Offset.Value..($Offset.Value + $Length - 1)]
    $Offset.Value += $Length
    return $Result
}
$KeyOffset = 0
$KeyTypeBytes = Read-SshString $KeyBlob ([ref]$KeyOffset)
$PublicKeyBytes = Read-SshString $KeyBlob ([ref]$KeyOffset)
if ($Ascii.GetString($KeyTypeBytes) -cne "ssh-ed25519" -or
    $PublicKeyBytes.Length -ne 32 -or $KeyOffset -ne $KeyBlob.Length) {
    Fail "publisher authenticity FAILED: release public key is not canonical ssh-ed25519"
}
$Hasher = [Security.Cryptography.SHA256]::Create()
try {
    $DerivedFingerprint = "SHA256:" + [Convert]::ToBase64String(
        $Hasher.ComputeHash($KeyBlob)
    ).TrimEnd("=")
} finally { $Hasher.Dispose() }
$FingerprintText = $Ascii.GetString([IO.File]::ReadAllBytes($FingerprintPath))
if ($FingerprintText -cne "$DerivedFingerprint`n") {
    Fail "publisher authenticity FAILED: fingerprint does not match allowed-signers"
}
$SumsPath = Join-Path $PackageRoot "SHA256SUMS"
$SignaturePath = Join-Path $PackageRoot "SHA256SUMS.sig"
foreach ($SignedFile in @($SumsPath, $SignaturePath)) {
    $Item = Get-Item -Force -LiteralPath $SignedFile -ErrorAction Stop
    if ($Item.PSIsContainer -or ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail "publisher authenticity FAILED: signed object or signature is linked"
    }
}
$SignatureText = $Ascii.GetString([IO.File]::ReadAllBytes($SignaturePath))
if ($SignatureText -cnotmatch '^-----BEGIN SSH SIGNATURE-----\n((?:[A-Za-z0-9+/]+={0,2}\n)+)-----END SSH SIGNATURE-----\n$') {
    Fail "publisher authenticity FAILED: SSH signature armor is not canonical"
}
try { [byte[]]$DecodedSignature = [Convert]::FromBase64String($Matches[1].Replace("`n", "")) } catch {
    Fail "publisher authenticity FAILED: SSH signature armor is invalid base64"
}
if ($DecodedSignature.Length -lt 6 -or
    $Ascii.GetString($DecodedSignature[0..5]) -cne "SSHSIG") {
    Fail "publisher authenticity FAILED: SSH signature payload is invalid"
}
$EncodedSignature = [Convert]::ToBase64String($DecodedSignature)
$CanonicalLines = for ($Offset = 0; $Offset -lt $EncodedSignature.Length; $Offset += 70) {
    $EncodedSignature.Substring($Offset, [Math]::Min(70, $EncodedSignature.Length - $Offset))
}
$CanonicalSignature = "-----BEGIN SSH SIGNATURE-----`n" +
    ($CanonicalLines -join "`n") + "`n-----END SSH SIGNATURE-----`n"
if ($SignatureText -cne $CanonicalSignature) {
    Fail "publisher authenticity FAILED: SSH signature armor is not canonical"
}
$SshKeygen = Join-Path ([Environment]::SystemDirectory) "OpenSSH\ssh-keygen.exe"
if (-not (Test-Path -LiteralPath $SshKeygen -PathType Leaf)) {
    Fail "publisher authenticity FAILED: system OpenSSH ssh-keygen is required"
}
Assert-ProtectedTrustAcl $SshKeygen "system ssh-keygen" -AllowTrustedInstaller
Assert-ProtectedTrustAncestors $SshKeygen "system ssh-keygen" -AllowTrustedInstaller
$Cmd = Join-Path ([Environment]::SystemDirectory) "cmd.exe"
if (-not (Test-Path -LiteralPath $Cmd -PathType Leaf)) {
    Fail "publisher authenticity FAILED: system command processor is required"
}
Assert-ProtectedTrustAcl $Cmd "system command processor" -AllowTrustedInstaller
Assert-ProtectedTrustAncestors $Cmd "system command processor" -AllowTrustedInstaller
$Docker = Join-Path (
    [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles)
) "Docker\Docker\resources\bin\docker.exe"
$DockerLabel = "Docker CLI"
if (-not (Test-Path -LiteralPath $Docker -PathType Leaf)) {
        Fail "publisher authenticity FAILED: fixed $DockerLabel is required"
    }
Assert-ProtectedTrustAcl $Docker "system $DockerLabel" -AllowTrustedInstaller
Assert-ProtectedTrustAncestors $Docker "system $DockerLabel" -AllowTrustedInstaller
$SafeSshKeygen = ConvertTo-CmdSafePath $SshKeygen "system ssh-keygen path"
$SafeAllowedSigners = ConvertTo-CmdSafePath $AllowedSigners "allowed-signers path"
$SafeSignaturePath = ConvertTo-CmdSafePath $SignaturePath "signature path"
$SafeSumsPath = ConvertTo-CmdSafePath $SumsPath "SHA256SUMS path"
$CommandLine = "$SafeSshKeygen -Y verify -f $SafeAllowedSigners " +
    "-I ruisheng-release -n ruisheng-candidate-v1 -s $SafeSignaturePath " +
    "< $SafeSumsPath"
$Start = [Diagnostics.ProcessStartInfo]::new()
$Start.FileName = $Cmd
$Start.UseShellExecute = $false
$Start.RedirectStandardOutput = $true
$Start.RedirectStandardError = $true
foreach ($Argument in @("/d", "/q", "/v:off", "/c", $CommandLine)) {
    [void]$Start.ArgumentList.Add($Argument)
}
[byte[]]$SumsBytes = [IO.File]::ReadAllBytes($SumsPath)
$Process = [Diagnostics.Process]::Start($Start)
$SshOutputTask = $Process.StandardOutput.ReadToEndAsync()
$SshErrorTask = $Process.StandardError.ReadToEndAsync()
if (-not $Process.WaitForExit(30000)) {
    $Process.Kill($true)
    $Process.WaitForExit()
    Fail "publisher authenticity FAILED: OpenSSH signature verification timed out"
}
$SshError = $SshErrorTask.GetAwaiter().GetResult()
$SshOutputTask.GetAwaiter().GetResult() | Out-Null
if ($Process.ExitCode -ne 0) {
    Fail "publisher authenticity FAILED: OpenSSH signature verification failed: $($SshError.Trim())"
}
$AuthenticatedSums = @{}
try {
    $AuthenticatedSumsText = [Text.UTF8Encoding]::new($false, $true).GetString($SumsBytes)
} catch {
    Fail "publisher authenticity FAILED: SHA256SUMS is not valid UTF-8"
}
if (-not $AuthenticatedSumsText.EndsWith("`n") -or $AuthenticatedSumsText.Contains("`r")) {
    Fail "publisher authenticity FAILED: SHA256SUMS must use canonical LF line endings"
}
$AuthenticatedLineNumber = 0
($AuthenticatedSumsText.Substring(0, $AuthenticatedSumsText.Length - 1) -csplit "`n") |
    ForEach-Object {
        $AuthenticatedLineNumber++
        if ($_ -cnotmatch '^([0-9a-f]{64})  ([^\\]+)$') {
            Fail "publisher authenticity FAILED: invalid SHA256SUMS entry at line $AuthenticatedLineNumber"
        }
        $Relative = $Matches[2]
        $Parts = $Relative.Split("/")
        if ($Relative.StartsWith("/") -or $Parts.Contains("") -or
            $Parts.Contains(".") -or $Parts.Contains("..")) {
            Fail "publisher authenticity FAILED: unsafe SHA256SUMS path: $Relative"
        }
        if ($AuthenticatedSums.ContainsKey($Relative)) {
            Fail "publisher authenticity FAILED: duplicate SHA256SUMS path: $Relative"
        }
        $AuthenticatedSums[$Relative] = $Matches[1]
    }
if (-not $AuthenticatedSums.ContainsKey("MANIFEST.json")) {
    Fail "publisher authenticity FAILED: MANIFEST.json is absent from SHA256SUMS"
}
$ManifestPath = Join-Path $PackageRoot "MANIFEST.json"
$ManifestBytes = [IO.File]::ReadAllBytes($ManifestPath)
$ManifestHasher = [Security.Cryptography.SHA256]::Create()
try {
    $ManifestDigest = ([BitConverter]::ToString(
        $ManifestHasher.ComputeHash($ManifestBytes)
    )).Replace("-", "").ToLowerInvariant()
} finally { $ManifestHasher.Dispose() }
if ($ManifestDigest -cne $AuthenticatedSums["MANIFEST.json"]) {
    Fail "publisher authenticity FAILED: SHA-256 mismatch for MANIFEST.json"
}
try {
    $Manifest = [Text.UTF8Encoding]::new($false, $true).GetString($ManifestBytes) |
        ConvertFrom-Json
} catch {
    Fail "publisher authenticity FAILED: cannot parse authenticated MANIFEST.json"
}
$ExpectedAuthenticity = @{
    status = "SIGNED"; scheme = "openssh-sshsig"; publisher = "ruisheng-release"
    namespace = "ruisheng-candidate-v1"; key_type = "ssh-ed25519"
    key_fingerprint = $DerivedFingerprint; signed_object = "SHA256SUMS"
    signature_file = "SHA256SUMS.sig"
}
if ($Manifest.schema_version -ne 2 -or
    @($Manifest.authenticity.PSObject.Properties).Count -ne $ExpectedAuthenticity.Count -or
    @($ExpectedAuthenticity.Keys | Where-Object {
        $Manifest.authenticity.PSObject.Properties.Name -cnotcontains $_ -or
        $Manifest.authenticity.$_ -cne $ExpectedAuthenticity[$_]
    }).Count -ne 0) {
    Fail "publisher authenticity FAILED: signed manifest authenticity contract is invalid"
}
function Test-SafeRelativePath([string]$Value) {
    $Parts = $Value.Split("/")
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value.Contains("\") -or
        $Value.StartsWith("/") -or $Parts.Contains("") -or $Parts.Contains("..") -or
        $Parts.Contains(".")) {
        Fail "Unsafe package path: $Value"
    }
}

function Get-Sha256Bytes([byte[]]$Value) {
    $Hasher = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($Hasher.ComputeHash($Value))).Replace("-", "").ToLowerInvariant()
    } finally {
        $Hasher.Dispose()
    }
}

function Read-TarEntries([string]$ArchivePath, [string[]]$WantedNames) {
    if (-not ("System.Formats.Tar.TarReader" -as [type])) {
        Fail "PowerShell 7.3 or newer is required for pre-load archive validation"
    }
    $Wanted = [System.Collections.Generic.HashSet[string]]::new(
        $WantedNames, [System.StringComparer]::Ordinal
    )
    $Names = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
    $Values = @{}
    $File = $null
    $Gzip = $null
    $Reader = $null
    try {
        $File = [IO.File]::OpenRead($ArchivePath)
        $Gzip = [IO.Compression.GZipStream]::new(
            $File, [IO.Compression.CompressionMode]::Decompress, $false
        )
        $Reader = [System.Formats.Tar.TarReader]::new($Gzip, $false)
        while ($null -ne ($Entry = $Reader.GetNextEntry($false))) {
            $Name = [string]$Entry.Name
            Test-SafeRelativePath $Name.TrimEnd("/")
            if (-not $Names.Add($Name)) {
                Fail "Archive contains duplicate member: $ArchivePath`:$Name"
            }
            if ($Entry.EntryType.ToString() -in @("SymbolicLink", "HardLink")) {
                Fail "Archive contains link member: $ArchivePath`:$Name"
            }
            if ($Wanted.Contains($Name)) {
                if ($Entry.Length -gt 16MB) {
                    Fail "Archive metadata member is too large: $ArchivePath`:$Name"
                }
                if ($null -eq $Entry.DataStream) {
                    Fail "Archive member is not a regular file: $ArchivePath`:$Name"
                }
                $Buffer = [IO.MemoryStream]::new()
                try {
                    $Entry.DataStream.CopyTo($Buffer)
                    $Values[$Name] = $Buffer.ToArray()
                } finally {
                    $Buffer.Dispose()
                }
            }
        }
    } catch {
        Fail "Invalid Docker archive ${ArchivePath}: $($_.Exception.Message)"
    } finally {
        if ($null -ne $Reader) { $Reader.Dispose() }
        if ($null -ne $Gzip) { $Gzip.Dispose() }
        if ($null -ne $File) { $File.Dispose() }
    }
    return [PSCustomObject]@{ Names = $Names; Values = $Values }
}

function Read-ArchiveSha256Blob(
    [string]$ArchivePath,
    [object]$DigestValue,
    [string]$Label,
    [bool]$AllowMissing = $false
) {
    $Digest = [string]$DigestValue
    if ($Digest -notmatch '^sha256:[0-9a-f]{64}$') {
        Fail "Archive $Label digest is invalid: $ArchivePath"
    }
    $BlobName = "blobs/sha256/$($Digest.Substring(7))"
    $Record = Read-TarEntries $ArchivePath @($BlobName)
    if (-not $Record.Values.ContainsKey($BlobName)) {
        if ($AllowMissing) { return $null }
        Fail "Archive $Label blob is missing: $ArchivePath`:$BlobName"
    }
    [byte[]]$Bytes = $Record.Values[$BlobName]
    if ("sha256:$(Get-Sha256Bytes $Bytes)" -cne $Digest) {
        Fail "Archive $Label digest mismatch: $ArchivePath"
    }
    return ,$Bytes
}

function ConvertFrom-ArchiveJsonObject(
    [byte[]]$Bytes,
    [string]$ArchivePath,
    [string]$Label
) {
    try {
        $Utf8 = [Text.UTF8Encoding]::new($false, $true)
        $Value = $Utf8.GetString($Bytes) | ConvertFrom-Json
    } catch {
        Fail "Archive $Label is invalid JSON: $ArchivePath"
    }
    if ($Value -isnot [System.Management.Automation.PSCustomObject]) {
        Fail "Archive $Label root is invalid: $ArchivePath"
    }
    return $Value
}

function Test-SlsaProvenanceStatement(
    [object]$Statement,
    [string]$ArchivePath,
    [string]$MainManifestDigest
) {
    $Subjects = $Statement.subject
    if ($Statement -isnot [System.Management.Automation.PSCustomObject] -or
        $Statement._type -isnot [string] -or
        $Statement._type -cne "https://in-toto.io/Statement/v0.1" -or
        $Statement.predicateType -isnot [string] -or
        $Statement.predicateType -cne "https://slsa.dev/provenance/v1" -or
        $Statement.predicate -isnot [System.Management.Automation.PSCustomObject] -or
        $Subjects -isnot [object[]] -or $Subjects.Count -eq 0) {
        Fail "Archive provenance statement is invalid: $ArchivePath"
    }
    $ExpectedSubject = $MainManifestDigest.Substring(7)
    $SubjectDigests = @()
    foreach ($Subject in $Subjects) {
        if ($Subject -isnot [System.Management.Automation.PSCustomObject] -or
            $Subject.name -isnot [string] -or
            [string]::IsNullOrEmpty($Subject.name) -or
            $Subject.digest -isnot [System.Management.Automation.PSCustomObject] -or
            $Subject.digest.sha256 -isnot [string] -or
            $Subject.digest.sha256 -notmatch '^[0-9a-f]{64}$') {
            Fail "Archive provenance statement is invalid: $ArchivePath"
        }
        $SubjectDigests += [string]$Subject.digest.sha256
    }
    if ($ExpectedSubject -cnotin $SubjectDigests) {
        Fail "Archive provenance statement subject mismatch: $ArchivePath"
    }
}

function Resolve-MainManifestDigest(
    [string]$ArchivePath,
    [string]$DescriptorDigest,
    [object]$DescriptorValue,
    [string]$ConfigDigest,
    [object]$Config
) {
    if ($DescriptorValue.config -is [System.Management.Automation.PSCustomObject] -and
        $DescriptorValue.config.digest -is [string] -and
        $DescriptorValue.config.digest -ceq $ConfigDigest) {
        return $DescriptorDigest
    }
    if ($null -eq $DescriptorValue.PSObject.Properties["manifests"]) {
        return $null
    }
    if ($DescriptorValue.manifests -isnot [object[]]) {
        Fail "Archive nested descriptors are invalid: $ArchivePath"
    }
    $MatchingNested = @()
    foreach ($NestedDescriptor in $DescriptorValue.manifests) {
        if ($NestedDescriptor -isnot [System.Management.Automation.PSCustomObject]) {
            Fail "Archive nested descriptor is invalid: $ArchivePath"
        }
        $NestedDigest = [string]$NestedDescriptor.digest
        [byte[]]$NestedBytes = Read-ArchiveSha256Blob `
            $ArchivePath $NestedDigest "nested descriptor" $true
        if ($null -eq $NestedBytes) {
            # Docker 29 can retain source index entries while exporting only
            # the manifest blob for the selected local platform.
            continue
        }
        $NestedValue = ConvertFrom-ArchiveJsonObject `
            $NestedBytes $ArchivePath "nested descriptor"
        $NestedConfig = $NestedValue.config
        if ($NestedConfig -isnot [System.Management.Automation.PSCustomObject]) {
            continue
        }
        if ($null -ne $NestedDescriptor.platform -and
            $NestedDescriptor.platform -isnot [System.Management.Automation.PSCustomObject]) {
            Fail "Archive nested descriptor platform is invalid: $ArchivePath"
        }
        if ($NestedConfig.digest -cne $ConfigDigest) {
            [byte[]]$NestedConfigBytes = Read-ArchiveSha256Blob `
                $ArchivePath $NestedConfig.digest "nested config"
            $NestedConfigValue = ConvertFrom-ArchiveJsonObject `
                $NestedConfigBytes $ArchivePath "nested config"
            if ($NestedConfigValue.os -isnot [string] -or
                $NestedConfigValue.architecture -isnot [string] -or
                $NestedConfigValue.os -cne "unknown" -or
                $NestedConfigValue.architecture -cne "unknown" -or
                ($null -ne $NestedDescriptor.platform -and
                    ($NestedDescriptor.platform.os -cne "unknown" -or
                        $NestedDescriptor.platform.architecture -cne "unknown"))) {
                Fail "Archive contains an additional runnable descriptor: $ArchivePath"
            }
            continue
        }
        if ($null -ne $NestedDescriptor.platform -and
            ($NestedDescriptor.platform.os -cne $Config.os -or
                $NestedDescriptor.platform.architecture -cne $Config.architecture)) {
            Fail "Archive nested descriptor platform mismatch: $ArchivePath"
        }
        $MatchingNested += $NestedDigest
    }
    if ($MatchingNested.Count -gt 1) {
        Fail "Archive main descriptor is not unique: $ArchivePath"
    }
    if ($MatchingNested.Count -eq 1) { return $MatchingNested[0] }
    return $null
}

function Test-ProvenanceAttachment(
    [string]$ArchivePath,
    [object]$Descriptor,
    [object]$DescriptorValue,
    [string]$MainManifestDigest
) {
    $ManifestMediaType = "application/vnd.oci.image.manifest.v1+json"
    if ($Descriptor.mediaType -isnot [string] -or
        $Descriptor.mediaType -cne $ManifestMediaType -or
        $DescriptorValue.schemaVersion -isnot [long] -or
        $DescriptorValue.schemaVersion -cne 2 -or
        $DescriptorValue.mediaType -isnot [string] -or
        $DescriptorValue.mediaType -cne $ManifestMediaType) {
        Fail "Unsupported archive attachment: $ArchivePath"
    }
    if ($Descriptor.annotations -isnot [System.Management.Automation.PSCustomObject] -or
        $Descriptor.annotations.'io.containerd.manifest.subject' -isnot [string] -or
        $Descriptor.annotations.'io.containerd.manifest.subject' -cne $MainManifestDigest) {
        Fail "Archive provenance subject mismatch: $ArchivePath"
    }
    if ($null -ne $Descriptor.platform -and
        ($Descriptor.platform -isnot [System.Management.Automation.PSCustomObject] -or
            $Descriptor.platform.os -isnot [string] -or
            $Descriptor.platform.architecture -isnot [string] -or
            $Descriptor.platform.os -cne "unknown" -or
            $Descriptor.platform.architecture -cne "unknown")) {
        Fail "Archive provenance descriptor platform mismatch: $ArchivePath"
    }
    if ($null -ne $DescriptorValue.subject -and
        ($DescriptorValue.subject -isnot [System.Management.Automation.PSCustomObject] -or
            $DescriptorValue.subject.digest -isnot [string] -or
            $DescriptorValue.subject.digest -cne $MainManifestDigest)) {
        Fail "Archive provenance subject mismatch: $ArchivePath"
    }
    $ConfigDescriptor = $DescriptorValue.config
    if ($ConfigDescriptor -isnot [System.Management.Automation.PSCustomObject] -or
        $ConfigDescriptor.mediaType -isnot [string] -or
        $ConfigDescriptor.mediaType -cne "application/vnd.oci.image.config.v1+json") {
        Fail "Archive provenance config is invalid: $ArchivePath"
    }
    [byte[]]$ProvenanceConfigBytes = Read-ArchiveSha256Blob `
        $ArchivePath $ConfigDescriptor.digest "provenance config"
    $ProvenanceConfig = ConvertFrom-ArchiveJsonObject `
        $ProvenanceConfigBytes $ArchivePath "provenance config"
    if ($ProvenanceConfig.os -isnot [string] -or
        $ProvenanceConfig.architecture -isnot [string] -or
        $ProvenanceConfig.os -cne "unknown" -or
        $ProvenanceConfig.architecture -cne "unknown") {
        Fail "Archive provenance config platform mismatch: $ArchivePath"
    }
    $Layers = $DescriptorValue.layers
    if ($Layers -isnot [object[]] -or $Layers.Count -ne 1) {
        Fail "Archive provenance layers are invalid: $ArchivePath"
    }
    foreach ($Layer in $Layers) {
        if ($Layer -isnot [System.Management.Automation.PSCustomObject] -or
            $Layer.mediaType -isnot [string] -or
            $Layer.mediaType -cne "application/vnd.in-toto+json") {
            Fail "Archive provenance layer media type is invalid: $ArchivePath"
        }
        if ($Layer.annotations -isnot [System.Management.Automation.PSCustomObject] -or
            $Layer.annotations.'in-toto.io/predicate-type' -isnot [string] -or
            $Layer.annotations.'in-toto.io/predicate-type' -cne
                "https://slsa.dev/provenance/v1") {
            Fail "Archive provenance layer is invalid: $ArchivePath"
        }
        [byte[]]$LayerBytes = Read-ArchiveSha256Blob `
            $ArchivePath $Layer.digest "provenance layer"
        $Statement = ConvertFrom-ArchiveJsonObject `
            $LayerBytes $ArchivePath "provenance layer"
        Test-SlsaProvenanceStatement $Statement $ArchivePath $MainManifestDigest
    }
}

function Get-DockerArchiveIdentity([string]$ArchivePath, [string]$ExpectedReference) {
    $Headers = Read-TarEntries $ArchivePath @("manifest.json", "index.json")
    if (-not $Headers.Values.ContainsKey("manifest.json")) {
        Fail "Archive is missing manifest.json: $ArchivePath"
    }
    try {
        $ArchiveManifest = [Text.Encoding]::UTF8.GetString(
            [byte[]]$Headers.Values["manifest.json"]
        ) | ConvertFrom-Json
    } catch {
        Fail "Archive manifest is invalid JSON: $ArchivePath"
    }
    $ManifestEntries = @($ArchiveManifest)
    if ($ManifestEntries.Count -ne 1) {
        Fail "Archive must contain exactly one image: $ArchivePath"
    }
    $RepoTags = @($ManifestEntries[0].RepoTags)
    if ($RepoTags.Count -ne 1 -or $RepoTags[0] -cne $ExpectedReference) {
        Fail "Archive candidate tag mismatch: $ArchivePath"
    }
    $ConfigName = [string]$ManifestEntries[0].Config
    Test-SafeRelativePath $ConfigName
    $ConfigRecord = Read-TarEntries $ArchivePath @($ConfigName)
    if (-not $ConfigRecord.Values.ContainsKey($ConfigName)) {
        Fail "Archive config is missing: $ArchivePath`:$ConfigName"
    }
    $ConfigBytes = [byte[]]$ConfigRecord.Values[$ConfigName]
    try {
        $Config = [Text.Encoding]::UTF8.GetString($ConfigBytes) | ConvertFrom-Json
    } catch {
        Fail "Archive config is invalid JSON: $ArchivePath"
    }
    $ConfigDigest = "sha256:$(Get-Sha256Bytes $ConfigBytes)"
    $ImageId = $ConfigDigest
    if ($Headers.Names.Contains("index.json")) {
        try {
            $Index = [Text.Encoding]::UTF8.GetString(
                [byte[]]$Headers.Values["index.json"]
            ) | ConvertFrom-Json
        } catch {
            Fail "Archive index is invalid JSON: $ArchivePath"
        }
        $Descriptors = $Index.manifests
        if ($Descriptors -isnot [object[]] -or $Descriptors.Count -eq 0) {
            Fail "Archive index must contain image descriptors: $ArchivePath"
        }
        $LoadedDescriptors = @()
        foreach ($Descriptor in $Descriptors) {
            if ($Descriptor -isnot [System.Management.Automation.PSCustomObject]) {
                Fail "Archive descriptor is invalid: $ArchivePath"
            }
            $DescriptorDigest = [string]$Descriptor.digest
            [byte[]]$DescriptorBytes = Read-ArchiveSha256Blob `
                $ArchivePath $DescriptorDigest "descriptor"
            $DescriptorValue = ConvertFrom-ArchiveJsonObject `
                $DescriptorBytes $ArchivePath "descriptor"
            $Resolved = Resolve-MainManifestDigest `
                $ArchivePath $DescriptorDigest $DescriptorValue $ConfigDigest $Config
            $LoadedDescriptors += [PSCustomObject]@{
                Descriptor = $Descriptor
                Digest = $DescriptorDigest
                Value = $DescriptorValue
                Resolved = $Resolved
            }
        }
        $MainDescriptors = @($LoadedDescriptors | Where-Object { $null -ne $_.Resolved })
        if ($MainDescriptors.Count -ne 1) {
            Fail "Archive main descriptor is not unique: $ArchivePath"
        }
        $ImageId = [string]$MainDescriptors[0].Digest
        $MainManifestDigest = [string]$MainDescriptors[0].Resolved
        foreach ($Loaded in $LoadedDescriptors) {
            if ($null -eq $Loaded.Resolved) {
                Test-ProvenanceAttachment `
                    $ArchivePath $Loaded.Descriptor $Loaded.Value $MainManifestDigest
            }
        }
    }
    return [PSCustomObject]@{
        ImageId = $ImageId
        Os = [string]$Config.os
        Architecture = [string]$Config.architecture
    }
}

if ($Manifest.candidate_id -notmatch '^[a-z0-9][a-z0-9._-]{0,62}$') {
    Fail "Invalid candidate ID"
}
if ($Manifest.schema_version -ne 2 -or $Manifest.authenticity.status -ne "SIGNED") {
    Fail "Manifest authenticity contract is invalid"
}
if (@($Manifest.images).Count -ne 5) {
    Fail "Manifest must contain five images"
}

$ExpectedFiles = [System.Collections.Generic.HashSet[string]]::new(
    [string[]]$FixedFiles, [System.StringComparer]::Ordinal
)
$References = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
$ImageIds = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
for ($Index = 0; $Index -lt $Components.Count; $Index++) {
    $Image = $Manifest.images[$Index]
    $Component = $Components[$Index]
    $ExpectedReference = "ruisheng-candidate/${Component}:$($Manifest.candidate_id)"
    $ExpectedArchive = "images/${Component}.tar.gz"
    if ($Image.component -ne $Component -or $Image.candidate_reference -ne $ExpectedReference) {
        Fail "Candidate reference mismatch for $Component"
    }
    if ($Image.archive -ne $ExpectedArchive) {
        Fail "Archive path mismatch for $Component"
    }
    if ($Image.os -ne $Manifest.target_os -or
        $Image.architecture -ne $Manifest.target_architecture) {
        Fail "Platform mismatch for $Component"
    }
    if ($Image.image_id -notmatch '^sha256:[0-9a-f]{64}$') {
        Fail "Invalid image ID for $Component"
    }
    if (-not $References.Add([string]$Image.candidate_reference) -or
        -not $ImageIds.Add([string]$Image.image_id)) {
        Fail "Duplicate image identity in manifest"
    }
    Test-SafeRelativePath $ExpectedArchive
    [void]$ExpectedFiles.Add($ExpectedArchive)
}

$ActualFiles = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
Get-ChildItem -LiteralPath $PackageRoot -Force -Recurse | ForEach-Object {
    $Relative = $_.FullName.Substring($PackageRoot.Length).TrimStart("\", "/").Replace("\", "/")
    Test-SafeRelativePath $Relative
    if (($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail "Candidate package contains a link: $Relative"
    }
    if ($_.PSIsContainer) {
        if ($Relative -ne "images") {
            Fail "Candidate package contains an extra directory: $Relative"
        }
    } else {
        [void]$ActualFiles.Add($Relative)
    }
}
$Missing = @($ExpectedFiles | Where-Object { -not $ActualFiles.Contains($_) })
$Extra = @($ActualFiles | Where-Object { -not $ExpectedFiles.Contains($_) })
if ($Missing.Count -ne 0 -or $Extra.Count -ne 0) {
    Fail "publisher authenticity FAILED: candidate file allowlist mismatch: missing=$($Missing -join ','), extra=$($Extra -join ',')"
}

$Sums = $AuthenticatedSums
$ExpectedSums = @($ExpectedFiles | Where-Object {
    $_ -ne "SHA256SUMS" -and $_ -ne "SHA256SUMS.sig"
})
$MissingSums = @($ExpectedSums | Where-Object { -not $Sums.ContainsKey($_) })
$ExtraSums = @($Sums.Keys | Where-Object { -not $ExpectedFiles.Contains($_) })
if ($Sums.Count -ne $ExpectedSums.Count -or $MissingSums.Count -ne 0 -or $ExtraSums.Count -ne 0) {
    Fail "publisher authenticity FAILED: SHA256SUMS allowlist mismatch: missing=$($MissingSums -join ','), extra=$($ExtraSums -join ',')"
}
foreach ($Relative in $ExpectedSums) {
    $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $PackageRoot $Relative)).Hash.ToLowerInvariant()
    if ($Actual -ne $Sums[$Relative]) {
        Fail "publisher authenticity FAILED: SHA-256 mismatch for ${Relative}: expected $($Sums[$Relative]), got $Actual"
    }
}
foreach ($Image in $Manifest.images) {
    if ($Sums[[string]$Image.archive] -ne $Image.sha256) {
        Fail "Manifest/SHA256SUMS mismatch for $($Image.archive)"
    }
    $ArchivePath = Join-Path $PackageRoot $Image.archive
    $ArchiveIdentity = Get-DockerArchiveIdentity $ArchivePath $Image.candidate_reference
    if ($ArchiveIdentity.ImageId -cne $Image.image_id -or
        $ArchiveIdentity.Os -cne $Image.os -or
        $ArchiveIdentity.Architecture -cne $Image.architecture) {
        Fail "Archive identity mismatch for $($Image.component)"
    }
}

$ArchiveHandles = @{}
try {
    foreach ($Image in $Manifest.images) {
        $ArchivePath = Join-Path $PackageRoot $Image.archive
        $Handle = [IO.File]::Open(
            $ArchivePath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
        )
        $ArchiveHandles[[string]$Image.component] = $Handle
        $Hasher = [Security.Cryptography.SHA256]::Create()
        try {
            $LockedDigest = ([BitConverter]::ToString(
                $Hasher.ComputeHash($Handle)
            )).Replace("-", "").ToLowerInvariant()
        } finally { $Hasher.Dispose() }
        $Handle.Position = 0
        if ($LockedDigest -cne $Sums[[string]$Image.archive]) {
            Fail "publisher authenticity FAILED: archive changed before load: $($Image.archive)"
        }
    }
    Write-Host "[verify] Publisher authenticity VERIFIED; file allowlist, SHA-256, and archive identities passed."
    foreach ($Image in $Manifest.images) {
        Write-Host "[verify] Loading $($Image.component) from $($Image.archive)"
        & $Docker image load --input (Join-Path $PackageRoot $Image.archive) | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Fail "Docker load failed for $($Image.component)"
        }
    }
} finally {
    foreach ($Handle in $ArchiveHandles.Values) {
        if ($null -ne $Handle) { $Handle.Dispose() }
    }
}

foreach ($Image in $Manifest.images) {
    $Raw = & $Docker image inspect $Image.candidate_reference --format '{{json .}}'
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker inspect failed for $($Image.component)"
    }
    $Inspected = $Raw | ConvertFrom-Json
    if ($Inspected.Id -ne $Image.image_id -or $Inspected.Os -ne $Image.os -or
        $Inspected.Architecture -ne $Image.architecture) {
        Fail "Loaded image identity mismatch for $($Image.component)"
    }
    if (@($Inspected.RepoTags) -notcontains $Image.candidate_reference) {
        Fail "Loaded candidate tag missing for $($Image.component)"
    }
}

$ComposeArgs = @(
    "compose", "--env-file", $ComposeEnvPath,
    "-f", (Join-Path $PackageRoot "docker-compose.prod.yml")
)
$ResolvedImages = @(& $Docker @ComposeArgs config --images | Where-Object { $_ })
if ($LASTEXITCODE -ne 0) {
    Fail "Docker Compose image rendering failed"
}
$ExpectedImages = @($Manifest.images | ForEach-Object { $_.candidate_reference } | Sort-Object -Unique)
$ActualImages = @($ResolvedImages | Sort-Object -Unique)
if ((Compare-Object $ExpectedImages $ActualImages).Count -ne 0) {
    Fail "Compose image set does not match the manifest"
}
$ApiReference = "ruisheng-candidate/api:$($Manifest.candidate_id)"
if (@($ResolvedImages | Where-Object { $_ -eq $ApiReference }).Count -ne 2) {
    Fail "Compose migrate/api do not share exactly one API image"
}
$ComposeConfig = (& $Docker @ComposeArgs config --format json) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    Fail "Docker Compose config rendering failed"
}
$ServiceNames = @($ComposeConfig.services.PSObject.Properties.Name | Sort-Object)
$ExpectedServices = @("api", "gw", "migrate", "postgres", "redis", "web")
if ((Compare-Object $ExpectedServices $ServiceNames).Count -ne 0) {
    Fail "Compose service set mismatch"
}
$ExpectedServiceImages = @{
    postgres = "ruisheng-candidate/postgres:$($Manifest.candidate_id)"
    redis = "ruisheng-candidate/redis:$($Manifest.candidate_id)"
    migrate = "ruisheng-candidate/api:$($Manifest.candidate_id)"
    api = "ruisheng-candidate/api:$($Manifest.candidate_id)"
    gw = "ruisheng-candidate/gw:$($Manifest.candidate_id)"
    web = "ruisheng-candidate/web:$($Manifest.candidate_id)"
}
$ExpectedPlatform = "$($Manifest.target_os)/$($Manifest.target_architecture)"
foreach ($Property in $ComposeConfig.services.PSObject.Properties) {
    if ($Property.Value.image -cne $ExpectedServiceImages[$Property.Name]) {
        Fail "Compose image mismatch for service: $($Property.Name)"
    }
    if ($Property.Value.platform -cne $ExpectedPlatform) {
        Fail "Compose platform mismatch for service: $($Property.Name)"
    }
    if ($null -ne $Property.Value.build -or $Property.Value.pull_policy -ne "never") {
        Fail "Compose service may build or pull: $($Property.Name)"
    }
}

Write-Host "[verify] Integrity and loaded image identity passed."
Write-Host "[verify] Publisher authenticity VERIFIED; CAP-1/G0-03 authenticity gate passed."
Write-Warning "B-04 remains BLOCKED; close it only through the independent field acceptance workflow."
exit 2
} finally {
    foreach ($ProtectedPath in @($DockerConfig, $SnapshotRoot)) {
        if ([string]::IsNullOrWhiteSpace($ProtectedPath) -or
            -not (Test-Path -LiteralPath $ProtectedPath)) {
            continue
        }
        try {
            Remove-Item -LiteralPath $ProtectedPath -Recurse -Force -ErrorAction Stop
        } catch {
            Write-Error "[verify] protected work cleanup failed: ${ProtectedPath}: $($_.Exception.Message)"
            throw
        }
    }
}
