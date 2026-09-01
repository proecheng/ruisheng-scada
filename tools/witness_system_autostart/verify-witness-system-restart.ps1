[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$taskName = "Ruisheng B08 Freshness Witness"
$root = "C:\ProgramData\RuishengWitness"
$listenAddress = "100.67.229.19"
$listenPort = 38475
$highWater = Join-Path $root "trust\high-water.json"
$witness = Join-Path $root "freshness_witness.py"
$testScript = Join-Path $PSScriptRoot "test-witness-system-autostart.ps1"
$statusPath = Join-Path $PSScriptRoot "restart-verification.status.json"
$expectedHighWaterSha256 = "134b160de987a102518105ca0feb32876c0b6f0d315f0dee8ca8d8d652cbe9db"
$expectedWitnessSha256 = "f441790914ce3d22e24d3ba78712bcac6cb2129f1b48beb27dcfaf53c56b15ca"
$expectedTestScriptSha256 = "ca53b2127b31be87fea0d74ee405d7dc36354a33893b00a55bcf923a053dc0e2"

function Get-Sha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
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
    throw "witness task did not stop"
}

function Invoke-Acceptance {
    $json = & $testScript -TaskName $taskName -Root $root `
        -ListenAddress $listenAddress -ListenPort $listenPort
    $value = $json | ConvertFrom-Json
    if (-not $value.all_passed) { throw "witness acceptance failed: $json" }
    return $value
}

function Write-Status([object]$Value) {
    [IO.File]::WriteAllText(
        $statusPath,
        (($Value | ConvertTo-Json -Depth 12 -Compress) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
}

$result = $null
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "administrator token required"
    }
    if (-not (Test-Path -LiteralPath $testScript -PathType Leaf) -or
        (Get-Sha256 $testScript) -cne $expectedTestScriptSha256) {
        throw "acceptance script hash mismatch"
    }

    $highWaterBefore = Get-Sha256 $highWater
    $witnessBefore = Get-Sha256 $witness
    if ($highWaterBefore -cne $expectedHighWaterSha256) { throw "unexpected high-water baseline" }
    if ($witnessBefore -cne $expectedWitnessSha256) { throw "unexpected witness baseline" }
    $beforeAcceptance = Invoke-Acceptance
    $beforePid = [int]$beforeAcceptance.listener_process_id

    Stop-ScheduledTask -TaskName $taskName
    [void](Wait-Listener $false 20)
    Wait-TaskNotRunning 20

    Start-ScheduledTask -TaskName $taskName
    [void](Wait-Listener $true 30)
    $afterAcceptance = Invoke-Acceptance
    $afterPid = [int]$afterAcceptance.listener_process_id
    if ($afterPid -eq $beforePid) { throw "witness listener PID did not change after restart" }

    $highWaterAfter = Get-Sha256 $highWater
    $witnessAfter = Get-Sha256 $witness
    if ($highWaterAfter -cne $highWaterBefore) { throw "high-water changed during restart" }
    if ($witnessAfter -cne $witnessBefore) { throw "witness changed during restart" }
    $result = [ordered]@{
        completed = $true
        passed = $true
        at = [DateTimeOffset]::Now.ToString("o")
        listener_pid_before = $beforePid
        listener_pid_after = $afterPid
        high_water_sha256_before = $highWaterBefore
        high_water_sha256_after = $highWaterAfter
        witness_sha256 = $witnessAfter
        acceptance_before = $beforeAcceptance
        acceptance_after = $afterAcceptance
    }
} catch {
    $failure = $_.Exception.Message
    $recoveryError = $null
    $recoveryAcceptance = $null
    try {
        try {
            $recoveryAcceptance = Invoke-Acceptance
        } catch {
            if (@(Get-Listeners).Count -ne 0) {
                throw "port 38475 is occupied but does not satisfy witness binding: $($_.Exception.Message)"
            }
            Start-ScheduledTask -TaskName $taskName
            [void](Wait-Listener $true 30)
            $recoveryAcceptance = Invoke-Acceptance
        }
    } catch {
        $recoveryError = $_.Exception.Message
    }
    $result = [ordered]@{
        completed = $true
        passed = $false
        at = [DateTimeOffset]::Now.ToString("o")
        error = $failure
        service_recovered = $null -eq $recoveryError -and $null -ne $recoveryAcceptance
        service_recovery_error = $recoveryError
        recovery_acceptance = $recoveryAcceptance
    }
} finally {
    Write-Status $result
}

$result | ConvertTo-Json -Depth 12 -Compress
if (-not $result.passed) { exit 1 }
