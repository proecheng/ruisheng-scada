# Run locally, elevated, on the named target. No credentials or private keys included.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$script:SupportKey = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBh3OfKMf+LJIX3RnTcXS//aYM1Wi/RZEc36Uv334wh2'
$script:SupportFingerprint = 'SHA256:dE1iYX3saiUfnlRvatcaFyIswTi5qQ4qMXGPne5pIaI'

function Assert-PlainPath([string]$Path) {
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'linked_path_refused' }
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
}

function Read-KeyText([byte[]]$Bytes) {
    if ($Bytes.Length -gt 65536) { throw 'key_file_too_large' }
    if ($Bytes.Length -ge 2 -and $Bytes[0] -eq 255 -and $Bytes[1] -eq 254) {
        $text = [Text.Encoding]::Unicode.GetString($Bytes, 2, $Bytes.Length - 2)
    } elseif ($Bytes.Length -ge 2 -and $Bytes[0] -eq 254 -and $Bytes[1] -eq 255) {
        $text = [Text.Encoding]::BigEndianUnicode.GetString($Bytes, 2, $Bytes.Length - 2)
    } else {
        $text = ([Text.UTF8Encoding]::new($false, $true)).GetString($Bytes).TrimStart([char]0xFEFF)
    }
    if ($text.Contains([string][char]0)) { throw 'invalid_key_file_encoding' }
    return $text
}

function Get-RepairedKeyText([string]$Text, [string]$PublicKey) {
    $blob = $PublicKey.Split(' ')[1]
    # Keep an existing key's from/command/no-forwarding restrictions intact.
    $pattern = '(?m)^[ \t]*(?![ \t]*#)[^\r\n]*\bssh-ed25519[ \t]+' + [regex]::Escape($blob) + '(?=[ \t\r\n]|$)'
    if ([regex]::IsMatch($Text, $pattern)) { return $Text }
    $prefix = if ($Text.Length -gt 0 -and -not $Text.EndsWith("`n")) { "`r`n" } else { '' }
    return $Text + $prefix + 'from="100.67.229.19" ' + $PublicKey + " support-restore-20260921`r`n"
}

function Resolve-KeyFile([string]$Configuration, [string]$ProfilePath) {
    $values = @{}
    foreach ($line in ($Configuration -split '\r?\n')) {
        $parts = $line.Trim() -split '\s+', 2
        if ($parts.Count -eq 2) { $values[$parts[0].ToLowerInvariant()] = $parts[1] }
    }
    if ($values.pubkeyauthentication -ne 'yes' -or $values.passwordauthentication -ne 'no' -or
        $values.kbdinteractiveauthentication -ne 'no' -or $values.authenticationmethods -ne 'publickey' -or
        $values.hostbasedauthentication -ne 'no' -or $values.gssapiauthentication -ne 'no') {
        throw 'ssh_policy_requires_engineer_review'
    }
    $raw = ([string]$values.authorizedkeysfile).Trim('"').Replace('/', '\')
    if ($raw -ieq '__PROGRAMDATA__\ssh\administrators_authorized_keys' -or
        $raw -ieq 'C:\ProgramData\ssh\administrators_authorized_keys') {
        return 'C:\ProgramData\ssh\administrators_authorized_keys'
    }
    if ($raw -eq '.ssh\authorized_keys' -or $raw -eq '%h\.ssh\authorized_keys' -or
        $raw -ieq (Join-Path $ProfilePath '.ssh\authorized_keys')) {
        return (Join-Path $ProfilePath '.ssh\authorized_keys')
    }
    throw 'custom_authorized_keys_path_requires_review'
}

function Set-RecoveryAcl([string]$Path, $Acl) {
    # Set-Acl may attempt SACL writes from reconstructed descriptors. Persist only
    # the explicitly changed sections, as the existing maintenance helpers do.
    $item = Get-Item -LiteralPath $Path -Force
    if ($PSVersionTable.PSEdition -eq 'Core') { [IO.FileSystemAclExtensions]::SetAccessControl($item, $Acl) }
    else { $item.SetAccessControl($Acl) }
}

function Set-KeyAcl([string]$Path, [string]$AccountSid, [switch]$Directory) {
    $acl = if ($Directory) { New-Object Security.AccessControl.DirectorySecurity } else { New-Object Security.AccessControl.FileSecurity }
    $acl.SetAccessRuleProtection($true, $false)
    $ownerSid = if ($AccountSid) { $AccountSid } else { 'S-1-5-32-544' }
    $acl.SetOwner([Security.Principal.SecurityIdentifier]::new($ownerSid))
    $sids = @('S-1-5-18', 'S-1-5-32-544')
    if ($AccountSid) { $sids += $AccountSid }
    foreach ($sid in $sids) {
        $inherit = if ($Directory) { [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit' } else { [Security.AccessControl.InheritanceFlags]::None }
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($sid), 'FullControl', $inherit, 'None', 'Allow')
        [void]$acl.AddAccessRule($rule)
    }
    Set-RecoveryAcl $Path $acl
}

function Get-KeySnapshot([string]$Path) {
    Assert-PlainPath $Path
    if (-not (Test-Path -LiteralPath $Path)) { return @{path=$Path;existed=$false} }
    $item = Get-Item -LiteralPath $Path -Force
    $record = @{path=$Path;existed=$true;directory=[bool]$item.PSIsContainer;sddl=(Get-Acl -LiteralPath $Path).Sddl}
    if (-not $item.PSIsContainer) {
        if ($item.Length -gt 65536) { throw 'key_file_too_large' }
        $record.bytes = [Convert]::ToBase64String([IO.File]::ReadAllBytes($Path))
    }
    return $record
}

function Restore-KeySnapshot($Record) {
    Assert-PlainPath $Record.path
    if (-not $Record.existed) {
        if (Test-Path -LiteralPath $Record.path) {
            # No recursive removal; only a file or the empty .ssh directory created here.
            $item = Get-Item -LiteralPath $Record.path -Force
            if ($item.PSIsContainer) { [IO.Directory]::Delete($Record.path, $false) }
            else { [IO.File]::Delete($Record.path) }
        }
        return
    }
    if (-not $Record.directory) { [IO.File]::WriteAllBytes($Record.path, [Convert]::FromBase64String($Record.bytes)) }
    $acl = if ($Record.directory) { New-Object Security.AccessControl.DirectorySecurity } else { New-Object Security.AccessControl.FileSecurity }
    $acl.SetSecurityDescriptorSddlForm($Record.sddl, [Security.AccessControl.AccessControlSections]'Access,Owner,Group')
    Set-RecoveryAcl $Record.path $acl
}

function Invoke-TargetSshRestore {
    if ($env:COMPUTERNAME -cne 'WIN-OAUCM8UQUGH') { throw 'wrong_computer_run_on_WIN-OAUCM8UQUGH' }
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'run_as_administrator_required' }
    Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1')
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $fingerprint = 'SHA256:' + [Convert]::ToBase64String($sha.ComputeHash([Convert]::FromBase64String($script:SupportKey.Split(' ')[1]))).TrimEnd('=') }
    finally { $sha.Dispose() }
    if ($fingerprint -cne $script:SupportFingerprint) { throw 'support_key_identity_mismatch' }

    . (Join-Path $PSScriptRoot 'diagnostic_limits.ps1')
    $checks = @'
try {
$account = Get-LocalUser -Name lenovo
if (-not $account.Enabled) { throw 'lenovo_account_disabled' }
$sid = $account.SID.Value
$profile = (Get-ItemProperty -LiteralPath ('HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\' + $sid)).ProfileImagePath
$service = Get-CimInstance Win32_Service -Filter "Name='sshd'"
if ($service.State -ne 'Running' -or $service.PathName.Trim('"') -ine 'C:\Windows\System32\OpenSSH\sshd.exe') { throw 'nonstandard_or_stopped_sshd_requires_review' }
$siteFile = 'C:\ProgramData\Ruisheng\trust\entitlement-site-id'
if ((Get-Item -LiteralPath $siteFile).Length -gt 256) { throw 'invalid_site_id' }
$site = [IO.File]::ReadAllText($siteFile).Trim()
if ($site -notmatch '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$') { throw 'invalid_site_id' }
$receipt = & 'C:\ProgramData\Ruisheng\bin\target_entitlement_verifier.ps1' -Action Authorize -SiteId $site -Feature remote-support
$authorization = $receipt | ConvertFrom-Json
if (-not $authorization.ok -or $authorization.status -cne 'authorized' -or $authorization.feature -cne 'remote-support') { throw 'remote_support_not_authorized' }
$siteRoot = 'C:\Ruisheng\candidates\site-deploy-20260831.1'
foreach ($lock in @((Join-Path $siteRoot '.remote-maintenance-state\.remote-maintenance.lock'), (Join-Path $siteRoot '.remote-hotfix.lock'))) {
    if (Test-Path -LiteralPath $lock) { throw 'maintenance_lock_present' }
}
$clientHost = '100.67.229.19'
try { $clientHost = [Net.Dns]::GetHostEntry($clientHost).HostName } catch { }
$context = 'user=lenovo,host=' + $clientHost + ',addr=100.67.229.19,laddr=100.109.90.21,lport=22'
$config = & 'C:\Windows\System32\OpenSSH\sshd.exe' -T -C $context
if ($LASTEXITCODE -ne 0) { throw 'effective_sshd_config_failed' }
$events = @()
try { $events = @(Get-WinEvent -FilterHashtable @{LogName='OpenSSH/Operational';StartTime=(Get-Date).AddDays(-1)} -MaxEvents 12 -ErrorAction Stop | ForEach-Object { @{at=$_.TimeCreated.ToString('o');id=$_.Id;message=([string]$_.Message).Substring(0,[Math]::Min(1200,([string]$_.Message).Length))} }) } catch { }
@{ok=$true;sid=$sid;profile=$profile;config=($config -join "`n");events=$events} | ConvertTo-Json -Depth 6 -Compress
} catch { @{ok=$false;error=$_.Exception.Message} | ConvertTo-Json -Compress }
'@
    $run = Invoke-BoundedDiagnostic -Script $checks -TimeoutSeconds 40 -MemoryLimitMb 256 -MaxOutputChars 65536
    $preflight = $run.output | ConvertFrom-Json
    if (-not $preflight.ok) { throw ('preflight: ' + $preflight.error) }
    $profile = [Environment]::ExpandEnvironmentVariables([string]$preflight.profile)
    if ($profile -notmatch '^C:\\Users\\[^\\]+$') { throw 'unexpected_lenovo_profile' }
    $keyFile = Resolve-KeyFile $preflight.config $profile
    $parent = [IO.Path]::GetDirectoryName($keyFile)
    Assert-PlainPath $keyFile
    $userKey = $keyFile -ine 'C:\ProgramData\ssh\administrators_authorized_keys'
    $accountSid = if ($userKey) { [string]$preflight.sid } else { '' }
    if (-not $userKey -and -not (Test-Path -LiteralPath $parent -PathType Container)) { throw 'ssh_directory_missing' }
    $records = @()
    if ($userKey) { $records += Get-KeySnapshot $parent }
    $before = Get-KeySnapshot $keyFile
    if ($before.existed -and $before.directory) { throw 'key_path_is_directory' }
    $records += $before
    $oldBytes = if ($before.existed) { [Convert]::FromBase64String($before.bytes) } else { [byte[]]@() }
    $newText = Get-RepairedKeyText (Read-KeyText $oldBytes) $script:SupportKey
    $root = 'C:\ProgramData\Ruisheng\ssh-access-recovery'
    Assert-PlainPath $root
    if (-not (Test-Path -LiteralPath $root)) { [void][IO.Directory]::CreateDirectory($root); Set-KeyAcl $root '' -Directory }
    $backup = Join-Path $root ((Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($backup)
    Set-KeyAcl $backup '' -Directory
    [IO.File]::WriteAllText((Join-Path $backup 'before.json'), (ConvertTo-Json -InputObject $records -Depth 5), [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $backup 'preflight.json'), ($preflight | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
    Write-Host ('Backup saved: ' + $backup)
    $applied = $false
    try {
        # Refuse a concurrent key edit between inspection and application.
        $current = Get-KeySnapshot $keyFile
        if ($current.existed -ne $before.existed -or $current.bytes -cne $before.bytes -or $current.sddl -cne $before.sddl) { throw 'authorized_keys_changed_during_check' }
        $applied = $true
        if ($userKey) { [void][IO.Directory]::CreateDirectory($parent); Set-KeyAcl $parent $accountSid -Directory }
        if (-not $before.existed) { $f=[IO.File]::Open($keyFile,'CreateNew','Write','None'); $f.Dispose() }
        Set-KeyAcl $keyFile $accountSid
        [IO.File]::WriteAllText($keyFile, $newText, [Text.UTF8Encoding]::new($false))
        $actual = [IO.File]::ReadAllText($keyFile, [Text.Encoding]::UTF8)
        if ($actual -cne $newText) { throw 'key_write_verification_failed' }
        $acl = Get-Acl -LiteralPath $keyFile
        $allowed = @('S-1-5-18', 'S-1-5-32-544')
        if ($userKey) { $allowed += $accountSid }
        if (-not $acl.AreAccessRulesProtected -or $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $allowed) { throw 'key_acl_verification_failed' }
        foreach ($rule in $acl.Access) {
            if ($rule.AccessControlType -ne 'Allow' -or $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $allowed) { throw 'key_acl_verification_failed' }
        }
        $result = @{status='key_and_acl_repaired_remote_login_pending';at=(Get-Date -Format o);file=$keyFile;fingerprint=$fingerprint;backup=$backup;after_sddl=$acl.Sddl;after_sha256=(Get-FileHash -LiteralPath $keyFile -Algorithm SHA256).Hash}
        [IO.File]::WriteAllText((Join-Path $backup 'result.json'), ($result | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
        return $result
    } catch {
        $failure = $_
        if ($applied) {
            $reverse = @($records); [array]::Reverse($reverse)
            foreach ($record in $reverse) { Restore-KeySnapshot $record }
        }
        throw $failure
    }
}

try {
    $result = Invoke-TargetSshRestore
    Write-Host 'REPAIR COMPLETE. Remote SSH login still needs verification.' -ForegroundColor Green
    Write-Host ('Backup: ' + $result.backup)
    $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'RESULT.txt') -Encoding UTF8
    exit 0
} catch {
    $message = 'NOT COMPLETE: ' + $_.Exception.Message
    Write-Host $message -ForegroundColor Red
    $message | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'RESULT.txt') -Encoding UTF8
    exit 1
}
