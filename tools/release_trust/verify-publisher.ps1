[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$PackagePath,
    [Parameter(Position = 1)][string]$SiteEnvPath = "",
    [switch]$InstallSerialTools,
    [ValidateSet("None", "ValidatorSchema", "ValidatorProfile", "ValidatorLegacy", "Receipt")]
    [string]$QualificationMode = "None",
    [string]$QualificationProfilePath = "",
    [string]$QualificationEvidencePath = "",
    [string]$QualificationRootPath = "",
    [string]$QualificationTrustPolicyPath = "",
    [string]$QualificationOutputDirectory = "",
    [string]$QualificationSigningIdentity = "",
    [ValidatePattern('^$|^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')]
    [string]$QualificationVerifierId = "",
    [ValidatePattern('^$|^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')]
    [string]$QualificationVerifierKeyId = ""
)

$ErrorActionPreference = "Stop"
$MaxReleaseJsonBytes = 4MB
$MaxFreshnessProviderBytes = 512MB
$FreshnessProviderTimeoutMilliseconds = 30000
$FreshnessProviderPath = "C:\ProgramData\Ruisheng\bin\trust-root-freshness-provider.exe"
$FreshnessProviderConfigPath = `
    "C:\ProgramData\Ruisheng\trust\point-profile-freshness-provider.json"
$FreshnessTrustRootPath = `
    "C:\ProgramData\Ruisheng\trust\point-profile-policy-root.json"
$FreshnessVerifierId = "ruisheng.protected-release-publisher.windows.v1"
# The candidate verifier must only reach the local Docker daemon, never a caller-selected endpoint.
Remove-Item Env:DOCKER_HOST -ErrorAction SilentlyContinue
Remove-Item Env:DOCKER_CONTEXT -ErrorAction SilentlyContinue
Remove-Item Env:DOCKER_CONFIG -ErrorAction SilentlyContinue
Remove-Item Env:DOCKER_CLI_PLUGIN_EXTRA_DIRS -ErrorAction SilentlyContinue
Remove-Item Env:XDG_CONFIG_HOME -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONSTARTUP -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONINSPECT -ErrorAction SilentlyContinue
function Fail([string]$Message) { throw "[publisher] authenticity FAILED: $Message" }
if ($PSVersionTable.PSVersion -lt [version]"7.3") {
    Fail "PowerShell 7.3 or newer is required"
}

function Test-HasText([string]$Value) {
    return -not [string]::IsNullOrWhiteSpace($Value)
}

$QualificationValues = @(
    $QualificationProfilePath, $QualificationEvidencePath, $QualificationRootPath,
    $QualificationTrustPolicyPath, $QualificationOutputDirectory,
    $QualificationSigningIdentity, $QualificationVerifierId,
    $QualificationVerifierKeyId
)
if ($QualificationMode -eq "None") {
    if (@($QualificationValues | Where-Object { Test-HasText $_ }).Count -ne 0) {
        Fail "qualification-only parameters require an explicit qualification mode"
    }
} else {
    if ($InstallSerialTools -or (Test-HasText $SiteEnvPath)) {
        Fail "qualification mode cannot install serial tools or accept a site environment"
    }
    switch ($QualificationMode) {
        "ValidatorSchema" {
            if (@($QualificationValues | Where-Object { Test-HasText $_ }).Count -ne 0) {
                Fail "ValidatorSchema does not accept additional qualification parameters"
            }
        }
        "ValidatorProfile" {
            if (-not (Test-HasText $QualificationProfilePath) -or
                -not (Test-HasText $QualificationRootPath) -or
                -not (Test-HasText $QualificationTrustPolicyPath) -or
                @(@(
                    $QualificationEvidencePath, $QualificationOutputDirectory,
                    $QualificationSigningIdentity, $QualificationVerifierId,
                    $QualificationVerifierKeyId
                ) | Where-Object { Test-HasText $_ }).Count -ne 0) {
                Fail "ValidatorProfile requires only profile, root, and trust-policy paths"
            }
        }
        "ValidatorLegacy" {
            if (-not (Test-HasText $QualificationEvidencePath) -or
                -not (Test-HasText $QualificationRootPath) -or
                @(@(
                    $QualificationProfilePath, $QualificationTrustPolicyPath,
                    $QualificationOutputDirectory, $QualificationSigningIdentity,
                    $QualificationVerifierId, $QualificationVerifierKeyId
                ) | Where-Object { Test-HasText $_ }).Count -ne 0) {
                Fail "ValidatorLegacy requires only evidence and root paths"
            }
        }
        "Receipt" {
            if (-not (Test-HasText $QualificationOutputDirectory) -or
                -not (Test-HasText $QualificationSigningIdentity) -or
                -not (Test-HasText $QualificationVerifierId) -or
                -not (Test-HasText $QualificationVerifierKeyId) -or
                @(@(
                    $QualificationProfilePath, $QualificationEvidencePath,
                    $QualificationRootPath, $QualificationTrustPolicyPath
                ) | Where-Object { Test-HasText $_ }).Count -ne 0) {
                Fail "Receipt requires only output, signing identity, verifier ID, and verifier key ID"
            }
        }
        default { Fail "unsupported qualification mode" }
    }
}

function ConvertTo-CmdSafePath([string]$Path, [string]$Label) {
    $UnsafeCharacters = [char[]]@(
        '"', ' ', "`t", '%', '!', '&', '|', '<', '>', '^', '(', ')', "`r", "`n"
    )
    if ($Path -cnotmatch '^[A-Za-z]:\\' -or $Path.IndexOfAny($UnsafeCharacters) -ge 0) {
        Fail "$Label cannot be safely passed to the system command processor"
    }
    return $Path
}

function Get-ApprovedSids([switch]$AllowTrustedInstaller) {
    $AllowedSids = @("S-1-5-18", "S-1-5-32-544")
    if ($AllowTrustedInstaller) {
        $AllowedSids += "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
    }
    return $AllowedSids
}

function Assert-ProtectedAcl(
    [string]$Path, [string]$Label, [switch]$AllowTrustedInstaller
) {
    $Item = Get-Item -Force -LiteralPath $Path -ErrorAction Stop
    if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail "$Label is linked"
    }
    $AllowedSids = Get-ApprovedSids -AllowTrustedInstaller:$AllowTrustedInstaller
    $Acl = Get-Acl -LiteralPath $Path
    $OwnerSid = $Acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($OwnerSid -notin $AllowedSids) { Fail "$Label has an unapproved owner: $OwnerSid" }
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
            Fail "$Label has an unresolvable writable identity"
        }
        if ($Sid -notin $AllowedSids) {
            Fail "$Label is writable by an unapproved identity: $Sid"
        }
    }
}

function Assert-ProtectedAncestors(
    [string]$Path, [string]$Label, [switch]$AllowTrustedInstaller
) {
    $AllowedSids = Get-ApprovedSids -AllowTrustedInstaller:$AllowTrustedInstaller
    $Current = (Get-Item -Force -LiteralPath $Path -ErrorAction Stop).Parent
    $UnsafeParentRights = [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
        [Security.AccessControl.FileSystemRights]::Delete -bor
        [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
        [Security.AccessControl.FileSystemRights]::TakeOwnership
    while ($null -ne $Current) {
        if (($Current.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Fail "$Label ancestor is linked: $($Current.FullName)"
        }
        $Acl = Get-Acl -LiteralPath $Current.FullName
        $OwnerSid = $Acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
        if ($OwnerSid -notin $AllowedSids) {
            Fail "$Label ancestor has an unapproved owner: $OwnerSid"
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
                Fail "$Label ancestor has an unresolvable replacement identity"
            }
            if ($Sid -notin $AllowedSids) {
                Fail "$Label ancestor permits replacement by: $Sid"
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
    foreach ($SidValue in @("S-1-5-18", "S-1-5-32-544")) {
        $Rule = [Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($SidValue),
            [Security.AccessControl.FileSystemRights]::FullControl,
            $Inheritance,
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow
        )
        [void]$Security.AddAccessRule($Rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $Security
}

function Install-AuthenticatedSerialTools(
    [string]$AuthenticatedRoot,
    [hashtable]$AuthenticatedSums,
    [object]$AuthenticatedManifest
) {
    $InstallMutex = [Threading.Mutex]::new(
        $false, "Global\RuishengAuthenticatedSerialToolInstall"
    )
    $LockAcquired = $false
    $TransactionRoot = $null
    $PreserveTransaction = $false
    try {
        try { $LockAcquired = $InstallMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] {
            $LockAcquired = $true
        }
        if (-not $LockAcquired) { Fail "another authenticated tool installation is active" }

    $DestinationRoot = "C:\Ruisheng"
    foreach ($Directory in @(
        $DestinationRoot,
        (Join-Path $DestinationRoot "tools"),
        (Join-Path $DestinationRoot "site"),
        (Join-Path $DestinationRoot "audit")
    )) {
        if (-not (Test-Path -LiteralPath $Directory)) {
            [void](New-Item -ItemType Directory -Path $Directory)
        }
        $Item = Get-Item -Force -LiteralPath $Directory -ErrorAction Stop
        if (-not $Item.PSIsContainer -or
            ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Fail "serial tool destination is missing or linked: $Directory"
        }
        Set-ProtectedSnapshotAcl $Directory
        Assert-ProtectedAcl $Directory "serial tool destination"
        Assert-ProtectedAncestors $Directory "serial tool destination" -AllowTrustedInstaller
    }
    $ToolRoot = Join-Path $DestinationRoot "tools"
    $ProgramDataRoot = "C:\ProgramData\Ruisheng"
    foreach ($Directory in @(
        (Join-Path $ProgramDataRoot "receipts"),
        (Join-Path $ProgramDataRoot "probe-runs")
    )) {
        if (-not (Test-Path -LiteralPath $Directory)) {
            [void](New-Item -ItemType Directory -Path $Directory)
        }
        Set-ProtectedSnapshotAcl $Directory
        Assert-ProtectedAcl $Directory "Modbus probe protected directory"
        Assert-ProtectedAncestors $Directory `
            "Modbus probe protected directory" -AllowTrustedInstaller
    }

    $GwImages = @($AuthenticatedManifest.images | Where-Object { $_.component -ceq "gw" })
    if ($GwImages.Count -ne 1 -or $GwImages[0].image_id -cnotmatch '^sha256:[0-9a-f]{64}$') {
        Fail "authenticated manifest does not contain one immutable GW image"
    }

    $TransactionRoot = New-ProtectedSnapshotRoot "serial-install-"
    $StageRoot = Join-Path $TransactionRoot "stage"
    $BackupRoot = Join-Path $TransactionRoot "backup"
    $DockerConfigRoot = Join-Path $TransactionRoot "docker-config"
    foreach ($Directory in @($StageRoot, $BackupRoot, $DockerConfigRoot)) {
        [void](New-Item -ItemType Directory -Path $Directory)
        Set-ProtectedSnapshotAcl $Directory
    }
    $DockerConfigPath = Join-Path $DockerConfigRoot "config.json"
    [IO.File]::WriteAllText(
        $DockerConfigPath, "{}`n", [Text.UTF8Encoding]::new($false)
    )
    Set-ProtectedSnapshotAcl $DockerConfigPath
    $DockerPath = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (-not (Test-Path -LiteralPath $DockerPath -PathType Leaf)) {
        Fail "fixed Docker CLI is required to bind the running GW image"
    }
    Assert-ProtectedAcl $DockerPath "fixed Docker CLI" -AllowTrustedInstaller
    Assert-ProtectedAncestors $DockerPath "fixed Docker CLI" -AllowTrustedInstaller
    $GwInspectArguments = @(
        "--host", "npipe:////./pipe/docker_engine", "--config", $DockerConfigRoot,
        "inspect", "ruisheng-gw", "--format", "{{json .}}"
    )
    $GwInspectRaw = @(& $DockerPath @GwInspectArguments)
    if ($LASTEXITCODE -ne 0 -or $GwInspectRaw.Count -ne 1) {
        Fail "cannot inspect the running GW container through the fixed Docker endpoint"
    }
    try { $GwContainer = $GwInspectRaw[0] | ConvertFrom-Json } catch {
        Fail "running GW inspection returned invalid JSON"
    }
    $RunningGwImageId = [string]$GwContainer.Image
    if ($GwContainer.Name -cne "/ruisheng-gw" -or -not $GwContainer.State.Running -or
        $RunningGwImageId -cnotmatch '^sha256:[0-9a-f]{64}$') {
        Fail "running GW container identity is invalid"
    }
    $TemplateRelative = "site-modbus-probe.json.example"
    $Receipt = [ordered]@{
        schema_version = 1
        candidate_id = [string]$AuthenticatedManifest.candidate_id
        source_commit = [string]$AuthenticatedManifest.source_commit
        probe_sha256 = [string]$AuthenticatedSums["probe_modbus_rtu.py"]
        runner_sha256 = [string]$AuthenticatedSums["run_modbus_probe.ps1"]
        template_sha256 = [string]$AuthenticatedSums[$TemplateRelative]
        gw_image_id = $RunningGwImageId
        installed_at = [DateTimeOffset]::UtcNow.ToString("o")
    }

    $Entries = @()
    $ToolRelatives = @(
        "serial_hardware_attach.ps1",
        "install_serial_hardware_task.ps1",
        "validate_serial_hardware.py",
        "probe_modbus_rtu.py",
        "run_modbus_probe.ps1"
    )
    foreach ($Relative in $ToolRelatives) {
        $Source = Join-Path $AuthenticatedRoot $Relative
        $Destination = Join-Path $ToolRoot $Relative
        $Staged = Join-Path $StageRoot $Relative
        Copy-Item -LiteralPath $Source -Destination $Staged
        $StagedHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Staged).Hash.ToLowerInvariant()
        if ($StagedHash -cne $AuthenticatedSums[$Relative]) {
            Fail "staged serial tool hash mismatch: $Relative"
        }
        $Entries += [ordered]@{
            relative = $Relative; staged = $Staged; destination = $Destination
            expected_hash = [string]$AuthenticatedSums[$Relative]; label = "installed serial tool"
        }
    }
    $TemplateDestination = Join-Path $DestinationRoot "site\$TemplateRelative"
    $TemplateStaged = Join-Path $StageRoot $TemplateRelative
    Copy-Item -LiteralPath (Join-Path $AuthenticatedRoot $TemplateRelative) `
        -Destination $TemplateStaged
    $TemplateHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $TemplateStaged).Hash.ToLowerInvariant()
    if ($TemplateHash -cne $AuthenticatedSums[$TemplateRelative]) {
        Fail "staged Modbus probe template hash mismatch"
    }
    $Entries += [ordered]@{
        relative = $TemplateRelative; staged = $TemplateStaged
        destination = $TemplateDestination; expected_hash = $TemplateHash
        label = "installed Modbus probe template"
    }

    $ReceiptPath = Join-Path $ProgramDataRoot "receipts\modbus-probe-release.json"
    $ReceiptStaged = Join-Path $StageRoot "modbus-probe-release.json"
    [IO.File]::WriteAllText(
        $ReceiptStaged,
        (($Receipt | ConvertTo-Json -Depth 10 -Compress) + "`n"),
        [Text.UTF8Encoding]::new($false)
    )
    $ReceiptHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ReceiptStaged).Hash.ToLowerInvariant()
    $Entries += [ordered]@{
        relative = "modbus-probe-release.json"; staged = $ReceiptStaged
        destination = $ReceiptPath; expected_hash = $ReceiptHash
        label = "Modbus probe release receipt"
    }

    $Committed = @()
    try {
        for ($Index = 0; $Index -lt $Entries.Count; $Index++) {
            $Entry = $Entries[$Index]
            $Existed = Test-Path -LiteralPath $Entry.destination -PathType Leaf
            $Backup = Join-Path $BackupRoot ("$Index.bak")
            if ($Existed) { Copy-Item -LiteralPath $Entry.destination -Destination $Backup }
            $Committed += [ordered]@{
                destination = $Entry.destination; existed = $Existed; backup = $Backup
            }
            Move-Item -LiteralPath $Entry.staged -Destination $Entry.destination -Force
            Assert-ProtectedAcl $Entry.destination $Entry.label
            Assert-ProtectedAncestors $Entry.destination $Entry.label -AllowTrustedInstaller
            $InstalledHash = (
                Get-FileHash -Algorithm SHA256 -LiteralPath $Entry.destination
            ).Hash.ToLowerInvariant()
            if ($InstalledHash -cne $Entry.expected_hash) {
                Fail "installed authenticated file hash mismatch: $($Entry.relative)"
            }
        }
    } catch {
        $InstallError = $_.Exception.Message
        $RollbackErrors = @()
        for ($Index = $Committed.Count - 1; $Index -ge 0; $Index--) {
            $Entry = $Committed[$Index]
            try {
                if ($Entry.existed) {
                    Copy-Item -LiteralPath $Entry.backup -Destination $Entry.destination -Force
                } elseif (Test-Path -LiteralPath $Entry.destination) {
                    Remove-Item -LiteralPath $Entry.destination -Force
                }
            } catch { $RollbackErrors += $_.Exception.Message }
        }
        if ($RollbackErrors.Count -ne 0) {
            $PreserveTransaction = $true
            Remove-Item -LiteralPath $ReceiptPath -Force -ErrorAction SilentlyContinue
            Fail "authenticated install failed and rollback was incomplete; recovery preserved at ${TransactionRoot}: $InstallError; $($RollbackErrors -join '; ')"
        }
        Fail "authenticated install failed and was rolled back: $InstallError"
    }
    Write-Host "[publisher] INSTALLED: authenticated serial tools in C:\Ruisheng\tools"
    } finally {
        if ($null -ne $TransactionRoot -and -not $PreserveTransaction -and
            (Test-Path -LiteralPath $TransactionRoot)) {
            Remove-Item -LiteralPath $TransactionRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
        if ($LockAcquired) { try { $InstallMutex.ReleaseMutex() } catch { } }
        $InstallMutex.Dispose()
    }
}

# BEGIN candidate snapshot identity helpers
if (-not ("Ruisheng.ReleaseTrust.Win32FileIdentity" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace Ruisheng.ReleaseTrust {
    public sealed class FileIdentitySnapshot {
        public string FinalPath { get; internal set; }
        public UInt32 VolumeSerialNumber { get; internal set; }
        public UInt64 FileIndex { get; internal set; }
        public Int64 Length { get; internal set; }
        public Int64 CreationTime { get; internal set; }
        public Int64 LastWriteTime { get; internal set; }
        public UInt32 NumberOfLinks { get; internal set; }
        public string Sha256 { get; set; }
    }

    public static class Win32FileIdentity {
        [StructLayout(LayoutKind.Sequential)]
        private struct NativeFileTime {
            public UInt32 Low;
            public UInt32 High;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct ByHandleFileInformation {
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
        private static extern bool GetFileInformationByHandle(
            SafeFileHandle handle,
            out ByHandleFileInformation information
        );

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern UInt32 GetFinalPathNameByHandleW(
            SafeFileHandle handle,
            StringBuilder path,
            UInt32 capacity,
            UInt32 flags
        );

        private static Int64 CombineTime(NativeFileTime value) {
            return ((Int64)value.High << 32) | value.Low;
        }

        private static string FinalPath(SafeFileHandle handle) {
            StringBuilder buffer = new StringBuilder(32768);
            UInt32 length = GetFinalPathNameByHandleW(
                handle, buffer, (UInt32)buffer.Capacity, 0
            );
            if (length == 0) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            if (length >= buffer.Capacity) {
                buffer = new StringBuilder(checked((int)length + 1));
                length = GetFinalPathNameByHandleW(
                    handle, buffer, (UInt32)buffer.Capacity, 0
                );
                if (length == 0 || length >= buffer.Capacity) {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
            }
            string value = buffer.ToString();
            if (value.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) {
                value = @"\\" + value.Substring(8);
            } else if (value.StartsWith(@"\\?\", StringComparison.OrdinalIgnoreCase)) {
                value = value.Substring(4);
            }
            return Path.GetFullPath(value);
        }

        public static FileIdentitySnapshot Capture(SafeFileHandle handle) {
            if (handle == null || handle.IsInvalid || handle.IsClosed) {
                throw new ArgumentException("file handle is not open", "handle");
            }
            ByHandleFileInformation information;
            if (!GetFileInformationByHandle(handle, out information)) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            return new FileIdentitySnapshot {
                FinalPath = FinalPath(handle),
                VolumeSerialNumber = information.VolumeSerialNumber,
                FileIndex = ((UInt64)information.FileIndexHigh << 32) |
                    information.FileIndexLow,
                Length = ((Int64)information.FileSizeHigh << 32) |
                    information.FileSizeLow,
                CreationTime = CombineTime(information.CreationTime),
                LastWriteTime = CombineTime(information.LastWriteTime),
                NumberOfLinks = information.NumberOfLinks
            };
        }
    }
}
'@
}

function Get-OpenFileIdentity(
    [IO.FileStream]$Stream, [string]$ExpectedPath, [string]$Label
) {
    try {
        $Identity = [Ruisheng.ReleaseTrust.Win32FileIdentity]::Capture(
            $Stream.SafeFileHandle
        )
    } catch {
        Fail "cannot read $Label handle identity: $($_.Exception.Message)"
    }
    $CanonicalExpected = [IO.Path]::GetFullPath($ExpectedPath)
    if (-not [string]::Equals(
        $Identity.FinalPath,
        $CanonicalExpected,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        Fail "$Label handle resolved to a different path"
    }
    if ($Identity.Length -ne $Stream.Length) {
        Fail "$Label handle length is inconsistent"
    }
    return $Identity
}

function Assert-SameFileIdentity(
    [object]$ExpectedIdentity, [object]$ActualIdentity, [string]$Label
) {
    if ($ExpectedIdentity.VolumeSerialNumber -ne $ActualIdentity.VolumeSerialNumber -or
        $ExpectedIdentity.FileIndex -ne $ActualIdentity.FileIndex -or
        $ExpectedIdentity.Length -ne $ActualIdentity.Length -or
        $ExpectedIdentity.CreationTime -ne $ActualIdentity.CreationTime -or
        $ExpectedIdentity.LastWriteTime -ne $ActualIdentity.LastWriteTime -or
        $ExpectedIdentity.NumberOfLinks -ne $ActualIdentity.NumberOfLinks -or
        -not [string]::Equals(
            $ExpectedIdentity.FinalPath,
            $ActualIdentity.FinalPath,
            [StringComparison]::OrdinalIgnoreCase
        )) {
        Fail "$Label identity or metadata changed"
    }
}

function Get-CandidateSourceIdentity([string]$SourcePath, [string]$Relative) {
    $Stream = $null
    $Hasher = $null
    try {
        $Stream = [IO.File]::Open(
            $SourcePath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
        )
        $Before = Get-OpenFileIdentity $Stream $SourcePath "candidate file ${Relative}"
        if ($Before.NumberOfLinks -ne 1) {
            Fail "candidate file has multiple hard links: $Relative"
        }
        $Hasher = [Security.Cryptography.SHA256]::Create()
        $Before.Sha256 = ([BitConverter]::ToString(
            $Hasher.ComputeHash($Stream)
        )).Replace("-", "").ToLowerInvariant()
        $After = Get-OpenFileIdentity $Stream $SourcePath "candidate file ${Relative}"
        Assert-SameFileIdentity $Before $After "candidate file ${Relative} during source hash"
        return $Before
    } finally {
        if ($null -ne $Hasher) { $Hasher.Dispose() }
        if ($null -ne $Stream) { $Stream.Dispose() }
    }
}

function Copy-CandidateFileToSnapshot(
    [string]$SourcePath,
    [string]$DestinationPath,
    [string]$Relative,
    [object]$ExpectedIdentity
) {
    $InputStream = $null
    $OutputStream = $null
    $ReboundStream = $null
    $CopyHasher = $null
    $SnapshotComplete = $false
    try {
        $InputStream = [IO.File]::Open(
            $SourcePath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
        )
        $Before = Get-OpenFileIdentity $InputStream $SourcePath "candidate file ${Relative}"
        Assert-SameFileIdentity $ExpectedIdentity $Before "candidate file ${Relative} before snapshot"
        if ($Before.NumberOfLinks -ne 1) {
            Fail "candidate file has multiple hard links: $Relative"
        }

        $OutputStream = [IO.File]::Open(
            $DestinationPath,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write,
            [IO.FileShare]::None
        )
        $CopyHasher = [Security.Cryptography.IncrementalHash]::CreateHash(
            [Security.Cryptography.HashAlgorithmName]::SHA256
        )
        $Buffer = [byte[]]::new(1MB)
        [Int64]$Copied = 0
        while ($Copied -lt $Before.Length) {
            $ReadLength = [int][Math]::Min($Buffer.Length, $Before.Length - $Copied)
            $Read = $InputStream.Read($Buffer, 0, $ReadLength)
            if ($Read -le 0) { break }
            $OutputStream.Write($Buffer, 0, $Read)
            $CopyHasher.AppendData($Buffer, 0, $Read)
            $Copied += $Read
        }
        if ($Copied -ne $Before.Length -or $InputStream.ReadByte() -ne -1) {
            Fail "candidate file size changed during snapshot: $Relative"
        }
        $OutputStream.Flush($true)
        $SnapshotDigest = ([BitConverter]::ToString(
            $CopyHasher.GetHashAndReset()
        )).Replace("-", "").ToLowerInvariant()
        if ($SnapshotDigest -cne $ExpectedIdentity.Sha256) {
            Fail "candidate file content changed during snapshot: $Relative"
        }

        $After = Get-OpenFileIdentity $InputStream $SourcePath "candidate file ${Relative}"
        Assert-SameFileIdentity $Before $After "candidate file ${Relative} during snapshot"

        # Reopen the original path while the first no-write/no-delete share is held. This
        # proves the path still names the exact handle that supplied the snapshot bytes.
        $ReboundStream = [IO.File]::Open(
            $SourcePath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
        )
        $Rebound = Get-OpenFileIdentity (
            $ReboundStream
        ) $SourcePath "candidate file ${Relative} rebound path"
        Assert-SameFileIdentity $After $Rebound "candidate file ${Relative} rebound path"
        $SnapshotComplete = $true
    } finally {
        if ($null -ne $CopyHasher) { $CopyHasher.Dispose() }
        if ($null -ne $ReboundStream) { $ReboundStream.Dispose() }
        if ($null -ne $OutputStream) { $OutputStream.Dispose() }
        if ($null -ne $InputStream) { $InputStream.Dispose() }
        if (-not $SnapshotComplete -and (Test-Path -LiteralPath $DestinationPath)) {
            Remove-Item -LiteralPath $DestinationPath -Force -ErrorAction SilentlyContinue
        }
    }
}
# END candidate snapshot identity helpers

function New-ProtectedSnapshotRoot([string]$Prefix) {
    $RuishengRoot = "C:\ProgramData\Ruisheng"
    Assert-ProtectedAcl $RuishengRoot "snapshot base"
    Assert-ProtectedAncestors $RuishengRoot "snapshot base" -AllowTrustedInstaller
    $WorkRoot = Join-Path $RuishengRoot "work"
    if (-not (Test-Path -LiteralPath $WorkRoot)) {
        [void](New-Item -ItemType Directory -Path $WorkRoot)
    }
    $WorkItem = Get-Item -Force -LiteralPath $WorkRoot -ErrorAction Stop
    if (-not $WorkItem.PSIsContainer -or
        ($WorkItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail "snapshot work directory is missing or linked"
    }
    Set-ProtectedSnapshotAcl $WorkRoot
    Assert-ProtectedAcl $WorkRoot "snapshot work directory"
    Assert-ProtectedAncestors $WorkRoot "snapshot work directory" -AllowTrustedInstaller
    $Snapshot = Join-Path $WorkRoot ($Prefix + [Guid]::NewGuid().ToString("N"))
    [void](New-Item -ItemType Directory -Path $Snapshot)
    Set-ProtectedSnapshotAcl $Snapshot
    return $Snapshot
}

$PackageItem = Get-Item -Force -LiteralPath $PackagePath -ErrorAction Stop
if (-not $PackageItem.PSIsContainer -or
    ($PackageItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    Fail "candidate directory is missing or linked"
}
$SourcePackageRoot = $PackageItem.FullName.TrimEnd("\", "/")
$TrustRoot = (Resolve-Path -LiteralPath "C:\ProgramData\Ruisheng\trust").Path.TrimEnd("\", "/")
if ($TrustRoot -eq $SourcePackageRoot -or $TrustRoot.StartsWith("$SourcePackageRoot\", [StringComparison]::OrdinalIgnoreCase)) {
    Fail "trust directory must be outside the candidate package"
}
$AllowedSigners = Join-Path $TrustRoot "release-allowed-signers"
$FingerprintPath = Join-Path $TrustRoot "release-key-fingerprint"
Assert-ProtectedAcl $PSCommandPath "external verifier"
Assert-ProtectedAncestors $PSCommandPath "external verifier" -AllowTrustedInstaller
Assert-ProtectedAcl $TrustRoot "trust directory"
Assert-ProtectedAncestors $TrustRoot "trust directory" -AllowTrustedInstaller
Assert-ProtectedAcl $AllowedSigners "allowed-signers"
Assert-ProtectedAcl $FingerprintPath "fingerprint"

$AllowedBytes = [IO.File]::ReadAllBytes($AllowedSigners)
$AllowedText = [Text.Encoding]::ASCII.GetString($AllowedBytes)
if ($AllowedText -cnotmatch '^ruisheng-release ssh-ed25519 ([A-Za-z0-9+/]+={0,2})\n$') {
    Fail "allowed-signers is not the approved single identity"
}
$SshKeygen = Join-Path ([Environment]::SystemDirectory) "OpenSSH\ssh-keygen.exe"
if (-not (Test-Path -LiteralPath $SshKeygen -PathType Leaf)) {
    Fail "system OpenSSH ssh-keygen is required"
}
Assert-ProtectedAcl $SshKeygen "system ssh-keygen" -AllowTrustedInstaller
Assert-ProtectedAncestors $SshKeygen "system ssh-keygen" -AllowTrustedInstaller
$Cmd = Join-Path ([Environment]::SystemDirectory) "cmd.exe"
if (-not (Test-Path -LiteralPath $Cmd -PathType Leaf)) {
    Fail "system command processor is required"
}
Assert-ProtectedAcl $Cmd "system command processor" -AllowTrustedInstaller
Assert-ProtectedAncestors $Cmd "system command processor" -AllowTrustedInstaller
$FingerprintOutput = & $SshKeygen -l -E sha256 -f $AllowedSigners 2>$null
if ($LASTEXITCODE -ne 0 -or $FingerprintOutput -notmatch '^256 (SHA256:[A-Za-z0-9+/]{43}) ') {
    Fail "allowed-signers does not contain a valid Ed25519 key"
}
$Fingerprint = $Matches[1]
if ([Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($FingerprintPath)) -cne "$Fingerprint`n") {
    Fail "fingerprint does not match allowed-signers"
}
$CurrentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$CurrentPrincipal = [Security.Principal.WindowsPrincipal]::new($CurrentIdentity)
if ($CurrentIdentity.User.Value -ne "S-1-5-18" -and -not $CurrentPrincipal.IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)) {
    Fail "bootstrap must run elevated to create an authenticated protected snapshot"
}
$FixedV2 = @(
    ".env.prod.example", "MANIFEST.json", "MANIFEST.md", "SHA256SUMS", "SHA256SUMS.sig",
    "docker-compose.prod.yml", "nginx.conf", "site-acceptance-profile.md.example",
    "site-health-acl.conf.example", "site-network.override.yml", "site-modbus-probe.json.example",
    "site-serial-hardware.json.example", "site-serial.env.example",
    "site-serial.override.yml", "setup-customer.md",
    "install_serial_hardware_task.ps1", "serial_hardware_attach.ps1",
    "probe_modbus_rtu.py", "run_modbus_probe.ps1",
    "validate-network-boundary.py", "validate_serial_hardware.py",
    "verify-candidate.ps1", "verify-candidate.sh"
)
$ExpectedV2 = [Collections.Generic.HashSet[string]]::new(
    [string[]]$FixedV2, [StringComparer]::Ordinal
)
foreach ($Component in @("postgres", "redis", "api", "gw", "web")) {
    [void]$ExpectedV2.Add("images/$Component.tar.gz")
}
$ExpectedV3 = [Collections.Generic.HashSet[string]]::new(
    [string[]]@($ExpectedV2), [StringComparer]::Ordinal
)
[void]$ExpectedV3.Add("qualification-toolchain.tar.gz")
$Actual = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
Get-ChildItem -LiteralPath $SourcePackageRoot -Force -Recurse | ForEach-Object {
    $Relative = $_.FullName.Substring($SourcePackageRoot.Length).
        TrimStart("\", "/").Replace("\", "/")
    if (($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail "candidate contains a link: $Relative"
    }
    if ($_.PSIsContainer) {
        if ($Relative -cne "images") { Fail "candidate contains an extra directory: $Relative" }
    } else { [void]$Actual.Add($Relative) }
}
$MatchesV2 = $Actual.SetEquals($ExpectedV2)
$MatchesV3 = $Actual.SetEquals($ExpectedV3)
if ($MatchesV2 -eq $MatchesV3) {
    Fail "candidate file allowlist mismatch: does not match complete v2 or v3"
}
$ExpectedSchemaVersion = if ($MatchesV3) { 3 } else { 2 }
$Expected = if ($MatchesV3) { $ExpectedV3 } else { $ExpectedV2 }

$SnapshotRoot = New-ProtectedSnapshotRoot "publisher-snapshot-"
$QualificationExtractionRoot = $null
$FreshnessContext = $null
try {
    [void](New-Item -ItemType Directory -Path (Join-Path $SnapshotRoot "images"))
    $SourceIdentities = @{}
    [Int64]$SnapshotBytes = 0
    foreach ($Relative in $Expected) {
        $SourcePath = Join-Path $SourcePackageRoot $Relative
        $SourceItem = Get-Item -Force -LiteralPath $SourcePath -ErrorAction Stop
        if ($SourceItem.PSIsContainer -or
            ($SourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Fail "candidate file changed or linked: $Relative"
        }
        $SourceIdentity = Get-CandidateSourceIdentity $SourcePath $Relative
        $SourceIdentities[$Relative] = $SourceIdentity
        $SnapshotBytes += [Int64]$SourceIdentity.Length
    }
    [Int64]$SnapshotReserve = [Math]::Max(64MB, [Int64]($SnapshotBytes / 10))
    $SnapshotDrive = (Get-Item -LiteralPath $SnapshotRoot -ErrorAction Stop).PSDrive
    if ($null -eq $SnapshotDrive -or $SnapshotDrive.Free -lt ($SnapshotBytes + $SnapshotReserve)) {
        Fail "insufficient free space for protected candidate snapshot"
    }
    foreach ($Relative in $Expected) {
        $SourcePath = Join-Path $SourcePackageRoot $Relative
        $DestinationPath = Join-Path $SnapshotRoot $Relative
        $SourceItem = Get-Item -Force -LiteralPath $SourcePath -ErrorAction Stop
        if ($SourceItem.PSIsContainer -or
            ($SourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Fail "candidate file changed or linked: $Relative"
        }
        Copy-CandidateFileToSnapshot $SourcePath $DestinationPath $Relative $SourceIdentities[$Relative]
    }
    $PackageRoot = $SnapshotRoot
$SumsPath = Join-Path $PackageRoot "SHA256SUMS"
$SignaturePath = Join-Path $PackageRoot "SHA256SUMS.sig"
foreach ($Path in @($SumsPath, $SignaturePath)) {
    $Item = Get-Item -Force -LiteralPath $Path -ErrorAction Stop
    if ($Item.PSIsContainer -or ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail "signed object or signature is missing or linked"
    }
}
$SignatureText = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($SignaturePath))
if ($SignatureText -cnotmatch '^-----BEGIN SSH SIGNATURE-----\n((?:[A-Za-z0-9+/]+={0,2}\n)+)-----END SSH SIGNATURE-----\n$') {
    Fail "SSH signature armor is not canonical"
}
try { [byte[]]$DecodedSignature = [Convert]::FromBase64String($Matches[1].Replace("`n", "")) } catch {
    Fail "SSH signature armor is invalid base64"
}
if ($DecodedSignature.Length -lt 6 -or
    [Text.Encoding]::ASCII.GetString($DecodedSignature[0..5]) -cne "SSHSIG") {
    Fail "SSH signature payload is invalid"
}
$EncodedSignature = [Convert]::ToBase64String($DecodedSignature)
$CanonicalLines = for ($Offset = 0; $Offset -lt $EncodedSignature.Length; $Offset += 70) {
    $EncodedSignature.Substring($Offset, [Math]::Min(70, $EncodedSignature.Length - $Offset))
}
$CanonicalSignature = "-----BEGIN SSH SIGNATURE-----`n" +
    ($CanonicalLines -join "`n") + "`n-----END SSH SIGNATURE-----`n"
if ($SignatureText -cne $CanonicalSignature) {
    Fail "SSH signature armor is not canonical"
}
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
    Fail "OpenSSH signature verification timed out"
}
$SshError = $SshErrorTask.GetAwaiter().GetResult()
$SshOutputTask.GetAwaiter().GetResult() | Out-Null
if ($Process.ExitCode -ne 0) { Fail "OpenSSH signature verification failed: $($SshError.Trim())" }

$Sums = @{}
$LineNumber = 0
try {
    $SumsText = [Text.UTF8Encoding]::new($false, $true).GetString($SumsBytes)
} catch {
    Fail "SHA256SUMS is not valid UTF-8"
}
if (-not $SumsText.EndsWith("`n") -or $SumsText.Contains("`r")) {
    Fail "SHA256SUMS must use canonical LF line endings"
}
($SumsText.Substring(0, $SumsText.Length - 1) -csplit "`n") | ForEach-Object {
    $LineNumber++
    if ($_ -cnotmatch '^([0-9a-f]{64})  ([^\\]+)$') { Fail "invalid SHA256SUMS entry at line $LineNumber" }
    $Relative = $Matches[2]
    $Parts = $Relative.Split("/")
    if ($Relative.StartsWith("/") -or $Parts.Contains("") -or $Parts.Contains(".") -or $Parts.Contains("..")) {
        Fail "unsafe SHA256SUMS path: $Relative"
    }
    if ($Sums.ContainsKey($Relative)) { Fail "duplicate SHA256SUMS path: $Relative" }
    $Sums[$Relative] = $Matches[1]
}
$ExpectedSums = @($Expected | Where-Object { $_ -cnotin @("SHA256SUMS", "SHA256SUMS.sig") })
if ($Sums.Count -ne $ExpectedSums.Count -or
    @($ExpectedSums | Where-Object { -not $Sums.ContainsKey($_) }).Count -ne 0) {
    Fail "SHA256SUMS allowlist mismatch"
}
$ManifestBytes = $null
foreach ($Relative in $ExpectedSums) {
    $CandidatePath = Join-Path $PackageRoot $Relative
    if ($Relative -ceq "MANIFEST.json") {
        if ((Get-Item -LiteralPath $CandidatePath -Force).Length -gt $MaxReleaseJsonBytes) {
            Fail "MANIFEST.json exceeds the 4 MiB JSON byte limit"
        }
        try { $CachedBytes = [IO.File]::ReadAllBytes($CandidatePath) }
        catch [OutOfMemoryException] { Fail "MANIFEST.json exceeds available memory" }
        $Hasher = [Security.Cryptography.SHA256]::Create()
        try {
            $Digest = ([BitConverter]::ToString(
                $Hasher.ComputeHash($CachedBytes)
            )).Replace("-", "").ToLowerInvariant()
        } finally { $Hasher.Dispose() }
        $ManifestBytes = $CachedBytes
    } else {
        $Digest = (Get-FileHash -Algorithm SHA256 -LiteralPath $CandidatePath).Hash.ToLowerInvariant()
    }
    if ($Digest -cne $Sums[$Relative]) { Fail "candidate hash mismatch: $Relative" }
}
if ($null -eq $ManifestBytes) { Fail "MANIFEST.json is missing from authenticated hashes" }
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
Assert-NoDuplicateJsonKeys $ManifestBytes "authenticated MANIFEST.json"
try {
    $Manifest = [Text.UTF8Encoding]::new($false, $true).GetString($ManifestBytes) | ConvertFrom-Json
} catch {
    Fail "cannot parse authenticated MANIFEST.json"
}
$ExpectedAuthenticity = @{
    status = "SIGNED"; scheme = "openssh-sshsig"; publisher = "ruisheng-release"
    namespace = "ruisheng-candidate-v1"; key_type = "ssh-ed25519"
    key_fingerprint = $Fingerprint; signed_object = "SHA256SUMS"
    signature_file = "SHA256SUMS.sig"
}
if ($Manifest.schema_version -is [bool] -or
    ($Manifest.schema_version -isnot [int] -and $Manifest.schema_version -isnot [long]) -or
    $Manifest.schema_version -ne $ExpectedSchemaVersion -or
    @($Manifest.authenticity.PSObject.Properties).Count -ne 8 -or
    @($ExpectedAuthenticity.Keys | Where-Object {
        $Manifest.authenticity.PSObject.Properties.Name -cnotcontains $_ -or
        $Manifest.authenticity.$_ -cne $ExpectedAuthenticity[$_]
    }).Count -ne 0) {
    Fail "signed manifest authenticity contract is invalid"
}

function Get-QualificationSha256([byte[]]$Bytes) {
    $Hasher = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($Hasher.ComputeHash($Bytes))).Replace("-", "").ToLowerInvariant()
    } finally { $Hasher.Dispose() }
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
    Fail "manifest logical identity contains an unsupported value type"
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
    return "sha256:$(Get-QualificationSha256 ([Text.Encoding]::UTF8.GetBytes($Json)))"
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
    if ($SchemaVersion -eq 2) { return $null }

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
        Fail "qualification toolchain descriptor contract is invalid"
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
            Fail "qualification toolchain identity is invalid for $($IdentityPaths[$Name])"
        }
    }

    if (-not ("System.Formats.Tar.TarReader" -as [type])) {
        Fail "PowerShell 7.3 or newer is required for qualification toolchain validation"
    }
    $ExpectedMembers = @($MemberNames) + $InternalName
    $QualificationMemberLimits = @{}
    foreach ($Name in $ExpectedMembers) { $QualificationMemberLimits[$Name] = 64MB }
    $QualificationMemberLimits[$InternalName] = $MaxReleaseJsonBytes
    $ArchivePath = Join-Path $AuthenticatedRoot $ArchiveName
    Assert-CanonicalQualificationUstarArchive `
        $ArchivePath $ExpectedMembers $QualificationMemberLimits
    $Contents = @{}
    $ArchiveFile = $null
    $Gzip = $null
    $Reader = $null
    try {
        $ArchiveFile = [IO.File]::OpenRead($ArchivePath)
        $Gzip = [IO.Compression.GZipStream]::new(
            $ArchiveFile, [IO.Compression.CompressionMode]::Decompress, $false
        )
        $Reader = [System.Formats.Tar.TarReader]::new($Gzip, $false)
        $Index = 0
        while ($null -ne ($Entry = $Reader.GetNextEntry($false))) {
            [Int64]$MemberLimit = if ([string]$Entry.Name -ceq $InternalName) {
                $MaxReleaseJsonBytes
            } else {
                64MB
            }
            if ($Index -ge $ExpectedMembers.Count -or
                [string]$Entry.Name -cne $ExpectedMembers[$Index] -or
                $null -eq $Entry.DataStream -or $Entry.Length -gt $MemberLimit -or
                $Entry.EntryType.ToString() -in @("SymbolicLink", "HardLink", "Directory")) {
                Fail "qualification toolchain archive member allowlist mismatch"
            }
            $Buffer = [IO.MemoryStream]::new()
            try {
                $Entry.DataStream.CopyTo($Buffer)
                if ($Buffer.Length -ne $Entry.Length) {
                    Fail "qualification toolchain member size mismatch"
                }
                $Contents[[string]$Entry.Name] = $Buffer.ToArray()
            } finally { $Buffer.Dispose() }
            $Index++
        }
        if ($Index -ne $ExpectedMembers.Count) {
            Fail "qualification toolchain archive member allowlist mismatch"
        }
    } catch {
        if ($_.Exception.Message -like "*qualification toolchain*") { throw }
        Fail "invalid qualification toolchain archive: $($_.Exception.Message)"
    } finally {
        if ($null -ne $Reader) { $Reader.Dispose() }
        if ($null -ne $Gzip) { $Gzip.Dispose() }
        if ($null -ne $ArchiveFile) { $ArchiveFile.Dispose() }
    }
    [byte[]]$InternalBytes = $Contents[$InternalName]
    if ((Get-QualificationSha256 $InternalBytes) -cne $Toolchain.toolchain_manifest.sha256) {
        Fail "qualification toolchain manifest SHA-256 mismatch"
    }
    Assert-NoDuplicateJsonKeys $InternalBytes "qualification toolchain manifest"
    try {
        $Internal = [Text.UTF8Encoding]::new($false, $true).GetString($InternalBytes) |
            ConvertFrom-Json
    } catch { Fail "qualification toolchain manifest is invalid JSON" }
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
        Fail "qualification toolchain manifest contract is invalid"
    }
    $Resolved = @{}
    for ($Index = 0; $Index -lt $MemberNames.Count; $Index++) {
        $Identity = $Internal.members[$Index]
        Assert-ExactProperties $Identity @("path", "sha256") "qualification member identity"
        $ExpectedPath = $MemberNames[$Index]
        $Digest = Get-QualificationSha256 ([byte[]]$Contents[$ExpectedPath])
        if ($Identity.path -cne $ExpectedPath -or $Identity.sha256 -cne $Digest) {
            Fail "qualification toolchain member SHA-256 mismatch: $ExpectedPath"
        }
        $Resolved[$ExpectedPath] = $Digest
    }
    foreach ($Name in @("schema", "validator", "producer", "receipt_producer")) {
        if ($Toolchain.$Name.sha256 -cne $Resolved[$IdentityPaths[$Name]]) {
            Fail "qualification toolchain descriptor identity mismatch: $($IdentityPaths[$Name])"
        }
    }
    return $Contents
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

function Write-AuthenticatedQualificationToolchain(
    [hashtable]$Contents, [string]$ExtractionRoot
) {
    $ExpectedMembers = @(
        "tools/validate_device_point_profile.py",
        "tools/trust_root_freshness.py",
        "schemas/point-profile/point-profile-v1.schema.json",
        "tools/release_artifacts.py",
        "tools/release_verification_receipt.py",
        "pyproject.toml",
        "uv.lock",
        "qualification-toolchain-manifest.json"
    )
    if ($null -eq $Contents -or $Contents.Count -ne $ExpectedMembers.Count -or
        @($ExpectedMembers | Where-Object { -not $Contents.ContainsKey($_) }).Count -ne 0) {
        Fail "authenticated qualification toolchain contents are incomplete"
    }
    foreach ($RelativeDirectory in @("tools", "schemas", "schemas/point-profile")) {
        $Directory = Join-Path $ExtractionRoot $RelativeDirectory
        [void](New-Item -ItemType Directory -Path $Directory)
        Set-ProtectedSnapshotAcl $Directory
    }
    foreach ($Relative in $ExpectedMembers) {
        $Destination = Join-Path $ExtractionRoot $Relative
        $Stream = $null
        try {
            $Stream = [IO.File]::Open(
                $Destination,
                [IO.FileMode]::CreateNew,
                [IO.FileAccess]::Write,
                [IO.FileShare]::None
            )
            [byte[]]$Bytes = $Contents[$Relative]
            $Stream.Write($Bytes, 0, $Bytes.Length)
            $Stream.Flush($true)
        } finally {
            if ($null -ne $Stream) { $Stream.Dispose() }
        }
    }
}

$MaxQualificationRuntimeFiles = [Int64]32768
$MaxQualificationRuntimeDirectories = [Int64]32768
$MaxQualificationRuntimeFileBytes = [Int64]536870912
$MaxQualificationRuntimeTotalBytes = [Int64]34359738368
$MaxQualificationRuntimePathBytes = [Int64]4096

function Assert-QualificationRuntimeManifestFileCount([Array]$Files) {
    $FileCount = [Int64]$Files.LongLength
    if ($FileCount -eq 0 -or $FileCount -ge $MaxQualificationRuntimeFiles) {
        Fail "qualification runtime manifest files are invalid"
    }
}

function Add-QualificationRuntimeFileBytes(
    [Int64]$CurrentBytes, [Int64]$FileBytes, [string]$Label
) {
    if ($FileBytes -lt 0 -or $FileBytes -gt $MaxQualificationRuntimeFileBytes) {
        Fail "$Label exceeds its byte limit"
    }
    if ($CurrentBytes -lt 0 -or
        $CurrentBytes -gt $MaxQualificationRuntimeTotalBytes -or
        $CurrentBytes -gt ($MaxQualificationRuntimeTotalBytes - $FileBytes)) {
        Fail "qualification runtime exceeds its aggregate byte limit"
    }
    return [Int64]($CurrentBytes + $FileBytes)
}

function Add-ExpectedQualificationRuntimeDirectory(
    [Collections.Generic.HashSet[string]]$ExpectedDirectories,
    [Collections.Generic.Dictionary[string, object]]$CaseInsensitiveMembers,
    [string]$Relative
) {
    if ($CaseInsensitiveMembers.ContainsKey($Relative)) {
        $ExistingMember = $CaseInsensitiveMembers[$Relative]
        if ($ExistingMember.Kind -cne "directory" -or
            $ExistingMember.Path -cne $Relative) {
            Fail "qualification runtime contains a case-insensitive path collision"
        }
        return
    }
    if ([Int64]$ExpectedDirectories.Count -ge $MaxQualificationRuntimeDirectories) {
        Fail "qualification runtime contains too many directories"
    }
    $CaseInsensitiveMembers.Add(
        $Relative, [pscustomobject]@{ Path = $Relative; Kind = "directory" }
    )
    if (-not $ExpectedDirectories.Add($Relative)) {
        Fail "qualification runtime contains a case-insensitive path collision"
    }
}

function Get-LockedFileSha256([IO.FileStream]$Stream) {
    $Hasher = [Security.Cryptography.SHA256]::Create()
    try {
        $Stream.Position = 0
        return ([BitConverter]::ToString(
            $Hasher.ComputeHash($Stream)
        )).Replace("-", "").ToLowerInvariant()
    } finally {
        $Stream.Position = 0
        $Hasher.Dispose()
    }
}

function Read-LockedFileBytes(
    [IO.FileStream]$Stream, [Int64]$MaximumBytes, [string]$Label
) {
    if ($Stream.Length -lt 0 -or $Stream.Length -gt $MaximumBytes -or
        $Stream.Length -gt [int]::MaxValue) {
        Fail "$Label exceeds its size boundary"
    }
    $Bytes = [byte[]]::new([int]$Stream.Length)
    $Stream.Position = 0
    $Offset = 0
    while ($Offset -lt $Bytes.Length) {
        $Read = $Stream.Read($Bytes, $Offset, $Bytes.Length - $Offset)
        if ($Read -le 0) { Fail "$Label was truncated while being read" }
        $Offset += $Read
    }
    if ($Stream.ReadByte() -ne -1) { Fail "$Label grew while being read" }
    $Stream.Position = 0
    return $Bytes
}

function Resolve-QualificationRuntimePath(
    [string]$RuntimeRoot, [object]$PathValue, [string]$Label
) {
    if ($PathValue -isnot [string] -or [string]::IsNullOrWhiteSpace($PathValue)) {
        Fail "$Label must be a non-empty relative path"
    }
    $Relative = [string]$PathValue
    if ($Relative.IndexOf([char]0) -ge 0 -or $Relative.Contains("\") -or
        $Relative.StartsWith("/", [StringComparison]::Ordinal)) {
        Fail "$Label is not a canonical relative path"
    }
    try {
        $EncodedLength = [Int64]([Text.UTF8Encoding]::new(
            $false, $true
        ).GetByteCount($Relative))
    } catch { Fail "$Label is not valid UTF-8" }
    if ($EncodedLength -gt $MaxQualificationRuntimePathBytes) {
        Fail "$Label is not a canonical relative path"
    }
    $Segments = $Relative.Split("/")
    foreach ($Segment in $Segments) {
        if ([string]::IsNullOrEmpty($Segment) -or $Segment -in @(".", "..") -or
            $Segment.EndsWith(" ", [StringComparison]::Ordinal) -or
            $Segment.EndsWith(".", [StringComparison]::Ordinal) -or
            $Segment.IndexOfAny([IO.Path]::GetInvalidFileNameChars()) -ge 0) {
            Fail "$Label is not a canonical relative path"
        }
    }
    try {
        $CanonicalRoot = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd("\")
        $FullPath = [IO.Path]::GetFullPath(
            (Join-Path $CanonicalRoot $Relative.Replace("/", "\"))
        )
    } catch { Fail "$Label is not a valid runtime path" }
    if (-not $FullPath.StartsWith(
        "$CanonicalRoot\", [StringComparison]::OrdinalIgnoreCase
    )) {
        Fail "$Label escapes the fixed qualification runtime"
    }
    $CanonicalRelative = $FullPath.Substring($CanonicalRoot.Length + 1).Replace("\", "/")
    if ($CanonicalRelative -cne $Relative) {
        Fail "$Label is not a canonical relative path"
    }
    return $FullPath
}

function Assert-QualificationRuntimeLayout(
    [string]$RuntimeRoot,
    [Collections.Generic.HashSet[string]]$ExpectedFiles,
    [Collections.Generic.HashSet[string]]$ExpectedDirectories,
    [string]$DependencyRoot
) {
    $RootItem = Get-Item -Force -LiteralPath $RuntimeRoot -ErrorAction Stop
    if (-not $RootItem.PSIsContainer -or
        ($RootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail "fixed qualification runtime root is missing or linked"
    }
    Assert-ProtectedAcl $RuntimeRoot "fixed qualification runtime root" -AllowTrustedInstaller
    Assert-ProtectedAncestors $RuntimeRoot `
        "fixed qualification runtime root" -AllowTrustedInstaller

    $ActualFiles = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    $ActualDirectories = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal
    )
    $Pending = [Collections.Generic.Stack[string]]::new()
    $TotalBytes = [Int64]0
    $MaximumActualFiles = [Int64]($MaxQualificationRuntimeFiles + 1)
    $Pending.Push($RuntimeRoot)
    while ($Pending.Count -ne 0) {
        $Current = $Pending.Pop()
        try {
            $Entries = [IO.Directory]::EnumerateFileSystemEntries($Current)
            foreach ($EntryPath in $Entries) {
                $Item = Get-Item -Force -LiteralPath $EntryPath -ErrorAction Stop
                $Relative = $Item.FullName.Substring($RuntimeRoot.Length).
                    TrimStart("\", "/").Replace("\", "/")
                if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    Fail "fixed qualification runtime contains a reparse point: $Relative"
                }
                Assert-ProtectedAcl $Item.FullName `
                    "qualification runtime member ${Relative}" -AllowTrustedInstaller
                Assert-ProtectedAncestors $Item.FullName `
                    "qualification runtime member ${Relative}" -AllowTrustedInstaller
                if ($Item.PSIsContainer) {
                    if (-not $ExpectedDirectories.Contains($Relative)) {
                        Fail "qualification runtime file allowlist mismatch"
                    }
                    if (-not $ActualDirectories.Contains($Relative)) {
                        if ([Int64]$ActualDirectories.Count -ge
                            $MaxQualificationRuntimeDirectories) {
                            Fail "qualification runtime contains too many directories"
                        }
                        [void]$ActualDirectories.Add($Relative)
                    }
                    $Pending.Push($Item.FullName)
                } else {
                    if (-not $ExpectedFiles.Contains($Relative)) {
                        Fail "qualification runtime file allowlist mismatch"
                    }
                    if (-not $ActualFiles.Contains($Relative)) {
                        if ([Int64]$ActualFiles.Count -ge $MaximumActualFiles) {
                            Fail "qualification runtime contains too many files"
                        }
                        $TotalBytes = Add-QualificationRuntimeFileBytes `
                            $TotalBytes ([Int64]$Item.Length) `
                            "qualification runtime member ${Relative}"
                        [void]$ActualFiles.Add($Relative)
                    }
                }
            }
        } catch {
            if ($_.Exception.Message -like "*qualification runtime*") { throw }
            Fail "cannot enumerate qualification runtime: ${Current}: $($_.Exception.Message)"
        }
    }
    if (-not (Test-Path -LiteralPath $DependencyRoot -PathType Container)) {
        Fail "qualification runtime dependency_root is missing"
    }
    if (-not $ActualFiles.SetEquals($ExpectedFiles) -or
        -not $ActualDirectories.SetEquals($ExpectedDirectories)) {
        Fail "qualification runtime file allowlist mismatch"
    }
}

function Assert-SafePythonPathConfiguration(
    [Collections.Generic.List[object]]$Locks,
    [string]$RuntimeRoot,
    [string]$DependencyRoot
) {
    $PathConfigurations = @($Locks | Where-Object {
        $_.Relative.EndsWith("._pth", [StringComparison]::OrdinalIgnoreCase)
    })
    if ($PathConfigurations.Count -gt 1) {
        Fail "qualification runtime contains multiple Python _pth configurations"
    }
    if ($PathConfigurations.Count -eq 0) { return }
    $Configuration = $PathConfigurations[0]
    if ($Configuration.Relative -cnotin @("python._pth", "python311._pth")) {
        Fail "qualification runtime contains an unsupported Python _pth configuration"
    }
    try {
        [byte[]]$Bytes = Read-LockedFileBytes (
            $Configuration.Stream
        ) 1MB "qualification runtime Python _pth configuration"
        $Text = [Text.UTF8Encoding]::new($false, $true).GetString($Bytes)
    } catch {
        if ($_.Exception.Message -like "*qualification runtime*") { throw }
        Fail "qualification runtime Python _pth configuration is not UTF-8"
    }
    foreach ($RawLine in ($Text -split "`r?`n")) {
        $Line = $RawLine.Trim()
        if ([string]::IsNullOrEmpty($Line) -or $Line.StartsWith("#")) { continue }
        if ($Line -match '^(?i:import)(?:\s|$)') {
            Fail "qualification runtime Python _pth must not import site"
        }
        $ConfiguredPath = if ($Line -ceq ".") {
            $RuntimeRoot
        } else {
            Resolve-QualificationRuntimePath `
                $RuntimeRoot $Line "qualification runtime Python _pth entry"
        }
        if ([string]::Equals(
            $ConfiguredPath, $DependencyRoot, [StringComparison]::OrdinalIgnoreCase
        )) {
            Fail "qualification runtime dependency_root must be added only by the bootstrap"
        }
        if (-not (Test-Path -LiteralPath $ConfiguredPath)) {
            Fail "qualification runtime Python _pth entry is missing"
        }
    }
}

function Open-ProtectedSystemPython([string]$AuthenticatedUvLockSha256) {
    if ($AuthenticatedUvLockSha256 -cnotmatch '^[0-9a-f]{64}$') {
        Fail "authenticated qualification uv.lock SHA-256 is invalid"
    }
    $RuntimeRoot = [IO.Path]::GetFullPath("C:\ProgramData\Ruisheng\runtime").TrimEnd("\")
    $ManifestPath = Join-Path $RuntimeRoot "qualification-runtime-manifest.json"
    $Locks = [Collections.Generic.List[object]]::new()
    $ManifestStream = $null
    try {
        $ManifestItem = Get-Item -Force -LiteralPath $ManifestPath -ErrorAction Stop
        if ($ManifestItem.PSIsContainer -or
            ($ManifestItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Fail "qualification runtime manifest is missing or linked"
        }
        $ManifestStream = [IO.File]::Open(
            $ManifestPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
        )
        $ManifestIdentity = Get-OpenFileIdentity `
            $ManifestStream $ManifestPath "qualification runtime manifest"
        if ($ManifestIdentity.NumberOfLinks -ne 1) {
            $ManifestStream.Dispose()
            Fail "qualification runtime manifest has multiple hard links"
        }
        Assert-ProtectedAcl $ManifestPath "qualification runtime manifest" -AllowTrustedInstaller
        Assert-ProtectedAncestors $ManifestPath `
            "qualification runtime manifest" -AllowTrustedInstaller
        [byte[]]$ManifestBytes = Read-LockedFileBytes `
            $ManifestStream 4MB "qualification runtime manifest"
        $ManifestDigest = Get-LockedFileSha256 $ManifestStream
        [void]$Locks.Add([pscustomobject]@{
            Path = $ManifestPath
            Relative = "qualification-runtime-manifest.json"
            Stream = $ManifestStream
            Identity = $ManifestIdentity
            ExpectedSha256 = $ManifestDigest
        })
        $ManifestAfter = Get-OpenFileIdentity `
            $ManifestStream $ManifestPath "qualification runtime manifest"
        Assert-SameFileIdentity $ManifestIdentity $ManifestAfter `
            "qualification runtime manifest during read"
        try {
            $Manifest = [Text.UTF8Encoding]::new($false, $true).GetString($ManifestBytes) |
                ConvertFrom-Json -Depth 20
        } catch { Fail "qualification runtime manifest is invalid JSON" }
        Assert-ExactProperties $Manifest @(
            "artifact_type", "schema_version", "python_version", "uv_lock_sha256",
            "dependency_root", "files"
        ) "qualification runtime manifest"
        if ($Manifest.artifact_type -isnot [string] -or
            $Manifest.artifact_type -cne "ruisheng.qualification-runtime" -or
            $Manifest.schema_version -is [bool] -or
            ($Manifest.schema_version -isnot [int] -and
                $Manifest.schema_version -isnot [long]) -or
            $Manifest.schema_version -ne 1 -or
            $Manifest.python_version -isnot [string] -or
            $Manifest.python_version -cne "3.11" -or
            $Manifest.uv_lock_sha256 -isnot [string] -or
            $Manifest.uv_lock_sha256 -cne $AuthenticatedUvLockSha256 -or
            $Manifest.dependency_root -isnot [string] -or
            $Manifest.dependency_root -cne "Lib/site-packages" -or
            $Manifest.files -isnot [Array]) {
            Fail "qualification runtime manifest contract is invalid"
        }

        $DependencyRelative = [string]$Manifest.dependency_root
        $DependencyRoot = Resolve-QualificationRuntimePath `
            $RuntimeRoot $DependencyRelative "qualification runtime dependency_root"
        Assert-QualificationRuntimeManifestFileCount $Manifest.files
        $Files = @($Manifest.files)
        $ExpectedFiles = [Collections.Generic.HashSet[string]]::new(
            [StringComparer]::Ordinal
        )
        $ExpectedDirectories = [Collections.Generic.HashSet[string]]::new(
            [StringComparer]::Ordinal
        )
        $CaseInsensitiveMembers = [Collections.Generic.Dictionary[string, object]]::new(
            [StringComparer]::OrdinalIgnoreCase
        )
        [void]$ExpectedFiles.Add("qualification-runtime-manifest.json")
        $CaseInsensitiveMembers.Add(
            "qualification-runtime-manifest.json",
            [pscustomobject]@{ Path = "qualification-runtime-manifest.json"; Kind = "file" }
        )
        $PreviousPath = $null
        foreach ($FileIdentity in $Files) {
            Assert-ExactProperties $FileIdentity @("path", "sha256") `
                "qualification runtime file identity"
            if ($FileIdentity.path -isnot [string] -or
                $FileIdentity.sha256 -isnot [string] -or
                $FileIdentity.sha256 -cnotmatch '^[0-9a-f]{64}$') {
                Fail "qualification runtime file identity is invalid"
            }
            $Relative = [string]$FileIdentity.path
            [void](Resolve-QualificationRuntimePath `
                $RuntimeRoot $Relative "qualification runtime file path"
            )
            if ($Relative -ceq "qualification-runtime-manifest.json" -or
                $Relative.EndsWith(".pth", [StringComparison]::OrdinalIgnoreCase) -or
                [string]::Equals(
                    $Relative, "pyvenv.cfg", [StringComparison]::OrdinalIgnoreCase
                )) {
                Fail "qualification runtime contains a forbidden file: $Relative"
            }
            if ($null -ne $PreviousPath -and
                [StringComparer]::Ordinal.Compare($PreviousPath, $Relative) -ge 0) {
                Fail "qualification runtime files are not in strict ordinal path order"
            }
            if (-not $ExpectedFiles.Add($Relative) -or
                $CaseInsensitiveMembers.ContainsKey($Relative)) {
                Fail "qualification runtime contains a case-insensitive path collision"
            }
            $CaseInsensitiveMembers.Add(
                $Relative, [pscustomobject]@{ Path = $Relative; Kind = "file" }
            )
            $Parent = [IO.Path]::GetDirectoryName($Relative.Replace("/", "\"))
            while (-not [string]::IsNullOrEmpty($Parent)) {
                $ParentRelative = $Parent.Replace("\", "/")
                Add-ExpectedQualificationRuntimeDirectory `
                    $ExpectedDirectories $CaseInsensitiveMembers $ParentRelative
                $Parent = [IO.Path]::GetDirectoryName($Parent)
            }
            $PreviousPath = $Relative
        }
        $DependencyParent = $DependencyRelative.Replace("/", "\")
        while (-not [string]::IsNullOrEmpty($DependencyParent)) {
            $DependencyParentRelative = $DependencyParent.Replace("\", "/")
            Add-ExpectedQualificationRuntimeDirectory `
                $ExpectedDirectories $CaseInsensitiveMembers $DependencyParentRelative
            $DependencyParent = [IO.Path]::GetDirectoryName($DependencyParent)
        }
        if (-not $ExpectedFiles.Contains("python.exe") -or
            -not $ExpectedFiles.Contains("python311.dll") -or
            (-not $ExpectedFiles.Contains("python311.zip") -and
                -not $ExpectedFiles.Contains("Lib/encodings/__init__.py"))) {
            Fail "qualification runtime is not a self-contained Python 3.11 runtime"
        }
        if (@($Files | Where-Object {
            ([string]$_.path).StartsWith(
                "$DependencyRelative/", [StringComparison]::Ordinal
            )
        }).Count -eq 0) {
            Fail "qualification runtime dependency_root has no manifest-bound files"
        }

        Assert-QualificationRuntimeLayout `
            $RuntimeRoot $ExpectedFiles $ExpectedDirectories $DependencyRoot
        $PythonLock = $null
        $RuntimeFileLocks = [Collections.Generic.List[object]]::new()
        $TotalBytes = [Int64]$ManifestIdentity.Length
        if ($TotalBytes -lt 0 -or $TotalBytes -gt $MaxQualificationRuntimeTotalBytes) {
            Fail "qualification runtime exceeds its aggregate byte limit"
        }
        foreach ($FileIdentity in $Files) {
            $Relative = [string]$FileIdentity.path
            $Path = Resolve-QualificationRuntimePath `
                $RuntimeRoot $Relative "qualification runtime file path"
            $Stream = $null
            try {
                $Stream = [IO.File]::Open(
                    $Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
                )
                $Identity = Get-OpenFileIdentity `
                    $Stream $Path "qualification runtime file ${Relative}"
                if ($Identity.NumberOfLinks -ne 1) {
                    Fail "qualification runtime file has multiple hard links: $Relative"
                }
                Assert-ProtectedAcl $Path "qualification runtime file ${Relative}" `
                    -AllowTrustedInstaller
                Assert-ProtectedAncestors $Path "qualification runtime file ${Relative}" `
                    -AllowTrustedInstaller
                $TotalBytes = Add-QualificationRuntimeFileBytes `
                    $TotalBytes ([Int64]$Identity.Length) `
                    "qualification runtime file ${Relative}"
                $Lock = [pscustomobject]@{
                    Path = $Path
                    Relative = $Relative
                    Stream = $Stream
                    Identity = $Identity
                    ExpectedSha256 = [string]$FileIdentity.sha256
                }
                [void]$Locks.Add($Lock)
                [void]$RuntimeFileLocks.Add($Lock)
                if ($Relative -ceq "python.exe") { $PythonLock = $Lock }
                $Stream = $null
            } finally {
                if ($null -ne $Stream) { $Stream.Dispose() }
            }
        }
        foreach ($Lock in $RuntimeFileLocks) {
            $Digest = Get-LockedFileSha256 $Lock.Stream
            if ($Digest -cne $Lock.ExpectedSha256) {
                Fail "qualification runtime file SHA-256 mismatch: $($Lock.Relative)"
            }
            $IdentityAfter = Get-OpenFileIdentity `
                $Lock.Stream $Lock.Path "qualification runtime file $($Lock.Relative)"
            Assert-SameFileIdentity $Lock.Identity $IdentityAfter `
                "qualification runtime file $($Lock.Relative) during hash"
        }
        if ($null -eq $PythonLock) { Fail "qualification runtime python.exe lock is missing" }
        Assert-SafePythonPathConfiguration $Locks $RuntimeRoot $DependencyRoot
        Assert-QualificationRuntimeLayout `
            $RuntimeRoot $ExpectedFiles $ExpectedDirectories $DependencyRoot
        return [pscustomobject]@{
            Root = $RuntimeRoot
            DependencyRoot = $DependencyRoot
            Python = $PythonLock
            Locks = $Locks
            ExpectedFiles = $ExpectedFiles
            ExpectedDirectories = $ExpectedDirectories
        }
    } catch {
        foreach ($Lock in $Locks) {
            if ($null -ne $Lock.Stream) { $Lock.Stream.Dispose() }
        }
        if ($null -ne $ManifestStream) { $ManifestStream.Dispose() }
        throw
    }
}

function Assert-ProtectedQualificationRuntimeUnchanged([object]$Runtime) {
    foreach ($Lock in $Runtime.Locks) {
        $After = Get-OpenFileIdentity `
            $Lock.Stream $Lock.Path "qualification runtime file $($Lock.Relative)"
        Assert-SameFileIdentity $Lock.Identity $After `
            "qualification runtime file $($Lock.Relative) during execution"
        if ((Get-LockedFileSha256 $Lock.Stream) -cne $Lock.ExpectedSha256) {
            Fail "qualification runtime file content changed during execution: $($Lock.Relative)"
        }
        Assert-ProtectedAcl $Lock.Path "qualification runtime file $($Lock.Relative)" `
            -AllowTrustedInstaller
        Assert-ProtectedAncestors $Lock.Path `
            "qualification runtime file $($Lock.Relative)" -AllowTrustedInstaller
    }
    Assert-QualificationRuntimeLayout `
        $Runtime.Root $Runtime.ExpectedFiles $Runtime.ExpectedDirectories $Runtime.DependencyRoot
}

function Get-QualificationInvocation(
    [string]$Mode,
    [object]$AuthenticatedManifest,
    [string]$AuthenticatedPackageRoot,
    [object]$FreshnessContext = $null
) {
    $Entrypoint = ""
    [string[]]$Arguments = @()
    switch ($Mode) {
        "ValidatorSchema" {
            $Entrypoint = "tools/validate_device_point_profile.py"
            $Arguments = @("schema")
        }
        "ValidatorProfile" {
            if ($null -eq $FreshnessContext) {
                Fail "ValidatorProfile freshness context is missing"
            }
            $Entrypoint = "tools/trust_root_freshness.py"
            $Arguments = @(
                "qualify"
            ) + [string[]](Get-FreshnessBoundArguments `
                $AuthenticatedManifest $FreshnessContext) + @(
                "--evidence-root", [IO.Path]::GetFullPath($QualificationRootPath)
            )
        }
        "ValidatorLegacy" {
            $Entrypoint = "tools/validate_device_point_profile.py"
            $Arguments = @(
                "validate-legacy", [IO.Path]::GetFullPath($QualificationEvidencePath),
                "--root", [IO.Path]::GetFullPath($QualificationRootPath)
            )
        }
        "Receipt" {
            $Entrypoint = "tools/release_verification_receipt.py"
            $Arguments = @(
                [IO.Path]::GetFullPath($AuthenticatedPackageRoot),
                "--output-directory", [IO.Path]::GetFullPath($QualificationOutputDirectory),
                "--signing-identity", [IO.Path]::GetFullPath($QualificationSigningIdentity),
                "--verifier-id", $QualificationVerifierId,
                "--verifier-key-id", $QualificationVerifierKeyId,
                "--verifier-tool-sha256",
                "sha256:$($AuthenticatedManifest.qualification_toolchain.receipt_producer.sha256)"
            )
        }
        default { Fail "unsupported qualification mode" }
    }
    return [pscustomobject]@{ Entrypoint = $Entrypoint; Arguments = $Arguments }
}

function Get-FreshnessBoundArguments(
    [object]$AuthenticatedManifest, [object]$FreshnessContext
) {
    return @(
        $FreshnessContext.ProfileSnapshot.Path,
        "--trust-policy", $FreshnessContext.PolicySnapshot.Path,
        "--trust-root-snapshot", $FreshnessContext.TrustRootSnapshot.Path,
        "--provider-config-snapshot", $FreshnessContext.ConfigSnapshot.Path,
        "--attestation", $FreshnessContext.Attestation.Path,
        "--challenge", $FreshnessContext.Challenge,
        "--requested-at", $FreshnessContext.RequestedAt,
        "--candidate-logical-identity", [string]$AuthenticatedManifest.logical_identity,
        "--expected-trust-root-snapshot-sha256",
        "sha256:$($FreshnessContext.TrustRootSnapshot.ExpectedSha256)",
        "--expected-provider-config-snapshot-sha256",
        "sha256:$($FreshnessContext.ConfigSnapshot.ExpectedSha256)",
        "--expected-attestation-sha256",
        "sha256:$($FreshnessContext.Attestation.ExpectedSha256)"
    )
}

function Get-FreshnessPreflightInvocation(
    [object]$AuthenticatedManifest, [object]$FreshnessContext
) {
    if ($null -eq $FreshnessContext) { Fail "freshness preflight context is missing" }
    return [pscustomobject]@{
        Entrypoint = "tools/trust_root_freshness.py"
        Arguments = @("preflight") + [string[]](Get-FreshnessBoundArguments `
            $AuthenticatedManifest $FreshnessContext)
    }
}

# BEGIN qualification process containment helpers
if (-not ("Ruisheng.ReleaseTrust.KillOnCloseJob" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace Ruisheng.ReleaseTrust {
    public sealed class DescendantProcessSet : IDisposable {
        private const UInt32 SnapshotProcesses = 0x00000002;
        private const UInt32 ProcessTerminate = 0x00000001;
        private const UInt32 Synchronize = 0x00100000;
        private const UInt32 WaitObject0 = 0x00000000;
        private const UInt32 WaitTimeout = 0x00000102;
        private const Int32 ErrorAccessDenied = 5;
        private const Int32 ErrorInvalidParameter = 87;
        private readonly System.Collections.Generic.List<IntPtr> handles;
        private readonly UInt32[] observedProcessIds;

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct ProcessEntry32 {
            public UInt32 Size;
            public UInt32 Usage;
            public UInt32 ProcessId;
            public IntPtr DefaultHeapId;
            public UInt32 ModuleId;
            public UInt32 Threads;
            public UInt32 ParentProcessId;
            public Int32 BasePriority;
            public UInt32 Flags;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)]
            public string ExecutableFile;
        }

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr CreateToolhelp32Snapshot(UInt32 flags, UInt32 processId);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool Process32FirstW(IntPtr snapshot, ref ProcessEntry32 entry);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool Process32NextW(IntPtr snapshot, ref ProcessEntry32 entry);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenProcess(
            UInt32 desiredAccess, [MarshalAs(UnmanagedType.Bool)] bool inherit, UInt32 processId
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool TerminateProcess(IntPtr process, UInt32 exitCode);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern UInt32 WaitForSingleObject(IntPtr process, UInt32 milliseconds);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CloseHandle(IntPtr handle);

        private DescendantProcessSet(
            System.Collections.Generic.List<IntPtr> values, UInt32[] processIds
        ) {
            handles = values;
            observedProcessIds = processIds;
        }

        public Int32 Count { get { return handles.Count; } }
        public UInt32[] ObservedProcessIds { get { return observedProcessIds; } }

        public static DescendantProcessSet Capture(UInt32 rootProcessId) {
            return Capture(new UInt32[] { rootProcessId });
        }

        public static DescendantProcessSet Capture(UInt32[] rootProcessIds) {
            if (rootProcessIds == null || rootProcessIds.Length == 0) {
                throw new ArgumentException("at least one process-tree root is required");
            }
            IntPtr snapshot = CreateToolhelp32Snapshot(SnapshotProcesses, 0);
            if (snapshot == new IntPtr(-1)) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            var children = new System.Collections.Generic.Dictionary<
                UInt32, System.Collections.Generic.List<UInt32>
            >();
            try {
                ProcessEntry32 entry = new ProcessEntry32();
                entry.Size = checked((UInt32)Marshal.SizeOf(typeof(ProcessEntry32)));
                if (Process32FirstW(snapshot, ref entry)) {
                    do {
                        System.Collections.Generic.List<UInt32> values;
                        if (!children.TryGetValue(entry.ParentProcessId, out values)) {
                            values = new System.Collections.Generic.List<UInt32>();
                            children.Add(entry.ParentProcessId, values);
                        }
                        values.Add(entry.ProcessId);
                        entry.Size = checked((UInt32)Marshal.SizeOf(typeof(ProcessEntry32)));
                    } while (Process32NextW(snapshot, ref entry));
                }
            } finally {
                CloseHandle(snapshot);
            }

            var retained = new System.Collections.Generic.List<IntPtr>();
            var pending = new System.Collections.Generic.Queue<UInt32>();
            var observed = new System.Collections.Generic.HashSet<UInt32>();
            foreach (UInt32 rootProcessId in rootProcessIds) {
                if (observed.Add(rootProcessId)) {
                    pending.Enqueue(rootProcessId);
                }
            }
            try {
                while (pending.Count != 0) {
                    UInt32 parent = pending.Dequeue();
                    System.Collections.Generic.List<UInt32> values;
                    if (!children.TryGetValue(parent, out values)) {
                        continue;
                    }
                    foreach (UInt32 processId in values) {
                        if (!observed.Add(processId)) {
                            continue;
                        }
                        pending.Enqueue(processId);
                        IntPtr process = OpenProcess(
                            ProcessTerminate | Synchronize, false, processId
                        );
                        if (process == IntPtr.Zero) {
                            Int32 error = Marshal.GetLastWin32Error();
                            if (error == ErrorInvalidParameter) {
                                continue;
                            }
                            throw new Win32Exception(error);
                        }
                        retained.Add(process);
                    }
                }
                var processIds = new UInt32[observed.Count];
                observed.CopyTo(processIds);
                return new DescendantProcessSet(retained, processIds);
            } catch {
                foreach (IntPtr process in retained) {
                    CloseHandle(process);
                }
                throw;
            }
        }

        public void TerminateAndWait(Int32 timeoutMilliseconds) {
            if (timeoutMilliseconds < 0) {
                throw new ArgumentOutOfRangeException("timeoutMilliseconds");
            }
            var clock = System.Diagnostics.Stopwatch.StartNew();
            foreach (IntPtr process in handles) {
                UInt32 state = WaitForSingleObject(process, 0);
                if (state == WaitObject0) {
                    continue;
                }
                if (state != WaitTimeout) {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
                if (!TerminateProcess(process, 1)) {
                    Int32 error = Marshal.GetLastWin32Error();
                    if (error != ErrorAccessDenied) {
                        throw new Win32Exception(error);
                    }
                }
            }
            foreach (IntPtr process in handles) {
                Int32 remaining = Math.Max(
                    0, timeoutMilliseconds - checked((Int32)clock.ElapsedMilliseconds)
                );
                if (WaitForSingleObject(process, checked((UInt32)remaining)) != WaitObject0) {
                    throw new TimeoutException("qualification descendant did not exit");
                }
            }
        }

        public void Dispose() {
            foreach (IntPtr process in handles) {
                CloseHandle(process);
            }
            handles.Clear();
            GC.SuppressFinalize(this);
        }
    }

    public sealed class KillOnCloseJob : IDisposable {
        private const UInt32 JobObjectLimitKillOnJobClose = 0x00002000;
        private const Int32 JobObjectExtendedLimitInformation = 9;
        private const UInt32 WaitObject0 = 0x00000000;
        private const UInt32 WaitTimeout = 0x00000102;
        private const UInt32 Infinite = 0xffffffff;
        private IntPtr handle;

        [StructLayout(LayoutKind.Sequential)]
        private struct IoCounters {
            public UInt64 ReadOperationCount;
            public UInt64 WriteOperationCount;
            public UInt64 OtherOperationCount;
            public UInt64 ReadTransferCount;
            public UInt64 WriteTransferCount;
            public UInt64 OtherTransferCount;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct BasicLimitInformation {
            public Int64 PerProcessUserTimeLimit;
            public Int64 PerJobUserTimeLimit;
            public UInt32 LimitFlags;
            public UIntPtr MinimumWorkingSetSize;
            public UIntPtr MaximumWorkingSetSize;
            public UInt32 ActiveProcessLimit;
            public UIntPtr Affinity;
            public UInt32 PriorityClass;
            public UInt32 SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct ExtendedLimitInformation {
            public BasicLimitInformation BasicLimitInformation;
            public IoCounters IoInfo;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemoryUsed;
            public UIntPtr PeakJobMemoryUsed;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateJobObjectW(IntPtr attributes, string name);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool SetInformationJobObject(
            IntPtr job,
            Int32 informationClass,
            IntPtr information,
            UInt32 informationLength
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool AssignProcessToJobObject(
            IntPtr job,
            IntPtr process
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool IsProcessInJob(
            IntPtr process,
            IntPtr job,
            [MarshalAs(UnmanagedType.Bool)] out bool result
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool TerminateJobObject(IntPtr job, UInt32 exitCode);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern UInt32 WaitForSingleObject(
            IntPtr process, UInt32 milliseconds
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CloseHandle(IntPtr handle);

        private KillOnCloseJob(IntPtr value) {
            handle = value;
        }

        public static KillOnCloseJob Create(string name) {
            if (String.IsNullOrWhiteSpace(name)) {
                throw new ArgumentException("job name is required", "name");
            }
            IntPtr job = CreateJobObjectW(IntPtr.Zero, name);
            if (job == IntPtr.Zero) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            IntPtr information = IntPtr.Zero;
            try {
                ExtendedLimitInformation limits = new ExtendedLimitInformation();
                limits.BasicLimitInformation.LimitFlags = JobObjectLimitKillOnJobClose;
                int length = Marshal.SizeOf(typeof(ExtendedLimitInformation));
                information = Marshal.AllocHGlobal(length);
                Marshal.StructureToPtr(limits, information, false);
                if (!SetInformationJobObject(
                    job,
                    JobObjectExtendedLimitInformation,
                    information,
                    checked((UInt32)length)
                )) {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
                return new KillOnCloseJob(job);
            } catch {
                CloseHandle(job);
                throw;
            } finally {
                if (information != IntPtr.Zero) {
                    Marshal.FreeHGlobal(information);
                }
            }
        }

        private void EnsureOpen() {
            if (handle == IntPtr.Zero) {
                throw new ObjectDisposedException("KillOnCloseJob");
            }
        }

        public void Assign(SafeProcessHandle process) {
            EnsureOpen();
            if (process == null || process.IsInvalid || process.IsClosed) {
                throw new ArgumentException("process handle is not open", "process");
            }
            bool retained = false;
            try {
                process.DangerousAddRef(ref retained);
                if (!AssignProcessToJobObject(handle, process.DangerousGetHandle())) {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
                bool assigned;
                if (!IsProcessInJob(process.DangerousGetHandle(), handle, out assigned)) {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
                if (!assigned) {
                    throw new InvalidOperationException(
                        "process assignment to qualification job was not effective"
                    );
                }
            } finally {
                if (retained) {
                    process.DangerousRelease();
                }
            }
        }

        public void Terminate(UInt32 exitCode) {
            EnsureOpen();
            if (!TerminateJobObject(handle, exitCode)) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
        }

        public static bool WaitForProcessExit(
            SafeProcessHandle process, Int32 milliseconds
        ) {
            if (process == null || process.IsInvalid || process.IsClosed) {
                throw new ArgumentException("process handle is not open", "process");
            }
            UInt32 timeout = milliseconds < 0 ? Infinite : checked((UInt32)milliseconds);
            bool retained = false;
            UInt32 result;
            try {
                process.DangerousAddRef(ref retained);
                result = WaitForSingleObject(process.DangerousGetHandle(), timeout);
            } finally {
                if (retained) {
                    process.DangerousRelease();
                }
            }
            if (result == WaitObject0) {
                return true;
            }
            if (result == WaitTimeout) {
                return false;
            }
            throw new Win32Exception(Marshal.GetLastWin32Error());
        }

        public void Dispose() {
            if (handle == IntPtr.Zero) {
                return;
            }
            IntPtr current = handle;
            handle = IntPtr.Zero;
            if (!CloseHandle(current)) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            GC.SuppressFinalize(this);
        }

        ~KillOnCloseJob() {
            if (handle != IntPtr.Zero) {
                CloseHandle(handle);
                handle = IntPtr.Zero;
            }
        }
    }
}
'@
}

function Invoke-GatedQualificationProcess(
    [Diagnostics.ProcessStartInfo]$Start, [int]$TimeoutMilliseconds
) {
    if ($TimeoutMilliseconds -le 0) {
        Fail "qualification process timeout must be positive"
    }
    $Gate = $null
    $CompletionGates = [Collections.Generic.List[Threading.EventWaitHandle]]::new()
    $HoldGate = $null
    $Process = $null
    $Job = $null
    $OutputTask = $null
    $ErrorTask = $null
    $PrimaryFailure = $null
    $GateReleased = $false
    $TimedOut = $false
    [int]$ReportedExitCode = -1
    $Output = ""
    $ErrorOutput = ""
    $ExitCode = -1
    [UInt32]$RootProcessId = 0
    $CapturedDescendants = $null
    [int]$CapturedDescendantCount = 0
    [int]$LateDescendantCount = 0
    $KnownProcessIds = [Collections.Generic.HashSet[UInt32]]::new()
    $CleanupFailures = [Collections.Generic.List[string]]::new()
    try {
        $GateName = "Local\RuishengQualificationGate-$([Guid]::NewGuid().ToString('N'))"
        try {
            $Gate = [Threading.EventWaitHandle]::new(
                $false, [Threading.EventResetMode]::ManualReset, $GateName
            )
        } catch {
            Fail "cannot create qualification release gate: $($_.Exception.Message)"
        }
        $Start.Environment["RUISHENG_QUALIFICATION_GATE"] = $GateName
        try {
            for ($Code = 0; $Code -le 3; $Code++) {
                $CompletionName = (
                    "Local\RuishengQualificationComplete${Code}-" +
                    [Guid]::NewGuid().ToString('N')
                )
                $CompletionGate = [Threading.EventWaitHandle]::new(
                    $false, [Threading.EventResetMode]::ManualReset, $CompletionName
                )
                [void]$CompletionGates.Add($CompletionGate)
                $Start.Environment["RUISHENG_QUALIFICATION_COMPLETE_${Code}"] = `
                    $CompletionName
            }
            $HoldName = "Local\RuishengQualificationHold-$([Guid]::NewGuid().ToString('N'))"
            $HoldGate = [Threading.EventWaitHandle]::new(
                $false, [Threading.EventResetMode]::ManualReset, $HoldName
            )
            $Start.Environment["RUISHENG_QUALIFICATION_HOLD"] = $HoldName
        } catch {
            Fail "cannot create qualification completion gates: $($_.Exception.Message)"
        }
        try {
            $JobName = "RuishengQualificationJob-$([Guid]::NewGuid().ToString('N'))"
            $Job = [Ruisheng.ReleaseTrust.KillOnCloseJob]::Create($JobName)
        } catch {
            Fail "cannot create qualification kill-on-close job: $($_.Exception.Message)"
        }
        try {
            $Process = [Diagnostics.Process]::Start($Start)
        } catch {
            Fail "cannot start gated qualification process: $($_.Exception.Message)"
        }
        if ($null -eq $Process) { Fail "cannot start gated qualification process" }
        $RootProcessId = [UInt32]$Process.Id
        $OutputTask = $Process.StandardOutput.ReadToEndAsync()
        $ErrorTask = $Process.StandardError.ReadToEndAsync()
        try {
            $Job.Assign($Process.SafeHandle)
        } catch {
            Fail "cannot assign qualification process to kill-on-close job: $($_.Exception.Message)"
        }
        try {
            if (-not $Gate.Set()) { Fail "cannot release qualification process gate" }
            $GateReleased = $true
        } catch {
            if ($_.Exception.Message -like "*qualification process gate*") { throw }
            Fail "cannot release qualification process gate: $($_.Exception.Message)"
        }
        $QualificationClock = [Diagnostics.Stopwatch]::StartNew()
        while ($ReportedExitCode -lt 0) {
            $Remaining = [Math]::Max(
                0, $TimeoutMilliseconds - [int]$QualificationClock.ElapsedMilliseconds
            )
            if ($Remaining -eq 0) {
                $TimedOut = $true
                break
            }
            $WaitSlice = [Math]::Min(100, $Remaining)
            $CompletionIndex = [Threading.WaitHandle]::WaitAny(
                [Threading.WaitHandle[]]$CompletionGates.ToArray(), $WaitSlice
            )
            if ($CompletionIndex -ne [Threading.WaitHandle]::WaitTimeout) {
                $ReportedExitCode = $CompletionIndex
                break
            }
            if ([Ruisheng.ReleaseTrust.KillOnCloseJob]::WaitForProcessExit(
                $Process.SafeHandle, 0
            )) {
                $ReportedExitCode = $Process.ExitCode
                break
            }
        }
    } catch {
        $PrimaryFailure = $_
    } finally {
        $CleanupClock = [Diagnostics.Stopwatch]::StartNew()
        if ($null -ne $Process) {
            [void]$KnownProcessIds.Add([UInt32]$Process.Id)
            try {
                $CapturedDescendants = [Ruisheng.ReleaseTrust.DescendantProcessSet]::Capture(
                    [UInt32]$Process.Id
                )
                $CapturedDescendantCount = $CapturedDescendants.Count
                foreach ($ProcessId in $CapturedDescendants.ObservedProcessIds) {
                    [void]$KnownProcessIds.Add([UInt32]$ProcessId)
                }
            } catch {
                [void]$CleanupFailures.Add(
                    "cannot capture qualification descendants: $($_.Exception.Message)"
                )
            }
        }
        if ($null -ne $Job) {
            try {
                $Job.Terminate(1)
            } catch {
                [void]$CleanupFailures.Add(
                    "cannot terminate qualification job: $($_.Exception.Message)"
                )
            } finally {
                try {
                    $Job.Dispose()
                } catch {
                    [void]$CleanupFailures.Add(
                        "cannot close qualification job: $($_.Exception.Message)"
                    )
                }
            }
        }
        if ($null -ne $CapturedDescendants) {
            try {
                $Remaining = [Math]::Max(0, 30000 - [int]$CleanupClock.ElapsedMilliseconds)
                $CapturedDescendants.TerminateAndWait($Remaining)
            } catch {
                [void]$CleanupFailures.Add(
                    "cannot terminate captured qualification descendants: " +
                    $_.Exception.Message
                )
            } finally {
                $CapturedDescendants.Dispose()
                $CapturedDescendants = $null
            }
        }
        if ($null -ne $Process) {
            while ($true) {
                $LateDescendants = $null
                try {
                    $LateDescendants = [Ruisheng.ReleaseTrust.DescendantProcessSet]::Capture(
                        [UInt32[]]@($KnownProcessIds)
                    )
                    foreach ($ProcessId in $LateDescendants.ObservedProcessIds) {
                        [void]$KnownProcessIds.Add([UInt32]$ProcessId)
                    }
                    if ($LateDescendants.Count -eq 0) { break }
                    $LateDescendantCount += $LateDescendants.Count
                    $Remaining = [Math]::Max(
                        0, 30000 - [int]$CleanupClock.ElapsedMilliseconds
                    )
                    if ($Remaining -eq 0) {
                        throw "qualification descendant cleanup deadline expired"
                    }
                    $LateDescendants.TerminateAndWait($Remaining)
                } catch {
                    [void]$CleanupFailures.Add(
                        "cannot terminate late qualification descendants: " +
                        $_.Exception.Message
                    )
                    break
                } finally {
                    if ($null -ne $LateDescendants) { $LateDescendants.Dispose() }
                }
            }
        }
        if ($null -ne $Process) {
            try {
                $Remaining = [Math]::Max(0, 30000 - [int]$CleanupClock.ElapsedMilliseconds)
                if ($Remaining -eq 0 -or -not (
                    [Ruisheng.ReleaseTrust.KillOnCloseJob]::WaitForProcessExit(
                        $Process.SafeHandle, $Remaining
                    )
                )) {
                    [void]$CleanupFailures.Add(
                        "qualification process did not exit within the cleanup deadline"
                    )
                    try { $Process.Kill() } catch {}
                } else {
                    $ExitCode = if ($ReportedExitCode -ge 0) {
                        $ReportedExitCode
                    } else {
                        $Process.ExitCode
                    }
                }
            } catch {
                [void]$CleanupFailures.Add(
                    "cannot wait for qualification process cleanup: $($_.Exception.Message)"
                )
            }
            if ($null -ne $OutputTask -and $null -ne $ErrorTask) {
                try {
                    $OutputTasks = [Threading.Tasks.Task[]]@($OutputTask, $ErrorTask)
                    $OutputCompletion = [Threading.Tasks.Task]::WhenAll($OutputTasks)
                    if (-not $OutputCompletion.IsCompleted) {
                        $Remaining = [Math]::Max(
                            0, 30000 - [int]$CleanupClock.ElapsedMilliseconds
                        )
                        if ($Remaining -gt 0) { [void]$OutputCompletion.Wait($Remaining) }
                    }
                    if (-not $OutputCompletion.IsCompleted) {
                        [void]$CleanupFailures.Add(
                            "qualification output did not drain within the cleanup deadline"
                        )
                        $Process.StandardOutput.Close()
                        $Process.StandardError.Close()
                    } else {
                        $Output = $OutputTask.GetAwaiter().GetResult()
                        $ErrorOutput = $ErrorTask.GetAwaiter().GetResult()
                    }
                } catch {
                    [void]$CleanupFailures.Add(
                        "cannot collect qualification output: $($_.Exception.Message)"
                    )
                }
            }
            $Process.Dispose()
        }
        if ($null -ne $Gate) { $Gate.Dispose() }
        foreach ($CompletionGate in $CompletionGates) { $CompletionGate.Dispose() }
        if ($null -ne $HoldGate) { $HoldGate.Dispose() }
    }
    if ($null -ne $PrimaryFailure) {
        if ($CleanupFailures.Count -ne 0) {
            Fail "$($PrimaryFailure.Exception.Message); cleanup failed: $($CleanupFailures -join '; ')"
        }
        throw $PrimaryFailure
    }
    if (-not $GateReleased) { Fail "qualification process gate was not released" }
    if ($CleanupFailures.Count -ne 0) {
        Fail ($CleanupFailures -join "; ")
    }
    if ($TimedOut) { Fail "qualification tool timed out" }
    return [pscustomobject]@{
        ExitCode = $ExitCode
        StandardOutput = $Output
        StandardError = $ErrorOutput
        CapturedDescendantCount = $CapturedDescendantCount
        LateDescendantCount = $LateDescendantCount
        RootProcessId = $RootProcessId
    }
}
# END qualification process containment helpers

function Open-LockedFreshnessFile(
    [string]$Path,
    [string]$Label,
    [Int64]$MaximumBytes,
    [switch]$RequireProtected
) {
    if ($RequireProtected) {
        Assert-ProtectedAcl $Path $Label -AllowTrustedInstaller
        Assert-ProtectedAncestors $Path $Label -AllowTrustedInstaller
    }
    $Stream = $null
    try {
        $Item = Get-Item -Force -LiteralPath $Path -ErrorAction Stop
        if ($Item.PSIsContainer -or
            ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Fail "$Label is missing, linked, or not a file"
        }
        $Stream = [IO.File]::Open(
            $Item.FullName,
            [IO.FileMode]::Open,
            [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
        $Identity = Get-OpenFileIdentity $Stream $Item.FullName $Label
        if ($Identity.NumberOfLinks -ne 1) { Fail "$Label has multiple hard links" }
        if ($Identity.Length -lt 0 -or $Identity.Length -gt $MaximumBytes) {
            Fail "$Label exceeds its byte limit"
        }
        $Digest = Get-LockedFileSha256 $Stream
        $Identity.Sha256 = $Digest
        $Result = [pscustomobject]@{
            Path = $Item.FullName
            Label = $Label
            Stream = $Stream
            Identity = $Identity
            ExpectedSha256 = $Digest
            MaximumBytes = $MaximumBytes
            Protected = [bool]$RequireProtected
        }
        $Stream = $null
        return $Result
    } finally {
        if ($null -ne $Stream) { $Stream.Dispose() }
    }
}

function Copy-LockedFreshnessSnapshot(
    [object]$Source,
    [string]$DestinationPath,
    [string]$Relative,
    [Collections.Generic.List[object]]$Locks
) {
    Copy-CandidateFileToSnapshot `
        $Source.Path $DestinationPath $Relative $Source.Identity
    $Snapshot = Open-LockedFreshnessFile `
        $DestinationPath "$($Source.Label) snapshot" $Source.MaximumBytes
    if ($Snapshot.ExpectedSha256 -cne $Source.ExpectedSha256) {
        $Snapshot.Stream.Dispose()
        Fail "$($Source.Label) snapshot content mismatch"
    }
    $Locks.Add($Snapshot)
    return $Snapshot
}

function Assert-FreshnessLocksUnchanged([object]$Context) {
    foreach ($Lock in $Context.Locks) {
        $After = Get-OpenFileIdentity $Lock.Stream $Lock.Path $Lock.Label
        Assert-SameFileIdentity $Lock.Identity $After "$($Lock.Label) during freshness validation"
        if ((Get-LockedFileSha256 $Lock.Stream) -cne $Lock.ExpectedSha256) {
            Fail "$($Lock.Label) content changed during freshness validation"
        }
        if ($Lock.Protected) {
            Assert-ProtectedAcl $Lock.Path $Lock.Label -AllowTrustedInstaller
            Assert-ProtectedAncestors $Lock.Path $Lock.Label -AllowTrustedInstaller
        }
    }
}

function Close-FreshnessLocks([object]$Context) {
    if ($null -eq $Context) { return }
    foreach ($Lock in @($Context.Locks)) {
        if ($null -ne $Lock.Stream) { $Lock.Stream.Dispose() }
    }
    $Context.Locks.Clear()
}

function New-FreshnessChallenge() {
    $Bytes = [byte[]]::new(32)
    [Security.Cryptography.RandomNumberGenerator]::Fill($Bytes)
    return [Convert]::ToBase64String($Bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Get-CanonicalUtcTimestamp(
    [DateTimeOffset]$Now = [DateTimeOffset]::UtcNow
) {
    [Int64]$Microseconds = [Math]::Floor(
        [decimal]($Now.Ticks % [TimeSpan]::TicksPerSecond) / 10
    )
    $Prefix = $Now.ToString("yyyy-MM-dd'T'HH:mm:ss", [Globalization.CultureInfo]::InvariantCulture)
    if ($Microseconds -eq 0) { return "${Prefix}+00:00" }
    return "${Prefix}.$($Microseconds.ToString('D6'))+00:00"
}

function Invoke-FixedFreshnessProvider([string[]]$Arguments) {
    $ProviderBootstrap = @'
$ErrorActionPreference = "Stop"
$GateName = $env:RUISHENG_QUALIFICATION_GATE
$CompletionNames = @(0..3 | ForEach-Object {
    [Environment]::GetEnvironmentVariable("RUISHENG_QUALIFICATION_COMPLETE_$_")
})
$HoldName = $env:RUISHENG_QUALIFICATION_HOLD
if ([string]::IsNullOrWhiteSpace($GateName) -or
    @($CompletionNames | Where-Object { [string]::IsNullOrWhiteSpace($_) }).Count -ne 0 -or
    [string]::IsNullOrWhiteSpace($HoldName)) {
    exit 1
}
$Gate = [Threading.EventWaitHandle]::OpenExisting($GateName)
try {
    if (-not $Gate.WaitOne(120000)) { exit 1 }
} finally { $Gate.Dispose() }
$Provider = $args[0]
[string[]]$ProviderArguments = @($args | Select-Object -Skip 1)
$ExitCode = 1
try {
    & $Provider @ProviderArguments *> $null
    if ($LASTEXITCODE -in @(0, 2, 3)) { $ExitCode = [int]$LASTEXITCODE }
} catch { $ExitCode = 1 }
$Completion = [Threading.EventWaitHandle]::OpenExisting($CompletionNames[$ExitCode])
$Hold = [Threading.EventWaitHandle]::OpenExisting($HoldName)
try {
    if (-not $Completion.Set()) { exit 1 }
    [void]$Hold.WaitOne()
} finally {
    $Completion.Dispose()
    $Hold.Dispose()
}
exit $ExitCode
'@
    try {
        $PowerShellPath = [Environment]::ProcessPath
        if ([string]::IsNullOrWhiteSpace($PowerShellPath)) {
            return 2
        }
        Assert-ProtectedAcl $PowerShellPath "publisher PowerShell runtime" -AllowTrustedInstaller
        Assert-ProtectedAncestors $PowerShellPath `
            "publisher PowerShell runtime" -AllowTrustedInstaller
        $Start = [Diagnostics.ProcessStartInfo]::new()
        $Start.FileName = $PowerShellPath
        $Start.WorkingDirectory = [IO.Path]::GetDirectoryName($Arguments[-1])
        $Start.UseShellExecute = $false
        $Start.RedirectStandardOutput = $true
        $Start.RedirectStandardError = $true
        foreach ($Argument in @(
            "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", $ProviderBootstrap,
            $FreshnessProviderPath
        ) + $Arguments) {
            [void]$Start.ArgumentList.Add($Argument)
        }
        $Start.Environment.Clear()
        $SystemDirectory = [Environment]::SystemDirectory
        $WindowsDirectory = [IO.Directory]::GetParent($SystemDirectory).FullName
        $Start.Environment["COMSPEC"] = Join-Path $SystemDirectory "cmd.exe"
        $Start.Environment["PATH"] = $SystemDirectory
        $Start.Environment["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
        $Start.Environment["SYSTEMROOT"] = $WindowsDirectory
        $Start.Environment["WINDIR"] = $WindowsDirectory
        $Result = Invoke-GatedQualificationProcess `
            $Start $FreshnessProviderTimeoutMilliseconds
        if ($Result.ExitCode -in @(0, 2, 3)) { return [int]$Result.ExitCode }
        return 2
    } catch {
        return 2
    }
}

function New-PublisherFreshnessContext(
    [object]$AuthenticatedManifest, [string]$FreshnessRoot
) {
    $Locks = [Collections.Generic.List[object]]::new()
    $Context = [pscustomobject]@{ Locks = $Locks }
    if (-not (Test-Path -LiteralPath $FreshnessProviderPath -PathType Leaf)) {
        return [pscustomobject]@{ ExitCode = 2; Context = $Context }
    }
    if (-not (Test-Path -LiteralPath $FreshnessProviderConfigPath -PathType Leaf)) {
        return [pscustomobject]@{ ExitCode = 2; Context = $Context }
    }
    if (-not (Test-Path -LiteralPath $FreshnessTrustRootPath -PathType Leaf)) {
        return [pscustomobject]@{ ExitCode = 3; Context = $Context }
    }
    try {
        [void](New-Item -ItemType Directory -Path $FreshnessRoot)
        Set-ProtectedSnapshotAcl $FreshnessRoot
        Assert-ProtectedAcl $FreshnessRoot "freshness snapshot directory"
        Assert-ProtectedAncestors $FreshnessRoot `
            "freshness snapshot directory" -AllowTrustedInstaller

        try {
            $Provider = Open-LockedFreshnessFile `
                $FreshnessProviderPath "fixed freshness provider" `
                $MaxFreshnessProviderBytes -RequireProtected
        } catch {
            return [pscustomobject]@{ ExitCode = 2; Context = $Context }
        }
        $Locks.Add($Provider)
        $Verifier = Open-LockedFreshnessFile `
            $PSCommandPath "protected publisher verifier" 64MB -RequireProtected
        $Locks.Add($Verifier)
        $TrustRoot = Open-LockedFreshnessFile `
            $FreshnessTrustRootPath "fixed point-profile trust root" `
            $MaxReleaseJsonBytes -RequireProtected
        $Locks.Add($TrustRoot)
        $Config = Open-LockedFreshnessFile `
            $FreshnessProviderConfigPath "fixed freshness provider config" `
            $MaxReleaseJsonBytes -RequireProtected
        $Locks.Add($Config)
        $Profile = Open-LockedFreshnessFile `
            ([IO.Path]::GetFullPath($QualificationProfilePath)) `
            "qualification profile" $MaxReleaseJsonBytes
        $Locks.Add($Profile)
        $Policy = Open-LockedFreshnessFile `
            ([IO.Path]::GetFullPath($QualificationTrustPolicyPath)) `
            "qualification trust policy" $MaxReleaseJsonBytes
        $Locks.Add($Policy)

        $TrustRootSnapshot = Copy-LockedFreshnessSnapshot `
            $TrustRoot (Join-Path $FreshnessRoot "trust-root.json") `
            "freshness/trust-root.json" $Locks
        $ConfigSnapshot = Copy-LockedFreshnessSnapshot `
            $Config (Join-Path $FreshnessRoot "provider-config.json") `
            "freshness/provider-config.json" $Locks
        $ProfileSnapshot = Copy-LockedFreshnessSnapshot `
            $Profile (Join-Path $FreshnessRoot "profile.json") `
            "freshness/profile.json" $Locks
        $PolicySnapshot = Copy-LockedFreshnessSnapshot `
            $Policy (Join-Path $FreshnessRoot "trust-policy.json") `
            "freshness/trust-policy.json" $Locks

        $ProfileBytes = Read-LockedFileBytes `
            $ProfileSnapshot.Stream $MaxReleaseJsonBytes "qualification profile snapshot"
        Assert-NoDuplicateJsonKeys $ProfileBytes "qualification profile snapshot"
        $ProfileValue = [Text.UTF8Encoding]::new($false, $true).GetString(
            $ProfileBytes
        ) | ConvertFrom-Json
        if ($ProfileValue.PSObject.Properties.Name -cnotcontains "profile_id" -or
            $ProfileValue.profile_id -isnot [string] -or
            $ProfileValue.PSObject.Properties.Name -cnotcontains "payload_sha256" -or
            $ProfileValue.payload_sha256 -isnot [string]) {
            Fail "qualification profile binding is invalid"
        }

        $Challenge = New-FreshnessChallenge
        if ($Challenge.Length -ne 43 -or $Challenge.Contains("=")) {
            Fail "freshness challenge generation failed"
        }
        $RequestedAt = Get-CanonicalUtcTimestamp
        $AttestationPath = Join-Path $FreshnessRoot "attestation.json"
        [string[]]$ProviderArguments = @(
            "attest",
            "--config", $ConfigSnapshot.Path,
            "--trust-root", $TrustRootSnapshot.Path,
            "--trust-policy", $PolicySnapshot.Path,
            "--profile", $ProfileSnapshot.Path,
            "--candidate-logical-identity", [string]$AuthenticatedManifest.logical_identity,
            "--verifier-id", $FreshnessVerifierId,
            "--verifier-tool-sha256", "sha256:$($Verifier.ExpectedSha256)",
            "--challenge", $Challenge,
            "--requested-at", $RequestedAt,
            "--output", $AttestationPath
        )
        Assert-FreshnessLocksUnchanged $Context
        $ProviderExitCode = Invoke-FixedFreshnessProvider $ProviderArguments
        if ($ProviderExitCode -ne 0) {
            return [pscustomobject]@{
                ExitCode = $(if ($ProviderExitCode -eq 3) { 3 } else { 2 })
                Context = $Context
            }
        }
        $Attestation = Open-LockedFreshnessFile `
            $AttestationPath "freshness attestation" $MaxReleaseJsonBytes
        $Locks.Add($Attestation)
        $AttestationBytes = Read-LockedFileBytes `
            $Attestation.Stream $MaxReleaseJsonBytes "freshness attestation"
        Assert-NoDuplicateJsonKeys $AttestationBytes "freshness attestation"
        $AttestationValue = [Text.UTF8Encoding]::new($false, $true).GetString(
            $AttestationBytes
        ) | ConvertFrom-Json
        if ($null -eq $AttestationValue.request -or
            $AttestationValue.request.challenge -cne $Challenge -or
            $AttestationValue.request.candidate_logical_identity -cne `
                [string]$AuthenticatedManifest.logical_identity -or
            $AttestationValue.request.profile_id -cne [string]$ProfileValue.profile_id -or
            $AttestationValue.request.payload_sha256 -cne `
                [string]$ProfileValue.payload_sha256 -or
            $AttestationValue.request.verifier_id -cne $FreshnessVerifierId -or
            $AttestationValue.request.verifier_tool_sha256 -cne `
                "sha256:$($Verifier.ExpectedSha256)") {
            Fail "freshness attestation request binding is invalid"
        }
        $Context | Add-Member -NotePropertyName Provider -NotePropertyValue $Provider
        $Context | Add-Member -NotePropertyName Verifier -NotePropertyValue $Verifier
        $Context | Add-Member -NotePropertyName TrustRootSnapshot `
            -NotePropertyValue $TrustRootSnapshot
        $Context | Add-Member -NotePropertyName ConfigSnapshot `
            -NotePropertyValue $ConfigSnapshot
        $Context | Add-Member -NotePropertyName ProfileSnapshot `
            -NotePropertyValue $ProfileSnapshot
        $Context | Add-Member -NotePropertyName PolicySnapshot `
            -NotePropertyValue $PolicySnapshot
        $Context | Add-Member -NotePropertyName Attestation -NotePropertyValue $Attestation
        $Context | Add-Member -NotePropertyName Challenge -NotePropertyValue $Challenge
        $Context | Add-Member -NotePropertyName RequestedAt -NotePropertyValue $RequestedAt
        return [pscustomobject]@{ ExitCode = 0; Context = $Context }
    } catch {
        return [pscustomobject]@{ ExitCode = 3; Context = $Context }
    }
}

function Invoke-AuthenticatedQualification(
    [string]$ExtractionRoot,
    [hashtable]$AuthenticatedContents,
    [object]$Invocation,
    [object]$FreshnessContext = $null
) {
    $ExpectedMembers = @(
        "tools/validate_device_point_profile.py",
        "tools/trust_root_freshness.py",
        "schemas/point-profile/point-profile-v1.schema.json",
        "tools/release_artifacts.py",
        "tools/release_verification_receipt.py",
        "pyproject.toml",
        "uv.lock",
        "qualification-toolchain-manifest.json"
    )
    $EntrypointPath = Join-Path $ExtractionRoot $Invocation.Entrypoint
    $Runtime = $null
    $Locks = [Collections.Generic.List[object]]::new()
    try {
        $AuthenticatedUvLockSha256 = Get-QualificationSha256 (
            [byte[]]$AuthenticatedContents["uv.lock"]
        )
        $Runtime = Open-ProtectedSystemPython $AuthenticatedUvLockSha256
        foreach ($Relative in $ExpectedMembers) {
            $Path = Join-Path $ExtractionRoot $Relative
            $Stream = $null
            try {
                $Stream = [IO.File]::Open(
                    $Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
                )
                $Identity = Get-OpenFileIdentity $Stream $Path "extracted qualification member ${Relative}"
                $Hasher = [Security.Cryptography.SHA256]::Create()
                try {
                    $ActualDigest = ([BitConverter]::ToString(
                        $Hasher.ComputeHash($Stream)
                    )).Replace("-", "").ToLowerInvariant()
                } finally { $Hasher.Dispose() }
                $Stream.Position = 0
                $ExpectedDigest = Get-QualificationSha256 (
                    [byte[]]$AuthenticatedContents[$Relative]
                )
                if ($ActualDigest -cne $ExpectedDigest) {
                    Fail "extracted qualification member SHA-256 mismatch: $Relative"
                }
                $Locks.Add([pscustomobject]@{
                    Path = $Path; Relative = $Relative; Stream = $Stream; Identity = $Identity
                })
                $Stream = $null
            } finally {
                if ($null -ne $Stream) { $Stream.Dispose() }
            }
        }
        $TemporaryRoot = Join-Path $ExtractionRoot "tmp"
        [void](New-Item -ItemType Directory -Path $TemporaryRoot)
        Set-ProtectedSnapshotAcl $TemporaryRoot
        $Bootstrap = @'
import ctypes
from ctypes import wintypes
import os
import sys

gate_name = os.environ.pop("RUISHENG_QUALIFICATION_GATE", "")
completion_names = [
    os.environ.pop("RUISHENG_QUALIFICATION_COMPLETE_{}".format(code), "")
    for code in range(4)
]
hold_name = os.environ.pop("RUISHENG_QUALIFICATION_HOLD", "")
if not gate_name or not all(completion_names) or not hold_name:
    raise SystemExit("qualification release gate is missing")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
open_event = kernel32.OpenEventW
open_event.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
open_event.restype = wintypes.HANDLE
wait_for_single_object = kernel32.WaitForSingleObject
wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
wait_for_single_object.restype = wintypes.DWORD
close_handle = kernel32.CloseHandle
close_handle.argtypes = (wintypes.HANDLE,)
close_handle.restype = wintypes.BOOL
set_event = kernel32.SetEvent
set_event.argtypes = (wintypes.HANDLE,)
set_event.restype = wintypes.BOOL
gate = open_event(0x00100000, False, gate_name)
if not gate:
    raise SystemExit("qualification release gate is unavailable")
try:
    gate_result = wait_for_single_object(gate, 120000)
finally:
    gate_closed = close_handle(gate)
if not gate_closed or gate_result != 0:
    raise SystemExit("qualification release gate did not open")

runtime = os.path.normcase(os.path.realpath(sys.argv.pop(1)))
dependency_root = os.path.normcase(os.path.realpath(sys.argv.pop(1)))
root = os.path.normcase(os.path.realpath(sys.argv.pop(1)))
script = os.path.normcase(os.path.realpath(sys.argv.pop(1)))
if sys.version_info[:2] != (3, 11):
    raise SystemExit("qualification runtime must be Python 3.11")
if not sys.flags.isolated or not sys.flags.no_site or not sys.flags.dont_write_bytecode:
    raise SystemExit("qualification runtime isolation flags are incomplete")
if "site" in sys.modules or "sitecustomize" in sys.modules or "usercustomize" in sys.modules:
    raise SystemExit("qualification runtime imported site before bootstrap")

def inside(path, parent):
    return path == parent or path.startswith(parent + os.sep)

executable = os.path.normcase(os.path.realpath(sys.executable))
if executable != os.path.join(runtime, "python.exe"):
    raise SystemExit("qualification executable escaped the fixed runtime")
for prefix in (sys.prefix, sys.exec_prefix, sys.base_prefix, sys.base_exec_prefix):
    if os.path.normcase(os.path.realpath(prefix)) != runtime:
        raise SystemExit("qualification Python prefix escaped the fixed runtime")
for startup_path in sys.path:
    if not startup_path:
        raise SystemExit("qualification runtime inherited an empty search path")
    resolved = os.path.normcase(os.path.realpath(startup_path))
    if not inside(resolved, runtime):
        raise SystemExit("qualification startup search path escaped the fixed runtime")
if not inside(dependency_root, runtime) or dependency_root in {
    os.path.normcase(os.path.realpath(value)) for value in sys.path
}:
    raise SystemExit("qualification dependency_root was not isolated for bootstrap")
if not inside(script, root):
    raise SystemExit("unsupported qualification entrypoint")

sys.modules["site"] = None
sys.modules["sitecustomize"] = None
sys.modules["usercustomize"] = None
sys.path.insert(0, dependency_root)
sys.path.insert(0, root)

import pathlib
import runpy
import types

root_path = pathlib.Path(root).resolve(strict=True)
script_path = pathlib.Path(script).resolve(strict=True)
allowed = {
    (root_path / "tools" / "validate_device_point_profile.py").resolve(strict=True),
    (root_path / "tools" / "trust_root_freshness.py").resolve(strict=True),
    (root_path / "tools" / "release_verification_receipt.py").resolve(strict=True),
}
if script_path not in allowed or root_path not in script_path.parents:
    raise SystemExit("unsupported qualification entrypoint")
package = types.ModuleType("tools")
package.__path__ = [str(root_path / "tools")]
sys.modules["tools"] = package
sys.argv = [str(script_path), *sys.argv[1:]]

exit_code = 0
try:
    runpy.run_path(str(script_path), run_name="__main__")
except SystemExit as error:
    if error.code is None:
        exit_code = 0
    elif type(error.code) is int and 0 <= error.code <= 3:
        exit_code = error.code
    else:
        if error.code:
            print(error.code, file=sys.stderr)
        exit_code = 1
except BaseException:
    import traceback
    traceback.print_exc()
    exit_code = 1

sys.stdout.flush()
sys.stderr.flush()
completion = open_event(0x0002, False, completion_names[exit_code])
hold = open_event(0x00100000, False, hold_name)
if not completion or not hold:
    raise SystemExit("qualification completion gates are unavailable")
if not set_event(completion):
    raise SystemExit("qualification completion gate cannot be signaled")
close_handle(completion)
wait_for_single_object(hold, 0xffffffff)
close_handle(hold)
raise SystemExit(exit_code)
'@
        $Start = [Diagnostics.ProcessStartInfo]::new()
        $Start.FileName = $Runtime.Python.Path
        $Start.WorkingDirectory = $ExtractionRoot
        $Start.UseShellExecute = $false
        $Start.RedirectStandardOutput = $true
        $Start.RedirectStandardError = $true
        foreach ($Argument in @(
            "-I", "-B", "-S", "-X", "utf8", "-c", $Bootstrap,
            $Runtime.Root, $Runtime.DependencyRoot, $ExtractionRoot, $EntrypointPath
        ) + [string[]]$Invocation.Arguments) {
            [void]$Start.ArgumentList.Add($Argument)
        }
        $Start.Environment.Clear()
        $SystemDirectory = [Environment]::SystemDirectory
        $WindowsDirectory = [IO.Directory]::GetParent($SystemDirectory).FullName
        $Start.Environment["COMSPEC"] = Join-Path $SystemDirectory "cmd.exe"
        $Start.Environment["PATH"] = $SystemDirectory
        $Start.Environment["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
        $Start.Environment["SYSTEMROOT"] = $WindowsDirectory
        $Start.Environment["WINDIR"] = $WindowsDirectory
        $Start.Environment["PYTHONDONTWRITEBYTECODE"] = "1"
        $Start.Environment["PYTHONNOUSERSITE"] = "1"
        $Start.Environment["TEMP"] = $TemporaryRoot
        $Start.Environment["TMP"] = $TemporaryRoot
        $ProcessResult = Invoke-GatedQualificationProcess $Start 900000

        Assert-ProtectedQualificationRuntimeUnchanged $Runtime
        foreach ($Lock in $Locks) {
            $After = Get-OpenFileIdentity (
                $Lock.Stream
            ) $Lock.Path "extracted qualification member $($Lock.Relative)"
            Assert-SameFileIdentity $Lock.Identity $After (
                "extracted qualification member $($Lock.Relative) during execution"
            )
        }
        if ($null -ne $FreshnessContext) {
            Assert-FreshnessLocksUnchanged $FreshnessContext
        }
        return [pscustomobject]@{
            ExitCode = $ProcessResult.ExitCode
            StandardOutput = $ProcessResult.StandardOutput
            StandardError = $ProcessResult.StandardError
        }
    } finally {
        foreach ($Lock in $Locks) {
            if ($null -ne $Lock.Stream) { $Lock.Stream.Dispose() }
        }
        if ($null -ne $Runtime) {
            foreach ($RuntimeLock in $Runtime.Locks) {
                if ($null -ne $RuntimeLock.Stream) { $RuntimeLock.Stream.Dispose() }
            }
        }
    }
}

# Test-QualificationToolchain $Manifest marks the isolated logical-identity helper boundary.
$QualificationContents = Test-QualificationToolchain $Manifest $Sums $PackageRoot $ExpectedSchemaVersion
Assert-ManifestValueTypes $Manifest
if ($Manifest.logical_identity -cne (Get-ManifestLogicalIdentity $Manifest $ExpectedSchemaVersion)) {
    Fail "manifest logical_identity does not match its immutable inputs"
}
Write-Host "[publisher] VERIFIED: publisher signature and complete candidate hashes passed"
if ($QualificationMode -ne "None") {
    if ($ExpectedSchemaVersion -ne 3 -or $null -eq $QualificationContents) {
        Fail "qualification mode requires an authenticated v3 qualification toolchain"
    }
    $QualificationExtractionRoot = New-ProtectedSnapshotRoot "qualification-extracted-"
    Write-AuthenticatedQualificationToolchain $QualificationContents $QualificationExtractionRoot
    if ($QualificationMode -eq "ValidatorProfile") {
        $FreshnessResult = New-PublisherFreshnessContext `
            $Manifest (Join-Path $QualificationExtractionRoot "freshness")
        $FreshnessContext = $FreshnessResult.Context
        if ($FreshnessResult.ExitCode -ne 0) {
            exit $FreshnessResult.ExitCode
        }
        $PreflightInvocation = Get-FreshnessPreflightInvocation `
            $Manifest $FreshnessContext
        $PreflightResult = Invoke-AuthenticatedQualification (
            $QualificationExtractionRoot
        ) $QualificationContents $PreflightInvocation $FreshnessContext
        try {
            $PreflightReport = $PreflightResult.StandardOutput | ConvertFrom-Json
        } catch {
            exit 3
        }
        $ExpectedDecision = switch ($PreflightResult.ExitCode) {
            0 { "EXACT" }
            2 { "BLOCKED" }
            3 { "INVALID" }
            default { "" }
        }
        if (@($PreflightReport.PSObject.Properties).Count -ne 2 -or
            $PreflightReport.PSObject.Properties.Name -cnotcontains "decision" -or
            $PreflightReport.PSObject.Properties.Name -cnotcontains "reason_code" -or
            $PreflightReport.decision -cne $ExpectedDecision) {
            exit 3
        }
        if ($PreflightResult.ExitCode -ne 0) {
            exit $PreflightResult.ExitCode
        }
        Assert-FreshnessLocksUnchanged $FreshnessContext
    }
    $Invocation = Get-QualificationInvocation `
        $QualificationMode $Manifest $PackageRoot $FreshnessContext
    $QualificationResult = Invoke-AuthenticatedQualification (
        $QualificationExtractionRoot
    ) $QualificationContents $Invocation $FreshnessContext
    if (-not [string]::IsNullOrEmpty($QualificationResult.StandardOutput)) {
        [Console]::Out.Write($QualificationResult.StandardOutput)
    }
    if (-not [string]::IsNullOrEmpty($QualificationResult.StandardError)) {
        [Console]::Error.Write($QualificationResult.StandardError)
    }
    exit $QualificationResult.ExitCode
}
$CandidateVerifier = Join-Path $PackageRoot "verify-candidate.ps1"
if ([string]::IsNullOrWhiteSpace($SiteEnvPath)) {
    & $CandidateVerifier $PackageRoot
} else {
    & $CandidateVerifier $PackageRoot $SiteEnvPath
}
$CandidateExitCode = $LASTEXITCODE
if ($CandidateExitCode -notin @(0, 2)) { exit $CandidateExitCode }
if ($InstallSerialTools) {
    Install-AuthenticatedSerialTools $PackageRoot $Sums $Manifest
}
exit $CandidateExitCode
} finally {
    Close-FreshnessLocks $FreshnessContext
    if ($null -ne $QualificationExtractionRoot -and
        (Test-Path -LiteralPath $QualificationExtractionRoot)) {
        try {
            Remove-Item -LiteralPath $QualificationExtractionRoot -Recurse -Force -ErrorAction Stop
        } catch {
            Write-Error "[publisher] protected work cleanup failed: ${QualificationExtractionRoot}: $($_.Exception.Message)"
            throw
        }
    }
    if (Test-Path -LiteralPath $SnapshotRoot) {
        try {
            Remove-Item -LiteralPath $SnapshotRoot -Recurse -Force -ErrorAction Stop
        } catch {
            Write-Error "[publisher] protected work cleanup failed: ${SnapshotRoot}: $($_.Exception.Message)"
            throw
        }
    }
}
