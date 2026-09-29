$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$AgentProcessId = $null
$Evidence = 'D:\江苏润盛\docs\superpowers\specs\evidence\zero-origin-block-20260907'
try {
    $Agent = & 'C:\ProgramData\Ruisheng\publisher-tools\Start-ReleaseAgent.ps1' | ConvertFrom-Json
    if (-not $Agent.ready) { throw 'Release agent unavailable' }
    $AgentProcessId = [int]$Agent.pid
    & 'D:\江苏润盛\.venv\Scripts\python.exe' -u (Join-Path $Evidence 'build_diagnostic_candidate.py') 2>&1 |
        Tee-Object -FilePath (Join-Path $Evidence 'build-output.log')
    if ($LASTEXITCODE -ne 0) { throw 'Diagnostic candidate build failed' }
} finally {
    if ($null -ne $AgentProcessId) {
        $AgentProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$AgentProcessId"
        if ($null -ne $AgentProcess -and $AgentProcess.CommandLine.Contains('publisher-secrets\release_agent.py')) {
            Stop-Process -Id $AgentProcessId -ErrorAction Stop
        }
        [ordered]@{ process_id=$AgentProcessId; stopped=$true; observed_at=(Get-Date).ToString('o') } |
            ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Evidence 'signer-lifecycle.json') -Encoding UTF8
    }
}
