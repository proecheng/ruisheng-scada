$ErrorActionPreference = "Stop"
$taskName = "Ruisheng B08 Freshness Witness"
$scriptSource = "D:\江苏润盛\tmp-test-logs\b08-provision\freshness_witness.py"
$scriptDestination = "C:\ProgramData\RuishengWitness\freshness_witness.py"
$python = "C:\Users\admin\AppData\Local\Programs\Python\Python311\python.exe"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "fixed Python runtime missing" }
if (-not (Test-Path -LiteralPath $scriptSource -PathType Leaf)) { throw "witness script missing" }
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $python).Hash.ToLowerInvariant() -cne
    "5f7b89a612c9b8af1d6456cdfcd1dbe5ca630849e79aebced9bee9a6694952ec") {
    throw "fixed Python runtime hash mismatch"
}
$protectedFiles = @(
    "C:\ProgramData\RuishengWitness\trust\witness-config.json",
    "C:\ProgramData\RuishengWitness\trust\high-water.json",
    "C:\ProgramData\RuishengWitness\trust\freshness-witness-ed25519.pem",
    "C:\ProgramData\RuishengWitness\tls\server-cert.pem",
    "C:\ProgramData\RuishengWitness\tls\server-key.pem",
    "C:\ProgramData\RuishengWitness\tls\client-cert.pem"
)
foreach ($protectedFile in $protectedFiles) {
    if (-not (Test-Path -LiteralPath $protectedFile -PathType Leaf)) {
        throw "protected witness material is missing: $protectedFile"
    }
    $acl = Get-Acl -LiteralPath $protectedFile
    if (-not $acl.AreAccessRulesProtected) {
        throw "protected witness material inherits ACLs: $protectedFile"
    }
    foreach ($rule in $acl.Access) {
        $sid = $rule.IdentityReference.Translate(
            [Security.Principal.SecurityIdentifier]
        ).Value
        if ($rule.AccessControlType -eq "Allow" -and
            $sid -notin @("S-1-5-18", "S-1-5-32-544", (
                [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
            ))) {
            throw "unexpected witness material ACL identity ${sid}: $protectedFile"
        }
    }
}
New-Item -ItemType Directory -Path (Split-Path -Parent $scriptDestination) -Force | Out-Null
$previousScript = if (Test-Path -LiteralPath $scriptDestination -PathType Leaf) {
    [IO.File]::ReadAllBytes($scriptDestination)
} else { $null }
Copy-Item -LiteralPath $scriptSource -Destination $scriptDestination -Force

$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().User
$system = [Security.Principal.SecurityIdentifier]::new("S-1-5-18")
$fileSecurity = [Security.AccessControl.FileSecurity]::new()
$fileSecurity.SetAccessRuleProtection($true, $false)
$fileSecurity.SetOwner($currentUser)
foreach ($sid in @($system, $currentUser)) {
    [void]$fileSecurity.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $sid,
        [Security.AccessControl.FileSystemRights]::FullControl,
        [Security.AccessControl.AccessControlType]::Allow
    ))
}
$existingAcl = Get-Acl -LiteralPath $scriptDestination
if (-not $existingAcl.AreAccessRulesProtected -or
    $existingAcl.Owner -cne [Security.Principal.WindowsIdentity]::GetCurrent().Name -or
    @($existingAcl.Access | Where-Object {
        $_.AccessControlType -eq "Allow" -and
        ($_.FileSystemRights -band [Security.AccessControl.FileSystemRights]::Write) -ne 0 -and
        $_.IdentityReference.Value -notin @(
            "NT AUTHORITY\SYSTEM", [Security.Principal.WindowsIdentity]::GetCurrent().Name
        )
    }).Count -ne 0) {
    Set-Acl -LiteralPath $scriptDestination -AclObject $fileSecurity
}

$action = New-ScheduledTaskAction -Execute $python -Argument (
    '"' + $scriptDestination + '" serve'
)
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 `
    -RestartInterval ([TimeSpan]::FromMinutes(1)) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings
try {
    Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName $taskName

    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 250
        $connection = Get-NetTCPConnection -LocalAddress "100.67.229.19" -LocalPort 38475 `
            -State Listen -ErrorAction SilentlyContinue
        if ($connection) {
            $process = Get-CimInstance Win32_Process -Filter (
                "ProcessId = " + [int]$connection.OwningProcess
            ) -ErrorAction SilentlyContinue
            $expectedCommand = '"' + $scriptDestination + '" serve'
            if ($null -eq $process -or
                $process.ExecutablePath -cne $python -or
                -not $process.CommandLine.Contains($expectedCommand)) {
                throw "unexpected process owns freshness witness listener"
            }
        }
    } until ($connection -or [DateTime]::UtcNow -ge $deadline)
    if (-not $connection) { throw "persistent witness did not start listening" }
} catch {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    if ($null -ne $previousScript) {
        [IO.File]::WriteAllBytes($scriptDestination, $previousScript)
        Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
        Start-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    } else {
        Remove-Item -LiteralPath $scriptDestination -Force -ErrorAction SilentlyContinue
    }
    throw
}

$taskInfo = Get-ScheduledTaskInfo -TaskName $taskName
[pscustomobject]@{
    installed = $true
    task_name = $taskName
    persistence = "current-user-logon"
    state = (Get-ScheduledTask -TaskName $taskName).State.ToString()
    last_result = $taskInfo.LastTaskResult
    script_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $scriptDestination).Hash.ToLowerInvariant()
    listener_process_id = $connection.OwningProcess
} | ConvertTo-Json -Compress
