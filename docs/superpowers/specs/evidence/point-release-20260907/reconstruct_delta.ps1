$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Transfer = 'C:\Users\lenovo\AppData\Local\Temp\ruisheng-point-release-20260907-delta'
$SourceRoot = 'C:\Ruisheng\candidates\deploy-20260905.2\images'
$Incoming = 'C:\Ruisheng\incoming\1c95b8c8-8f92-4e8b-962f-207800c7e9c9\deploy-20260907.1'
$State = 'C:\Ruisheng\candidates\site-deploy-20260831.1\.remote-maintenance-state'
if ((Test-Path -LiteralPath "$State\full-upgrade-1c95b8c8-8f92-4e8b-962f-207800c7e9c9.json") -or
    (Test-Path -LiteralPath "$State\.remote-maintenance.lock") -or
    (Test-Path -LiteralPath 'C:\Ruisheng\candidates\site-deploy-20260831.1\.remote-hotfix.lock') -or
    (Get-Content -Raw -LiteralPath "$State\active-release.json" | ConvertFrom-Json).candidate_id -cne 'deploy-20260905.2') {
    throw 'Upgrade state changed; do not reconstruct while an Apply may be active.'
}
foreach ($Path in @($Transfer, $SourceRoot, $Incoming)) {
    $Item = Get-Item -LiteralPath $Path -Force
    if (-not $Item.PSIsContainer -or ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Delta source or destination root is linked or invalid.'
    }
}
$Tools = @{
    'zstd.exe'='959e8299adfbbc039f7d2aede537666aba5e605dec1169f582d88898554a728e'
    'zstd.dll'='166f5159df543d6278e71d13d3a56a6013d28cb847b23d3cde0305672c559c2a'
    'zlib.dll'='7d86de8659d728d0cc22615ea37248ca83b24af64a9fc19ecc4548105308ee0a'
    'VCRUNTIME140.dll'='184146852727a9db4eea06178716bec3cdbb1015c911f6b0f915b184ad7775b2'
}
function Assert-Hash([string]$Path, [string]$Expected) {
    $Item = Get-Item -LiteralPath $Path -Force
    if ($Item.PSIsContainer -or ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
        (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Expected) {
        throw "Delta input/output hash mismatch: $([IO.Path]::GetFileName($Path))"
    }
}
foreach ($Name in $Tools.Keys) { Assert-Hash (Join-Path $Transfer $Name) $Tools[$Name] }
$Images = @(
    @{name='postgres';old='823099bb7389d4c34dadfef82901b0cf2951d1b26a6dbb81e57c647aed4befd7';patch='4dcbc9f93523d58a697c280a7100db67d52c27d9a3f0ac5db403c17ce70ee104';new='cd114aa1242ca3f2f53cb08141708313454cb5ae783ad4de3acf99fcfdd5c693'},
    @{name='redis';old='14a4d053022fd9417ef9f56d2698738140ca5e077174f56ee8bd9acfbf5b8e19';patch='46face65ddf408f2df10dd0704044dc986bd42acf5879beb356bca75a6be16e0';new='c95401e12023caf124380960be7e5426f7dd6271595b6144d75aca3360764126'},
    @{name='api';old='75c1947e91cbaf5dc2ac2c87690dd381bc070a3364b04393c2bfaac90b660832';patch='385e8b26f594c7b9762e3546c36e61f512f4fc85a2961e90f2a23c07ba71702e';new='d4fc10e3648bab5ba296fda8067bc49e39f29fe03513667b02038906d38dffb6'},
    @{name='gw';old='ea545e6061c2d4d7b081490757051af37d810e292001dfcbb68661abd22879e0';patch='ff468f7c64240a2718e93cb7d5bbaeaf6061c9877c372b5db2ec208fe33b5a1d';new='7caf587be0053c0d184ee1c177843687e57019f58916aadea6bc45ec3f553bfe'},
    @{name='web';old='2171c3e271f299fdb02153cd5e44c67d542c09703b2d51f4672d760ded4a60b2';patch='5193f553c8eef5681881c3c47faa878480a00ca408dd870caf40fad52e2db445';new='c80466f0bc2e0b0e100e0e317c674a8dca954b5d66763d7fbedae8ade12f6117'}
)
$Rebuilt = Join-Path $Transfer 'reconstructed'
$PreviousUpload = Join-Path $Transfer 'previous-upload'
foreach ($Path in @($Rebuilt,$PreviousUpload)) {
    if (Test-Path -LiteralPath $Path) { throw 'Reconstruction staging already exists; inspect before retry.' }
    New-Item -ItemType Directory -Path $Path | Out-Null
}
foreach ($Image in $Images) {
    $Source = Join-Path $SourceRoot ($Image.name+'.tar.gz')
    $Patch = Join-Path $Transfer ($Image.name+'.zst')
    $Output = Join-Path $Rebuilt ($Image.name+'.tar.gz')
    Assert-Hash $Source $Image.old
    Assert-Hash $Patch $Image.patch
    & (Join-Path $Transfer 'zstd.exe') -d --memory=512MB "--patch-from=$Source" $Patch -o $Output
    if ($LASTEXITCODE -ne 0) { throw 'Delta reconstruction failed.' }
    Assert-Hash $Output $Image.new
}
# All five outputs must verify before replacing any owned upload placeholder.
foreach ($Image in $Images) {
    $Output = Join-Path $Rebuilt ($Image.name+'.tar.gz')
    $Destination = Join-Path "$Incoming\images" ($Image.name+'.tar.gz')
    $Backup = Join-Path $PreviousUpload ($Image.name+'.tar.gz')
    $Item = Get-Item -LiteralPath $Destination -Force
    if ($Item.PSIsContainer -or ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Upload placeholder is linked or invalid.'
    }
    [IO.File]::Replace($Output,$Destination,$Backup)
    Assert-Hash $Destination $Image.new
    Assert-Hash (Join-Path $SourceRoot ($Image.name+'.tar.gz')) $Image.old
}
[ordered]@{reconstructed=$true;candidate_id='deploy-20260907.1';images=@($Images | ForEach-Object {
    @{component=$_.name;sha256=$_.new}
});previous_upload_preserved=$PreviousUpload;source_archives_unchanged=$true} | ConvertTo-Json -Depth 5 -Compress
