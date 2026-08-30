[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigPath,
    [switch]$RunOnce,
    [string]$AuditPath = "C:\Ruisheng\audit\serial-hardware.jsonl",
    [string]$StatePath = "C:\Ruisheng\audit\serial-hardware-state.json"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
if ($AuditPath -cne "C:\Ruisheng\audit\serial-hardware.jsonl" -or
    $StatePath -cne "C:\Ruisheng\audit\serial-hardware-state.json") {
    throw "audit_paths_are_fixed"
}

function Assert-Pattern([string]$Name, [string]$Value, [string]$Pattern) {
    if ($Value -notmatch $Pattern) { throw "invalid_$Name" }
}

function New-ProtectedFileSystemSecurity([bool]$Directory) {
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
    foreach ($sidValue in @("S-1-5-18", "S-1-5-32-544")) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($sidValue),
            [Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow
        )
        [void]$security.AddAccessRule($rule)
    }
    return $security
}

function Initialize-ProtectedAuditPath {
    $directory = Split-Path -Parent $AuditPath
    if ([string]::IsNullOrWhiteSpace($directory) -or
        -not $StatePath.StartsWith("$directory\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "invalid_audit_paths"
    }
    foreach ($protectedDirectory in @("C:\Ruisheng", $directory)) {
        if (-not (Test-Path -LiteralPath $protectedDirectory)) {
            [void](New-Item -ItemType Directory -Path $protectedDirectory)
        }
        $item = Get-Item -Force -LiteralPath $protectedDirectory
        if (-not $item.PSIsContainer -or
            ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "audit_directory_is_linked"
        }
        Set-Acl -LiteralPath $protectedDirectory `
            -AclObject (New-ProtectedFileSystemSecurity $true)
    }
    foreach ($path in @($AuditPath, $StatePath)) {
        if (-not (Test-Path -LiteralPath $path)) {
            [IO.File]::WriteAllBytes($path, [byte[]]::new(0))
        }
        $file = Get-Item -Force -LiteralPath $path
        if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "audit_file_is_linked"
        }
        Set-Acl -LiteralPath $path -AclObject (New-ProtectedFileSystemSecurity $false)
    }
}

function Write-AuditRecord(
    [string]$Result,
    [string]$ErrorCode = "",
    [string]$BusId = "",
    [string]$DevicePath = ""
) {
    $record = [ordered]@{
        schema_version = 1
        timestamp = [DateTimeOffset]::Now.ToString("o")
        actor = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        result = $Result
        error_code = $ErrorCode
        bus_id = $BusId
        device_path = $DevicePath
    }
    $line = ($record | ConvertTo-Json -Depth 3 -Compress) + [Environment]::NewLine
    [IO.File]::AppendAllText($AuditPath, $line, [Text.UTF8Encoding]::new($false))
}

function Write-StateRecord(
    [string]$Result,
    [string]$BusId = "",
    [string]$DevicePath = ""
) {
    $record = [ordered]@{
        schema_version = 1
        result = $Result
        timestamp = [DateTimeOffset]::Now.ToString("o")
        vendor_id = $script:VendorId
        product_id = $script:ProductId
        serial_number = $script:SerialNumber
        stable_path = $script:StableAlias
        device_path = $DevicePath
        bus_id = $BusId
    }
    $temporary = Join-Path (Split-Path -Parent $StatePath) ([IO.Path]::GetRandomFileName())
    try {
        $stream = [IO.File]::Open(
            $temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None
        )
        try {
            $bytes = [Text.UTF8Encoding]::new($false).GetBytes(
                (($record | ConvertTo-Json -Depth 3 -Compress) + [Environment]::NewLine)
            )
            $stream.Write($bytes, 0, $bytes.Length)
        } finally {
            $stream.Dispose()
        }
        Set-Acl -LiteralPath $temporary -AclObject (New-ProtectedFileSystemSecurity $false)
        Move-Item -LiteralPath $temporary -Destination $StatePath -Force
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-NativeCommand(
    [string]$FilePath,
    [string[]]$ArgumentList,
    [string]$StandardInput = "",
    [int]$TimeoutSeconds = 30
) {
    foreach ($argument in $ArgumentList) {
        if ($argument -match '[\s"]') { throw "unsafe_native_argument" }
    }
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $FilePath
    $start.Arguments = $ArgumentList -join " "
    $start.UseShellExecute = $false
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.RedirectStandardInput = $true
    $process = [Diagnostics.Process]::Start($start)
    $outputTask = $process.StandardOutput.ReadToEndAsync()
    $errorTask = $process.StandardError.ReadToEndAsync()
    if ($StandardInput.Length -gt 0) { $process.StandardInput.Write($StandardInput) }
    $process.StandardInput.Close()
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        $process.Kill()
        $process.WaitForExit()
        throw "native_command_timeout"
    }
    return [pscustomobject]@{
        ExitCode = $process.ExitCode
        Output = $outputTask.GetAwaiter().GetResult()
        Error = $errorTask.GetAwaiter().GetResult()
    }
}

function Invoke-Usbipd([string[]]$Arguments) {
    $result = Invoke-NativeCommand $script:UsbipdPath $Arguments
    if ($result.ExitCode -ne 0) { throw "usbipd_$($Arguments[0])_failed" }
    return $result.Output
}

function Get-TargetDevice {
    $state = (Invoke-Usbipd @("state")) | ConvertFrom-Json -ErrorAction Stop
    $prefix = [Regex]::Escape("USB\VID_$($script:VendorId)&PID_$($script:ProductId)")
    $serial = [Regex]::Escape($script:SerialNumber)
    $instancePattern = "^${prefix}(?:&MI_[0-9A-F]{2})?\\${serial}$"
    $matches = @(
        $state.Devices | Where-Object { [string]$_.InstanceId -match $instancePattern }
    )
    if ($matches.Count -eq 0) { throw "device_not_present" }
    if ($matches.Count -ne 1) { throw "device_identity_ambiguous" }
    Assert-Pattern "bus_id" ([string]$matches[0].BusId) '^[0-9]+-[0-9]+$'
    return $matches[0]
}

function Invoke-WslScript([string]$Script, [string[]]$Arguments, [int]$TimeoutSeconds = 30) {
    $commandArguments = @("-d", $script:WslDistribution, "-u", "root", "--", "sh", "-s", "--") +
        $Arguments
    return Invoke-NativeCommand $script:WslPath $commandArguments $Script $TimeoutSeconds
}

function Remove-StableAlias {
    try {
        $result = Invoke-WslScript 'rm -f -- "$1"' @($script:StableAlias) 15
        if ($result.ExitCode -ne 0) { Write-Warning "wsl_alias_cleanup_failed" }
    } catch {
        Write-Warning "wsl_alias_cleanup_failed"
    }
}

function Ensure-WslDevice {
    $device = Get-TargetDevice
    if ([string]::IsNullOrWhiteSpace([string]$device.PersistedGuid)) {
        $device = Get-TargetDevice
        Invoke-Usbipd @("bind", "--busid", [string]$device.BusId) | Out-Null
    }
    $device = Get-TargetDevice
    if ([string]::IsNullOrWhiteSpace([string]$device.ClientIPAddress)) {
        $device = Get-TargetDevice
        Invoke-Usbipd @(
            "attach", "--wsl", $script:WslDistribution, "--busid", [string]$device.BusId
        ) | Out-Null
    }
    $device = Get-TargetDevice
    $busId = [string]$device.BusId

    $linuxScript = @'
set -eu
vendor=$(printf '%s' "$1" | tr 'A-F' 'a-f')
product=$(printf '%s' "$2" | tr 'A-F' 'a-f')
serial=$(printf '%s' "$3" | tr 'A-Z' 'a-z')
alias_path=$4

modprobe usbserial
modprobe ftdi_sio

attempt=0
while [ "$attempt" -lt 30 ]; do
    device_node=""
    for tty_path in /sys/class/tty/ttyUSB* /sys/class/tty/ttyACM*; do
        [ -e "$tty_path" ] || continue
        current=$(readlink -f "$tty_path/device")
        while [ -n "$current" ] && [ "$current" != "/" ]; do
            if [ -f "$current/idVendor" ] && [ -f "$current/idProduct" ] && [ -f "$current/serial" ]; then
                current_vendor=$(tr 'A-F' 'a-f' < "$current/idVendor")
                current_product=$(tr 'A-F' 'a-f' < "$current/idProduct")
                current_serial=$(tr 'A-Z' 'a-z' < "$current/serial")
                if [ "$current_vendor" = "$vendor" ] && [ "$current_product" = "$product" ] && [ "$current_serial" = "$serial" ]; then
                    device_node="/dev/$(basename "$tty_path")"
                    break 2
                fi
            fi
            parent=$(dirname "$current")
            [ "$parent" != "$current" ] || break
            current=$parent
        done
    done
    [ -n "$device_node" ] && break
    attempt=$((attempt + 1))
    sleep 1
done

[ -n "$device_node" ] || exit 42
rm -f -- "$alias_path"
ln -s "$device_node" "$alias_path"
printf '%s\n' "$device_node"
'@
    $result = Invoke-WslScript $linuxScript @(
        $script:VendorId, $script:ProductId, $script:SerialNumber, $script:StableAlias
    ) 45
    if ($result.ExitCode -ne 0) { throw "wsl_device_node_unavailable" }
    $node = [string](($result.Output -split "`r?`n") | Where-Object { $_ } | Select-Object -Last 1)
    if ($node -notmatch '^/dev/tty(?:USB|ACM)[0-9]+$') { throw "wsl_device_node_invalid" }
    return [pscustomobject]@{ BusId = $busId; DevicePath = $node.Trim() }
}

if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    throw "serial_hardware_config_missing"
}
$ConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path
$config = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json -ErrorAction Stop
if ($config.schema_version -is [bool] -or
    -not ($config.schema_version -is [int] -or $config.schema_version -is [long]) -or
    [int64]$config.schema_version -ne 1) {
    throw "unsupported_config_schema"
}

$script:VendorId = ([string]$config.adapter.vendor_id).ToUpperInvariant()
$script:ProductId = ([string]$config.adapter.product_id).ToUpperInvariant()
$script:SerialNumber = [string]$config.adapter.serial_number
$script:StableAlias = [string]$config.adapter.stable_path
$script:WslDistribution = [string]$config.adapter.wsl_distribution
$retrySeconds = [int]$config.adapter.retry_seconds

Assert-Pattern "vendor_id" $script:VendorId '^[0-9A-F]{4}$'
Assert-Pattern "product_id" $script:ProductId '^[0-9A-F]{4}$'
if (($script:VendorId, $script:ProductId) -join ":" -ne "0403:6001") {
    throw "unsupported_usb_adapter"
}
Assert-Pattern "serial_number" $script:SerialNumber '^[A-Za-z0-9._-]{1,64}$'
if ($script:SerialNumber -match '(?i)unresolved|change[_-]?me|pending|tbd') {
    throw "invalid_serial_number"
}
Assert-Pattern "stable_path" $script:StableAlias '^/dev/ruisheng-[A-Za-z0-9._-]+$'
if ($script:WslDistribution -cne "docker-desktop") { throw "invalid_wsl_distribution" }
if ($retrySeconds -lt 2 -or $retrySeconds -gt 300) { throw "invalid_retry_seconds" }

$script:UsbipdPath = "C:\Program Files\usbipd-win\usbipd.exe"
if (-not (Test-Path -LiteralPath $script:UsbipdPath -PathType Leaf)) { throw "usbipd_missing" }
$script:WslPath = Join-Path ([Environment]::SystemDirectory) "wsl.exe"
if (-not (Test-Path -LiteralPath $script:WslPath -PathType Leaf)) { throw "wsl_missing" }
Initialize-ProtectedAuditPath

$lastAuditState = ""
while ($true) {
    try {
        $ready = Ensure-WslDevice
        $auditState = "ready:$($ready.BusId):$($ready.DevicePath)"
        Write-StateRecord "ready" $ready.BusId $ready.DevicePath
        if ($lastAuditState -ne $auditState) {
            Write-AuditRecord "ready" "" $ready.BusId $ready.DevicePath
            $lastAuditState = $auditState
        }
        Write-Output "READY bus_id=$($ready.BusId) device_path=$($ready.DevicePath) alias=$script:StableAlias"
        if ($RunOnce) { exit 0 }
    } catch {
        $errorCode = $_.Exception.Message
        $result = if ($errorCode -eq "device_not_present") { "unavailable" } else { "failed" }
        Remove-StableAlias
        try {
            Write-StateRecord $result
            if ($lastAuditState -ne "${result}:$errorCode") {
                Write-AuditRecord $result $errorCode
                $lastAuditState = "${result}:$errorCode"
            }
        } catch {
            Write-Error "audit_write_failed"
            exit 1
        }
        Write-Warning $errorCode
        if ($RunOnce) {
            if ($result -eq "unavailable") { exit 2 }
            exit 1
        }
    }
    Start-Sleep -Seconds $retrySeconds
}
