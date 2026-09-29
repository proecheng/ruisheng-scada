param([Parameter(Mandatory=$true)][string]$ExpectedManifestHash)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Transfer = 'C:\Ruisheng\incoming\zero-origin-block-20260907'
$ToolRoot = 'C:\Users\lenovo\AppData\Local\Temp\ruisheng-point-release-20260907-delta'
$Old = 'C:\Ruisheng\candidates\deploy-20260907.1'
$Candidate = 'C:\Ruisheng\candidates\deploy-20260907.3'
$Site = 'C:\Ruisheng\candidates\site-deploy-20260831.1'
function Hash([string]$Path) { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Assert-Hash([string]$Path, [string]$Expected) {
    $Item = Get-Item -LiteralPath $Path -Force
    if ($Item.PSIsContainer -or ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or (Hash $Path) -cne $Expected) {
        throw ('File identity mismatch: ' + [IO.Path]::GetFileName($Path))
    }
}
foreach ($Root in @($Transfer,$ToolRoot,$Old,(Split-Path -Parent $Candidate))) {
    $Item = Get-Item -LiteralPath $Root -Force
    if (-not $Item.PSIsContainer -or ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Unsafe root' }
}
$State = Join-Path $Site '.remote-maintenance-state'
$Active = Get-Content -LiteralPath (Join-Path $State 'active-release.json') -Raw | ConvertFrom-Json
if ($Active.candidate_id -cne 'deploy-20260907.1' -or $Active.source_commit -cne '2150b5ee904760ce0af4483c201009b8744669a2' -or
    (Test-Path -LiteralPath (Join-Path $State '.remote-maintenance.lock')) -or (Test-Path -LiteralPath (Join-Path $Site '.remote-hotfix.lock'))) {
    throw 'Active release or maintenance state changed'
}
Assert-Hash (Join-Path $Transfer 'transfer-manifest.json') $ExpectedManifestHash
$Manifest = Get-Content -LiteralPath (Join-Path $Transfer 'transfer-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Manifest.candidate_id -cne 'deploy-20260907.3' -or @($Manifest.images).Count -ne 5 -or
    ((@($Manifest.images | ForEach-Object component | Sort-Object) -join ',') -cne 'api,gw,postgres,redis,web')) { throw 'Invalid transfer manifest' }
foreach ($Tool in $Manifest.tools) {
    if ($Tool.name -cnotin @('zstd.exe','zstd.dll','zlib.dll','VCRUNTIME140.dll')) { throw 'Unexpected helper' }
    Assert-Hash (Join-Path $ToolRoot $Tool.name) $Tool.sha256
}
if (@($Manifest.tools).Count -ne 4) { throw 'Missing helper hashes' }
Assert-Hash (Join-Path $Transfer 'metadata.zip') $Manifest.metadata_zip_sha256
if (Test-Path -LiteralPath $Candidate) { throw 'Candidate already exists; refusing overwrite' }
[void](New-Item -ItemType Directory -Path $Candidate)
[void](New-Item -ItemType Directory -Path (Join-Path $Candidate 'images'))
Add-Type -AssemblyName System.IO.Compression.FileSystem
$Zip = [IO.Compression.ZipFile]::OpenRead((Join-Path $Transfer 'metadata.zip'))
try {
    $Seen = @{}
    foreach ($Entry in $Zip.Entries) {
        if ($Entry.FullName -cnotmatch '^\.?[A-Za-z0-9][A-Za-z0-9._-]*$' -or $Seen.ContainsKey($Entry.FullName)) { throw 'Invalid metadata entry' }
        $Expected = @($Manifest.metadata | Where-Object name -CEQ $Entry.FullName)
        if ($Expected.Count -ne 1) { throw 'Unexpected metadata file' }
        $Destination = Join-Path $Candidate $Entry.FullName
        [IO.Compression.ZipFileExtensions]::ExtractToFile($Entry,$Destination,$false)
        Assert-Hash $Destination $Expected[0].sha256
        $Seen[$Entry.FullName] = $true
    }
    if ($Seen.Count -ne @($Manifest.metadata).Count) { throw 'Missing metadata file' }
} finally { $Zip.Dispose() }
foreach ($Image in $Manifest.images) {
    $Base = Join-Path (Join-Path $Old 'images') ($Image.component + '.tar.gz')
    $Patch = Join-Path $Transfer ($Image.component + '.zst')
    $Output = Join-Path (Join-Path $Candidate 'images') ($Image.component + '.tar.gz')
    Assert-Hash $Base $Image.old_sha256
    Assert-Hash $Patch $Image.patch_sha256
    & (Join-Path $ToolRoot 'zstd.exe') -d --memory=512MB --patch-from=$Base $Patch -o $Output
    if ($LASTEXITCODE -ne 0) { throw 'Image reconstruction failed' }
    Assert-Hash $Output $Image.new_sha256
    Assert-Hash $Base $Image.old_sha256
}
Assert-Hash (Join-Path $Candidate 'SHA256SUMS') $Manifest.sha256sums_sha256
Assert-Hash (Join-Path $Candidate 'SHA256SUMS.sig') $Manifest.signature_sha256
[ordered]@{reconstructed=$true;candidate=$Candidate;old_archives_unchanged=$true;production_release_unchanged=$true} | ConvertTo-Json -Compress
