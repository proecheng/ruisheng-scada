[CmdletBinding()]
param(
    [ValidatePattern('^[0-9a-f]{32}$')]
    [string]$TransactionId,
    [switch]$RecoverActive
)

$ErrorActionPreference = "Stop"
$taskName = "Ruisheng B08 Freshness Witness"
$root = "C:\ProgramData\RuishengWitness"
$migration = Join-Path $root "migration"
$runtime = Join-Path $root "runtime"
$witness = Join-Path $root "freshness_witness.py"
$manifest = Join-Path $migration "runtime-manifest.json"
$serviceStderr = Join-Path $migration "witness-stderr.log"
$activeTransactionPath = Join-Path $migration "active-transaction.json"
$currentInstallationPath = Join-Path $migration "current-installation.json"
$listenAddress = "100.67.229.19"
$listenPort = 38475
$mutexName = "Global\RuishengWitness-SystemAutostart-Migration"

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "administrator token required"
    }
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function New-ProtectedFileSecurity {
    $security = [Security.AccessControl.FileSecurity]::new()
    $security.SetAccessRuleProtection($true, $false)
    $administrators = [Security.Principal.SecurityIdentifier]::new("S-1-5-32-544")
    $system = [Security.Principal.SecurityIdentifier]::new("S-1-5-18")
    $security.SetOwner($administrators)
    foreach ($sid in @($system, $administrators)) {
        [void]$security.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [Security.AccessControl.FileSystemRights]::FullControl,
            [Security.AccessControl.AccessControlType]::Allow
        ))
    }
    return $security
}

function Write-ProtectedJson([string]$Path, [object]$Value) {
    [IO.File]::WriteAllText(
        $Path,
        (($Value | ConvertTo-Json -Depth 12) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
    Set-Acl -LiteralPath $Path -AclObject (New-ProtectedFileSecurity)
}

function Restore-AclSnapshot([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    $snapshot = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    foreach ($entry in @($snapshot)) {
        $target = [string]$entry.path
        if (-not (Test-Path -LiteralPath $target)) { continue }
        if ([bool]$entry.directory) {
            $security = [Security.AccessControl.DirectorySecurity]::new()
        } else {
            $security = [Security.AccessControl.FileSecurity]::new()
        }
        $security.SetSecurityDescriptorSddlForm([string]$entry.sddl)
        Set-Acl -LiteralPath $target -AclObject $security
    }
}

function Get-Listeners {
    return @(Get-NetTCPConnection -LocalAddress $listenAddress -LocalPort $listenPort `
        -State Listen -ErrorAction SilentlyContinue)
}

function Wait-Listener([bool]$Present, [int]$Seconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        $listeners = @(Get-Listeners)
        if (($Present -and $listeners.Count -eq 1) -or
            (-not $Present -and $listeners.Count -eq 0)) {
            return $listeners
        }
        Start-Sleep -Milliseconds 250
    } until ([DateTime]::UtcNow -ge $deadline)
    throw $(if ($Present) { "witness listener did not appear" } else {
        "witness listener did not stop"
    })
}

function Wait-TaskNotRunning([int]$Seconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($null -eq $task -or $task.State.ToString() -ne "Running") { return }
        Start-Sleep -Milliseconds 250
    } until ([DateTime]::UtcNow -ge $deadline)
    throw "witness task did not stop during rollback"
}

function Get-ProcessCreationTime([object]$Process) {
    if ($Process.CreationDate -is [DateTime]) { return [DateTime]$Process.CreationDate }
    return [Management.ManagementDateTimeConverter]::ToDateTime(
        [string]$Process.CreationDate
    )
}

function Assert-RestoredTaskListener([int]$ProcessId, [object]$Task) {
    $action = @($Task.Actions)[0]
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId"
    if ($null -eq $process -or
        ([string]$process.CommandLine).IndexOf(
            $witness, [StringComparison]::OrdinalIgnoreCase
        ) -lt 0 -or
        ([string]$process.CommandLine) -notmatch '(?i)(?:^|\s)serve(?:\s|$)') {
        throw "restored witness listener command is invalid"
    }
    $actionPath = [IO.Path]::GetFullPath([string]$action.Execute)
    $owner = $process
    if ([IO.Path]::GetFullPath([string]$process.ExecutablePath) -cne $actionPath) {
        $owner = Get-CimInstance Win32_Process `
            -Filter "ProcessId=$([int]$process.ParentProcessId)"
        if ($null -eq $owner -or
            [IO.Path]::GetFullPath([string]$owner.ExecutablePath) -cne $actionPath -or
            ([string]$owner.CommandLine).IndexOf(
                $witness, [StringComparison]::OrdinalIgnoreCase
            ) -lt 0) {
            throw "restored witness process tree does not match the task action"
        }
    }
    $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
    if ($Task.State.ToString() -cne "Running" -or
        (Get-ProcessCreationTime $owner) -lt $taskInfo.LastRunTime.AddSeconds(-2)) {
        throw "restored witness process is not bound to the task run"
    }
}

function Assert-RuntimeMatchesManifest([string]$RuntimePath, [string]$ManifestPath) {
    $value = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json
    $entries = @($value.files)
    if ($entries.Count -eq 0) { throw "previous runtime manifest is empty" }
    $runtimePrefix = [IO.Path]::GetFullPath($RuntimePath).TrimEnd('\') + '\'
    $expectedPaths = @()
    foreach ($entry in $entries) {
        $relativePath = [string]$entry.relative_path
        $target = [IO.Path]::GetFullPath((Join-Path $RuntimePath $relativePath.Replace('/', '\')))
        if ([IO.Path]::IsPathRooted($relativePath) -or
            $target.IndexOf($runtimePrefix, [StringComparison]::OrdinalIgnoreCase) -ne 0) {
            throw "previous runtime manifest path escaped runtime: $relativePath"
        }
        $expectedHash = if ($entry.PSObject.Properties.Name -contains "sha256") {
            [string]$entry.sha256
        } else {
            [string]$entry.runtime_sha256
        }
        if (-not (Test-Path -LiteralPath $target -PathType Leaf) -or
            (Get-Sha256 $target) -cne $expectedHash) {
            throw "previous runtime hash mismatch: $relativePath"
        }
        if ($entry.PSObject.Properties.Name -contains "size" -and
            [int64](Get-Item -LiteralPath $target).Length -ne [int64]$entry.size) {
            throw "previous runtime size mismatch: $relativePath"
        }
        $expectedPaths += $relativePath
    }
    $actualPaths = @(Get-ChildItem -LiteralPath $RuntimePath -Recurse -File | ForEach-Object {
        ([IO.Path]::GetFullPath($_.FullName).Substring($runtimePrefix.Length)).Replace('\', '/')
    })
    if (@(Compare-Object -ReferenceObject $expectedPaths -DifferenceObject $actualPaths `
        -CaseSensitive).Count) {
        throw "previous runtime inventory differs from its protected manifest"
    }
}

Assert-Administrator
$mutex = [Threading.Mutex]::new($false, $mutexName)
$mutexAcquired = $false
$result = $null
try {
    try {
        $mutexAcquired = $mutex.WaitOne(0)
    } catch [Threading.AbandonedMutexException] {
        $mutexAcquired = $true
    }
    if (-not $mutexAcquired) { throw "another witness migration or rollback is already running" }

    $active = $null
    if (Test-Path -LiteralPath $activeTransactionPath -PathType Leaf) {
        $active = Get-Content -Raw -LiteralPath $activeTransactionPath | ConvertFrom-Json
        if ([int]$active.schema_version -ne 1 -or
            [string]$active.artifact_type -cne
                "ruisheng.witness-system-autostart-active-transaction" -or
            [string]$active.transaction_id -cnotmatch '^[0-9a-f]{32}$') {
            throw "active witness migration pointer is invalid"
        }
    }
    if ($RecoverActive) {
        if ($null -eq $active) { throw "no active witness migration exists" }
        $TransactionId = [string]$active.transaction_id
    }
    if (-not $TransactionId) { throw "transaction id is required" }
    if ($TransactionId -cnotmatch '^[0-9a-f]{32}$') { throw "transaction id is invalid" }
    $current = $null
    if (Test-Path -LiteralPath $currentInstallationPath -PathType Leaf) {
        $current = Get-Content -Raw -LiteralPath $currentInstallationPath | ConvertFrom-Json
        if ([int]$current.schema_version -ne 1 -or
            [string]$current.artifact_type -cne
                "ruisheng.witness-system-autostart-current-installation" -or
            [string]$current.transaction_id -cnotmatch '^[0-9a-f]{32}$') {
            throw "current witness installation pointer is invalid"
        }
    }
    $isActive = $null -ne $active -and [string]$active.transaction_id -ceq $TransactionId
    $isCurrent = $null -ne $current -and [string]$current.transaction_id -ceq $TransactionId
    if (-not $isActive -and -not $isCurrent) {
        throw "rollback transaction is neither active nor the current installation"
    }

    $migrationPrefix = [IO.Path]::GetFullPath($migration).TrimEnd('\') + '\'
    $transaction = [IO.Path]::GetFullPath((Join-Path $migration $TransactionId))
    if ($transaction.IndexOf($migrationPrefix, [StringComparison]::OrdinalIgnoreCase) -ne 0 -or
        -not (Test-Path -LiteralPath $transaction -PathType Container)) {
        throw "rollback transaction directory is unavailable"
    }
    $statePath = Join-Path $transaction "rollback-state.json"
    $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
    if ([int]$state.schema_version -ne 1 -or
        [string]$state.artifact_type -cne "ruisheng.witness-system-autostart-rollback-state" -or
        [string]$state.transaction_id -cne $TransactionId) {
        throw "rollback state identity is invalid"
    }

    $runtimeBackup = Join-Path $transaction "runtime.previous"
    $witnessBackup = Join-Path $transaction "freshness_witness.previous.py"
    $manifestBackup = Join-Path $transaction "runtime-manifest.previous.json"
    $taskBackup = Join-Path $transaction "task.previous.xml"
    $aclBackup = Join-Path $transaction "acl.previous.json"
    $runtimeAlreadyRestored = $false
    if ([bool]$state.previous_runtime_existed -and
        -not (Test-Path -LiteralPath $runtimeBackup -PathType Container)) {
        if (-not (Test-Path -LiteralPath $runtime -PathType Container) -or
            -not (Test-Path -LiteralPath $manifestBackup -PathType Leaf)) {
            throw "previous runtime backup is missing and restored runtime cannot be verified"
        }
        Assert-RuntimeMatchesManifest $runtime $manifestBackup
        $runtimeAlreadyRestored = $true
    }
    if ([bool]$state.previous_witness_existed -and
        -not (Test-Path -LiteralPath $witnessBackup -PathType Leaf)) {
        throw "previous witness backup is missing"
    }
    if ([bool]$state.previous_manifest_existed -and
        -not (Test-Path -LiteralPath $manifestBackup -PathType Leaf)) {
        throw "previous runtime manifest backup is missing"
    }
    if ([bool]$state.old_task_existed -and
        -not (Test-Path -LiteralPath $taskBackup -PathType Leaf)) {
        throw "previous task definition is missing"
    }
    if (-not (Test-Path -LiteralPath $aclBackup -PathType Leaf)) {
        throw "previous ACL snapshot is missing"
    }

    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        [void](Wait-Listener $false 30)
        Wait-TaskNotRunning 30
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    } elseif (@(Get-Listeners).Count) {
        throw "listener exists without the recoverable witness task"
    }

    if ([bool]$state.previous_runtime_existed) {
        if (-not $runtimeAlreadyRestored) {
            if (Test-Path -LiteralPath $runtime -PathType Container) {
                Remove-Item -LiteralPath $runtime -Recurse -Force
            }
            Move-Item -LiteralPath $runtimeBackup -Destination $runtime
        }
    } elseif (Test-Path -LiteralPath $runtime -PathType Container) {
        Remove-Item -LiteralPath $runtime -Recurse -Force
    }

    if (Test-Path -LiteralPath $witness -PathType Leaf) {
        Remove-Item -LiteralPath $witness -Force
    }
    if ([bool]$state.previous_witness_existed) {
        Copy-Item -LiteralPath $witnessBackup -Destination $witness
    }

    if (Test-Path -LiteralPath $manifest -PathType Leaf) {
        Remove-Item -LiteralPath $manifest -Force
    }
    if ([bool]$state.previous_manifest_existed) {
        Copy-Item -LiteralPath $manifestBackup -Destination $manifest
    }
    if (-not [bool]$state.service_stderr_existed -and
        (Test-Path -LiteralPath $serviceStderr -PathType Leaf)) {
        Remove-Item -LiteralPath $serviceStderr -Force
    }

    Restore-AclSnapshot (Join-Path $transaction "acl.previous.json")

    if ([bool]$state.old_task_existed) {
        Register-ScheduledTask -TaskName $taskName `
            -Xml (Get-Content -Raw -LiteralPath $taskBackup) -Force | Out-Null
        if ([bool]$state.old_task_was_running) {
            Start-ScheduledTask -TaskName $taskName
            $listeners = @(Wait-Listener $true 60)
            $restoredTask = Get-ScheduledTask -TaskName $taskName
            Assert-RestoredTaskListener ([int]$listeners[0].OwningProcess) $restoredTask
        }
    }

    $materialFailures = @()
    foreach ($property in @($state.material_hashes_before.PSObject.Properties)) {
        $path = [string]$property.Name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or
            (Get-Sha256 $path) -cne [string]$property.Value) {
            $materialFailures += $path
        }
    }
    if ($materialFailures.Count) {
        throw "protected witness material changed: $($materialFailures -join ', ')"
    }

    if ($null -ne $active -and [string]$active.transaction_id -ceq $TransactionId) {
        Remove-Item -LiteralPath $activeTransactionPath -Force
    }
    if ($null -ne $current -and [string]$current.transaction_id -ceq $TransactionId) {
        Remove-Item -LiteralPath $currentInstallationPath -Force
    }
    $state.phase = "rolled-back"
    $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-ProtectedJson $statePath $state
    $result = [ordered]@{
        schema_version = 1
        artifact_type = "ruisheng.witness-system-autostart-rollback"
        rolled_back = $true
        transaction_id = $TransactionId
        restored_task = [bool]$state.old_task_existed
        restored_task_running = [bool]$state.old_task_was_running
        material_hashes_verified = $true
        at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-ProtectedJson (Join-Path $transaction "rollback-result.json") $result
} catch {
    $result = [ordered]@{
        schema_version = 1
        artifact_type = "ruisheng.witness-system-autostart-rollback"
        rolled_back = $false
        transaction_id = $TransactionId
        error = $_.Exception.Message
        at = [DateTimeOffset]::UtcNow.ToString("o")
    }
} finally {
    if ($mutexAcquired) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}

$result | ConvertTo-Json -Depth 12 -Compress
if (-not $result.rolled_back) { exit 1 }
