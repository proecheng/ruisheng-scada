[CmdletBinding()]
param(
  [string]$SourceLauncherPath = (Join-Path $PSScriptRoot "start_ruisheng_local.ps1"),
  [string]$InstallRoot = "C:\Program Files\Ruisheng\Launcher",
  [string]$AuditRoot = "C:\Ruisheng\launcher-audit",
  [string]$LauncherUser = "lenovo",
  [string]$ShortcutName = "",
  [string]$DesktopPath = "",
  [switch]$EnableSerialRecovery
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$AdministratorsSid = "S-1-5-32-544"
$SystemSid = "S-1-5-18"

function Assert-Administrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "administrator_required"
  }
}

function Resolve-LauncherUserSid {
  $accounts = @($LauncherUser)
  if ($LauncherUser -notmatch '[\\@]') { $accounts = @("$env:COMPUTERNAME\$LauncherUser", $LauncherUser) }
  foreach ($account in $accounts) {
    try {
      return (New-Object Security.Principal.NTAccount($account)).Translate(
        [Security.Principal.SecurityIdentifier]
      ).Value
    }
    catch { }
  }
  throw "launcher_user_not_found"
}

function Assert-PlainPath {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$ErrorCode)
  $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw $ErrorCode }
  return $item
}

function Assert-ProtectedSourceFile {
  param([Parameter(Mandatory)][string]$Path)
  $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  $allowedWriters = @($currentSid, $AdministratorsSid, $SystemSid) | Select-Object -Unique
  $acl = Get-Acl -LiteralPath $Path
  try { $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value }
  catch { throw "launcher_source_owner_invalid" }
  if ($owner -notin $allowedWriters) { throw "launcher_source_owner_invalid" }
  $unsafe = [Security.AccessControl.FileSystemRights]::AppendData -bor `
    [Security.AccessControl.FileSystemRights]::WriteData -bor `
    [Security.AccessControl.FileSystemRights]::WriteAttributes -bor `
    [Security.AccessControl.FileSystemRights]::WriteExtendedAttributes -bor `
    [Security.AccessControl.FileSystemRights]::Delete -bor `
    [Security.AccessControl.FileSystemRights]::ChangePermissions -bor `
    [Security.AccessControl.FileSystemRights]::TakeOwnership
  foreach ($rule in @($acl.Access)) {
    if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
        ($rule.FileSystemRights -band $unsafe) -eq 0) { continue }
    try { $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value }
    catch { throw "launcher_source_acl_invalid" }
    if ($sid -notin $allowedWriters) { throw "launcher_source_unapproved_writer" }
  }
}

function Install-FileAtomic {
  param(
    [Parameter(Mandatory)][string]$StagedPath,
    [Parameter(Mandatory)][string]$DestinationPath
  )
  if (Test-Path -LiteralPath $DestinationPath -PathType Leaf) {
    $backup = "$DestinationPath.$([Guid]::NewGuid().ToString('N')).replace.bak"
    $oldHash = (Get-FileHash -LiteralPath $DestinationPath -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::Copy($DestinationPath, $backup, $false)
    if ((Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash.ToLowerInvariant() -cne $oldHash) {
      throw "launcher_backup_verification_failed"
    }
    [IO.File]::WriteAllText("$backup.sha256", $oldHash, (New-Object Text.UTF8Encoding($false)))
    foreach ($path in @($backup, "$backup.sha256")) {
      Set-LauncherFileAcl -Path $path -UserSid $launcherUserSid
      Assert-LauncherAcl -Path $path -UserSid $launcherUserSid
    }
    if ((Get-FileHash -LiteralPath $DestinationPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $oldHash) {
      throw "launcher_destination_changed"
    }
    [IO.File]::Replace($StagedPath, $DestinationPath, [NullString]::Value)
  }
  else { [IO.File]::Move($StagedPath, $DestinationPath) }
}

function Assert-LauncherGuardSource {
  param([Parameter(Mandatory)][string]$Path)
  # Pin reviewed code, including guard call sites; a version label cannot attest to executable behavior.
  $text = [IO.File]::ReadAllText($Path).Replace("`r`n", "`n")
  $sha = [Security.Cryptography.SHA256]::Create()
  try { $hash = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($text)))).Replace("-", "").ToLowerInvariant() }
  finally { $sha.Dispose() }
  if ($hash -cne "a1940251fce08604f24d70994fec85f4e395423806b3882391693ac033353dc5") { throw "launcher_maintenance_guard_unapproved" }
}

function Write-GuardInstallReceipt {
  param([Parameter(Mandatory)][string]$LauncherPath, [Parameter(Mandatory)][string]$UserSid,
    [Parameter(Mandatory)][string]$ExpectedHash)
  if ($LauncherPath -cne "C:\Program Files\Ruisheng\Launcher\start_ruisheng_local.ps1") {
    throw "launcher_guard_path_invalid"
  }
  Assert-LauncherGuardSource -Path $LauncherPath
  Assert-LauncherAcl -Path $LauncherPath -UserSid $UserSid
  if ((Get-FileHash -LiteralPath $LauncherPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ExpectedHash) {
    throw "launcher_installed_hash_changed"
  }
  $receiptPath = Join-Path (Split-Path -Parent $LauncherPath) "schema-upgrade-guard.json"
  $staged = "$receiptPath.$([Guid]::NewGuid().ToString('N')).tmp"
  $receipt = [ordered]@{
    schema_version = 1; guard_version = "bounded-schema-v1"; launcher = $LauncherPath
    launcher_sha256 = $ExpectedHash; installed_at = [DateTimeOffset]::UtcNow.ToString("o")
  }
  try {
    [IO.File]::WriteAllText($staged, ($receipt | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding($false)))
    Set-LauncherFileAcl -Path $staged -UserSid $UserSid
    Assert-LauncherAcl -Path $staged -UserSid $UserSid
    Install-FileAtomic -StagedPath $staged -DestinationPath $receiptPath
    Assert-LauncherAcl -Path $receiptPath -UserSid $UserSid
  }
  finally { Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue }
}

function Register-SerialRecoveryTask {
  param([Parameter(Mandatory)][string]$LauncherPath, [Parameter(Mandatory)][string]$UserSid,
    [Parameter(Mandatory)][string]$RuntimePath)
  Assert-LauncherGuardSource -Path $LauncherPath
  Assert-LauncherAcl -Path $LauncherPath -UserSid $UserSid
  $name = 'Ruisheng-Serial-Gateway-Recovery'
  $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if ($null -ne $existing) {
    $backup = Join-Path $AuditRoot ("serial-recovery-task-{0}.xml" -f [Guid]::NewGuid().ToString('N'))
    [IO.File]::WriteAllText($backup, (Export-ScheduledTask -TaskName $name), [Text.UTF8Encoding]::new($false))
    Set-RuntimeAuditFileAcl -Path $backup -UserSid $UserSid
  }
  $arguments = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -RecoverSerialGateway -NoUi -NoBrowser -StartupTimeoutSeconds 120' -f $LauncherPath
  $action = New-ScheduledTaskAction -Execute $RuntimePath -Argument $arguments
  $periodic = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
  $boot = New-ScheduledTaskTrigger -AtStartup
  $boot.Delay = 'PT90S'
  $principal = New-ScheduledTaskPrincipal -UserId $UserSid -LogonType S4U -RunLevel Highest
  $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
  Register-ScheduledTask -TaskName $name -Action $action -Trigger @($periodic,$boot) `
    -Principal $principal -Settings $settings -Force | Out-Null
}

function Set-LauncherDirectoryAcl {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$UserSid)
  $acl = New-Object Security.AccessControl.DirectorySecurity
  $acl.SetAccessRuleProtection($true, $false)
  $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier($AdministratorsSid)))
  $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor `
    [Security.AccessControl.InheritanceFlags]::ObjectInherit
  foreach ($sidValue in @($AdministratorsSid, $SystemSid)) {
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
      (New-Object Security.Principal.SecurityIdentifier($sidValue)),
      [Security.AccessControl.FileSystemRights]::FullControl,
      $inheritance,
      [Security.AccessControl.PropagationFlags]::None,
      [Security.AccessControl.AccessControlType]::Allow
    )
    [void]$acl.AddAccessRule($rule)
  }
  $readRule = New-Object Security.AccessControl.FileSystemAccessRule(
    (New-Object Security.Principal.SecurityIdentifier($UserSid)),
    [Security.AccessControl.FileSystemRights]::ReadAndExecute,
    $inheritance,
    [Security.AccessControl.PropagationFlags]::None,
    [Security.AccessControl.AccessControlType]::Allow
  )
  [void]$acl.AddAccessRule($readRule)
  if ($null -ne [IO.Directory].GetMethod(
      "SetAccessControl", [type[]]@([string], [Security.AccessControl.DirectorySecurity])
  )) { [IO.Directory]::SetAccessControl($Path, $acl) }
  else { Set-Acl -LiteralPath $Path -AclObject $acl }
}

function Set-LauncherFileAcl {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$UserSid)
  $acl = New-Object Security.AccessControl.FileSecurity
  $acl.SetAccessRuleProtection($true, $false)
  $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier($AdministratorsSid)))
  foreach ($sidValue in @($AdministratorsSid, $SystemSid)) {
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
      (New-Object Security.Principal.SecurityIdentifier($sidValue)),
      [Security.AccessControl.FileSystemRights]::FullControl,
      [Security.AccessControl.AccessControlType]::Allow
    )
    [void]$acl.AddAccessRule($rule)
  }
  $readRule = New-Object Security.AccessControl.FileSystemAccessRule(
    (New-Object Security.Principal.SecurityIdentifier($UserSid)),
    [Security.AccessControl.FileSystemRights]::ReadAndExecute,
    [Security.AccessControl.AccessControlType]::Allow
  )
  [void]$acl.AddAccessRule($readRule)
  if ($null -ne [IO.File].GetMethod(
      "SetAccessControl", [type[]]@([string], [Security.AccessControl.FileSecurity])
  )) { [IO.File]::SetAccessControl($Path, $acl) }
  else { Set-Acl -LiteralPath $Path -AclObject $acl }
}

function Set-RuntimeAuditDirectoryAcl {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$UserSid)
  $acl = New-Object Security.AccessControl.DirectorySecurity
  $acl.SetAccessRuleProtection($true, $false)
  $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier($AdministratorsSid)))
  $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor `
    [Security.AccessControl.InheritanceFlags]::ObjectInherit
  foreach ($sidValue in @($AdministratorsSid, $SystemSid, $UserSid) | Select-Object -Unique) {
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
      (New-Object Security.Principal.SecurityIdentifier($sidValue)),
      [Security.AccessControl.FileSystemRights]::FullControl,
      $inheritance,
      [Security.AccessControl.PropagationFlags]::None,
      [Security.AccessControl.AccessControlType]::Allow
    )
    [void]$acl.AddAccessRule($rule)
  }
  if ($null -ne [IO.Directory].GetMethod(
      "SetAccessControl", [type[]]@([string], [Security.AccessControl.DirectorySecurity])
  )) { [IO.Directory]::SetAccessControl($Path, $acl) }
  else { Set-Acl -LiteralPath $Path -AclObject $acl }
}

function Set-RuntimeAuditFileAcl {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$UserSid)
  $acl = New-Object Security.AccessControl.FileSecurity
  $acl.SetAccessRuleProtection($true, $false)
  $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier($AdministratorsSid)))
  foreach ($sidValue in @($AdministratorsSid, $SystemSid, $UserSid) | Select-Object -Unique) {
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
      (New-Object Security.Principal.SecurityIdentifier($sidValue)),
      [Security.AccessControl.FileSystemRights]::FullControl,
      [Security.AccessControl.AccessControlType]::Allow
    )
    [void]$acl.AddAccessRule($rule)
  }
  if ($null -ne [IO.File].GetMethod(
      "SetAccessControl", [type[]]@([string], [Security.AccessControl.FileSecurity])
  )) { [IO.File]::SetAccessControl($Path, $acl) }
  else { Set-Acl -LiteralPath $Path -AclObject $acl }
}

function Assert-LauncherAcl {
  param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$UserSid)
  $acl = Get-Acl -LiteralPath $Path
  if (-not $acl.AreAccessRulesProtected) { throw "launcher_acl_inheritance_enabled" }
  $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
  if ($owner -notin @($AdministratorsSid, $SystemSid)) { throw "launcher_acl_owner_invalid" }
  $unsafe = [Security.AccessControl.FileSystemRights]::CreateFiles -bor `
    [Security.AccessControl.FileSystemRights]::CreateDirectories -bor `
    [Security.AccessControl.FileSystemRights]::AppendData -bor `
    [Security.AccessControl.FileSystemRights]::WriteData -bor `
    [Security.AccessControl.FileSystemRights]::WriteAttributes -bor `
    [Security.AccessControl.FileSystemRights]::WriteExtendedAttributes -bor `
    [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor `
    [Security.AccessControl.FileSystemRights]::Delete -bor `
    [Security.AccessControl.FileSystemRights]::ChangePermissions -bor `
    [Security.AccessControl.FileSystemRights]::TakeOwnership
  $userCanRead = $false
  foreach ($rule in @($acl.Access)) {
    if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) {
      throw "launcher_acl_invalid"
    }
    $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
    if ($sid -notin @($AdministratorsSid, $SystemSid) -and
        ($rule.FileSystemRights -band $unsafe) -ne 0) {
      throw "launcher_acl_unapproved_writer"
    }
    if ($sid -eq $UserSid -and
        ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::ReadAndExecute) -eq
          [Security.AccessControl.FileSystemRights]::ReadAndExecute) {
      $userCanRead = $true
    }
  }
  if (-not $userCanRead) { throw "launcher_user_read_access_missing" }
}

function New-RuishengIcon {
  param([Parameter(Mandatory)][string]$Path)
  Add-Type -AssemblyName System.Drawing
  $bitmap = New-Object Drawing.Bitmap(64, 64)
  $graphics = [Drawing.Graphics]::FromImage($bitmap)
  $brush = New-Object Drawing.SolidBrush([Drawing.Color]::FromArgb(18, 92, 73))
  $accent = New-Object Drawing.SolidBrush([Drawing.Color]::FromArgb(238, 190, 62))
  $font = New-Object Drawing.Font("Segoe UI", 36, [Drawing.FontStyle]::Bold, [Drawing.GraphicsUnit]::Pixel)
  $format = New-Object Drawing.StringFormat
  $format.Alignment = [Drawing.StringAlignment]::Center
  $format.LineAlignment = [Drawing.StringAlignment]::Center
  try {
    $graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.FillRectangle($brush, 0, 0, 64, 64)
    $graphics.FillRectangle($accent, 0, 52, 64, 12)
    $graphics.DrawString("R", $font, [Drawing.Brushes]::White, [Drawing.RectangleF]::new(0, -3, 64, 58), $format)
    $icon = [Drawing.Icon]::FromHandle($bitmap.GetHicon())
    try {
      $stream = [IO.File]::Open($Path, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
      try { $icon.Save($stream) } finally { $stream.Dispose() }
    }
    finally { $icon.Dispose() }
  }
  finally {
    $format.Dispose(); $font.Dispose(); $accent.Dispose(); $brush.Dispose()
    $graphics.Dispose(); $bitmap.Dispose()
  }
}

Assert-Administrator
$installMutex = New-Object Threading.Mutex($false, "Global\RuishengDesktopLauncherInstall")
$ownsInstallMutex = $false
try {
  try { $ownsInstallMutex = $installMutex.WaitOne(0) }
  catch [Threading.AbandonedMutexException] { $ownsInstallMutex = $true }
  if (-not $ownsInstallMutex) { throw "launcher_install_in_progress" }

if (-not ([IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')).Equals(
    "C:\Program Files\Ruisheng\Launcher", [StringComparison]::OrdinalIgnoreCase
)) { throw "launcher_install_root_not_approved" }
if (-not ([IO.Path]::GetFullPath($AuditRoot).TrimEnd('\')).Equals(
    "C:\Ruisheng\launcher-audit", [StringComparison]::OrdinalIgnoreCase
)) { throw "launcher_audit_root_not_approved" }
if (-not $ShortcutName) {
  # Generate "Ruisheng Monitoring System" in Chinese without requiring a UTF-8 BOM for PowerShell 5.1.
  $ShortcutName = (-join @(
      [char]0x6DA6, [char]0x76DB, [char]0x76D1, [char]0x63A7,
      [char]0x7CFB, [char]0x7EDF
  )) + ".lnk"
}
if ($ShortcutName -notmatch '^[^\\/:*?"<>\x00-\x1f]{1,120}\.lnk$') {
  throw "shortcut_name_invalid"
}
$source = Assert-PlainPath -Path $SourceLauncherPath -ErrorCode "launcher_source_linked"
if (-not $source.Name.Equals("start_ruisheng_local.ps1", [StringComparison]::OrdinalIgnoreCase)) {
  throw "launcher_source_name_invalid"
}
Assert-ProtectedSourceFile -Path $source.FullName
$launcherUserSid = Resolve-LauncherUserSid

# Resolve the explicit desktop runtime before changing installed files.
$powerShellPath = "C:\Program Files\PowerShell\7\pwsh.exe"
if (-not (Test-Path -LiteralPath $powerShellPath -PathType Leaf)) { throw "pwsh_missing" }
$runtimeMajor = & $powerShellPath -NoLogo -NoProfile -NonInteractive -Command '$PSVersionTable.PSVersion.Major'
if ($LASTEXITCODE -ne 0 -or [int]$runtimeMajor.Trim() -ne 7) { throw "pwsh_version_invalid" }

if (Test-Path -LiteralPath $AuditRoot) {
  $auditItem = Assert-PlainPath -Path $AuditRoot -ErrorCode "launcher_audit_root_linked"
  if (-not $auditItem.PSIsContainer) { throw "launcher_audit_root_invalid" }
}
else { [void](New-Item -ItemType Directory -Path $AuditRoot -Force) }
Set-RuntimeAuditDirectoryAcl -Path $AuditRoot -UserSid $launcherUserSid
$auditLockPath = Join-Path $AuditRoot ".desktop-launcher-audit.lock"
if (-not (Test-Path -LiteralPath $auditLockPath -PathType Leaf)) {
  $auditLockStream = [IO.File]::Open(
    $auditLockPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None
  )
  $auditLockStream.Dispose()
}
$auditLockItem = Assert-PlainPath -Path $auditLockPath -ErrorCode "launcher_audit_lock_linked"
if ($auditLockItem.PSIsContainer) { throw "launcher_audit_lock_invalid" }
Set-RuntimeAuditFileAcl -Path $auditLockPath -UserSid $launcherUserSid

if (Test-Path -LiteralPath $InstallRoot) {
  $installItem = Assert-PlainPath -Path $InstallRoot -ErrorCode "launcher_install_root_linked"
  if (-not $installItem.PSIsContainer) { throw "launcher_install_root_invalid" }
}
else { [void](New-Item -ItemType Directory -Path $InstallRoot -Force) }
Set-LauncherDirectoryAcl -Path $InstallRoot -UserSid $launcherUserSid
Assert-LauncherAcl -Path $InstallRoot -UserSid $launcherUserSid
$installLockPath = Join-Path $InstallRoot ".install.lock"
if (-not (Test-Path -LiteralPath $installLockPath -PathType Leaf)) {
  try {
    $createdLock = [IO.File]::Open(
      $installLockPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None
    )
    $createdLock.Dispose()
  }
  catch [IO.IOException] { }
}
$lockItem = Assert-PlainPath -Path $installLockPath -ErrorCode "launcher_install_lock_linked"
if ($lockItem.PSIsContainer) { throw "launcher_install_lock_invalid" }
Set-LauncherFileAcl -Path $installLockPath -UserSid $launcherUserSid
Assert-LauncherAcl -Path $installLockPath -UserSid $launcherUserSid
try {
  $installLock = [IO.File]::Open(
    $installLockPath, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None
  )
}
catch [IO.IOException] { throw "launcher_install_in_progress" }
try {

$installedLauncher = Join-Path $InstallRoot "start_ruisheng_local.ps1"
$iconPath = Join-Path $InstallRoot "ruisheng.ico"
$guardReceiptPath = Join-Path $InstallRoot "schema-upgrade-guard.json"
foreach ($path in @($installedLauncher, $iconPath, $guardReceiptPath)) {
  if (Test-Path -LiteralPath $path) {
    $existing = Assert-PlainPath -Path $path -ErrorCode "launcher_payload_linked"
    if ($existing.PSIsContainer) { throw "launcher_payload_invalid" }
    Assert-LauncherAcl -Path $path -UserSid $launcherUserSid
  }
}
$stagedLauncher = Join-Path $InstallRoot (".launcher-{0}.tmp" -f [Guid]::NewGuid().ToString("N"))
$stagedIcon = Join-Path $InstallRoot (".icon-{0}.tmp" -f [Guid]::NewGuid().ToString("N"))
try {
  $sourceStream = [IO.File]::Open(
    $source.FullName, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read
  )
  try {
    Assert-ProtectedSourceFile -Path $source.FullName
    $destinationStream = [IO.File]::Open(
      $stagedLauncher, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None
    )
    try { $sourceStream.CopyTo($destinationStream) }
    finally { $destinationStream.Dispose() }
    $sourceStream.Position = 0
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
      $sourceHash = ([BitConverter]::ToString($sha256.ComputeHash($sourceStream))).Replace("-", "")
    }
    finally { $sha256.Dispose() }
  }
  finally { $sourceStream.Dispose() }
  if ($sourceHash -cne (Get-FileHash -LiteralPath $stagedLauncher -Algorithm SHA256).Hash) {
    throw "launcher_copy_verification_failed"
  }
  Assert-LauncherGuardSource -Path $stagedLauncher
  New-RuishengIcon -Path $stagedIcon
  Set-LauncherFileAcl -Path $stagedLauncher -UserSid $launcherUserSid
  Set-LauncherFileAcl -Path $stagedIcon -UserSid $launcherUserSid
  Install-FileAtomic -StagedPath $stagedLauncher -DestinationPath $installedLauncher
  Install-FileAtomic -StagedPath $stagedIcon -DestinationPath $iconPath
}
finally {
  Remove-Item -LiteralPath $stagedLauncher -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $stagedIcon -Force -ErrorAction SilentlyContinue
}
foreach ($path in @($installedLauncher, $iconPath)) {
  Set-LauncherFileAcl -Path $path -UserSid $launcherUserSid
  Assert-LauncherAcl -Path $path -UserSid $launcherUserSid
}
Assert-LauncherAcl -Path $InstallRoot -UserSid $launcherUserSid

if (-not $DesktopPath) {
  $currentSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  if ($currentSid -ne $launcherUserSid) { throw "desktop_path_required_for_different_user" }
  $DesktopPath = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
}
$desktop = Assert-PlainPath -Path $DesktopPath -ErrorCode "desktop_path_linked"
if (-not $desktop.PSIsContainer) { throw "desktop_path_invalid" }
$shortcutPath = Join-Path $desktop.FullName $ShortcutName
if (Test-Path -LiteralPath $shortcutPath) {
  $existingShortcut = Assert-PlainPath -Path $shortcutPath -ErrorCode "shortcut_target_linked"
  if ($existingShortcut.PSIsContainer) { throw "shortcut_target_invalid" }
}
$stagedShortcutPath = Join-Path $desktop.FullName (
  ".ruisheng-launcher-{0}.tmp.lnk" -f [Guid]::NewGuid().ToString("N")
)
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($stagedShortcutPath)
$shortcut.TargetPath = $powerShellPath
$shortcut.Arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $installedLauncher
$shortcut.WorkingDirectory = $InstallRoot
$shortcut.IconLocation = "$iconPath,0"
$shortcut.Description = "Start Ruisheng Monitoring System"
$shortcut.WindowStyle = 1
$shortcut.Save()
try { Install-FileAtomic -StagedPath $stagedShortcutPath -DestinationPath $shortcutPath }
finally { Remove-Item -LiteralPath $stagedShortcutPath -Force -ErrorAction SilentlyContinue }

if (-not (Test-Path -LiteralPath $shortcutPath -PathType Leaf)) { throw "shortcut_creation_failed" }
Write-GuardInstallReceipt -LauncherPath $installedLauncher -UserSid $launcherUserSid -ExpectedHash $sourceHash.ToLowerInvariant()
if ($EnableSerialRecovery) {
  Register-SerialRecoveryTask -LauncherPath $installedLauncher -UserSid $launcherUserSid -RuntimePath $powerShellPath
}
$receipt = [ordered]@{
  status = "installed"
  launcher = $installedLauncher
  launcher_sha256 = (Get-FileHash -LiteralPath $installedLauncher -Algorithm SHA256).Hash.ToLowerInvariant()
  icon = $iconPath
  shortcut = $shortcutPath
  user_sid = $launcherUserSid
  requires_elevation_to_run = $false
  startup_task_changed = [bool]$EnableSerialRecovery
}
Write-Output ($receipt | ConvertTo-Json -Compress)
}
finally { $installLock.Dispose() }
}
finally {
  if ($ownsInstallMutex) { try { $installMutex.ReleaseMutex() } catch { } }
  $installMutex.Dispose()
}
