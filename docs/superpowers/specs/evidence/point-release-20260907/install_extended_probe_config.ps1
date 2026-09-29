$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$ActivePath = 'C:\Ruisheng\candidates\site-deploy-20260831.1\.remote-maintenance-state\active-release.json'
$ReceiptPath = 'C:\ProgramData\Ruisheng\receipts\modbus-probe-release.json'
$OriginalPath = 'C:\Ruisheng\site\modbus-probe.json'
$Destination = 'C:\Ruisheng\site\modbus-probe-extended-20260907.json'
$ExpectedImage = 'sha256:becb5435ecff9449f7fe9d39e655d0d8d33c7cb14629a33247ed0a0b29c420b6'
$Active = Get-Content -Raw -LiteralPath $ActivePath | ConvertFrom-Json
$Receipt = Get-Content -Raw -LiteralPath $ReceiptPath | ConvertFrom-Json
if ($Active.candidate_id -cne 'deploy-20260907.1' -or
    $Active.source_commit -cne '2150b5ee904760ce0af4483c201009b8744669a2' -or
    $Receipt.candidate_id -cne $Active.candidate_id -or
    $Receipt.source_commit -cne $Active.source_commit -or
    $Receipt.gw_image_id -cne $ExpectedImage) { throw 'Verified release is not installed and active.' }
if (Test-Path -LiteralPath $Destination) { throw 'Extended config already exists; refusing overwrite.' }
$OriginalHash = (Get-FileHash -LiteralPath $OriginalPath -Algorithm SHA256).Hash
if ($OriginalHash -cne '3D6871155456477922F7F1CF28B142A7BF1604559B1B584832A6182E11BF081E') {
    throw 'Original approved configuration changed.'
}
$Config = Get-Content -Raw -LiteralPath $OriginalPath | ConvertFrom-Json
$Config.scope.requests = @(
    [ordered]@{ start_address=0; register_count=6; requires_previous_valid=$false },
    [ordered]@{ start_address=6; register_count=21; requires_previous_valid=$true },
    [ordered]@{ start_address=27; register_count=9; requires_previous_valid=$true }
)
$Config.budget.max_requests = 6
$Config.approval.scope_id = 'b09-9600-8n1-unit1-fc3-r0-5-r6-26-r27-35'
$Config.approval.approved_by = 'current-thread-user:01a012ef-5097-7aa1-9011-35c391b826c3'
$Config.approval.approved_at = [DateTimeOffset]::UtcNow.ToString('o')
$Security = [Security.AccessControl.FileSecurity]::new()
$Security.SetAccessRuleProtection($true, $false)
$Security.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
foreach ($SidValue in @('S-1-5-18','S-1-5-32-544')) {
    $Rule = [Security.AccessControl.FileSystemAccessRule]::new(
        [Security.Principal.SecurityIdentifier]::new($SidValue),
        [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.AccessControlType]::Allow
    )
    [void]$Security.AddAccessRule($Rule)
}
$Bytes = [Text.UTF8Encoding]::new($false).GetBytes(($Config | ConvertTo-Json -Depth 10) + "`n")
$Stream = [IO.FileStream]::new($Destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
    [IO.FileShare]::None, 4096, [IO.FileOptions]::WriteThrough)
try { $Stream.Write($Bytes,0,$Bytes.Length); $Stream.Flush($true) } finally { $Stream.Dispose() }
Set-Acl -LiteralPath $Destination -AclObject $Security
$Acl = Get-Acl -LiteralPath $Destination
if (-not $Acl.AreAccessRulesProtected -or
    $Acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -cne 'S-1-5-32-544' -or
    @($Acl.Access).Count -ne 2) { throw 'Extended configuration ACL verification failed.' }
if ((Get-FileHash -LiteralPath $OriginalPath -Algorithm SHA256).Hash -cne $OriginalHash) {
    throw 'Original configuration changed while preparing extended profile.'
}
[ordered]@{ path=$Destination; sha256=(Get-FileHash -LiteralPath $Destination).Hash;
    scope_id=$Config.approval.scope_id; approved_by=$Config.approval.approved_by;
    approval_recorded_at=$Config.approval.approved_at; original_unchanged=$true } | ConvertTo-Json -Compress
