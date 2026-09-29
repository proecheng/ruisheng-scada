$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Old = 'C:\ProgramData\Ruisheng\publisher-output\deploy-20260907.1'
$New = 'C:\ProgramData\Ruisheng\publisher-output\deploy-20260907.2'
$Transfer = 'C:\ProgramData\Ruisheng\publisher-transfer\address-correlation-20260907'
$ToolRoot = 'C:\ProgramData\Ruisheng\publisher-tools'
$Evidence = 'D:\江苏润盛\docs\superpowers\specs\evidence\address-correlation-20260907'
if (Test-Path -LiteralPath $Transfer) { throw 'Transfer directory already exists' }
[void](New-Item -ItemType Directory -Path $Transfer)
[void](New-Item -ItemType Directory -Path (Join-Path $Transfer 'roundtrip'))
function Hash([string]$Path) { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
$OldManifest = Get-Content -LiteralPath (Join-Path $Old 'MANIFEST.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$NewManifest = Get-Content -LiteralPath (Join-Path $New 'MANIFEST.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($OldManifest.candidate_id -cne 'deploy-20260907.1' -or $NewManifest.candidate_id -cne 'deploy-20260907.2') {
    throw 'Unexpected candidate pair'
}
$Images = @()
foreach ($Image in $NewManifest.images) {
    $Previous = @($OldManifest.images | Where-Object component -eq $Image.component)
    if ($Previous.Count -ne 1 -or $Previous[0].image_id -cne $Image.image_id -or
        $Image.component -cnotin @('postgres','redis','api','gw','web')) { throw 'Application image reuse mismatch' }
    $Base = Join-Path $Old $Previous[0].archive
    $Source = Join-Path $New $Image.archive
    $Patch = Join-Path $Transfer ($Image.component + '.zst')
    $Roundtrip = Join-Path (Join-Path $Transfer 'roundtrip') ($Image.component + '.tar.gz')
    if ((Hash $Base) -cne $Previous[0].sha256 -or (Hash $Source) -cne $Image.sha256) { throw 'Signed image hash mismatch' }
    & (Join-Path $ToolRoot 'zstd.exe') --patch-from=$Base $Source -o $Patch
    if ($LASTEXITCODE -ne 0) { throw 'Delta creation failed' }
    & (Join-Path $ToolRoot 'zstd.exe') -d --memory=512MB --patch-from=$Base $Patch -o $Roundtrip
    if ($LASTEXITCODE -ne 0 -or (Hash $Roundtrip) -cne $Image.sha256) { throw 'Delta roundtrip failed' }
    $Images += [ordered]@{ component=$Image.component; old_sha256=$Previous[0].sha256; new_sha256=$Image.sha256;
        image_id=$Image.image_id; patch_sha256=(Hash $Patch); patch_bytes=(Get-Item -LiteralPath $Patch).Length }
    Write-Output ('Verified delta: ' + $Image.component)
}
$Metadata = @(Get-ChildItem -LiteralPath $New -File)
Compress-Archive -LiteralPath @($Metadata | ForEach-Object FullName) -DestinationPath (Join-Path $Transfer 'metadata.zip')
Copy-Item -LiteralPath (Join-Path $Evidence 'approved-config.json') -Destination (Join-Path $Transfer 'approved-config.json')
$Tools = @('zstd.exe','zstd.dll','zlib.dll','VCRUNTIME140.dll') | ForEach-Object {
    [ordered]@{ name=$_; sha256=(Hash (Join-Path $ToolRoot $_)) }
}
$Manifest = [ordered]@{
    candidate_id=$NewManifest.candidate_id; source_commit=$NewManifest.source_commit;
    logical_identity=$NewManifest.logical_identity; images=$Images; tools=@($Tools);
    metadata_zip_sha256=(Hash (Join-Path $Transfer 'metadata.zip'));
    metadata=@($Metadata | ForEach-Object { [ordered]@{name=$_.Name;sha256=(Hash $_.FullName)} });
    config_sha256=(Hash (Join-Path $Transfer 'approved-config.json'));
    sha256sums_sha256=(Hash (Join-Path $New 'SHA256SUMS'));
    signature_sha256=(Hash (Join-Path $New 'SHA256SUMS.sig'))
}
$Manifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $Transfer 'transfer-manifest.json') -Encoding UTF8
$Manifest | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $Evidence 'transfer-manifest.json') -Encoding UTF8
Write-Output ('Transfer ready: ' + $Transfer)
