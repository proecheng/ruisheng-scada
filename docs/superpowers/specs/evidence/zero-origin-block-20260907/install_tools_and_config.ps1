param([Parameter(Mandatory=$true)][string]$ExpectedManifestHash)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Transfer = 'C:\Ruisheng\incoming\zero-origin-block-20260907'
$Candidate = 'C:\Ruisheng\candidates\deploy-20260907.3'
$Site = 'C:\Ruisheng\candidates\site-deploy-20260831.1'
$ReceiptPath = 'C:\ProgramData\Ruisheng\receipts\modbus-probe-release.json'
$Destination = 'C:\Ruisheng\site\modbus-probe-zero-origin-20260907.json'
function Hash([string]$Path) { return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
if ((Hash (Join-Path $Transfer 'transfer-manifest.json')) -cne $ExpectedManifestHash) { throw 'Transfer manifest mismatch' }
$Manifest = Get-Content -LiteralPath (Join-Path $Transfer 'transfer-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Manifest.candidate_id -cne 'deploy-20260907.3') { throw 'Unexpected candidate' }
if (Test-Path -LiteralPath $Destination) { throw 'Zero-origin config exists; refusing overwrite' }
$ActivePath = Join-Path $Site '.remote-maintenance-state\active-release.json'
$ActiveHash = Hash $ActivePath
$EnvironmentHash = Hash (Join-Path $Site '.env.prod')
$Original = 'C:\Ruisheng\site\modbus-probe.json'
$Extended = 'C:\Ruisheng\site\modbus-probe-extended-20260907.json'
$OriginalHash = Hash $Original
$ExtendedHash = Hash $Extended
$Correlation = 'C:\Ruisheng\site\modbus-probe-correlation-20260907.json'
$CorrelationHash = Hash $Correlation
$Active = Get-Content -LiteralPath $ActivePath -Raw | ConvertFrom-Json
if ($Active.candidate_id -cne 'deploy-20260907.1' -or $Active.source_commit -cne '2150b5ee904760ce0af4483c201009b8744669a2') { throw 'Production release changed' }
if (Test-Path -LiteralPath (Join-Path $Transfer 'receipt-before.json')) {
    if ((Hash $ReceiptPath) -cne (Hash (Join-Path $Transfer 'receipt-before.json'))) { throw 'Receipt changed since failed attempt' }
} else {
    Copy-Item -LiteralPath $ReceiptPath -Destination (Join-Path $Transfer 'receipt-before.json')
}
$Publisher = 'C:\ProgramData\Ruisheng\bin\verify-publisher.ps1'
if ((Hash $Publisher) -cne 'f37bb3ea11bc4fa039350e6309d4169f4a8072816153440cd00cbd95e5ba7df3') { throw 'Enrolled publisher changed' }
& 'C:\Program Files\PowerShell\7\pwsh.exe' -NoProfile -NonInteractive -File $Publisher -PackagePath $Candidate -InstallSerialTools
$PublisherExit = $LASTEXITCODE
if ($PublisherExit -notin @(0,2)) { throw ('Protected publisher failed: ' + $PublisherExit) }
$Receipt = Get-Content -LiteralPath $ReceiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
$ProbeHash = @($Manifest.metadata | Where-Object name -CEQ 'probe_modbus_rtu.py')[0].sha256
$RunnerHash = @($Manifest.metadata | Where-Object name -CEQ 'run_modbus_probe.ps1')[0].sha256
if ($Receipt.candidate_id -cne $Manifest.candidate_id -or $Receipt.source_commit -cne $Manifest.source_commit -or
    $Receipt.gw_image_id -cne 'sha256:becb5435ecff9449f7fe9d39e655d0d8d33c7cb14629a33247ed0a0b29c420b6' -or
    $Receipt.probe_sha256 -cne $ProbeHash -or $Receipt.runner_sha256 -cne $RunnerHash -or
    (Hash 'C:\Ruisheng\tools\probe_modbus_rtu.py') -cne $ProbeHash -or
    (Hash 'C:\Ruisheng\tools\run_modbus_probe.ps1') -cne $RunnerHash) { throw 'Installed receipt or tools mismatch' }
$InputConfig = Join-Path $Transfer 'approved-config.json'
if ((Hash $InputConfig) -cne $Manifest.config_sha256) { throw 'Approved configuration hash mismatch' }
$Security = [Security.AccessControl.FileSecurity]::new()
$Security.SetAccessRuleProtection($true,$false)
$Security.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
foreach ($Sid in @('S-1-5-18','S-1-5-32-544')) {
    $Security.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        [Security.Principal.SecurityIdentifier]::new($Sid),[Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.AccessControlType]::Allow))
}
$Bytes = [IO.File]::ReadAllBytes($InputConfig)
$Stream = [IO.FileStream]::new($Destination,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None,4096,[IO.FileOptions]::WriteThrough)
try { $Stream.Write($Bytes,0,$Bytes.Length); $Stream.Flush($true) } finally { $Stream.Dispose() }
Set-Acl -LiteralPath $Destination -AclObject $Security
$Acl = Get-Acl -LiteralPath $Destination
if (-not $Acl.AreAccessRulesProtected -or $Acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -cne 'S-1-5-32-544' -or
    @($Acl.Access).Count -ne 2 -or (Hash $Destination) -cne $Manifest.config_sha256) { throw 'Protected configuration verification failed' }
if ((Hash $Original) -cne $OriginalHash -or (Hash $Extended) -cne $ExtendedHash -or
    (Hash $Correlation) -cne $CorrelationHash -or
    (Hash $ActivePath) -cne $ActiveHash -or (Hash (Join-Path $Site '.env.prod')) -cne $EnvironmentHash) { throw 'Production or prior configuration changed' }
[ordered]@{installed=$true;publisher_exit=$PublisherExit;candidate_id=$Receipt.candidate_id;source_commit=$Receipt.source_commit;
    gw_image_id=$Receipt.gw_image_id;config_path=$Destination;config_sha256=(Hash $Destination);
    previous_profiles_unchanged=$true;production_release_unchanged=$true;site_environment_unchanged=$true;observed_at=(Get-Date).ToString('o')} | ConvertTo-Json -Compress
