[CmdletBinding()]
param(
  [ValidateSet('Plan', 'Create', 'Status', 'ShowCredential')][string]$Action = 'Plan',
  [string]$Target = 'lenovo@100.109.90.21',
  [string]$ExpectedComputer = 'WIN-OAUCM8UQUGH',
  [string]$SiteRoot = 'C:\Ruisheng\candidates\site-deploy-20260831.1',
  [string]$SiteId = 'site-win-oaucm8uqugh',
  [string]$UserName = 'rs_admin',
  [string]$ExpectedCandidateId = '',
  [string]$CredentialDirectory = '',
  [switch]$Approved,
  [switch]$LibraryOnly
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Assert-BootstrapPath {
  param([string]$Path)
  if ($Path -notmatch '^[A-Za-z]:\\[^:\x00-\x1f]*$' -or $Path -match '[\\/]\.\.?([\\/]|$)') {
    throw 'bootstrap_path_invalid'
  }
  $cursor = [IO.Path]::GetFullPath($Path)
  while ($cursor) {
    if (Test-Path -LiteralPath $cursor) {
      $item = Get-Item -LiteralPath $cursor -Force
      if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'bootstrap_path_reparse'
      }
    }
    $parent = [IO.Directory]::GetParent($cursor)
    $cursor = if ($null -eq $parent) { '' } else { $parent.FullName }
  }
}

function Assert-BootstrapAcl {
  param([string]$Path, [switch]$Credential, [switch]$SystemTool)
  Assert-BootstrapPath $Path
  Assert-BootstrapAncestors $Path
  $acl = Get-Acl -LiteralPath $Path
  $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  $allowed = @($sid, 'S-1-5-18')
  if (-not $Credential) { $allowed += 'S-1-5-32-544' }
  if ($SystemTool -and -not $Credential) {
    $allowed += 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'
  }
  if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $allowed) {
    throw 'bootstrap_acl_owner'
  }
  if ($Credential -and -not $acl.AreAccessRulesProtected) { throw 'bootstrap_acl_inherited' }
  $seen = @{}
  foreach ($rule in @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))) {
    $identity = $rule.IdentityReference.Value
    if ($Credential) {
      if ($rule.AccessControlType -ne 'Allow' -or $identity -notin $allowed -or
          ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne
            [Security.AccessControl.FileSystemRights]::FullControl -or
          $rule.PropagationFlags -ne [Security.AccessControl.PropagationFlags]::None) {
        throw 'bootstrap_credential_acl'
      }
      $seen[$identity] = $true
    }
    elseif ($rule.AccessControlType -eq 'Allow' -and $identity -notin $allowed -and
      ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]'WriteData,AppendData,WriteAttributes,WriteExtendedAttributes,Delete,DeleteSubdirectoriesAndFiles,ChangePermissions,TakeOwnership') -ne 0) {
      throw 'bootstrap_acl_writable'
    }
  }
  if ($Credential -and (-not $seen.ContainsKey($sid) -or -not $seen.ContainsKey('S-1-5-18'))) {
    throw 'bootstrap_credential_acl_missing'
  }
}

function Assert-BootstrapAncestors {
  param([string]$Path)
  $allowed = @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value, 'S-1-5-18',
    'S-1-5-32-544', 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
  $cursor = [IO.Directory]::GetParent([IO.Path]::GetFullPath($Path))
  while ($null -ne $cursor) {
    $acl = Get-Acl -LiteralPath $cursor.FullName
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $allowed) { throw 'bootstrap_parent_owner' }
    foreach ($rule in @($acl.Access)) {
      if (($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0) { continue }
      if ($rule.AccessControlType -eq 'Allow' -and
          ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]'DeleteSubdirectoriesAndFiles,Delete,ChangePermissions,TakeOwnership') -ne 0 -and
          $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $allowed) {
        throw 'bootstrap_parent_replaceable'
      }
    }
    $cursor = $cursor.Parent
  }
}

function New-BootstrapAcl {
  param([switch]$Directory)
  $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User
  $acl = if ($Directory) { New-Object Security.AccessControl.DirectorySecurity } else {
    New-Object Security.AccessControl.FileSecurity
  }
  $acl.SetOwner($sid)
  $acl.SetAccessRuleProtection($true, $false)
  foreach ($identity in @($sid, (New-Object Security.Principal.SecurityIdentifier('S-1-5-18')))) {
    $inheritance = if ($Directory) { [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit' } else {
      [Security.AccessControl.InheritanceFlags]::None
    }
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
      $identity, 'FullControl', $inheritance, 'None', 'Allow'
    )
    [void]$acl.AddAccessRule($rule)
  }
  return $acl
}

function Initialize-BootstrapCredentialDirectory {
  param([string]$Path)
  Assert-BootstrapPath $Path
  if (-not (Test-Path -LiteralPath $Path)) {
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw 'bootstrap_parent_missing' }
    # .NET Framework can atomically create with ACL; .NET on PS7 uses the ACL extension API.
    $security = New-BootstrapAcl -Directory
    if ($PSVersionTable.PSVersion.Major -lt 6) { [void][IO.Directory]::CreateDirectory($Path, $security) }
    else { [void][IO.FileSystemAclExtensions]::Create((New-Object IO.DirectoryInfo($Path)), $security) }
  }
  Assert-BootstrapAcl $Path -Credential
}

function New-BootstrapPassword {
  $bytes = New-Object byte[] 32
  $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $rng.GetBytes($bytes)
    $alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-'
    return -join @($bytes | ForEach-Object { $alphabet[[int]$_ -band 63] })
  }
  finally { [Array]::Clear($bytes, 0, $bytes.Length); $rng.Dispose() }
}

function Read-BootstrapCredential {
  param([string]$Path)
  Assert-BootstrapAcl (Split-Path -Parent $Path) -Credential
  Assert-BootstrapAcl $Path -Credential
  $stream = [IO.File]::Open($Path, 'Open', 'Read', 'Read')
  try {
    if ($stream.Length -gt 16384 -or $stream.Length -lt 1) { throw 'bootstrap_credential_size' }
    $bytes = New-Object byte[] ([int]$stream.Length)
    if ($stream.Read($bytes, 0, $bytes.Length) -ne $bytes.Length) { throw 'bootstrap_credential_read' }
  }
  finally { $stream.Dispose() }
  try {
    Add-Type -AssemblyName System.Security
    $clear = [Security.Cryptography.ProtectedData]::Unprotect(
      $bytes, [Text.Encoding]::UTF8.GetBytes('ruisheng-admin-bootstrap-v1'), 'CurrentUser'
    )
    $record = [Text.Encoding]::UTF8.GetString($clear) | ConvertFrom-Json
    if (@($record.PSObject.Properties).Count -ne 9 -or $record.schema_version -ne 1 -or
        [string]$record.password -cnotmatch '^[A-Za-z0-9_-]{32}$' -or
        [string]$record.operation_id -cnotmatch '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') {
      throw 'bootstrap_credential_schema'
    }
    return $record
  }
  catch { throw 'bootstrap_credential_unavailable' }
  finally {
    if ($null -ne $clear) { [Array]::Clear($clear, 0, $clear.Length) }
    [Array]::Clear($bytes, 0, $bytes.Length)
  }
}

function Save-BootstrapCredential {
  param([string]$Path, $Record)
  Assert-BootstrapAcl (Split-Path -Parent $Path) -Credential
  Assert-BootstrapPath $Path
  Add-Type -AssemblyName System.Security
  $clear = [Text.Encoding]::UTF8.GetBytes(($Record | ConvertTo-Json -Compress))
  try {
    $cipher = [Security.Cryptography.ProtectedData]::Protect(
      $clear, [Text.Encoding]::UTF8.GetBytes('ruisheng-admin-bootstrap-v1'), 'CurrentUser'
    )
    $roundTrip = [Security.Cryptography.ProtectedData]::Unprotect(
      $cipher, [Text.Encoding]::UTF8.GetBytes('ruisheng-admin-bootstrap-v1'), 'CurrentUser'
    )
    if ([Convert]::ToBase64String($clear) -cne [Convert]::ToBase64String($roundTrip)) {
      throw 'bootstrap_credential_roundtrip'
    }
    $stream = [IO.File]::Open($Path, 'CreateNew', 'Write', 'None')
    try { $stream.Write($cipher, 0, $cipher.Length); $stream.Flush($true) }
    finally { $stream.Dispose() }
    Set-Acl -LiteralPath $Path -AclObject (New-BootstrapAcl)
    $saved = Read-BootstrapCredential $Path
    foreach ($property in $Record.PSObject.Properties) {
      if ([string]$saved.($property.Name) -cne [string]$property.Value) { throw 'bootstrap_credential_roundtrip' }
    }
    return $saved
  }
  catch { throw 'bootstrap_credential_not_dispatched' }
  finally {
    [Array]::Clear($clear, 0, $clear.Length)
    if ($null -ne $roundTrip) { [Array]::Clear($roundTrip, 0, $roundTrip.Length) }
  }
}

function Show-BootstrapCredential {
  param([string]$Path)
  if (-not [Environment]::UserInteractive -or [Console]::IsInputRedirected -or
      $env:SSH_CONNECTION -or [Environment]::GetCommandLineArgs() -contains '-NonInteractive') {
    throw 'bootstrap_interactive_window_required'
  }
  $record = Read-BootstrapCredential $Path
  Add-Type -AssemblyName System.Windows.Forms
  $form = New-Object Windows.Forms.Form
  try {
    $form.Text = 'Ruisheng administrator credential'
    $form.Width = 580; $form.Height = 220
    $form.StartPosition = 'CenterScreen'; $form.KeyPreview = $true
    $form.FormBorderStyle = 'FixedDialog'; $form.MaximizeBox = $false; $form.MinimizeBox = $false
    $label = New-Object Windows.Forms.Label
    $label.AutoSize = $false; $label.Left = 20; $label.Top = 20; $label.Width = 530; $label.Height = 110
    $label.Text = "Site: $($record.site_id)`r`nAccount: $($record.user_name)`r`nPassword: $($record.password)"
    $label.UseMnemonic = $false
    $form.Controls.Add($label)
    $button = New-Object Windows.Forms.Button
    $button.Text = 'Close'; $button.Left = 450; $button.Top = 130
    $button.DialogResult = [Windows.Forms.DialogResult]::OK
    $form.Controls.Add($button); $form.AcceptButton = $button; $form.CancelButton = $button
    $form.Add_KeyDown({ if ($_.Control -and $_.KeyCode -eq 'C') { $_.Handled = $true; $_.SuppressKeyPress = $true } })
    [void]$form.ShowDialog()
  }
  finally { $form.Dispose() }
}

function Initialize-BootstrapNative {
  if ('RuishengBootstrapNative' -as [type]) { return }
  Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.Diagnostics;
using System.Threading.Tasks;
public sealed class RuishengBootstrapResult {
  public int ExitCode; public string Output; public bool HasError;
}
public static class RuishengBootstrapNative {
  static async Task<byte[]> ReadBounded(Stream stream, int limit) {
    using (var buffer = new MemoryStream()) {
      var chunk = new byte[4096]; int count;
      while ((count = await stream.ReadAsync(chunk, 0, chunk.Length)) > 0) {
        if (buffer.Length + count > limit) throw new IOException("output_limit");
        buffer.Write(chunk, 0, count);
      }
      return buffer.ToArray();
    }
  }
  public static RuishengBootstrapResult Run(string file, string args, byte[] input, int timeout) {
    using (var process = new Process()) {
      process.StartInfo = new ProcessStartInfo(file, args) { UseShellExecute = false,
        CreateNoWindow = true, RedirectStandardInput = true, RedirectStandardOutput = true,
        RedirectStandardError = true };
      var clock = Stopwatch.StartNew();
      try {
        if (!process.Start()) throw new IOException("start_failed");
        var stdout = ReadBounded(process.StandardOutput.BaseStream, 65536);
        var stderr = ReadBounded(process.StandardError.BaseStream, 65536);
        var write = process.StandardInput.BaseStream.WriteAsync(input, 0, input.Length);
        while (!write.IsCompleted) {
          if (clock.ElapsedMilliseconds > timeout) throw new IOException("timeout");
          System.Threading.Thread.Sleep(10);
        }
        write.GetAwaiter().GetResult(); process.StandardInput.Close();
        while (!process.WaitForExit(20)) {
          if (stdout.IsFaulted || stderr.IsFaulted || clock.ElapsedMilliseconds > timeout)
            throw new IOException("transport_unknown");
        }
        int remaining = Math.Max(1, timeout - (int)clock.ElapsedMilliseconds);
        if (!Task.WaitAll(new Task[] { stdout, stderr }, remaining)) throw new IOException("timeout");
        return new RuishengBootstrapResult { ExitCode = process.ExitCode,
          Output = new UTF8Encoding(false, true).GetString(stdout.Result), HasError = stderr.Result.Length != 0 };
      } catch { throw new IOException("bootstrap_transport_unknown"); }
      finally { try { if (!process.HasExited) { process.Kill(); process.WaitForExit(2000); } } catch {} }
    }
  }
}
'@
}

function ConvertTo-BootstrapNativeArgument {
  param([AllowEmptyString()][string]$Value)
  if ($Value -and $Value -notmatch '[\s"]') { return $Value }
  return '"' + [regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}

function Invoke-BootstrapNative {
  param([string]$File, [string[]]$Arguments, [byte[]]$InputBytes = @(), [int]$Timeout = 60000)
  Initialize-BootstrapNative
  $line = (@($Arguments | ForEach-Object { ConvertTo-BootstrapNativeArgument $_ }) -join ' ')
  return [RuishengBootstrapNative]::Run($File, $line, $InputBytes, $Timeout)
}

function Assert-BootstrapReceipt {
  param($Result, [string]$RequestedAction, [string]$Operation, [string]$Site, [string]$Account)
  $statuses = switch ($RequestedAction) {
    'Plan' { @('planned','unknown') }
    'Create' { @('created','rejected','unknown') }
    'Status' { @('confirmed','empty','conflict','rejected','unknown') }
    default { throw 'bootstrap_action_invalid' }
  }
  $keys = @('operation_id','site_id','user_name','status')
  if ($RequestedAction -eq 'Plan' -and $Result.status -ceq 'planned') {
    $keys += @('candidate_id','computer','image_authentication')
  }
  if ($null -eq $Result -or @($Result.PSObject.Properties).Count -ne $keys.Count) { throw 'bootstrap_receipt_invalid' }
  foreach ($property in $Result.PSObject.Properties) {
    if ($property.Name -cnotin $keys -or $property.Value -isnot [string]) { throw 'bootstrap_receipt_invalid' }
  }
  if ($Result.operation_id -cne $Operation -or $Result.site_id -cne $Site -or
      $Result.user_name -cne $Account -or $Result.status -cnotin $statuses) { throw 'bootstrap_receipt_invalid' }
}

function Assert-NoFullUpgradeMaintenance {
  param([Parameter(Mandatory)][string]$SiteRoot)
  $stateDirectory = Join-Path $SiteRoot ".remote-maintenance-state"
  $path = Join-Path $stateDirectory "full-upgrade-maintenance.json"
  try { $marker = Get-Item -LiteralPath $path -Force -ErrorAction Stop }
  catch [System.Management.Automation.ItemNotFoundException] { return }
  $allowed = @([Security.Principal.WindowsIdentity]::GetCurrent().User.Value, "S-1-5-18", "S-1-5-32-544")
  foreach ($entry in @($SiteRoot, $stateDirectory, $path)) {
    $item = Get-Item -LiteralPath $entry -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        ($entry -eq $path -and ($item.PSIsContainer -or $item.Length -gt 16KB))) {
      throw "full_upgrade_maintenance_invalid"
    }
    $acl = Get-Acl -LiteralPath $entry
    if (($item.PSIsContainer -and -not $acl.AreAccessRulesProtected) -or
        $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $allowed) {
      throw "full_upgrade_maintenance_acl_invalid"
    }
    foreach ($rule in @($acl.Access)) {
      $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
      $write = [Security.AccessControl.FileSystemRights]'Write,Delete,DeleteSubdirectoriesAndFiles,ChangePermissions,TakeOwnership'
      if ($rule.AccessControlType -eq "Allow" -and ($rule.FileSystemRights -band $write) -ne 0 -and
          $sid -notin $allowed) { throw "full_upgrade_maintenance_acl_invalid" }
    }
  }
  try {
    $json = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey("DateKind")) {
      $state = $json | ConvertFrom-Json -DateKind String
    }
    else { $state = $json | ConvertFrom-Json }
  }
  catch { throw "full_upgrade_maintenance_invalid" }
  $keys = @("schema_version", "operation_id", "site_root", "status", "source_identity", "candidate_identity",
    "source_head", "target_head", "journal_path", "updated_at")
  if ($state -isnot [PSCustomObject] -or @($state.PSObject.Properties).Count -ne $keys.Count -or
      ($state.schema_version -isnot [int] -and $state.schema_version -isnot [long]) -or
      $state.schema_version -ne 1) { throw "full_upgrade_maintenance_invalid" }
  foreach ($key in $keys) {
    if (@($state.PSObject.Properties.Name) -cnotcontains $key -or
        ($key -ne "schema_version" -and $state.$key -isnot [string])) { throw "full_upgrade_maintenance_invalid" }
  }
  if ($state.site_root -cne $SiteRoot -or
      $state.operation_id -cnotmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -or
      $state.source_identity -cnotmatch '^sha256:[0-9a-f]{64}$' -or
      $state.candidate_identity -cnotmatch '^sha256:[0-9a-f]{64}$' -or
      $state.source_head -cne "0012_alarm_notification_runtime" -or
      $state.target_head -cne "0013_serial_polling_profile" -or
      $state.journal_path -cne (Join-Path $stateDirectory "full-upgrade-$($state.operation_id).json") -or
      $state.status -cnotin @("active", "committed", "rolled_back")) { throw "full_upgrade_maintenance_invalid" }
  [DateTimeOffset]$updatedAt = [DateTimeOffset]::MinValue
  if (-not [DateTimeOffset]::TryParseExact($state.updated_at, "o", [Globalization.CultureInfo]::InvariantCulture,
      [Globalization.DateTimeStyles]::None, [ref]$updatedAt)) { throw "full_upgrade_maintenance_invalid" }
  if ($state.status -ceq "active") { throw "full_upgrade_maintenance_active" }
}

function Get-BootstrapRemoteScript {
  $shared = @('Assert-BootstrapPath', 'Assert-BootstrapAcl', 'Assert-BootstrapAncestors', 'Assert-BootstrapReceipt', 'Initialize-BootstrapNative',
    'ConvertTo-BootstrapNativeArgument', 'Invoke-BootstrapNative', 'Assert-NoFullUpgradeMaintenance')
  $definitions = -join @($shared | ForEach-Object { "function $_ {`n$((Get-Item Function:\$_).Definition)`n}`n" })
  return $definitions + @'
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$deadline = [DateTimeOffset]::UtcNow.AddSeconds(50)
$handles = New-Object Collections.ArrayList
$heldLock = $null
$lockPath = ''
$cliDispatched = $false
$cliFinished = $false
$request = $null
function Invoke-Fixed {
  param([string]$File, [string[]]$Arguments, [byte[]]$Bytes = @())
  $remaining = [int]($deadline - [DateTimeOffset]::UtcNow).TotalMilliseconds
  if ($remaining -le 0) { throw 'bootstrap_timeout' }
  return Invoke-BootstrapNative $File $Arguments $Bytes $remaining
}
function Read-ProtectedBytes {
  param([string]$Path, [int]$Limit = 65536)
  Assert-BootstrapAcl $Path
  $stream = [IO.File]::Open($Path, 'Open', 'Read', 'Read')
  [void]$handles.Add($stream)
  if ($stream.Length -gt $Limit -or $stream.Length -lt 1) { throw 'bootstrap_input_size' }
  $bytes = New-Object byte[] ([int]$stream.Length)
  if ($stream.Read($bytes, 0, $bytes.Length) -ne $bytes.Length) { throw 'bootstrap_input_changed' }
  return ,$bytes
}
function Read-ProtectedText {
  param([string]$Path, [int]$Limit = 65536)
  return (New-Object Text.UTF8Encoding($false, $true)).GetString((Read-ProtectedBytes $Path $Limit))
}
function Invoke-FixedDocker {
  param([string[]]$Arguments, [byte[]]$Bytes = @())
  return Invoke-Fixed 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' `
    (@('--host', 'npipe:////./pipe/dockerDesktopLinuxEngine') + $Arguments) $Bytes
}
function Get-SafeContainer {
  param([string]$Name)
  $result = Invoke-FixedDocker @('inspect', '--format', '{{json .}}', $Name)
  if ($result.ExitCode -ne 0 -or $result.HasError) { throw 'bootstrap_container_unavailable' }
  return $result.Output | ConvertFrom-Json
}
function Invoke-BootstrapSignatureCheck {
  param([string]$Keygen, [string]$Signers, [string]$SumsPath, [string]$SignaturePath)
  $cmd = 'C:\Windows\System32\cmd.exe'
  Assert-BootstrapAcl $Keygen -SystemTool
  Assert-BootstrapAcl $cmd -SystemTool
  foreach ($path in @($Keygen, $Signers, $SumsPath, $SignaturePath)) {
    if ($path -cnotmatch '^[A-Za-z]:\\[A-Za-z0-9._\\-]+$') { throw 'bootstrap_signature_path_invalid' }
  }
  # Target OpenSSH requires file stdin; these protected files remain held read-only.
  $line = "$Keygen -Y verify -f $Signers -I ruisheng-release -n ruisheng-candidate-v1 -s $SignaturePath < $SumsPath"
  return Invoke-Fixed $cmd @('/d', '/q', '/v:off', '/c', $line)
}
try {
  if ([Text.Encoding]::UTF8.GetByteCount($BootstrapRequestJson) -gt 4096) { throw 'bootstrap_request_limit' }
  $request = $BootstrapRequestJson | ConvertFrom-Json
  if (@($request.PSObject.Properties).Count -ne 10 -or $request.schema_version -ne 1 -or
      [string]$request.action -cnotmatch '^(Plan|Create|Status)$' -or
      [string]$request.site_id -cnotmatch '^site-[a-z0-9][a-z0-9-]{0,43}$' -or
      [string]$request.user_name -cnotmatch '^[a-zA-Z][a-zA-Z0-9_]{3,29}$' -or
      [string]$request.operation_id -cnotmatch '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') {
    throw 'bootstrap_request_invalid'
  }
  if ([string]$request.expected_computer -cne $env:COMPUTERNAME -or
      [string]$request.target -cne "$($env:USERNAME)@$($env:SSH_CONNECTION.Split(' ')[2])") {
    throw 'bootstrap_target_mismatch'
  }
  $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
  if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'bootstrap_target_not_admin' }
  $site = [string]$request.site_root
  Assert-BootstrapAcl $site
  $state = Join-Path $site '.remote-maintenance-state'
  Assert-BootstrapAcl $state
  $lockPath = Join-Path $state '.remote-maintenance.lock'
  if ($request.action -eq 'Create') {
    Assert-NoFullUpgradeMaintenance -SiteRoot $site
    # Other maintenance tools see the same lock. Never reclaim unknown/stale records automatically.
    $record = [ordered]@{ schema_version = 1; lock_name = 'shared-maintenance';
      operation_id = [string]$request.operation_id; action = 'admin-bootstrap'; pid = $PID;
      process_started_at = (Get-Process -Id $PID).StartTime.ToUniversalTime().ToString('o');
      target = [string]$env:COMPUTERNAME; acquired_at = [DateTimeOffset]::UtcNow.ToString('o');
      expires_at = [DateTimeOffset]::UtcNow.AddSeconds(120).ToString('o') }
    $heldLock = [IO.File]::Open($lockPath, 'CreateNew', 'ReadWrite', 'None')
    $lockBytes = [Text.Encoding]::UTF8.GetBytes(($record | ConvertTo-Json -Compress))
    $heldLock.Write($lockBytes, 0, $lockBytes.Length); $heldLock.Flush($true)
    if (Test-Path -LiteralPath (Join-Path $site '.remote-hotfix.lock')) { throw 'bootstrap_legacy_lock_present' }
    Assert-NoFullUpgradeMaintenance -SiteRoot $site
  }
  elseif (Test-Path -LiteralPath $lockPath) {
    # A lost create receipt retains its lease. Only its own read-only reconciliation may pass.
    $pending = (Read-ProtectedText $lockPath 4096) | ConvertFrom-Json
    if ($request.action -cne 'Status' -or $pending.action -cne 'admin-bootstrap' -or
        $pending.operation_id -cne $request.operation_id -or $pending.target -cne $env:COMPUTERNAME) {
      throw 'bootstrap_maintenance_busy'
    }
  }
  $active = (Read-ProtectedText (Join-Path $state 'active-release.json')) | ConvertFrom-Json
  if ([string]$active.site_root -cne $site -or
      [string]$active.candidate_id -cnotmatch '^[a-z0-9][a-z0-9._-]{0,62}$' -or
      [string]$active.candidate_root -cne "C:\Ruisheng\candidates\$($active.candidate_id)" -or
      ($request.expected_candidate_id -and [string]$active.candidate_id -cne [string]$request.expected_candidate_id)) {
    throw 'bootstrap_active_release_mismatch'
  }
  if ($request.action -ne 'Plan' -and -not $request.expected_candidate_id) { throw 'bootstrap_candidate_required' }
  $root = [string]$active.candidate_root
  $trust = 'C:\ProgramData\Ruisheng\trust'
  $signers = Join-Path $trust 'release-allowed-signers'
  $allowed = Read-ProtectedText $signers 1024
  if ($allowed -cnotmatch '^ruisheng-release ssh-ed25519 ([A-Za-z0-9+/]+={0,2})\n$') { throw 'bootstrap_signer_invalid' }
  $fingerprint = Read-ProtectedText (Join-Path $trust 'release-key-fingerprint') 128
  $keygen = 'C:\Windows\System32\OpenSSH\ssh-keygen.exe'
  $fp = Invoke-Fixed $keygen @('-l', '-E', 'sha256', '-f', $signers)
  if ($fp.ExitCode -ne 0 -or $fp.Output -cnotmatch '^256 (SHA256:[A-Za-z0-9+/]{43}) ' -or
      $fingerprint -cne "$($Matches[1])`n") { throw 'bootstrap_fingerprint_invalid' }
  $sums = Read-ProtectedBytes (Join-Path $root 'SHA256SUMS') 16384
  [void](Read-ProtectedBytes (Join-Path $root 'SHA256SUMS.sig') 4096)
  $signature = Invoke-BootstrapSignatureCheck $keygen $signers (Join-Path $root 'SHA256SUMS') (Join-Path $root 'SHA256SUMS.sig')
  if ($signature.ExitCode -ne 0) { throw 'bootstrap_publisher_signature_invalid' }
  $manifestBytes = Read-ProtectedBytes (Join-Path $root 'MANIFEST.json')
  $sha = [Security.Cryptography.SHA256]::Create()
  try { $manifestHash = ([BitConverter]::ToString($sha.ComputeHash($manifestBytes))).Replace('-', '').ToLowerInvariant() }
  finally { $sha.Dispose() }
  $entries = @([Text.Encoding]::ASCII.GetString($sums).Split("`n") | Where-Object { $_ -match '  MANIFEST\.json$' })
  if ($entries.Count -ne 1 -or $entries[0] -cne "$manifestHash  MANIFEST.json") { throw 'bootstrap_manifest_hash_invalid' }
  $manifest = [Text.Encoding]::UTF8.GetString($manifestBytes) | ConvertFrom-Json
  if ($manifest.schema_version -notin @(2,3) -or [string]$manifest.candidate_id -cne [string]$active.candidate_id -or
      [string]$manifest.source_commit -cne [string]$active.source_commit -or
      [string]$manifest.logical_identity -cne [string]$active.logical_identity) { throw 'bootstrap_manifest_identity_invalid' }
  $apiImages = @($manifest.images | Where-Object { $_.component -ceq 'api' })
  if ($apiImages.Count -ne 1 -or [string]$apiImages[0].image_id -cnotmatch '^sha256:[0-9a-f]{64}$') { throw 'bootstrap_image_invalid' }
  $container = Get-SafeContainer 'ruisheng-api'
  if (-not $container.State.Running -or
      [string]$container.Image -cne [string]$apiImages[0].image_id -or
      [string]$container.Config.Image -cne [string]$apiImages[0].candidate_reference -or
      @($container.Mounts).Count -ne 0 -or $container.HostConfig.Privileged -or
      $container.HostConfig.Devices -or
      $container.HostConfig.ExtraHosts) { throw 'bootstrap_runtime_identity_invalid' }
  $dbValues = @($container.Config.Env | Where-Object { $_ -cmatch '^API_DB_URL=' })
  if ($dbValues.Count -ne 1 -or $dbValues[0] -cnotmatch '^API_DB_URL=postgresql\+asyncpg://ruisheng_api:[A-Za-z0-9._~-]+@postgres:5432/ruisheng$' -or
      @($container.Config.Env | Where-Object { $_ -ceq 'API_ENV=prod' }).Count -ne 1) { throw 'bootstrap_database_target_invalid' }
  $postgres = Get-SafeContainer 'ruisheng-postgres'
  $apiNetworks = @($container.NetworkSettings.Networks.PSObject.Properties.Name)
  $pgNetworks = @($postgres.NetworkSettings.Networks.PSObject.Properties | Where-Object {
    $_.Name -in $apiNetworks -and 'postgres' -in @($_.Value.Aliases)
  })
  $pgImages = @($manifest.images | Where-Object { $_.component -ceq 'postgres' })
  if ($pgImages.Count -ne 1 -or [string]$postgres.Image -cne [string]$pgImages[0].image_id -or
      -not $postgres.State.Running -or $pgNetworks.Count -ne 1) { throw 'bootstrap_database_identity_invalid' }
  $address = $null
  if (-not [Net.IPAddress]::TryParse([string]$pgNetworks[0].Value.IPAddress, [ref]$address) -or
      $address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
    throw 'bootstrap_database_address_invalid'
  }
  $resolved = Invoke-FixedDocker @('exec', [string]$container.Id, '/usr/bin/getent', 'ahostsv4', 'postgres')
  if ($resolved.ExitCode -ne 0 -or $resolved.HasError) { throw 'bootstrap_database_resolution_failed' }
  $resolvedAddresses = @($resolved.Output.Trim() -split '\r?\n' | ForEach-Object {
    $fields = $_.Trim() -split '\s+'
    if ($fields.Count -lt 2 -or $fields[1] -cnotin @('STREAM','DGRAM','RAW')) {
      throw 'bootstrap_database_resolution_invalid'
    }
    $fields[0]
  } | Sort-Object -Unique)
  if ($resolvedAddresses.Count -ne 1 -or $resolvedAddresses[0] -cne $address.ToString()) {
    throw 'bootstrap_database_address_mismatch'
  }
  if ((Read-ProtectedText (Join-Path $trust 'entitlement-site-id') 256).TrimEnd("`n") -cne [string]$request.site_id) {
    throw 'bootstrap_site_mismatch'
  }
  $verifier = 'C:\ProgramData\Ruisheng\bin\target_entitlement_verifier.ps1'
  [void](Read-ProtectedBytes $verifier 262144)
  $authorization = Invoke-Fixed 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' @(
    '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $verifier,
    '-Action', 'Authorize', '-SiteId', [string]$request.site_id, '-Feature', 'remote-support')
  if ($authorization.ExitCode -ne 0 -or $authorization.HasError) { throw 'bootstrap_entitlement_denied' }
  $grant = $authorization.Output | ConvertFrom-Json
  if ($grant.ok -ne $true -or $grant.status -cne 'authorized' -or $grant.feature -cne 'remote-support' -or
      [DateTimeOffset]::Parse([string]$grant.valid_until) -le $deadline.AddSeconds(5)) {
    throw 'bootstrap_entitlement_denied'
  }
  $health = Invoke-FixedDocker @('exec', [string]$container.Id, '/app/.venv/bin/python', '-I', '-m', 'ruisheng_api.healthcheck')
  if ($health.ExitCode -ne 0 -or $health.HasError) { throw 'bootstrap_api_not_ready' }
  $readiness = $health.Output | ConvertFrom-Json
  if (@($readiness.PSObject.Properties).Count -ne 4) { throw 'bootstrap_health_invalid' }
  foreach ($field in @('status','database','redis','service')) {
    if ($readiness.$field -isnot [string] -or $readiness.$field -cne 'ready') {
      throw 'bootstrap_api_not_ready'
    }
  }
  if ($request.action -eq 'Plan') {
    $result = [ordered]@{ operation_id = [string]$request.operation_id; site_id = [string]$request.site_id;
      user_name = [string]$request.user_name; status = 'planned'; candidate_id = [string]$active.candidate_id;
      computer = [string]$env:COMPUTERNAME; image_authentication = 'publisher_signed_installed_image_only' }
  }
  else {
    if ([string]$request.password -cnotmatch '^[A-Za-z0-9_-]{32}$') { throw 'bootstrap_password_invalid' }
    $cli = [ordered]@{ schema_version = 1; action = ([string]$request.action).ToLowerInvariant();
      operation_id = [string]$request.operation_id; site_id = [string]$request.site_id;
      user_name = [string]$request.user_name; password = [string]$request.password }
    $bytes = [Text.Encoding]::UTF8.GetBytes(($cli | ConvertTo-Json -Compress))
    if (($deadline - [DateTimeOffset]::UtcNow).TotalSeconds -lt 20) { throw 'bootstrap_deadline_insufficient' }
    if ($request.action -eq 'Create') { Assert-NoFullUpgradeMaintenance -SiteRoot $site }
    $cliDispatched = $true
    $reply = Invoke-FixedDocker @('exec', '-i', [string]$container.Id, '/app/.venv/bin/python', '-I', '-m', 'ruisheng_api.admin_bootstrap') $bytes
    $result = $reply.Output | ConvertFrom-Json
    Assert-BootstrapReceipt $result ([string]$request.action) ([string]$request.operation_id) ([string]$request.site_id) ([string]$request.user_name)
    $expectedExit = @{ created=0; confirmed=0; empty=0; conflict=2; rejected=2; unknown=3 }
    if ($reply.HasError -or $reply.ExitCode -ne $expectedExit[[string]$result.status]) {
      throw 'bootstrap_receipt_unknown'
    }
    $cliFinished = $result.status -cne 'unknown'
  }
  $result | ConvertTo-Json -Compress
}
catch {
  # No raw exception, Docker environment, SQL parameters or secret input may escape.
  [ordered]@{ operation_id = if ($request) { [string]$request.operation_id } else { '' };
    site_id = if ($request) { [string]$request.site_id } else { '' };
    user_name = if ($request) { [string]$request.user_name } else { '' }; status = 'unknown' } | ConvertTo-Json -Compress
}
finally {
  foreach ($handle in $handles) { $handle.Dispose() }
  if ($null -ne $heldLock) {
    $heldLock.Dispose()
    if (-not $cliDispatched -or $cliFinished) {
      Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
    }
  }
}
'@
}

function ConvertTo-BootstrapRemoteCommand {
  $loader = @'
$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
[Console]::InputEncoding=New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$line=[string]($input|Select-Object -First 1)
if([string]::IsNullOrEmpty($line) -or [Text.Encoding]::UTF8.GetByteCount($line) -gt 65536){exit 2}
$envelope=$line|ConvertFrom-Json
if(@($envelope.PSObject.Properties).Count -ne 2 -or $envelope.script -isnot [string] -or $envelope.request -isnot [string]){exit 2}
$BootstrapRequestJson=$envelope.request
& ([scriptblock]::Create($envelope.script))
exit 0
'@
  return [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($loader))
}

function Invoke-RemoteAdminBootstrap {
  if ($Target -cnotmatch '^[a-zA-Z0-9._-]+@[0-9.]+$' -or
      $ExpectedComputer -cnotmatch '^[A-Z0-9-]{1,63}$' -or
      $SiteId -cnotmatch '^site-[a-z0-9][a-z0-9-]{0,43}$' -or
      $UserName -cnotmatch '^[a-zA-Z][a-zA-Z0-9_]{3,29}$' -or
      ($ExpectedCandidateId -and $ExpectedCandidateId -cnotmatch '^[a-z0-9][a-z0-9._-]{0,62}$')) {
    throw 'bootstrap_identity_invalid'
  }
  Assert-BootstrapPath $SiteRoot
  if (-not $CredentialDirectory) {
    $CredentialDirectory = 'C:\ProgramData\Ruisheng\admin-bootstrap-' + [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
  }
  $path = Join-Path $CredentialDirectory "$SiteId.dpapi"
  if ($Action -eq 'ShowCredential') { Show-BootstrapCredential $path; return }
  if ($Action -eq 'Create' -and (-not $Approved -or -not $ExpectedCandidateId)) { throw 'bootstrap_approval_and_candidate_required' }
  $record = $null
  if ($Action -ne 'Plan') {
    Initialize-BootstrapCredentialDirectory $CredentialDirectory
    if (Test-Path -LiteralPath $path) { $record = Read-BootstrapCredential $path }
    elseif ($Action -eq 'Status') { throw 'bootstrap_credential_missing' }
    else {
      $record = [pscustomobject][ordered]@{ schema_version = 1; operation_id = [Guid]::NewGuid().ToString('D');
        site_id = $SiteId; user_name = $UserName; target = $Target; expected_computer = $ExpectedComputer;
        site_root = $SiteRoot; expected_candidate_id = $ExpectedCandidateId; password = (New-BootstrapPassword) }
      $record = Save-BootstrapCredential $path $record
    }
    foreach ($pair in @(@('site_id',$SiteId), @('user_name',$UserName), @('target',$Target),
      @('expected_computer',$ExpectedComputer), @('site_root',$SiteRoot))) {
      if ([string]$record.($pair[0]) -cne [string]$pair[1]) { throw 'bootstrap_credential_identity_conflict' }
    }
    if ($ExpectedCandidateId -and [string]$record.expected_candidate_id -cne $ExpectedCandidateId) { throw 'bootstrap_credential_candidate_conflict' }
    $ExpectedCandidateId = [string]$record.expected_candidate_id
  }
  $operation = if ($record) { [string]$record.operation_id } else { [Guid]::NewGuid().ToString('D') }
  $request = [ordered]@{ schema_version = 1; action = $Action; operation_id = $operation;
    site_id = $SiteId; user_name = $UserName; target = $Target; expected_computer = $ExpectedComputer;
    site_root = $SiteRoot; expected_candidate_id = $ExpectedCandidateId;
    password = if ($record) { [string]$record.password } else { '' } }
  $bytes = [Text.Encoding]::UTF8.GetBytes(($request | ConvertTo-Json -Compress))
  if ($bytes.Length -gt 4096) { throw 'bootstrap_request_limit' }
  try {
    $command = ConvertTo-BootstrapRemoteCommand
    if ($command.Length -gt 4000) { throw 'bootstrap_command_limit' }
    $envelope = [ordered]@{ script = Get-BootstrapRemoteScript; request = [Text.Encoding]::UTF8.GetString($bytes) }
    $transportBytes = [Text.Encoding]::UTF8.GetBytes(($envelope | ConvertTo-Json -Compress) + "`n")
    if ($transportBytes.Length -gt 65536) { throw 'bootstrap_envelope_limit' }
    $reply = Invoke-BootstrapNative 'C:\Windows\System32\OpenSSH\ssh.exe' @(
      '-T', '-F', 'NUL', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
      '-o', 'IdentitiesOnly=yes', '-o', 'PreferredAuthentications=publickey',
      '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no',
      '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=2',
      $Target, 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
      'Bypass', '-OutputFormat', 'Text', '-EncodedCommand', $command
    ) $transportBytes 60000
    if ($reply.ExitCode -ne 0 -or $reply.HasError) { throw 'bootstrap_transport_unknown' }
    $result = $reply.Output | ConvertFrom-Json
    Assert-BootstrapReceipt $result $Action $operation $SiteId $UserName
    # Reconstruct an allowlisted receipt instead of forwarding remote data.
    $safe = [ordered]@{ operation_id = $operation; site_id = $SiteId; user_name = $UserName; status = [string]$result.status }
    if ($Action -eq 'Plan' -and $result.status -eq 'planned') {
      if ([string]$result.candidate_id -cnotmatch '^[a-z0-9][a-z0-9._-]{0,62}$' -or
          [string]$result.computer -cne $ExpectedComputer) { throw 'bootstrap_plan_invalid' }
      $safe.candidate_id = [string]$result.candidate_id
      $safe.computer = $ExpectedComputer
      $safe.image_authentication = 'publisher_signed_installed_image_only'
    }
    $safe | ConvertTo-Json -Compress
  }
  catch {
    [ordered]@{ operation_id = $operation; site_id = $SiteId; user_name = $UserName; status = 'unknown' } | ConvertTo-Json -Compress
  }
  finally {
    [Array]::Clear($bytes, 0, $bytes.Length)
    if ($null -ne $transportBytes) { [Array]::Clear($transportBytes, 0, $transportBytes.Length) }
  }
}

function Invoke-BootstrapEntryPoint {
  $output = Invoke-RemoteAdminBootstrap
  if ($Action -eq 'ShowCredential') { return 0 }
  $result = $output | ConvertFrom-Json
  $exitCodes = @{ planned=0; created=0; confirmed=0; empty=0; rejected=2; conflict=2; unknown=3 }
  if (-not $exitCodes.ContainsKey([string]$result.status)) { throw 'bootstrap_receipt_invalid' }
  [Console]::Out.WriteLine($output)
  return $exitCodes[[string]$result.status]
}

if (-not $LibraryOnly) {
  try { exit (Invoke-BootstrapEntryPoint) }
  catch {
    [Console]::Error.WriteLine('Administrator bootstrap stopped before dispatch. Check protected credentials and approved identity; do not delete or reset them.')
    exit 2
  }
}
