[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$PackagePath,
    [Parameter(Position = 1)][string]$SiteEnvPath = ""
)

$ErrorActionPreference = "Stop"
# The candidate verifier must only reach the local Docker daemon, never a caller-selected endpoint.
Remove-Item Env:DOCKER_HOST -ErrorAction SilentlyContinue
Remove-Item Env:DOCKER_CONTEXT -ErrorAction SilentlyContinue
function Fail([string]$Message) { throw "[publisher] authenticity FAILED: $Message" }
if ($PSVersionTable.PSVersion -lt [version]"7.3") {
    Fail "PowerShell 7.3 or newer is required"
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
$Fixed = @(
    ".env.prod.example", "MANIFEST.json", "MANIFEST.md", "SHA256SUMS", "SHA256SUMS.sig",
    "docker-compose.prod.yml", "nginx.conf", "site-acceptance-profile.md.example",
    "site-health-acl.conf.example", "site-network.override.yml", "setup-customer.md",
    "validate-network-boundary.py", "verify-candidate.ps1", "verify-candidate.sh"
)
$Expected = [Collections.Generic.HashSet[string]]::new(
    [string[]]$Fixed, [StringComparer]::Ordinal
)
foreach ($Component in @("postgres", "redis", "api", "gw", "web")) {
    [void]$Expected.Add("images/$Component.tar.gz")
}
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
$Missing = @($Expected | Where-Object { -not $Actual.Contains($_) })
$Extra = @($Actual | Where-Object { -not $Expected.Contains($_) })
if ($Missing.Count -ne 0 -or $Extra.Count -ne 0) {
    Fail "candidate file allowlist mismatch: missing=$($Missing -join ','), extra=$($Extra -join ',')"
}

$SnapshotRoot = New-ProtectedSnapshotRoot "publisher-snapshot-"
try {
    [void](New-Item -ItemType Directory -Path (Join-Path $SnapshotRoot "images"))
    $SourceLengths = @{}
    [Int64]$SnapshotBytes = 0
    foreach ($Relative in $Expected) {
        $SourcePath = Join-Path $SourcePackageRoot $Relative
        $SourceItem = Get-Item -Force -LiteralPath $SourcePath -ErrorAction Stop
        if ($SourceItem.PSIsContainer -or
            ($SourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Fail "candidate file changed or linked: $Relative"
        }
        $SourceLengths[$Relative] = [Int64]$SourceItem.Length
        $SnapshotBytes += [Int64]$SourceItem.Length
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
        $InputStream = $null
        $OutputStream = $null
        try {
            $InputStream = [IO.File]::Open(
                $SourcePath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
            )
            [Int64]$ExpectedLength = $SourceLengths[$Relative]
            if ($InputStream.Length -ne $ExpectedLength) {
                Fail "candidate file changed before snapshot: $Relative"
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
                Fail "candidate file size changed during snapshot: $Relative"
            }
        } finally {
            if ($null -ne $OutputStream) { $OutputStream.Dispose() }
            if ($null -ne $InputStream) { $InputStream.Dispose() }
        }
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
$Start = [Diagnostics.ProcessStartInfo]::new()
$Start.FileName = $SshKeygen
$Start.UseShellExecute = $false
$Start.RedirectStandardInput = $true
$Start.RedirectStandardOutput = $true
$Start.RedirectStandardError = $true
foreach ($Argument in @("-Y", "verify", "-f", $AllowedSigners, "-I", "ruisheng-release", "-n", "ruisheng-candidate-v1", "-s", $SignaturePath)) {
    [void]$Start.ArgumentList.Add($Argument)
}
$Process = [Diagnostics.Process]::Start($Start)
$SshOutputTask = $Process.StandardOutput.ReadToEndAsync()
$SshErrorTask = $Process.StandardError.ReadToEndAsync()
[byte[]]$SumsBytes = [IO.File]::ReadAllBytes($SumsPath)
$Process.StandardInput.BaseStream.Write($SumsBytes, 0, $SumsBytes.Length)
$Process.StandardInput.Close()
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
        $CachedBytes = [IO.File]::ReadAllBytes($CandidatePath)
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
if ($Manifest.schema_version -ne 2 -or @($Manifest.authenticity.PSObject.Properties).Count -ne 8 -or
    @($ExpectedAuthenticity.Keys | Where-Object {
        $Manifest.authenticity.PSObject.Properties.Name -cnotcontains $_ -or
        $Manifest.authenticity.$_ -cne $ExpectedAuthenticity[$_]
    }).Count -ne 0) {
    Fail "signed manifest authenticity contract is invalid"
}
Write-Host "[publisher] VERIFIED: publisher signature and complete candidate hashes passed"
$CandidateVerifier = Join-Path $PackageRoot "verify-candidate.ps1"
if ([string]::IsNullOrWhiteSpace($SiteEnvPath)) {
    & $CandidateVerifier $PackageRoot
} else {
    & $CandidateVerifier $PackageRoot $SiteEnvPath
}
$CandidateExitCode = $LASTEXITCODE
exit $CandidateExitCode
} finally {
    if (Test-Path -LiteralPath $SnapshotRoot) {
        try {
            Remove-Item -LiteralPath $SnapshotRoot -Recurse -Force -ErrorAction Stop
        } catch {
            Write-Error "[publisher] protected work cleanup failed: ${SnapshotRoot}: $($_.Exception.Message)"
            throw
        }
    }
}
