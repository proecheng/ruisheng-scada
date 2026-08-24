[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigPath,
    [string]$AttachScriptPath = "C:\Ruisheng\tools\serial_hardware_attach.ps1",
    [string]$TaskName = "Ruisheng-Serial-Hardware-Attach"
)

$ErrorActionPreference = "Stop"

function Get-ApprovedSids {
    return @("S-1-5-18", "S-1-5-32-544")
}

function Assert-ProtectedPath([string]$Path, [string]$Label) {
    $item = Get-Item -Force -LiteralPath $Path -ErrorAction Stop
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "${Label}_is_linked"
    }
    $allowed = Get-ApprovedSids
    $unsafe = [Security.AccessControl.FileSystemRights]::CreateFiles -bor
        [Security.AccessControl.FileSystemRights]::CreateDirectories -bor
        [Security.AccessControl.FileSystemRights]::AppendData -bor
        [Security.AccessControl.FileSystemRights]::WriteData -bor
        [Security.AccessControl.FileSystemRights]::WriteAttributes -bor
        [Security.AccessControl.FileSystemRights]::WriteExtendedAttributes -bor
        [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
        [Security.AccessControl.FileSystemRights]::Delete -bor
        [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
        [Security.AccessControl.FileSystemRights]::TakeOwnership
    $acl = Get-Acl -LiteralPath $Path
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($owner -notin $allowed) { throw "${Label}_owner_is_unapproved" }
    foreach ($rule in $acl.Access) {
        if ($rule.AccessControlType -ne "Allow" -or
            ($rule.FileSystemRights -band $unsafe) -eq 0) { continue }
        $sid = $rule.IdentityReference.Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
        if ($sid -notin $allowed) { throw "${Label}_is_writable_by_unapproved_identity" }
    }
}

function Set-ProtectedPathAcl([string]$Path, [bool]$Directory) {
    $security = if ($Directory) {
        [Security.AccessControl.DirectorySecurity]::new()
    } else {
        [Security.AccessControl.FileSecurity]::new()
    }
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner([Security.Principal.SecurityIdentifier]::new("S-1-5-32-544"))
    $inheritance = if ($Directory) {
        [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
            [Security.AccessControl.InheritanceFlags]::ObjectInherit
    } else {
        [Security.AccessControl.InheritanceFlags]::None
    }
    foreach ($sidValue in Get-ApprovedSids) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($sidValue),
            [Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow
        )
        [void]$security.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $security
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "administrator_required"
}
if ($identity.User.Value -eq "S-1-5-18") { throw "interactive_docker_desktop_owner_required" }

foreach ($directory in @("C:\Ruisheng", "C:\Ruisheng\tools", "C:\Ruisheng\site", "C:\Ruisheng\audit")) {
    if (-not (Test-Path -LiteralPath $directory)) {
        [void](New-Item -ItemType Directory -Path $directory)
    }
    $item = Get-Item -Force -LiteralPath $directory
    if (-not $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "protected_directory_is_invalid:$directory"
    }
    Set-ProtectedPathAcl $directory $true
}

foreach ($pathName in @("ConfigPath", "AttachScriptPath")) {
    $path = Get-Variable -Name $pathName -ValueOnly
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "required_file_missing:$path"
    }
    Set-Variable -Name $pathName -Value (Resolve-Path -LiteralPath $path).Path
}
if (-not $AttachScriptPath.Equals(
    "C:\Ruisheng\tools\serial_hardware_attach.ps1", [StringComparison]::OrdinalIgnoreCase
)) { throw "attach_script_path_is_not_approved" }
if (-not $ConfigPath.StartsWith("C:\Ruisheng\site\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "config_path_is_not_approved"
}
if (-not [IO.Path]::GetDirectoryName($ConfigPath).Equals(
    "C:\Ruisheng\site", [StringComparison]::OrdinalIgnoreCase
)) { throw "config_path_must_be_directly_below_site_root" }
foreach ($path in @($AttachScriptPath, $ConfigPath)) {
    Set-ProtectedPathAcl $path $false
    Assert-ProtectedPath $path "scheduled_task_input"
}

$wslDistributions = @(& wsl.exe -l -q 2>$null) | ForEach-Object { ([string]$_).Trim("`0 ") }
if ($LASTEXITCODE -ne 0 -or $wslDistributions -cnotcontains "docker-desktop") {
    throw "docker_desktop_wsl_not_available_for_current_user"
}

$pwsh = "C:\Program Files\PowerShell\7\pwsh.exe"
if (-not (Test-Path -LiteralPath $pwsh -PathType Leaf)) {
    $pwsh = (Get-Command powershell.exe -ErrorAction Stop).Source
}

& $pwsh -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $AttachScriptPath `
    -ConfigPath $ConfigPath -RunOnce
$initialExitCode = $LASTEXITCODE
if ($initialExitCode -notin @(0, 2)) { throw "initial_serial_hardware_check_failed" }

$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -ConfigPath "{1}"' -f `
    $AttachScriptPath, $ConfigPath
$action = New-ScheduledTaskAction -Execute $pwsh -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = "PT45S"
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $identity.User.Value -LogonType S4U `
    -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $taskPrincipal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
$initialState = if ($initialExitCode -eq 0) { "ready" } else { "device_unavailable" }
Write-Output "REGISTERED task=$TaskName user=$($identity.User.Value) initial_state=$initialState"
