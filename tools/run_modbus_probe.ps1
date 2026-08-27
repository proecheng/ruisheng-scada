[CmdletBinding()]
param(
    [string]$ConfigPath = "",
    [string]$AuditPath = "",
    [switch]$Execute,
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
$ProbePath = "C:\Ruisheng\tools\probe_modbus_rtu.py"
$RunnerPath = "C:\Ruisheng\tools\run_modbus_probe.ps1"
$ReceiptPath = "C:\ProgramData\Ruisheng\receipts\modbus-probe-release.json"
$SiteRoot = "C:\Ruisheng\site"
$AuditRoot = "C:\Ruisheng\audit"
$WorkRoot = "C:\ProgramData\Ruisheng\probe-runs"
$DockerPath = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
$DockerHost = "npipe:////./pipe/docker_engine"
$DevicePath = "/dev/ruisheng-rs485"
$ApprovalScope = "b06-9600-8n1-unit1-fc3-r0-5-r27-35"
$ContainerNames = @(
    "ruisheng-postgres", "ruisheng-redis", "ruisheng-api", "ruisheng-gw", "ruisheng-web"
)
$script:RunnerAudit = $null
$script:RunRoot = $null
$script:DockerConfigRoot = $null
$script:RunId = [Guid]::NewGuid().ToString()
$script:ProbeStarted = $false
$script:ProbeContainerName = $null
$script:ProbeContainerAbsent = $true

function Fail([string]$Message) { throw "[modbus-runner] BLOCKED: $Message" }

function Test-DevicePath([string]$Value) {
    return $Value -ceq "/" -or $Value -ceq "/dev" -or
        $Value.StartsWith("/dev/", [StringComparison]::Ordinal)
}

function Assert-Sha256([string]$Value, [string]$Label, [switch]$ImageId) {
    $Pattern = if ($ImageId) { '^sha256:[0-9a-f]{64}$' } else { '^[0-9a-f]{64}$' }
    if ($Value -cnotmatch $Pattern) { Fail "$Label is not a canonical SHA-256" }
}

function Assert-DirectChild([string]$Path, [string]$Root, [string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Path)) { Fail "$Label is required" }
    $FullPath = [IO.Path]::GetFullPath($Path)
    $FullRoot = [IO.Path]::GetFullPath($Root).TrimEnd("\")
    if ([IO.Path]::GetDirectoryName($FullPath) -ine $FullRoot) {
        Fail "$Label must be a direct child of $FullRoot"
    }
    if ([IO.Path]::GetFileName($FullPath).Contains(":")) {
        Fail "$Label must not use an alternate data stream"
    }
    return $FullPath
}

function Get-ApprovedSids {
    return @(
        "S-1-5-18", "S-1-5-32-544",
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
    )
}

function Assert-ProtectedPath([string]$Path, [string]$Label, [switch]$Directory) {
    $Item = Get-Item -Force -LiteralPath $Path -ErrorAction Stop
    if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        ($Directory -and -not $Item.PSIsContainer) -or
        (-not $Directory -and $Item.PSIsContainer)) { Fail "$Label is linked or has the wrong type" }
    $AllowedSids = Get-ApprovedSids
    $Acl = Get-Acl -LiteralPath $Path
    $Owner = $Acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($Owner -notin $AllowedSids) { Fail "$Label has an unapproved owner" }
    $Unsafe = [Security.AccessControl.FileSystemRights]::CreateFiles -bor
        [Security.AccessControl.FileSystemRights]::CreateDirectories -bor
        [Security.AccessControl.FileSystemRights]::AppendData -bor
        [Security.AccessControl.FileSystemRights]::WriteData -bor
        [Security.AccessControl.FileSystemRights]::WriteAttributes -bor
        [Security.AccessControl.FileSystemRights]::WriteExtendedAttributes -bor
        [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
        [Security.AccessControl.FileSystemRights]::Delete -bor
        [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
        [Security.AccessControl.FileSystemRights]::TakeOwnership
    foreach ($Rule in $Acl.Access) {
        if ($Rule.AccessControlType -ne "Allow" -or ($Rule.FileSystemRights -band $Unsafe) -eq 0) {
            continue
        }
        try {
            $Sid = $Rule.IdentityReference.Translate(
                [Security.Principal.SecurityIdentifier]
            ).Value
        } catch { Fail "$Label has an unresolvable writable identity" }
        if ($Sid -notin $AllowedSids) { Fail "$Label is writable by an unapproved identity" }
    }
}

function Assert-ProtectedAncestors([string]$Path, [string]$Label) {
    $AllowedSids = Get-ApprovedSids
    $Current = (Get-Item -Force -LiteralPath $Path -ErrorAction Stop).Parent
    $UnsafeParentRights = `
        [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
        [Security.AccessControl.FileSystemRights]::Delete -bor
        [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
        [Security.AccessControl.FileSystemRights]::TakeOwnership
    while ($null -ne $Current) {
        if (($Current.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Fail "$Label ancestor is linked: $($Current.FullName)"
        }
        $Acl = Get-Acl -LiteralPath $Current.FullName
        $Owner = $Acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
        if ($Owner -notin $AllowedSids) {
            Fail "$Label ancestor has an unapproved owner: $Owner"
        }
        foreach ($Rule in $Acl.Access) {
            if (($Rule.PropagationFlags -band
                    [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0 -or
                $Rule.AccessControlType -ne "Allow" -or
                ($Rule.FileSystemRights -band $UnsafeParentRights) -eq 0) {
                continue
            }
            try {
                $Sid = $Rule.IdentityReference.Translate(
                    [Security.Principal.SecurityIdentifier]
                ).Value
            } catch { Fail "$Label ancestor has an unresolvable replacement identity" }
            if ($Sid -notin $AllowedSids) {
                Fail "$Label ancestor permits replacement by: $Sid"
            }
        }
        $Current = $Current.Parent
    }
}

function Set-ProtectedDirectory([string]$Path) {
    $Security = [Security.AccessControl.DirectorySecurity]::new()
    $Security.SetAccessRuleProtection($true, $false)
    $Security.SetOwner([Security.Principal.SecurityIdentifier]::new("S-1-5-32-544"))
    $Inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    foreach ($SidValue in @("S-1-5-18", "S-1-5-32-544")) {
        $Rule = [Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($SidValue),
            [Security.AccessControl.FileSystemRights]::FullControl,
            $Inheritance,
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow
        )
        [void]$Security.AddAccessRule($Rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $Security
}

function Write-RunnerEvent([hashtable]$Event) {
    if ($null -eq $script:RunnerAudit) { return }
    $Event.run_id = $script:RunId
    $Event.timestamp = [DateTimeOffset]::UtcNow.ToString("o")
    $Bytes = [Text.UTF8Encoding]::new($false).GetBytes(
        (($Event | ConvertTo-Json -Depth 30 -Compress) + "`n")
    )
    $script:RunnerAudit.Write($Bytes, 0, $Bytes.Length)
    $script:RunnerAudit.Flush($true)
}

function ConvertTo-ProcessArgument([string]$Value) {
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    $Builder = [Text.StringBuilder]::new()
    [void]$Builder.Append('"')
    $Backslashes = 0
    foreach ($Character in $Value.ToCharArray()) {
        if ($Character -eq [char]92) {
            $Backslashes += 1
            continue
        }
        if ($Character -eq [char]34) {
            [void]$Builder.Append([string]::new([char]92, (2 * $Backslashes) + 1))
            [void]$Builder.Append([char]34)
            $Backslashes = 0
            continue
        }
        if ($Backslashes -gt 0) {
            [void]$Builder.Append([string]::new([char]92, $Backslashes))
            $Backslashes = 0
        }
        [void]$Builder.Append($Character)
    }
    if ($Backslashes -gt 0) {
        [void]$Builder.Append([string]::new([char]92, 2 * $Backslashes))
    }
    [void]$Builder.Append('"')
    return $Builder.ToString()
}

function Invoke-DockerProcess(
    [string[]]$Arguments, [string]$Label, [int]$TimeoutMilliseconds = 30000,
    [string]$ExecutablePath = $DockerPath, [switch]$SkipDockerHost
) {
    $EffectiveArguments = if ($SkipDockerHost) { $Arguments } else {
        @("--host", $DockerHost) + $Arguments
    }
    $Info = [Diagnostics.ProcessStartInfo]::new()
    $Info.FileName = $ExecutablePath
    $Info.Arguments = (($EffectiveArguments | ForEach-Object {
        ConvertTo-ProcessArgument $_
    }) -join " ")
    $Info.UseShellExecute = $false
    $Info.CreateNoWindow = $true
    $Info.RedirectStandardOutput = $true
    $Info.RedirectStandardError = $true
    foreach ($Name in @(
        "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CLI_PLUGIN_EXTRA_DIRS",
        "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION"
    )) {
        [void]$Info.EnvironmentVariables.Remove($Name)
    }
    if (-not [string]::IsNullOrWhiteSpace($script:DockerConfigRoot)) {
        $Info.EnvironmentVariables["DOCKER_CONFIG"] = $script:DockerConfigRoot
    }
    $Process = [Diagnostics.Process]::new()
    $Process.StartInfo = $Info
    if (-not $Process.Start()) { Fail "$Label did not start" }
    $OutTask = $Process.StandardOutput.ReadToEndAsync()
    $ErrTask = $Process.StandardError.ReadToEndAsync()
    if (-not $Process.WaitForExit($TimeoutMilliseconds)) {
        try { $Process.Kill() } catch { }
        $ExitedAfterKill = $Process.WaitForExit(5000)
        $OutputClosed = $ExitedAfterKill -and $OutTask.Wait(5000) -and $ErrTask.Wait(5000)
        return [ordered]@{
            exit_code = -1
            stdout = if ($OutputClosed) { $OutTask.Result.Trim() } else { "" }
            stderr = if ($ExitedAfterKill) {
                "$Label timed out"
            } else {
                "$Label timed out and its client process did not exit after termination"
            }
            timed_out = $true
            client_process_exited = $ExitedAfterKill
        }
    }
    $OutTask.Wait()
    $ErrTask.Wait()
    return [ordered]@{
        exit_code = $Process.ExitCode
        stdout = $OutTask.Result.Trim()
        stderr = $ErrTask.Result.Trim()
        timed_out = $false
        client_process_exited = $true
    }
}

function Get-ProbeContainerIds([string]$ContainerName, [string]$Label) {
    $Result = Invoke-DockerProcess @(
        "container", "ls", "--all", "--filter", "name=^/$ContainerName$",
        "--quiet", "--no-trunc"
    ) $Label 10000
    $Ids = if ($Result.exit_code -eq 0) {
        @($Result.stdout -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    } else { @() }
    return [ordered]@{ result = $Result; ids = $Ids }
}

function Assert-ProbeContainerNameAvailable([string]$ContainerName) {
    $Check = Get-ProbeContainerIds $ContainerName "probe container name preflight"
    if ($Check.result.exit_code -ne 0 -or $Check.ids.Count -ne 0) {
        Fail "probe container name is unavailable"
    }
}

function Remove-ProbeContainerAndConfirm(
    [string]$ContainerName, [int]$MinimumObservationMilliseconds = 1000
) {
    $Initial = Get-ProbeContainerIds $ContainerName "probe container cleanup inspection"
    $RemoveResults = @()
    $ConsecutiveAbsent = 0
    $Final = $Initial
    $Observation = [Diagnostics.Stopwatch]::StartNew()
    for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
        $Final = Get-ProbeContainerIds $ContainerName "probe container cleanup confirmation"
        if ($Final.result.exit_code -ne 0) {
            $ConsecutiveAbsent = 0
        } elseif ($Final.ids.Count -eq 0) {
            $ConsecutiveAbsent += 1
            if ($ConsecutiveAbsent -ge 3 -and
                $Observation.ElapsedMilliseconds -ge $MinimumObservationMilliseconds) { break }
        } else {
            $ConsecutiveAbsent = 0
            foreach ($Id in $Final.ids) {
                $RemoveResults += Invoke-DockerProcess @("container", "rm", "--force", $Id) `
                    "probe container cleanup" 10000
            }
        }
        Start-Sleep -Milliseconds 500
    }
    return [ordered]@{
        initial_check_exit_code = $Initial.result.exit_code
        initial_count = $Initial.ids.Count
        remove_exit_codes = @($RemoveResults | ForEach-Object { $_.exit_code })
        remove_timed_out = @($RemoveResults | Where-Object { $_.timed_out }).Count -ne 0
        final_check_exit_code = $Final.result.exit_code
        final_count = $Final.ids.Count
        consecutive_absent_checks = $ConsecutiveAbsent
        observation_milliseconds = $Observation.ElapsedMilliseconds
        confirmed_absent = $ConsecutiveAbsent -ge 3 -and
            $Observation.ElapsedMilliseconds -ge $MinimumObservationMilliseconds
    }
}

function Publish-ProbeAudit([string]$StagingRoot, [string]$StagedPath, [string]$FinalPath) {
    $Result = [ordered]@{ published = $false; path = $FinalPath; reason = $null }
    try {
        $Items = @(Get-ChildItem -Force -LiteralPath $StagingRoot -ErrorAction Stop)
        if ($Items.Count -ne 1 -or $Items[0].FullName -ine $StagedPath -or
            $Items[0].PSIsContainer -or
            ($Items[0].Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Fail "probe audit staging directory does not contain exactly the expected file"
        }
        if (Test-Path -LiteralPath $FinalPath) { Fail "probe audit destination already exists" }
        Move-Item -LiteralPath $StagedPath -Destination $FinalPath -ErrorAction Stop
        Assert-ProtectedPath $FinalPath "published probe audit"
        Assert-ProtectedAncestors $FinalPath "published probe audit"
        $Result.published = $true
    } catch { $Result.reason = $_.Exception.Message }
    return $Result
}

function Invoke-DockerText([string[]]$Arguments, [string]$Label) {
    $Result = Invoke-DockerProcess $Arguments $Label
    if ($Result.exit_code -ne 0) { Fail "$Label failed: $($Result.stderr)" }
    return $Result.stdout
}

function Read-ReleaseReceipt {
    Assert-ProtectedPath $ReceiptPath "release receipt"
    Assert-ProtectedAncestors $ReceiptPath "release receipt"
    try { $Receipt = Get-Content -LiteralPath $ReceiptPath -Raw | ConvertFrom-Json } catch {
        Fail "release receipt is invalid JSON"
    }
    $Expected = @(
        "schema_version", "candidate_id", "source_commit", "probe_sha256",
        "runner_sha256", "template_sha256", "gw_image_id", "installed_at"
    )
    $Actual = @($Receipt.PSObject.Properties.Name)
    if (@($Expected | Where-Object { $_ -cnotin $Actual }).Count -ne 0 -or
        @($Actual | Where-Object { $_ -cnotin $Expected }).Count -ne 0 -or
        $Receipt.schema_version -ne 1) { Fail "release receipt schema mismatch" }
    Assert-Sha256 $Receipt.probe_sha256 "receipt probe hash"
    Assert-Sha256 $Receipt.runner_sha256 "receipt runner hash"
    Assert-Sha256 $Receipt.template_sha256 "receipt template hash"
    Assert-Sha256 $Receipt.gw_image_id "receipt GW image" -ImageId
    if ($Receipt.source_commit -cnotmatch '^[0-9a-f]{40}$') { Fail "receipt source commit is invalid" }
    return $Receipt
}

function ConvertTo-GatewayBoundary([object]$Value) {
    $SerialEnvironment = @($Value.Config.Env | Where-Object { $_ -match '^GW_SERIAL_' })
    $Devices = @($Value.HostConfig.Devices | Where-Object { $null -ne $_ })
    $Binds = @($Value.HostConfig.Binds)
    $Mounts = @($Value.Mounts)
    $DevBinds = @($Binds | Where-Object {
        $Parts = ([string]$_).Split(":")
        @($Parts | Where-Object { Test-DevicePath $_ }).Count -ne 0
    })
    $DevMounts = @($Mounts | Where-Object {
        (Test-DevicePath ([string]$_.Source)) -or (Test-DevicePath ([string]$_.Destination))
    })
    return [ordered]@{
        image_id = [string]$Value.Image
        serial_environment = $SerialEnvironment
        devices = $Devices
        dev_binds = $DevBinds
        dev_mounts = $DevMounts
        dev_tmpfs = @($Value.HostConfig.Tmpfs.PSObject.Properties.Name | Where-Object {
            Test-DevicePath $_
        })
        privileged = [bool]$Value.HostConfig.Privileged
        device_cgroup_rules = @(
            $Value.HostConfig.DeviceCgroupRules | Where-Object { $null -ne $_ }
        )
    }
}

function Get-GatewayBoundary {
    $Raw = Invoke-DockerText @("inspect", "ruisheng-gw") "GW inspection"
    return ConvertTo-GatewayBoundary @($Raw | ConvertFrom-Json)[0]
}

function Get-ProductionState {
    $Containers = @()
    foreach ($Name in $ContainerNames) {
        $Raw = Invoke-DockerText @("inspect", $Name) "container inspection"
        $Value = @($Raw | ConvertFrom-Json)[0]
        $Health = if ($null -eq $Value.State.Health) { "none" } else { [string]$Value.State.Health.Status }
        $Containers += [ordered]@{
            name = $Name
            id = [string]$Value.Id
            image_id = [string]$Value.Image
            status = [string]$Value.State.Status
            health = $Health
            started_at = [string]$Value.State.StartedAt
            restart_count = [int64]$Value.RestartCount
        }
    }
    $Sql = "SELECT (SELECT count(*) FROM devices)::text || '|' || (SELECT count(*) FROM device_points)::text"
    $Counts = Invoke-DockerText @(
        "exec", "ruisheng-postgres", "psql", "-v", "ON_ERROR_STOP=1",
        "-U", "ruisheng_admin", "-d", "ruisheng", "-At", "-c", $Sql
    ) "read-only production count query"
    if ($Counts -cnotmatch '^[0-9]+\|[0-9]+$') { Fail "production count result is malformed" }
    $Parts = $Counts.Split("|")
    return [ordered]@{
        containers = $Containers
        database_counts_raw = $Counts
        device_count = [int64]$Parts[0]
        device_point_count = [int64]$Parts[1]
        gateway = Get-GatewayBoundary
    }
}

function Assert-SafeProductionState(
    [System.Collections.IDictionary]$State, [string]$ExpectedImageId
) {
    foreach ($Container in @($State.containers)) {
        if ($Container.status -cne "running") { Fail "$($Container.name) is not running" }
        $RequiresHealth = $Container.name -in @("ruisheng-postgres", "ruisheng-redis") -or
            $Container.health -cne "none"
        if ($RequiresHealth -and $Container.health -cne "healthy") {
            Fail "$($Container.name) is not healthy"
        }
    }
    if ($State.device_count -ne 0 -or $State.device_point_count -ne 0) {
        Fail "production devices/device_points must remain empty"
    }
    if ($State.gateway.image_id -cne $ExpectedImageId) { Fail "running GW image is not approved" }
    if (@($State.gateway.serial_environment).Count -ne 0 -or
        @($State.gateway.devices).Count -ne 0 -or
        @($State.gateway.dev_binds).Count -ne 0 -or
        @($State.gateway.dev_mounts).Count -ne 0 -or
        @($State.gateway.dev_tmpfs).Count -ne 0 -or
        $State.gateway.privileged -or
        @($State.gateway.device_cgroup_rules).Count -ne 0) {
        Fail "GW has serial or device access capability"
    }
}

function Read-ProbeTerminal([string]$Output) {
    $Prefix = "RUISHENG_PROBE_TERMINAL="
    $Lines = @($Output -split "`r?`n" | Where-Object { $_.StartsWith($Prefix) })
    if ($Lines.Count -ne 1) { return $null }
    try { return $Lines[0].Substring($Prefix.Length) | ConvertFrom-Json } catch { return $null }
}

function Assert-ProbeAuditSequence([object[]]$Events) {
    $ExpectedRequests = @(
        [ordered]@{ index = 0; address = 0; count = 6; frame = "010300000006c5c8" },
        [ordered]@{ index = 1; address = 27; count = 9; frame = "0103001b0009f5cb" }
    )
    if ($Events.Count -lt 3 -or $Events[1].event -cne "port_verified") {
        Fail "probe audit lacks the verified port event"
    }
    $Pending = $null
    $ValidRequestIndexes = @()
    for ($Index = 2; $Index -lt $Events.Count - 1; $Index++) {
        $Event = $Events[$Index]
        if ($Event.event -ceq "request_tx") {
            if ($null -ne $Pending -or $Event.request_index -notin 0..1 -or
                $Event.attempt -notin 0..1 -or $Event.tx_number -notin 1..4) {
                Fail "probe audit request sequence is invalid"
            }
            $Expected = $ExpectedRequests[[int]$Event.request_index]
            if ($Event.function_code -ne 3 -or $Event.start_address -ne $Expected.address -or
                $Event.register_count -ne $Expected.count -or $Event.tx_hex -cne $Expected.frame) {
                Fail "probe audit contains an out-of-scope request"
            }
            $Pending = $Event
            continue
        }
        if ($Event.event -ceq "response_rx") {
            if ($null -eq $Pending -or $Event.request_index -ne $Pending.request_index -or
                $Event.attempt -ne $Pending.attempt -or $Event.tx_number -ne $Pending.tx_number -or
                $Event.latency_ms -lt 0 -or $Event.rx_hex -cnotmatch '^(?:[0-9a-f]{2})*$' -or
                $Event.classification -notin @(
                    "valid", "modbus_exception", "invalid_exception", "timeout", "noise",
                    "mismatch", "truncated", "crc_error"
                )) {
                Fail "probe audit response sequence is invalid"
            }
            if ($Event.classification -ceq "valid") {
                $Expected = $ExpectedRequests[[int]$Event.request_index]
                if ($Event.crc_valid -ne $true -or
                    @($Event.registers).Count -ne $Expected.count -or
                    $Event.conclusion -cne "仅证明区间可读，型号/点名/倍率未决") {
                    Fail "probe audit valid response evidence is incomplete"
                }
                $ValidRequestIndexes += [int]$Event.request_index
            }
            $Pending = $null
            continue
        }
        Fail "probe audit contains an unexpected event"
    }
    $TerminalEvent = $Events[-1]
    if ($TerminalEvent.event -ceq "completed" -and $null -ne $Pending) {
        Fail "completed probe audit has a request without a response"
    }
    if ($TerminalEvent.event -ceq "completed" -and $TerminalEvent.result -ceq "valid" -and
        (@($ValidRequestIndexes).Count -ne 2 -or $ValidRequestIndexes[0] -ne 0 -or
            $ValidRequestIndexes[1] -ne 1)) {
        Fail "successful probe audit lacks both ordered valid responses"
    }
}

function Assert-ProbeTerminalMatches(
    [object[]]$Events, [object]$Terminal, [int]$ExitCode
) {
    $TerminalFields = @(
        "exit_code", "result", "completed_tx_count", "attempted_write_bytes", "tx_count_known",
        "audit_complete"
    )
    if ($null -eq $Terminal -or
        @($TerminalFields | Where-Object { $_ -cnotin $Terminal.PSObject.Properties.Name }).Count -ne 0 -or
        $Terminal.exit_code -ne $ExitCode) {
        Fail "probe terminal summary is invalid or does not match the process exit code"
    }
    $TxCount = @($Events | Where-Object { $_.event -ceq "request_tx" }).Count
    if (-not $Terminal.audit_complete) { Fail "probe reported an incomplete audit sink" }
    if ($Terminal.tx_count_known) {
        if ($Terminal.completed_tx_count -notin 0..4 -or
            $Terminal.attempted_write_bytes -lt 0 -or
            $Terminal.attempted_write_bytes -gt 32 -or
            $Terminal.completed_tx_count -ne $TxCount -or
            $Events[-1].completed_tx_count -ne $TxCount) {
            Fail "probe terminal TX count mismatch"
        }
    } elseif ($null -ne $Terminal.completed_tx_count -or
        $null -ne $Terminal.attempted_write_bytes -or
        $Events[-1].tx_count_known -ne $false -or
        $null -ne $Events[-1].completed_tx_count -or
        $null -ne $Events[-1].attempted_write_bytes -or
        $Terminal.result -cne "aborted" -or $ExitCode -eq 0) {
        Fail "probe terminal must report unknown TX after an indeterminate write"
    }
    if (($Events[-1].event -ceq "completed" -and
            ($Terminal.result -cne $Events[-1].result -or -not $Terminal.tx_count_known -or
                $Terminal.attempted_write_bytes -ne 8 * $TxCount)) -or
        ($Events[-1].event -ceq "aborted" -and
            ($Terminal.result -cne "aborted" -or
                $Terminal.attempted_write_bytes -ne $Events[-1].attempted_write_bytes -or
                $Terminal.tx_count_known -ne $Events[-1].tx_count_known))) {
        Fail "probe terminal summary does not match the terminal audit event"
    }
}

function Test-ProbeAudit(
    [string]$Path, [object]$Terminal, [object]$Receipt,
    [string]$ConfigHash, [string]$ReceiptHash, [int]$ExitCode
) {
    $Evidence = [ordered]@{ valid = $false; path = $Path; sha256 = $null; reason = $null }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        $Evidence.reason = "probe audit is missing"
        return $Evidence
    }
    try {
        Assert-ProtectedPath $Path "probe audit"
        $Lines = @(Get-Content -LiteralPath $Path | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
        $Events = @($Lines | ForEach-Object { $_ | ConvertFrom-Json })
        if ($Events.Count -lt 2 -or $Events[0].event -cne "run_started" -or
            $Events[-1].event -notin @("completed", "aborted")) { Fail "probe audit is not closed" }
        if (@($Events | Where-Object { $_.run_id -cne $script:RunId }).Count -ne 0) {
            Fail "probe audit run ID mismatch"
        }
        Assert-ProbeAuditSequence $Events
        Assert-ProbeTerminalMatches $Events $Terminal $ExitCode
        $Started = $Events[0]
        if ($Started.script_sha256 -cne $Receipt.probe_sha256 -or
            $Started.config_sha256 -cne $ConfigHash -or
            $Started.image_id -cne $Receipt.gw_image_id -or
            $Started.receipt_sha256 -cne $ReceiptHash -or
            $Started.approval_scope -cne $ApprovalScope) { Fail "probe audit binding mismatch" }
        if ($ExitCode -eq 0 -and ($Events[-1].event -cne "completed" -or
            $Events[-1].result -cne "valid")) {
            Fail "successful probe lacks a valid terminal audit"
        }
        $Evidence.sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
        $Evidence.valid = $true
    } catch { $Evidence.reason = $_.Exception.Message }
    return $Evidence
}

if ($SelfTest) {
    $SelfEvents = @(
        [pscustomobject]@{ event = "run_started" },
        [pscustomobject]@{ event = "port_verified" },
        [pscustomobject]@{
            event = "request_tx"; request_index = 0; attempt = 0; tx_number = 1
            function_code = 3; start_address = 0; register_count = 6
            tx_hex = "010300000006c5c8"
        },
        [pscustomobject]@{
            event = "response_rx"; request_index = 0; attempt = 0; tx_number = 1
            latency_ms = 1; rx_hex = "01030c0003000000000000000000009c34"
            classification = "valid"; crc_valid = $true; registers = @(3, 0, 0, 0, 0, 0)
            conclusion = "仅证明区间可读，型号/点名/倍率未决"
        },
        [pscustomobject]@{ event = "aborted"; completed_tx_count = 1; attempted_write_bytes = 8; tx_count_known = $true }
    )
    $SelfTerminal = [pscustomobject]@{
        exit_code = 1; result = "aborted"; completed_tx_count = 1
        attempted_write_bytes = 8; tx_count_known = $true; audit_complete = $true
    }
    Assert-ProbeAuditSequence $SelfEvents
    Assert-ProbeTerminalMatches $SelfEvents $SelfTerminal 1
    $AuditTerminalValid = $true
    $SelfTerminal.exit_code = 2
    $ExitMismatchRejected = $false
    try { [void](Assert-ProbeTerminalMatches $SelfEvents $SelfTerminal 1) } catch {
        $ExitMismatchRejected = $true
    }
    $IncompleteAuditRejected = $false
    try {
        Assert-ProbeAuditSequence @(
            [pscustomobject]@{ event = "run_started" },
            [pscustomobject]@{ event = "port_verified" },
            $SelfEvents[2],
            [pscustomobject]@{
                event = "completed"; result = "valid"; completed_tx_count = 1
                attempted_write_bytes = 8; tx_count_known = $true
            }
        )
    } catch { $IncompleteAuditRejected = $true }
    $SelfImage = "sha256:" + ("a" * 64)
    $SelfGatewayInspect = @{
        Image = $SelfImage
        Config = @{ Env = @() }
        HostConfig = @{
            Devices = @(); Binds = @(); Mounts = @(); Tmpfs = @{ "/dev/hidden" = "rw" }
            Privileged = $false; DeviceCgroupRules = @()
        }
        Mounts = @()
    } | ConvertTo-Json -Depth 10 | ConvertFrom-Json
    $SelfContainers = @($ContainerNames | ForEach-Object {
        [ordered]@{
            name = $_; id = "id-$_"; image_id = $SelfImage; status = "running"
            health = if ($_ -in @("ruisheng-postgres", "ruisheng-redis")) { "healthy" } else { "none" }
            started_at = "2026-08-25T00:00:00Z"; restart_count = 0
        }
    })
    $TmpfsRejected = $false
    try {
        Assert-SafeProductionState ([ordered]@{
            containers = $SelfContainers; device_count = 0; device_point_count = 0
            gateway = ConvertTo-GatewayBoundary $SelfGatewayInspect
        }) $SelfImage
    } catch { $TmpfsRejected = $true }
    $PowerShellPath = Join-Path ([Environment]::SystemDirectory) `
        "WindowsPowerShell\v1.0\powershell.exe"
    $TimeoutSelfTest = Invoke-DockerProcess @(
        "-NoProfile", "-Command", "Start-Sleep -Seconds 30"
    ) "timeout self-test" 100 -ExecutablePath $PowerShellPath -SkipDockerHost
    [ordered]@{
        exact_dev = Test-DevicePath "/dev"
        child_dev = Test-DevicePath "/dev/ttyUSB0"
        root_exposes_dev = Test-DevicePath "/"
        similar_path = Test-DevicePath "/device"
        quoted_argument = ConvertTo-ProcessArgument "SELECT 1"
        embedded_quote = ConvertTo-ProcessArgument 'a"b'
        trailing_slash = ConvertTo-ProcessArgument 'C:\path with space\'
        empty_argument = ConvertTo-ProcessArgument ""
        docker_host = $DockerHost
        audit_terminal_valid = $AuditTerminalValid
        exit_mismatch_rejected = $ExitMismatchRejected
        incomplete_audit_rejected = $IncompleteAuditRejected
        tmpfs_rejected = $TmpfsRejected
        process_timeout_bounded = $TimeoutSelfTest.timed_out -and
            $TimeoutSelfTest.client_process_exited
    } | ConvertTo-Json -Compress
    exit 0
}

$RunnerAuditPath = Join-Path $AuditRoot "modbus-runner-$($script:RunId).jsonl"
$ProbeExitCode = 1
$RawProbeExitCode = 1
$Terminal = $null
$ContainerCleanup = $null
$DryRun = $null
$Before = $null
$After = $null
$ProbeAuditStagingRoot = $null
$StagedProbeAuditPath = $null
$AuditPublish = $null
$ProbeEvidence = [ordered]@{ valid = $false; path = $AuditPath; sha256 = $null; reason = "not started" }
try {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = [Security.Principal.WindowsPrincipal]::new($Identity)
    if ($Identity.User.Value -ne "S-1-5-18" -and -not $Principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator
    )) { Fail "runner must be elevated" }
    foreach ($Name in @(
        "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CLI_PLUGIN_EXTRA_DIRS",
        "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION"
    )) {
        Remove-Item "Env:$Name" -ErrorAction SilentlyContinue
    }
    Assert-ProtectedPath $DockerPath "Docker CLI"
    Assert-ProtectedAncestors $DockerPath "Docker CLI"
    Assert-ProtectedPath $AuditRoot "audit directory" -Directory
    Assert-ProtectedAncestors $AuditRoot "audit directory"
    Assert-ProtectedPath $SiteRoot "site directory" -Directory
    Assert-ProtectedAncestors $SiteRoot "site directory"
    Assert-ProtectedPath $WorkRoot "probe work directory" -Directory
    Assert-ProtectedAncestors $WorkRoot "probe work directory"
    $script:RunRoot = Join-Path $WorkRoot $script:RunId
    [void](New-Item -ItemType Directory -Path $script:RunRoot)
    Set-ProtectedDirectory $script:RunRoot
    Assert-ProtectedPath $script:RunRoot "probe run directory" -Directory
    Assert-ProtectedAncestors $script:RunRoot "probe run directory"
    $script:DockerConfigRoot = Join-Path $script:RunRoot "docker-config"
    [void](New-Item -ItemType Directory -Path $script:DockerConfigRoot)
    Set-ProtectedDirectory $script:DockerConfigRoot
    [IO.File]::WriteAllText(
        (Join-Path $script:DockerConfigRoot "config.json"), "{}`n", [Text.Encoding]::ASCII
    )
    Assert-ProtectedPath (Join-Path $script:DockerConfigRoot "config.json") `
        "probe Docker config"
    $ProbeAuditStagingRoot = Join-Path $script:RunRoot "probe-audit"
    [void](New-Item -ItemType Directory -Path $ProbeAuditStagingRoot)
    Set-ProtectedDirectory $ProbeAuditStagingRoot
    Assert-ProtectedPath $ProbeAuditStagingRoot "probe audit staging directory" -Directory
    $StagedProbeAuditPath = Join-Path $ProbeAuditStagingRoot "probe.jsonl"
    $script:RunnerAudit = [IO.FileStream]::new(
        $RunnerAuditPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
        [IO.FileShare]::None, 4096, [IO.FileOptions]::WriteThrough
    )
    Write-RunnerEvent @{ event = "runner_started"; execute = [bool]$Execute; probe_audit_path = $AuditPath }

    $Receipt = Read-ReleaseReceipt
    $ReceiptHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ReceiptPath).Hash.ToLowerInvariant()
    if ([IO.Path]::GetFullPath($PSCommandPath) -ine [IO.Path]::GetFullPath($RunnerPath)) {
        Fail "runner must execute from the authenticated installation path"
    }
    $ConfigPath = Assert-DirectChild $ConfigPath $SiteRoot "config path"
    $AuditPath = Assert-DirectChild $AuditPath $AuditRoot "probe audit path"
    if ([IO.Path]::GetExtension($AuditPath) -cne ".jsonl" -or
        (Test-Path -LiteralPath $AuditPath)) { Fail "probe audit path must be a new JSONL file" }
    foreach ($Pair in @(@($RunnerPath, "runner"), @($ProbePath, "probe"), @($ConfigPath, "config"))) {
        Assert-ProtectedPath $Pair[0] $Pair[1]
        Assert-ProtectedAncestors $Pair[0] $Pair[1]
    }
    $ActualRunnerHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $RunnerPath).Hash.ToLowerInvariant()
    if ($ActualRunnerHash -cne $Receipt.runner_sha256) { Fail "installed runner hash mismatch" }
    $SnapshotProbe = Join-Path $script:RunRoot "probe_modbus_rtu.py"
    $SnapshotConfig = Join-Path $script:RunRoot "probe.json"
    Copy-Item -LiteralPath $ProbePath -Destination $SnapshotProbe
    Copy-Item -LiteralPath $ConfigPath -Destination $SnapshotConfig
    $ProbeHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $SnapshotProbe).Hash.ToLowerInvariant()
    $ConfigHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $SnapshotConfig).Hash.ToLowerInvariant()
    if ($ProbeHash -cne $Receipt.probe_sha256) { Fail "snapshot probe hash mismatch" }

    $Before = Get-ProductionState
    Assert-SafeProductionState $Before $Receipt.gw_image_id
    Write-RunnerEvent @{
        event = "preflight_passed"; receipt_sha256 = $ReceiptHash
        probe_sha256 = $ProbeHash; config_sha256 = $ConfigHash
        candidate_id = $Receipt.candidate_id; source_commit = $Receipt.source_commit
        image_id = $Receipt.gw_image_id; approval_scope = $ApprovalScope
        probe_audit_path = $AuditPath; production_state = $Before
    }

    $ContainerName = "ruisheng-modbus-probe-$($script:RunId.Substring(0, 8))"
    Assert-ProbeContainerNameAvailable $ContainerName
    $Arguments = @(
        "container", "create", "--name", $ContainerName, "--rm", "--network", "none",
        "--read-only",
        "--pull", "never",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "64",
        "--memory", "128m", "--cpus", "0.5",
        "--mount", "type=bind,source=$SnapshotProbe,target=/opt/ruisheng/probe_modbus_rtu.py,readonly",
        "--mount", "type=bind,source=$SnapshotConfig,target=/opt/ruisheng/probe.json,readonly"
    )
    if ($Execute) {
        $Arguments += @(
            "--device", "${DevicePath}:${DevicePath}:rwm",
            "--mount", "type=bind,source=$ProbeAuditStagingRoot,target=/audit"
        )
    }
    $Arguments += @(
        "--entrypoint", "python", $Receipt.gw_image_id,
        "/opt/ruisheng/probe_modbus_rtu.py", "--config", "/opt/ruisheng/probe.json"
    )
    if ($Execute) {
        $Arguments += @(
            "--execute", "--audit-path", "/audit/probe.jsonl",
            "--expected-config-sha256", $ConfigHash, "--expected-script-sha256", $ProbeHash,
            "--image-id", $Receipt.gw_image_id, "--approval-scope", $ApprovalScope,
            "--receipt-sha256", $ReceiptHash, "--run-id", $script:RunId
        )
    }
    $script:ProbeContainerName = $ContainerName
    $script:ProbeContainerAbsent = $false
    $CreateResult = Invoke-DockerProcess $Arguments "Modbus probe container creation" 30000
    if ($CreateResult.exit_code -ne 0 -or $CreateResult.stdout -cnotmatch '^[0-9a-f]{64}$') {
        $CleanupWindow = if ($CreateResult.timed_out) { 5000 } else { 1000 }
        $ContainerCleanup = Remove-ProbeContainerAndConfirm $ContainerName $CleanupWindow
        $script:ProbeContainerAbsent = [bool]$ContainerCleanup.confirmed_absent
        Fail "probe container creation failed or returned an invalid container ID"
    }
    $CreatedContainerId = $CreateResult.stdout
    $CreatedCheck = Get-ProbeContainerIds $ContainerName "created probe container inspection"
    if ($CreatedCheck.result.exit_code -ne 0 -or $CreatedCheck.ids.Count -ne 1 -or
        $CreatedCheck.ids[0] -cne $CreatedContainerId) {
        $ContainerCleanup = Remove-ProbeContainerAndConfirm $ContainerName
        $script:ProbeContainerAbsent = [bool]$ContainerCleanup.confirmed_absent
        Fail "created probe container identity mismatch"
    }
    Write-RunnerEvent @{
        event = "probe_container_created"; container_id = $CreatedContainerId
        create_exit_code = $CreateResult.exit_code
    }
    $script:ProbeStarted = $true
    $ProbeResult = Invoke-DockerProcess @(
        "container", "start", "--attach", $CreatedContainerId
    ) "Modbus probe" 45000
    $RawProbeExitCode = [int]$ProbeResult.exit_code
    $ProbeExitCode = $RawProbeExitCode
    $CleanupWindow = if ($ProbeResult.timed_out) { 5000 } else { 1000 }
    $ContainerCleanup = Remove-ProbeContainerAndConfirm $ContainerName $CleanupWindow
    $script:ProbeContainerAbsent = [bool]$ContainerCleanup.confirmed_absent
    $Terminal = Read-ProbeTerminal $ProbeResult.stdout
    if ($Execute) {
        $AuditPublish = Publish-ProbeAudit `
            $ProbeAuditStagingRoot $StagedProbeAuditPath $AuditPath
        if ($AuditPublish.published) {
            $ProbeEvidence = Test-ProbeAudit `
                $AuditPath $Terminal $Receipt $ConfigHash $ReceiptHash $ProbeExitCode
        } else {
            $ProbeEvidence = [ordered]@{
                valid = $false; path = $AuditPath; sha256 = $null; reason = $AuditPublish.reason
            }
        }
        if (-not $ProbeEvidence.valid) { $ProbeExitCode = 1 }
    } elseif ($ProbeExitCode -eq 0) {
        try {
            $DryRun = $ProbeResult.stdout | ConvertFrom-Json
            if ($DryRun.mode -cne "dry-run" -or $DryRun.config_sha256 -cne $ConfigHash -or
                $DryRun.script_sha256 -cne $ProbeHash) { Fail "dry-run binding mismatch" }
        } catch { Fail "dry-run output is invalid" }
    }
    Write-RunnerEvent @{
        event = "probe_finished"; process_exit_code = $RawProbeExitCode
        accepted_exit_code = $ProbeExitCode; timed_out = $ProbeResult.timed_out
        terminal = $Terminal; probe_audit = $ProbeEvidence; container_cleanup = $ContainerCleanup
        audit_publish = $AuditPublish; dry_run = $DryRun; stderr = $ProbeResult.stderr
    }

    $After = Get-ProductionState
    Assert-SafeProductionState $After $Receipt.gw_image_id
    if (($Before | ConvertTo-Json -Depth 30 -Compress) -cne
        ($After | ConvertTo-Json -Depth 30 -Compress)) { Fail "production state changed" }
    if (-not $ContainerCleanup.confirmed_absent) {
        Fail "probe container cleanup could not be confirmed"
    }
    if ($ProbeExitCode -ne 0) { Fail "probe did not produce an accepted result" }
    Write-RunnerEvent @{
        event = "runner_completed"; probe_exit_code = 0
        probe_audit = $ProbeEvidence; production_state = $After
    }
    if (-not $Execute) {
        Write-Output ($DryRun | ConvertTo-Json -Depth 20 -Compress)
    }
    Write-Host "[modbus-runner] audit: $RunnerAuditPath"
    exit 0
} catch {
    $FailureDetail = $_.Exception.Message
    if (-not $script:ProbeContainerAbsent -and
        -not [string]::IsNullOrWhiteSpace($script:ProbeContainerName)) {
        try {
            $ContainerCleanup = Remove-ProbeContainerAndConfirm `
                $script:ProbeContainerName 5000
            $script:ProbeContainerAbsent = [bool]$ContainerCleanup.confirmed_absent
        } catch {
            $ContainerCleanup = [ordered]@{
                confirmed_absent = $false
                cleanup_error = $_.Exception.Message
            }
        }
    }
    $AfterCaptureError = $null
    if ($null -ne $Before -and $null -eq $After) {
        try { $After = Get-ProductionState } catch {
            $AfterCaptureError = $_.Exception.Message
        }
    }
    try {
        Write-RunnerEvent @{
            event = if ($script:ProbeStarted) { "runner_failed" } else { "rejected_zero_tx" }
            detail = $FailureDetail; probe_started = $script:ProbeStarted
            process_exit_code = $RawProbeExitCode; accepted_exit_code = $ProbeExitCode
            terminal = $Terminal; probe_audit = $ProbeEvidence; container_cleanup = $ContainerCleanup
            audit_publish = $AuditPublish; production_state_before = $Before
            production_state_after = $After
            production_state_after_error = $AfterCaptureError
            production_state_unchanged = if ($null -eq $Before -or $null -eq $After) {
                $null
            } else {
                ($Before | ConvertTo-Json -Depth 30 -Compress) -ceq
                    ($After | ConvertTo-Json -Depth 30 -Compress)
            }
            tx_count = if ($null -eq $Terminal -or -not $Terminal.tx_count_known -or
                $Terminal.completed_tx_count -notin 0..4) { "unknown" } else {
                $Terminal.completed_tx_count
            }
        }
    } catch { }
    Write-Host "[modbus-runner] audit: $RunnerAuditPath"
    Write-Error $FailureDetail -ErrorAction Continue
    exit 1
} finally {
    if (-not $script:ProbeContainerAbsent -and
        -not [string]::IsNullOrWhiteSpace($script:ProbeContainerName)) {
        try {
            $FinalCleanup = Remove-ProbeContainerAndConfirm $script:ProbeContainerName 5000
            $script:ProbeContainerAbsent = [bool]$FinalCleanup.confirmed_absent
            if (-not $FinalCleanup.confirmed_absent) {
                Write-Error "[modbus-runner] probe container cleanup remains unconfirmed"
            }
        } catch {
            Write-Error "[modbus-runner] final probe container cleanup failed: $($_.Exception.Message)"
        }
    }
    if ($null -ne $script:RunnerAudit) {
        try { $script:RunnerAudit.Flush($true) } catch { }
        $script:RunnerAudit.Dispose()
    }
}
