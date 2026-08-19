[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$PackagePath = ".",

    [Parameter(Position = 1)]
    [string]$SiteEnvPath = ""
)

$ErrorActionPreference = "Stop"
$PackageRoot = (Resolve-Path -LiteralPath $PackagePath).Path.TrimEnd("\", "/")
$ComposeEnvPath = if ([string]::IsNullOrWhiteSpace($SiteEnvPath)) {
    Join-Path $PackageRoot ".env.prod.example"
} else {
    (Resolve-Path -LiteralPath $SiteEnvPath).Path
}
$ManifestPath = Join-Path $PackageRoot "MANIFEST.json"
$Manifest = Get-Content -Raw -Encoding UTF8 -LiteralPath $ManifestPath | ConvertFrom-Json
$Components = @("postgres", "redis", "api", "gw", "web")
$FixedFiles = @(
    ".env.prod.example",
    "MANIFEST.json",
    "MANIFEST.md",
    "SHA256SUMS",
    "docker-compose.prod.yml",
    "setup-customer.md",
    "verify-candidate.ps1",
    "verify-candidate.sh"
)

function Fail([string]$Message) {
    throw "[verify] $Message"
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
        $Descriptors = @($Index.manifests)
        if ($Descriptors.Count -ne 1 -or
            $Descriptors[0].digest -notmatch '^sha256:[0-9a-f]{64}$') {
            Fail "Archive index must contain one valid descriptor: $ArchivePath"
        }
        $DescriptorDigest = [string]$Descriptors[0].digest
        $DescriptorName = "blobs/sha256/$($DescriptorDigest.Substring(7))"
        $DescriptorRecord = Read-TarEntries $ArchivePath @($DescriptorName)
        if (-not $DescriptorRecord.Values.ContainsKey($DescriptorName)) {
            Fail "Archive descriptor is missing: $ArchivePath`:$DescriptorName"
        }
        $DescriptorBytes = [byte[]]$DescriptorRecord.Values[$DescriptorName]
        if ("sha256:$(Get-Sha256Bytes $DescriptorBytes)" -cne $DescriptorDigest) {
            Fail "Archive descriptor digest mismatch: $ArchivePath"
        }
        try {
            $Descriptor = [Text.Encoding]::UTF8.GetString($DescriptorBytes) | ConvertFrom-Json
        } catch {
            Fail "Archive descriptor is invalid JSON: $ArchivePath"
        }
        if ($Descriptor.config.digest -cne $ConfigDigest) {
            Fail "Archive descriptor/config digest mismatch: $ArchivePath"
        }
        $ImageId = $DescriptorDigest
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
if ($Manifest.authenticity.status -ne "BLOCKED") {
    Fail "Manifest removed the publisher-authenticity BLOCKED gate"
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
    Fail "Candidate file allowlist mismatch: missing=$($Missing -join ','), extra=$($Extra -join ',')"
}

$Sums = @{}
$LineNumber = 0
Get-Content -Encoding UTF8 -LiteralPath (Join-Path $PackageRoot "SHA256SUMS") | ForEach-Object {
    $LineNumber++
    if ($_ -notmatch '^([0-9a-f]{64})  (.+)$') {
        Fail "Invalid SHA256SUMS entry at line $LineNumber"
    }
    $Digest = $Matches[1]
    $Relative = $Matches[2]
    Test-SafeRelativePath $Relative
    if ($Sums.ContainsKey($Relative)) {
        Fail "Duplicate SHA256SUMS path: $Relative"
    }
    $Sums[$Relative] = $Digest
}
$ExpectedSums = @($ExpectedFiles | Where-Object { $_ -ne "SHA256SUMS" })
$MissingSums = @($ExpectedSums | Where-Object { -not $Sums.ContainsKey($_) })
$ExtraSums = @($Sums.Keys | Where-Object { -not $ExpectedFiles.Contains($_) })
if ($Sums.Count -ne $ExpectedSums.Count -or $MissingSums.Count -ne 0 -or $ExtraSums.Count -ne 0) {
    Fail "SHA256SUMS allowlist mismatch: missing=$($MissingSums -join ','), extra=$($ExtraSums -join ',')"
}
foreach ($Relative in $ExpectedSums) {
    $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $PackageRoot $Relative)).Hash.ToLowerInvariant()
    if ($Actual -ne $Sums[$Relative]) {
        Fail "SHA-256 mismatch for ${Relative}: expected $($Sums[$Relative]), got $Actual"
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

Write-Host "[verify] File allowlist, SHA-256, and archive identities passed."
foreach ($Image in $Manifest.images) {
    Write-Host "[verify] Loading $($Image.component) from $($Image.archive)"
    & docker image load --input (Join-Path $PackageRoot $Image.archive) | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker load failed for $($Image.component)"
    }
}

foreach ($Image in $Manifest.images) {
    $Raw = & docker image inspect $Image.candidate_reference --format '{{json .}}'
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
$ResolvedImages = @(& docker @ComposeArgs config --images | Where-Object { $_ })
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
$ComposeConfig = (& docker @ComposeArgs config --format json) | ConvertFrom-Json
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
Write-Warning "Publisher authenticity is not configured; CAP-1/G0-03 remain BLOCKED."
