[CmdletBinding()]
param(
    [string]$TaskName = "Ruisheng B08 Freshness Witness",
    [string]$Root = "C:\ProgramData\RuishengWitness",
    [string]$ListenAddress = "100.67.229.19",
    [int]$ListenPort = 38475,
    [string]$ExpectedSourcePythonSha256 = "5f7b89a612c9b8af1d6456cdfcd1dbe5ca630849e79aebced9bee9a6694952ec",
    [string]$ExpectedWitnessSha256 = "f441790914ce3d22e24d3ba78712bcac6cb2129f1b48beb27dcfaf53c56b15ca",
    [string]$ExpectedRuntimeManifestSha256 = "301172759e6269bcd1b04d7aed04c9b4df78f32150d34dd1a4c5d0cd7be329d0"
)

$ErrorActionPreference = "Stop"
$runtime = Join-Path $Root "runtime"
$python = Join-Path $runtime "python.exe"
$witness = Join-Path $Root "freshness_witness.py"
$manifestPath = Join-Path $Root "migration\runtime-manifest.json"
$stderrPath = Join-Path $Root "migration\witness-stderr.log"
$commandProcessor = "C:\Windows\System32\cmd.exe"
$expectedArguments = '-I -B -S "' + $witness + '" serve'
$expectedWrapperArguments = '/d /s /c ""' + $python + '" ' + $expectedArguments +
    ' 1>NUL 2>>"' + $stderrPath + '""'
$checks = [Collections.Generic.List[object]]::new()

function Add-Check([string]$Name, [bool]$Passed, [object]$Actual, [object]$Expected) {
    $checks.Add([pscustomobject]@{
        name = $Name
        passed = $Passed
        actual = $Actual
        expected = $Expected
    })
}

function Get-Sha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Test-ProtectedAcl([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $acl = Get-Acl -LiteralPath $Path
    if (-not $acl.AreAccessRulesProtected) { return $false }
    $allowed = @("S-1-5-18", "S-1-5-32-544")
    try {
        $ownerSid = ([Security.Principal.NTAccount]$acl.Owner).Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
    } catch {
        return $false
    }
    if ($ownerSid -cne "S-1-5-32-544") { return $false }
    $fullControlSids = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase
    )
    foreach ($rule in $acl.Access) {
        try {
            $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        } catch {
            return $false
        }
        if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
            $sid -notin $allowed -or
            ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::FullControl) -ne
                [Security.AccessControl.FileSystemRights]::FullControl) {
            return $false
        }
        [void]$fullControlSids.Add($sid)
    }
    return $fullControlSids.SetEquals($allowed)
}

try {
    $task = Get-ScheduledTask -TaskName $TaskName
    $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
    $principalOk = $task.Principal.UserId -in @("SYSTEM", "NT AUTHORITY\SYSTEM", "S-1-5-18") -and
        $task.Principal.LogonType.ToString() -ceq "ServiceAccount" -and
        $task.Principal.RunLevel.ToString() -ceq "Highest"
    Add-Check "task_principal" $principalOk (
        "$($task.Principal.UserId)|$($task.Principal.LogonType)|$($task.Principal.RunLevel)"
    ) "SYSTEM|ServiceAccount|Highest"

    $startupTriggers = @($task.Triggers | Where-Object {
        $_.CimClass.CimClassName -ceq "MSFT_TaskBootTrigger" -and $_.Enabled
    })
    Add-Check "task_at_startup" ($startupTriggers.Count -eq 1) $startupTriggers.Count 1

    $actions = @($task.Actions)
    $actionOk = $actions.Count -eq 1 -and
        [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($actions[0].Execute)) -ceq
            [IO.Path]::GetFullPath($commandProcessor) -and
        $actions[0].Arguments -ceq $expectedWrapperArguments -and
        [IO.Path]::GetFullPath($actions[0].WorkingDirectory) -ceq [IO.Path]::GetFullPath($Root)
    if ($actions.Count) {
        $actualAction = "$($actions[0].Execute)|$($actions[0].Arguments)"
    } else {
        $actualAction = $null
    }
    Add-Check "task_action" $actionOk $actualAction `
        "$commandProcessor|$expectedWrapperArguments"

    Add-Check "allow_start_on_battery" (-not $task.Settings.DisallowStartIfOnBatteries) `
        (-not $task.Settings.DisallowStartIfOnBatteries) $true
    Add-Check "continue_on_battery" (-not $task.Settings.StopIfGoingOnBatteries) `
        (-not $task.Settings.StopIfGoingOnBatteries) $true

    $pythonHash = Get-Sha256 $python
    $witnessHash = Get-Sha256 $witness
    Add-Check "runtime_python_sha256" ($pythonHash -ceq $ExpectedSourcePythonSha256) `
        $pythonHash $ExpectedSourcePythonSha256
    Add-Check "witness_sha256" ($witnessHash -ceq $ExpectedWitnessSha256) `
        $witnessHash $ExpectedWitnessSha256

    $manifest = $null
    $manifestHash = Get-Sha256 $manifestPath
    if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
        $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    }
    $manifestOk = $null -ne $manifest -and
        $manifestHash -ceq $ExpectedRuntimeManifestSha256 -and
        [int]$manifest.schema_version -eq 1 -and
        [string]$manifest.artifact_type -ceq "ruisheng.witness-approved-python-runtime" -and
        @($manifest.files).Count -gt 0
    Add-Check "runtime_manifest" $manifestOk "$manifestPath|$manifestHash" `
        "approved manifest $ExpectedRuntimeManifestSha256"

    $manifestFailures = @()
    $manifestPaths = @()
    if ($manifestOk) {
        $runtimePrefix = [IO.Path]::GetFullPath($runtime).TrimEnd('\') + '\'
        foreach ($entry in @($manifest.files)) {
            $relativePath = [string]$entry.relative_path
            $nativeRelativePath = $relativePath.Replace('/', '\')
            $targetPath = [IO.Path]::GetFullPath((Join-Path $runtime $nativeRelativePath))
            if ([IO.Path]::IsPathRooted($relativePath) -or
                $targetPath.IndexOf($runtimePrefix, [StringComparison]::OrdinalIgnoreCase) -ne 0) {
                $manifestFailures += "$relativePath`: path escaped runtime"
                continue
            }
            $manifestPaths += $relativePath
            $targetHash = Get-Sha256 $targetPath
            $targetSize = if (Test-Path -LiteralPath $targetPath -PathType Leaf) {
                (Get-Item -LiteralPath $targetPath).Length
            } else { -1 }
            if ($targetHash -cne [string]$entry.sha256 -or
                [int64]$targetSize -ne [int64]$entry.size) {
                $manifestFailures += "$relativePath`: runtime hash mismatch"
            }
        }
        $actualPaths = @(Get-ChildItem -LiteralPath $runtime -Recurse -File | ForEach-Object {
            ([IO.Path]::GetFullPath($_.FullName).Substring($runtimePrefix.Length)).Replace('\', '/')
        })
        $inventoryDifference = @(Compare-Object -ReferenceObject $manifestPaths `
            -DifferenceObject $actualPaths -CaseSensitive)
        if ($inventoryDifference.Count) {
            $manifestFailures += "runtime inventory differs from manifest"
        }
    } else {
        $manifestFailures += "runtime manifest identity invalid"
    }
    Add-Check "runtime_manifest_files" ($manifestFailures.Count -eq 0) `
        $manifestFailures "exact per-file SHA-256 inventory"

    $smokeOutput = & $python -I -B -S -c (
        "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey;" +
        "import cffi,hashlib,json,sqlite3,ssl;" +
        "k=Ed25519PrivateKey.generate();m=b'witness-runtime-smoke';s=k.sign(m);" +
        "k.public_key().verify(s,m);print('ok')"
    ) 2>&1
    $smokeOk = $LASTEXITCODE -eq 0 -and ($smokeOutput -join "`n").Trim() -ceq "ok"
    Add-Check "runtime_smoke" $smokeOk ($smokeOutput -join "`n") "ok"

    $aclPaths = @(
        $Root,
        $runtime,
        (Join-Path $Root "migration"),
        $stderrPath,
        $witness,
        $manifestPath,
        (Join-Path $Root "trust"),
        (Join-Path $Root "tls"),
        (Join-Path $Root "trust\freshness-witness-ed25519.pem"),
        (Join-Path $Root "trust\high-water.json"),
        (Join-Path $Root "trust\witness-config.json"),
        (Join-Path $Root "tls\server-key.pem")
    ) + @(Get-ChildItem -LiteralPath $runtime -Recurse -Force | Select-Object -ExpandProperty FullName) +
        @(Get-ChildItem -LiteralPath (Join-Path $Root "trust") -Recurse -Force |
            Select-Object -ExpandProperty FullName) +
        @(Get-ChildItem -LiteralPath (Join-Path $Root "tls") -Recurse -Force |
            Select-Object -ExpandProperty FullName)
    $aclPaths = @($aclPaths | Sort-Object -Unique)
    $aclFailures = @($aclPaths | Where-Object { -not (Test-ProtectedAcl $_) })
    Add-Check "protected_acl" ($aclFailures.Count -eq 0) $aclFailures $aclPaths

    $listeners = @(Get-NetTCPConnection -LocalAddress $ListenAddress -LocalPort $ListenPort `
        -State Listen -ErrorAction SilentlyContinue)
    $listenerOk = $listeners.Count -eq 1
    Add-Check "listener_unique" $listenerOk $listeners.Count 1

    $process = $null
    if ($listenerOk) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listeners[0].OwningProcess)"
    }
    $processPath = if ($null -ne $process) { [string]$process.ExecutablePath } else { $null }
    $commandLine = if ($null -ne $process) { [string]$process.CommandLine } else { $null }
    $processOk = $null -ne $process -and
        [IO.Path]::GetFullPath($processPath) -ceq [IO.Path]::GetFullPath($python) -and
        $commandLine.IndexOf($witness, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $commandLine -match '(?i)(?:^|\s)serve(?:\s|$)'
    Add-Check "listener_process_command" $processOk "$processPath|$commandLine" `
        "$python|$expectedArguments"

    $parentProcess = $null
    if ($null -ne $process -and [int]$process.ParentProcessId -gt 0) {
        $parentProcess = Get-CimInstance Win32_Process `
            -Filter "ProcessId=$([int]$process.ParentProcessId)"
    }
    $parentPath = if ($null -ne $parentProcess) {
        [string]$parentProcess.ExecutablePath
    } else { $null }
    $parentCommandLine = if ($null -ne $parentProcess) {
        [string]$parentProcess.CommandLine
    } else { $null }
    $parentOk = $null -ne $parentProcess -and
        [IO.Path]::GetFullPath($parentPath) -ceq [IO.Path]::GetFullPath($commandProcessor) -and
        $parentCommandLine.IndexOf($python, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $parentCommandLine.IndexOf($witness, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $parentCommandLine.IndexOf($stderrPath, [StringComparison]::OrdinalIgnoreCase) -ge 0
    Add-Check "listener_wrapper_parent" $parentOk "$parentPath|$parentCommandLine" `
        "$commandProcessor|$expectedWrapperArguments"

    $scheduleService = Get-CimInstance Win32_Service -Filter "Name='Schedule'"
    $schedulerParentOk = $null -ne $scheduleService -and $null -ne $parentProcess -and
        [int]$parentProcess.ParentProcessId -eq [int]$scheduleService.ProcessId -and
        [int]$scheduleService.ProcessId -gt 0 -and $scheduleService.State -ceq "Running"
    Add-Check "listener_task_scheduler_parent" $schedulerParentOk `
        "$($parentProcess.ParentProcessId)|$($scheduleService.ProcessId)|$($scheduleService.State)" `
        "cmd parent is the running Schedule service"

    $creationOk = $false
    if ($null -ne $process -and $null -ne $process.CreationDate -and
        $null -ne $parentProcess -and $null -ne $parentProcess.CreationDate) {
        $creation = if ($process.CreationDate -is [DateTime]) {
            [DateTime]$process.CreationDate
        } else {
            [Management.ManagementDateTimeConverter]::ToDateTime([string]$process.CreationDate)
        }
        $parentCreation = if ($parentProcess.CreationDate -is [DateTime]) {
            [DateTime]$parentProcess.CreationDate
        } else {
            [Management.ManagementDateTimeConverter]::ToDateTime(
                [string]$parentProcess.CreationDate
            )
        }
        $creationOk = $creation -ge $taskInfo.LastRunTime.AddSeconds(-2) -and
            $parentCreation -ge $taskInfo.LastRunTime.AddSeconds(-2) -and
            [Math]::Abs(($creation - $parentCreation).TotalSeconds) -le 2 -and
            $task.State.ToString() -ceq "Running"
    }
    if ($null -ne $process) {
        $actualCreationDate = $process.CreationDate
    } else {
        $actualCreationDate = $null
    }
    Add-Check "listener_task_run_binding" $creationOk $actualCreationDate $taskInfo.LastRunTime

    $failed = @($checks | Where-Object { -not $_.passed })
    [pscustomobject]@{
        schema_version = 1
        artifact_type = "ruisheng.witness-system-autostart-acceptance"
        all_passed = $failed.Count -eq 0
        task_name = $TaskName
        principal = if ($null -ne $task) { [string]$task.Principal.UserId } else { $null }
        trigger = "AtStartup"
        listener_address = $ListenAddress
        listener_port = $ListenPort
        listener_process_id = if ($listenerOk) { [int]$listeners[0].OwningProcess } else { $null }
        source_python_sha256 = $ExpectedSourcePythonSha256
        runtime_python_sha256 = $pythonHash
        witness_sha256 = $witnessHash
        checks = $checks
    } | ConvertTo-Json -Depth 8 -Compress
} catch {
    Add-Check "acceptance_exception" $false $_.Exception.Message "no exception"
    [pscustomobject]@{
        schema_version = 1
        artifact_type = "ruisheng.witness-system-autostart-acceptance"
        all_passed = $false
        task_name = $TaskName
        checks = $checks
    } | ConvertTo-Json -Depth 8 -Compress
}
