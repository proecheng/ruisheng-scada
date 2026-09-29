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
        if ($protectedDirectory -cne 'C:\Ruisheng') {
            Set-Acl -LiteralPath $protectedDirectory `
                -AclObject (New-ProtectedFileSystemSecurity $true)
        }
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
    if ($script:ConfigSchema -eq 2) {
        $record.identity_policy = $script:IdentityPolicy
        $record.instance_id = $script:InstanceId
        if (-not [string]::IsNullOrWhiteSpace($script:ConfiguredInstanceId) -and
            $script:ConfiguredInstanceId -cne $script:InstanceId) {
            $record.rebound_from = $script:ConfiguredInstanceId
        }
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
        schema_version = $script:ConfigSchema
        result = $Result
        timestamp = [DateTimeOffset]::Now.ToString("o")
        vendor_id = $script:VendorId
        product_id = $script:ProductId
        serial_number = $script:SerialNumber
        stable_path = $script:StableAlias
        device_path = $DevicePath
        bus_id = $BusId
    }
    if ($script:ConfigSchema -eq 2) {
        $record.Remove('serial_number')
        $record.instance_id = $script:InstanceId
        $record.identity_policy = $script:IdentityPolicy
        $record.configured_instance_id = $script:ConfiguredInstanceId
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
    # .NET Framework initializes its stdin writer from Console.InputEncoding;
    # a BOM-bearing console encoding can write a preamble before BaseStream.
    $previousInputEncoding = [Console]::InputEncoding
    [Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
    try {
    $process = [Diagnostics.Process]::Start($start)
    } finally { [Console]::InputEncoding = $previousInputEncoding }
    $outputTask = $process.StandardOutput.ReadToEndAsync()
    $errorTask = $process.StandardError.ReadToEndAsync()
    $inputStream = $process.StandardInput.BaseStream
    try {
        if ($StandardInput.Length -gt 0) {
            $inputBytes = [Text.UTF8Encoding]::new($false).GetBytes($StandardInput)
            $inputStream.Write($inputBytes, 0, $inputBytes.Length)
            $inputStream.Flush()
        }
    } finally {
        $inputStream.Close()
    }
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
    if ([string]::IsNullOrWhiteSpace($script:ConfiguredInstanceId)) {
        $script:ConfiguredInstanceId = $script:InstanceId
    }
    if ([string]::IsNullOrWhiteSpace($script:IdentityPolicy)) {
        $script:IdentityPolicy = 'exact_instance'
    }
    $state = (Invoke-Usbipd @("state")) | ConvertFrom-Json -ErrorAction Stop
    $prefix = [Regex]::Escape("USB\VID_$($script:VendorId)&PID_$($script:ProductId)")
    $serial = [Regex]::Escape($script:SerialNumber)
    $instancePattern = "^${prefix}(?:&MI_[0-9A-F]{2})?\\${serial}$"
    $identityPattern = "^${prefix}(?:&MI_[0-9A-F]{2})?\\[A-Za-z0-9&._-]{1,160}$"
    $busPattern = '^[0-9]+-[0-9]+$'
    $candidates = @(if ($script:ConfigSchema -eq 2) {
        # Keep an approved instance even when its BusId is malformed. Filtering
        # it out here reports device_not_present and hides invalid_bus_id.
        $identified = @($state.Devices | Where-Object {
            [string]$_.InstanceId -match $identityPattern
        })
        $matching = @($identified | Where-Object { [string]$_.BusId -match $busPattern })
        $approved = @($identified | Where-Object { [string]$_.InstanceId -ieq $script:ConfiguredInstanceId })
        if ($approved.Count -gt 1) { throw "device_identity_ambiguous" }
        if ($approved.Count -eq 1) {
            # Prefer the approved instance when it is present. This allows a
            # second, same-model adapter to remain connected without sending
            # traffic to it accidentally.
            $approved
        } elseif ($script:IdentityPolicy -ceq 'single_present_device' -and $matching.Count -eq 1) {
            # CH340 has no stable serial. When the approved instance has
            # disappeared, rebind only if exactly one approved VID/PID device
            # is currently present. The selected InstanceId is persisted in
            # state/audit as evidence of the rebind.
            $script:InstanceId = ([string]$matching[0].InstanceId).ToUpperInvariant()
            $matching
        } elseif ($script:IdentityPolicy -ceq 'exact_instance') {
            @()
        } else {
            $matching
        }
    } else {
        $state.Devices | Where-Object { [string]$_.InstanceId -match $instancePattern }
    })
    if ($candidates.Count -eq 0) { throw "device_not_present" }
    if ($candidates.Count -ne 1) { throw "device_identity_ambiguous" }
    Assert-Pattern "bus_id" ([string]$candidates[0].BusId) '^[0-9]+-[0-9]+$'
    return $candidates[0]
}

function Invoke-WslScript([string]$Script, [string[]]$Arguments, [int]$TimeoutSeconds = 30) {
    $commandArguments = @("-d", $script:WslDistribution, "-u", "root", "--", "sh", "-s", "--") +
        $Arguments
    $normalizedScript = $Script.Replace("`r`n", "`n").Replace("`r", "`n")
    if ($normalizedScript.Length -gt 0 -and $normalizedScript[0] -eq [char]0xFEFF) {
        $normalizedScript = $normalizedScript.Substring(1)
    }
    return Invoke-NativeCommand `
        $script:WslPath $commandArguments $normalizedScript $TimeoutSeconds
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
physical_busid=$5
identity_mode=${6:-usb_serial}

# A stale VHCI slot can retain the same USB serial and ttyUSB0 indefinitely.
# Bind sysfs identity to the currently attached usbipd bus/device identifier.
bus_number=${physical_busid%-*}
device_number=${physical_busid#*-}
remote_devid=$(printf '%08x' "$((bus_number * 65536 + device_number))")
usb_path=""
attach_attempt=0
# usbipd can return while the slot is still 005 (enumerating). Keep the
# same identity guard and wait briefly for that exact slot to become active.
while [ "$attach_attempt" -lt 10 ]; do
    matched_slots=0
    usb_path=""
    for status_file in /sys/devices/platform/vhci_hcd.*/status; do
        [ -f "$status_file" ] || continue
        while read -r hub port status speed devid sockfd local_busid; do
            [ "$devid" = "$remote_devid" ] && [ "$status" = "006" ] || continue
            matched_slots=$((matched_slots + 1))
            usb_path=$(readlink -f "/sys/bus/usb/devices/$local_busid")
        done < "$status_file"
    done
    [ "$matched_slots" -le 1 ] || exit 43
    [ "$matched_slots" -eq 1 ] && [ -n "$usb_path" ] && break
    attach_attempt=$((attach_attempt + 1))
    sleep 1
done
[ "$matched_slots" -eq 1 ] && [ -n "$usb_path" ] || exit 43

modprobe usbserial
case "$vendor:$product:$identity_mode" in
    0403:6001:usb_serial) modprobe ftdi_sio ;;
    1a86:7523:windows_instance) modprobe ch341 ;;
    *) exit 44 ;;
esac

attempt=0
while [ "$attempt" -lt 30 ]; do
    device_node=""
    for tty_path in /sys/class/tty/ttyUSB* /sys/class/tty/ttyACM*; do
        [ -e "$tty_path" ] || continue
        current=$(readlink -f "$tty_path/device")
        case "$current" in "$usb_path"/*) ;; *) continue ;; esac
        while [ -n "$current" ] && [ "$current" != "/" ]; do
            if [ -f "$current/idVendor" ] && [ -f "$current/idProduct" ]; then
                current_vendor=$(tr 'A-F' 'a-f' < "$current/idVendor")
                current_product=$(tr 'A-F' 'a-f' < "$current/idProduct")
                current_serial=""
                if [ -f "$current/serial" ]; then current_serial=$(tr 'A-Z' 'a-z' < "$current/serial"); fi
                # Serial-less CH340 is pinned in Windows to the approved exact
                # InstanceId and here to its single current VHCI path, not any tty.
                if [ "$current" = "$usb_path" ] && [ "$current_vendor" = "$vendor" ] && [ "$current_product" = "$product" ] &&
                    { [ "$identity_mode" = windows_instance ] || [ "$current_serial" = "$serial" ]; }; then
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
        $script:VendorId, $script:ProductId, $script:SerialNumber, $script:StableAlias, $busId, $script:IdentityMode
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
    [int64]$config.schema_version -notin @(1,2)) {
    throw "unsupported_config_schema"
}

$script:VendorId = ([string]$config.adapter.vendor_id).ToUpperInvariant()
$script:ProductId = ([string]$config.adapter.product_id).ToUpperInvariant()
$script:SerialNumber = [string]$config.adapter.serial_number
$script:ConfigSchema = [int]$config.schema_version
$script:InstanceId = [string]$config.adapter.instance_id
$script:ConfiguredInstanceId = $script:InstanceId
$script:IdentityPolicy = 'exact_instance'
$script:IdentityMode = 'usb_serial'
$script:StableAlias = [string]$config.adapter.stable_path
$script:WslDistribution = [string]$config.adapter.wsl_distribution
$retrySeconds = [int]$config.adapter.retry_seconds

Assert-Pattern "vendor_id" $script:VendorId '^[0-9A-F]{4}$'
Assert-Pattern "product_id" $script:ProductId '^[0-9A-F]{4}$'
if ($script:ConfigSchema -eq 2) {
    if (($script:VendorId, $script:ProductId) -join ':' -cne '1A86:7523') { throw 'unsupported_usb_adapter' }
    Assert-Pattern 'instance_id' $script:InstanceId '^USB\\VID_1A86&PID_7523\\[A-Za-z0-9&._-]{1,160}$'
    if ($script:InstanceId -match '(?i)unresolved|change[_-]?me|pending|tbd' -or
        $config.adapter.PSObject.Properties.Name -contains 'serial_number') { throw 'invalid_instance_id' }
    $script:InstanceId = $script:InstanceId.ToUpperInvariant()
    if ($config.adapter.PSObject.Properties.Name -contains 'identity_policy') {
        $script:IdentityPolicy = [string]$config.adapter.identity_policy
    }
    if ($script:IdentityPolicy -notin @('exact_instance', 'single_present_device')) {
        throw 'invalid_identity_policy'
    }
    $script:SerialNumber = '-'
    $script:IdentityMode = 'windows_instance'
} else {
    if (($script:VendorId, $script:ProductId) -join ':' -cne '0403:6001') { throw 'unsupported_usb_adapter' }
    Assert-Pattern 'serial_number' $script:SerialNumber '^[A-Za-z0-9._-]{1,64}$'
    if ($script:SerialNumber -match '(?i)unresolved|change[_-]?me|pending|tbd' -or
        $config.adapter.PSObject.Properties.Name -contains 'instance_id') { throw 'invalid_serial_number' }
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
