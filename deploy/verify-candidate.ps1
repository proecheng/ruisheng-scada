[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$PackagePath = ".",

    [Parameter(Position = 1)]
    [string]$SiteEnvPath = ""
)

$ErrorActionPreference = "Stop"
$MaxReleaseJsonBytes = 4MB
$MaxDockerArchiveMembers = 32768
$MaxDockerArchiveMemberBytes = 8GB
$MaxDockerArchiveTotalBytes = 32GB
$MaxDockerDescriptorReferences = 32768
$MaxDockerMetadataBytes = 64MB
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

if (-not ("RuishengCandidateSnapshotNativeMethods" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class RuishengCandidateSnapshotNativeMethods
{
    [StructLayout(LayoutKind.Sequential)]
    public struct NativeFileTime
    {
        public UInt32 Low;
        public UInt32 High;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct ByHandleFileInformation
    {
        public UInt32 FileAttributes;
        public NativeFileTime CreationTime;
        public NativeFileTime LastAccessTime;
        public NativeFileTime LastWriteTime;
        public UInt32 VolumeSerialNumber;
        public UInt32 FileSizeHigh;
        public UInt32 FileSizeLow;
        public UInt32 NumberOfLinks;
        public UInt32 FileIndexHigh;
        public UInt32 FileIndexLow;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool GetFileInformationByHandle(
        SafeFileHandle handle,
        out ByHandleFileInformation information
    );
}
'@
}

function Join-FileIdentityUInt32([UInt32]$High, [UInt32]$Low) {
    return ([UInt64]$High * 4294967296L) + [UInt64]$Low
}

function Get-OpenFileIdentity([IO.FileStream]$Stream, [string]$Relative) {
    $Information = [RuishengCandidateSnapshotNativeMethods+ByHandleFileInformation]::new()
    if (-not [RuishengCandidateSnapshotNativeMethods]::GetFileInformationByHandle(
        $Stream.SafeFileHandle, [ref]$Information
    )) {
        $Code = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        Fail "publisher authenticity FAILED: cannot identify open candidate file (${Relative}): Win32 error $Code"
    }
    return [pscustomobject]@{
        VolumeSerialNumber = [UInt32]$Information.VolumeSerialNumber
        FileIndex = Join-FileIdentityUInt32 $Information.FileIndexHigh $Information.FileIndexLow
        NumberOfLinks = [UInt32]$Information.NumberOfLinks
        Length = Join-FileIdentityUInt32 $Information.FileSizeHigh $Information.FileSizeLow
        CreationTime = Join-FileIdentityUInt32 `
            $Information.CreationTime.High $Information.CreationTime.Low
        LastWriteTime = Join-FileIdentityUInt32 `
            $Information.LastWriteTime.High $Information.LastWriteTime.Low
        FileAttributes = [UInt32]$Information.FileAttributes
    }
}

function Test-SameOpenFileIdentity([object]$Left, [object]$Right) {
    foreach ($Name in @(
        "VolumeSerialNumber", "FileIndex", "NumberOfLinks", "Length",
        "CreationTime", "LastWriteTime", "FileAttributes"
    )) {
        if ($Left.$Name -ne $Right.$Name) { return $false }
    }
    return $true
}

function Assert-CandidatePathIsRegular([string]$Path, [string]$Relative) {
    $Item = Get-Item -Force -LiteralPath $Path -ErrorAction Stop
    if ($Item.PSIsContainer -or
        ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail "publisher authenticity FAILED: candidate file changed or linked: $Relative"
    }
}

$Components = @("postgres", "redis", "api", "gw", "web")
$FixedFilesV2 = @(
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
    "site-modbus-probe.json.example",
    "site-serial-hardware.json.example",
    "site-serial.env.example",
    "site-serial.override.yml",
    "setup-customer.md",
    "install_serial_hardware_task.ps1",
    "probe_modbus_rtu.py",
    "run_modbus_probe.ps1",
    "serial_hardware_attach.ps1",
    "validate-network-boundary.py",
    "validate_serial_hardware.py",
    "verify-candidate.ps1",
    "verify-candidate.sh"
)
$SnapshotExpectedV2 = [System.Collections.Generic.HashSet[string]]::new(
    [string[]]$FixedFilesV2, [System.StringComparer]::Ordinal
)
foreach ($Component in $Components) {
    [void]$SnapshotExpectedV2.Add("images/$Component.tar.gz")
}
$SnapshotExpectedV3 = [System.Collections.Generic.HashSet[string]]::new(
    [string[]]@($SnapshotExpectedV2), [System.StringComparer]::Ordinal
)
[void]$SnapshotExpectedV3.Add("qualification-toolchain.tar.gz")

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
    Remove-Item Env:XDG_CONFIG_HOME -ErrorAction SilentlyContinue

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
    $SnapshotMatchesV2 = $SnapshotActualFiles.SetEquals($SnapshotExpectedV2)
    $SnapshotMatchesV3 = $SnapshotActualFiles.SetEquals($SnapshotExpectedV3)
    if ($SnapshotMatchesV2 -eq $SnapshotMatchesV3) {
        Fail "publisher authenticity FAILED: candidate file allowlist mismatch: does not match complete v2 or v3"
    }
    $ExpectedSchemaVersion = if ($SnapshotMatchesV3) { 3 } else { 2 }
    $SnapshotExpectedFiles = if ($SnapshotMatchesV3) { $SnapshotExpectedV3 } else { $SnapshotExpectedV2 }
    $FixedFiles = if ($SnapshotMatchesV3) {
        @($FixedFilesV2) + @("qualification-toolchain.tar.gz")
    } else { $FixedFilesV2 }
    $SourceIdentities = @{}
    [Int64]$SnapshotBytes = 0
    foreach ($Relative in $SnapshotExpectedFiles) {
        $SourcePath = Join-Path $SourcePackageRoot $Relative
        Assert-CandidatePathIsRegular $SourcePath $Relative
        $IdentityStream = $null
        try {
            $IdentityStream = [IO.File]::Open(
                $SourcePath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None
            )
            $SourceIdentity = Get-OpenFileIdentity $IdentityStream $Relative
            Assert-CandidatePathIsRegular $SourcePath $Relative
            if ($SourceIdentity.NumberOfLinks -ne 1 -or
                $IdentityStream.Length -ne $SourceIdentity.Length) {
                Fail "publisher authenticity FAILED: candidate file is not a unique regular file: $Relative"
            }
            $SourceIdentities[$Relative] = $SourceIdentity
            $SnapshotBytes += [Int64]$SourceIdentity.Length
        } finally {
            if ($null -ne $IdentityStream) { $IdentityStream.Dispose() }
        }
    }
    [Int64]$SnapshotReserve = [Math]::Max(64MB, [Int64]($SnapshotBytes / 10))
    $SnapshotDrive = (Get-Item -LiteralPath $SnapshotRoot -ErrorAction Stop).PSDrive
    if ($null -eq $SnapshotDrive -or $SnapshotDrive.Free -lt ($SnapshotBytes + $SnapshotReserve)) {
        Fail "publisher authenticity FAILED: insufficient free space for protected candidate snapshot"
    }
    foreach ($Relative in $SnapshotExpectedFiles) {
        $SourcePath = Join-Path $SourcePackageRoot $Relative
        $DestinationPath = Join-Path $SnapshotRoot $Relative
        Assert-CandidatePathIsRegular $SourcePath $Relative
        $InputStream = $null
        $OutputStream = $null
        try {
            $InputStream = [IO.File]::Open(
                $SourcePath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None
            )
            $ExpectedIdentity = $SourceIdentities[$Relative]
            $OpenedIdentity = Get-OpenFileIdentity $InputStream $Relative
            Assert-CandidatePathIsRegular $SourcePath $Relative
            if (-not (Test-SameOpenFileIdentity $OpenedIdentity $ExpectedIdentity) -or
                $OpenedIdentity.NumberOfLinks -ne 1 -or
                $InputStream.Length -ne $OpenedIdentity.Length) {
                Fail "publisher authenticity FAILED: candidate file changed before snapshot: $Relative"
            }
            [Int64]$ExpectedLength = $ExpectedIdentity.Length
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
            $AfterIdentity = Get-OpenFileIdentity $InputStream $Relative
            Assert-CandidatePathIsRegular $SourcePath $Relative
            if (-not (Test-SameOpenFileIdentity $AfterIdentity $ExpectedIdentity)) {
                Fail "publisher authenticity FAILED: candidate file changed during snapshot: $Relative"
            }
        } finally {
            if ($null -ne $OutputStream) { $OutputStream.Dispose() }
            if ($null -ne $InputStream) { $InputStream.Dispose() }
        }
        $PathStream = $null
        try {
            Assert-CandidatePathIsRegular $SourcePath $Relative
            $PathStream = [IO.File]::Open(
                $SourcePath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None
            )
            $PathIdentity = Get-OpenFileIdentity $PathStream $Relative
            Assert-CandidatePathIsRegular $SourcePath $Relative
            if (-not (Test-SameOpenFileIdentity $PathIdentity $SourceIdentities[$Relative])) {
                Fail "publisher authenticity FAILED: candidate file path changed during snapshot: $Relative"
            }
        } finally {
            if ($null -ne $PathStream) { $PathStream.Dispose() }
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
if ((Get-Item -LiteralPath $ManifestPath -Force).Length -gt $MaxReleaseJsonBytes) {
    Fail "MANIFEST.json exceeds the 4 MiB JSON byte limit"
}
try { $ManifestBytes = [IO.File]::ReadAllBytes($ManifestPath) }
catch [OutOfMemoryException] { Fail "MANIFEST.json exceeds available memory" }
$ManifestHasher = [Security.Cryptography.SHA256]::Create()
try {
    $ManifestDigest = ([BitConverter]::ToString(
        $ManifestHasher.ComputeHash($ManifestBytes)
    )).Replace("-", "").ToLowerInvariant()
} finally { $ManifestHasher.Dispose() }
if ($ManifestDigest -cne $AuthenticatedSums["MANIFEST.json"]) {
    Fail "publisher authenticity FAILED: SHA-256 mismatch for MANIFEST.json"
}
function Assert-NoDuplicateJsonKeys([byte[]]$Bytes, [string]$Label) {
    $Reader = $null
    try {
        $Reader = [Runtime.Serialization.Json.JsonReaderWriterFactory]::CreateJsonReader(
            $Bytes, [Xml.XmlDictionaryReaderQuotas]::Max
        )
        $Document = [Xml.XmlDocument]::new()
        $Document.Load($Reader)
    } catch {
        Fail "$Label is invalid JSON"
    } finally {
        if ($null -ne $Reader) { $Reader.Dispose() }
    }
    $Pending = [Collections.Generic.Stack[Xml.XmlElement]]::new()
    $Pending.Push($Document.DocumentElement)
    while ($Pending.Count -ne 0) {
        $Node = $Pending.Pop()
        $Names = [Collections.Generic.HashSet[string]]::new(
            [StringComparer]::Ordinal
        )
        foreach ($Child in @($Node.ChildNodes | Where-Object { $_ -is [Xml.XmlElement] })) {
            if ($Node.GetAttribute("type") -ceq "object" -and -not $Names.Add($Child.LocalName)) {
                Fail "$Label contains a duplicate object key"
            }
            $Pending.Push($Child)
        }
    }
}
# BEGIN authenticated manifest JSON helpers
function Get-AuthenticatedManifestJsonCommand {
    return Microsoft.PowerShell.Core\Get-Command `
        Microsoft.PowerShell.Utility\ConvertFrom-Json `
        -CommandType Cmdlet -ErrorAction Stop
}
function ConvertFrom-AuthenticatedManifestJson([string]$Json) {
    $Command = Get-AuthenticatedManifestJsonCommand
    if ($Command.Parameters.ContainsKey("DateKind")) {
        return & $Command -InputObject $Json -DateKind String
    }
    return & $Command -InputObject $Json
}

function Test-PythonIsoClock([string]$Value) {
    $Pattern = '^(?<hour>[0-9]{2})(?:(?::(?<minute>[0-9]{2})(?::(?<second>[0-9]{2}))?)|(?<minute>[0-9]{2})(?<second>[0-9]{2})?)?(?:[\.,][0-9]+)?\z'
    $Match = [Text.RegularExpressions.Regex]::Match(
        $Value,
        $Pattern,
        [Text.RegularExpressions.RegexOptions]::CultureInvariant,
        [TimeSpan]::FromSeconds(1)
    )
    if (-not $Match.Success) { return $false }
    $Hour = [int]$Match.Groups["hour"].Value
    $Minute = if ($Match.Groups["minute"].Success) {
        [int]$Match.Groups["minute"].Value
    } else { 0 }
    $Second = if ($Match.Groups["second"].Success) {
        [int]$Match.Groups["second"].Value
    } else { 0 }
    return $Hour -le 23 -and $Minute -le 59 -and $Second -le 59
}

function Test-PythonIsoOffset([string]$Value) {
    $Pattern = '^(?<hour>[0-9]{2})(?:(?::(?<minute>[0-9]{2})(?::(?<second>[0-9]{2}))?)|(?<minute>[0-9]{2})(?<second>[0-9]{2})?)?(?:[\.,][0-9]+)?\z'
    $Match = [Text.RegularExpressions.Regex]::Match(
        $Value,
        $Pattern,
        [Text.RegularExpressions.RegexOptions]::CultureInvariant,
        [TimeSpan]::FromSeconds(1)
    )
    if (-not $Match.Success) { return $false }
    $Hour = [int]$Match.Groups["hour"].Value
    $Minute = if ($Match.Groups["minute"].Success) {
        [int]$Match.Groups["minute"].Value
    } else { 0 }
    $Second = if ($Match.Groups["second"].Success) {
        [int]$Match.Groups["second"].Value
    } else { 0 }
    return ($Hour * 3600 + $Minute * 60 + $Second) -lt 86400
}

function Test-PythonIsoDate([string]$Value) {
    $Parsed = [DateTime]::MinValue
    foreach ($Format in @("yyyy-MM-dd", "yyyyMMdd")) {
        if ([DateTime]::TryParseExact(
                $Value,
                $Format,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::None,
                [ref]$Parsed
            )) {
            return $true
        }
    }
    $WeekMatch = [Text.RegularExpressions.Regex]::Match(
        $Value,
        '^(?:(?<year>[0-9]{4})-W(?<week>[0-9]{2})(?:-(?<day>[0-9]))?|(?<year>[0-9]{4})W(?<week>[0-9]{2})(?<day>[0-9])?)\z',
        [Text.RegularExpressions.RegexOptions]::CultureInvariant,
        [TimeSpan]::FromSeconds(1)
    )
    if (-not $WeekMatch.Success) { return $false }
    $Year = [int]$WeekMatch.Groups["year"].Value
    $Week = [int]$WeekMatch.Groups["week"].Value
    $Day = if ($WeekMatch.Groups["day"].Success) {
        [int]$WeekMatch.Groups["day"].Value
    } else { 1 }
    if ($Year -lt 1 -or $Year -gt 9999 -or $Week -lt 1 -or
        $Day -lt 1 -or $Day -gt 7) {
        return $false
    }
    if ($Week -gt [Globalization.ISOWeek]::GetWeeksInYear($Year)) { return $false }
    try {
        [void][Globalization.ISOWeek]::ToDateTime(
            $Year,
            $Week,
            [DayOfWeek]($Day % 7)
        )
        return $true
    } catch [ArgumentOutOfRangeException] {
        return $false
    }
}

function Test-PythonIsoDateTimeWithOffset([string]$Value) {
    if ([string]::IsNullOrEmpty($Value)) { return $false }
    $ClockPattern = '[0-9]{2}(?:(?::[0-9]{2}(?::[0-9]{2})?)|(?:[0-9]{2}(?:[0-9]{2})?))?(?:[\.,][0-9]+)?'
    $Pattern = '^(?<date>(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{8}|[0-9]{4}-W[0-9]{2}(?:-[0-9])?|[0-9]{4}W[0-9]{2}[0-9]?))' +
        '(?<separator>(?:[\uD800-\uDBFF][\uDC00-\uDFFF]|[\s\S]))' +
        '(?<time>' + $ClockPattern + ')' +
        '(?<offset>Z|[+-](?:' + $ClockPattern + '))\z'
    $Match = [Text.RegularExpressions.Regex]::Match(
        $Value,
        $Pattern,
        [Text.RegularExpressions.RegexOptions]::CultureInvariant,
        [TimeSpan]::FromSeconds(1)
    )
    if (-not $Match.Success -or
        -not (Test-PythonIsoDate $Match.Groups["date"].Value) -or
        -not (Test-PythonIsoClock $Match.Groups["time"].Value)) {
        return $false
    }
    $Offset = $Match.Groups["offset"].Value
    return $Offset -ceq "Z" -or (Test-PythonIsoOffset $Offset.Substring(1))
}
# END authenticated manifest JSON helpers
Assert-NoDuplicateJsonKeys $ManifestBytes "authenticated MANIFEST.json"
try {
    $Manifest = ConvertFrom-AuthenticatedManifestJson (
        [Text.UTF8Encoding]::new($false, $true).GetString($ManifestBytes)
    )
} catch {
    Fail "publisher authenticity FAILED: cannot parse authenticated MANIFEST.json"
}
$ExpectedAuthenticity = @{
    status = "SIGNED"; scheme = "openssh-sshsig"; publisher = "ruisheng-release"
    namespace = "ruisheng-candidate-v1"; key_type = "ssh-ed25519"
    key_fingerprint = $DerivedFingerprint; signed_object = "SHA256SUMS"
    signature_file = "SHA256SUMS.sig"
}
if ($Manifest.schema_version -is [bool] -or
    ($Manifest.schema_version -isnot [int] -and $Manifest.schema_version -isnot [long]) -or
    $Manifest.schema_version -ne $ExpectedSchemaVersion -or
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

function Test-ZeroUstarRange(
    [byte[]]$Bytes, [int]$Offset, [int]$Length
) {
    for ($Index = $Offset; $Index -lt $Offset + $Length; $Index++) {
        if ($Bytes[$Index] -ne 0) { return $false }
    }
    return $true
}

function Test-UstarAsciiField(
    [byte[]]$Header, [int]$Offset, [int]$Length, [string]$Expected
) {
    [byte[]]$ExpectedBytes = [Text.Encoding]::ASCII.GetBytes($Expected)
    if ($ExpectedBytes.Length -ne $Length) { return $false }
    for ($Index = 0; $Index -lt $Length; $Index++) {
        if ($Header[$Offset + $Index] -ne $ExpectedBytes[$Index]) { return $false }
    }
    return $true
}

function Read-UstarBlock(
    [IO.Stream]$Stream, [byte[]]$Buffer, [bool]$AllowEnd
) {
    [int]$Offset = 0
    while ($Offset -lt $Buffer.Length) {
        [int]$Read = $Stream.Read($Buffer, $Offset, $Buffer.Length - $Offset)
        if ($Read -eq 0) {
            if ($AllowEnd -and $Offset -eq 0) { return 0 }
            Fail "qualification toolchain archive is truncated"
        }
        $Offset += $Read
    }
    return $Offset
}

function Read-CanonicalUstarBytes(
    [IO.Stream]$Stream, [Int64]$Length, [bool]$RequireZero
) {
    [byte[]]$Buffer = [byte[]]::new(8192)
    [Int64]$Remaining = $Length
    while ($Remaining -gt 0) {
        [int]$ChunkLength = [int][Math]::Min([Int64]$Buffer.Length, $Remaining)
        [int]$Offset = 0
        while ($Offset -lt $ChunkLength) {
            [int]$Read = $Stream.Read($Buffer, $Offset, $ChunkLength - $Offset)
            if ($Read -eq 0) { Fail "qualification toolchain archive is truncated" }
            $Offset += $Read
        }
        if ($RequireZero -and -not (Test-ZeroUstarRange $Buffer 0 $ChunkLength)) {
            Fail "qualification toolchain archive contains non-zero member padding"
        }
        $Remaining -= $ChunkLength
    }
}

function Get-CanonicalUstarOctal(
    [byte[]]$Header,
    [int]$Offset,
    [int]$Digits,
    [Int64]$Maximum,
    [string]$Label
) {
    if ($Header[$Offset + $Digits] -ne 0) {
        Fail "qualification toolchain archive has a noncanonical $Label field"
    }
    [Int64]$Value = 0
    for ($Index = 0; $Index -lt $Digits; $Index++) {
        [int]$Digit = [int]$Header[$Offset + $Index] - 48
        if ($Digit -lt 0 -or $Digit -gt 7 -or
            $Value -gt [Math]::Floor(($Maximum - $Digit) / 8)) {
            Fail "qualification toolchain archive has an invalid $Label field"
        }
        $Value = ($Value * 8) + $Digit
    }
    return $Value
}

function Assert-SingleQualificationGzipMember(
    [string]$ArchivePath, [Int64]$MaximumExpandedBytes
) {
    if (-not ("Ruisheng.Qualification.SingleByteReadStream" -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.IO;

namespace Ruisheng.Qualification {
    public sealed class SingleByteReadStream : Stream {
        private readonly Stream inner;
        public SingleByteReadStream(Stream value) {
            if (value == null) { throw new ArgumentNullException("value"); }
            inner = value;
        }
        public override bool CanRead { get { return true; } }
        public override bool CanSeek { get { return false; } }
        public override bool CanWrite { get { return false; } }
        public override long Length { get { throw new NotSupportedException(); } }
        public override long Position {
            get { throw new NotSupportedException(); }
            set { throw new NotSupportedException(); }
        }
        public override void Flush() { }
        public override int Read(byte[] buffer, int offset, int count) {
            return inner.Read(buffer, offset, Math.Min(count, 1));
        }
        public override int ReadByte() { return inner.ReadByte(); }
        public override long Seek(long offset, SeekOrigin origin) {
            throw new NotSupportedException();
        }
        public override void SetLength(long value) { throw new NotSupportedException(); }
        public override void Write(byte[] buffer, int offset, int count) {
            throw new NotSupportedException();
        }
    }
}
'@
    }
    $Archive = $null
    $Throttle = $null
    $Deflate = $null
    try {
        $Archive = [IO.File]::OpenRead($ArchivePath)
        $Archive.Position = 10
        $Throttle = [Ruisheng.Qualification.SingleByteReadStream]::new($Archive)
        $Deflate = [IO.Compression.DeflateStream]::new(
            $Throttle, [IO.Compression.CompressionMode]::Decompress, $true
        )
        [byte[]]$Buffer = [byte[]]::new(65536)
        [Int64]$ExpandedBytes = 0
        while (($Read = $Deflate.Read($Buffer, 0, $Buffer.Length)) -ne 0) {
            if ($ExpandedBytes -gt $MaximumExpandedBytes - $Read) {
                Fail "qualification toolchain expanded archive exceeds its byte budget"
            }
            $ExpandedBytes += $Read
        }
        $Deflate.Dispose()
        $Deflate = $null
        [byte[]]$Footer = [byte[]]::new(8)
        [int]$FooterOffset = 0
        while ($FooterOffset -lt $Footer.Length) {
            [int]$Read = $Archive.Read($Footer, $FooterOffset, $Footer.Length - $FooterOffset)
            if ($Read -eq 0) { Fail "qualification toolchain gzip member is truncated" }
            $FooterOffset += $Read
        }
        if ($Archive.Position -ne $Archive.Length) {
            Fail "qualification toolchain archive must contain exactly one gzip member"
        }
    } finally {
        if ($null -ne $Deflate) { $Deflate.Dispose() }
        if ($null -ne $Throttle) { $Throttle.Dispose() }
        if ($null -ne $Archive) { $Archive.Dispose() }
    }
}

function Assert-CanonicalQualificationUstarArchive(
    [string]$ArchivePath,
    [string[]]$ExpectedMembers,
    [hashtable]$MaximumBytesByName
) {
    [byte[]]$Header = [byte[]]::new(512)
    [Int64]$MaximumTarBytes = 21 * 512
    foreach ($ExpectedMember in $ExpectedMembers) {
        if (-not $MaximumBytesByName.ContainsKey($ExpectedMember)) {
            Fail "qualification toolchain archive preflight contract is invalid"
        }
        [Int64]$MaximumMemberBytes = [Int64]$MaximumBytesByName[$ExpectedMember]
        $MaximumTarBytes += 512 + (
            [Int64][Math]::Floor(($MaximumMemberBytes + 511) / 512) * 512
        )
    }
    [Int64]$MaximumGzipBytes = $MaximumTarBytes + (
        [Int64][Math]::Floor($MaximumTarBytes / 100)
    ) + 64KB
    $ArchiveMetadata = Get-Item -LiteralPath $ArchivePath -Force -ErrorAction Stop
    if ([Int64]$ArchiveMetadata.Length -gt $MaximumGzipBytes) {
        Fail "qualification toolchain gzip archive exceeds its byte budget"
    }
    $ArchiveFile = $null
    $Gzip = $null
    try {
        $ArchiveFile = [IO.File]::OpenRead($ArchivePath)
        [byte[]]$GzipHeader = [byte[]]::new(10)
        [int]$GzipHeaderRead = $ArchiveFile.Read($GzipHeader, 0, $GzipHeader.Length)
        [byte[]]$ExpectedGzipHeader = @(
            0x1f, 0x8b, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02, 0xff
        )
        if ($GzipHeaderRead -ne $GzipHeader.Length -or
            -not [Linq.Enumerable]::SequenceEqual($GzipHeader, $ExpectedGzipHeader)) {
            Fail "qualification toolchain gzip header is not canonical"
        }
        $ArchiveFile.Dispose()
        $ArchiveFile = $null
        Assert-SingleQualificationGzipMember $ArchivePath $MaximumTarBytes
        $ArchiveFile = [IO.File]::OpenRead($ArchivePath)
        $Gzip = [IO.Compression.GZipStream]::new(
            $ArchiveFile, [IO.Compression.CompressionMode]::Decompress, $false
        )
        [Int64]$ConsumedBlocks = 0
        foreach ($ExpectedMember in $ExpectedMembers) {
            if (-not $MaximumBytesByName.ContainsKey($ExpectedMember) -or
                $ExpectedMember.Length -gt 100) {
                Fail "qualification toolchain archive preflight contract is invalid"
            }
            [void](Read-UstarBlock $Gzip $Header $false)
            [byte[]]$ExpectedName = [Text.Encoding]::ASCII.GetBytes($ExpectedMember)
            for ($Index = 0; $Index -lt $ExpectedName.Length; $Index++) {
                if ($Header[$Index] -ne $ExpectedName[$Index]) {
                    Fail "qualification toolchain archive member allowlist mismatch"
                }
            }
            if (-not (Test-ZeroUstarRange (
                    $Header
                ) $ExpectedName.Length (100 - $ExpectedName.Length)) -or
                -not (Test-UstarAsciiField $Header 100 8 "0000644`0") -or
                -not (Test-UstarAsciiField $Header 108 8 "0000000`0") -or
                -not (Test-UstarAsciiField $Header 116 8 "0000000`0") -or
                -not (Test-UstarAsciiField $Header 136 12 "00000000000`0") -or
                $Header[156] -ne 48 -or
                -not (Test-ZeroUstarRange $Header 157 100) -or
                -not (Test-UstarAsciiField $Header 257 6 "ustar`0") -or
                -not (Test-UstarAsciiField $Header 263 2 "00") -or
                -not (Test-ZeroUstarRange $Header 265 235) -or
                -not (Test-ZeroUstarRange $Header 500 12)) {
                Fail "qualification toolchain archive contains a noncanonical USTAR header"
            }

            [Int64]$MemberLimit = [Int64]$MaximumBytesByName[$ExpectedMember]
            [Int64]$MemberLength = Get-CanonicalUstarOctal (
                $Header
            ) 124 11 $MemberLimit "size"
            [Int64]$StoredChecksum = Get-CanonicalUstarOctal (
                $Header
            ) 148 6 262143 "checksum"
            if ($Header[155] -ne 32) {
                Fail "qualification toolchain archive has a noncanonical checksum field"
            }
            [Int64]$ComputedChecksum = 0
            for ($Index = 0; $Index -lt $Header.Length; $Index++) {
                $ComputedChecksum += if ($Index -ge 148 -and $Index -lt 156) {
                    32
                } else { $Header[$Index] }
            }
            if ($StoredChecksum -ne $ComputedChecksum) {
                Fail "qualification toolchain archive header checksum mismatch"
            }

            Read-CanonicalUstarBytes $Gzip $MemberLength $false
            [Int64]$PaddingLength = (512 - ($MemberLength % 512)) % 512
            Read-CanonicalUstarBytes $Gzip $PaddingLength $true
            $ConsumedBlocks += 1 + [Int64][Math]::Floor(($MemberLength + 511) / 512)
        }

        [int]$ExpectedTailZeroBlocks = 2 + (
            (20 - (($ConsumedBlocks + 2) % 20)) % 20
        )
        if ($ExpectedTailZeroBlocks -lt 2 -or $ExpectedTailZeroBlocks -gt 21) {
            Fail "qualification toolchain archive trailer budget is invalid"
        }
        for ($Index = 0; $Index -lt $ExpectedTailZeroBlocks; $Index++) {
            [void](Read-UstarBlock $Gzip $Header $false)
            if (-not (Test-ZeroUstarRange $Header 0 $Header.Length)) {
                Fail "qualification toolchain archive has invalid trailing blocks"
            }
        }
        if ((Read-UstarBlock $Gzip $Header $true) -ne 0) {
            Fail "qualification toolchain archive has excessive trailing blocks"
        }
    } catch {
        if ($_.Exception.Message -like "*qualification toolchain archive*") { throw }
        Fail "invalid qualification toolchain archive: $($_.Exception.Message)"
    } finally {
        if ($null -ne $Gzip) { $Gzip.Dispose() }
        if ($null -ne $ArchiveFile) { $ArchiveFile.Dispose() }
    }
}

function Read-TarEntries(
    [string]$ArchivePath,
    [string[]]$WantedNames,
    [hashtable]$MaximumBytesByName = @{},
    [switch]$CollectSha256Metadata
) {
    [Int64]$MaxJsonBytes = 4MB
    [int]$MaxDescriptorReferences = 32768
    [Int64]$MaxMetadataBytes = 64MB
    [int]$MaxDockerArchiveMembers = 32768
    [Int64]$MaxDockerArchiveMemberBytes = 8GB
    [Int64]$MaxDockerArchiveTotalBytes = 32GB
    if (-not ("System.Formats.Tar.TarReader" -as [type])) {
        Fail "PowerShell 7.3 or newer is required for pre-load archive validation"
    }
    $Wanted = [System.Collections.Generic.HashSet[string]]::new(
        $WantedNames, [System.StringComparer]::Ordinal
    )
    $Names = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
    $Values = @{}
    $MetadataBlobs = @{}
    $OversizedMetadata = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    [Int64]$MetadataBytes = 0
    [int]$MemberCount = 0
    [Int64]$ExpandedBytes = 0
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
            $MemberCount++
            if ($MemberCount -gt $MaxDockerArchiveMembers) { Fail "Archive has too many members: $ArchivePath" }
            if ($Entry.Length -lt 0 -or $Entry.Length -gt $MaxDockerArchiveMemberBytes) {
                Fail "Archive member exceeds the byte budget: $ArchivePath"
            }
            $ExpandedBytes += [Int64]$Entry.Length
            if ($ExpandedBytes -gt $MaxDockerArchiveTotalBytes) { Fail "Archive exceeds the total byte budget: $ArchivePath" }
            $Name = [string]$Entry.Name
            Test-SafeRelativePath $Name.TrimEnd("/")
            if (-not $Names.Add($Name)) {
                Fail "Archive contains duplicate member: $ArchivePath`:$Name"
            }
            if ($Entry.EntryType.ToString() -in @("SymbolicLink", "HardLink")) {
                Fail "Archive contains link member: $ArchivePath`:$Name"
            }
            $CollectMetadata = $CollectSha256Metadata -and
                $Name -cmatch '^blobs/sha256/[0-9a-f]{64}$'
            if ($CollectMetadata -and $Entry.Length -gt $MaxJsonBytes) {
                [void]$OversizedMetadata.Add($Name)
                $CollectMetadata = $false
            }
            if ($CollectMetadata) {
                $MetadataBytes += [Int64]$Entry.Length
                if ($MetadataBytes -gt $MaxMetadataBytes -or
                    $MetadataBlobs.Count -ge $MaxDescriptorReferences) {
                    Fail "Archive metadata exceeds the descriptor budget: $ArchivePath"
                }
            }
            if ($Wanted.Contains($Name) -or $CollectMetadata) {
                [Int64]$WantedLimit = 64MB
                if ($MaximumBytesByName.ContainsKey($Name)) {
                    $WantedLimit = [Int64]$MaximumBytesByName[$Name]
                }
                if ($Entry.Length -gt $WantedLimit) {
                    Fail "Archive metadata member is too large: $ArchivePath`:$Name"
                }
                if ($null -eq $Entry.DataStream) {
                    Fail "Archive member is not a regular file: $ArchivePath`:$Name"
                }
                $Buffer = [IO.MemoryStream]::new()
                try {
                    try { $Entry.DataStream.CopyTo($Buffer) }
                    catch [OutOfMemoryException] { Fail "Archive metadata exceeds available memory" }
                    [byte[]]$Captured = $Buffer.ToArray()
                    if ($Wanted.Contains($Name)) { $Values[$Name] = $Captured }
                    if ($CollectMetadata) { $MetadataBlobs[$Name] = $Captured }
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
    return [PSCustomObject]@{
        Names = $Names
        Values = $Values
        MetadataBlobs = $MetadataBlobs
        OversizedMetadata = $OversizedMetadata
    }
}

function Read-ArchiveSha256Blob(
    [object]$ArchiveRecord,
    [hashtable]$ReferenceBudget,
    [string]$ArchivePath,
    [object]$DigestValue,
    [string]$Label,
    [bool]$AllowMissing = $false
) {
    $ReferenceBudget.Count = [int]$ReferenceBudget.Count + 1
    if ($ReferenceBudget.Count -gt 32768) {
        Fail "Archive descriptor reference budget exceeded: $ArchivePath"
    }
    $Digest = [string]$DigestValue
    if ($Digest -notmatch '^sha256:[0-9a-f]{64}$') {
        Fail "Archive $Label digest is invalid: $ArchivePath"
    }
    $BlobName = "blobs/sha256/$($Digest.Substring(7))"
    if ($ArchiveRecord.OversizedMetadata.Contains($BlobName)) {
        Fail "Archive $Label exceeds the JSON byte limit: $ArchivePath"
    }
    if (-not $ArchiveRecord.MetadataBlobs.ContainsKey($BlobName)) {
        if ($AllowMissing) { return $null }
        Fail "Archive $Label blob is missing: $ArchivePath`:$BlobName"
    }
    [byte[]]$Bytes = $ArchiveRecord.MetadataBlobs[$BlobName]
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
    [object]$ArchiveRecord,
    [hashtable]$ReferenceBudget,
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
            $ArchiveRecord $ReferenceBudget $ArchivePath `
            $NestedDigest "nested descriptor" $true
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
                $ArchiveRecord $ReferenceBudget $ArchivePath `
                $NestedConfig.digest "nested config"
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
    [object]$ArchiveRecord,
    [hashtable]$ReferenceBudget,
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
        $ArchiveRecord $ReferenceBudget $ArchivePath `
        $ConfigDescriptor.digest "provenance config"
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
            $ArchiveRecord $ReferenceBudget $ArchivePath `
            $Layer.digest "provenance layer"
        $Statement = ConvertFrom-ArchiveJsonObject `
            $LayerBytes $ArchivePath "provenance layer"
        Test-SlsaProvenanceStatement $Statement $ArchivePath $MainManifestDigest
    }
}

function Get-DockerArchiveIdentity([string]$ArchivePath, [string]$ExpectedReference) {
    $Headers = Read-TarEntries $ArchivePath @("manifest.json", "index.json") @{
        "manifest.json" = 4MB
        "index.json" = 4MB
    } -CollectSha256Metadata
    $ReferenceBudget = @{ Count = 0 }
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
    $ConfigRecord = Read-TarEntries $ArchivePath @($ConfigName) @{
        $ConfigName = 4MB
    }
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
        if ($Descriptors.Count -gt 32768) {
            Fail "Archive descriptor reference budget exceeded: $ArchivePath"
        }
        $LoadedDescriptors = @()
        foreach ($Descriptor in $Descriptors) {
            if ($Descriptor -isnot [System.Management.Automation.PSCustomObject]) {
                Fail "Archive descriptor is invalid: $ArchivePath"
            }
            $DescriptorDigest = [string]$Descriptor.digest
            [byte[]]$DescriptorBytes = Read-ArchiveSha256Blob `
                $Headers $ReferenceBudget $ArchivePath $DescriptorDigest "descriptor"
            $DescriptorValue = ConvertFrom-ArchiveJsonObject `
                $DescriptorBytes $ArchivePath "descriptor"
            $Resolved = Resolve-MainManifestDigest `
                $Headers $ReferenceBudget $ArchivePath `
                $DescriptorDigest $DescriptorValue $ConfigDigest $Config
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
                    $Headers $ReferenceBudget $ArchivePath `
                    $Loaded.Descriptor $Loaded.Value $MainManifestDigest
            }
        }
    }
    return [PSCustomObject]@{
        ImageId = $ImageId
        Os = [string]$Config.os
        Architecture = [string]$Config.architecture
    }
}

function Assert-ExactProperties([object]$Value, [string[]]$ExpectedNames, [string]$Label) {
    if ($null -eq $Value) { Fail "$Label is missing" }
    $Names = @($Value.PSObject.Properties.Name)
    if ($Names.Count -ne $ExpectedNames.Count -or
        @($ExpectedNames | Where-Object { $Names -cnotcontains $_ }).Count -ne 0) {
        Fail "$Label keys mismatch"
    }
}

function ConvertTo-PythonCanonicalJson([object]$Value) {
    if ($null -eq $Value) { return "null" }
    if ($Value -is [bool]) { return $(if ($Value) { "true" } else { "false" }) }
    if ($Value -is [string]) {
        $Builder = [Text.StringBuilder]::new()
        [void]$Builder.Append('"')
        foreach ($Character in $Value.ToCharArray()) {
            $Code = [int][char]$Character
            switch ($Code) {
                8 { [void]$Builder.Append('\b'); continue }
                9 { [void]$Builder.Append('\t'); continue }
                10 { [void]$Builder.Append('\n'); continue }
                12 { [void]$Builder.Append('\f'); continue }
                13 { [void]$Builder.Append('\r'); continue }
                34 { [void]$Builder.Append('\"'); continue }
                92 { [void]$Builder.Append('\\'); continue }
            }
            if ($Code -lt 32 -or $Code -gt 126) {
                [void]$Builder.Append(('\u{0:x4}' -f $Code))
            } else { [void]$Builder.Append($Character) }
        }
        [void]$Builder.Append('"')
        return $Builder.ToString()
    }
    if ($Value -is [Collections.IDictionary]) {
        $Parts = foreach ($Key in $Value.Keys) {
            (ConvertTo-PythonCanonicalJson ([string]$Key)) + ":" +
                (ConvertTo-PythonCanonicalJson $Value[$Key])
        }
        return "{" + ($Parts -join ",") + "}"
    }
    if ($Value -is [Collections.IEnumerable]) {
        $Parts = foreach ($Item in $Value) { ConvertTo-PythonCanonicalJson $Item }
        return "[" + ($Parts -join ",") + "]"
    }
    Fail "Manifest logical identity contains an unsupported value type"
}

function Get-ManifestLogicalIdentity([object]$Value, [int]$SchemaVersion) {
    $Images = @($Value.images | ForEach-Object {
        [ordered]@{
            candidate_reference = [string]$_.candidate_reference
            component = [string]$_.component
            image_id = [string]$_.image_id
            repo_digest = if ($null -eq $_.repo_digest) { $null } else { [string]$_.repo_digest }
            source_reference = [string]$_.source_reference
        }
    })
    if ($SchemaVersion -eq 3) {
        $Toolchain = $Value.qualification_toolchain
        $Identity = { param($InputValue) [ordered]@{
            path = [string]$InputValue.path
            sha256 = [string]$InputValue.sha256
        } }
        $Descriptor = [ordered]@{
            format = [string]$Toolchain.format
            path = [string]$Toolchain.path
            producer = & $Identity $Toolchain.producer
            receipt_producer = & $Identity $Toolchain.receipt_producer
            schema = & $Identity $Toolchain.schema
            semantic_validator = [string]$Toolchain.semantic_validator
            sha256 = [string]$Toolchain.sha256
            toolchain_manifest = & $Identity $Toolchain.toolchain_manifest
            validator = & $Identity $Toolchain.validator
        }
        $LogicalValue = [ordered]@{
            alembic_head = [string]$Value.alembic_head
            candidate_id = [string]$Value.candidate_id
            images = $Images
            qualification_toolchain = $Descriptor
            source_commit = [string]$Value.source_commit
            target_architecture = [string]$Value.target_architecture
            target_os = [string]$Value.target_os
        }
    } else {
        $LogicalValue = [ordered]@{
            alembic_head = [string]$Value.alembic_head
            candidate_id = [string]$Value.candidate_id
            images = $Images
            source_commit = [string]$Value.source_commit
            target_architecture = [string]$Value.target_architecture
            target_os = [string]$Value.target_os
        }
    }
    $Json = ConvertTo-PythonCanonicalJson $LogicalValue
    return "sha256:$(Get-Sha256Bytes ([Text.Encoding]::UTF8.GetBytes($Json)))"
}

function Test-QualificationToolchain(
    [object]$AuthenticatedManifest,
    [hashtable]$AuthenticatedSums,
    [string]$AuthenticatedRoot,
    [int]$SchemaVersion
) {
    $BaseKeys = @(
        "schema_version", "candidate_id", "source_commit", "generated_at", "target_os",
        "target_architecture", "alembic_head", "logical_identity", "tools",
        "authenticity", "images"
    )
    $ExpectedKeys = if ($SchemaVersion -eq 3) {
        @($BaseKeys) + "qualification_toolchain"
    } else { $BaseKeys }
    Assert-ExactProperties $AuthenticatedManifest $ExpectedKeys "MANIFEST.json"
    if ($SchemaVersion -eq 2) { return }

    $Toolchain = $AuthenticatedManifest.qualification_toolchain
    Assert-ExactProperties $Toolchain @(
        "path", "sha256", "format", "semantic_validator", "schema", "validator",
        "producer", "receipt_producer", "toolchain_manifest"
    ) "qualification toolchain descriptor"
    $ArchiveName = "qualification-toolchain.tar.gz"
    $SemanticValidator = "ruisheng.device-point-profile-validator/v5"
    if ($Toolchain.path -cne $ArchiveName -or $Toolchain.format -cne "tar+gzip" -or
        $Toolchain.semantic_validator -cne $SemanticValidator -or
        [string]$Toolchain.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        $AuthenticatedSums[$ArchiveName] -cne $Toolchain.sha256) {
        Fail "Qualification toolchain descriptor contract is invalid"
    }
    $InternalName = "qualification-toolchain-manifest.json"
    $MemberNames = @(
        "tools/validate_device_point_profile.py",
        "tools/trust_root_freshness.py",
        "schemas/point-profile/point-profile-v1.schema.json",
        "tools/release_artifacts.py",
        "tools/release_verification_receipt.py",
        "pyproject.toml",
        "uv.lock"
    )
    $IdentityPaths = [ordered]@{
        schema = $MemberNames[2]
        validator = $MemberNames[0]
        producer = $MemberNames[3]
        receipt_producer = $MemberNames[4]
        toolchain_manifest = $InternalName
    }
    foreach ($Name in $IdentityPaths.Keys) {
        $Identity = $Toolchain.$Name
        Assert-ExactProperties $Identity @("path", "sha256") "qualification toolchain identity"
        if ($Identity.path -cne $IdentityPaths[$Name] -or
            [string]$Identity.sha256 -cnotmatch '^[0-9a-f]{64}$') {
            Fail "Qualification toolchain identity is invalid for $($IdentityPaths[$Name])"
        }
    }

    $ExpectedMembers = @($MemberNames) + $InternalName
    $ArchivePath = Join-Path $AuthenticatedRoot $ArchiveName
    $QualificationMemberLimits = @{}
    foreach ($Name in $ExpectedMembers) { $QualificationMemberLimits[$Name] = 64MB }
    $QualificationMemberLimits[$InternalName] = $MaxReleaseJsonBytes
    Assert-CanonicalQualificationUstarArchive `
        $ArchivePath $ExpectedMembers $QualificationMemberLimits
    $Record = Read-TarEntries $ArchivePath $ExpectedMembers @{
        $InternalName = $MaxReleaseJsonBytes
    }
    if ($Record.Names.Count -ne $ExpectedMembers.Count -or
        @($ExpectedMembers | Where-Object { -not $Record.Names.Contains($_) }).Count -ne 0 -or
        $Record.Values.Count -ne $ExpectedMembers.Count) {
        Fail "Qualification toolchain archive member allowlist mismatch"
    }
    [byte[]]$InternalBytes = $Record.Values[$InternalName]
    if ((Get-Sha256Bytes $InternalBytes) -cne $Toolchain.toolchain_manifest.sha256) {
        Fail "Qualification toolchain manifest SHA-256 mismatch"
    }
    Assert-NoDuplicateJsonKeys $InternalBytes "qualification toolchain manifest"
    try {
        $Internal = [Text.UTF8Encoding]::new($false, $true).GetString($InternalBytes) |
            ConvertFrom-Json
    } catch { Fail "Qualification toolchain manifest is invalid JSON" }
    Assert-ExactProperties $Internal @(
        "artifact_type", "members", "schema_version", "semantic_validator"
    ) "qualification toolchain manifest"
    if ($Internal.artifact_type -cne "ruisheng.qualification-toolchain" -or
        $Internal.schema_version -is [bool] -or
        ($Internal.schema_version -isnot [int] -and
            $Internal.schema_version -isnot [long]) -or
        $Internal.schema_version -ne 1 -or
        $Internal.semantic_validator -cne $SemanticValidator -or
        @($Internal.members).Count -ne $MemberNames.Count) {
        Fail "Qualification toolchain manifest contract is invalid"
    }
    $Resolved = @{}
    for ($Index = 0; $Index -lt $MemberNames.Count; $Index++) {
        $Identity = $Internal.members[$Index]
        Assert-ExactProperties $Identity @("path", "sha256") "qualification member identity"
        $ExpectedPath = $MemberNames[$Index]
        $Digest = Get-Sha256Bytes ([byte[]]$Record.Values[$ExpectedPath])
        if ($Identity.path -cne $ExpectedPath -or $Identity.sha256 -cne $Digest) {
            Fail "Qualification toolchain member SHA-256 mismatch: $ExpectedPath"
        }
        $Resolved[$ExpectedPath] = $Digest
    }
    foreach ($Name in @("schema", "validator", "producer", "receipt_producer")) {
        if ($Toolchain.$Name.sha256 -cne $Resolved[$IdentityPaths[$Name]]) {
            Fail "Qualification toolchain descriptor identity mismatch: $($IdentityPaths[$Name])"
        }
    }
}

function Assert-ManifestValueTypes([object]$Value) {
    foreach ($Name in @(
        "candidate_id", "source_commit", "generated_at", "target_os",
        "target_architecture", "alembic_head", "logical_identity"
    )) {
        if ($Value.$Name -isnot [string]) {
            Fail "MANIFEST.json scalar field has an invalid type: $Name"
        }
    }
    if (-not (Test-PythonIsoDateTimeWithOffset $Value.generated_at)) {
        Fail "MANIFEST.json generated_at must be an ISO-8601 timestamp with a timezone"
    }
    if ($Value.candidate_id -cnotmatch '^[a-z0-9][a-z0-9._-]{0,62}$' -or
        $Value.source_commit -cnotmatch '^[0-9a-f]{40}$' -or
        $Value.target_os -cnotmatch '^[a-z0-9][a-z0-9._-]*$' -or
        $Value.target_architecture -cnotmatch '^[a-z0-9][a-z0-9._-]*$' -or
        [string]::IsNullOrEmpty($Value.alembic_head) -or
        $Value.logical_identity -cnotmatch '^sha256:[0-9a-f]{64}$') {
        Fail "MANIFEST.json scalar field contract is invalid"
    }
    if ($Value.tools -isnot [PSCustomObject] -or
        @($Value.tools.PSObject.Properties).Count -eq 0 -or
        @($Value.tools.PSObject.Properties | Where-Object {
            $_.Value -isnot [string] -or [string]::IsNullOrEmpty([string]$_.Value)
        }).Count -ne 0) {
        Fail "MANIFEST.json tools contract is invalid"
    }
    if ($Value.images -isnot [Array]) {
        Fail "MANIFEST.json images must be an array"
    }
    $ImageKeys = @(
        "component", "source_reference", "repo_digest", "candidate_reference", "image_id",
        "os", "architecture", "archive", "sha256"
    )
    foreach ($Image in @($Value.images)) {
        Assert-ExactProperties $Image $ImageKeys "manifest image"
        foreach ($Name in @(
            "component", "source_reference", "candidate_reference", "image_id", "os",
            "architecture", "archive", "sha256"
        )) {
            if ($Image.$Name -isnot [string]) {
                Fail "MANIFEST.json image field has an invalid type: $Name"
            }
        }
        if ($null -ne $Image.repo_digest -and $Image.repo_digest -isnot [string]) {
            Fail "MANIFEST.json repo_digest has an invalid type"
        }
        if (($null -ne $Image.repo_digest -and
                $Image.repo_digest -cnotmatch '^[^\s@]+@sha256:[0-9a-f]{64}$') -or
            $Image.image_id -cnotmatch '^sha256:[0-9a-f]{64}$' -or
            $Image.sha256 -cnotmatch '^[0-9a-f]{64}$') {
            Fail "MANIFEST.json image identity contract is invalid"
        }
    }
}

Assert-ManifestValueTypes $Manifest
if ($Manifest.candidate_id -cnotmatch '^[a-z0-9][a-z0-9._-]{0,62}$') {
    Fail "Invalid candidate ID"
}
if ($Manifest.schema_version -cne $ExpectedSchemaVersion -or
    $Manifest.authenticity.status -cne "SIGNED") {
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
    if ($Image.component -cne $Component -or $Image.candidate_reference -cne $ExpectedReference) {
        Fail "Candidate reference mismatch for $Component"
    }
    if ($Image.archive -cne $ExpectedArchive) {
        Fail "Archive path mismatch for $Component"
    }
    if ($Image.os -cne $Manifest.target_os -or
        $Image.architecture -cne $Manifest.target_architecture) {
        Fail "Platform mismatch for $Component"
    }
    if ($Image.image_id -cnotmatch '^sha256:[0-9a-f]{64}$') {
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
Test-QualificationToolchain $Manifest $Sums $PackageRoot $ExpectedSchemaVersion
if ($Manifest.logical_identity -cne (Get-ManifestLogicalIdentity $Manifest $ExpectedSchemaVersion)) {
    Fail "Manifest logical_identity does not match its immutable inputs"
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
        & $Docker --host npipe:////./pipe/docker_engine --config $DockerConfig `
            image load --input (Join-Path $PackageRoot $Image.archive) | Out-Null
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
    $Raw = & $Docker --host npipe:////./pipe/docker_engine --config $DockerConfig `
        image inspect $Image.image_id --format '{{json .}}'
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker inspect failed for $($Image.component)"
    }
    $Inspected = $Raw | ConvertFrom-Json
    if ($Inspected.Id -ne $Image.image_id -or $Inspected.Os -ne $Image.os -or
        $Inspected.Architecture -ne $Image.architecture) {
        Fail "Loaded image identity mismatch for $($Image.component)"
    }
    $ReferenceRaw = & $Docker --host npipe:////./pipe/docker_engine --config $DockerConfig `
        image inspect $Image.candidate_reference --format '{{json .}}'
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker candidate reference inspect failed for $($Image.component)"
    }
    $ReferenceInspected = $ReferenceRaw | ConvertFrom-Json
    if ($ReferenceInspected.Id -ne $Image.image_id -or
        $ReferenceInspected.Os -ne $Image.os -or
        $ReferenceInspected.Architecture -ne $Image.architecture -or
        $Image.candidate_reference -cnotin @($ReferenceInspected.RepoTags)) {
        Fail "Loaded candidate reference mismatch for $($Image.component)"
    }
}

$ComposeArgs = @(
    "compose", "--env-file", $ComposeEnvPath,
    "-f", (Join-Path $PackageRoot "docker-compose.prod.yml")
)
$ResolvedImages = @(& $Docker --host npipe:////./pipe/docker_engine --config $DockerConfig `
    @ComposeArgs config --images | Where-Object { $_ })
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
$ComposeConfig = (& $Docker --host npipe:////./pipe/docker_engine --config $DockerConfig `
    @ComposeArgs config --format json) | ConvertFrom-Json
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
