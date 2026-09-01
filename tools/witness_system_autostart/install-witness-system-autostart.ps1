[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$taskName = "Ruisheng B08 Freshness Witness"
$root = "C:\ProgramData\RuishengWitness"
$migration = Join-Path $root "migration"
$runtime = Join-Path $root "runtime"
$witnessSource = Join-Path $PSScriptRoot "freshness_witness.py"
$witnessDestination = Join-Path $root "freshness_witness.py"
$testScript = Join-Path $PSScriptRoot "test-witness-system-autostart.ps1"
$probeSource = Join-Path $PSScriptRoot "diagnose-witness-system-start.py"
$rollbackScript = Join-Path $PSScriptRoot "rollback-witness-system-autostart.ps1"
$approvedRuntimeManifest = Join-Path $PSScriptRoot "runtime-source-manifest.json"
$sourcePythonRoot = "C:\Users\admin\AppData\Local\Programs\Python\Python311"
$sourcePython = Join-Path $sourcePythonRoot "python.exe"
$expectedPythonSha256 = "5f7b89a612c9b8af1d6456cdfcd1dbe5ca630849e79aebced9bee9a6694952ec"
$expectedWitnessSha256 = "f441790914ce3d22e24d3ba78712bcac6cb2129f1b48beb27dcfaf53c56b15ca"
$listenAddress = "100.67.229.19"
$listenPort = 38475
$transactionId = [guid]::NewGuid().ToString("N")
$transaction = Join-Path $migration $transactionId
$runtimeStage = Join-Path $transaction "runtime.new"
$runtimeBackup = Join-Path $transaction "runtime.previous"
$witnessBackup = Join-Path $transaction "freshness_witness.previous.py"
$taskBackup = Join-Path $transaction "task.previous.xml"
$aclBackup = Join-Path $transaction "acl.previous.json"
$rollbackStatePath = Join-Path $transaction "rollback-state.json"
$startupStdout = Join-Path $transaction "system-start.stdout.log"
$startupStderr = Join-Path $transaction "system-start.stderr.log"
$serviceStderr = Join-Path $migration "witness-stderr.log"
$resultPath = Join-Path $transaction "installation-result.json"
$probeDestination = Join-Path $transaction "diagnose-witness-system-start.py"
$manifestPath = Join-Path $migration "runtime-manifest.json"
$manifestBackup = Join-Path $transaction "runtime-manifest.previous.json"
$diagnosticTaskName = "$taskName Migration Preflight $transactionId"
$activeTransactionPath = Join-Path $migration "active-transaction.json"
$currentInstallationPath = Join-Path $migration "current-installation.json"
$mutexName = "Global\RuishengWitness-SystemAutostart-Migration"
$oldTaskExisted = $false
$oldTaskWasRunning = $false
$previousRuntimeExisted = $false
$previousWitnessExisted = $false
$previousManifestExisted = $false
$runtimeReplaced = $false
$witnessReplaced = $false
$taskReplaced = $false
$diagnosticTaskRegistered = $false
$serviceStderrExisted = Test-Path -LiteralPath $serviceStderr -PathType Leaf
$aclSnapshot = @()
$expectedTestScriptSha256 = "ca53b2127b31be87fea0d74ee405d7dc36354a33893b00a55bcf923a053dc0e2"
$expectedProbeSha256 = "13f38fa3f9d60da94edff7ebfd8e480a0d9357a8967f4b1ad9a8913784c2a9da"
$expectedRollbackScriptSha256 = "b964a4e052fecaf551e3ff4e8c7f3f2f8c8d13c46c6830329fdd2c86abc6f86a"
$expectedApprovedRuntimeManifestSha256 = "301172759e6269bcd1b04d7aed04c9b4df78f32150d34dd1a4c5d0cd7be329d0"

function Get-Sha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "administrator token required"
    }
}

function Get-BytesSha256([byte[]]$Bytes) {
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha256.ComputeHash($Bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }
}

function New-ProtectedDirectorySecurity {
    $security = [Security.AccessControl.DirectorySecurity]::new()
    $security.SetAccessRuleProtection($true, $false)
    $administrators = [Security.Principal.SecurityIdentifier]::new("S-1-5-32-544")
    $system = [Security.Principal.SecurityIdentifier]::new("S-1-5-18")
    $security.SetOwner($administrators)
    $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    foreach ($sid in @($system, $administrators)) {
        [void]$security.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            $sid,
            [Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance,
            [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow
        ))
    }
    return $security
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

function Save-AclSnapshot([string[]]$Paths) {
    $result = @()
    foreach ($path in $Paths) {
        if (-not (Test-Path -LiteralPath $path)) { continue }
        $item = Get-Item -LiteralPath $path -Force
        $result += [pscustomobject]@{
            path = $item.FullName
            directory = $item.PSIsContainer
            sddl = (Get-Acl -LiteralPath $item.FullName).Sddl
        }
    }
    return $result
}

function Restore-AclSnapshot([object[]]$Snapshot) {
    foreach ($entry in @($Snapshot)) {
        if (-not (Test-Path -LiteralPath ([string]$entry.path))) { continue }
        if ([bool]$entry.directory) {
            $security = [Security.AccessControl.DirectorySecurity]::new()
        } else {
            $security = [Security.AccessControl.FileSecurity]::new()
        }
        $security.SetSecurityDescriptorSddlForm([string]$entry.sddl)
        Set-Acl -LiteralPath ([string]$entry.path) -AclObject $security
    }
}

function Protect-Tree([string[]]$Paths) {
    foreach ($path in $Paths) {
        if (-not (Test-Path -LiteralPath $path)) { throw "required protected path missing: $path" }
        $item = Get-Item -LiteralPath $path -Force
        if ($item.PSIsContainer) {
            $protectedAcl = New-ProtectedDirectorySecurity
        } else {
            $protectedAcl = New-ProtectedFileSecurity
        }
        Set-Acl -LiteralPath $item.FullName -AclObject $protectedAcl
    }
}

function Read-ApprovedRuntimeManifest([string]$Path) {
    $bytes = [IO.File]::ReadAllBytes($Path)
    if ((Get-BytesSha256 $bytes) -cne $expectedApprovedRuntimeManifestSha256) {
        throw "approved runtime manifest hash mismatch"
    }
    $utf8 = [Text.UTF8Encoding]::new($false, $true)
    $manifest = $utf8.GetString($bytes) | ConvertFrom-Json
    $properties = @($manifest.PSObject.Properties.Name | Sort-Object)
    $expectedProperties = @(
        "artifact_type", "files", "python_version", "schema_version", "source_root_identity"
    ) | Sort-Object
    if (@(Compare-Object $expectedProperties $properties -SyncWindow 0).Count -or
        [int]$manifest.schema_version -ne 1 -or
        [string]$manifest.artifact_type -cne "ruisheng.witness-approved-python-runtime" -or
        [string]$manifest.python_version -cne "3.11.9" -or
        [string]$manifest.source_root_identity -cne "CPython-3.11.9-amd64-approved-2026-09-01") {
        throw "approved runtime manifest identity is invalid"
    }
    $entries = @($manifest.files)
    if ($entries.Count -lt 1 -or $entries.Count -gt 32768) {
        throw "approved runtime manifest file count is invalid"
    }
    return [pscustomobject]@{ value = $manifest; bytes = $bytes }
}

function Install-ApprovedRuntime([object]$Manifest, [string]$StageRoot, [string]$SourceRoot) {
    $stagePrefix = [IO.Path]::GetFullPath($StageRoot).TrimEnd('\') + '\'
    $sourcePrefix = [IO.Path]::GetFullPath($SourceRoot).TrimEnd('\') + '\'
    New-Item -ItemType Directory -Path $StageRoot -Force | Out-Null
    $seen = @{}
    foreach ($entry in @($Manifest.files)) {
        $entryProperties = @($entry.PSObject.Properties.Name | Sort-Object)
        $expectedEntryProperties = @("generated", "relative_path", "sha256", "size") | Sort-Object
        if (@(Compare-Object $expectedEntryProperties $entryProperties -SyncWindow 0).Count) {
            throw "approved runtime manifest entry schema is invalid"
        }
        $relativePath = [string]$entry.relative_path
        $expectedHash = [string]$entry.sha256
        $expectedSize = [int64]$entry.size
        $generated = [bool]$entry.generated
        if (-not $relativePath -or $relativePath.Contains("\") -or
            [IO.Path]::IsPathRooted($relativePath) -or $relativePath.Contains(":") -or
            @($relativePath.Split('/') | Where-Object { $_ -in @("", ".", "..") }).Count -or
            $expectedHash -cnotmatch '^[0-9a-f]{64}$' -or $expectedSize -lt 0) {
            throw "approved runtime manifest entry is invalid: $relativePath"
        }
        $caseKey = $relativePath.ToLowerInvariant()
        if ($seen.ContainsKey($caseKey)) { throw "duplicate runtime manifest path: $relativePath" }
        $seen[$caseKey] = $true
        $nativePath = $relativePath.Replace('/', '\')
        $targetPath = [IO.Path]::GetFullPath((Join-Path $StageRoot $nativePath))
        if ($targetPath.IndexOf($stagePrefix, [StringComparison]::OrdinalIgnoreCase) -ne 0) {
            throw "runtime target escaped staging root: $relativePath"
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $targetPath) -Force | Out-Null
        if ($generated) {
            if ($relativePath -cne "python311._pth") {
                throw "unapproved generated runtime file: $relativePath"
            }
            [IO.File]::WriteAllText(
                $targetPath,
                ".`r`nLib`r`nDLLs`r`nLib\site-packages`r`n",
                [Text.Encoding]::ASCII
            )
        } else {
            $sourcePath = [IO.Path]::GetFullPath((Join-Path $SourceRoot $nativePath))
            if ($sourcePath.IndexOf($sourcePrefix, [StringComparison]::OrdinalIgnoreCase) -ne 0 -or
                -not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
                throw "approved runtime source is unavailable: $relativePath"
            }
            $sourceItem = Get-Item -LiteralPath $sourcePath -Force
            if (($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "approved runtime source is a reparse point: $relativePath"
            }
            Copy-Item -LiteralPath $sourcePath -Destination $targetPath
        }
        $targetItem = Get-Item -LiteralPath $targetPath -Force
        if ([int64]$targetItem.Length -ne $expectedSize -or
            (Get-Sha256 $targetPath) -cne $expectedHash) {
            throw "approved runtime copy hash mismatch: $relativePath"
        }
    }
    $actualPaths = @(Get-ChildItem -LiteralPath $StageRoot -Recurse -File | ForEach-Object {
        ([IO.Path]::GetFullPath($_.FullName).Substring($stagePrefix.Length)).Replace('\', '/')
    })
    $approvedPaths = @($Manifest.files | ForEach-Object { [string]$_.relative_path })
    if (@(Compare-Object $approvedPaths $actualPaths -CaseSensitive).Count) {
        throw "installed runtime inventory differs from the approved manifest"
    }
}

function Invoke-RuntimeSmoke([string]$PythonPath, [string]$StdoutPath, [string]$StderrPath) {
    $code = "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey;" +
        "import cffi,hashlib,json,sqlite3,ssl;" +
        "k=Ed25519PrivateKey.generate();m=b'witness-runtime-smoke';s=k.sign(m);" +
        "k.public_key().verify(s,m);print('ok')"
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $PythonPath
    $startInfo.Arguments = '-I -B -S -c "' + $code.Replace('"', '\"') + '"'
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) { throw "minimal runtime smoke process did not start" }
    try {
        if (-not $process.WaitForExit(20000)) {
            $process.Kill()
            [void]$process.WaitForExit(5000)
            throw "minimal runtime smoke test timed out"
        }
        $stdout = $process.StandardOutput.ReadToEnd()
        $stderr = $process.StandardError.ReadToEnd()
        [IO.File]::WriteAllText($StdoutPath, $stdout, [Text.UTF8Encoding]::new($false))
        [IO.File]::WriteAllText($StderrPath, $stderr, [Text.UTF8Encoding]::new($false))
        if ($process.ExitCode -ne 0 -or $stdout.Trim() -cne "ok") {
            throw "minimal runtime smoke test failed: $($stderr.Trim())"
        }
    } finally {
        $process.Dispose()
    }
}

function Get-Listener {
    return @(Get-NetTCPConnection -LocalAddress $listenAddress -LocalPort $listenPort `
        -State Listen -ErrorAction SilentlyContinue)
}

function Assert-ListenerProcess([int]$ProcessId, [string]$ActionPath, [string]$ScriptPath) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId"
    if ($null -eq $process) { throw "listener process is unavailable" }
    $commandLine = [string]$process.CommandLine
    if ($commandLine.IndexOf($ScriptPath, [StringComparison]::OrdinalIgnoreCase) -lt 0 -or
        $commandLine -notmatch '(?i)(?:^|\s)serve(?:\s|$)') {
        throw "listener command line does not match witness action"
    }
    $processPath = [IO.Path]::GetFullPath([string]$process.ExecutablePath)
    $expectedActionPath = [IO.Path]::GetFullPath($ActionPath)
    if ($processPath -ceq $expectedActionPath) { return }

    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$process.ParentProcessId)"
    if ($null -eq $parent -or
        [IO.Path]::GetFullPath([string]$parent.ExecutablePath) -cne $expectedActionPath -or
        ([string]$parent.CommandLine).IndexOf(
            $ScriptPath, [StringComparison]::OrdinalIgnoreCase
        ) -lt 0) {
        throw "listener process tree does not match task action"
    }
    $task = Get-ScheduledTask -TaskName $taskName
    $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
    $parentCreated = if ($parent.CreationDate -is [DateTime]) {
        [DateTime]$parent.CreationDate
    } else {
        [Management.ManagementDateTimeConverter]::ToDateTime([string]$parent.CreationDate)
    }
    if ($task.State.ToString() -cne "Running" -or
        $parentCreated -lt $taskInfo.LastRunTime.AddSeconds(-2)) {
        throw "listener process tree is not bound to the restored task run"
    }
}

function Wait-Listener([bool]$Present, [int]$Seconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        $listeners = @(Get-Listener)
        if (($Present -and $listeners.Count -eq 1) -or (-not $Present -and $listeners.Count -eq 0)) {
            return $listeners
        }
        Start-Sleep -Milliseconds 250
    } until ([DateTime]::UtcNow -ge $deadline)
    throw $(if ($Present) { "witness listener did not appear" } else { "witness listener did not stop" })
}

function Write-JsonFile([string]$Path, [object]$Value) {
    [IO.File]::WriteAllText(
        $Path,
        (($Value | ConvertTo-Json -Depth 12) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
}

function Write-ProtectedJsonFile([string]$Path, [object]$Value) {
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    try {
        Write-JsonFile $temporary $Value
        Set-Acl -LiteralPath $temporary -AclObject (New-ProtectedFileSecurity)
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Save-RollbackState([string]$Phase) {
    $state = [ordered]@{
        schema_version = 1
        artifact_type = "ruisheng.witness-system-autostart-rollback-state"
        transaction_id = $transactionId
        phase = $Phase
        task_name = $taskName
        old_task_existed = $oldTaskExisted
        old_task_was_running = $oldTaskWasRunning
        previous_runtime_existed = $previousRuntimeExisted
        previous_witness_existed = $previousWitnessExisted
        previous_manifest_existed = $previousManifestExisted
        service_stderr_existed = $serviceStderrExisted
        material_hashes_before = $materialHashesBefore
        updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-ProtectedJsonFile $rollbackStatePath $state
}

function Get-TaskFailureDetail([string]$Name) {
    $info = Get-ScheduledTaskInfo -TaskName $Name -ErrorAction SilentlyContinue
    $stdout = ""
    if (Test-Path -LiteralPath $startupStdout -PathType Leaf) {
        $stdoutValue = Get-Content -LiteralPath $startupStdout -Raw -ErrorAction SilentlyContinue
        if ($null -ne $stdoutValue) { $stdout = [string]$stdoutValue }
    }
    $stderrPath = if ($Name -ceq $taskName) { $serviceStderr } else { $startupStderr }
    $stderr = ""
    if (Test-Path -LiteralPath $stderrPath -PathType Leaf) {
        $stderrValue = Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue
        if ($null -ne $stderrValue) { $stderr = [string]$stderrValue }
    }
    $processes = @(Get-CimInstance Win32_Process | Where-Object {
        [string]$_.ExecutablePath -and
        [IO.Path]::GetFullPath([string]$_.ExecutablePath) -ceq [IO.Path]::GetFullPath($python)
    } | ForEach-Object { "$($_.ProcessId):$($_.CommandLine)" })
    return "last_result=$($info.LastTaskResult); processes=$($processes -join ' | '); " +
        "stdout=$($stdout.Trim()); stderr=$($stderr.Trim())"
}

function Wait-TaskCompletion([string]$Name, [DateTime]$StartedAt, [int]$Seconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
        $info = Get-ScheduledTaskInfo -TaskName $Name -ErrorAction SilentlyContinue
        if ($null -ne $task -and $null -ne $info -and
            $info.LastRunTime -ge $StartedAt.AddSeconds(-2) -and
            $task.State.ToString() -ne "Running") {
            return $info
        }
        Start-Sleep -Milliseconds 250
    } until ([DateTime]::UtcNow -ge $deadline)
    throw "scheduled task did not complete"
}

function Wait-TaskNotRunning([string]$Name, [int]$Seconds) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
        if ($null -eq $task -or $task.State.ToString() -ne "Running") { return }
        Start-Sleep -Milliseconds 250
    } until ([DateTime]::UtcNow -ge $deadline)
    throw "scheduled task instance did not stop: $Name"
}

Assert-Administrator
$mutex = [Threading.Mutex]::new($false, $mutexName)
$mutexAcquired = $false
try {
    try {
        $mutexAcquired = $mutex.WaitOne(0)
    } catch [Threading.AbandonedMutexException] {
        $mutexAcquired = $true
    }
    if (-not $mutexAcquired) { throw "another witness migration or rollback is already running" }

foreach ($path in @(
    $sourcePython, $witnessSource, $testScript, $probeSource, $rollbackScript,
    $approvedRuntimeManifest
)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "required input missing: $path" }
}
if ((Get-Sha256 $sourcePython) -cne $expectedPythonSha256) { throw "source Python hash mismatch" }
if ((Get-Sha256 $witnessSource) -cne $expectedWitnessSha256) { throw "witness source hash mismatch" }
if ((Get-Sha256 $testScript) -cne $expectedTestScriptSha256) { throw "acceptance script hash mismatch" }
if ((Get-Sha256 $probeSource) -cne $expectedProbeSha256) { throw "SYSTEM probe script hash mismatch" }
if ((Get-Sha256 $rollbackScript) -cne $expectedRollbackScriptSha256) {
    throw "rollback script hash mismatch"
}
$approvedManifestData = Read-ApprovedRuntimeManifest $approvedRuntimeManifest

New-Item -ItemType Directory -Path $migration -Force | Out-Null
Set-Acl -LiteralPath $migration -AclObject (New-ProtectedDirectorySecurity)
if (Test-Path -LiteralPath $activeTransactionPath -PathType Leaf) {
    $recoveryJson = & $rollbackScript -RecoverActive
    $recovery = $recoveryJson | ConvertFrom-Json
    if (-not $recovery.rolled_back) {
        throw "incomplete previous migration could not be recovered: $recoveryJson"
    }
}

$requiredExisting = @(
    (Join-Path $root "trust\freshness-witness-ed25519.pem"),
    (Join-Path $root "trust\high-water.json"),
    (Join-Path $root "trust\witness-config.json"),
    (Join-Path $root "tls\server-cert.pem"),
    (Join-Path $root "tls\server-key.pem"),
    (Join-Path $root "tls\client-cert.pem")
)
foreach ($path in $requiredExisting) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "existing witness material missing: $path" }
}
$materialHashesBefore = [ordered]@{}
foreach ($path in $requiredExisting) { $materialHashesBefore[$path] = Get-Sha256 $path }

New-Item -ItemType Directory -Path $transaction -Force | Out-Null
Set-Acl -LiteralPath $transaction -AclObject (New-ProtectedDirectorySecurity)
Copy-Item -LiteralPath $probeSource -Destination $probeDestination
if ((Get-Sha256 $probeDestination) -cne $expectedProbeSha256) {
    throw "copied SYSTEM probe script hash mismatch"
}
try {
    $oldTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $oldTask) {
        $oldTaskExisted = $true
        $oldTaskWasRunning = $oldTask.State.ToString() -ceq "Running"
        Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath $taskBackup -Encoding Unicode
    }
    $previousRuntimeExisted = Test-Path -LiteralPath $runtime -PathType Container
    $previousWitnessExisted = Test-Path -LiteralPath $witnessDestination -PathType Leaf
    $previousManifestExisted = Test-Path -LiteralPath $manifestPath -PathType Leaf
    Save-RollbackState "prepared"
    Write-ProtectedJsonFile $activeTransactionPath ([ordered]@{
        schema_version = 1
        artifact_type = "ruisheng.witness-system-autostart-active-transaction"
        transaction_id = $transactionId
        rollback_state = $rollbackStatePath
    })

    $existingListeners = @(Get-Listener)
    if ($existingListeners.Count -gt 1) { throw "multiple processes listen on the witness endpoint" }
    if ($existingListeners.Count -eq 1) {
        $existingProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($existingListeners[0].OwningProcess)"
        if ($null -eq $existingProcess -or
            ([string]$existingProcess.CommandLine).IndexOf(
                $witnessDestination, [StringComparison]::OrdinalIgnoreCase
            ) -lt 0) {
            throw "witness endpoint is occupied by an unrelated process"
        }
    }

    if ($oldTaskExisted -and $oldTaskWasRunning) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        [void](Wait-Listener $false 20)
        Wait-TaskNotRunning $taskName 20
    } elseif ($existingListeners.Count) {
        throw "listener exists but the recoverable old task is not running"
    }
    if ($oldTaskExisted) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    Save-RollbackState "old-task-removed"

    Install-ApprovedRuntime $approvedManifestData.value $runtimeStage $sourcePythonRoot
    $stagePython = Join-Path $runtimeStage "python.exe"
    Invoke-RuntimeSmoke $stagePython `
        (Join-Path $transaction "runtime-smoke.stdout.log") `
        (Join-Path $transaction "runtime-smoke.stderr.log")

    if (Test-Path -LiteralPath $runtime) {
        Move-Item -LiteralPath $runtime -Destination $runtimeBackup
    }
    Move-Item -LiteralPath $runtimeStage -Destination $runtime
    $runtimeReplaced = $true
    if (Test-Path -LiteralPath $witnessDestination -PathType Leaf) {
        Copy-Item -LiteralPath $witnessDestination -Destination $witnessBackup
    }
    Copy-Item -LiteralPath $witnessSource -Destination $witnessDestination -Force
    $witnessReplaced = $true
    Save-RollbackState "runtime-replaced"

    if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
        Copy-Item -LiteralPath $manifestPath -Destination $manifestBackup
    }
    [IO.File]::WriteAllBytes($manifestPath, [byte[]]$approvedManifestData.bytes)
    if ((Get-Sha256 $manifestPath) -cne $expectedApprovedRuntimeManifestSha256) {
        throw "installed runtime manifest hash mismatch"
    }
    if (-not (Test-Path -LiteralPath $serviceStderr -PathType Leaf)) {
        [IO.File]::WriteAllText($serviceStderr, "", [Text.UTF8Encoding]::new($false))
    }

    $aclTargets = @(
        $root, $migration, $transaction, $runtime, $witnessDestination, $manifestPath,
        $probeDestination, $serviceStderr
    ) +
        @(Get-ChildItem -LiteralPath $runtime -Recurse -Force | Select-Object -ExpandProperty FullName) +
        @(Get-ChildItem -LiteralPath (Join-Path $root "trust") -Recurse -Force | Select-Object -ExpandProperty FullName) +
        @(Get-ChildItem -LiteralPath (Join-Path $root "tls") -Recurse -Force | Select-Object -ExpandProperty FullName) +
        @((Join-Path $root "trust"), (Join-Path $root "tls"))
    $aclSnapshot = Save-AclSnapshot $aclTargets
    Write-JsonFile $aclBackup $aclSnapshot
    Set-Acl -LiteralPath $aclBackup -AclObject (New-ProtectedFileSecurity)
    Protect-Tree $aclTargets
    Save-RollbackState "acl-protected"

    $python = Join-Path $runtime "python.exe"
    $arguments = '-I -B -S "' + $witnessDestination + '" serve'
    $commandProcessor = "C:\Windows\System32\cmd.exe"
    if (-not (Test-Path -LiteralPath $commandProcessor -PathType Leaf)) {
        throw "fixed Windows command processor missing"
    }
    $serviceArguments = '/d /s /c ""' + $python + '" ' + $arguments +
        ' 1>NUL 2>>"' + $serviceStderr + '""'
    $action = New-ScheduledTaskAction -Execute $commandProcessor `
        -Argument $serviceArguments -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 999 `
        -RestartInterval ([TimeSpan]::FromMinutes(1)) `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew

    $probeArguments = '-I -B -S "' + $probeDestination + '" "' + $witnessDestination + '"'
    $diagnosticArguments = '/d /s /c ""' + $python + '" ' + $probeArguments +
        ' 1>"' + $startupStdout + '" 2>"' + $startupStderr + '""'
    $diagnosticAction = New-ScheduledTaskAction -Execute $commandProcessor `
        -Argument $diagnosticArguments -WorkingDirectory $root
    $diagnosticTask = New-ScheduledTask -Action $diagnosticAction -Principal $principal -Settings $settings
    Register-ScheduledTask -TaskName $diagnosticTaskName -InputObject $diagnosticTask -Force | Out-Null
    $diagnosticTaskRegistered = $true
    $diagnosticStartedAt = [DateTime]::Now
    Start-ScheduledTask -TaskName $diagnosticTaskName
    try {
        $diagnosticInfo = Wait-TaskCompletion $diagnosticTaskName $diagnosticStartedAt 20
    } catch {
        throw "SYSTEM witness preflight failed: $(Get-TaskFailureDetail $diagnosticTaskName)"
    }
    if ($diagnosticInfo.LastTaskResult -ne 0) {
        throw "SYSTEM witness preflight failed: $(Get-TaskFailureDetail $diagnosticTaskName)"
    }
    $expectedProbeOutput = @(
        "python_started", "witness_module_loaded", "config_loaded", "witness_key_loaded",
        "server_certificate_loaded", "client_certificate_loaded", "listener_bound",
        "socket_accepting", "probe_complete"
    )
    $actualProbeOutput = @([IO.File]::ReadAllLines($startupStdout))
    if (@(Compare-Object -ReferenceObject $expectedProbeOutput -DifferenceObject $actualProbeOutput `
        -SyncWindow 0).Count) {
        throw "SYSTEM witness preflight output mismatch: $($actualProbeOutput -join ',')"
    }
    Unregister-ScheduledTask -TaskName $diagnosticTaskName -Confirm:$false
    $diagnosticTaskRegistered = $false

    [IO.File]::WriteAllText($startupStdout, "", [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText($startupStderr, "", [Text.UTF8Encoding]::new($false))
    $traceArguments = '-I -B -S "' + $probeDestination + '" --trace-serve "' +
        $witnessDestination + '"'
    $traceCommandArguments = '/d /s /c ""' + $python + '" ' + $traceArguments +
        ' 1>"' + $startupStdout + '" 2>"' + $startupStderr + '""'
    $traceAction = New-ScheduledTaskAction -Execute $commandProcessor `
        -Argument $traceCommandArguments -WorkingDirectory $root
    $traceTask = New-ScheduledTask -Action $traceAction -Principal $principal -Settings $settings
    Register-ScheduledTask -TaskName $diagnosticTaskName -InputObject $traceTask -Force | Out-Null
    $diagnosticTaskRegistered = $true
    Start-ScheduledTask -TaskName $diagnosticTaskName
    try {
        $traceListeners = Wait-Listener $true 30
    } catch {
        throw "SYSTEM traced service preflight failed: $(Get-TaskFailureDetail $diagnosticTaskName)"
    }
    $traceProcess = Get-CimInstance Win32_Process `
        -Filter "ProcessId=$([int]$traceListeners[0].OwningProcess)"
    $traceProcessOk = $null -ne $traceProcess -and
        [IO.Path]::GetFullPath([string]$traceProcess.ExecutablePath) -ceq `
            [IO.Path]::GetFullPath($python) -and
        ([string]$traceProcess.CommandLine).IndexOf(
            $probeDestination, [StringComparison]::OrdinalIgnoreCase
        ) -ge 0 -and
        ([string]$traceProcess.CommandLine).IndexOf(
            $witnessDestination, [StringComparison]::OrdinalIgnoreCase
        ) -ge 0
    if (-not $traceProcessOk) { throw "SYSTEM traced service process identity mismatch" }
    Stop-ScheduledTask -TaskName $diagnosticTaskName -ErrorAction SilentlyContinue
    [void](Wait-Listener $false 20)
    Wait-TaskNotRunning $diagnosticTaskName 20
    Unregister-ScheduledTask -TaskName $diagnosticTaskName -Confirm:$false
    $diagnosticTaskRegistered = $false

    $task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings
    Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
    $taskReplaced = $true
    Save-RollbackState "system-task-registered"
    Start-ScheduledTask -TaskName $taskName
    try {
        $listeners = Wait-Listener $true 30
    } catch {
        throw "final SYSTEM witness task failed: $(Get-TaskFailureDetail $taskName)"
    }
    Assert-ListenerProcess ([int]$listeners[0].OwningProcess) $python $witnessDestination

    foreach ($path in $requiredExisting) {
        if ((Get-Sha256 $path) -cne [string]$materialHashesBefore[$path]) {
            throw "protected witness material changed during migration: $path"
        }
    }
    $acceptanceJson = & $testScript -TaskName $taskName -Root $root `
        -ListenAddress $listenAddress -ListenPort $listenPort `
        -ExpectedSourcePythonSha256 $expectedPythonSha256 `
        -ExpectedWitnessSha256 $expectedWitnessSha256
    $acceptance = $acceptanceJson | ConvertFrom-Json
    if (-not $acceptance.all_passed) { throw "SYSTEM witness acceptance failed: $acceptanceJson" }

    $result = [pscustomobject]@{
        schema_version = 1
        artifact_type = "ruisheng.witness-system-autostart-installation"
        installed = $true
        rolled_back = $false
        transaction_id = $transactionId
        backup_directory = $transaction
        old_task_definition_retained = $oldTaskExisted
        old_task_was_running = $oldTaskWasRunning
        previous_runtime_existed = $previousRuntimeExisted
        previous_witness_existed = $previousWitnessExisted
        previous_manifest_existed = $previousManifestExisted
        material_hashes_before = $materialHashesBefore
        runtime_manifest_sha256 = $expectedApprovedRuntimeManifestSha256
        acceptance = $acceptance
        stderr_log = $serviceStderr
    }
    Write-JsonFile $resultPath $result
    Set-Acl -LiteralPath $resultPath -AclObject (New-ProtectedFileSecurity)
    Save-RollbackState "committed"
    Write-ProtectedJsonFile $currentInstallationPath ([ordered]@{
        schema_version = 1
        artifact_type = "ruisheng.witness-system-autostart-current-installation"
        transaction_id = $transactionId
        rollback_state = $rollbackStatePath
        committed_at = [DateTimeOffset]::UtcNow.ToString("o")
    })
    Remove-Item -LiteralPath $activeTransactionPath -Force
    $result | ConvertTo-Json -Depth 12 -Compress
} catch {
    $failure = $_.Exception.Message
    $rollbackErrors = [Collections.Generic.List[string]]::new()
    try {
        if ($diagnosticTaskRegistered -or
            (Get-ScheduledTask -TaskName $diagnosticTaskName -ErrorAction SilentlyContinue)) {
            Stop-ScheduledTask -TaskName $diagnosticTaskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $diagnosticTaskName -Confirm:$false `
                -ErrorAction SilentlyContinue
        }
    } catch { $rollbackErrors.Add($_.Exception.Message) }
    try {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Wait-TaskNotRunning $taskName 20
    } catch { $rollbackErrors.Add($_.Exception.Message) }
    try {
        if ($taskReplaced -or (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
        }
    } catch { $rollbackErrors.Add($_.Exception.Message) }
    try {
        if ($aclSnapshot.Count) { Restore-AclSnapshot $aclSnapshot }
    } catch { $rollbackErrors.Add($_.Exception.Message) }
    try {
        if ($runtimeReplaced -and (Test-Path -LiteralPath $runtime)) {
            Remove-Item -LiteralPath $runtime -Recurse -Force
        }
        if (Test-Path -LiteralPath $runtimeBackup) {
            Move-Item -LiteralPath $runtimeBackup -Destination $runtime
        }
    } catch { $rollbackErrors.Add($_.Exception.Message) }
    try {
        if ($witnessReplaced -and (Test-Path -LiteralPath $witnessDestination)) {
            Remove-Item -LiteralPath $witnessDestination -Force
        }
        if (Test-Path -LiteralPath $witnessBackup) {
            Copy-Item -LiteralPath $witnessBackup -Destination $witnessDestination -Force
        }
        if (Test-Path -LiteralPath $manifestBackup) {
            Copy-Item -LiteralPath $manifestBackup -Destination $manifestPath -Force
        } elseif (Test-Path -LiteralPath $manifestPath) {
            Remove-Item -LiteralPath $manifestPath -Force
        }
    } catch { $rollbackErrors.Add($_.Exception.Message) }
    try {
        if (-not $serviceStderrExisted -and (Test-Path -LiteralPath $serviceStderr -PathType Leaf)) {
            Remove-Item -LiteralPath $serviceStderr -Force
        }
    } catch { $rollbackErrors.Add($_.Exception.Message) }
    try {
        if ($oldTaskExisted -and (Test-Path -LiteralPath $taskBackup)) {
            Register-ScheduledTask -TaskName $taskName -Xml (Get-Content -Raw -LiteralPath $taskBackup) `
                -Force | Out-Null
            if ($oldTaskWasRunning) {
                Start-ScheduledTask -TaskName $taskName
                $oldListeners = Wait-Listener $true 60
                Assert-ListenerProcess ([int]$oldListeners[0].OwningProcess) `
                    ([string](Get-ScheduledTask -TaskName $taskName).Actions[0].Execute) `
                    $witnessDestination
            }
        }
    } catch { $rollbackErrors.Add($_.Exception.Message) }
    try {
        foreach ($path in $requiredExisting) {
            if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or
                (Get-Sha256 $path) -cne [string]$materialHashesBefore[$path]) {
                throw "protected witness material changed during rollback: $path"
            }
        }
    } catch { $rollbackErrors.Add($_.Exception.Message) }
    $result = [pscustomobject]@{
        schema_version = 1
        artifact_type = "ruisheng.witness-system-autostart-installation"
        installed = $false
        rolled_back = $rollbackErrors.Count -eq 0
        transaction_id = $transactionId
        error = $failure
        rollback_errors = $rollbackErrors
        old_task_definition_retained = Test-Path -LiteralPath $taskBackup
        material_hashes_before = $materialHashesBefore
        stderr_log = $serviceStderr
    }
    try {
        Write-JsonFile $resultPath $result
        Set-Acl -LiteralPath $resultPath -AclObject (New-ProtectedFileSecurity)
    } catch {
        $rollbackErrors.Add("failed to persist installation result: $($_.Exception.Message)")
    }
    $result.rolled_back = $rollbackErrors.Count -eq 0
    $result.rollback_errors = @($rollbackErrors)
    if ($rollbackErrors.Count -eq 0) {
        Remove-Item -LiteralPath $activeTransactionPath -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $currentInstallationPath -PathType Leaf) {
            $current = Get-Content -Raw -LiteralPath $currentInstallationPath |
                ConvertFrom-Json -ErrorAction SilentlyContinue
            if ($null -ne $current -and [string]$current.transaction_id -ceq $transactionId) {
                Remove-Item -LiteralPath $currentInstallationPath -Force -ErrorAction SilentlyContinue
            }
        }
    }
    $result | ConvertTo-Json -Depth 8 -Compress
    throw
}
} finally {
    if ($mutexAcquired) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
